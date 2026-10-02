import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from hardware_stream import (FrameRateBudget, HostHardwareSession,
                             experimental_raw_submit_arguments, raw_submit_budget_configuration,
                             verify_raw_submit_budget_readback, worker)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'experiments/moonlight-v2/transport/android-udp'))
import run_phone_udp as runner
from scripts.probes import run_surface_hint_matrix as matrix


def experiment_report(raw_fps=60, fps=120):
    client = matrix.client_artifacts(True)
    return {'experimental_client': True, 'experimental_encoder': True,
            'client_package': client['client_package'], 'probe_package': client['probe_package'],
            'instrumentation_target_verified': True, 'surface_submit_report_verified': True,
            'requested_encoder_fps': fps, 'requested_raw_submit_fps': raw_fps,
            'raw_submit_budget_readback': dict(raw_submit_budget_configuration(fps, raw_fps),
                                             phone_fps_limit=fps),
            'host_failures': [], 'actual_surface_samples': {'source': {
                'presented_frames': 100, 'seconds': 35., 'display_vsync_ns': 8333333}},
            'phone': {'fps_limit': fps, 'requested_seconds': 35, 'running_at_end': True,
                      'first_server_packet_ns': 1_000_000_000, 'receive_end_ns': 36_000_000_000,
                      'observation_end_ns': 36_000_000_000, 'received_media_frames': 100,
                      'queued_media_frames': 99, 'codec_callback_count': 98,
                      'video_worker_alive': False, 'video_worker_join_timed_out': False}}


