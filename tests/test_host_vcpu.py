import contextlib
import ctypes
from dataclasses import replace
import io
import inspect
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.probes import measure_host_vcpu as probe


class FakeClock:
    def __init__(self):
        self.ns = 1_000_000_000

    def __call__(self):
        return self.ns

    def sleep(self, seconds):
        self.ns += int(seconds * 1e9)


class FakeProc:
    def __init__(self, clock, names=None, rates=None, read_ns=10_000):
        self.clock = clock
        self.names = names or {101: b'private account', 102: b'other secret'}
        self.rates = rates or {101: 100, 102: 200}
        self.read_ns = read_ns
        self.identity_value = probe.Identity(123, 501, 501, 501, 100, 200,
                                             b'private-token', b'account-name')
        self.thread_calls = []
        self.identity_calls = 0
        self.counts = {}
        self.changed_at = 0
        self.regressed_at = 0
        self.qemu = True

    def identity(self, pid):
        self.identity_calls += 1
        self.clock.ns += 1000
        if self.changed_at and self.identity_calls >= self.changed_at:
            return replace(self.identity_value, start_usec=201)
        return self.identity_value

    def is_qemu(self, pid):
        return self.qemu

    def threads(self, pid):
        return list(self.names)

    def thread(self, pid, handle, flavor=5):
        self.thread_calls.append((pid, handle, flavor))
        self.counts[handle] = self.counts.get(handle, 0) + 1
        before = self.clock()
        self.clock.ns += self.read_ns
        count = self.counts[handle]
        user = count * self.rates[handle]
        if self.regressed_at and count >= self.regressed_at:
            user = 0
        return probe.ThreadRead(before, self.clock(), user, count * 10, 3, 0,
                                0, self.names[handle])


def numeric_values_only(value):
    if isinstance(value, dict):
        return all(isinstance(key, str) and numeric_values_only(item)
                   for key, item in value.items())
    if isinstance(value, list):
        return all(numeric_values_only(item) for item in value)
    return isinstance(value, int) and not isinstance(value, bool)


