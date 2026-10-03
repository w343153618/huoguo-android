"""Candidate HTTPS routing/body checks using fake auth and workers, no listener.

This layer does not read credentials, launch an emulator, open a network socket,
or claim Android/phone acceptance. TLS and real account authentication belong to
the independently validated existing gateway and the live acceptance layer.
"""
from email.message import Message
import io
import json
import ssl
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from udp_lan_gateway import BoundedTlsServer, handler_for, physical_lan_address, same_private_lan, shutdown_registry
from udp_network_scope import ScopeUnavailable
from udp_lan_sessions import SessionError, UdpLanSessions


class ShutdownChecks(unittest.TestCase):
    def test_shutdown_barrier_uses_bounded_default_and_reports_only_closed_result(self):
        registry = Mock()
        registry.close_and_wait.return_value = {'quiescence_confirmed': True, 'stop_failures': 0}
        self.assertEqual(shutdown_registry(registry), {'event': 'candidate_shutdown',
            'quiescence_confirmed': True, 'stop_failures': 0})
        registry.close_and_wait.assert_called_once_with(45.0)
        registry.close.assert_not_called()

    def test_timeout_and_cleanup_failure_are_not_successful_shutdown(self):
        for reason in ('udp_shutdown_quiescence_timeout', 'udp_cleanup_failed', 'udp_shutdown_worker_unavailable'):
            with self.subTest(reason=reason):
                registry = Mock()
                registry.close_and_wait.side_effect = SessionError(503, reason)
                self.assertEqual(shutdown_registry(registry, .025), {'event': 'candidate_shutdown',
                    'quiescence_confirmed': False, 'reason': reason})

    def test_unknown_shutdown_detail_is_not_exported(self):
        for failure in (SessionError(503, 'untrusted_account_key_detail'), RuntimeError('untrusted detail')):
            with self.subTest(failure=type(failure).__name__):
                registry = Mock()
                registry.close_and_wait.side_effect = failure
                self.assertEqual(shutdown_registry(registry), {'event': 'candidate_shutdown',
                    'quiescence_confirmed': False, 'reason': 'udp_shutdown_unclassified_failure'})

    def test_shutdown_result_schema_never_forwards_additional_detail(self):
        registry = Mock()
        registry.close_and_wait.return_value = {'quiescence_confirmed': True,
            'stop_failures': 0, 'untrusted_detail': 'not a public field'}
        self.assertEqual(shutdown_registry(registry), {'event': 'candidate_shutdown',
            'quiescence_confirmed': True, 'stop_failures': 0})
        for result in ({}, {'quiescence_confirmed': False, 'stop_failures': 0},
                       {'quiescence_confirmed': True, 'stop_failures': True}):
            registry.close_and_wait.return_value = result
            self.assertFalse(shutdown_registry(registry)['quiescence_confirmed'])


class Worker:
    def __init__(self): self.starts = 0; self.stops = 0
    def start(self): self.starts += 1
    def stop(self): self.stops += 1


class CountingBody(io.BytesIO):
    def __init__(self, body): super().__init__(body); self.read_calls = []
    def read(self, size=-1): self.read_calls.append(size); return super().read(size)


