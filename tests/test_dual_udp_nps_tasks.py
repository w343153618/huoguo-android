"""Owned, inert fixtures for the dual UDP NPS task helper; no live requests."""
import copy
from contextlib import nullcontext
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('dual_udp_nps_helper', ROOT / 'scripts/deployment/create_dual_udp_nps_tasks.py')
HELPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)


def fixture():
    clients, tasks = {}, {}
    for client_id, task_id, port, _ in HELPER.SCOPES:
        client = {'Id': client_id, 'Status': True, 'VerifyKey': 'offline-only-identity-%d' % client_id,
                  'Cnf': {'U': '', 'P': '', 'Compress': False, 'Crypt': False},
                  'MaxTunnelNum': 0, 'ConfigConnAllow': False, 'Remark': 'Original owner',
                  'Flow': {'ExportFlow': 0, 'InletFlow': 0, 'FlowLimit': 0, 'TimeLimit': '0001-01-01T00:00:00Z'}}
        clients[client_id] = client
        tasks[task_id] = {'Id': task_id, 'Mode': 'tcp', 'Port': port,
                          'Client': copy.deepcopy(client), 'Remark': 'Original formal task',
                          'Status': True, 'ServerIp': '0.0.0.0',
                          'Target': {'TargetStr': '127.0.0.1:15556', 'LocalProxy': False, 'ProxyProtocol': 0},
                          'Flow': copy.deepcopy(client['Flow'])}
    # The unrelated maintenance client is part of the original-online guard.
    clients[999] = dict(copy.deepcopy(clients[1466]), Id=999, VerifyKey='offline-maintenance-identity')
    snapshot = {'clients': clients, 'tasks': tasks,
                'runtime_clients': copy.deepcopy(clients), 'runtime_tasks': copy.deepcopy(tasks),
                'process': {'pid': 3412973, 'start_ticks': 12},
                'geo': {'geo_in': {'rules_sha256': 'in', 'references_sha256': 'in-ref'},
                        'geo_fwd': {'rules_sha256': 'fwd', 'references_sha256': 'fwd-ref'}},
                'online_ids': frozenset(clients), 'listeners': {15556: frozenset(), 15558: frozenset()},
                'conf_hashes': {'nps.conf': 'offline-config-hash', 'nested/other.conf': 'offline-other-hash'}}
    for client in snapshot['runtime_clients'].values():
        client.update(Mode='quic,quic', IsConnect=True, Version='0.34.7')
    for task in snapshot['runtime_tasks'].values():
        task['RunStatus'] = True
    return snapshot


def install_udp(snapshot, scope, task_id):
    client_id, _, port, remark = scope
    row = {'Id': task_id, 'Mode': 'udp', 'Port': port, 'Client': copy.deepcopy(snapshot['clients'][client_id]),
           'Remark': remark, 'Status': True, 'ServerIp': '0.0.0.0',
           'Target': {'TargetStr': HELPER.TARGET, 'LocalProxy': False, 'ProxyProtocol': 0},
           'DestAclMode': 0, 'DestAclRules': '', 'Password': '', 'TargetType': '',
           'UserAuth': {'Content': '', 'AccountMap': {}}, 'Flow': {'FlowLimit': 0}}
    snapshot['tasks'][task_id] = row
    snapshot['runtime_tasks'][task_id] = dict(copy.deepcopy(row), RunStatus=True)
    snapshot['listeners'][port] = frozenset((snapshot['process']['pid'],))
    return row


class FakeAdmin:
    def __init__(self, snapshot, response=None):
        self.snapshot, self.response, self.calls = snapshot, response, []

    def json(self, endpoint, fields):
        self.calls.append((endpoint, copy.deepcopy(fields)))
        if endpoint != '/index/add':
            raise AssertionError('Unexpected mutation endpoint')
        if self.response is not None:
            if isinstance(self.response, Exception):
                raise self.response
            return copy.deepcopy(self.response)
        matching = [scope for scope in HELPER.SCOPES if HELPER.add_fields(scope) == fields]
        if len(matching) != 1:
            raise AssertionError('Unscoped mutation')
        task_id = max(self.snapshot['tasks']) + 1
        install_udp(self.snapshot, matching[0], task_id)
        return {'status': 1, 'id': task_id}


