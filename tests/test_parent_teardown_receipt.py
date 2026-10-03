"""Actual cleanup branches against inert Popen/socket fixtures, no devices/media."""
import ast
import copy
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import hardware_stream as module


class FakeProcess:
    def __init__(self, events, label, polls=(None,), waits=(0,)):
        self.pid = 12345
        self.events, self.label = events, label
        self.polls, self.waits = iter(polls), iter(waits)
        self.stdin = FakeStream(events, label + '.stdin')
        self.stdout = FakeStream(events, label + '.stdout')

    def poll(self):
        self.events.append((self.label, 'poll'))
        return next(self.polls)

    def wait(self, timeout):
        self.events.append((self.label, 'wait', timeout))
        answer = next(self.waits)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def terminate(self):
        self.events.append((self.label, 'terminate'))

    def kill(self):
        self.events.append((self.label, 'kill'))


class FakeStream:
    def __init__(self, events, name):
        self.events, self.name, self.closed = events, name, False

    def close(self):
        self.events.append((self.name, 'close'))
        self.closed = True


class FakeChannel(FakeStream):
    def shutdown(self, how):
        self.events.append((self.name, 'shutdown', how))


class DeferredThread:
    """Capture dispatch without running an async writer during branch fixtures."""
    calls = []

    def __init__(self, target, args, **kwargs):
        self.target, self.args = target, args
        self.kwargs = kwargs

    def start(self):
        self.calls.append((self.target, self.args, self.kwargs))


def actual_worker_cleanup():
    tree = ast.parse(Path(module.__file__).read_text())
    worker = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'worker')
    owned = next(n for n in worker.body if isinstance(n, ast.Try) and n.finalbody)
    harness = ast.parse('def cleanup():\n    try:\n        raise primary\n    finally:\n        pass\n')
    harness.body[0].body[0].finalbody = copy.deepcopy(owned.finalbody)
    return compile(ast.fix_missing_locations(harness), '<actual-worker-finally>', 'exec')


