"""Owned trusted-account policy checks; no files, listeners, auth secrets or devices.

The HTTPS authentication boundary is mocked. Actual existing password
verification remains inherited and unchanged; these fixtures verify that it is
called before the new closed owner policy and actual account-owned registry.
"""
from email.message import Message
import io
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from udp_lan_sessions import (SessionError, UdpLanSessions,
                              trusted_nps_owner_accounts)
from udp_nps_gateway import handler_for, main, parse_arguments
from udp_nps_profile import owner_profile


class Worker:
    def __init__(self): self.starts = 0; self.stops = 0
    def start(self): self.starts += 1
    def stop(self): self.stops += 1


class Scope:
    name = 'nps_owner'
    ping_scope = 'bounded_owner_NPS_public_UDP_not_friend_deployment'
    def healthy(self): return True
    def verify(self): return True
    def permits_peer(self, peer): return peer == ('127.0.0.1', 50001)


def registry(node='m1', accounts=None):
    profile = owner_profile(node, allow_m5_owner_trial=node == 'm5')
    return UdpLanSessions(profile.public_media.host, profile.public_media.port,
        network_scope='nps_owner', node=node, scope_guard=lambda: True,
        allow_m5_owner_trial=node == 'm5', guest_serial=profile.guest_serial,
        guest_avd=profile.guest_avd, owner_accounts=accounts)


def arguments(node='m1', *extra):
    common = ['--node', node, '--runtime', '/fake/runtime',
        '--packetizer', '/fake/packetizer', '--native-encoder', '/fake/encoder',
        '--evidence-dir', '/fake/evidence', '--max-runtime', '3600']
    if node == 'm5': common.append('--owner-m5-trial')
    return parse_arguments([*common, *extra])


class PolicyChecks(unittest.TestCase):
    def test_default_and_explicit_closed_account_lists_are_immutable_and_canonical(self):
        self.assertEqual(trusted_nps_owner_accounts(), ('wyw',))
        self.assertEqual(trusted_nps_owner_accounts(['huoguo']), ('huoguo',))
        self.assertEqual(trusted_nps_owner_accounts(['wyw', 'huoguo']), ('huoguo', 'wyw'))
        source = ['wyw', 'huoguo']; candidate = registry(accounts=source)
        source[:] = ['outsider']
        self.assertEqual(candidate.owner_accounts, ('huoguo', 'wyw'))
        with self.assertRaises(AttributeError): candidate.owner_accounts = ('outsider',)

    def test_invalid_unbounded_duplicate_typed_or_arbitrary_lists_fail_closed(self):
        for accounts in ([], (), ['wyw', 'wyw'], ['huoguo', 'huoguo'],
                ['wyw', 'huoguo', 'wyw'], ['WYw'], ['outsider'], [''],
                ['wyw', True], [1], [None], [[]], 'huoguo', False, 1,
                {'wyw'}, {'account': 'huoguo'}):
            with self.subTest(accounts=accounts), self.assertRaises(ValueError):
                trusted_nps_owner_accounts(accounts)
            with self.subTest(registry_accounts=accounts), self.assertRaises(ValueError):
                registry(accounts=accounts)

    def test_non_nps_scopes_cannot_accept_even_default_explicit_policy(self):
        for scope, host in (('lan', '192.168.9.128'), ('tailnet', '100.65.0.2')):
            for accounts in (['wyw'], ['huoguo'], ['wyw', 'huoguo'], False, []):
                with self.subTest(scope=scope, accounts=accounts), self.assertRaises(ValueError):
                    UdpLanSessions(host, network_scope=scope,
                        scope_guard=lambda: True, owner_accounts=accounts)
            candidate = UdpLanSessions(host, network_scope=scope, scope_guard=lambda: True)
            self.assertIsNone(candidate.owner_accounts)

    def test_cli_default_or_repeated_trusted_parameters_have_closed_policy(self):
        for node in ('m1', 'm5'):
            self.assertEqual(arguments(node).owner_accounts, ('wyw',))
            self.assertEqual(arguments(node, '--owner-account', 'huoguo').owner_accounts,
                             ('huoguo',))
            self.assertEqual(arguments(node, '--owner-account', 'wyw',
                '--owner-account', 'huoguo').owner_accounts, ('huoguo', 'wyw'))
        for extra in (('--owner-account', 'outsider'), ('--owner-account', 'WYw'),
                ('--owner-account', 'wyw', '--owner-account', 'wyw'),
                ('--owner-account', 'huoguo', '--owner-account', 'huoguo'),
                ('--owner-account', 'wyw', '--owner-account', 'huoguo', '--owner-account', 'wyw'),
                ('--network-scope', 'lan', '--owner-account', 'huoguo')):
            with self.subTest(extra=extra), patch('sys.stderr', io.StringIO()):
                with self.assertRaises(SystemExit): arguments('m1', *extra)

    def test_handler_and_registry_must_agree_on_effective_allowlist(self):
        profile = owner_profile('m1')
        for registered, supplied in ((None, ['huoguo']), (['huoguo'], None),
                (['wyw', 'huoguo'], ['wyw'])):
            with self.subTest(registered=registered, supplied=supplied), self.assertRaises(ValueError):
                handler_for(registry(accounts=registered), Mock(), profile,
                    scope=Scope(), owner_accounts=supplied)
        handler_for(registry(accounts=['wyw', 'huoguo']), Mock(), profile,
                    scope=Scope(), owner_accounts=['huoguo', 'wyw'])