class DualUdpScope(unittest.TestCase):
    def setUp(self):
        self.snapshot = fixture()

    def test_same_numeric_tcp_ports_are_allowed_without_udp_tasks(self):
        result = HELPER.plan(self.snapshot)
        self.assertEqual([item['scope'][2] for item in result], [15556, 15558])
        self.assertEqual([item['task'] for item in result], [None, None])

    def test_exact_existing_udp_pair_is_idempotent(self):
        install_udp(self.snapshot, HELPER.SCOPES[0], 2000)
        install_udp(self.snapshot, HELPER.SCOPES[1], 2001)
        admin = FakeAdmin(self.snapshot)
        with patch.object(HELPER, 'capture', side_effect=lambda _: copy.deepcopy(self.snapshot)), \
                patch.object(HELPER, 'backup') as backup, patch.object(HELPER, 'creation_lock') as lock:
            first = HELPER.execute(admin, create=True)
            second = HELPER.execute(admin, create=True)
        self.assertEqual(first, second)
        self.assertEqual([task['udp_task_id'] for task in first['tasks']], [2000, 2001])
        self.assertEqual(admin.calls, [])
        backup.assert_not_called()
        lock.assert_not_called()

    def test_old_tcp_task_wrong_owner_or_port_cannot_authorize_write(self):
        for source in ('tasks', 'runtime_tasks'):
            for change in ({'Client': {'Id': 999}}, {'Port': 15559}, {'Mode': 'udp'}, {'Status': False}):
                with self.subTest(source=source, change=change):
                    snapshot = fixture()
                    snapshot[source][1409].update(change)
                    with self.assertRaisesRegex(HELPER.Refuse, 'formal_tcp_task_ownership'):
                        HELPER.plan(snapshot)

    def test_actual_formal_run_status_required(self):
        self.snapshot['runtime_tasks'][1411]['RunStatus'] = False
        with self.assertRaisesRegex(HELPER.Refuse, 'formal_tcp_task_not_running'):
            HELPER.plan(self.snapshot)

    def test_runtime_online_quic_required_not_database_mode(self):
        for value in ('tcp', 'quicish', 'kcp', 'tcp,quic', 'quic,tcp', 'quic,', ',quic', None):
            with self.subTest(mode=value):
                snapshot = fixture()
                snapshot['runtime_clients'][1466]['Mode'] = value
                with self.assertRaisesRegex(HELPER.Refuse, 'not_online_quic'):
                    HELPER.plan(snapshot)
        self.snapshot['runtime_clients'][1468]['IsConnect'] = False
        with self.assertRaisesRegex(HELPER.Refuse, 'not_online_quic'):
            HELPER.plan(self.snapshot)

    def test_m1_m5_credentials_cannot_share_one_identity(self):
        key = self.snapshot['clients'][1466]['VerifyKey']
        self.snapshot['clients'][1468]['VerifyKey'] = key
        self.snapshot['runtime_clients'][1468]['VerifyKey'] = key
        with self.assertRaisesRegex(HELPER.Refuse, 'not_independent'):
            HELPER.plan(self.snapshot)

    def test_udp_other_owner_same_numeric_port_conflicts(self):
        row = install_udp(self.snapshot, HELPER.SCOPES[0], 2000)
        row['Client'] = {'Id': 999}
        with self.assertRaisesRegex(HELPER.Refuse, 'udp_task_conflict'):
            HELPER.plan(self.snapshot)

    def test_reserved_remark_on_another_port_conflicts(self):
        row = install_udp(self.snapshot, HELPER.SCOPES[0], 2000)
        row['Port'] = 45556
        with self.assertRaisesRegex(HELPER.Refuse, 'udp_task_conflict'):
            HELPER.plan(self.snapshot)

    def test_duplicate_udp_or_reserved_name_conflicts(self):
        install_udp(self.snapshot, HELPER.SCOPES[0], 2000)
        self.snapshot['tasks'][2001] = dict(self.snapshot['tasks'][2000], Id=2001)
        with self.assertRaisesRegex(HELPER.Refuse, 'udp_task_conflict'):
            HELPER.plan(self.snapshot)

    def test_wrong_udp_target_local_proxy_or_auth_does_not_resume(self):
        for mutation in ('target', 'local', 'proxy', 'acl', 'password', 'flow', 'auth'):
            with self.subTest(mutation=mutation):
                snapshot = fixture()
                row = install_udp(snapshot, HELPER.SCOPES[0], 2000)
                if mutation == 'target':
                    row['Target']['TargetStr'] = '127.0.0.1:15556'
                elif mutation == 'local':
                    row['Target']['LocalProxy'] = True
                elif mutation == 'proxy':
                    row['Target']['ProxyProtocol'] = 1
                elif mutation == 'acl':
                    row['DestAclMode'] = 1
                elif mutation == 'password':
                    row['Password'] = 'offline-unwanted-password'
                elif mutation == 'flow':
                    row['Flow']['FlowLimit'] = 1
                else:
                    row['UserAuth']['Content'] = 'offline-unwanted-auth'
                with self.assertRaisesRegex(HELPER.Refuse, 'udp_task_conflict'):
                    HELPER.plan(snapshot)

    def test_nil_account_map_is_exact_empty_auth_but_expiry_is_not(self):
        row = install_udp(self.snapshot, HELPER.SCOPES[0], 2000)
        row['UserAuth']['AccountMap'] = None
        self.snapshot['runtime_tasks'][2000]['UserAuth']['AccountMap'] = None
        self.assertEqual(HELPER.plan(self.snapshot)[0]['task']['Id'], 2000)
        row['Flow']['TimeLimit'] = '2026-12-01T00:00:00Z'
        with self.assertRaisesRegex(HELPER.Refuse, 'udp_task_conflict'):
            HELPER.plan(self.snapshot)

    def test_udp_must_have_live_task_and_exact_formal_listener_owner(self):
        install_udp(self.snapshot, HELPER.SCOPES[0], 2000)
        self.snapshot['listeners'][15556] = frozenset((99,))
        with self.assertRaisesRegex(HELPER.Refuse, 'listener_not_formal'):
            HELPER.plan(self.snapshot)
        self.snapshot['listeners'][15556] = frozenset((3412973,))
        self.snapshot['runtime_tasks'][2000]['RunStatus'] = False
        with self.assertRaisesRegex(HELPER.Refuse, 'owned_udp_task_not_running'):
            HELPER.plan(self.snapshot)

    def test_unowned_udp_listener_is_refused_but_tcp_not_queried(self):
        self.snapshot['listeners'][15558] = frozenset((123,))
        with self.assertRaisesRegex(HELPER.Refuse, 'udp_port'):
            HELPER.plan(self.snapshot)


