"""Offline Tailnet identity, kernel route and read-only LocalAPI boundaries.

These fixture checks do not establish Tailnet media/direct/relay acceptance.
"""
import copy
import json
import subprocess
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from udp_network_scope import (LanScope, LocalTailscaleApi, ScopeUnavailable,
                               TailnetScope, same_private_lan)


def fixture():
    return {
        '/localapi/v0/prefs': {'ControlURL': 'https://hs.yilufa.site', 'WantRunning': True,
                             'ExitNodeID': '', 'ExitNodeIP': ''},
        '/localapi/v0/status': {
            'BackendState': 'Running', 'TUN': True, 'Health': [],
            'TailscaleIPs': ['100.65.0.2', 'fd7a:115c:a1e0::2'],
            'Self': {'ID': '46', 'UserID': 3, 'Online': True,
                     'TailscaleIPs': ['100.65.0.2']},
            'Peer': {'test-node-key': {'ID': '48', 'UserID': 3, 'Online': True,
                     'InNetworkMap': True, 'TailscaleIPs': ['100.65.0.3'],
                     'CurAddr': '', 'Relay': 'headscale', 'RxBytes': 0, 'TxBytes': 0}}},
        '/localapi/v0/whois?addr=100.65.0.3': {
            'Node': {'ID': 48, 'StableID': '48', 'User': 3,
                     'Addresses': ['100.65.0.3/32', 'fd7a:115c:a1e0::3/128']},
            'UserProfile': {'ID': 3, 'LoginName': 'wyw'}},
    }


class ScopeChecks(unittest.TestCase):
    def setUp(self):
        self.responses = fixture()
        self.api = Mock()
        self.api.get.side_effect = lambda path: copy.deepcopy(self.responses[path])
        self.interface = 'utun0: flags=8051<UP,POINTOPOINT,RUNNING> mtu 1280\n\tinet 100.65.0.2 --> 100.65.0.2 netmask 0xffffffff\n'
        self.route = 'route to: 100.65.0.3\n  interface: utun0\n'
        self.commands = []
        def run(command, **kwargs):
            self.commands.append((command, kwargs))
            return SimpleNamespace(stdout=self.interface if command[0].endswith('ifconfig') else self.route)
        self.run = run

    def scope(self):
        return TailnetScope('100.65.0.2', 'utun0', self.api, self.run)

    def test_exact_registered_identity_with_kernel_route_is_required(self):
        scope = self.scope()
        self.assertTrue(scope.healthy())
        self.assertTrue(scope.permits_peer('100.65.0.3'))
        for peer in ('100.65.0.4', '100.65.0.11', '100.64.0.3', '192.168.9.149', '::1'):
            self.assertFalse(scope.permits_peer(peer))
        self.assertEqual(self.api.get.call_args_list[2].args,
                         ('/localapi/v0/whois?addr=100.65.0.3',))
        self.assertEqual(self.commands, [
            (['/sbin/ifconfig', 'utun0'], dict(check=True, capture_output=True, text=True, timeout=2)),
            (['/sbin/route', '-n', 'get', '100.65.0.3'], dict(check=True, capture_output=True, text=True, timeout=2))])
        # No CurAddr is needed for identity admission, and a home DERP field
        # must not be mislabeled as a measured direct or active relay path.
        self.assertTrue(scope.verify())

    def test_other_host_or_physical_interface_rejected_before_localapi(self):
        for host, interface in (('100.65.0.11', 'utun0'), ('100.64.0.2', 'utun0'),
                                ('100.65.0.2', 'en7'), ('100.65.0.2', 'utun0;id')):
            with self.subTest(host=host, interface=interface), self.assertRaises(ValueError):
                TailnetScope(host, interface, self.api, self.run)
        self.api.get.assert_not_called()
        self.assertEqual(self.commands, [])

    def test_profile_self_peer_or_whois_drift_fails_closed(self):
        mutations = [
            ('prefs', lambda d: d.update(ControlURL='https://different.invalid')),
            ('prefs', lambda d: d.update(WantRunning=False)),
            ('prefs', lambda d: d.update(ExitNodeID='different-node')),
            ('status', lambda d: d.update(TUN=False)),
            ('status', lambda d: d.update(BackendState='NeedsLogin')),
            ('status', lambda d: d.update(Health=['fixture-health-error'])),
            ('status', lambda d: d.update(TailscaleIPs=['100.65.0.99'])),
            ('status', lambda d: d['Self'].update(ID='47')),
            ('status', lambda d: d['Self'].update(UserID=4)),
            ('status', lambda d: d['Self'].update(Online=False)),
            ('status', lambda d: d['Peer']['test-node-key'].update(ID='49')),
            ('status', lambda d: d['Peer']['test-node-key'].update(UserID=4)),
            ('status', lambda d: d['Peer']['test-node-key'].update(Online=False)),
            ('status', lambda d: d['Peer']['test-node-key'].update(InNetworkMap=False)),
            ('status', lambda d: d['Peer']['test-node-key'].update(Expired=True)),
            ('status', lambda d: d['Peer'].update(duplicate=d['Peer']['test-node-key'])),
            ('whois?addr=100.65.0.3', lambda d: d['Node'].update(ID=49)),
            ('whois?addr=100.65.0.3', lambda d: d['Node'].update(StableID='49')),
            ('whois?addr=100.65.0.3', lambda d: d['Node'].update(User=4)),
            ('whois?addr=100.65.0.3', lambda d: d['Node'].update(Addresses=['100.65.0.0/24'])),
            ('whois?addr=100.65.0.3', lambda d: d['UserProfile'].update(ID=4)),
            ('whois?addr=100.65.0.3', lambda d: d['UserProfile'].update(LoginName='someoneelse')),
        ]
        for name, mutate in mutations:
            with self.subTest(field=name):
                self.responses = fixture()
                mutate(self.responses['/localapi/v0/' + name])
                with self.assertRaises(ScopeUnavailable):
                    self.scope()

    def test_route_address_up_state_and_mtu_changes_revoke_stickily(self):
        for part, value in (
            ('route', '  interface: en7\n'),
            ('interface', self.interface.replace('100.65.0.2', '100.65.0.9')),
            ('interface', self.interface.replace('UP,', '')),
            ('interface', self.interface.replace('1280', '1200'))):
            with self.subTest(part=part, value=value):
                original = getattr(self, part)
                scope = self.scope()
                setattr(self, part, value)
                with self.assertRaises(ScopeUnavailable):
                    scope.verify()
                self.assertFalse(scope.healthy())
                setattr(self, part, original)
                calls = self.api.get.call_count
                with self.assertRaises(ScopeUnavailable):
                    scope.verify()
                self.assertEqual(self.api.get.call_count, calls)

    def test_localapi_and_command_failures_do_not_expose_response_or_commands(self):
        self.api.get.side_effect = OSError('fixture private response')
        with self.assertRaises(ScopeUnavailable) as error:
            self.scope()
        self.assertNotIn('private response', str(error.exception))
        self.api.get.side_effect = lambda path: copy.deepcopy(self.responses[path])
        with self.assertRaises(ScopeUnavailable) as error:
            TailnetScope('100.65.0.2', 'utun0', self.api,
                         Mock(side_effect=subprocess.TimeoutExpired('fixture secret argv', 2)))
        self.assertNotIn('secret argv', str(error.exception))

    def test_concurrent_verifier_cannot_undo_failed_scope_with_late_success(self):
        scope = self.scope()
        entered, release = threading.Event(), threading.Event()
        results = []
        baseline_calls = self.api.get.call_count
        def delayed_bad_profile(path):
            entered.set()
            if not release.wait(1):
                raise TimeoutError('fixture verification wait')
            bad = copy.deepcopy(self.responses[path])
            bad['ControlURL'] = 'https://different.invalid'
            return bad
        self.api.get.side_effect = delayed_bad_profile
        def verify():
            try:
                scope.verify()
                results.append('unexpected-success')
            except ScopeUnavailable:
                results.append('revoked')
        first, second = threading.Thread(target=verify), threading.Thread(target=verify)
        first.start()
        try:
            self.assertTrue(entered.wait(1))
            second.start()
        finally:
            release.set()
            first.join(1)
            if second.ident is not None:
                second.join(1)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(results, ['revoked', 'revoked'])
        self.assertFalse(scope.healthy())
        self.assertEqual(self.api.get.call_count, baseline_calls + 1)

    def test_lan_never_admits_cgn_or_testnet_as_private(self):
        self.assertTrue(LanScope('192.168.9.128').permits_peer('192.168.9.149'))
        for host in ('100.65.0.2', '100.64.0.2', '192.0.2.4', '198.51.100.4'):
            with self.subTest(host=host), self.assertRaises(ValueError):
                LanScope(host)
            self.assertFalse(same_private_lan(host, host))


