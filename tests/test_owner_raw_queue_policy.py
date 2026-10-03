"""Explicit LAN raw-policy opt-in; all servers, sockets and children are fake."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import udp_lan_gateway as gateway
from udp_lan_worker import LanMediaWorker, owner_raw_queue_policy
from tests.test_udp_lan_worker import bare_worker, config


class OwnerRawPolicyChecks(unittest.TestCase):
    def test_default_fifo_has_no_trace_or_scope_dependency(self):
        for scope in ('lan', 'tailnet', 'nps_owner'):
            self.assertEqual(owner_raw_queue_policy('fifo', scope, None), 'fifo')

    def test_latest_requires_explicit_LAN_and_private_trace(self):
        self.assertEqual(owner_raw_queue_policy('latest', 'lan', Path('/fake/trace')), 'latest')
        for scope, directory in (('tailnet', Path('/fake')), ('nps_owner', Path('/fake')),
                                 ('lan', None), (None, Path('/fake'))):
            with self.subTest(scope=scope, directory=directory), self.assertRaises(ValueError):
                owner_raw_queue_policy('latest', scope, directory)

    def test_foreign_policy_types_and_values_are_refused(self):
        for value in (True, 1, None, 'auto', 'LATEST', [], {'policy': 'latest'}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                owner_raw_queue_policy(value, 'lan', Path('/fake'))

    def test_worker_rejects_nonLAN_latest_before_files_sockets_or_threads(self):
        for scope in ('nps_owner', 'tailnet'):
            with self.subTest(scope=scope), patch('udp_lan_worker.socket.socket') as sock, \
                    patch.object(Path, 'read_text') as read, \
                    patch.object(LanMediaWorker, '_background') as background:
                with self.assertRaisesRegex(ValueError, 'owner_latest_requires_LAN_trace'):
                    LanMediaWorker(config(network_scope=scope), '192.168.9.1', '192.168.9.2',
                        'en7', '/fake', '/fake/p', '/fake/e', Mock(), '/fake', Mock(),
                        raw_queue_policy='latest', capture_trace_dir=Path('/fake'))
                read.assert_not_called(); sock.assert_not_called(); background.assert_not_called()

    def test_session_request_and_environment_do_not_choose_policy(self):
        worker = bare_worker()
        worker.config['raw_queue_policy'] = 'latest'
        worker.config['owner_raw_queue_policy'] = 'latest'
        worker._background, worker.sender = Mock(), Mock()
        with patch.dict('os.environ', {'DIRECT_RAW_QUEUE_POLICY': 'latest'}), \
                patch('udp_lan_worker.HostHardwareSession') as hardware, \
                patch('udp_lan_worker.subprocess.Popen'):
            worker._start_media()
        self.assertEqual(hardware.call_args.kwargs['raw_queue_policy'], 'fifo')

    def test_local_latest_is_forwarded_without_changing_budget_or_packetizer(self):
        worker = bare_worker()
        worker.raw_queue_policy = 'latest'
        worker.capture_trace_path = Path('/fake/capture.jsonl')
        worker._background, worker.sender = Mock(), Mock()
        with patch('udp_lan_worker.HostHardwareSession') as hardware, \
                patch('udp_lan_worker.subprocess.Popen') as native:
            worker._start_media()
        self.assertEqual(hardware.call_args.kwargs['raw_queue_policy'], 'latest')
        self.assertEqual(hardware.call_args.kwargs['raw_submit_fps'], 60)
        self.assertEqual(hardware.call_args.kwargs['capture_trace'], worker.capture_trace_path)
        self.assertEqual(native.call_args.args[0], ['/fake/packetizer', '32000000', '500000', '0', '2048'])

    @staticmethod
    def argv(extra=()):
        return ['udp_lan_gateway.py', '--host', '192.168.9.128', '--interface', 'en7',
            '--runtime', '/fake/runtime', '--packetizer', '/fake/p',
            '--native-encoder', '/fake/e', '--evidence-dir', '/fake/evidence', *extra]

    def test_CLI_refusal_precedes_address_auth_SSL_or_listener(self):
        for extra in (('--owner-raw-queue-policy', 'latest'),
                ('--owner-raw-queue-policy', 'latest', '--network-scope', 'tailnet',
                 '--capture-trace-dir', '/fake/trace')):
            with self.subTest(extra=extra), patch('sys.argv', self.argv(extra)), \
                    patch('udp_lan_gateway.physical_lan_address') as address, \
                    patch('udp_lan_gateway.ssl.SSLContext') as ssl, \
                    patch('udp_lan_gateway.BoundedTlsServer') as server, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exited:
                    gateway.main()
                self.assertEqual(exited.exception.code, 2)
                address.assert_not_called(); ssl.assert_not_called(); server.assert_not_called()

    def test_actual_CLI_factory_and_numeric_listening_readback(self):
        for policy in ('fifo', 'latest'):
            extra = ('--owner-raw-queue-policy', 'latest', '--capture-trace-dir', '/fake/trace') \
                if policy == 'latest' else ()
            out = io.StringIO()
            registry = Mock()
            registry.close_and_wait.return_value = {'quiescence_confirmed': True, 'stop_failures': 0}
            captured = {}
            def handler(registry, factory, host, scope):
                captured['factory'] = factory
                return Mock()
            with self.subTest(policy=policy), patch('sys.argv', self.argv(extra)), \
                    patch.dict('os.environ', {'DIRECT_AUTH_FILE': '/fake/existing/auth'}), \
                    patch('udp_lan_gateway.physical_lan_address', return_value='192.168.9.128'), \
                    patch('udp_lan_gateway.UdpLanSessions', return_value=registry), \
                    patch('udp_lan_gateway.ssl.SSLContext'), \
                    patch('udp_lan_gateway.BoundedTlsServer'), \
                    patch('udp_lan_gateway.threading.Thread'), \
                    patch('udp_lan_gateway.signal.signal'), \
                    patch('udp_lan_gateway.handler_for', side_effect=handler), \
                    patch('udp_lan_gateway.LanMediaWorker') as worker, redirect_stdout(out):
                self.assertIsNone(gateway.main())
                captured['factory'](config(), '192.168.9.149')
            self.assertEqual(worker.call_args.kwargs['raw_queue_policy'], policy)
            ready = [json.loads(x) for x in out.getvalue().splitlines()
                     if json.loads(x)['event'] == 'listening'][0]
            self.assertEqual(ready['owner_raw_queue_policy_requested'], policy)
            self.assertEqual(ready['capture_trace_enabled'], policy == 'latest')


if __name__ == '__main__':
    unittest.main()
