import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'experiments/moonlight-v2/transport/android-udp'))
import run_phone_udp as runner
from scripts.probes import build_phone_transport_probe as builder
from scripts.probes import run_surface_hint_matrix as matrix


def completed_playback_report(seconds=35):
    first = 1_000_000_000
    return {'host_failures': [], 'actual_surface_samples': {'source': {
        'presented_frames': 100, 'seconds': 35.0, 'display_vsync_ns': 8333333}}, 'phone': {
        'requested_seconds': seconds, 'running_at_end': True,
        'first_server_packet_ns': first, 'receive_end_ns': first+seconds*1_000_000_000,
        'observation_end_ns': first+(seconds+1)*1_000_000_000,
        'received_media_frames': 100, 'queued_media_frames': 99, 'codec_callback_count': 98,
        'video_worker_failure_class': '', 'video_worker_alive': False,
        'video_worker_join_timed_out': False, 'udp_audio': {'failure_class': ''}}}


def playing_source_state():
    return {'command_ok': True, 'unknown': False, 'state_known': True,
            'active_sessions': 1, 'state': 3}


class ExperimentalPhoneClientCheck(unittest.TestCase):
    def test_fixed_package_pairs_and_all_data_paths_are_isolated(self):
        default = runner.client_configuration()
        experiment = runner.client_configuration(True)
        self.assertEqual(default['component'], runner.COMPONENT)
        self.assertEqual(default['session_file'], runner.SESSION_FILE)
        self.assertEqual(default['report_file'], runner.REPORT_FILE)
        self.assertEqual(experiment['client_package'], 'local.remoteandroid.direct.experiment')
        self.assertEqual(experiment['probe_package'], 'local.remoteandroid.phoneprobe.experiment')
        for key in ('component', 'session_file', 'report_file'):
            self.assertNotEqual(default[key], experiment[key])
        self.assertIn('/local.remoteandroid.direct.UdpVideoProbe', experiment['component'])
        with self.assertRaises(ValueError):
            runner.client_configuration('true')

    def test_installed_target_requires_one_exact_component_and_target(self):
        for experimental in (False, True):
            config = runner.client_configuration(experimental)
            line = f"instrumentation:{config['component']} (target={config['client_package']})\n"
            self.assertTrue(runner.verify_instrumentation_target(
                'instrumentation:other.package/Other (target=other.target)\n'+line, experimental))
            for bad in ('', line+line, line.replace(config['client_package']+')', 'wrong.target)'),
                        line.replace(' (target=', ' malformed='), 'x'*65537):
                with self.subTest(experimental=experimental, case=len(bad)):
                    with self.assertRaises((ValueError, RuntimeError)):
                        runner.verify_instrumentation_target(bad, experimental)

    def test_instrumentation_preflight_is_read_only_and_bounded(self):
        config = runner.client_configuration(True)
        stdout = f"instrumentation:{config['component']} (target={config['client_package']})".encode()
        with patch.object(runner.subprocess, 'run', return_value=SimpleNamespace(stdout=stdout)) as read:
            self.assertTrue(runner.read_instrumentation_target(['adb', '-s', 'phone'], True))
        self.assertEqual(read.call_args.args[0], ['adb', '-s', 'phone', 'shell', 'pm', 'list', 'instrumentation'])
        self.assertEqual(read.call_args.kwargs['timeout'], 5)
        self.assertTrue(read.call_args.kwargs['check'])

    def test_directory_initialization_is_fixed_nonrecursive_and_opt_in(self):
        root = Mock()
        self.assertFalse(runner.initialize_experimental_client_files(root, '10001'))
        root.assert_not_called()
        self.assertTrue(runner.initialize_experimental_client_files(root, '10001', True))
        directory = '/data/user/0/local.remoteandroid.direct.experiment/files'
        self.assertEqual(root.call_args.args[0], 'mkdir -p '+directory+' && chown 10001:10001 '+directory+
                         ' && chmod 700 '+directory+' && restorecon '+directory)
        self.assertEqual(root.call_args.kwargs['timeout'], 5)
        for uid in ('10001; echo invalid', '\u0661', '', None):
            root.reset_mock()
            with self.assertRaises(ValueError):
                runner.initialize_experimental_client_files(root, uid, True)
            root.assert_not_called()

    def test_startup_failure_records_failed_result_and_masks_command(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'failed.json'
            argv = ['runner', '--experimental-client', '--bind-ip', '192.168.1.1', '--peer-ip',
                    '192.168.1.2', '--packetizer', '/unused/packetizer', '--source', 'fixed-test',
                    '--output', str(output)]
            error = runner.subprocess.CalledProcessError(1, ['SENSITIVE_SENTINEL'], stderr=b'SENSITIVE_SENTINEL')
            with patch.object(sys, 'argv', argv), patch.object(sys, 'platform', 'darwin'), \
                    patch.object(Path, 'is_file', return_value=True), patch.object(runner.os, 'access', return_value=True), \
                    patch.object(runner, 'read_instrumentation_target', return_value=True), \
                    patch.object(runner.subprocess, 'run', return_value=SimpleNamespace(stdout=b'10001\n')), \
                    patch.object(runner, 'initialize_experimental_client_files', side_effect=error), \
                    patch.object(runner.socket, 'socket') as socket, patch.object(runner.subprocess, 'Popen') as spawn:
                with self.assertRaises(SystemExit) as stopped:
                    runner.main()
                self.assertEqual(str(stopped.exception), 'UDP component failed: CalledProcessError')
                socket.assert_not_called()
                spawn.assert_not_called()
            report = json.loads(output.read_text())
            self.assertFalse(report['valid_playback_test'])
            self.assertIsNone(report['phone'])
            self.assertEqual(report['report_failure_class'], 'CalledProcessError')
            self.assertNotIn('SENSITIVE_SENTINEL', output.read_text())

    def test_mismatched_target_stops_runner_before_writes_or_processes(self):
        argv = ['runner', '--experimental-client', '--bind-ip', '192.168.1.1', '--peer-ip',
                '192.168.1.2', '--packetizer', '/unused/packetizer', '--source', 'fixed-test',
                '--output', '/unused/output.json']
        with patch.object(sys, 'argv', argv), patch.object(sys, 'platform', 'darwin'), \
                patch.object(Path, 'is_file', return_value=True), patch.object(runner.os, 'access', return_value=True), \
                patch.object(runner.subprocess, 'run', return_value=SimpleNamespace(stdout=b'')) as read, \
                patch.object(runner.subprocess, 'Popen') as spawn, patch.object(runner.socket, 'socket') as socket:
            with self.assertRaises(RuntimeError):
                runner.main()
            self.assertEqual(read.call_count, 1)
            self.assertEqual(read.call_args.args[0][-4:], ['shell', 'pm', 'list', 'instrumentation'])
            spawn.assert_not_called()
            socket.assert_not_called()

    def test_formal_client_rejects_nonzero_submission_before_io(self):
        argv = ['runner', '--bind-ip', '192.168.1.1', '--peer-ip', '192.168.1.2',
                '--packetizer', '/unused/packetizer', '--source', 'fixed-test', '--output',
                '/unused/output.json', '--surface-submit-lead-ms', '8']
        with patch.object(sys, 'argv', argv), patch.object(runner.subprocess, 'run') as read, \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                runner.main()
            self.assertEqual(error.exception.code, 2)
            read.assert_not_called()

    def test_experimental_surface_readback_requires_matching_lead_and_mode(self):
        for lead, status in ((0, 'disabled_existing_release_path'), (8, 'applied_bounded_wait'),
                             (8, 'enabled_no_wait_observed'), (16, 'applied_bounded_wait')):
            self.assertTrue(runner.verify_surface_submit_report(
                {'surface_submit_lead_ms': lead, 'surface_submit_status': status}, lead, True))
        for report in ({}, {'surface_submit_lead_ms': 0, 'surface_submit_status': 'applied_bounded_wait'},
                       {'surface_submit_lead_ms': 8, 'surface_submit_status': 'unknown'},
                       {'surface_submit_lead_ms': True, 'surface_submit_status': 'disabled_existing_release_path'},
                       {'surface_submit_lead_ms': 16, 'surface_submit_status': 'applied_bounded_wait'}):
            with self.assertRaises(ValueError):
                runner.verify_surface_submit_report(report, 8, True)
        self.assertFalse(runner.verify_surface_submit_report({}, 0, False))

    def test_builder_retargets_exact_manifest_without_modifying_source(self):
        source = builder.SOURCE/'AndroidManifest.xml'
        before = source.read_bytes()
        result = ET.fromstring(builder.experimental_manifest(before.decode()))
        self.assertEqual(result.get('package'), 'local.remoteandroid.phoneprobe.experiment')
        target = '{'+builder.ANDROID_NAMESPACE+'}targetPackage'
        self.assertEqual(len(result.findall('instrumentation')), 3)
        self.assertEqual({item.get(target) for item in result.findall('instrumentation')},
                         {'local.remoteandroid.direct.experiment'})
        self.assertEqual(source.read_bytes(), before)
        with self.assertRaises(ValueError):
            builder.experimental_manifest(before.decode().replace('local.remoteandroid.phoneprobe', 'wrong.package'))

    def test_matrix_forwards_flag_and_hashes_matching_probe(self):
        for experimental in (False, True):
            with self.subTest(experimental=experimental), tempfile.TemporaryDirectory() as directory:
                commands = []
                output = Path(directory)
                argv = ['matrix', '--packetizer', '/unused/packetizer', '--encoder', '/unused/encoder',
                        '--output-dir', str(output), '--rounds', '1', '--hints', '60', '--no-trace',
                        '--surface-submit-leads', '0'] + (['--experimental-client'] if experimental else [])
                def run(command, **kwargs):
                    if str(command[1]).endswith('run_phone_udp.py'):
                        commands.append(command)
                        client = matrix.client_artifacts(experimental)
                        report = dict(completed_playback_report(), experimental_client=experimental,
                                      client_package=client['client_package'], probe_package=client['probe_package'],
                                      instrumentation_target_verified=True, surface_submit_report_verified=experimental)
                        Path(command[command.index('--output')+1]).write_text(json.dumps(report))
                    return SimpleNamespace(returncode=0)
                def read_bytes(path):
                    return b'experimental-probe' if '/experimental/' in str(path) else b'default-artifact'
                with patch.object(sys, 'argv', argv), patch.object(matrix, 'fingerprints', return_value={}), \
                        patch.object(matrix, 'require_no_source_capture'), patch.object(matrix, 'collect', return_value=playing_source_state()), \
                        patch.object(matrix.subprocess, 'run', side_effect=run), patch.object(matrix.time, 'sleep'), \
                        patch.object(matrix, 'summarize', return_value={'verified': True}), \
                        patch.object(Path, 'read_bytes', read_bytes), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(matrix.main(), 0)
                manifest = json.loads((output/'matrix.json').read_text())
                self.assertEqual('--experimental-client' in commands[0], experimental)
                expected = b'experimental-probe' if experimental else b'default-artifact'
                self.assertEqual(manifest['binaries']['probe'], hashlib.sha256(expected).hexdigest())
                self.assertEqual(manifest['client_package'], matrix.client_artifacts(experimental)['client_package'])
                defaults = {'--serial': '3B15AL00M9U00000', '--bind-ip': '192.168.9.128',
                            '--peer-ip': '192.168.9.6', '--interface': 'en7', '--fps': '60'}
                for flag, value in defaults.items():
                    self.assertEqual(commands[0][commands[0].index(flag)+1], value)
                self.assertEqual(manifest['conditions']['video_fps_cap'], 60)
                self.assertEqual(manifest['phone']['requested_serial'], defaults['--serial'])
                self.assertEqual(manifest['phone']['requested_label'], 'physical Android phone')
                self.assertEqual(manifest['transport'], {'bind_ip': defaults['--bind-ip'],
                                 'peer_ip': defaults['--peer-ip'], 'interface': defaults['--interface']})

    def test_matrix_forwards_selected_phone_network_and_120_cap_without_retargeting_source(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            commands = []
            argv = ['matrix', '--packetizer', '/unused/packetizer', '--encoder', '/unused/encoder',
                    '--output-dir', str(output), '--rounds', '1', '--hints', '120', '--no-trace',
                    '--serial', 'f7fc9469', '--phone-label', 'OnePlus12 PJD110',
                    '--bind-ip', '192.168.9.128', '--peer-ip', '192.168.9.12', '--interface', 'en0',
                    '--fps', '120', '--restart-source']
            def run(command, **kwargs):
                commands.append(command)
                if str(command[1]).endswith('run_phone_udp.py'):
                    Path(command[command.index('--output')+1]).write_text(json.dumps(completed_playback_report()))
                return SimpleNamespace(returncode=0)
            with patch.object(sys, 'argv', argv), patch.object(matrix, 'fingerprints', return_value={}), \
                    patch.object(matrix, 'require_no_source_capture'), \
                    patch.object(matrix, 'collect', return_value=playing_source_state()) as collect, \
                    patch.object(matrix.subprocess, 'run', side_effect=run), patch.object(matrix.time, 'sleep'), \
                    patch.object(matrix, 'summarize', return_value={'verified': True}), \
                    patch.object(Path, 'read_bytes', return_value=b'fixed-artifact'), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(matrix.main(), 0)
            child = next(command for command in commands if str(command[1]).endswith('run_phone_udp.py'))
            requested = {'--serial': 'f7fc9469', '--bind-ip': '192.168.9.128', '--peer-ip': '192.168.9.12',
                         '--interface': 'en0', '--fps': '120', '--content-hint-fps': '120'}
            for flag, value in requested.items():
                self.assertEqual(child[child.index(flag)+1], value)
            self.assertIn('OnePlus12 PJD110', child[child.index('--source')+1])
            self.assertTrue(all(command[command.index('-s')+1] == 'emulator-5556'
                                for command in commands if '-s' in command))
            self.assertEqual(collect.call_count, 2)
            self.assertTrue(all(call.args[1] == 'emulator-5556' for call in collect.call_args_list))
            manifest = json.loads((output/'matrix.json').read_text())
            self.assertEqual(manifest['source_serial'], 'emulator-5556')
            self.assertEqual(manifest['phone'], {'requested_serial': 'f7fc9469',
                                               'requested_label': 'OnePlus12 PJD110'})
            self.assertEqual(manifest['transport'], {'bind_ip': '192.168.9.128', 'peer_ip': '192.168.9.12',
                                                   'interface': 'en0'})
            self.assertEqual(manifest['conditions']['video_fps_cap'], 120)
            self.assertEqual(manifest['conditions']['hint_order_first_round'], [120])
            self.assertNotIn('OnePlus15', manifest['scope'])
            row = manifest['runs'][0]
            self.assertTrue(row['valid_playback_test'])
            self.assertEqual(row['experiment_validation_status'], 'passed_playback_acceptance')
            self.assertEqual(set(row['missing_measurements']),
                             {'phone_surface_fps', 'phone_surface_timeline'})

    def test_idle_heartbeat_completion_is_not_real_video_evidence(self):
        report = completed_playback_report()
        before, after = playing_source_state(), playing_source_state()
        self.assertTrue(matrix.real_source_validation(before, after, report)['passed'])
        idle_report = completed_playback_report()
        idle_report['actual_surface_samples']['source'] = {'measurement_available': False, 'exit_code': 1}
        unknown = {'command_ok': True, 'unknown': True, 'state_known': False,
                   'active_sessions': 0, 'state': None}
        # Receiving idle-screen heartbeats for 35s must not validate a video.
        self.assertTrue(matrix.playback_validation(idle_report, 35)['passed'])
        validation = matrix.real_source_validation(unknown, unknown, idle_report)
        self.assertFalse(validation['passed'])
        self.assertEqual(set(validation['reasons']), {
            'source_playing_unverified_before', 'source_playing_unverified_after',
            'source_video_surface_progress_unverified'})
        for key, value in (('state', True), ('state', 2), ('command_ok', False),
                           ('state_known', False), ('active_sessions', 2), ('active_sessions', True)):
            with self.subTest(key=key):
                self.assertFalse(matrix.real_source_validation(before, dict(after, **{key: value}), report)['passed'])
        for seconds in (0, 1, 30.99, float('nan'), float('inf'), True):
            short = completed_playback_report()
            short['actual_surface_samples']['source']['seconds'] = seconds
            self.assertFalse(matrix.real_source_validation(before, after, short)['passed'])
        self.assertTrue(matrix.real_source_validation(before, after, report)['passed'])

    def test_matrix_acceptance_rejects_failure_and_missing_completion_evidence(self):
        bad_cases = (
            ('phone', 'failure_class', 'IOException', 'phone_failure'),
            ('phone', 'video_worker_failure_class', 'IllegalStateException', 'video_worker_failure'),
            ('phone', 'udp_audio', {'failure_class': 'IllegalStateException'}, 'audio_worker_failure'),
            ('phone', 'running_at_end', None, 'phone_not_running_at_end'),
            ('phone', 'running_at_end', False, 'phone_not_running_at_end'),
            ('phone', 'video_worker_join_timed_out', True, 'video_worker_join_timed_out'),
            ('phone', 'video_worker_alive', True, 'video_worker_alive'),
            ('phone', 'codec_callback_count', 0, 'missing_video_progress'),
            ('phone', 'requested_seconds', 45, 'phone_requested_duration_mismatch'),
            ('report', 'host_failures', [{'stage': 'udp_send', 'error_type': 'ConnectionRefusedError'}],
             'host_failures_present'),
            ('report', 'host_failures', None, 'missing_or_invalid_host_failure_readback'),
            ('report', 'valid_playback_test', False, 'child_report_marked_invalid'))
        for target, key, value, reason in bad_cases:
            with self.subTest(target=target, key=key, value=value):
                report = completed_playback_report()
                (report['phone'] if target == 'phone' else report)[key] = value
                validation = matrix.playback_validation(report, 35)
                self.assertFalse(validation['passed'])
                self.assertIn(reason, validation['reasons'])

    def test_matrix_duration_uses_phone_window_and_explicit_tolerance(self):
        for duration_ns, accepted in ((35_000_000_000, True), (34_000_000_000, True),
                                      (33_999_999_999, False), (960_193_000, False)):
            with self.subTest(duration_ns=duration_ns):
                report = completed_playback_report()
                report['phone']['receive_end_ns'] = report['phone']['first_server_packet_ns']+duration_ns
                # Long teardown must not hide an early receive-loop exit.
                report['phone']['observation_end_ns'] = report['phone']['first_server_packet_ns']+40_000_000_000
                validation = matrix.playback_validation(report, 35)
                self.assertEqual(validation['passed'], accepted)
                self.assertEqual(validation['coverage']['tolerance_s'], 1.0)
                self.assertEqual(validation['coverage']['basis'], 'phone_first_packet_to_receive_end')
        for omit_receive_end in (True, False):
            with self.subTest(omit_receive_end=omit_receive_end):
                report = completed_playback_report()
                if omit_receive_end:
                    del report['phone']['receive_end_ns']
                else:
                    report['phone']['receive_end_ns'] = 0
                validation = matrix.playback_validation(report, 35)
                self.assertTrue(validation['passed'])
                self.assertEqual(validation['coverage']['basis'], 'phone_first_packet_to_observation_end')
        for key, value in (('first_server_packet_ns', 0), ('first_server_packet_ns', True),
                           ('receive_end_ns', None), ('receive_end_ns', True),
                           ('receive_end_ns', -1), ('observation_end_ns', 1)):
            with self.subTest(key=key, value=value):
                report = completed_playback_report()
                report['phone'][key] = value
                self.assertFalse(matrix.playback_validation(report, 35)['passed'])

    def test_matrix_retains_matched_exit_zero_failures_without_success_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            argv = ['matrix', '--packetizer', '/unused/packetizer', '--encoder', '/unused/encoder',
                    '--experimental-client', '--output-dir', str(output), '--rounds', '2',
                    '--hints', '60', '120', '--no-trace']
            def run(command, **kwargs):
                if str(command[1]).endswith('run_phone_udp.py'):
                    client = matrix.client_artifacts(True)
                    report = dict(completed_playback_report(), experimental_client=True,
                                  client_package=client['client_package'], probe_package=client['probe_package'],
                                  instrumentation_target_verified=True, surface_submit_report_verified=True,
                                  valid_playback_test=True)
                    report['host_failures'] = [{'stage': 'udp_send', 'error_type': 'ConnectionRefusedError'}]
                    report['phone'].update(failure_class='IOException', video_worker_failure_class='IllegalStateException',
                                           receive_end_ns=1_960_193_000, observation_end_ns=1_965_708_000)
                    del report['phone']['running_at_end']
                    Path(command[command.index('--output')+1]).write_text(json.dumps(report))
                return SimpleNamespace(returncode=0)
            with patch.object(sys, 'argv', argv), patch.object(matrix, 'fingerprints', return_value={}), \
                    patch.object(matrix, 'require_no_source_capture'), patch.object(matrix, 'collect', return_value={'state': 4}), \
                    patch.object(matrix.subprocess, 'run', side_effect=run), patch.object(matrix.time, 'sleep'), \
                    patch.object(Path, 'read_bytes', return_value=b'fixed-artifact'), \
                    patch.object(matrix, 'summarize') as summarize, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(matrix.main(), 1)
                summarize.assert_not_called()
            manifest = json.loads((output/'matrix.json').read_text())
            self.assertTrue(manifest['matrix_failed'])
            self.assertEqual(len(manifest['runs']), 4)
            for row in manifest['runs']:
                self.assertEqual(row['exit_code'], 0)
                self.assertTrue(row['matched_experimental_client_verified'])
                self.assertFalse(row['valid_playback_test'])
                self.assertNotIn('summary', row)
                self.assertEqual(row['playback_validation']['coverage']['observed_seconds'], 0.960193)
                self.assertEqual(set(row['playback_validation']['reasons']),
                                 {'host_failures_present', 'phone_failure', 'video_worker_failure',
                                  'phone_not_running_at_end', 'phone_observation_too_short'})

    def test_matrix_rejects_unsupported_cap_hint_and_interface_before_io(self):
        base = ['matrix', '--packetizer', '/unused/packetizer', '--encoder', '/unused/encoder',
                '--output-dir', '/unused/output']
        for extra in (['--fps', '90'], ['--hints', '90'], ['--interface', 'en9']):
            with self.subTest(extra=extra), patch.object(sys, 'argv', base+extra), \
                    patch.object(matrix.subprocess, 'run') as run, \
                    patch.object(matrix, 'collect') as collect, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    matrix.main()
                self.assertEqual(error.exception.code, 2)
                run.assert_not_called()
                collect.assert_not_called()

    def test_matrix_rejects_failed_or_unmatched_experiment_report(self):
        client = matrix.client_artifacts(True)
        good = {'experimental_client': True, 'client_package': client['client_package'],
                'probe_package': client['probe_package'], 'instrumentation_target_verified': True,
                'surface_submit_report_verified': True, 'phone': {}}
        self.assertTrue(matrix.matched_experimental_report(good, client))
        for key, value in (('experimental_client', False), ('client_package', 'local.remoteandroid.direct'),
                           ('instrumentation_target_verified', False), ('surface_submit_report_verified', False),
                           ('phone', None), ('valid_playback_test', False)):
            self.assertFalse(matrix.matched_experimental_report(dict(good, **{key: value}), client))

    def test_matrix_retains_four_failed_children_and_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            argv = ['matrix', '--packetizer', '/unused/packetizer', '--encoder', '/unused/encoder',
                    '--experimental-client', '--output-dir', str(output), '--rounds', '2',
                    '--hints', '60', '--no-trace', '--surface-submit-leads', '0', '8']
            def run(command, **kwargs):
                if str(command[1]).endswith('run_phone_udp.py'):
                    Path(command[command.index('--output')+1]).write_text(json.dumps(
                        {'valid_playback_test': False, 'phone': None, 'report_failure_class': 'CalledProcessError'}))
                    return SimpleNamespace(returncode=1)
                return SimpleNamespace(returncode=0)
            with patch.object(sys, 'argv', argv), patch.object(matrix, 'fingerprints', return_value={}), \
                    patch.object(matrix, 'require_no_source_capture'), patch.object(matrix, 'collect', return_value={'state': 4}), \
                    patch.object(matrix.subprocess, 'run', side_effect=run), patch.object(matrix.time, 'sleep'), \
                    patch.object(Path, 'read_bytes', return_value=b'fixed-artifact'), \
                    patch.object(matrix, 'summarize') as summarize, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(matrix.main(), 1)
                summarize.assert_not_called()
            manifest = json.loads((output/'matrix.json').read_text())
            self.assertTrue(manifest['matrix_failed'])
            self.assertEqual(len(manifest['runs']), 4)
            self.assertEqual([item['surface_submit_lead_ms'] for item in manifest['runs']], [0, 8, 8, 0])
            self.assertTrue(all(item['exit_code'] == 1 and item['valid_playback_test'] is False
                                for item in manifest['runs']))


if __name__ == '__main__':
    unittest.main()