class DualUdpExecution(unittest.TestCase):
    def setUp(self):
        self.snapshot = fixture()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.destination = Path(self.temp.name)

    def run_helper(self, admin, create=True):
        with patch.object(HELPER, 'capture', side_effect=lambda _: copy.deepcopy(self.snapshot)), \
                patch.object(HELPER, 'creation_lock', return_value=nullcontext()), \
                patch.object(HELPER, 'backup', return_value=(self.destination, 6)) as backup, \
                patch.object(HELPER, 'private_write') as write:
            result = HELPER.execute(admin, create=create)
        return result, backup, write

    def test_default_readonly_never_calls_mutation_or_backup(self):
        admin = FakeAdmin(self.snapshot)
        result, backup, write = self.run_helper(admin, create=False)
        self.assertTrue(result['read_only'])
        self.assertEqual(admin.calls, [])
        backup.assert_not_called()
        write.assert_not_called()

    def test_create_backs_up_before_api_and_preserves_all_original_config(self):
        before = copy.deepcopy(self.snapshot)
        admin = FakeAdmin(self.snapshot)
        timeline = []
        original = admin.json
        def api(*args):
            timeline.append('api')
            return original(*args)
        admin.json = api
        with patch.object(HELPER, 'capture', side_effect=lambda _: copy.deepcopy(self.snapshot)), \
                patch.object(HELPER, 'creation_lock', return_value=nullcontext()), \
                patch.object(HELPER, 'backup', side_effect=lambda _: (timeline.append('backup') or self.destination, 6)), \
                patch.object(HELPER, 'private_write'):
            result = HELPER.execute(admin, create=True)
        self.assertEqual(timeline, ['backup', 'api', 'api'])
        self.assertEqual(self.snapshot['clients'], before['clients'])
        self.assertEqual(self.snapshot['tasks'][1409], before['tasks'][1409])
        self.assertEqual(self.snapshot['tasks'][1411], before['tasks'][1411])
        self.assertEqual([fields['type'] for _, fields in admin.calls], ['udp', 'udp'])
        self.assertEqual([fields['client_id'] for _, fields in admin.calls], [1466, 1468])
        self.assertEqual(len(result['created_task_ids']), 2)
        self.assertTrue(result['preservation_verified'])
        public = json.dumps(result)
        for row in before['clients'].values():
            self.assertNotIn(row['VerifyKey'], public)
        self.assertNotIn('rules_sha256', public)
        self.assertNotIn('conf_hash', public)

    def test_partial_exact_pair_adds_only_missing_task(self):
        install_udp(self.snapshot, HELPER.SCOPES[0], 2000)
        self.snapshot['clients'][1468]['Cnf']['Compress'] = True
        self.snapshot['runtime_clients'][1468]['Cnf']['Compress'] = True
        before = copy.deepcopy(self.snapshot['clients'])
        admin = FakeAdmin(self.snapshot)
        result, _, _ = self.run_helper(admin)
        self.assertEqual(len(admin.calls), 1)
        self.assertEqual(admin.calls[0][1]['client_id'], 1468)
        self.assertEqual(self.snapshot['clients'], before)
        self.assertEqual(len(result['created_task_ids']), 1)

    def test_client_max_tunnel_one_fails_closed_without_changing_limit(self):
        self.snapshot['clients'][1466]['MaxTunnelNum'] = 1
        self.snapshot['runtime_clients'][1466]['MaxTunnelNum'] = 1
        admin = FakeAdmin(self.snapshot)
        with self.assertRaisesRegex(HELPER.Refuse, 'max_tunnel_limit_rejected'):
            self.run_helper(admin)
        self.assertEqual(admin.calls, [])
        self.assertEqual(self.snapshot['clients'][1466]['MaxTunnelNum'], 1)

    def test_api_limit_rejection_is_explicit_and_not_retried(self):
        admin = FakeAdmin(self.snapshot, {'status': 0, 'msg': 'The number of tunnels exceeds the limit'})
        with self.assertRaisesRegex(HELPER.Refuse, 'max_tunnel_limit_rejected'):
            self.run_helper(admin)
        self.assertEqual(len(admin.calls), 1)

    def test_unknown_api_result_is_not_retried_or_rolled_back(self):
        for response in (OSError('offline message'), {'status': 0, 'msg': 'offline untrusted secret'},
                         {'status': True, 'id': 2000}, {'status': 1, 'id': True}):
            with self.subTest(response=type(response).__name__):
                admin = FakeAdmin(fixture(), response)
                with self.assertRaises(HELPER.Refuse):
                    self.run_helper(admin)
                self.assertEqual(len(admin.calls), 1)
                self.assertEqual(admin.calls[0][0], '/index/add')

    def test_natural_unrelated_disconnect_after_backup_blocks_first_api(self):
        admin = FakeAdmin(self.snapshot)
        def backup(_):
            self.snapshot['online_ids'] = frozenset((1466, 1468))
            return self.destination, 6
        with patch.object(HELPER, 'capture', side_effect=lambda _: copy.deepcopy(self.snapshot)), \
                patch.object(HELPER, 'creation_lock', return_value=nullcontext()), patch.object(HELPER, 'backup', side_effect=backup):
            with self.assertRaisesRegex(HELPER.Refuse, 'original_online_client_missing'):
                HELPER.execute(admin, create=True)
        self.assertEqual(admin.calls, [])

    def test_created_task_start_timeout_retains_id_and_backup_without_retry(self):
        admin = FakeAdmin(self.snapshot)
        with patch.object(HELPER, 'capture', side_effect=lambda _: copy.deepcopy(self.snapshot)), \
                patch.object(HELPER, 'creation_lock', return_value=nullcontext()), \
                patch.object(HELPER, 'backup', return_value=(self.destination, 6)), \
                patch.object(HELPER, 'await_created_ready', side_effect=HELPER.Refuse('new_udp_task_start_outcome_unknown_reinspect_required')):
            with self.assertRaises(HELPER.Refuse) as raised:
                HELPER.execute(admin, create=True)
        self.assertEqual(len(admin.calls), 1)
        self.assertEqual(raised.exception.created_task_ids, (1412,))
        self.assertEqual(raised.exception.backup_path, str(self.destination))
        self.assertIn(1412, self.snapshot['tasks'])


