"""Offline owner-NPS routing/auth/body/lifecycle fixtures, never live service.

Existing account verification is mocked at the inherited auth boundary. The
real registry handles reservation and expiry; sockets, TLS and lsof are fake.
These checks do not read deployment secrets or establish public phone/media
acceptance, source-network geography or Mac isolation.
"""
from email.message import Message
import io
import json
import socket
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from udp_lan_sessions import UdpLanSessions
from udp_network_scope import ScopeUnavailable
from udp_nps_profile import ProfileError, owner_profile, validate_proxy_peer
from udp_nps_gateway import (LoopbackTlsServer, OwnerNpsScope, competing_candidate_busy,
    guest_for, handler_for, parse_arguments, trial_busy, validate_settings)


class Clock:
    def __init__(self): self.now = 100.0
    def __call__(self): return self.now


class Worker:
    def __init__(self): self.starts = 0; self.stops = 0
    def start(self): self.starts += 1
    def stop(self): self.stops += 1


class Body(io.BytesIO):
    def __init__(self, value): super().__init__(value); self.reads = []
    def read(self, size=-1): self.reads.append(size); return super().read(size)


class FakeScope:
    name = 'nps_owner'
    ping_scope = 'bounded_owner_NPS_public_UDP_not_friend_deployment'
    def __init__(self, profile, allow=False):
        self.profile, self.allow, self.available = profile, allow, True
    def verify(self):
        if not self.available: raise ScopeUnavailable('fake_owned_scope_unavailable')
        return True
    def healthy(self): return self.available
    def permits_peer(self, peer):
        try:
            validate_proxy_peer(self.profile, peer, allow_m5_owner_trial=self.allow)
            return True
        except ProfileError:
            return False


