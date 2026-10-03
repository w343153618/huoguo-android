"""Owned pure endpoint/profile checks, not auth, networking or App acceptance."""
from dataclasses import FrozenInstanceError, replace
import unittest

from udp_nps_profile import (Endpoint, M1_OWNER, M5_PLANNED, NPS_OWNER_SCOPE,
                             ProfileError, owner_profile, planned_profile,
                             validate_loopback_backend, validate_owner_request,
                             validate_proxy_peer, validate_proxy_protocol,
                             validate_public_endpoints, validate_scope,
                             validate_session_seconds)


class EndpointChecks(unittest.TestCase):
    def test_ports_are_typed_and_bounded_without_coercion(self):
        for port in (1, 15556, 45561, 45965, 49556, 65535):
            self.assertEqual(Endpoint('127.0.0.1', port, 'udp').port, port)
        for port in (True, False, 0, -1, 65536, 49556.0, '49556', None):
            with self.subTest(port=port), self.assertRaises(ProfileError):
                Endpoint('127.0.0.1', port, 'udp')

    def test_ipv4_host_is_literal_and_canonical(self):
        for host in ('localhost', '127.1', '127.000.0.1', ' 127.0.0.1',
                     '127.0.0.1 ', '::1', '146.56.249.175:15556',
                     'https://146.56.249.175', None, 2130706433, b'127.0.0.1'):
            with self.subTest(host=host), self.assertRaises(ProfileError):
                Endpoint(host, 45965, 'udp')

    def test_transport_is_explicit_not_coerced_or_guessed(self):
        for transport in ('UDP', 'quic', 'tls', 'kcp', '', None, True):
            with self.subTest(transport=transport), self.assertRaises(ProfileError):
                Endpoint('127.0.0.1', 45965, transport)


class ProfileChecks(unittest.TestCase):
    def test_m1_control_media_and_local_bind_are_independent_fixed_tuples(self):
        profile = owner_profile('m1')
        self.assertIs(profile, M1_OWNER)
        self.assertEqual(profile.public_control, Endpoint('146.56.249.175', 49556, 'tcp'))
        self.assertEqual(profile.public_media, Endpoint('146.56.249.175', 15556, 'udp'))
        self.assertEqual(profile.local_https, Endpoint('127.0.0.1', 45561, 'tcp'))
        self.assertEqual(profile.local_udp, Endpoint('127.0.0.1', 45965, 'udp'))
        self.assertEqual((profile.interface, profile.scope, profile.proxy_protocol,
                          profile.max_session_seconds), ('lo0', 'nps_owner', 0, 120))
        self.assertEqual(profile.deployment, 'owner_nonisolated')

    def test_m5_is_planning_only_with_distinct_public_profile(self):
        profile = planned_profile('m5')
        self.assertIs(profile, M5_PLANNED)
        self.assertEqual(profile.public_control, Endpoint('146.56.249.175', 49558, 'tcp'))
        self.assertEqual(profile.public_media, Endpoint('146.56.249.175', 15558, 'udp'))
        self.assertEqual(profile.deployment, 'friend_deployment_gated')
        with self.assertRaisesRegex(ProfileError, 'friend_deployment_isolation_gate_required'):
            owner_profile('m5')

    def test_unknown_node_and_aliases_do_not_select_another_host(self):
        for node in (None, True, 1, b'm1', '', 'M1', 'M5', 'm1 ', 'm2',
                     'Macbook-m1-64', '100.65.0.2', ['m1']):
            with self.subTest(node=node), self.assertRaises(ProfileError):
                planned_profile(node)

    def test_profiles_and_nested_endpoints_cannot_be_mutated(self):
        with self.assertRaises(FrozenInstanceError):
            M1_OWNER.local_udp = Endpoint('0.0.0.0', 45965, 'udp')
        with self.assertRaises(FrozenInstanceError):
            M1_OWNER.public_media.port = 15558

    def test_profile_metadata_does_not_accept_bool_float_or_unknown_modes(self):
        for field, value in (('scope', 'lan'), ('scope', None),
                             ('interface', 'en7'), ('interface', 'lo1'),
                             ('proxy_protocol', False), ('proxy_protocol', 0.0),
                             ('proxy_protocol', 1), ('deployment', True),
                             ('deployment', 'public'), ('max_session_seconds', 120.0),
                             ('max_session_seconds', True), ('max_session_seconds', 121),
                             ('public_control', ('146.56.249.175', 49556))):
            with self.subTest(field=field, value=value), self.assertRaises(ProfileError):
                replace(M1_OWNER, **{field: value})

    def test_altered_bind_or_node_profile_is_not_admitted(self):
        variants = (
            replace(M1_OWNER, local_udp=Endpoint('0.0.0.0', 45965, 'udp')),
            replace(M1_OWNER, public_media=M5_PLANNED.public_media),
            replace(M1_OWNER, public_control=Endpoint('192.168.9.128', 49556, 'tcp')),
            replace(M5_PLANNED, deployment='owner_nonisolated'),
        )
        for profile in variants:
            with self.subTest(node=profile.node), self.assertRaises(ProfileError):
                validate_proxy_peer(profile, ('127.0.0.1', 50001))