class HandlerChecks(unittest.TestCase):
    def setUp(self):
        self.registry = UdpLanSessions('192.168.9.128')
        self.worker = Worker()
        self.factory_calls = []
        self.busy = Mock(return_value=False)
        def factory(config, peer):
            self.factory_calls.append((config, peer))
            return self.worker
        self.handler = handler_for(self.registry, factory, '192.168.9.128', self.busy)

    def request(self, path='/udp/session', body=b'{}', headers=None,
                peer='192.168.9.149', account='test-owner', accepted=True):
        request = self.handler.__new__(self.handler)
        request.path, request.client_address = path, (peer, 45678)
        request.headers = Message()
        for key, value in ([('Content-Length', str(len(body)))] if headers is None else headers):
            request.headers.add_header(key, value)
        request.rfile = CountingBody(body)
        request.connection = Mock()
        request.responses = []
        request.auth_calls = 0
        request.reply = lambda status, result: request.responses.append((status, result))
        def auth():
            request.auth_calls += 1
            if accepted:
                request.account = account
                return True
            request.reply(401, {'error': 'fake_auth_rejected'})
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

    def test_https_auth_rejection_never_reads_settings_or_allocates_worker(self):
        request = self.request(accepted=False)
        self.assertEqual(self.dispatch(request)[0], 401)
        self.assertEqual(request.rfile.read_calls, [])
        self.assertEqual(request.auth_calls, 1)
        self.assertEqual(self.factory_calls, [])
        self.busy.assert_not_called()

    def test_foreign_subnet_rejected_before_auth_body_or_worker(self):
        for peer in ('192.168.10.149', '100.65.0.3', '146.56.249.175',
                     '192.168.9.0', '192.168.9.255', '::1'):
            with self.subTest(peer=peer):
                request = self.request(peer=peer)
                self.assertEqual(self.dispatch(request)[0], 403)
                self.assertEqual(request.auth_calls, 0)
                self.assertEqual(request.rfile.read_calls, [])
        self.assertEqual(self.factory_calls, [])

    def test_valid_authenticated_request_allocates_one_owned_waiting_worker(self):
        request = self.request(body=b'{"max_size":1920,"max_fps":120,"buffer_ms":80}')
        status, descriptor = self.dispatch(request)
        self.assertEqual(status, 201)
        self.assertEqual(request.rfile.read_calls, [request.rfile.tell()])
        request.connection.settimeout.assert_called_once_with(5)
        self.assertEqual(len(self.factory_calls), 1)
        config, peer = self.factory_calls[0]
        self.assertEqual(peer, '192.168.9.149')
        self.assertEqual(config['session'], descriptor['session'])
        self.assertEqual(descriptor['max_size'], 1920)
        self.assertEqual(descriptor['fps'], 120)
        self.assertEqual(self.worker.starts, 0)

    def test_default_gateway_rejects_authenticated_nonzero_surface_lead_without_worker(self):
        self.assertEqual(self.dispatch(self.request(body=b'{"surface_submit_lead_ms":16}')),
            (400, {'error': 'owner_surface_submit_experiment_not_enabled'}))
        self.assertEqual(self.factory_calls, [])

    def test_explicit_owner_surface_flag_preserves_auth_and_formal_busy_before_allocation(self):
        self.registry = UdpLanSessions('192.168.9.128', allow_owner_surface_submit_lead=True)
        def factory(config, peer):
            self.factory_calls.append((config, peer))
            return self.worker
        self.handler = handler_for(self.registry, factory, '192.168.9.128', self.busy)
        body = b'{"surface_submit_lead_ms":16}'
        self.assertEqual(self.dispatch(self.request(body=body, accepted=False))[0], 401)
        self.busy.return_value = True
        self.assertEqual(self.dispatch(self.request(body=body))[0], 409)
        self.assertEqual(self.factory_calls, [])
        self.busy.return_value = False
        status, descriptor = self.dispatch(self.request(body=body))
        self.assertEqual(status, 201)
        self.assertEqual(descriptor['surface_submit_lead_ms'], 16)
        self.assertEqual(self.factory_calls[0][0]['surface_submit_lead_ms'], 16)
        self.assertEqual(self.worker.starts, 0)

    def test_bounded_content_length_requires_one_decimal_value_and_rejects_any_transfer_encoding(self):
        invalid = [[], [('Content-Length', '0')], [('Content-Length', '-1')],
                   [('Content-Length', '513')], [('Content-Length', 'no')],
                   [('Content-Length', '+2')], [('Content-Length', ' 2')],
                   [('Content-Length', '2 ')],
                   [('Content-Length', '2'), ('Content-Length', '2')],
                   [('Content-Length', '2'), ('Transfer-Encoding', 'chunked')],
                   [('Content-Length', '2'), ('Transfer-Encoding', '')]]
        for headers in invalid:
            with self.subTest(headers=headers):
                request = self.request(headers=headers)
                self.assertEqual(self.dispatch(request)[0], 400)
                self.assertEqual(request.rfile.read_calls, [])
        self.assertEqual(self.factory_calls, [])

    def test_exact_body_size_and_json_object_are_required(self):
        for body, length in ((b'{', '2'), (b'{broken}', '8'), (b'null', '4'),
                             (b'[]', '2'), (b'\xff\xff', '2'),
                             (b'{"max_fps":30}', '14'), (b'{"buffer_ms":120}', '17')):
            with self.subTest(body=body):
                request = self.request(body=body, headers=[('Content-Length', length)])
                self.assertEqual(self.dispatch(request)[0], 400)
        self.assertEqual(self.factory_calls, [])

    def test_body_read_timeout_returns_safe_error_and_no_worker(self):
        request = self.request()
        request.rfile = Mock()
        request.rfile.read.side_effect = TimeoutError('fake read timeout')
        self.assertEqual(self.dispatch(request), (400, {'error': 'invalid_bounded_settings'}))
        self.assertEqual(self.factory_calls, [])

    def test_formal_busy_never_starts_candidate_or_displaces_a_session(self):
        self.busy.return_value = True
        status, response = self.dispatch(self.request())
        self.assertEqual((status, response['error']),
                         (409, 'formal_session_busy_skip_candidate'))
        self.assertEqual(self.factory_calls, [])

    def test_busy_check_timeout_fails_closed_without_worker(self):
        self.busy.side_effect = subprocess.TimeoutExpired(['fake-owned-check'], 2)
        status, response = self.dispatch(self.request())
        self.assertEqual(status, 503)
        self.assertEqual(response['error'], 'busy_check_unavailable_skip_candidate')
        self.assertEqual(self.factory_calls, [])

    def test_second_authenticated_owner_gets_busy_without_displacement(self):
        first = self.create()
        status, response = self.dispatch(self.request(account='second-owner'))
        self.assertEqual((status, response['error']), (409, 'udp_session_busy'))
        self.assertEqual(len(self.factory_calls), 1)
        self.assertEqual(self.worker.stops, 0)
        self.assertIsNotNone(self.registry.status('test-owner', first['session']))

    def test_owner_status_has_no_keys_and_other_owner_is_not_found(self):
        descriptor = self.create()
        path = '/udp/session/' + descriptor['session']
        status, response = self.dispatch(self.request(path=path), 'GET')
        self.assertEqual(status, 200)
        self.assertEqual(response['phase'], 'waiting')
        self.assertNotIn(descriptor['key_b64'], json.dumps(response))
        self.assertNotIn('account', response)
        self.assertEqual(self.dispatch(self.request(path=path, account='second-owner'), 'GET')[0], 404)
        self.assertEqual(self.dispatch(self.request(path='/udp/session/' + 'f' * 32), 'GET')[0], 404)

    def test_delete_requires_owner_and_is_idempotent_only_for_owned_tombstone(self):
        sid = self.create()['session']
        path = '/udp/session/' + sid
        self.registry.authenticated_ready(sid)
        self.assertEqual(self.dispatch(self.request(path=path, account='second-owner'), 'DELETE')[0], 404)
        self.assertTrue(self.registry.touch_allowed(sid))
        self.assertEqual(self.dispatch(self.request(path=path), 'DELETE'), (200, {'closed': True}))
        self.assertFalse(self.registry.touch_allowed(sid))
        self.assertEqual(self.dispatch(self.request(path=path), 'DELETE')[0], 200)
        self.assertEqual(self.worker.stops, 1)
        self.assertEqual(self.dispatch(self.request(path='/udp/session/' + 'f' * 32), 'DELETE')[0], 404)

    def test_status_and_delete_need_auth_and_never_run_on_rejection(self):
        for method in ('GET', 'DELETE'):
            with self.subTest(method=method):
                request = self.request(path='/udp/session/' + '0' * 32, accepted=False)
                self.assertEqual(self.dispatch(request, method)[0], 401)
        self.assertEqual(self.factory_calls, [])

    def test_worker_factory_failure_only_exposes_safe_code(self):
        def failure(config, peer):
            raise RuntimeError('fake failure with ephemeral secret ' + config['key_b64'])
        handler = handler_for(self.registry, failure, '192.168.9.128', self.busy)
        request = self.request()
        request.__class__ = handler
        self.assertEqual(self.dispatch(request), (503, {'error': 'udp_worker_unavailable'}))

    def test_unrecognized_routes_and_connect_never_delegate_to_tcp_media(self):
        for method in ('GET', 'POST', 'DELETE', 'CONNECT'):
            with self.subTest(method=method):
                request = self.request(path='/session')
                self.assertEqual(self.dispatch(request, method)[0], 404)
                self.assertEqual(request.auth_calls, 0)
        self.assertEqual(self.factory_calls, [])

    def test_public_ping_explains_scope_without_session_credentials(self):
        request = self.request(path='/ping', accepted=False)
        status, response = self.dispatch(request, 'GET')
        self.assertEqual(status, 200)
        self.assertEqual(response['media'], 'UDP')
        self.assertEqual(response['scope'], 'isolated_same_subnet_LAN_not_WAN')
        self.assertNotIn('key_b64', response)
        self.assertEqual(request.auth_calls, 0)