class HandlerChecks(unittest.TestCase):
    def setUp(self): self.setup_profile('m1')

    def setup_profile(self, node):
        self.node, self.allow = node, node == 'm5'
        self.profile = owner_profile(node, allow_m5_owner_trial=self.allow)
        self.scope, self.clock = FakeScope(self.profile, self.allow), Clock()
        serial, avd = (('emulator-5556', 'RemoteAndroid17Compare') if node == 'm1'
                       else ('emulator-5554', 'phone17-root'))
        self.registry = UdpLanSessions(self.profile.public_media.host,
            self.profile.public_media.port, clock=self.clock, network_scope='nps_owner',
            node=node, scope_guard=self.scope.healthy, allow_m5_owner_trial=self.allow,
            guest_serial=serial, guest_avd=avd)
        self.worker, self.factory_calls, self.busy = Worker(), [], Mock(return_value=False)
        def factory(config, peer):
            self.factory_calls.append((config, peer))
            return self.worker
        self.handler = handler_for(self.registry, factory, self.profile, self.busy,
            scope=self.scope, allow_m5_owner_trial=self.allow)

    def request(self, path='/udp/session', body=None, *, headers=None,
                peer=('127.0.0.1', 50001), account='wyw', accepted=True):
        if body is None:
            body = json.dumps({'node': self.node, 'network_scope': 'nps_owner'}).encode()
        request = self.handler.__new__(self.handler)
        request.path, request.client_address = path, peer
        request.headers = Message()
        for name, value in ([('Content-Length', str(len(body)))] if headers is None else headers):
            request.headers.add_header(name, value)
        request.rfile, request.connection = Body(body), Mock()
        request.responses, request.auth_calls = [], 0
        request.reply = lambda status, result: request.responses.append((status, result))
        def auth():
            request.auth_calls += 1
            if accepted:
                request.account = account
                return True
            request.reply(401, {'error': 'fake_owned_auth_rejection'})
            return False
        request.auth = auth
        return request

    def dispatch(self, request, method='POST'):
        getattr(request, 'do_' + method)()
        self.assertEqual(len(request.responses), 1)
        return request.responses[0]

    def create(self):
        status, descriptor = self.dispatch(self.request())
        self.assertEqual(status, 201)
        return descriptor

    def test_exact_loopback_peer_required_on_every_signaling_route_before_auth(self):
        for method, path in (('POST', '/udp/session'), ('GET', '/ping'),
                             ('GET', '/udp/session/' + 'a' * 32),
                             ('DELETE', '/udp/session/' + 'a' * 32)):
            for peer in (('127.0.0.2', 50001), ('::1', 50001),
                         ('192.168.9.128', 50001), ('146.56.249.175', 50001),
                         ('127.0.0.1', True), ('127.0.0.1', 0)):
                with self.subTest(method=method, peer=peer):
                    request = self.request(path=path, peer=peer)
                    self.assertEqual(self.dispatch(request, method)[0], 403)
                    self.assertEqual(request.auth_calls, 0)
                    self.assertEqual(request.rfile.reads, [])
        self.assertEqual(self.factory_calls, [])

    def test_existing_auth_failure_never_reads_body_or_reserves_resources(self):
        request = self.request(accepted=False)
        self.assertEqual(self.dispatch(request)[0], 401)
        self.assertEqual(request.auth_calls, 1)
        self.assertEqual(request.rfile.reads, [])
        self.assertEqual(self.factory_calls, [])
        self.busy.assert_not_called()

    def test_authenticated_friend_account_cannot_bypass_owner_trial_gate(self):
        for node in ('m1', 'm5'):
            self.setup_profile(node)
            for method, path in (('POST', '/udp/session'),
                                 ('GET', '/udp/session/' + 'a' * 32),
                                 ('DELETE', '/udp/session/' + 'a' * 32)):
                request = self.request(path=path, account='huoguo')
                self.assertEqual(self.dispatch(request, method),
                                 (403, {'error': 'nps_owner_account_required'}))
                self.assertEqual(request.rfile.reads, [])
            self.assertEqual(self.factory_calls, [])

    def test_public_descriptor_and_private_worker_bind_are_distinct_for_both_nodes(self):
        for node, media_port, serial, avd in (('m1', 15556, 'emulator-5556', 'RemoteAndroid17Compare'),
                                            ('m5', 15558, 'emulator-5554', 'phone17-root')):
            with self.subTest(node=node):
                self.setup_profile(node)
                descriptor = self.create()
                self.assertEqual((descriptor['peer_host'], descriptor['peer_port'],
                                  descriptor['bind_port'], descriptor['node']),
                                 ('146.56.249.175', media_port, 0, node))
                config, peer = self.factory_calls[0]
                self.assertEqual((peer, config['local_bind_host'], config['local_bind_port'],
                                  config['guest_serial'], config['guest_avd']),
                                 ('127.0.0.1', '127.0.0.1', 45965, serial, avd))
                for private in ('local_bind_host', 'local_bind_port', 'guest_serial', 'guest_avd'):
                    self.assertNotIn(private, descriptor)
                self.assertEqual(self.worker.starts, 0)

    def test_closed_request_rejects_scope_node_or_routing_override_before_worker(self):
        cases = ({}, {'node': 'm1', 'network_scope': 'lan'},
                 {'node': 'm5', 'network_scope': 'nps_owner'},
                 {'node': 'm1', 'network_scope': 'nps_owner', 'guest_serial': 'phone'},
                 {'node': 'm1', 'network_scope': 'nps_owner', 'bind_port': 45965},
                 {'node': 'm1', 'network_scope': 'nps_owner', 'peer_host': '127.0.0.1'},
                 {'node': 'm1', 'network_scope': 'nps_owner', 'proxy_protocol': 0},
                 {'node': 'm1', 'network_scope': 'nps_owner', 'seconds': 121},
                 {'node': 'm1', 'network_scope': 'nps_owner', 'surface_submit_lead_ms': 16})
        for value in cases:
            with self.subTest(value=value):
                self.assertEqual(self.dispatch(self.request(body=json.dumps(value).encode()))[0], 400)
        self.assertEqual(self.factory_calls, [])

    def test_duplicate_transfer_oversize_or_incomplete_body_never_reads_unbounded(self):
        headers = ([], [('Content-Length', '0')], [('Content-Length', '513')],
                   [('Content-Length', '-1')], [('Content-Length', '2'), ('Content-Length', '2')],
                   [('Content-Length', '2'), ('Transfer-Encoding', 'chunked')])
        for values in headers:
            request = self.request(headers=values)
            self.assertEqual(self.dispatch(request)[0], 400)
            self.assertEqual(request.rfile.reads, [])
        request = self.request(body=b'{', headers=[('Content-Length', '2')])
        self.assertEqual(self.dispatch(request)[0], 400)
        self.assertEqual(request.rfile.reads, [2])
        for body in (b'null', b'[]', b'\xff\xff'):
            self.assertEqual(self.dispatch(self.request(body=body))[0], 400)
        self.assertEqual(self.factory_calls, [])

    def test_busy_and_busy_check_failure_fail_closed_without_worker_or_displacement(self):
        self.busy.return_value = True
        self.assertEqual(self.dispatch(self.request()),
            (409, {'error': 'formal_or_other_candidate_busy_skip_trial'}))
        self.busy.side_effect = subprocess.TimeoutExpired(['fake-owned-check'], 2)
        self.assertEqual(self.dispatch(self.request()),
            (503, {'error': 'busy_check_unavailable_skip_candidate'}))
        self.assertEqual(self.factory_calls, [])

    def test_scope_revocation_blocks_new_status_but_normal_owned_delete_still_works(self):
        descriptor = self.create()
        path = '/udp/session/' + descriptor['session']
        self.scope.available = False
        self.assertEqual(self.dispatch(self.request(path=path), 'GET'),
                         (503, {'error': 'udp_network_scope_unavailable'}))
        self.assertEqual(self.worker.stops, 1)
        self.assertEqual(self.dispatch(self.request(path=path), 'DELETE'), (200, {'closed': True}))
        self.assertEqual(self.worker.stops, 1)

    def test_reserved_second_session_is_not_displacing_and_waits_for_GCM_ready(self):
        first = self.create()
        self.assertEqual(self.dispatch(self.request()), (409, {'error': 'udp_session_busy'}))
        self.assertEqual(len(self.factory_calls), 1)
        self.assertEqual(self.worker.starts, 0)
        self.assertTrue(self.registry.authenticated_ready(first['session']))
        self.assertEqual(self.worker.starts, 1)

    def test_auth_and_expiry_use_same_account_bound_existing_registry(self):
        descriptor = self.create()
        sid, path = descriptor['session'], '/udp/session/' + descriptor['session']
        self.assertTrue(self.registry.authenticated_ready(sid))
        request = self.request(path=path, accepted=False)
        self.assertEqual(self.dispatch(request, 'DELETE')[0], 401)
        self.assertEqual(self.worker.stops, 0)
        self.clock.now += 3
        self.assertEqual(self.dispatch(self.request(path=path), 'GET')[0], 404)
        self.assertEqual(self.worker.stops, 1)
        self.assertFalse(self.registry.authenticated_ready(sid))
        self.assertEqual(self.dispatch(self.request(path=path), 'DELETE'), (200, {'closed': True}))
        self.assertEqual(self.worker.stops, 1)

    def test_hard_120_second_deadline_not_extended_by_authenticated_alive(self):
        descriptor = self.create()
        sid = descriptor['session']
        self.registry.authenticated_ready(sid)
        for second in range(1, 120):
            self.clock.now = 100.0 + second
            self.assertTrue(self.registry.authenticated_alive(sid))
        self.clock.now = 220.0
        self.assertFalse(self.registry.authenticated_alive(sid))
        self.assertEqual(self.worker.stops, 1)

    def test_unknown_routes_and_CONNECT_do_not_inherit_formal_TCP_media(self):
        for method in ('GET', 'POST', 'DELETE', 'CONNECT'):
            request = self.request(path='/session')
            self.assertEqual(self.dispatch(request, method)[0], 404)
            self.assertEqual(request.auth_calls, 0)
        self.assertEqual(self.factory_calls, [])

    def test_ping_describes_owner_unaccepted_scope_without_credentials(self):
        request = self.request(path='/ping', accepted=False)
        status, result = self.dispatch(request, 'GET')
        self.assertEqual(status, 200)
        self.assertEqual((result['media'], result['node'], result['network_scope']),
                         ('UDP', 'm1', 'nps_owner'))
        self.assertTrue(result['owner_trial'])
        self.assertFalse(result['host_isolation_accepted'])
        self.assertNotIn('key_b64', result)
        self.assertEqual(request.auth_calls, 0)