class DualUdpPreservation(unittest.TestCase):
    def setUp(self):
        self.before = fixture()

    def test_live_counter_growth_is_not_configuration_drift(self):
        after = copy.deepcopy(self.before)
        for source in ('clients', 'runtime_clients'):
            after[source][1466]['Flow']['InletFlow'] += 500
            after[source][1466]['NowConn'] = 7
        for source in ('tasks', 'runtime_tasks'):
            after[source][1409]['Flow']['ExportFlow'] += 250
            after[source][1409]['Client']['IsConnect'] = False
            after[source][1409]['Target']['TargetArr'] = ['127.0.0.1:15556']
        HELPER.preserved(self.before, after)

    def test_vkey_compression_limit_and_unknown_client_config_are_protected(self):
        for change in ('key', 'compress', 'limit', 'newunknown'):
            with self.subTest(change=change):
                after = copy.deepcopy(self.before)
                if change == 'key':
                    after['clients'][999]['VerifyKey'] = 'offline-drift'
                elif change == 'compress':
                    after['clients'][999]['Cnf']['Compress'] = True
                elif change == 'limit':
                    after['clients'][999]['Flow']['FlowLimit'] = 4
                else:
                    after['clients'][999]['UnexpectedConfig'] = True
                with self.assertRaisesRegex(HELPER.Refuse, 'original_client_or_task_config_changed'):
                    HELPER.preserved(self.before, after)

    def test_original_tasks_pid_starttime_geo_and_conf_are_protected(self):
        for change, reason in (('task', 'original_client_or_task_config_changed'),
                               ('pid', 'process_changed'), ('start', 'process_changed'),
                               ('geo', 'geo_reference_changed'), ('conf', 'conf_file_changed')):
            with self.subTest(change=change):
                after = copy.deepcopy(self.before)
                if change == 'task':
                    after['tasks'][1411]['Target']['LocalProxy'] = True
                elif change == 'pid':
                    after['process']['pid'] += 1
                elif change == 'start':
                    after['process']['start_ticks'] += 1
                elif change == 'geo':
                    after['geo']['geo_fwd']['references_sha256'] = 'changed'
                else:
                    after['conf_hashes']['nested/other.conf'] = 'changed'
                with self.assertRaisesRegex(HELPER.Refuse, reason):
                    HELPER.preserved(self.before, after)

    def test_foreign_task_appearing_is_refused(self):
        after = copy.deepcopy(self.before)
        after['tasks'][2000] = dict(after['tasks'][1409], Id=2000, Port=18000)
        with self.assertRaisesRegex(HELPER.Refuse, 'original_client_or_task_config_changed'):
            HELPER.preserved(self.before, after)


