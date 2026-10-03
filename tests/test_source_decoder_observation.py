import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

PATH = Path(__file__).resolve().parents[1]/'scripts/probes/source_decoder_observation.py'
SPEC = importlib.util.spec_from_file_location('source_decoder_observation', PATH)
PROBE = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(PROBE)
PRIVATE = 'SYNTHETIC_PRIVATE_TEXT_MUST_NOT_EXPORT'


def identity(pid=5212, uid=10235, start=453401, cmdline=None, comm=b'player (synthetic)'):
    cmdline = PROBE.playback.TARGET_PACKAGE.encode() + b'\0' if cmdline is None else cmdline
    status = (f'Name:\t{PRIVATE}\nTgid:\t{pid}\nPid:\t{pid}\n'
              f'Uid:\t{uid}\t{uid}\t{uid}\t{uid}\n').encode()
    # Fields3..21 followed by the starttime at field22.
    suffix = b'S ' + b' '.join([b'0'] * 18) + b' ' + str(start).encode()
    return str(pid).encode()+b'\n'+cmdline+PROBE.STATUS_MARKER+status+PROBE.STAT_MARKER+str(pid).encode()+b' ('+comm+b') '+suffix+b'\n'


def session(pid=5212, uid=10235, position=0, state=3, speed=1):
    return (f'  session (userId=0)\n    ownerPid={pid}, ownerUid={uid}, userId=0\n'
            f'    package={PROBE.playback.TARGET_PACKAGE}\n    active=true\n'
            f'    state=PlaybackState {{state={state}, position={position}, buffered position=0, '
            f'speed={speed}, updated=18312027, actions=123}}\n    metadata: {PRIVATE}\n').encode()


class FakeReader:
    def __init__(self, values):
        self.values = values
        self.started_ns = time.monotonic_ns()
        self.total_bytes, self.unreaped = 0, False
        self.arguments, self.buffers = [], []

    def read(self, args, byte_limit=PROBE.MAX_DUMP_BYTES):
        self.arguments.append((args, byte_limit))
        value = self.values[len(self.arguments)-1]
        raw, error = value if isinstance(value, tuple) else (value, 0)
        raw = bytearray(raw)
        self.buffers.append(raw)
        self.total_bytes += len(raw)
        stamp = time.monotonic_ns()
        info = dict(PROBE.command_info(), command_ok=error == 0, error_code=error,
                    raw_bytes=len(raw), host_started_monotonic_ns=stamp,
                    host_finished_monotonic_ns=stamp)
        return raw, info