class RawSubmitFpsCheck(unittest.TestCase):
    def test_default_omits_override_and_uses_native_fps(self):
        self.assertEqual(experimental_raw_submit_arguments(None, None), [])
        self.assertEqual(experimental_raw_submit_arguments('independent', None, True), [])
        config = raw_submit_budget_configuration(120)
        self.assertIsNone(config['requested_raw_submit_fps'])
        self.assertEqual(config['effective_raw_submit_fps'], 120)
        self.assertEqual(config['native_encoder_fps_arg'], 120)
        self.assertIsNone(runner.raw_submit_experiment_readback(None, {}, 120, None))
        self.assertIsNone(matrix.raw_submit_experiment_verified({}, {}, 120, None))

    def test_override_does_not_clamp_to_encoder_fps_and_only_changes_budget(self):
        for fps, raw in ((120, 30), (120, 60), (60, 120)):
            with self.subTest(fps=fps, raw=raw):
                argv = experimental_raw_submit_arguments('independent', raw, True)
                self.assertEqual(argv, ['--raw-submit-fps', str(raw), '--matched-experimental-client'])
                config = raw_submit_budget_configuration(fps, raw)
                self.assertEqual(config['native_encoder_fps_arg'], fps)
                self.assertEqual(config['effective_raw_submit_fps'], raw)
                self.assertIs(config['grpc_fps_limit_applied'], False)
                now = [0.]
                budget = FrameRateBudget(config['effective_raw_submit_fps'], lambda: now[0])
                budget.consume(); budget.consume()
                self.assertAlmostEqual(budget.delay(), 1 / raw)

    def test_raw_fps_and_client_attestation_reject_type_confusion(self):
        for value in (True, False, 30., '60', 0, 61, [], {}):
            with self.subTest(value_type=type(value).__name__, value=value):
                with self.assertRaises(ValueError):
                    experimental_raw_submit_arguments('independent', value, True)
        for native, matched in ((None, True), ('independent', False), ('independent', 1),
                                ('independent', 'true')):
            with self.assertRaises(ValueError):
                experimental_raw_submit_arguments(native, 60, matched)
        with self.assertRaises(ValueError):
            experimental_raw_submit_arguments(None, None, 0)

    def test_host_and_worker_reject_invalid_override_before_allocations_or_imports(self):
        with patch('hardware_stream.socket.socketpair') as sockets, \
                patch('hardware_stream.subprocess.Popen') as spawn:
            for native, raw, matched in ((None, 60, True), ('independent', 60, False),
                                         ('independent', True, True)):
                with self.assertRaises(ValueError):
                    HostHardwareSession('/unused', 'emulator-5556', 'compare', 1280,
                                        8000000, 120, 'VBR', native_encoder=native,
                                        raw_submit_fps=raw, matched_experimental_client=matched)
                with self.assertRaises(ValueError):
                    worker(SimpleNamespace(native_encoder=native, raw_submit_fps=raw,
                                           matched_experimental_client=matched))
            sockets.assert_not_called()
            spawn.assert_not_called()

    def test_host_argv_preserves_native_fps_and_requires_typed_worker_readback(self):
        for raw in (None, 30, 60, 120):
            with self.subTest(raw=raw):
                pairs = [(Mock(), Mock()) for _ in range(3)]
                for i, (_, remote) in enumerate(pairs, 20):
                    remote.fileno.return_value = i
                reply = {'initialized': True}
                if raw is not None:
                    reply['raw_submit_budget'] = raw_submit_budget_configuration(120, raw)
                process = Mock(stdout=io.BytesIO((json.dumps(reply) + '\n').encode()))
                process.poll.return_value = 0
                with patch('hardware_stream.socket.socketpair', side_effect=pairs), \
                        patch('hardware_stream.pathlib.Path.is_file', return_value=True), \
                        patch('hardware_stream.pathlib.Path.open', return_value=io.BytesIO()), \
                        patch('hardware_stream.os.access', return_value=True), \
                        patch('hardware_stream.select.select', return_value=([process.stdout], [], [])), \
                        patch('hardware_stream.subprocess.Popen', return_value=process) as spawn:
                    session = HostHardwareSession('/unused', 'emulator-5556', 'compare', 1280,
                                                  8000000, 120, 'VBR', native_encoder='/independent',
                                                  raw_submit_fps=raw,
                                                  matched_experimental_client=raw is not None)
                    argv = spawn.call_args.args[0]
                    self.assertEqual(argv[argv.index('--fps') + 1], '120')
                    if raw is None:
                        self.assertNotIn('--raw-submit-fps', argv)
                        self.assertNotIn('--matched-experimental-client', argv)
                        self.assertIsNone(session.raw_submit_budget_readback)
                    else:
                        self.assertEqual(argv[argv.index('--raw-submit-fps') + 1], str(raw))
                        self.assertEqual(session.raw_submit_budget_readback, reply['raw_submit_budget'])
                    session.close()

    def test_worker_readback_rejects_missing_mismatch_and_bool_numeric_equality(self):
        valid = raw_submit_budget_configuration(120, 60)
        self.assertEqual(verify_raw_submit_budget_readback(valid, 120, 60), valid)
        for value in (None, {}, dict(valid, effective_raw_submit_fps=120),
                      dict(valid, effective_raw_submit_fps=60.),
                      dict(valid, grpc_fps_limit_applied=0), dict(valid, native_encoder_fps_arg=True)):
            with self.assertRaises(ValueError):
                verify_raw_submit_budget_readback(value, 120, 60)
        hardware = SimpleNamespace(raw_submit_budget_readback=valid)
        for phone in ({}, {'fps_limit': 60}, {'fps_limit': 120.}, {'fps_limit': True}):
            with self.assertRaises(ValueError):
                runner.raw_submit_experiment_readback(hardware, phone, 120, 60)
        self.assertEqual(runner.raw_submit_experiment_readback(hardware, {'fps_limit': 120}, 120, 60),
                         dict(valid, phone_fps_limit=120))

    def test_runner_cli_requires_independent_encoder_and_experimental_client_before_io(self):
        base = ['runner', '--bind-ip', '192.168.1.1', '--peer-ip', '192.168.1.2',
                '--packetizer', '/unused', '--source', 'fixed', '--output', '/unused/report']
        for extra in (['--raw-submit-fps', '60'],
                      ['--native-encoder', '/independent', '--raw-submit-fps', '60'],
                      ['--experimental-client', '--raw-submit-fps', '60'],
                      ['--experimental-client', '--native-encoder', '/independent', '--raw-submit-fps', '60.0']):
            with patch.object(sys, 'argv', base + extra), \
                    patch.object(runner.subprocess, 'run') as run, \
                    patch.object(runner.subprocess, 'Popen') as spawn, \
                    patch.object(runner.socket, 'socket') as sockets, \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    runner.main()
                self.assertEqual(error.exception.code, 2)
                run.assert_not_called(); spawn.assert_not_called(); sockets.assert_not_called()

    def test_mismatched_instrumentation_stops_raw_override_before_writes_or_launch(self):
        argv = ['runner', '--experimental-client', '--native-encoder', '/independent',
                '--raw-submit-fps', '60', '--fps', '120', '--bind-ip', '192.168.1.1',
                '--peer-ip', '192.168.1.2', '--packetizer', '/unused', '--source', 'fixed',
                '--output', '/unused/report']
        with patch.object(sys, 'argv', argv), patch.object(sys, 'platform', 'darwin'), \
                patch.object(Path, 'is_file', return_value=True), patch.object(runner.os, 'access', return_value=True), \
                patch.object(runner.subprocess, 'run', return_value=SimpleNamespace(stdout=b'')) as read, \
                patch.object(runner.subprocess, 'Popen') as spawn, \
                patch.object(runner.socket, 'socket') as sockets:
            with self.assertRaises(RuntimeError):
                runner.main()
            self.assertEqual(read.call_count, 1)
            self.assertEqual(read.call_args.args[0][-4:], ['shell', 'pm', 'list', 'instrumentation'])
            spawn.assert_not_called(); sockets.assert_not_called()

    def test_runner_phone_config_keeps_fps_and_only_host_receives_raw_override(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'failed.json'
            argv = ['runner', '--experimental-client', '--native-encoder', '/independent',
                    '--raw-submit-fps', '60', '--fps', '120', '--bind-ip', '192.168.1.1',
                    '--peer-ip', '192.168.1.2', '--packetizer', '/unused', '--source', 'fixed',
                    '--output', str(output)]
            phone_configs = []
            def root_read(command, **kw):
                if 'input' in kw:
                    # Inspect only public configuration fields, never retain the generated key.
                    value = json.loads(kw['input'])
                    phone_configs.append({key: value[key] for key in ('fps', 'buffer_ms')})
                    self.assertNotIn('raw_submit_fps', value)
                return SimpleNamespace(stdout=b'10001\n')
            udp = Mock()
            udp.getsockopt.return_value = 9
            phone = Mock()
            with patch.object(sys, 'argv', argv), patch.object(sys, 'platform', 'darwin'), \
                    patch.object(Path, 'is_file', return_value=True), patch.object(runner.os, 'access', return_value=True), \
                    patch.object(runner, 'read_instrumentation_target', return_value=True), \
                    patch.object(runner, 'initialize_experimental_client_files'), \
                    patch.object(runner.subprocess, 'run', side_effect=root_read), \
                    patch.object(runner.subprocess, 'Popen', return_value=phone), \
                    patch.object(runner.socket, 'socket', return_value=udp), \
                    patch.object(runner.socket, 'if_nametoindex', return_value=9), \
                    patch.object(runner, 'open_packet', return_value=b'READY'), \
                    patch.object(runner, 'HostHardwareSession', side_effect=RuntimeError('test startup stop')) as host:
                with self.assertRaises(SystemExit):
                    runner.main()
                self.assertEqual(host.call_args.args[5], 120)
                self.assertEqual(host.call_args.kwargs['raw_submit_fps'], 60)
                self.assertIs(host.call_args.kwargs['matched_experimental_client'], True)
            self.assertEqual(phone_configs, [{'fps': 120, 'buffer_ms': 80}])
            report = json.loads(output.read_text())
            self.assertEqual(report['requested_encoder_fps'], 120)
            self.assertEqual(report['requested_raw_submit_fps'], 60)
            self.assertFalse(report['valid_playback_test'])

    def test_native_rate_readback_is_whitelisted_and_typed(self):
        good = {'event': 'ready', 'fps_expected': 120, 'expected_frame_rate_read_status': 0,
                'expected_frame_rate_readback': 120.}
        bad = {'event': 'summary', 'fps_expected': True, 'expected_frame_rate_read_status': False,
               'expected_frame_rate_readback': 'SENSITIVE_SENTINEL', 'unrelated': 'SENSITIVE_SENTINEL'}
        log = SimpleNamespace(read_text=lambda **kw: '\n'.join(map(json.dumps, [good, bad])))
        self.assertEqual(runner.encoder_readback(log), [good, {'event': 'summary'}])

    def test_matrix_rejects_unmatched_client_before_mkdir_or_source_io(self):
        argv = ['matrix', '--encoder', '/independent', '--packetizer', '/unused',
                '--raw-submit-fps', '60', '--output-dir', '/unused/matrix']
        with patch.object(sys, 'argv', argv), patch.object(Path, 'mkdir') as mkdir, \
                patch.object(matrix.subprocess, 'run') as run, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                matrix.main()
            self.assertEqual(error.exception.code, 2)
            mkdir.assert_not_called(); run.assert_not_called()

    def test_matrix_typed_report_validation_requires_all_fps_and_fixed_artifacts(self):
        report = experiment_report()
        client = matrix.client_artifacts(True)
        self.assertTrue(matrix.raw_submit_experiment_verified(report, client, 120, 60))
        cases = [('report', 'requested_raw_submit_fps', 60.),
                 ('report', 'requested_encoder_fps', True), ('report', 'experimental_encoder', False),
                 ('report', 'instrumentation_target_verified', False),
                 ('readback', 'effective_raw_submit_fps', 120), ('readback', 'native_encoder_fps_arg', 60),
                 ('readback', 'grpc_fps_limit_applied', 0), ('readback', 'phone_fps_limit', 120.),
                 ('phone', 'fps_limit', 60)]
        for target, key, value in cases:
            bad = copy.deepcopy(report)
            dest = bad if target == 'report' else bad['raw_submit_budget_readback' if target == 'readback' else 'phone']
            dest[key] = value
            with self.subTest(target=target, key=key):
                self.assertFalse(matrix.raw_submit_experiment_verified(bad, client, 120, 60))

    def test_matrix_propagates_only_raw_override_and_invalid_readback_blocks_acceptance(self):
        for raw, bad_readback in ((None, False), (60, False), (120, False), (60, True)):
            with self.subTest(raw=raw, bad=bad_readback), tempfile.TemporaryDirectory() as directory:
                commands = []
                output = Path(directory)
                argv = ['matrix', '--experimental-client', '--encoder', '/independent',
                        '--packetizer', '/unused', '--output-dir', str(output), '--rounds', '1',
                        '--hints', '120', '--fps', '120', '--no-trace']
                if raw is not None:
                    argv += ['--raw-submit-fps', str(raw)]
                def fake_run(command, **kw):
                    if len(command) > 1 and str(command[1]).endswith('run_phone_udp.py'):
                        commands.append(command)
                        report = experiment_report(raw)
                        if bad_readback:
                            report.pop('raw_submit_budget_readback')
                        Path(command[command.index('--output') + 1]).write_text(json.dumps(report))
                    return SimpleNamespace(returncode=0)
                with patch.object(sys, 'argv', argv), patch.object(matrix, 'fingerprints', return_value={}), \
                        patch.object(matrix, 'require_no_source_capture'), \
                        patch.object(matrix, 'collect', return_value={'command_ok': True, 'unknown': False,
                            'state_known': True, 'active_sessions': 1, 'state': 3}), \
                        patch.object(matrix.subprocess, 'run', side_effect=fake_run), \
                        patch.object(matrix.time, 'sleep'), patch.object(Path, 'read_bytes', return_value=b'fixed'), \
                        patch.object(matrix, 'summarize', return_value={'verified': True}) as summarize, \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(matrix.main(), 1 if bad_readback else 0)
                    self.assertEqual(summarize.call_count, 0 if bad_readback else 1)
                child = commands[0]
                self.assertEqual(child[child.index('--fps') + 1], '120')
                if raw is None:
                    self.assertNotIn('--raw-submit-fps', child)
                else:
                    self.assertEqual(child[child.index('--raw-submit-fps') + 1], str(raw))
                manifest = json.loads((output / 'matrix.json').read_text())
                self.assertEqual(manifest['conditions']['video_fps_cap'], 120)
                self.assertEqual(manifest['conditions']['requested_raw_submit_fps'], raw)
                row = manifest['runs'][0]
                self.assertEqual(row['valid_real_video_test'], not bad_readback)
                self.assertIs(row['raw_submit_budget_verified'], None if raw is None else not bad_readback)
                if bad_readback:
                    self.assertIn('failed_raw_submit_budget_readback', row['playback_validation']['reasons'])


if __name__ == '__main__':
    unittest.main()