class HttpAndRegistryChecks(unittest.TestCase):
    def setup_policy(self, node='m1', accounts=None):
        self.node, self.candidate = node, registry(node, accounts)
        self.worker, self.factory_calls = Worker(), []
        def factory(config, peer):
            self.factory_calls.append((config, peer)); return self.worker
        self.handler = handler_for(self.candidate, factory,
            owner_profile(node, allow_m5_owner_trial=node == 'm5'),
            busy=lambda: False, scope=Scope(), allow_m5_owner_trial=node == 'm5',
            owner_accounts=accounts)

    def dispatch(self, method='POST', account='wyw', path='/udp/session', *, accepted=True, fields=None):
        body = json.dumps({'node': self.node, 'network_scope': 'nps_owner', **(fields or {})}).encode()
        request = self.handler.__new__(self.handler)
        request.path, request.client_address = path, ('127.0.0.1', 50001)
        request.headers = Message();request.headers.add_header('Content-Length', str(len(body)))
        request.rfile, request.connection = io.BytesIO(body), Mock()
        responses = [];request.auth_calls = 0
        request.reply = lambda status, value: responses.append((status, value))
        def auth():
            request.auth_calls += 1
            if not accepted: request.reply(401, {'error':'mock_existing_password_rejected'}); return False
            request.account = account;return True
        request.auth = auth
        getattr(request, 'do_' + method)()
        self.assertEqual(request.auth_calls, 1)
        self.assertEqual(len(responses), 1)
        return responses[0]

    def test_default_still_rejects_huoguo_before_factory_on_all_signaling_methods(self):
        self.setup_policy()
        for method in ('POST', 'GET', 'DELETE'):
            path = '/udp/session' if method == 'POST' else '/udp/session/'+'a'*32
            self.assertEqual(self.dispatch(method, 'huoguo', path)[0], 403)
        with self.assertRaises(SessionError) as raised:
            self.candidate.create('huoguo', {'node':'m1','network_scope':'nps_owner'}, lambda _: self.worker)
        self.assertEqual(raised.exception.code, 'nps_owner_account_required')
        self.assertIsNone(self.candidate.status('huoguo', 'a'*32))
        self.assertFalse(self.candidate.cancel('huoguo', 'a'*32))
        self.assertEqual(self.factory_calls, [])

    def test_explicit_huoguo_can_POST_GET_and_DELETE_under_normal_auth_on_both_nodes(self):
        for node in ('m1', 'm5'):
            with self.subTest(node=node):
                self.setup_policy(node, ['huoguo'])
                status, descriptor = self.dispatch(account='huoguo');self.assertEqual(status, 201)
                sid = descriptor['session'];route = '/udp/session/'+sid
                self.assertNotIn('owner_accounts', descriptor);self.assertNotIn('account', descriptor)
                self.assertTrue(self.candidate.authenticated_ready(sid))
                self.assertEqual(self.dispatch('GET', 'huoguo', route)[0], 200)
                self.assertEqual(self.dispatch('DELETE', 'huoguo', route)[0], 200)
                self.assertEqual((self.worker.starts, self.worker.stops), (1, 1))
                self.assertEqual(self.dispatch('DELETE', 'huoguo', route)[0], 200)

    def test_two_allowed_accounts_cannot_read_or_cancel_each_others_session(self):
        self.setup_policy(accounts=['wyw', 'huoguo'])
        status, descriptor = self.dispatch(account='huoguo');self.assertEqual(status, 201)
        sid = descriptor['session'];route = '/udp/session/'+sid
        self.assertTrue(self.candidate.authenticated_ready(sid))
        self.assertEqual(self.dispatch('GET', 'wyw', route)[0], 404)
        self.assertEqual(self.dispatch('DELETE', 'wyw', route)[0], 404)
        self.assertIsNone(self.candidate.status('wyw', sid))
        self.assertFalse(self.candidate.cancel('wyw', sid))
        self.assertTrue(self.candidate.touch_allowed(sid));self.assertEqual(self.worker.stops, 0)
        self.assertEqual(self.dispatch('GET', 'huoguo', route)[0], 200)
        self.assertEqual(self.dispatch('DELETE', 'huoguo', route)[0], 200)
        self.assertEqual(self.dispatch('DELETE', 'wyw', route)[0], 404)
        self.assertTrue(self.candidate.cancel('huoguo', sid));self.assertEqual(self.worker.stops, 1)
        status, second = self.dispatch(account='wyw');self.assertEqual(status, 201)
        self.assertIsNone(self.candidate.status('huoguo', second['session']))
        self.assertFalse(self.candidate.cancel('huoguo', second['session']))
        self.assertTrue(self.candidate.cancel('wyw', second['session']))

    def test_allowlisting_never_bypasses_existing_password_auth(self):
        self.setup_policy(accounts=['wyw', 'huoguo'])
        for method in ('POST', 'GET', 'DELETE'):
            path = '/udp/session' if method == 'POST' else '/udp/session/'+'a'*32
            self.assertEqual(self.dispatch(method, 'huoguo', path, accepted=False)[0], 401)
        self.assertEqual(self.factory_calls, [])

    def test_HTTP_settings_cannot_set_accounts_or_maintenance_policy(self):
        self.setup_policy()
        for field in ('owner_account', 'owner_accounts', 'allowed_accounts',
                      'draining', 'maintenance', 'allow_huoguo'):
            self.assertEqual(self.dispatch(fields={field:['huoguo']})[0], 400)
        self.assertEqual(self.factory_calls, [])

    def test_arbitrary_accounts_still_fail_even_with_both_trusted_accounts_enabled(self):
        self.setup_policy(accounts=['wyw', 'huoguo'])
        for account in ('outsider', 'root', 'WYw', 'huoguo ', 'wyw:password'):
            self.assertEqual(self.dispatch(account=account)[0], 403)
        self.assertEqual(self.factory_calls, [])