class TailnetHandlerChecks(unittest.TestCase):
    request = HandlerChecks.request
    dispatch = HandlerChecks.dispatch

    def setUp(self):
        self.worker = Worker()
        self.factory_calls = []
        self.healthy = True
        self.scope = Mock()
        self.scope.name = 'tailnet'
        self.scope.ping_scope = 'isolated_registered_Tailnet_UDP_not_public_UDP'
        self.scope.permits_peer.side_effect = lambda peer: peer == '100.65.0.3'
        self.scope.verify.return_value = True
        self.registry = UdpLanSessions('100.65.0.2', network_scope='tailnet',
                                       scope_guard=lambda: self.healthy)
        self.busy = Mock(return_value=False)
        def factory(config, peer):
            self.factory_calls.append((config, peer))
            return self.worker
        self.handler = handler_for(self.registry, factory, '100.65.0.2', self.busy, scope=self.scope)

    def tailnet_request(self, **kwargs):
        kwargs.setdefault('peer', '100.65.0.3')
        kwargs.setdefault('body', b'{"network_scope":"tailnet"}')
        return self.request(**kwargs)

    def create(self):
        status, result = self.dispatch(self.tailnet_request())
        self.assertEqual(status, 201)
        return result

    def test_only_registered_peer_is_allowed_without_auth_or_localapi_for_unknown_peer(self):
        for peer in ('100.65.0.4', '100.65.0.11', '100.64.0.3', '192.168.9.149'):
            with self.subTest(peer=peer):
                request = self.tailnet_request(peer=peer)
                self.assertEqual(self.dispatch(request)[0], 403)
                self.assertEqual(request.auth_calls, 0)
                self.assertEqual(request.rfile.read_calls, [])
        self.scope.verify.assert_not_called()
        self.assertEqual(self.factory_calls, [])

    def test_tailnet_request_descriptor_and_peer_are_exact_and_media_waits_for_ready(self):
        descriptor = self.create()
        self.assertEqual((descriptor['network_scope'], descriptor['peer_host'], descriptor['peer_port']),
                         ('tailnet', '100.65.0.2', 45963))
        self.assertEqual(self.factory_calls[0][1], '100.65.0.3')
        self.scope.verify.assert_called_once_with()
        self.assertEqual(self.worker.starts, 0)

    def test_missing_or_mismatched_request_scope_never_allocates_worker(self):
        for body in (b'{}', b'{"network_scope":"lan"}'):
            self.assertEqual(self.dispatch(self.tailnet_request(body=body))[0], 400)
        self.assertEqual(self.factory_calls, [])

    def test_scope_failure_before_body_read_allocates_nothing_and_stops_existing_owned_session(self):
        descriptor = self.create()
        self.registry.authenticated_ready(descriptor['session'])
        self.scope.verify.side_effect = ScopeUnavailable('fixed_test_code')
        request = self.tailnet_request()
        self.assertEqual(self.dispatch(request), (503, {'error': 'udp_network_scope_unavailable'}))
        self.assertEqual(request.rfile.read_calls, [])
        self.assertEqual(self.worker.stops, 1)
        self.assertEqual(len(self.factory_calls), 1)
        # Cleanup remains available even if the scope cannot be reverified.
        path = '/udp/session/' + descriptor['session']
        self.assertEqual(self.dispatch(self.tailnet_request(path=path), 'DELETE'),
                         (200, {'closed': True}))

    def test_status_revalidates_and_unknown_peer_cannot_status_or_delete(self):
        descriptor = self.create()
        path = '/udp/session/' + descriptor['session']
        self.assertEqual(self.dispatch(self.tailnet_request(path=path), 'GET')[0], 200)
        self.assertEqual(self.scope.verify.call_count, 2)
        for method in ('GET', 'DELETE'):
            self.assertEqual(self.dispatch(self.tailnet_request(path=path, peer='100.65.0.4'), method)[0], 403)
        self.assertEqual(self.worker.stops, 0)


