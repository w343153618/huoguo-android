"""Explicit raw-input budget experiments, without sockets, devices or processes."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import udp_lan_gateway as gateway
from hardware_stream import raw_submit_budget_configuration
from udp_lan_worker import LanMediaWorker, owner_raw_submit_fps
from tests import test_owner_raw_queue_policy as raw_policy_tests
from tests.test_udp_lan_worker import bare_worker, config


class OwnerRawSubmitBudgetChecks(unittest.TestCase):
    def test_default_keeps_all_existing_scopes_independent_of_trace(self):
        for scope in ('lan', 'tailnet', 'nps_owner'):
            self.assertIsNone(owner_raw_submit_fps(None, scope, None))

    def test_explicit_values_require_LAN_trace(self):
        for value in (30, 60):
            self.assertEqual(owner_raw_submit_fps(value, 'lan', Path('/fake')), value)
            for scope, trace in (('tailnet', Path('/fake')), ('nps_owner', Path('/fake')),
                                 ('lan', None)):
                with self.subTest(scope=scope, trace=trace), self.assertRaises(ValueError):
                    owner_raw_submit_fps(value, scope, trace)

    def test_boolean_float_string_and_unbounded_values_refused(self):
        for value in (True, False, 60.0, '60', 0, 31, 120, [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                owner_raw_submit_fps(value, 'lan', Path('/fake'))

    def test_worker_refusal_precedes_files_sockets_and_background(self):
        with patch('udp_lan_worker.socket.socket') as sock, \
                patch.object(Path, 'read_text') as read, \
                patch.object(LanMediaWorker, '_background') as background:
            with self.assertRaisesRegex(ValueError, 'owner_raw_submit_requires_LAN_trace'):
                LanMediaWorker(config(network_scope='tailnet'), '192.168.9.1', '192.168.9.2',
                    'en7', '/fake', '/fake/p', '/fake/e', Mock(), '/fake', Mock(),
                    raw_submit_fps=60, capture_trace_dir=Path('/fake'))
        sock.assert_not_called(); read.assert_not_called(); background.assert_not_called()

    def worker(self):
        worker = bare_worker()
        worker.config['fps'] = 30
        worker._background, worker.sender = Mock(), Mock()
        return worker

    def test_default_cannot_be_selected_by_session_fields_or_environment(self):
        worker = self.worker()
        worker.config.update(owner_raw_submit_fps=60, raw_submit_fps=60)
        with patch.dict('os.environ', {'DIRECT_RAW_SUBMIT_FPS': '60'}), \
                patch('udp_lan_worker.HostHardwareSession') as hardware, \
                patch('udp_lan_worker.subprocess.Popen'):
            worker._start_media()
        self.assertEqual(hardware.call_args.kwargs['raw_submit_fps'], 30)
        self.assertEqual(hardware.call_args.args[5], 30)

    def test_raw60_native30_is_a_single_budget_override_and_actual_reply(self):
        worker = self.worker()
        worker.owner_raw_submit_fps_requested = 60
        worker.raw_queue_policy = 'fifo'
        worker.capture_trace_path = Path('/fake/capture')
        reply = raw_submit_budget_configuration(30, 60)
        with patch('udp_lan_worker.HostHardwareSession') as hardware, \
                patch('udp_lan_worker.subprocess.Popen') as packetizer:
            hardware.return_value.raw_submit_budget_readback = dict(reply, foreign='discard')
            worker._start_media()
        self.assertEqual(hardware.call_args.args[5], 30)
        self.assertEqual(hardware.call_args.kwargs['raw_submit_fps'], 60)
        self.assertEqual(hardware.call_args.kwargs['raw_queue_policy'], 'fifo')
        self.assertEqual(worker.config['fps'], 30)
        self.assertEqual(worker.owner_raw_submit_budget_readback, reply)
        self.assertEqual(packetizer.call_args.args[0], ['/fake/packetizer', '32000000', '500000', '0', '2048'])

    def test_uncertain_reply_does_not_become_requested_readback_and_remains_owned(self):
        worker = self.worker()
        worker.owner_raw_submit_fps_requested = 60
        with patch('udp_lan_worker.HostHardwareSession') as hardware, \
                patch('udp_lan_worker.subprocess.Popen') as packetizer:
            hardware.return_value.raw_submit_budget_readback = raw_submit_budget_configuration(30, 30)
            with self.assertRaisesRegex(ValueError, 'readback missing or mismatched'):
                worker._start_media()
        self.assertIs(worker.hardware, hardware.return_value)
        self.assertIsNone(getattr(worker, 'owner_raw_submit_budget_readback', None))
        packetizer.assert_not_called()

    def test_CLI_gate_refuses_before_auth_address_SSL_listener(self):
        for extra in (('--owner-raw-submit-fps', '60'),
                      ('--owner-raw-submit-fps', '60', '--capture-trace-dir', '/fake',
                       '--network-scope', 'tailnet')):
            with self.subTest(extra=extra), patch('sys.argv', raw_policy_tests.OwnerRawPolicyChecks.argv(extra)), \
                    patch('udp_lan_gateway.physical_lan_address') as address, \
                    patch('udp_lan_gateway.ssl.SSLContext') as ssl, \
                    patch('udp_lan_gateway.BoundedTlsServer') as server, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exited:
                    gateway.main()
                self.assertEqual(exited.exception.code, 2)
                address.assert_not_called(); ssl.assert_not_called(); server.assert_not_called()

    def test_actual_CLI_factory_and_listening_readback(self):
        for value in (None, 60):
            extra = () if value is None else ('--owner-raw-submit-fps', '60', '--capture-trace-dir', '/fake')
            out, captured, registry = io.StringIO(), {}, Mock()
            registry.close_and_wait.return_value = {'quiescence_confirmed': True, 'stop_failures': 0}
            def handler(registry, factory, host, scope):
                captured['factory'] = factory
                return Mock()
            with self.subTest(value=value), patch('sys.argv', raw_policy_tests.OwnerRawPolicyChecks.argv(extra)), \
                    patch.dict('os.environ', {'DIRECT_AUTH_FILE': '/fake/auth'}), \
                    patch('udp_lan_gateway.physical_lan_address', return_value='192.168.9.128'), \
                    patch('udp_lan_gateway.UdpLanSessions', return_value=registry), \
                    patch('udp_lan_gateway.ssl.SSLContext'), \
                    patch('udp_lan_gateway.BoundedTlsServer'), patch('udp_lan_gateway.threading.Thread'), \
                    patch('udp_lan_gateway.signal.signal'), \
                    patch('udp_lan_gateway.handler_for', side_effect=handler), \
                    patch('udp_lan_gateway.LanMediaWorker') as worker, redirect_stdout(out):
                gateway.main()
                captured['factory'](config(fps=30), '192.168.9.149')
            self.assertEqual(worker.call_args.kwargs['raw_submit_fps'], value)
            self.assertEqual(worker.call_args.kwargs['raw_queue_policy'], 'fifo')
            listening = [json.loads(line) for line in out.getvalue().splitlines()
                         if json.loads(line)['event'] == 'listening'][0]
            self.assertEqual(listening['owner_raw_submit_fps_requested'], value)


if __name__ == '__main__':
    unittest.main()