class DualUdpAsynchronousBind(unittest.TestCase):
    def setUp(self):
        self.before = fixture()
        self.starting = copy.deepcopy(self.before)
        install_udp(self.starting, HELPER.SCOPES[0], 2000)
        self.starting['runtime_tasks'][2000]['RunStatus'] = False
        self.starting['listeners'][15556] = frozenset()
        self.ready = copy.deepcopy(self.starting)
        self.ready['runtime_tasks'][2000]['RunStatus'] = True
        self.ready['listeners'][15556] = frozenset((3412973,))

    def test_one_not_running_then_running_is_read_only_wait_not_duplicate_add(self):
        admin = FakeAdmin(self.starting)
        with patch.object(HELPER, 'capture', side_effect=[self.starting, self.ready]) as capture, \
                patch.object(HELPER.time, 'sleep') as sleep:
            result = HELPER.await_created_ready(admin, self.before, [2000], 2000)
        self.assertIs(result, self.ready)
        self.assertEqual(capture.call_count, 2)
        sleep.assert_called_once()
        self.assertEqual(admin.calls, [])

    def test_existing_udp_false_is_not_allowed_even_while_new_task_starts(self):
        before = fixture()
        install_udp(before, HELPER.SCOPES[1], 2001)
        after = copy.deepcopy(before)
        install_udp(after, HELPER.SCOPES[0], 2000)
        after['runtime_tasks'][2001]['RunStatus'] = False
        with patch.object(HELPER, 'capture', return_value=after):
            with self.assertRaisesRegex(HELPER.Refuse, 'owned_udp_task_not_running'):
                HELPER.await_created_ready(FakeAdmin(after), before, [2000], 2000)

    def test_wait_does_not_tolerate_new_task_target_drift_or_foreign_listener(self):
        for change in ('target', 'listener', 'pid', 'geo', 'online'):
            with self.subTest(change=change):
                snapshot = copy.deepcopy(self.starting)
                if change == 'target':
                    snapshot['tasks'][2000]['Target']['TargetStr'] = '127.0.0.1:1234'
                elif change == 'listener':
                    snapshot['listeners'][15556] = frozenset((999,))
                elif change == 'pid':
                    snapshot['process']['pid'] += 1
                elif change == 'geo':
                    snapshot['geo']['geo_in']['rules_sha256'] = 'changed'
                else:
                    snapshot['online_ids'] = frozenset((1466, 1468))
                with patch.object(HELPER, 'capture', return_value=snapshot), patch.object(HELPER.time, 'sleep') as sleep:
                    with self.assertRaises(HELPER.Refuse):
                        HELPER.await_created_ready(FakeAdmin(snapshot), self.before, [2000], 2000)
                    sleep.assert_not_called()

    def test_permanent_false_times_out_and_does_not_write(self):
        now = [0.0]
        def advance(delay):
            now[0] += delay
        admin = FakeAdmin(self.starting)
        with patch.object(HELPER, 'capture', return_value=self.starting), \
                patch.object(HELPER.time, 'monotonic', side_effect=lambda: now[0]), \
                patch.object(HELPER.time, 'sleep', side_effect=advance):
            with self.assertRaisesRegex(HELPER.Refuse, 'start_outcome_unknown'):
                HELPER.await_created_ready(admin, self.before, [2000], 2000)
        self.assertAlmostEqual(now[0], 5.0)
        self.assertEqual(admin.calls, [])

    def test_subprocess_and_admin_socket_timeout_clip_to_remaining_budget(self):
        with patch.object(HELPER.time, 'monotonic', return_value=4.75), HELPER.inspection_budget(5.0), \
                patch.object(HELPER.subprocess, 'run', return_value=types.SimpleNamespace(stdout='active')) as run:
            HELPER.command(['systemctl', 'is-active', 'nps.service'])
            self.assertEqual(run.call_args.kwargs['timeout'], 0.25)
            original = types.SimpleNamespace(open=lambda request, timeout: timeout)
            self.assertEqual(HELPER._DeadlineOpener(original).open('offline', timeout=15), 0.25)

    def test_readback_exception_is_fixed_safe_reinspect_error(self):
        with patch.object(HELPER, 'capture', side_effect=TimeoutError('offline untrusted message')):
            with self.assertRaisesRegex(HELPER.Refuse, '^new_udp_task_readback_failed_reinspect_required$'):
                HELPER.await_created_ready(FakeAdmin(self.starting), self.before, [2000], 2000)