class LocalApiChecks(unittest.TestCase):
    @patch('udp_network_scope._UnixHttpConnection')
    def test_get_only_exact_socket_routes_are_bounded_and_closed(self, constructor):
        response = Mock(status=200)
        response.read.return_value = b'{"BackendState":"Running"}'
        connection = constructor.return_value
        connection.getresponse.return_value = response
        api = LocalTailscaleApi()
        self.assertEqual(api.get('/localapi/v0/status'), {'BackendState': 'Running'})
        constructor.assert_called_once_with('/var/run/tailscaled.socket')
        connection.request.assert_called_once_with('GET', '/localapi/v0/status')
        response.read.assert_called_once_with(262145)
        connection.close.assert_called_once_with()
        for path in ('/localapi/v0/logout', '/localapi/v0/whois?addr=100.65.0.4',
                     'https://outside.invalid/status'):
            with self.assertRaises(ScopeUnavailable):
                api.get(path)
        self.assertEqual(constructor.call_count, 1)

    @patch('udp_network_scope._UnixHttpConnection')
    def test_bad_status_oversize_schema_and_timeout_fail_safely(self, constructor):
        connection = constructor.return_value
        for status, body in ((403, b'fixture secret'), (200, b'x' * 262145),
                             (200, b'[]'), (200, b'not-json')):
            with self.subTest(status=status, size=len(body)):
                connection.getresponse.return_value = Mock(status=status)
                connection.getresponse.return_value.read.return_value = body
                with self.assertRaises(ScopeUnavailable) as error:
                    LocalTailscaleApi().get('/localapi/v0/status')
                self.assertNotIn('fixture secret', str(error.exception))
        connection.request.side_effect = TimeoutError('fixture private timeout')
        with self.assertRaises(ScopeUnavailable) as error:
            LocalTailscaleApi().get('/localapi/v0/status')
        self.assertNotIn('private timeout', str(error.exception))
        self.assertEqual(connection.close.call_count, 5)


if __name__ == '__main__':
    unittest.main()