class SourceDecoderObservationTests(unittest.TestCase):
    def run_fixture(self, values=None):
        reader = FakeReader(values or [identity(), session(), b'codec historical '+PRIVATE.encode(), b'', session(), identity()])
        result = PROBE.collect('/synthetic/no-device', 'emulator-5556', reader_factory=lambda *_: reader)
        return result, reader

    def test_identity_requires_exact_process_and_correct_field22(self):
        value = PROBE.parse_identity(identity())
        self.assertEqual(value, {'known': True, 'pid': 5212, 'uid': 10235, 'start_ticks': 453401})
        for raw in (b'', identity(cmdline=b'app.morphe.android.youtube:foreign\0'),
                    identity(cmdline=b'app.morphe.android.youtube\0argument\0'),
                    identity(start=0), identity(start=9223372036854775808),
                    identity().replace(b'Pid:\t5212', b'Pid:\t42'),
                    identity().replace(b'Uid:\t10235', b'Uid:\n10235'),
                    identity().replace(b'10235\t10235\t10235\t10235', b'1\t2\t1\t1'),
                    identity()+PROBE.STAT_MARKER, b'x'*(PROBE.IDENTITY_BYTES+1)):
            with self.subTest(size=len(raw)):
                self.assertFalse(PROBE.parse_identity(raw)['known'])

    def test_owner_boundary_rejects_foreign_nested_and_duplicate_active(self):
        self.assertEqual(PROBE.active_owner(session()), (5212, 10235))
        self.assertIsNone(PROBE.active_owner(session()+session()))
        self.assertIsNone(PROBE.active_owner(session().replace(b'    active=true', b'      active=true')))
        self.assertIsNone(PROBE.active_owner(session().replace(b'    active=true', b'    active=true\n    active=true')))
        self.assertIsNone(PROBE.active_owner(session().replace(PROBE.playback.TARGET_PACKAGE.encode(), b'other.app')))
        self.assertIsNone(PROBE.active_owner(session().replace(b'ownerPid=5212', b'ownerPid=999999999999')))

    def test_installed_zero_padding_schema_accepts_only_exact_package_and_nuls(self):
        # Root's separate bounded in-memory schema read observed a99-byte
        # cmdline whose NUL-stripped bytes equaled the fixed public package.
        # PID/UID/start and all other content below remain synthetic.
        package = PROBE.playback.TARGET_PACKAGE.encode('ascii')
        for cmdline in (package+b'\0', package.ljust(99, b'\0'), package+b'\0'*200):
            with self.subTest(length=len(cmdline)):
                self.assertTrue(PROBE.parse_identity(identity(cmdline=cmdline))['known'])
        for cmdline in (package, b'\0'+package+b'\0', package+b'\0arg\0',
                        package+b'\0\0x\0', package+b':worker\0\0',
                        package+b'x\0\0', package+b'\0 \0', package+b'\0\n\0'):
            with self.subTest(length=len(cmdline)):
                self.assertFalse(PROBE.parse_identity(identity(cmdline=cmdline))['known'])

    def test_stable_process_and_unchanged_playing_position_do_not_prove_frozen(self):
        out, reader = self.run_fixture()
        self.assertTrue(out['identity_stable']); self.assertTrue(out['playing_state_bracket'])
        self.assertFalse(out['reported_position_changed'])
        self.assertFalse(out['live_format_known']); self.assertFalse(out['content_fps_known'])
        self.assertIsNone(out['configured_fps']); self.assertIsNone(out['width'])
        self.assertFalse(out['itag_known']); self.assertFalse(out['source_bitrate_known'])
        self.assertTrue(out['metrics']['historical_queue_only'])
        self.assertFalse(out['codec']['raw_available']); self.assertTrue(out['codec']['command_ok'])
        self.assertEqual(len(reader.arguments), 6)
        self.assertTrue(all(not buffer for buffer in reader.buffers))
        self.assertNotIn(PRIVATE, json.dumps(out))

    def test_numeric_only_result_and_fixed_command_allowlist(self):
        out, reader = self.run_fixture()
        def check(value):
            if isinstance(value, dict):
                for child in value.values(): check(child)
            else:
                self.assertTrue(value is None or type(value) in (bool, int, float))
        check(out)
        self.assertEqual(reader.arguments[0], ([PROBE.IDENTITY_SCRIPT], PROBE.IDENTITY_BYTES))
        self.assertEqual(reader.arguments[2][0], ['dumpsys', '-t', '3', 'media.metrics', '--prefix', 'codec', '--since', '-15'])
        self.assertEqual(reader.arguments[3][0], ['dumpsys', '-t', '3', 'media.codec'])
        self.assertEqual(reader.arguments[5], reader.arguments[0])
        self.assertNotIn('--clear', repr(reader.arguments))

    def test_restart_wrong_owner_pause_and_ambiguous_session_are_separate_unknowns(self):
        cases = [
            ([identity(), session(), b'', b'', session(), identity(start=453402)], 'identity_stable'),
            ([identity(), session(pid=90), b'', b'', session(), identity()], 'session_owner_matches_before'),
            ([identity(), session(), b'', b'', session(state=2, speed=0), identity()], 'playing_state_bracket'),
            ([identity(), session()+session(), b'', b'', session(), identity()], 'session_owner_matches_before')]
        for values, field in cases:
            with self.subTest(field=field):
                out, _ = self.run_fixture(values)
                self.assertFalse(out[field]); self.assertFalse(out['playing_state_bracket'])
                self.assertFalse(out['live_format_known'])

    def test_failed_read_cannot_parse_private_fake_success_payload(self):
        out, reader = self.run_fixture([(identity(), 4), (session(), 2), (PRIVATE.encode(), 3), (b'\xff', 0), session(), identity()])
        self.assertFalse(out['identity_before']['known']); self.assertTrue(out['playback_before']['unknown'])
        self.assertFalse(out['metrics']['raw_available']); self.assertFalse(out['codec']['utf8_valid'])
        self.assertIsNone(out['codec']['line_count'])
        self.assertNotIn(PRIVATE, json.dumps(out)); self.assertTrue(all(not buffer for buffer in reader.buffers))

    def test_invalid_selectors_and_timeout_fail_before_reader_creation(self):
        with mock.patch.object(PROBE, 'BoundedReader') as constructor:
            for timeout in (0, 2.99, 15.01, float('nan'), float('inf')):
                with self.assertRaises(ValueError): PROBE.collect('x', 'emulator-5556', timeout)
            for serial in ('', 'x;PRIVATE', 'a'*129):
                with self.assertRaises(ValueError): PROBE.collect('x', serial)
            constructor.assert_not_called()

    def fake_adb(self, directory, body):
        path = Path(directory)/'synthetic-adb'
        path.write_text('#!'+sys.executable+'\n'+body+'\n')
        path.chmod(0o700)
        return path

    def test_owned_local_fake_process_nonzero_does_not_export_stderr(self):
        with tempfile.TemporaryDirectory() as directory:
            adb = self.fake_adb(directory, 'import sys\nprint("'+PRIVATE+'", file=sys.stderr)\nsys.exit(7)')
            reader = PROBE.BoundedReader(adb, 'synthetic', 5)
            raw, info = reader.read(['fixture'])
            self.assertEqual(info['error_code'], 4); self.assertTrue(info['child_reaped'])
            self.assertFalse(raw); self.assertNotIn(PRIVATE, json.dumps(info))

    def test_per_read_total_bounds_and_no_extra_overflow_byte(self):
        with tempfile.TemporaryDirectory() as directory:
            adb = self.fake_adb(directory, 'import os\nos.write(1,b"x"*65536)')
            reader = PROBE.BoundedReader(adb, 'synthetic', 5)
            raw, info = reader.read(['fixture'], 1024)
            self.assertEqual(len(raw), 1024); self.assertEqual(reader.total_bytes, 1024)
            self.assertEqual(info['error_code'], 3); self.assertTrue(info['child_reaped'])
            reader.total_bytes = PROBE.MAX_TOTAL_BYTES-17
            raw, info = reader.read(['fixture'])
            self.assertEqual(len(raw), 17); self.assertEqual(reader.total_bytes, PROBE.MAX_TOTAL_BYTES)
            self.assertEqual(info['error_code'], 3)
            raw, info = reader.read(['fixture'])
            self.assertEqual(info['error_code'], 6); self.assertFalse(raw)

    def test_deadline_reserves_cleanup_and_timeout_reaps_owned_fake_child(self):
        with tempfile.TemporaryDirectory() as directory:
            adb = self.fake_adb(directory, 'import time\ntime.sleep(60)')
            reader = PROBE.BoundedReader(adb, 'synthetic', 3)
            with mock.patch.object(PROBE, 'COMMAND_NS', 40_000_000):
                started = time.monotonic()
                raw, info = reader.read(['fixture'])
            self.assertEqual(info['error_code'], 2); self.assertTrue(info['child_reaped'])
            self.assertLess(time.monotonic()-started, 2)
            self.assertEqual(reader.deadline_ns-reader.read_deadline_ns, PROBE.CLEANUP_RESERVE_NS)
            reader.read_deadline_ns = time.monotonic_ns()-1
            with mock.patch.object(PROBE.subprocess, 'Popen') as popen:
                raw, info = reader.read(['fixture'])
                self.assertEqual(info['error_code'], 7); popen.assert_not_called()

    def test_unreaped_child_prevents_new_launches(self):
        reader = PROBE.BoundedReader('/not-run', 'synthetic', 3)
        reader.unreaped = True
        with mock.patch.object(PROBE.subprocess, 'Popen') as popen:
            raw, info = reader.read(['fixture'])
        self.assertEqual(info['error_code'], 7); self.assertFalse(raw); popen.assert_not_called()

    def test_cleanup_failure_is_reported_and_blocks_later_launch(self):
        process = mock.Mock()
        process.poll.return_value = None
        process.wait.side_effect = PROBE.subprocess.TimeoutExpired('synthetic-owned-process', 0)
        reader = PROBE.BoundedReader('/not-run', 'synthetic', 3)
        with mock.patch.object(PROBE.subprocess, 'Popen', return_value=process), \
                mock.patch.object(PROBE.os, 'set_blocking'), \
                mock.patch.object(PROBE.selectors, 'DefaultSelector', side_effect=OSError(PRIVATE)):
            raw, info = reader.read(['fixture'])
        self.assertEqual(info['error_code'], 5); self.assertFalse(info['child_reaped'])
        self.assertTrue(reader.unreaped); self.assertFalse(info['command_ok'])
        process.kill.assert_called_once(); process.stdout.close.assert_called_once()
        self.assertNotIn(PRIVATE, json.dumps(info))
        with mock.patch.object(PROBE.subprocess, 'Popen') as popen:
            raw, later = reader.read(['fixture'])
            self.assertEqual(later['error_code'], 7); popen.assert_not_called()

    def test_missing_local_executable_returns_fixed_error_and_empty_raw(self):
        reader = PROBE.BoundedReader('/missing/'+PRIVATE, 'synthetic', 3)
        raw, info = reader.read(['fixture'])
        self.assertEqual(info['error_code'], 1); self.assertFalse(raw)
        self.assertNotIn(PRIVATE, json.dumps(info))


if __name__ == '__main__':
    unittest.main()