class PhysicalScopeChecks(unittest.TestCase):
    def test_physical_interface_must_match_exact_requested_address(self):
        with patch('udp_lan_gateway.subprocess.run', return_value=SimpleNamespace(stdout='192.168.9.128\n')) as run:
            self.assertEqual(physical_lan_address('192.168.9.128', 'en7'), '192.168.9.128')
            run.assert_called_once_with(['ipconfig', 'getifaddr', 'en7'], check=True,
                                        capture_output=True, text=True, timeout=2)
        with patch('udp_lan_gateway.subprocess.run', return_value=SimpleNamespace(stdout='192.168.9.125\n')):
            with self.assertRaises(ValueError):
                physical_lan_address('192.168.9.128', 'en7')

    def test_nonphysical_or_nonprivate_configuration_rejected_before_query(self):
        for host, interface in (('100.65.0.2', 'en7'), ('146.56.249.175', 'en7'),
                                ('127.0.0.1', 'en0'), ('192.168.9.128', 'utun4')):
            with self.subTest(host=host, interface=interface), patch('udp_lan_gateway.subprocess.run') as run:
                with self.assertRaises(ValueError):
                    physical_lan_address(host, interface)
                run.assert_not_called()

    def test_exact_physical_subnet_only(self):
        self.assertTrue(same_private_lan('192.168.9.149', '192.168.9.128'))
        for peer in ('192.168.10.149', '192.168.9.255', '192.168.9.0', 'not-an-IP', '::1'):
            with self.subTest(peer=peer):
                self.assertFalse(same_private_lan(peer, '192.168.9.128'))