class TeardownChecks(unittest.TestCase):
    def diagnostic(self, role='worker-parent'):
        result = module.ParentTeardownReceipt('/unused/capture.jsonl', role)
        with patch.object(module.os, 'getpgid', return_value=12345) as lookup:
            result.bind(SimpleNamespace(pid=12345))
            result.bind(SimpleNamespace(pid=77777))
        self.assertEqual(lookup.call_count, 1)
        return result

    def run_host(self, enabled, polls=(None,), waits=(0,), signal_fault=None, clock_fault=None):
        events = []
        process = FakeProcess(events, 'worker', polls, waits)
        session = module.HostHardwareSession.__new__(module.HostHardwareSession)
        session.clients = {'video': FakeChannel(events, 'video'), 'audio': FakeChannel(events, 'audio')}
        session.proc = process
        diagnostic = self.diagnostic() if enabled else None
        session.teardown_diagnostic = diagnostic
        def killpg(pid, sig):
            events.append(('group', pid, sig))
            if signal_fault is not None:
                raise signal_fault
        DeferredThread.calls.clear()
        with patch.object(module.os, 'killpg', side_effect=killpg), \
                patch.object(module.threading, 'Thread', DeferredThread), \
                patch.object(module, 'capture_trace_clock_ns', side_effect=clock_fault,
                             return_value=100) as clock:
            try:
                session.close()
                failure = None
            except BaseException as error:
                failure = error
        if not enabled:
            clock.assert_not_called()
            self.assertFalse(DeferredThread.calls)
        return events, diagnostic, failure

    def run_worker(self, enabled, polls=(None,), waits=(0,)):
        events = []
        native = FakeProcess(events, 'encoder', polls, waits)
        audio = FakeProcess(events, 'audio-control')
        diagnostic = self.diagnostic('encoder-parent') if enabled else None
        namespace = dict(module.__dict__)
        namespace.update(stop=SimpleNamespace(set=lambda: events.append(('stop',))),
            channel=None, sockets={'video': FakeChannel(events, 'video')}, scid=None,
            native=native, audio_control=audio, encoder_teardown=diagnostic, port=None,
            log=lambda value: events.append(('session_closed',)), counters={},
            trace=None, raw_diagnostics=None, primary=RuntimeError('owned_primary'))
        exec(actual_worker_cleanup(), namespace)
        DeferredThread.calls.clear()
        with patch.object(module.threading, 'Thread', DeferredThread), \
                patch.object(module, 'capture_trace_clock_ns', return_value=100) as clock:
            with self.assertRaises(RuntimeError) as raised:
                namespace['cleanup']()
        self.assertIs(raised.exception, namespace['primary'])
        if not enabled:
            clock.assert_not_called()
            self.assertFalse(DeferredThread.calls)
        return events, diagnostic

    def test_host_existing_poll_signal_wait_pipe_actions_and_deadlines_are_identical(self):
        for polls, waits in (((None,), (0,)), ((-15,), ()),
                ((None, None), (subprocess.TimeoutExpired('owned', 5), -9)),
                ((None, 0), (subprocess.TimeoutExpired('owned', 5),))):
            with self.subTest(polls=polls):
                off, _, failure = self.run_host(False, polls, waits)
                on, diagnostic, observed_failure = self.run_host(True, polls, waits)
                self.assertEqual(on, off)
                self.assertIsNone(failure)
                self.assertIsNone(observed_failure)
                self.assertTrue(diagnostic.value['cleanup_returned'])
                self.assertTrue(module.validate_teardown_receipt(diagnostic.value, 'worker-parent'))

    def test_group_lookup_and_permission_failures_preserve_original_branch_and_exception(self):
        lookup = ProcessLookupError(3, 'private detail')
        permission = PermissionError(1, 'private detail')
        for error, polls in ((lookup, (None, 0)), (permission, (None,))):
            off, _, failure = self.run_host(False, polls, (), error)
            on, diagnostic, observed_failure = self.run_host(True, polls, (), error)
            self.assertEqual(on, off)
            self.assertIs(observed_failure, failure)
            if error is permission:
                self.assertIs(failure, permission)
                self.assertFalse(diagnostic.value['cleanup_returned'])
            serialized = json.dumps(diagnostic.value)
            self.assertNotIn('private detail', serialized)
            self.assertIn(4, [row[0] for row in diagnostic.value['operation_rows']])

    def test_channel_close_failure_stops_before_poll_and_preserves_primary(self):
        primary = OSError('owned_channel_close_failure')
        observations = []
        for enabled in (False, True):
            events = []
            session = module.HostHardwareSession.__new__(module.HostHardwareSession)
            channel = FakeChannel(events, 'video')
            def close():
                events.append(('video', 'close'))
                raise primary
            channel.close = close
            session.clients = {'video': channel}
            session.proc = FakeProcess(events, 'worker')
            diagnostic = self.diagnostic() if enabled else None
            session.teardown_diagnostic = diagnostic
            with patch.object(module.threading, 'Thread', DeferredThread), \
                    patch.object(module.os, 'killpg') as signal_call, \
                    patch.object(module, 'capture_trace_clock_ns', return_value=100) as clock:
                with self.assertRaises(OSError) as raised:
                    session.close()
            self.assertIs(raised.exception, primary)
            signal_call.assert_not_called()
            self.assertNotIn(('worker', 'poll'), events)
            if enabled:
                self.assertFalse(diagnostic.value['cleanup_returned'])
                self.assertEqual([row[0] for row in diagnostic.value['operation_rows']], [1, 2])
                self.assertEqual(diagnostic.value['operation_rows'][-1][4], 1)
            else:
                clock.assert_not_called()
            observations.append(events)
        self.assertEqual(observations[0], observations[1])

    def test_worker_encoder_only_and_audio_control_cleanup_preserve_order_and_deadlines(self):
        for polls, waits in (((None,), (0,)), ((-15,), ()),
                ((None,), (subprocess.TimeoutExpired('owned', 3), -9))):
            off, _ = self.run_worker(False, polls, waits)
            on, diagnostic = self.run_worker(True, polls, waits)
            self.assertEqual(on, off)
            self.assertTrue(diagnostic.value['cleanup_returned'])
            self.assertTrue(module.validate_teardown_receipt(diagnostic.value, 'encoder-parent'))
            self.assertTrue(all(row[0] >= 11 for row in diagnostic.value['operation_rows']))
            self.assertEqual([x for x in on if x[0] == 'audio-control'],
                             [('audio-control', 'poll'), ('audio-control', 'terminate'),
                              ('audio-control', 'wait', 3)])

    def test_observation_clock_failure_cannot_change_actions_or_mask_primary(self):
        permission = PermissionError(1, 'private detail')
        for fault in (OSError('private clock'), ValueError('private clock')):
            off, _, failure = self.run_host(False, signal_fault=permission)
            on, diagnostic, actual = self.run_host(True, signal_fault=permission, clock_fault=fault)
            self.assertEqual(on, off)
            self.assertIs(actual, failure)
            self.assertGreater(diagnostic.value['observer_errors'], 0)
            self.assertTrue(all(not row[3] for row in diagnostic.value['operation_rows']))
            self.assertTrue(module.validate_teardown_receipt(diagnostic.value, 'worker-parent'))

    def test_pgid_error_is_unknown_without_poll_or_retry(self):
        process = SimpleNamespace(pid=12345)
        diagnostic = module.ParentTeardownReceipt('/unused/capture.jsonl', 'encoder-parent')
        with patch.object(module.os, 'getpgid', side_effect=ProcessLookupError(3, 'private')) as lookup:
            diagnostic.bind(process)
            diagnostic.bind(process)
        self.assertEqual(lookup.call_count, 1)
        self.assertTrue(diagnostic.value['identity_bound'])
        self.assertFalse(diagnostic.value['pgid_known'])
        self.assertEqual(diagnostic.value['pgid_failure_code'], 1)
        self.assertTrue(module.validate_teardown_receipt(diagnostic.value, 'encoder-parent'))

    def test_base_exception_and_bad_errno_property_preserve_exact_primary(self):
        class ClosedFailure(Exception):
            @property
            def errno(self):
                raise RuntimeError('private attribute detail')
        for primary, expected_code in ((KeyboardInterrupt(), 6), (ClosedFailure(), 5)):
            diagnostic = self.diagnostic()
            diagnostic.value['cleanup_entered'] = True
            calls = []
            def operation():
                calls.append(1)
                raise primary
            with patch.object(module, 'capture_trace_clock_ns', return_value=100):
                try:
                    diagnostic.call(4, operation)
                except BaseException as failure:
                    self.assertIs(failure, primary)
                else:
                    self.fail('original failure lost')
            self.assertEqual(calls, [1])
            self.assertEqual(diagnostic.value['operation_rows'][0][7:9], [expected_code, 0])

    def test_operation_prefix_cap_records_loss_without_repeating_actions(self):
        diagnostic = self.diagnostic()
        diagnostic.value['cleanup_entered'] = True
        calls = []
        with patch.object(module, 'capture_trace_clock_ns', return_value=100):
            for _ in range(31):
                diagnostic.call(3, lambda: calls.append(1))
        self.assertEqual(len(calls), 31)
        self.assertEqual(len(diagnostic.value['operation_rows']), 24)
        self.assertEqual(diagnostic.value['operations_dropped'], 7)
        self.assertTrue(module.validate_teardown_receipt(diagnostic.value, 'worker-parent'))

    def test_factory_off_does_not_create_object_clock_thread_or_file(self):
        with patch.object(module, 'ParentTeardownReceipt') as factory, \
                patch.object(module, 'capture_trace_clock_ns') as clock, \
                patch.object(module.threading, 'Thread') as thread:
            self.assertIsNone(module.parent_teardown_receipt(None, 'worker-parent'))
        factory.assert_not_called()
        clock.assert_not_called()
        thread.assert_not_called()

    def test_method_return_observation_is_not_signal_delivery_or_native_final(self):
        diagnostic = self.diagnostic('encoder-parent')
        diagnostic.value['cleanup_entered'] = True
        process = SimpleNamespace(terminate=lambda: None)
        with patch.object(module, 'capture_trace_clock_ns', return_value=100):
            module.observed_teardown(diagnostic, 12, process.terminate)
        row = diagnostic.value['operation_rows'][0]
        self.assertEqual(row[0], 12)
        self.assertEqual(row[4], 0)  # method returned, no dispatch/receive claim
        self.assertFalse(row[5])
        self.assertNotIn('native_producer_final', diagnostic.value)

    def test_nonexit_integer_return_is_not_an_exit_result(self):
        calls = []
        for operation in (1, 2, 4, 7, 9, 10, 12, 14):
            role = 'worker-parent' if operation <= 10 else 'encoder-parent'
            diagnostic = self.diagnostic(role)
            diagnostic.value['cleanup_entered'] = True
            def action():
                calls.append(operation)
                return 17
            with patch.object(module, 'capture_trace_clock_ns', return_value=100):
                answer = module.observed_teardown(diagnostic, operation, action)
            self.assertEqual(answer, 17)
            self.assertEqual(diagnostic.value['operation_rows'][0][4:7], [0, False, 0])
            self.assertTrue(module.validate_teardown_receipt(diagnostic.value, role))
        self.assertEqual(calls, [1, 2, 4, 7, 9, 10, 12, 14])

    def test_errno_getter_base_exception_cannot_replace_actual_host_primary(self):
        class PrimaryFailure(OSError):
            @property
            def errno(self):
                raise KeyboardInterrupt('owned_diagnostic_getter_failure')
        primary = PrimaryFailure('owned_action_failure')
        off, _, failure = self.run_host(False, signal_fault=primary)
        on, diagnostic, observed_failure = self.run_host(True, signal_fault=primary)
        self.assertEqual(on, off)
        self.assertIs(failure, primary)
        self.assertIs(observed_failure, primary)
        row = next(row for row in diagnostic.value['operation_rows'] if row[0] == 4)
        self.assertEqual(row[4:9], [1, False, 0, 4, 0])
        self.assertFalse(diagnostic.value['cleanup_returned'])
        self.assertTrue(module.validate_teardown_receipt(diagnostic.value, 'worker-parent'))

    def test_repeated_close_does_not_repeat_receipt_publication(self):
        session = module.HostHardwareSession.__new__(module.HostHardwareSession)
        session.clients, session.proc = {}, None
        diagnostic = self.diagnostic()
        session.teardown_diagnostic = diagnostic
        DeferredThread.calls.clear()
        with patch.object(module.threading, 'Thread', DeferredThread):
            session.close()
            session.close()
        self.assertEqual(len(DeferredThread.calls), 1)


