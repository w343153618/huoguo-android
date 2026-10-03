"""Offline gateway wiring; no real TLS, listeners, credentials or media."""
import contextlib
import io
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import udp_lan_gateway as lan
import udp_nps_gateway as nps
from udp_lan_sessions import parse_udp_settings
from udp_nps_profile import ProfileError, owner_profile


class EntryChecks(unittest.TestCase):
    def nps_args(self, trace=None):
        cli = ['--node', 'm1', '--runtime', '/fake/runtime',
               '--packetizer', '/fake/packetizer', '--native-encoder', '/fake/encoder',
               '--evidence-dir', '/fake/evidence']
        if trace is not None:
            cli += ['--capture-trace-dir', trace]
        return nps.parse_arguments(cli)

    def test_nps_cli_default_off_and_trusted_path_are_distinct(self):
        self.assertIsNone(self.nps_args().capture_trace_dir)
        self.assertEqual(self.nps_args('/fake/private-traces').capture_trace_dir,
                         Path('/fake/private-traces'))

    def test_trace_cannot_opt_into_unlimited_gateway_lifetime(self):
        common = ['--node', 'm1', '--runtime', '/fake/runtime',
                  '--packetizer', '/fake/packetizer', '--native-encoder', '/fake/encoder',
                  '--evidence-dir', '/fake/evidence', '--max-runtime', '0']
        self.assertIsNone(nps.parse_arguments(common).capture_trace_dir)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            nps.parse_arguments([*common, '--capture-trace-dir', '/fake/private-traces'])

    def test_http_settings_cannot_enable_or_replace_trace_parent(self):
        for name in ('capture_trace_dir', 'raw_writer_diagnostics', 'capture_trace'):
            with self.subTest(name=name), self.assertRaises(ProfileError):
                nps.validate_settings({'node': 'm1', 'network_scope': 'nps_owner',
                    name: '/fake/requested'}, owner_profile('m1'))
            parsed = parse_udp_settings({name: '/fake/requested'})
            self.assertNotIn(name, parsed)

    def test_nps_factory_only_passes_trusted_server_path_and_enabled_bit(self):
        for trace in (None, '/fake/private-traces'):
            args = self.nps_args(trace)
            scope = SimpleNamespace(name='nps_owner', healthy=lambda: True)
            def handler(registry, factory, *a, **kw):
                factory({'capture_trace_dir': '/fake/body-value'}, '127.0.0.1')
                return Mock()
            output = io.StringIO()
            with self.subTest(trace=trace), contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(nps, 'parse_arguments', return_value=args))
                stack.enter_context(patch.dict(os.environ, {
                    'DIRECT_AUTH_FILE': '/fake/existing', 'DIRECT_SERIAL': 'emulator-5556',
                    'DIRECT_AVD': 'RemoteAndroid17Compare', 'DIRECT_EXTERNAL_VM': '1'}, clear=True))
                stack.enter_context(patch.object(nps, 'trial_busy', return_value=False))
                stack.enter_context(patch.object(nps, 'OwnerNpsScope', return_value=scope))
                stack.enter_context(patch.object(nps, 'UdpLanSessions'))
                stack.enter_context(patch.object(nps.ssl, 'SSLContext'))
                stack.enter_context(patch.object(nps, 'LoopbackTlsServer'))
                stack.enter_context(patch.object(nps, 'handler_for', side_effect=handler))
                worker = stack.enter_context(patch.object(nps, 'LanMediaWorker'))
                stack.enter_context(patch.object(nps.threading, 'Thread'))
                stack.enter_context(patch.object(nps.signal, 'signal'))
                stack.enter_context(patch.object(nps, 'shutdown_registry', return_value={'quiescence_confirmed': True}))
                stack.enter_context(contextlib.redirect_stdout(output))
                nps.main()
                self.assertEqual(worker.call_args.kwargs['capture_trace_dir'], args.capture_trace_dir)
                listening = json.loads(output.getvalue().splitlines()[0])
                self.assertEqual(listening['capture_trace_enabled'], trace is not None)
                self.assertNotIn('capture_trace_dir', listening)

    def test_lan_factory_preserves_default_off_and_server_optin(self):
        for trace in (None, '/fake/private-traces'):
            cli = ['udp_lan_gateway.py', '--host', '192.168.9.128', '--interface', 'en7',
                   '--runtime', '/fake/runtime', '--packetizer', '/fake/packetizer',
                   '--native-encoder', '/fake/encoder', '--evidence-dir', '/fake/evidence']
            if trace is not None:
                cli += ['--capture-trace-dir', trace]
            scope = SimpleNamespace(name='lan', healthy=lambda: True, ping_scope='fixture')
            def handler(registry, factory, *a, **kw):
                factory({'capture_trace_dir': '/fake/body-value'}, '192.168.9.149')
                return Mock()
            output = io.StringIO()
            with self.subTest(trace=trace), contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(sys, 'argv', cli))
                stack.enter_context(patch.dict(os.environ, {'DIRECT_AUTH_FILE': '/fake/existing'}, clear=True))
                stack.enter_context(patch.object(lan, 'physical_lan_address', return_value='192.168.9.128'))
                stack.enter_context(patch.object(lan, 'LanScope', return_value=scope))
                stack.enter_context(patch.object(lan, 'UdpLanSessions'))
                stack.enter_context(patch.object(lan.ssl, 'SSLContext'))
                stack.enter_context(patch.object(lan, 'BoundedTlsServer'))
                stack.enter_context(patch.object(lan, 'handler_for', side_effect=handler))
                worker = stack.enter_context(patch.object(lan, 'LanMediaWorker'))
                stack.enter_context(patch.object(lan.threading, 'Thread'))
                stack.enter_context(patch.object(lan.signal, 'signal'))
                stack.enter_context(patch.object(lan, 'shutdown_registry', return_value={'quiescence_confirmed': True}))
                stack.enter_context(contextlib.redirect_stdout(output))
                lan.main()
                expected = None if trace is None else Path(trace)
                self.assertEqual(worker.call_args.kwargs['capture_trace_dir'], expected)
                self.assertEqual(json.loads(output.getvalue().splitlines()[0])['capture_trace_enabled'], trace is not None)


if __name__ == '__main__':
    unittest.main()