class MainBindOrderingChecks(unittest.TestCase):
    def test_occupied_private_control_port_exits_before_mediaworker_or_reaper(self):
        for node in ('m1', 'm5'):
            args = arguments(node, '--owner-account', 'wyw', '--owner-account', 'huoguo')
            env = {'DIRECT_AUTH_FILE':'/fake/existing-auth',
                'DIRECT_SERIAL':args.profile.guest_serial, 'DIRECT_AVD':args.profile.guest_avd,
                'DIRECT_EXTERNAL_VM':'1'}
            with self.subTest(node=node), patch('udp_nps_gateway.parse_arguments', return_value=args), \
                 patch.dict(os.environ, env, clear=True), \
                 patch('udp_nps_gateway.trial_busy', return_value=False), \
                 patch('udp_nps_gateway.OwnerNpsScope', return_value=Scope()), \
                 patch('udp_nps_gateway.ssl.SSLContext'), \
                 patch('udp_nps_gateway.LoopbackTlsServer', side_effect=OSError('owned_bind_busy')), \
                 patch('udp_nps_gateway.LanMediaWorker') as worker, \
                 patch('udp_nps_gateway.threading.Thread') as thread, \
                 patch('udp_nps_gateway.signal.signal') as signals:
                with self.assertRaisesRegex(OSError, 'owned_bind_busy'): main()
                worker.assert_not_called();thread.assert_not_called();signals.assert_not_called()

    def test_formal_busy_exit_does_not_start_registry_server_or_worker(self):
        args = arguments()
        env = {'DIRECT_AUTH_FILE':'/fake/existing-auth', 'DIRECT_SERIAL':'emulator-5556',
               'DIRECT_AVD':'RemoteAndroid17Compare', 'DIRECT_EXTERNAL_VM':'1'}
        with patch('udp_nps_gateway.parse_arguments', return_value=args), \
             patch.dict(os.environ, env, clear=True), \
             patch('udp_nps_gateway.trial_busy', return_value=True), \
             patch('udp_nps_gateway.UdpLanSessions') as candidate, \
             patch('udp_nps_gateway.LoopbackTlsServer') as server, \
             patch('udp_nps_gateway.LanMediaWorker') as worker:
            with self.assertRaisesRegex(SystemExit, 'formal_or_other_candidate_busy_skip_trial'): main()
            candidate.assert_not_called();server.assert_not_called();worker.assert_not_called()


if __name__ == '__main__':
    unittest.main()