class DualUdpBoundaries(unittest.TestCase):
    def test_host_must_be_linux_real_and_effective_root(self):
        for platform, uid, euid in (('darwin', 0, 0), ('linux', 501, 0), ('linux', 0, 501)):
            with self.subTest(platform=platform, uid=uid, euid=euid), \
                    patch.object(HELPER.sys, 'platform', platform), \
                    patch.object(HELPER.os, 'getuid', return_value=uid), patch.object(HELPER.os, 'geteuid', return_value=euid):
                with self.assertRaisesRegex(HELPER.Refuse, 'linux_cloud_root_required'):
                    HELPER.require_host()

    def test_runtime_table_duplicate_or_incomplete_is_refused(self):
        for response in ({'total': 2, 'rows': [{'Id': 1}, {'Id': 1}]},
                         {'total': 1, 'rows': []}, {'total': True, 'rows': []}):
            admin = types.SimpleNamespace(json=lambda *_: response)
            with self.subTest(response=response), self.assertRaises(HELPER.Refuse):
                HELPER.runtime_rows(admin, '/client/list')

    def test_runtime_pagination_cannot_change_total(self):
        responses = iter(({'total': 2, 'rows': [{'Id': 1}]}, {'total': 3, 'rows': [{'Id': 2}, {'Id': 3}]}))
        admin = types.SimpleNamespace(json=lambda *_: next(responses))
        with self.assertRaisesRegex(HELPER.Refuse, 'changed_during_read'):
            HELPER.runtime_rows(admin, '/client/list')

    def test_capture_queries_each_persisted_mode_including_udp_not_empty_all(self):
        snapshot = fixture()
        snapshot['tasks'][2010] = dict(copy.deepcopy(snapshot['tasks'][1409]), Id=2010, Mode='mixProxy', Port=25000)
        calls = []
        def rows(admin, endpoint, fields=None):
            calls.append((endpoint, fields))
            if endpoint == '/client/list':
                return list(snapshot['runtime_clients'].values())
            self.assertIn(fields['type'], ('tcp', 'mixProxy', 'udp'))
            return [dict(copy.deepcopy(row), RunStatus=True) for row in snapshot['tasks'].values() if row['Mode'] == fields['type']]
        with patch.object(HELPER, 'conf_files', return_value=[HELPER.CLIENTS, HELPER.TASKS]), \
                patch.object(HELPER, 'records', side_effect=lambda path: list(snapshot['clients' if path == HELPER.CLIENTS else 'tasks'].values())), \
                patch.object(HELPER, 'runtime_rows', side_effect=rows), \
                patch.object(HELPER, 'process_identity', return_value=snapshot['process']), \
                patch.object(HELPER, 'geo_reference', return_value=snapshot['geo']), \
                patch.object(HELPER, 'udp_listeners', return_value=snapshot['listeners']):
            result = HELPER.capture(object())
        self.assertEqual(set(result['runtime_tasks']), set(snapshot['tasks']))
        self.assertEqual({fields['type'] for endpoint, fields in calls if endpoint == '/index/gettunnel'}, {'tcp', 'mixProxy', 'udp'})

    def test_geo_reference_includes_rules_and_jump_references(self):
        rules = '-P INPUT ACCEPT\n-N geo_in\n-N geo_fwd\n-A INPUT -j geo_in\n-A FORWARD -g geo_fwd\n-A geo_in -m set --match-set cn4 src -j RETURN\n-A geo_fwd -j DROP\n'
        with patch.object(HELPER, 'command', return_value=rules):
            reference = HELPER.geo_reference()
        self.assertIn('-A INPUT -j geo_in', reference['geo_in']['references'])
        self.assertIn('-A FORWARD -g geo_fwd', reference['geo_fwd']['references'])
        self.assertNotEqual(reference['geo_in']['rules_sha256'], reference['geo_fwd']['rules_sha256'])
        with patch.object(HELPER, 'command', return_value='-N geo_in\n-N geo_fwd\n'):
            with self.assertRaisesRegex(HELPER.Refuse, 'reference_missing'):
                HELPER.geo_reference()

    def test_listener_commands_are_udp_only_and_unidentified_owner_refused(self):
        calls = []
        def run(args):
            calls.append(args)
            return ''
        with patch.object(HELPER, 'command', side_effect=run):
            self.assertEqual(HELPER.udp_listeners(), {15556: frozenset(), 15558: frozenset()})
        self.assertTrue(all('-u' in args and '-t' not in args for args in calls))
        with patch.object(HELPER, 'command', return_value='UNCONN 0 0 0.0.0.0:15556 0.0.0.0:*\n'):
            with self.assertRaisesRegex(HELPER.Refuse, 'ownership_unavailable'):
                HELPER.udp_listeners()

    def test_backup_copies_all_nested_regular_files_and_verifies_private_modes(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            conf, backups = base / 'conf', base / 'backups'
            conf.mkdir()
            (conf / 'nested').mkdir()
            contents = {'nps.conf': b'offline-admin-config', 'clients.json': b'[]', 'tasks.json': b'[]',
                        'hosts.json': b'[]', 'nested/custom.conf': b'custom-only'}
            for name, data in contents.items():
                (conf / name).write_bytes(data)
            def private_dir(path):
                path.mkdir(mode=0o700, parents=True, exist_ok=True)
                path.chmod(0o700)
            with patch.object(HELPER, 'CONF_DIR', conf), patch.object(HELPER, 'CLIENTS', conf / 'clients.json'), \
                    patch.object(HELPER, 'TASKS', conf / 'tasks.json'), patch.object(HELPER, 'BACKUP_ROOT', backups), \
                    patch.object(HELPER, 'private_dir', side_effect=private_dir), \
                    patch.object(HELPER, 'read_regular', side_effect=lambda path: path.read_bytes()):
                destination, count = HELPER.backup(fixture())
            self.assertEqual(count, len(contents))
            for name, data in contents.items():
                path = destination / 'conf' / name
                self.assertEqual(path.read_bytes(), data)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            manifest = json.loads((destination / 'manifest.json').read_text())
            self.assertEqual(set(manifest['conf_sha256']), set(contents))
            self.assertEqual(manifest['online_ids'], [999, 1466, 1468])
            self.assertEqual(stat.S_IMODE((destination / 'manifest.json').stat().st_mode), 0o600)

    def test_conf_symlink_is_not_followed(self):
        with tempfile.TemporaryDirectory() as temporary:
            conf = Path(temporary)
            for name in ('clients.json', 'tasks.json', 'nps.conf'):
                (conf / name).write_text('offline')
            (conf / 'foreign.conf').symlink_to(conf / 'nps.conf')
            with patch.object(HELPER, 'CONF_DIR', conf), patch.object(HELPER, 'CLIENTS', conf / 'clients.json'), \
                    patch.object(HELPER, 'TASKS', conf / 'tasks.json'):
                with self.assertRaisesRegex(HELPER.Refuse, 'nonregular'):
                    HELPER.conf_files()

    def test_backup_copy_checksum_failure_is_not_ignored(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            conf, backups = base / 'conf', base / 'backups'
            conf.mkdir()
            for name in ('clients.json', 'tasks.json', 'nps.conf'):
                (conf / name).write_bytes(b'offline')
            def private_dir(path):
                path.mkdir(mode=0o700, parents=True, exist_ok=True)
                path.chmod(0o700)
            def read(path):
                return b'corrupted-copy' if backups in path.parents else path.read_bytes()
            with patch.object(HELPER, 'CONF_DIR', conf), patch.object(HELPER, 'CLIENTS', conf / 'clients.json'), \
                    patch.object(HELPER, 'TASKS', conf / 'tasks.json'), patch.object(HELPER, 'BACKUP_ROOT', backups), \
                    patch.object(HELPER, 'private_dir', side_effect=private_dir), patch.object(HELPER, 'read_regular', side_effect=read):
                with self.assertRaisesRegex(HELPER.Refuse, 'checksum_failed'):
                    HELPER.backup(fixture())

    def test_read_regular_rejects_non_root_source_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'file'
            path.write_bytes(b'offline')
            fake = types.SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=501, st_size=7)
            with patch.object(HELPER.os, 'fstat', return_value=fake):
                with self.assertRaisesRegex(HELPER.Refuse, 'ownership_or_size'):
                    HELPER.read_regular(path)

    def test_only_scoped_task_add_is_a_mutation_endpoint(self):
        source = (ROOT / 'scripts/deployment/create_dual_udp_nps_tasks.py').read_text()
        for forbidden in ("'/client/add'", "'/client/edit'", "'/index/edit'", "'/index/del'", "'/index/stop'",
                          "'restart'", "'reload'", "'stop'"):
            self.assertNotIn(forbidden, source)
        self.assertEqual(source.count("admin.json('/index/add',"), 1)
        self.assertEqual(HELPER.add_fields(HELPER.SCOPES[0])['local_proxy'], 'false')
        self.assertNotIn('vkey', HELPER.add_fields(HELPER.SCOPES[0]))


if __name__ == '__main__':
    unittest.main()
