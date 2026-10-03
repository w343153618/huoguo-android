"""Trusted NPS duration policies and actual reaper logic, entirely offline."""
from contextlib import redirect_stdout
import io
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from udp_lan_sessions import (SessionError, UdpLanSessions, parse_udp_settings,
                             trusted_max_session_seconds)
from udp_nps_gateway import main, parse_arguments
from udp_network_scope import ScopeUnavailable
from udp_nps_profile import owner_profile


class Clock:
    def __init__(self): self.now = 100.0
    def __call__(self): return self.now


class Worker:
    def __init__(self): self.starts = self.stops = 0
    def start(self): self.starts += 1
    def stop(self): self.stops += 1


def candidate(cap=120, node='m1', **changes):
    profile = owner_profile(node, allow_m5_owner_trial=node == 'm5')
    options = dict(network_scope='nps_owner', node=node,
        scope_guard=lambda: True, allow_m5_owner_trial=node == 'm5',
        guest_serial=profile.guest_serial, guest_avd=profile.guest_avd,
        max_session_seconds=cap, **changes)
    return UdpLanSessions(profile.public_media.host, profile.public_media.port, **options)


def settings(seconds=120, node='m1', **changes):
    return {'network_scope': 'nps_owner', 'node': node, 'seconds': seconds, **changes}


def arguments(*extra):
    return parse_arguments(['--node', 'm1', '--runtime', '/fake/runtime',
        '--packetizer', '/fake/packetizer', '--native-encoder', '/fake/encoder',
        '--evidence-dir', '/fake/evidence', *extra])


class LongSessionPolicyChecks(unittest.TestCase):
    def test_default_duration_and_private_cap_remain_120(self):
        for scope in ('lan', 'tailnet', 'nps_owner'):
            request = {'network_scope': scope}
            if scope == 'nps_owner': request['node'] = 'm1'
            self.assertEqual(parse_udp_settings(request)['seconds'], 120)
            with self.assertRaises(ValueError):
                parse_udp_settings({**request, 'seconds': 121})
        self.assertEqual(arguments().max_session_seconds, 120)
        self.assertEqual(arguments().max_runtime, 600)

    def test_closed_typed_cap_requires_explicit_nps_owner_constructor(self):
        for value in (None, True, False, 120.0, 3600.0, '3600', 0, 121, 28800, 3601):
            with self.subTest(value=value), self.assertRaises(ValueError): candidate(value)
        for scope, host in (('lan', '192.168.9.128'), ('tailnet', '100.65.0.2')):
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                UdpLanSessions(host, network_scope=scope, scope_guard=lambda: True,
                               max_session_seconds=3600)
            with self.assertRaises(ValueError):
                parse_udp_settings({'network_scope': scope}, max_session_seconds=3600)
        for scope in (None, True, 'public', 'nps'):
            with self.assertRaises(ValueError): trusted_max_session_seconds(scope, 120)

    def test_client_cannot_select_or_override_server_cap(self):
        for cap in (120, 3600):
            for name in ('max_session_seconds', 'max-session-seconds', 'session_cap', 'max_runtime'):
                factory = Mock()
                with self.subTest(cap=cap, name=name), self.assertRaises(SessionError) as raised:
                    candidate(cap).create('wyw', settings(3600, **{name: 3600}), factory)
                self.assertEqual(raised.exception.code, 'invalid_udp_settings')
                factory.assert_not_called()
        for scope in ('lan', 'tailnet'):
            with self.assertRaises(ValueError):
                parse_udp_settings({'network_scope': scope, 'seconds': 121,
                                    'max_session_seconds': 3600})

    def test_opt_in_accepts_one_second_to_one_hour_but_default_request_stays_120(self):
        self.assertEqual(parse_udp_settings({'network_scope': 'nps_owner', 'node': 'm1'},
                                          max_session_seconds=3600)['seconds'], 120)
        for seconds in (1, 120, 121, 1800, 3600):
            for node in ('m1', 'm5'):
                with self.subTest(seconds=seconds, node=node):
                    registry, worker = candidate(3600, node), Worker()
                    configs = []
                    def factory(config): configs.append(config); return worker
                    descriptor = registry.create('wyw', settings(seconds, node), factory)
                    self.assertEqual((descriptor['seconds'], configs[0]['seconds']), (seconds, seconds))
                    self.assertNotIn('max_session_seconds', descriptor)
                    self.assertNotIn('max_session_seconds', configs[0])
                    registry.close()
        for seconds in (None, True, False, 1.0, 3600.0, '3600', 0, 3601):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                parse_udp_settings(settings(seconds), max_session_seconds=3600)

    def test_one_hour_cap_remains_a_hard_deadline_despite_valid_keepalives(self):
        clock, worker = Clock(), Worker()
        registry = candidate(3600, clock=clock)
        sid = registry.create('wyw', settings(3600), lambda config: worker)['session']
        self.assertTrue(registry.authenticated_ready(sid))
        self.assertEqual(registry._active.hard_deadline, 3700.0)
        for _ in range(1799):
            clock.now += 2
            self.assertTrue(registry.authenticated_alive(sid))
        self.assertTrue(registry.touch_allowed(sid))
        clock.now += 2
        self.assertFalse(registry.authenticated_alive(sid))
        self.assertFalse(registry.touch_allowed(sid))
        self.assertEqual((worker.starts, worker.stops), (1, 1))
        self.assertTrue(registry.close_and_wait()['quiescence_confirmed'])

    def test_long_cap_keeps_ready_alive_account_and_cancel_guards(self):
        for phase in ('waiting', 'active'):
            clock, worker = Clock(), Worker()
            registry = candidate(3600, clock=clock)
            sid = registry.create('wyw', settings(3600), lambda config: worker)['session']
            if phase == 'active': registry.authenticated_ready(sid)
            self.assertFalse(registry.cancel('huoguo', sid))
            clock.now += registry.READY_SECONDS if phase == 'waiting' else registry.ALIVE_SECONDS
            registry.reap()
            self.assertIsNone(registry.status('wyw', sid))
            self.assertEqual(worker.stops, 1)
        registry, worker = candidate(3600), Worker()
        sid = registry.create('wyw', settings(3600), lambda config: worker)['session']
        registry.authenticated_ready(sid)
        self.assertTrue(registry.cancel('wyw', sid))
        self.assertFalse(registry.authenticated_alive(sid))
        self.assertEqual(worker.stops, 1)

    def test_cli_only_accepts_two_session_caps_and_zero_or_original_runtime_range(self):
        for cap in (120, 3600):
            for lifetime in (0, 30, 600, 3600):
                got = arguments('--max-session-seconds', str(cap), '--max-runtime', str(lifetime))
                self.assertEqual((got.max_session_seconds, got.max_runtime), (cap, lifetime))
        for flag, values in (('--max-session-seconds', ('0', '121', '28800', '3601', '3600.0')),
                             ('--max-runtime', ('-1', '1', '29', '3601', '0.0'))):
            for value in values:
                with self.subTest(flag=flag, value=value), patch('sys.stderr', io.StringIO()):
                    with self.assertRaises(SystemExit): arguments(flag, value)