class ReceiptFileChecks(unittest.TestCase):
    def encoded(self, role='encoder-parent'):
        diagnostic = module.ParentTeardownReceipt('/unused/capture', role)
        diagnostic.value.update(cleanup_entered=True, cleanup_returned=True)
        return json.dumps(diagnostic.value, separators=(',', ':')).encode() + b'\n'

    def test_cross_process_publish_is_private_exclusive_and_missing_sibling_stays_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            capture = Path(folder) / 'capture.jsonl'
            path = Path(str(capture) + '.encoder-parent.json')
            module.ParentTeardownReceipt._publish(path, self.encoded())
            result = module.read_teardown_receipt(capture, 'encoder-parent')
            self.assertEqual(result['status'], 'observed_parent_receipt')
            self.assertFalse(result['native_producer_final'])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(path.stat().st_nlink, 1)
            self.assertEqual(module.read_teardown_receipt(capture, 'worker-parent')['status'], 'unknown')
            before = path.read_bytes()
            module.ParentTeardownReceipt._publish(path, b'private replacement\n')
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse(Path(str(path) + '.tmp').exists())

    def test_async_finish_dispatches_bounded_snapshot_without_join_or_clock(self):
        diagnostic = module.ParentTeardownReceipt('/unused/capture', 'encoder-parent')
        diagnostic.value['cleanup_entered'] = True
        DeferredThread.calls.clear()
        with patch.object(module.threading, 'Thread', DeferredThread), \
                patch.object(module, 'capture_trace_clock_ns') as clock:
            diagnostic.finish(True)
        clock.assert_not_called()
        self.assertEqual(len(DeferredThread.calls), 1)
        _, (path, data), options = DeferredThread.calls[0]
        self.assertTrue(options['daemon'])
        self.assertLessEqual(len(data), 4096)
        self.assertTrue(data.endswith(b'\n'))
        diagnostic.value['cleanup_returned'] = False
        self.assertTrue(json.loads(data)['cleanup_returned'])

    def test_failed_thread_or_encoding_is_only_missing_evidence(self):
        for field in ('thread', 'encoding'):
            diagnostic = module.ParentTeardownReceipt('/unused/capture', 'encoder-parent')
            diagnostic.value['cleanup_entered'] = True
            selected = patch.object(module.threading.Thread, 'start', side_effect=OSError('private')) \
                if field == 'thread' else patch.object(module.json, 'dumps', side_effect=ValueError('private'))
            with selected:
                diagnostic.finish(True)
            self.assertTrue(diagnostic.finished)

    def test_symlink_nonprivate_directory_and_partial_or_malformed_receipt_are_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            capture = Path(folder) / 'capture'
            path = Path(str(capture) + '.encoder-parent.json')
            target = Path(folder) / 'target'
            target.write_bytes(self.encoded())
            target.chmod(0o600)
            path.symlink_to(target)
            self.assertEqual(module.read_teardown_receipt(capture, 'encoder-parent')['status'], 'unknown')
            module.ParentTeardownReceipt._publish(path, self.encoded())
            self.assertTrue(path.is_symlink())
            path.unlink()
            for value in (b'{', self.encoded().rstrip(b'\n'), b'x' * 4097,
                    self.encoded().replace(b'"parent_pid":', b'"foreign":0,"parent_pid":'),
                    self.encoded().replace(b'"child_pid":0', b'"child_pid":true'),
                    self.encoded().replace(b'"child_pid":0', b'"child_pid":0,"child_pid":0')):
                path.write_bytes(value)
                path.chmod(0o600)
                self.assertEqual(module.read_teardown_receipt(capture, 'encoder-parent')['status'], 'unknown')
            path.unlink()
            Path(folder).chmod(0o755)
            module.ParentTeardownReceipt._publish(path, self.encoded())
            self.assertFalse(path.exists())

    def test_publication_failure_never_publishes_partial_final(self):
        for stage in ('write', 'close', 'link'):
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'capture.encoder-parent.json'
                if stage == 'close':
                    real_close = os.close
                    calls = []
                    def close(fd):
                        calls.append(fd)
                        real_close(fd)
                        if len(calls) == 1:
                            raise OSError('owned close fault')
                    selected = patch.object(module.os, 'close', side_effect=close)
                else:
                    selected = patch.object(module.os, stage, side_effect=OSError('private'))
                with selected:
                    module.ParentTeardownReceipt._publish(path, self.encoded())
                self.assertFalse(path.exists())
                self.assertFalse(Path(str(path) + '.tmp').exists())

    def test_role_and_operation_closed_schema_rejects_cross_layer_rows(self):
        value = json.loads(self.encoded())
        value['operation_rows'] = [[4, 100, 101, True, 0, False, 0, 0, 0]]
        self.assertFalse(module.validate_teardown_receipt(value, 'encoder-parent'))
        value['operation_rows'][0][0] = 12
        self.assertTrue(module.validate_teardown_receipt(value, 'encoder-parent'))
        value['operation_rows'][0][3] = False
        self.assertFalse(module.validate_teardown_receipt(value, 'encoder-parent'))

    def test_malformed_nonexit_known_integer_receipt_is_unknown(self):
        for operation in (1, 2, 4, 7, 9, 10, 12, 14):
            role = 'worker-parent' if operation <= 10 else 'encoder-parent'
            value = json.loads(self.encoded(role))
            value['operation_rows'] = [[operation, 100, 101, True, 0, True, 17, 0, 0]]
            self.assertFalse(module.validate_teardown_receipt(value, role))
            with tempfile.TemporaryDirectory() as folder:
                capture = Path(folder) / 'capture'
                path = Path(str(capture) + '.' + role + '.json')
                path.write_bytes(json.dumps(value).encode() + b'\n')
                path.chmod(0o600)
                self.assertEqual(module.read_teardown_receipt(capture, role)['status'], 'unknown')


if __name__ == '__main__':
    unittest.main()