class TlsDeadlineChecks(unittest.TestCase):
    def test_tailnet_listener_verifies_exact_inner_kernel_interface_before_bind(self):
        server = BoundedTlsServer.__new__(BoundedTlsServer)
        server.interface, server.socket = 'utun0', Mock()
        server.socket.getsockopt.return_value = 20
        with patch('udp_lan_gateway.socket.if_nametoindex', return_value=20), \
                patch('udp_lan_gateway.ThreadingHTTPServer.server_bind') as bind:
            server.server_bind()
        server.socket.setsockopt.assert_called_once_with(0, 25, 20)
        server.socket.getsockopt.assert_called_once_with(0, 25)
        bind.assert_called_once_with()

    def test_tailnet_listener_bind_mismatch_fails_before_listening(self):
        server = BoundedTlsServer.__new__(BoundedTlsServer)
        server.interface, server.socket = 'utun0', Mock()
        server.socket.getsockopt.return_value = 7
        with patch('udp_lan_gateway.socket.if_nametoindex', return_value=20), \
                patch('udp_lan_gateway.ThreadingHTTPServer.server_bind') as bind:
            with self.assertRaises(ScopeUnavailable):
                server.server_bind()
        bind.assert_not_called()

    def test_accepted_connection_gets_deadline_before_tls_handshake(self):
        # __new__ deliberately avoids socket creation or an HTTP listener.
        server = BoundedTlsServer.__new__(BoundedTlsServer)
        connection, tls_connection = Mock(), Mock()
        server.socket = Mock()
        peer = ('192.168.9.149', 45678)
        server.socket.accept.return_value = (connection, peer)
        order = []
        connection.settimeout.side_effect = lambda timeout: order.append(('timeout', timeout))
        server.context = Mock()
        def wrap(sock, server_side):
            order.append(('handshake', server_side))
            self.assertIs(sock, connection)
            return tls_connection
        server.context.wrap_socket.side_effect = wrap
        self.assertEqual(server.get_request(), (tls_connection, peer))
        self.assertEqual(order, [('timeout', 5), ('handshake', True)])
        connection.close.assert_not_called()

    def test_tls_handshake_failure_closes_accepted_owned_connection(self):
        server = BoundedTlsServer.__new__(BoundedTlsServer)
        connection = Mock()
        server.socket, server.context = Mock(), Mock()
        server.socket.accept.return_value = (connection, ('192.168.9.149', 45678))
        server.context.wrap_socket.side_effect = ssl.SSLError('fake handshake failure')
        with self.assertRaises(ssl.SSLError):
            server.get_request()
        connection.settimeout.assert_called_once_with(5)
        connection.close.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
