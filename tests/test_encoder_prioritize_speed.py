import contextlib
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from hardware_stream import (HostHardwareSession,
                             experimental_prioritize_speed_arguments, worker)

DIRECTORY = Path(__file__).resolve().parents[1] / 'experiments/moonlight-v2/transport/android-udp'
sys.path.insert(0, str(DIRECTORY))
import run_phone_udp


class EncoderPrioritizeSpeedCheck(unittest.TestCase):
    def test_omitted_option_preserves_deployed_and_experimental_defaults(self):
        self.assertEqual(experimental_prioritize_speed_arguments(None, None), [])
        self.assertEqual(experimental_prioritize_speed_arguments('independent', None), [])
        self.assertEqual(experimental_prioritize_speed_arguments('independent', True),
                         ['--prioritize-speed', 'true'])
        self.assertEqual(experimental_prioritize_speed_arguments('independent', False),
                         ['--prioritize-speed', 'false'])

    def test_explicit_false_also_requires_an_independent_encoder_and_real_boolean(self):
        for native, value in ((None, True), (None, False), ('independent', 'false'),
                              ('independent', 0), ('independent', 1), ('independent', [])):
            with self.subTest(native=native, value_type=type(value).__name__):
                with self.assertRaises(ValueError):
                    experimental_prioritize_speed_arguments(native, value)

    def test_host_rejects_deployed_option_before_allocating_or_spawning(self):
        with patch('hardware_stream.socket.socketpair') as sockets, \
                patch('hardware_stream.subprocess.Popen') as process:
            for value in (True, False):
                with self.assertRaises(ValueError):
                    HostHardwareSession('/unused/runtime', 'emulator-5556', 'compare',
                                        1920, 8000000, 60, 'VBR', prioritize_speed=value)
            sockets.assert_not_called()
            process.assert_not_called()

    def test_host_worker_argv_distinguishes_omitted_true_and_false(self):
        for value in (None, True, False):
            with self.subTest(value=value):
                pairs = [(Mock(), Mock()) for _ in range(3)]
                for number, (_, remote) in enumerate(pairs, 20):
                    remote.fileno.return_value = number
                process = Mock(stdout=io.BytesIO(b'{"initialized":true}\n'))
                process.poll.return_value = 0
                with patch('hardware_stream.socket.socketpair', side_effect=pairs), \
                        patch('hardware_stream.pathlib.Path.is_file', return_value=True), \
                        patch('hardware_stream.pathlib.Path.open', return_value=io.BytesIO()), \
                        patch('hardware_stream.os.access', return_value=True), \
                        patch('hardware_stream.select.select', return_value=([process.stdout], [], [])), \
                        patch('hardware_stream.subprocess.Popen', return_value=process) as spawn:
                    session = HostHardwareSession('/unused/runtime', 'emulator-5556', 'compare',
                                                  1920, 8000000, 60, 'VBR',
                                                  native_encoder='/independent/encoder',
                                                  prioritize_speed=value)
                    argv = spawn.call_args.args[0]
                    if value is None:
                        self.assertNotIn('--encoder-prioritize-speed', argv)
                    else:
                        index = argv.index('--encoder-prioritize-speed')
                        self.assertEqual(argv[index + 1], 'true' if value else 'false')
                    self.assertNotIn('--prioritize-speed', argv)
                    session.close()

    def test_worker_rejects_bad_option_or_deployed_encoder_before_runtime_imports(self):
        for option, native in (('false', None), ('true', None), ('invalid', 'independent')):
            with self.subTest(option=option, native=native):
                with self.assertRaises(ValueError):
                    worker(SimpleNamespace(encoder_prioritize_speed=option, native_encoder=native))

    def test_runner_rejects_explicit_option_without_native_before_device_io(self):
        argv = ['run_phone_udp.py', '--bind-ip', '192.168.9.128', '--peer-ip', '192.168.9.6',
                '--packetizer', '/unused/packetizer', '--source', 'fixed public source',
                '--output', '/unused/report.json', '--encoder-prioritize-speed', 'false']
        with patch.object(sys, 'argv', argv), contextlib.redirect_stderr(io.StringIO()) as errors, \
                patch.object(run_phone_udp.subprocess, 'run') as run, \
                patch.object(run_phone_udp.subprocess, 'Popen') as spawn, \
                patch.object(run_phone_udp.socket, 'socket') as sockets:
            with self.assertRaises(SystemExit) as stopped:
                run_phone_udp.main()
            self.assertEqual(stopped.exception.code, 2)
            self.assertIn('independent experimental encoder', errors.getvalue())
            run.assert_not_called()
            spawn.assert_not_called()
            sockets.assert_not_called()

    def test_readback_preserves_nulls_bool_statuses_and_only_fixed_speed_states(self):
        fixed = {'event': 'ready', 'prioritize_speed_requested': None,
                 'prioritize_speed_supported_properties_status': 0,
                 'prioritize_speed_supported': True, 'prioritize_speed_set_status': None,
                 'prioritize_speed_read_status': -12900, 'prioritize_speed_readback': None,
                 'prioritize_speed_status': 'not_requested'}
        invalid = {'event': 'summary', 'prioritize_speed_requested': 'private value',
                   'prioritize_speed_supported_properties_status': True,
                   'prioritize_speed_supported': 1, 'prioritize_speed_set_status': 'failed',
                   'prioritize_speed_read_status': [], 'prioritize_speed_readback': 0,
                   'prioritize_speed_status': ['arbitrary status'], 'unrelated': 'private value'}
        fake_log = SimpleNamespace(read_text=lambda **kwargs: '\n'.join(map(json.dumps, [fixed, invalid])))
        self.assertEqual(run_phone_udp.encoder_readback(fake_log), [fixed, {'event': 'summary'}])

    def test_pool_readback_preserves_real_observations_and_rejects_type_confusion(self):
        observed = {'event': 'ready', 'pixel_pool_mode': 'session',
                    'pixel_pool_attributes_verified': True, 'pixel_pool_buffer_verified': False,
                    'pixel_pool_pixel_format_readback': 1111970369,
                    'pixel_pool_width_readback': 720, 'pixel_pool_height_readback': 1280,
                    'pixel_pool_probe_pixel_format': 1111970369,
                    'pixel_pool_probe_width': 720, 'pixel_pool_probe_height': 1280,
                    'pixel_pool_probe_bytes_per_row': 2880}
        fields = {key: value for key, value in observed.items() if key != 'event'}
        cases = [
            (observed, observed),
            ({**observed, 'pixel_pool_mode': 'manual'}, {**observed, 'pixel_pool_mode': 'manual'}),
            ({'event': 'summary', 'pixel_pool_mode': 'unknown'}, {'event': 'summary'}),
        ]
        for invalid in (None, '1', [], 1.0, True):
            # A bool is not numeric evidence; an int is not bool evidence.
            row = {'event': 'summary', **{key: invalid for key in fields}}
            expected = {'event': 'summary'}
            if type(invalid) is bool:
                expected.update(pixel_pool_attributes_verified=invalid,
                                pixel_pool_buffer_verified=invalid)
            cases.append((row, expected))
        cases += [
            ({'event': 'summary', **{key: -1 for key in fields}}, {'event': 'summary'}),
            ({'event': 'summary', **{key: 0 for key in fields}},
             {'event': 'summary', 'pixel_pool_pixel_format_readback': 0,
              'pixel_pool_probe_pixel_format': 0}),
        ]
        for row, expected in cases:
            with self.subTest(row=row):
                fake_log = SimpleNamespace(read_text=lambda **kwargs: json.dumps(row))
                self.assertEqual(run_phone_udp.encoder_readback(fake_log), [expected])


if __name__ == '__main__':
    unittest.main()