class PlacementChecks(unittest.TestCase):
    def test_guest_is_selected_only_from_explicit_known_host_environment(self):
        for node, serial, avd in (('m1', 'emulator-5556', 'RemoteAndroid17Compare'),
                                  ('m5', 'emulator-5554', 'phone17-root')):
            profile = owner_profile(node, allow_m5_owner_trial=node == 'm5')
            env = {'DIRECT_SERIAL': serial, 'DIRECT_AVD': avd, 'DIRECT_EXTERNAL_VM': '1'}
            self.assertEqual(guest_for(profile, env), (serial, avd))
            for key in env:
                wrong = dict(env); wrong.pop(key)
                with self.assertRaises(ProfileError): guest_for(profile, wrong)
            wrong = dict(env); wrong['DIRECT_SERIAL'] = 'physical-phone'
            with self.assertRaises(ProfileError): guest_for(profile, wrong)

    def test_service_lifetime_and_M5_owner_flag_have_no_endpoint_override(self):
        common = ['--runtime', '/fake/runtime', '--packetizer', '/fake/packetizer',
                  '--native-encoder', '/fake/encoder', '--evidence-dir', '/fake/evidence']
        for node, extra in (('m1', []), ('m5', ['--owner-m5-trial'])):
            args = parse_arguments(['--node', node, '--max-runtime', '3600', *extra, *common])
            self.assertEqual((args.profile.local_https.host, args.profile.interface), ('127.0.0.1', 'lo0'))
        for extra in (['--node', 'm5'], ['--node', 'm1', '--owner-m5-trial'],
                      ['--node', 'm1', '--max-runtime', '3601'],
                      ['--node', 'm1', '--host', '0.0.0.0']):
            with self.subTest(extra=extra), patch('sys.stderr', io.StringIO()):
                with self.assertRaises(SystemExit): parse_arguments([*extra, *common])

    def test_busy_check_keeps_formal_port_and_other_candidate_separate_from_new_signal(self):
        with patch('udp_nps_gateway.formal_busy', return_value=True) as formal, \
             patch('udp_nps_gateway.competing_candidate_busy') as candidate:
            self.assertTrue(trial_busy()); formal.assert_called_once(); candidate.assert_not_called()
        with patch('udp_nps_gateway.subprocess.run', return_value=SimpleNamespace(
                returncode=1, stdout='')) as run:
            self.assertFalse(competing_candidate_busy())
        self.assertEqual([call.args[0] for call in run.call_args_list],
            [['lsof', '-nP', '-iTCP:45560', '-sTCP:LISTEN', '-t'],
             ['lsof', '-nP', '-iUDP:45963', '-t']])
        for result in (SimpleNamespace(returncode=0, stdout='123\n'),
                       SimpleNamespace(returncode=2, stdout='')):
            with patch('udp_nps_gateway.subprocess.run', return_value=result):
                self.assertTrue(competing_candidate_busy())

    def test_loopback_scope_failure_is_sticky_and_never_claims_WAN_verification(self):
        with patch('udp_nps_gateway.socket.if_nametoindex', return_value=1) as lookup:
            scope = OwnerNpsScope(owner_profile('m1'))
            self.assertTrue(scope.healthy()); lookup.assert_called_once_with('lo0')
        with patch('udp_nps_gateway.socket.if_nametoindex', side_effect=OSError()):
            with self.assertRaises(ScopeUnavailable): scope.verify()
        with patch('udp_nps_gateway.socket.if_nametoindex') as lookup:
            with self.assertRaises(ScopeUnavailable): scope.verify()
            lookup.assert_not_called()
        self.assertFalse(scope.healthy())

    def test_TLS_accept_rejects_foreign_proxy_before_handshake_and_closes_only_owned_socket(self):
        server = LoopbackTlsServer.__new__(LoopbackTlsServer)
        server.profile, server.allow_m5_owner_trial = owner_profile('m1'), False
        server.context, server.socket = Mock(), Mock()
        connection = Mock()
        server.socket.accept.return_value = (connection, ('127.0.0.2', 50001))
        with self.assertRaises(ProfileError): server.get_request()
        connection.close.assert_called_once(); server.context.wrap_socket.assert_not_called()
        connection = Mock()
        server.socket.accept.return_value = (connection, ('127.0.0.1', 50001))
        result = server.get_request()
        connection.settimeout.assert_called_once_with(5)
        server.context.wrap_socket.assert_called_once_with(connection, server_side=True)
        self.assertEqual(result[1], ('127.0.0.1', 50001))
        connection.close.assert_not_called()

    def test_loopback_HTTPS_bind_requires_interface_readback_and_exact_endpoint(self):
        server = LoopbackTlsServer.__new__(LoopbackTlsServer)
        server.profile, server.socket = owner_profile('m1'), Mock()
        server.socket.getsockopt.return_value = 1
        server.socket.getsockname.return_value = ('127.0.0.1', 45561)
        with patch('udp_nps_gateway.socket.if_nametoindex', return_value=1), \
             patch('http.server.ThreadingHTTPServer.server_bind') as bind:
            server.server_bind()
            bind.assert_called_once_with(server)
        server.socket.setsockopt.assert_called_once_with(socket.IPPROTO_IP, 25, 1)
        for wrong_interface, wrong_endpoint in ((2, ('127.0.0.1', 45561)),
                                                (1, ('0.0.0.0', 45561))):
            server.socket.getsockopt.return_value = wrong_interface
            server.socket.getsockname.return_value = wrong_endpoint
            with patch('udp_nps_gateway.socket.if_nametoindex', return_value=1), \
                 patch('http.server.ThreadingHTTPServer.server_bind'):
                with self.assertRaises(ScopeUnavailable): server.server_bind()


if __name__ == '__main__':
    unittest.main()