class HostVcpuCheck(unittest.TestCase):
    def run_capture(self, backend, clock, **kwargs):
        return probe.capture(123, seconds=2, backend=backend, clock=clock,
                             sleep=clock.sleep, owner_uid=501, **kwargs)

    def test_sdk_abi_sizes(self):
        self.assertEqual(ctypes.sizeof(probe.ProcThreadInfo), 112)
        self.assertEqual(ctypes.sizeof(probe.ProcBsdInfo), 136)
        # The fence model uses this POSIX clock explicitly. On macOS Python's
        # monotonic_ns may use a different origin, so check both API defaults.
        self.assertIs(inspect.signature(probe.LibProc).parameters['clock'].default,
                      probe.host_clock_ns)
        self.assertIs(inspect.signature(probe.capture).parameters['clock'].default,
                      probe.host_clock_ns)
        with patch.object(probe.time, 'clock_gettime_ns', return_value=123) as clock:
            self.assertEqual(probe.host_clock_ns(), 123)
        clock.assert_called_once_with(probe.time.CLOCK_MONOTONIC)

    def test_strict_name_roles_reject_arbitrary_or_partial_names(self):
        for value in (b'CPU 0/HVF', b'CPU 1', b'CPU 63/KVM'):
            self.assertGreaterEqual(probe.vcpu_index(value), 0)
        for value in (b'CPU 64/HVF', b'private CPU 1/HVF', b'CPU 2/HVF secret',
                      b'CPU 1/unknown', b'CPU 999/HVF', b'CPU -1/HVF'):
            self.assertEqual(probe.vcpu_index(value), -1)

    def test_unknown_candidates_are_ranked_but_never_declared_vcpus(self):
        clock = FakeClock()
        backend = FakeProc(clock)
        report = self.run_capture(backend, clock, max_threads=1)
        self.assertEqual(report['status_code'], 0)
        self.assertEqual(report['roles'][0]['thread_handle'], 102)
        self.assertEqual(report['roles'][0]['role_id'], 2000)
        self.assertEqual(report['discovery']['vcpu_identity_unknown'], 1)
        self.assertEqual(report['discovery']['unselected_thread_count'], 1)
        self.assertEqual(report['limitations']['candidate_roles_prove_vcpu_identity'], 0)
        self.assertEqual(report['limitations']['excludes_between_sample_pauses'], 0)
        self.assertTrue(numeric_values_only(report))
        for private in ('private account', 'other secret', 'private-token', 'account-name'):
            self.assertNotIn(private, json.dumps(report))

    def test_id64_direct_sampling_has_external_scope_and_unknown_guest_index(self):
        clock = FakeClock()
        backend = FakeProc(clock)
        report = self.run_capture(backend, clock, vcpu_thread_ids=[102, 101])
        self.assertEqual([role['thread_id64'] for role in report['roles']], [101, 102])
        self.assertEqual([role['role_id'] for role in report['roles']], [3000, 3001])
        self.assertTrue(all(role['thread_handle'] == 0 for role in report['roles']))
        self.assertTrue(all(call[2] == 15 for call in backend.thread_calls))
        self.assertEqual(report['discovery']['external_vcpu_role_asserted'], 1)
        self.assertEqual(report['limitations']['probe_performed_external_stack_validation'], 0)
        self.assertEqual(report['limitations']['guest_vcpu_index_known'], 0)
        self.assertEqual(report['limitations']['vcpu_set_completeness_known'], 0)
        self.assertEqual(report['pid_start_sec'], 100)
        self.assertEqual(report['pid_start_usec'], 200)
        self.assertTrue(numeric_values_only(report))

    def test_exact_known_names_keep_cpu_roles_independent_of_rank(self):
        clock = FakeClock()
        backend = FakeProc(clock, {101: b'CPU 2/HVF', 102: b'CPU 0/HVF'})
        report = self.run_capture(backend, clock)
        self.assertEqual([role['role_id'] for role in report['roles']], [1000, 1002])
        self.assertEqual(report['discovery']['startup_ranking_used'], 0)

    def test_ownership_and_wrong_executable_fail_before_thread_read(self):
        clock = FakeClock()
        backend = FakeProc(clock)
        backend.identity_value = replace(backend.identity_value, uid=502)
        with self.assertRaises(probe.ProbeError) as result:
            self.run_capture(backend, clock)
        self.assertEqual(result.exception.code, probe.OWNER)
        self.assertEqual(backend.thread_calls, [])
        backend.identity_value = replace(backend.identity_value, uid=501)
        backend.qemu = False
        with self.assertRaises(probe.ProbeError) as result:
            self.run_capture(backend, clock)
        self.assertEqual(result.exception.code, probe.EXECUTABLE)

    def test_pid_reuse_during_sample_marks_bracket_invalid_and_stops(self):
        clock = FakeClock()
        backend = FakeProc(clock)
        # Initial + external-set validation + sample-before are stable;
        # sample-after sees a different start time.
        backend.changed_at = 4
        report = self.run_capture(backend, clock, vcpu_thread_ids=[101])
        self.assertEqual(report['host_clock_code'], 1)
        self.assertEqual(report['status_code'], probe.IDENTITY_CHANGED)
        self.assertEqual(report['samples_count'], 1)
        self.assertEqual(report['samples'][0]['identity_validated'], 0)

    def test_cpu_counter_regression_stops_instead_of_making_a_zero_delta(self):
        clock = FakeClock()
        backend = FakeProc(clock)
        backend.regressed_at = 3
        report = self.run_capture(backend, clock, vcpu_thread_ids=[101])
        self.assertEqual(report['status_code'], probe.COUNTER_REGRESSED)
        self.assertEqual(report['samples'][-1]['threads'][0]['available'], 0)
        self.assertEqual(report['samples'][-1]['threads'][0]['delta_user_ns'], -1)

    def test_read_brackets_and_cpu_delta_interval_are_preserved(self):
        clock = FakeClock()
        backend = FakeProc(clock)
        report = self.run_capture(backend, clock, vcpu_thread_ids=[101])
        first, second = [sample['threads'][0] for sample in report['samples'][:2]]
        self.assertEqual(first['delta_elapsed_ns'], -1)
        self.assertEqual(second['delta_elapsed_ns'], 25_000_000)
        self.assertEqual(second['delta_user_ns'], 100)
        for sample in report['samples']:
            self.assertEqual(sample['identity_validated'], 1)
            self.assertEqual(sample['complete_thread_set_read'], 1)
            for row in sample['threads']:
                self.assertLessEqual(sample['host_before_monotonic_ns'], row['host_before_monotonic_ns'])
                self.assertLessEqual(row['host_after_monotonic_ns'], sample['host_after_monotonic_ns'])

    def test_slow_read_skips_slots_without_catchup_burst(self):
        clock = FakeClock()
        backend = FakeProc(clock, read_ns=100_000_000)
        report = self.run_capture(backend, clock, interval_ms=20, vcpu_thread_ids=[101])
        self.assertGreater(report['skipped_slots'], 0)
        self.assertLessEqual(report['samples_count'], 20)
        self.assertGreaterEqual(report['sample_gap_median_ns'], 100_000_000)

    def test_full_or_malformed_thread_list_is_not_silently_truncated(self):
        backend = object.__new__(probe.LibProc)
        backend.pidinfo = lambda pid, flavor, arg, buffer, size: size
        with self.assertRaises(probe.ProbeError) as result:
            backend.threads(123)
        self.assertEqual(result.exception.code, probe.LIST_LIMIT)
        backend.pidinfo = lambda pid, flavor, arg, buffer, size: 9
        with self.assertRaises(probe.ProbeError) as result:
            backend.threads(123)
        self.assertEqual(result.exception.code, probe.THREAD_READ)

    def test_limits_reject_nonfinite_duration_wrapping_pid_duplicate_or_large_ids(self):
        for kwargs in ({'seconds': float('nan')}, {'seconds': float('inf')},
                       {'seconds': 1}, {'seconds': 61}, {'interval_ms': 19},
                       {'interval_ms': 51}, {'max_threads': 17},
                       {'vcpu_thread_ids': [1, 1]}, {'vcpu_thread_ids': [2**64]},
                       {'vcpu_thread_ids': [0]}):
            with self.assertRaises(probe.ProbeError) as result:
                probe.capture(123, **kwargs)
            self.assertEqual(result.exception.code, probe.OPTIONS)
        with self.assertRaises(probe.ProbeError):
            probe.capture(2**31 + 123)

    def test_existing_evidence_is_retained_without_process_query(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'existing.json'
            output.write_text('retained')
            with patch.object(probe, 'capture') as mocked, contextlib.redirect_stdout(io.StringIO()) as out:
                result = probe.main(['--pid', '123', '--output', str(output)])
            self.assertEqual(result, 1)
            self.assertEqual(output.read_text(), 'retained')
            mocked.assert_not_called()
            self.assertEqual(json.loads(out.getvalue())['status_code'], probe.OUTPUT)

    def test_failure_evidence_and_stdout_do_not_leak_os_messages(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'probe.json'
            with patch.object(probe, 'capture', side_effect=probe.ProbeError(probe.OWNER, 1)), contextlib.redirect_stdout(io.StringIO()) as out:
                result = probe.main(['--pid', '123', '--output', str(output)])
            self.assertEqual(result, 1)
            self.assertEqual(json.loads(output.read_text())['status_code'], probe.OWNER)
            self.assertEqual(json.loads(output.read_text())['host_clock_code'], 1)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertTrue(numeric_values_only(json.loads(output.read_text())))
            self.assertNotIn(directory, out.getvalue())


if __name__ == '__main__':
    unittest.main()
