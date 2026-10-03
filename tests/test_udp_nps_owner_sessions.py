"""Owned owner-NPS registry fixtures; no real auth, UDP sockets or device tests."""
import base64
import threading
import unittest

from udp_lan_sessions import SessionError, UdpLanSessions, parse_udp_settings
from udp_nps_profile import M1_OWNER, PUBLIC_HOST, owner_profile


class Clock:
    def __init__(self): self.now = 100.0
    def __call__(self): return self.now
    def advance(self, seconds): self.now += seconds


class Worker:
    def __init__(self): self.starts = 0; self.stops = 0
    def start(self): self.starts += 1
    def stop(self): self.stops += 1


def settings(node='m1', **changes):
    return {'network_scope': 'nps_owner', 'node': node, **changes}


def registry(profile_node='m1', **changes):
    profile = owner_profile(profile_node, allow_m5_owner_trial=profile_node == 'm5')
    options = dict(network_scope='nps_owner', node=profile_node,
        scope_guard=lambda: True, allow_m5_owner_trial=profile_node == 'm5',
        guest_serial=profile.guest_serial, guest_avd=profile.guest_avd)
    options.update(changes)
    return UdpLanSessions(PUBLIC_HOST, profile.public_media.port, **options)


class NpsSettingsChecks(unittest.TestCase):
    def test_explicit_node_and_scope_are_top_level_normalized(self):
        for node in ('m1', 'm5'):
            got = parse_udp_settings(settings(node))
            self.assertEqual((got['node'], got['network_scope']), (node, 'nps_owner'))
            self.assertEqual((got['seconds'], got['buffer_ms'], got['fps']), (120, 80, 60))

    def test_known_stream_settings_keep_existing_typed_validation(self):
        got = parse_udp_settings(settings('m5', max_size=960, video_bit_rate=4_000_000,
            bitrate_mode='ADAPTIVE_VBR', max_fps=120, buffer_ms=100, seconds=1,
            audio=False, audio_enabled=False, touch_enabled=True, surface_submit_lead_ms=0))
        self.assertEqual((got['max_size'], got['video_bit_rate'], got['fps']), (960, 4_000_000, 120))
        self.assertFalse(got['audio_enabled'])
        for changes in ({'seconds': 121}, {'seconds': True}, {'buffer_ms': 120},
                        {'max_fps': 90}, {'video_bit_rate': '4000000'}, {'audio': 1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                parse_udp_settings(settings(**changes))

    def test_unknown_identity_routing_guest_and_client_gate_fields_are_rejected(self):
        for field in ('peer_host', 'peer_port', 'bind_port', 'local_bind_host',
                      'local_bind_port', 'target', 'interface', 'guest_serial',
                      'guest_avd', 'proxy_protocol', 'allow_m5_owner_trial',
                      'owner_trial', 'arbitrary_extension'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                parse_udp_settings(settings(**{field: True}))
        for node in (None, True, 1, 'M1', 'm2', '100.65.0.2'):
            with self.subTest(node=node), self.assertRaises(ValueError):
                parse_udp_settings(settings(node))
        with self.assertRaises(ValueError):
            parse_udp_settings({'network_scope': 'nps_owner'})

    def test_legacy_unknown_fields_and_return_shapes_remain_unchanged(self):
        for scope in ('lan', 'tailnet'):
            got = parse_udp_settings({'network_scope': scope, 'old_extra': 'legacy', 'node': 'm5'})
            self.assertNotIn('node', got)
            self.assertNotIn('old_extra', got)
            self.assertEqual(got['network_scope'], scope)


class NpsConstructorChecks(unittest.TestCase):
    def test_m5_defaults_off_even_with_its_correct_guest_and_endpoint(self):
        with self.assertRaisesRegex(ValueError, 'friend_deployment_isolation_gate_required'):
            registry('m5', allow_m5_owner_trial=False)
        candidate = registry('m5', allow_m5_owner_trial=True)
        self.assertEqual(candidate._nps_profile.node, 'm5')
        for flag in (None, 0, 1, 'true', 1.0):
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                registry('m5', allow_m5_owner_trial=flag)

    def test_guard_and_explicit_trusted_guest_are_mandatory(self):
        for changes in ({'scope_guard': None}, {'scope_guard': True},
                        {'guest_serial': None}, {'guest_avd': None},
                        {'guest_serial': 'emulator-5554'}, {'guest_avd': 'phone17-root'},
                        {'guest_serial': '127.0.0.1:5555'}, {'node': 'm5'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                registry('m1', **changes)

    def test_advertised_public_node_host_and_media_port_are_exact(self):
        options = dict(network_scope='nps_owner', node='m1', scope_guard=lambda: True,
            guest_serial='emulator-5556', guest_avd='RemoteAndroid17Compare')
        for host, port in (('127.0.0.1', 15556), ('0.0.0.0', 15556),
                           ('192.168.9.128', 15556), ('146.56.249.174', 15556),
                           (PUBLIC_HOST, 15558), (PUBLIC_HOST, 45965),
                           (PUBLIC_HOST, 49556), (PUBLIC_HOST, '15556'),
                           (PUBLIC_HOST, True)):
            with self.subTest(host=host, port=port), self.assertRaises(ValueError):
                UdpLanSessions(host, port, **options)

    def test_nps_parameters_cannot_enable_a_legacy_scope(self):
        for changes in ({'node': 'm1'}, {'allow_m5_owner_trial': True},
                        {'guest_serial': 'emulator-5556'}, {'guest_avd': 'RemoteAndroid17Compare'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                UdpLanSessions('192.168.9.128', **changes)


class NpsLifecycleChecks(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.healthy = True
        self.worker = Worker()
        self.configs = []
        self.candidate = registry(clock=self.clock, scope_guard=lambda: self.healthy)

    def factory(self, config):
        self.configs.append(config)
        return self.worker

    def create(self, **changes):
        return self.candidate.create('wyw', settings(**changes), self.factory)

    def assertPrivateProjection(self, descriptor, node):
        self.assertEqual((descriptor['node'], descriptor['network_scope']), (node, 'nps_owner'))
        self.assertEqual((descriptor['peer_host'], descriptor['peer_port'], descriptor['bind_port']),
                         (PUBLIC_HOST, 15556 if node == 'm1' else 15558, 0))
        self.assertEqual(len(base64.b64decode(descriptor['key_b64'])), 32)
        for private in ('local_bind_host', 'local_bind_port', 'guest_serial', 'guest_avd',
                        'allow_m5_owner_trial', 'interface', 'account'):
            self.assertNotIn(private, descriptor)
        self.assertEqual((self.configs[-1]['local_bind_host'], self.configs[-1]['local_bind_port']),
                         ('127.0.0.1', 45965))

    def test_m1_descriptor_is_public_and_worker_config_is_private_guest_scoped(self):
        descriptor = self.create()
        self.assertPrivateProjection(descriptor, 'm1')
        self.assertEqual((self.configs[-1]['guest_serial'], self.configs[-1]['guest_avd']),
                         ('emulator-5556', 'RemoteAndroid17Compare'))
        self.assertEqual(self.worker.starts, 0)
        status = self.candidate.status('wyw', descriptor['session'])
        self.assertEqual(set(status), {'session', 'phase', 'expires_in_ms'})

    def test_trusted_m5_owner_trial_has_its_own_public_tuple_and_guest(self):
        self.candidate = registry('m5', clock=self.clock)
        descriptor = self.create(node='m5')
        self.assertPrivateProjection(descriptor, 'm5')
        self.assertEqual((self.configs[-1]['guest_serial'], self.configs[-1]['guest_avd']),
                         ('emulator-5554', 'phone17-root'))
        self.assertTrue(self.candidate.authenticated_ready(descriptor['session']))
        self.assertTrue(self.candidate.cancel('wyw', descriptor['session']))
        self.assertEqual((self.worker.starts, self.worker.stops), (1, 1))

    def test_public_or_factory_mutation_cannot_pollute_projection_or_registry(self):
        def changing_factory(config):
            self.factory(config)
            config.update(node='m5', peer_host='127.0.0.1', local_bind_port=1)
            return self.worker
        descriptor = self.candidate.create('wyw', settings(), changing_factory)
        self.assertEqual((descriptor['node'], descriptor['peer_host']), ('m1', PUBLIC_HOST))
        self.assertNotIn('local_bind_port', descriptor)
        descriptor['node'] = 'm5'
        self.assertEqual(self.candidate._active.config['node'], 'm1')
        self.assertNotIn('guest_serial', self.candidate._active.config)

    def test_owner_account_only_and_node_mismatch_fail_before_factory(self):
        for account in ('huoguo', 'WYw', 'another-owner'):
            with self.subTest(account=account), self.assertRaises(SessionError) as raised:
                self.candidate.create(account, settings(), self.factory)
            self.assertEqual((raised.exception.status, raised.exception.code),
                             (403, 'nps_owner_account_required'))
        with self.assertRaises(SessionError) as raised:
            self.create(node='m5')
        self.assertEqual((raised.exception.status, raised.exception.code),
                         (400, 'udp_node_profile_mismatch'))
        self.assertEqual(self.configs, [])

    def test_client_optin_or_backend_fields_cannot_cross_factory_boundary(self):
        for changes in ({'allow_m5_owner_trial': True}, {'local_bind_port': 45965},
                        {'guest_serial': 'emulator-5556'}, {'node': 'm5', 'owner_trial': True}):
            with self.subTest(changes=changes), self.assertRaises(SessionError) as raised:
                self.create(**changes)
            self.assertEqual(raised.exception.code, 'invalid_udp_settings')
        self.assertEqual(self.configs, [])

    def test_single_reservation_account_bound_cancel_and_new_session_identity(self):
        descriptor = self.create()
        sid = descriptor['session']
        self.assertFalse(self.candidate.cancel('huoguo', sid))
        with self.assertRaises(SessionError) as raised:
            self.create()
        self.assertEqual(raised.exception.code, 'udp_session_busy')
        self.assertTrue(self.candidate.authenticated_ready(sid))
        self.assertTrue(self.candidate.touch_allowed(sid))
        self.assertTrue(self.candidate.cancel('wyw', sid))
        self.assertFalse(self.candidate.touch_allowed(sid))
        self.assertTrue(self.candidate.cancel('wyw', sid))
        self.assertEqual((self.worker.starts, self.worker.stops), (1, 1))
        next_descriptor = self.create()
        self.assertNotEqual(next_descriptor['session'], sid)
        self.assertNotEqual(next_descriptor['key_b64'], descriptor['key_b64'])
        self.assertFalse(self.candidate.authenticated_ready(sid))

    def test_ready_alive_and_hard_deadline_remain_bounded(self):
        descriptor = self.create(seconds=4)
        sid = descriptor['session']
        self.assertFalse(self.candidate.authenticated_alive(sid))
        self.assertTrue(self.candidate.authenticated_ready(sid))
        self.clock.advance(2)
        self.assertTrue(self.candidate.authenticated_alive(sid))
        self.clock.advance(2)
        self.assertFalse(self.candidate.authenticated_alive(sid))
        self.assertFalse(self.candidate.touch_allowed(sid))
        self.assertEqual(self.worker.stops, 1)

    def test_ready_timeout_and_repeated_ready_cannot_extend_silence(self):
        descriptor = self.create()
        self.clock.advance(10)
        self.assertFalse(self.candidate.authenticated_ready(descriptor['session']))
        self.assertEqual((self.worker.starts, self.worker.stops), (0, 1))
        descriptor = self.create()
        self.assertTrue(self.candidate.authenticated_ready(descriptor['session']))
        self.clock.advance(2)
        self.assertTrue(self.candidate.authenticated_ready(descriptor['session']))
        self.clock.advance(1)
        self.assertFalse(self.candidate.touch_allowed(descriptor['session']))
        self.assertEqual(self.worker.stops, 2)

    def test_scope_guard_failure_blocks_access_and_gateway_close_revokes(self):
        self.healthy = False
        with self.assertRaises(SessionError) as raised:
            self.create()
        self.assertEqual(raised.exception.code, 'udp_network_scope_unavailable')
        self.assertEqual(self.configs, [])
        self.healthy = True
        descriptor = self.create()
        self.assertTrue(self.candidate.authenticated_ready(descriptor['session']))
        self.healthy = False
        self.assertFalse(self.candidate.authenticated_alive(descriptor['session']))
        self.assertFalse(self.candidate.touch_allowed(descriptor['session']))
        # Existing behavior: the gateway's sticky verifier closes the registry;
        # the in-memory guard itself performs no cleanup I/O on hot paths.
        self.candidate.close()
        self.assertEqual(self.worker.stops, 1)

    def test_cancel_while_factory_pending_returns_no_descriptor_and_keeps_barrier(self):
        entered, release = threading.Event(), threading.Event()
        outcomes = []
        def factory(config):
            self.configs.append(config)
            entered.set()
            release.wait(2)
            return self.worker
        def create():
            try: outcomes.append(self.candidate.create('wyw', settings(), factory))
            except SessionError as error: outcomes.append(error.code)
        thread = threading.Thread(target=create)
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            sid = self.configs[-1]['session']
            self.assertTrue(self.candidate.cancel('wyw', sid))
            with self.assertRaises(SessionError) as raised:
                self.create()
            self.assertEqual(raised.exception.code, 'udp_session_busy')
        finally:
            release.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outcomes, ['udp_session_revoked'])
        self.assertEqual((self.worker.starts, self.worker.stops), (0, 1))
        result = self.candidate.close_and_wait(1)
        self.assertTrue(result['quiescence_confirmed'])
        self.assertEqual(result['stop_failures'], 0)

    def test_factory_failure_and_close_cleanup_do_not_publish_backend(self):
        def fail(config):
            raise RuntimeError('owned synthetic failure')
        with self.assertRaises(SessionError) as raised:
            self.candidate.create('wyw', settings(), fail)
        self.assertEqual(raised.exception.code, 'udp_worker_unavailable')
        descriptor = self.create()
        self.assertPrivateProjection(descriptor, 'm1')
        result = self.candidate.close_and_wait(1)
        self.assertTrue(result['quiescence_confirmed'])
        self.assertEqual(self.worker.stops, 1)
        with self.assertRaises(SessionError) as raised:
            self.create()
        self.assertEqual(raised.exception.code, 'registry_closed')


if __name__ == '__main__':
    unittest.main()