class AdmissionChecks(unittest.TestCase):
    def test_closed_owner_identity_selects_only_existing_fixed_m1(self):
        identity = {'node': 'm1', 'network_scope': NPS_OWNER_SCOPE}
        original = dict(identity)
        self.assertIs(validate_owner_request(identity), M1_OWNER)
        self.assertEqual(identity, original)

    def test_client_cannot_supply_a_bind_target_or_public_endpoint(self):
        for name in ('host', 'port', 'target', 'bind', 'bind_port', 'interface',
                     'peer_host', 'peer_port', 'public_control', 'public_media',
                     'local_https', 'local_udp', 'proxy_protocol', 'gate',
                     'seconds', 'video_bit_rate'):
            identity = {'node': 'm1', 'network_scope': NPS_OWNER_SCOPE, name: 'ignored?'}
            with self.subTest(name=name), self.assertRaisesRegex(ProfileError, 'closed_nps_identity_required'):
                validate_owner_request(identity)

    def test_missing_wrong_type_or_unknown_scope_is_rejected(self):
        for identity in (None, [], {}, {'node': 'm1'}, {'network_scope': 'nps_owner'},
                         {'node': 'm1', 'network_scope': 'lan'},
                         {'node': 'm1', 'network_scope': 'tailnet'},
                         {'node': 'm1', 'network_scope': 'nps_public'},
                         {'node': 'm1', 'network_scope': None},
                         {'node': 'm5', 'network_scope': 'nps_owner'}):
            with self.subTest(identity=identity), self.assertRaises(ProfileError):
                validate_owner_request(identity)

    def test_scope_and_proxy_protocol_require_exact_typed_values(self):
        validate_scope('nps_owner')
        validate_proxy_protocol(0)
        for scope in ('', 'public', 'NPS_OWNER', None, True, b'nps_owner'):
            with self.subTest(scope=scope), self.assertRaises(ProfileError):
                validate_scope(scope)
        for value in (1, 2, -1, True, False, '0', 0.0, None):
            with self.subTest(value=value), self.assertRaises(ProfileError):
                validate_proxy_protocol(value)

    def test_public_contract_accepts_fixed_selected_control_and_media(self):
        validate_public_endpoints(M1_OWNER, M1_OWNER.public_control, M1_OWNER.public_media,
                                  network_scope='nps_owner', proxy_protocol=0)

    def test_public_control_media_role_node_ip_and_port_confusion_is_rejected(self):
        variants = (
            (M5_PLANNED.public_control, M1_OWNER.public_media),
            (M1_OWNER.public_control, M5_PLANNED.public_media),
            (M1_OWNER.local_https, M1_OWNER.local_udp),
            (Endpoint('146.56.249.175', 15556, 'tcp'), M1_OWNER.public_media),
            (M1_OWNER.public_control, Endpoint('146.56.249.175', 49556, 'udp')),
            (M1_OWNER.public_control, Endpoint('146.56.249.174', 15556, 'udp')),
            (M1_OWNER.public_control, Endpoint('100.65.0.2', 15556, 'udp')),
            (M1_OWNER.public_control, Endpoint('127.0.0.1', 15556, 'udp')),
            (M1_OWNER.public_control, Endpoint('0.0.0.0', 15556, 'udp')),
            (M1_OWNER.public_control, Endpoint('224.0.0.1', 15556, 'udp')),
            (M1_OWNER.public_control, Endpoint('146.56.249.175', 15556, 'tcp')),
            (('146.56.249.175', 49556), M1_OWNER.public_media),
        )
        for control, media in variants:
            with self.subTest(control=control, media=media), self.assertRaises(ProfileError):
                validate_public_endpoints(M1_OWNER, control, media,
                                          network_scope='nps_owner', proxy_protocol=0)

    def test_loopback_backend_does_not_bind_public_or_lan_address(self):
        validate_loopback_backend(M1_OWNER, M1_OWNER.local_https, M1_OWNER.local_udp,
                                 'lo0', proxy_protocol=0)
        variants = (
            (M1_OWNER.public_control, M1_OWNER.local_udp, 'lo0'),
            (M1_OWNER.local_https, M1_OWNER.public_media, 'lo0'),
            (M1_OWNER.local_https, Endpoint('0.0.0.0', 45965, 'udp'), 'lo0'),
            (M1_OWNER.local_https, Endpoint('127.0.0.2', 45965, 'udp'), 'lo0'),
            (M1_OWNER.local_https, Endpoint('192.168.9.128', 45965, 'udp'), 'lo0'),
            (Endpoint('127.0.0.1', 45560, 'tcp'), M1_OWNER.local_udp, 'lo0'),
            (M1_OWNER.local_https, Endpoint('127.0.0.1', 45963, 'udp'), 'lo0'),
            (M1_OWNER.local_https, M1_OWNER.local_udp, 'en7'),
            (M1_OWNER.local_https, M1_OWNER.local_udp, 'lo1'),
            (M1_OWNER.local_https, M1_OWNER.local_udp, None),
        )
        for https, udp, interface in variants:
            with self.subTest(https=https, udp=udp, interface=interface), self.assertRaises(ProfileError):
                validate_loopback_backend(M1_OWNER, https, udp, interface, proxy_protocol=0)

    def test_nonzero_proxy_header_rejected_for_both_endpoint_contracts(self):
        for value in (1, 2, True, False, '0', 0.0):
            with self.subTest(value=value), self.assertRaises(ProfileError):
                validate_public_endpoints(M1_OWNER, M1_OWNER.public_control, M1_OWNER.public_media,
                                          network_scope='nps_owner', proxy_protocol=value)
            with self.subTest(value=value), self.assertRaises(ProfileError):
                validate_loopback_backend(M1_OWNER, M1_OWNER.local_https,
                                          M1_OWNER.local_udp, 'lo0', proxy_protocol=value)

    def test_m5_cannot_pass_any_runtime_contract_without_friend_gate(self):
        actions = (
            lambda: validate_owner_request({'node': 'm5', 'network_scope': 'nps_owner'}),
            lambda: validate_public_endpoints(M5_PLANNED, M5_PLANNED.public_control,
                M5_PLANNED.public_media, network_scope='nps_owner', proxy_protocol=0),
            lambda: validate_loopback_backend(M5_PLANNED, M5_PLANNED.local_https,
                M5_PLANNED.local_udp, 'lo0', proxy_protocol=0),
            lambda: validate_proxy_peer(M5_PLANNED, ('127.0.0.1', 50001)),
        )
        for action in actions:
            with self.assertRaisesRegex(ProfileError, 'friend_deployment_isolation_gate_required'):
                action()

    def test_proxy_peer_is_exact_loopback_but_not_original_phone_identity(self):
        # Distinct HTTPS and UDP ephemeral ports are acceptable before the
        # worker's authenticated READY pin; this function performs no pinning.
        validate_proxy_peer(M1_OWNER, ('127.0.0.1', 50001))
        validate_proxy_peer(M1_OWNER, ('127.0.0.1', 50002))
        for peer in (None, [], ['127.0.0.1', 50001], (),
                     ('127.0.0.1',), ('127.0.0.1', 50001, 'extra'),
                     ('127.0.0.2', 50001), ('localhost', 50001), ('::1', 50001),
                     ('192.168.9.128', 50001), ('146.56.249.175', 50001),
                     ('100.65.0.3', 50001), ('127.0.0.1', True),
                     ('127.0.0.1', 0), ('127.0.0.1', 65536),
                     ('127.0.0.1', 50001.0), ('127.0.0.1', '50001')):
            with self.subTest(peer=peer), self.assertRaises(ProfileError):
                validate_proxy_peer(M1_OWNER, peer)

    def test_session_range_is_bounded_but_does_not_implement_lifecycle(self):
        for seconds in (1, 30, 120):
            validate_session_seconds(seconds)
        for seconds in (0, -1, 121, True, False, 120.0, '120', None):
            with self.subTest(seconds=seconds), self.assertRaises(ProfileError):
                validate_session_seconds(seconds)

    def test_existing_lan_tailnet_contract_remains_unmodified_and_public_scope_unintegrated(self):
        from udp_lan_sessions import UdpLanSessions, parse_udp_settings
        self.assertEqual(parse_udp_settings({})['network_scope'], 'lan')
        self.assertEqual(parse_udp_settings({'network_scope': 'tailnet'})['network_scope'], 'tailnet')
        with self.assertRaises(ValueError):
            parse_udp_settings({'network_scope': 'nps_owner'})
        with self.assertRaises(ValueError):
            UdpLanSessions('146.56.249.175', network_scope='nps_owner')


if __name__ == '__main__':
    unittest.main()