class LongGatewayReaperChecks(unittest.TestCase):
    def run_gateway(self, lifetime, *, scope_failure=False):
        args = arguments('--max-runtime', str(lifetime), '--max-session-seconds', '3600')
        # The actual nested reaper runs synchronously with fake time and handles;
        # no thread, listener, credential read or guest worker is created.
        stop, server = Mock(), Mock()
        stop.wait.side_effect = [False, False, False, True]
        scope = SimpleNamespace(name='nps_owner', healthy=lambda: True, verify=Mock())
        if scope_failure:
            scope.verify.side_effect = ScopeUnavailable('public inert fixture scope failure')
        scope.permits_peer = lambda peer: True
        captured, registries, times = [], [], [0.0]
        def monotonic(): times[0] += 10000; return times[0]
        def create_thread(**kwargs): captured.append(kwargs['target']); return Mock()
        def serve(**kwargs): captured[0]()
        def create_registry(*args, **kwargs):
            registry = UdpLanSessions(*args, **kwargs)
            registry.reap = Mock(wraps=registry.reap)
            registries.append(registry)
            return registry
        server.serve_forever.side_effect = serve
        output = io.StringIO()
        environment = {'DIRECT_AUTH_FILE': '/fake/existing-auth',
            'DIRECT_SERIAL': args.profile.guest_serial, 'DIRECT_AVD': args.profile.guest_avd,
            'DIRECT_EXTERNAL_VM': '1'}
        with patch('udp_nps_gateway.parse_arguments', return_value=args), \
                patch.dict(os.environ, environment, clear=True), \
                patch('udp_nps_gateway.trial_busy', return_value=False), \
                patch('udp_nps_gateway.OwnerNpsScope', return_value=scope), \
                patch('udp_nps_gateway.UdpLanSessions', side_effect=create_registry) as registry_factory, \
                patch('udp_nps_gateway.ssl.SSLContext'), \
                patch('udp_nps_gateway.LoopbackTlsServer', return_value=server), \
                patch('udp_nps_gateway.LanMediaWorker') as media, \
                patch('udp_nps_gateway.threading', SimpleNamespace(Event=lambda: stop, Thread=create_thread)), \
                patch('udp_nps_gateway.time', SimpleNamespace(monotonic=monotonic)), \
                patch('udp_nps_gateway.signal.signal'), redirect_stdout(output):
            main()
        self.assertEqual(registry_factory.call_args.kwargs['max_session_seconds'], 3600)
        media.assert_not_called()
        log = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual((log[0]['max_session_seconds'], log[0]['max_runtime_seconds']),
                         (3600, lifetime))
        self.assertTrue(log[-1]['quiescence_confirmed'])
        return server, scope, registries[0]

    def test_zero_runtime_never_expires_process_but_still_checks_scope_and_reaps(self):
        server, scope, registry = self.run_gateway(0)
        server.shutdown.assert_not_called()
        self.assertEqual(scope.verify.call_count, 3)
        self.assertEqual(registry.reap.call_count, 3)
        self.assertTrue(registry._closed)  # Ordinary finally cleanup still runs.

    def test_original_positive_runtime_still_triggers_proactive_shutdown(self):
        for lifetime in (30, 3600):
            with self.subTest(lifetime=lifetime):
                server, scope, registry = self.run_gateway(lifetime)
                server.shutdown.assert_called_once_with()
                self.assertEqual(scope.verify.call_count, 1)
                self.assertEqual(registry.reap.call_count, 1)

    def test_zero_runtime_still_fails_closed_when_scope_verification_fails(self):
        server, scope, registry = self.run_gateway(0, scope_failure=True)
        server.shutdown.assert_called_once_with()
        self.assertEqual(scope.verify.call_count, 1)
        self.assertEqual(registry.reap.call_count, 0)
        self.assertTrue(registry._closed)


if __name__ == '__main__': unittest.main()
