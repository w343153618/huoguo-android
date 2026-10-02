"""Fail-closed tests for the scoped NPS provisioning preflight; no live requests."""
import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('m5_nps_helper', ROOT / 'scripts/deployment/create_m5_nps_tunnel.py')
HELPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)


class M5ProvisionPreflight(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.clients, self.tasks, self.key = base / 'clients.json', base / 'tasks.json', base / 'vkey'
        self.old = {'Id': 1466, 'Remark': 'Existing M1', 'VerifyKey': 'old-test-value', 'Status': True}
        self.old_task = {'Id': 1409, 'Port': 15556, 'Remark': 'Existing M1', 'Mode': 'tcp'}
        self.client = {'Id': 2001, 'Remark': HELPER.CLIENT_REMARK, 'VerifyKey': 'new-offline-test-value',
                       'ConfigConnAllow': False, 'Status': True}
        self.task = {'Id': 2002, 'Port': 15558, 'Remark': HELPER.TASK_REMARK, 'Mode': 'tcp',
                     'ServerIp': '0.0.0.0', 'Status': True, 'Client': {'Id': 2001},
                     'Target': {'TargetStr': HELPER.TARGET, 'LocalProxy': False}}
        self.patchers = [patch.object(HELPER, 'CLIENTS', self.clients), patch.object(HELPER, 'TASKS', self.tasks),
                         patch.object(HELPER, 'VKEY_FILE', self.key),
                         patch.object(HELPER, 'regular_secret', return_value='new-offline-test-value'),
                         patch.object(HELPER.subprocess, 'run', return_value=types.SimpleNamespace(stdout=''))]
        for p in self.patchers:
            p.start()
        self.addCleanup(self.temp.cleanup)
        for p in self.patchers:
            self.addCleanup(p.stop)

    def save(self, clients=(), tasks=(), key=False):
        self.clients.write_text(json.dumps([self.old, *clients]))
        self.tasks.write_text(json.dumps([self.old_task, *tasks]))
        if key:
            self.key.write_text('offline placeholder')

    def test_fresh_preflight_does_not_create_secret_file(self):
        self.save()
        self.assertEqual(HELPER.new_pair_snapshot(), (None, None, None))
        self.assertFalse(self.key.exists())

    def test_owned_exact_pair_can_resume_without_duplicate(self):
        self.save([self.client], [self.task], key=True)
        client, task, key = HELPER.new_pair_snapshot()
        self.assertEqual((client['Id'], task['Id'], key), (2001, 2002, 'new-offline-test-value'))

    def test_shared_m1_identity_is_refused(self):
        candidate = dict(self.client, Id=1466)
        self.save([candidate])
        with self.assertRaisesRegex(HELPER.Refuse, 'not_independent'):
            HELPER.new_pair_snapshot()

    def test_port_owned_by_other_client_is_refused(self):
        task = dict(self.task, Client={'Id': 1466})
        self.save([self.client], [task], key=True)
        with self.assertRaisesRegex(HELPER.Refuse, 'owned_by_other_client'):
            HELPER.new_pair_snapshot()

    def test_wrong_target_or_server_local_proxy_is_refused(self):
        for target in [{'TargetStr': '192.168.9.128:15556', 'LocalProxy': False},
                       {'TargetStr': HELPER.TARGET, 'LocalProxy': True}]:
            with self.subTest(target=target):
                self.save([self.client], [dict(self.task, Target=target)], key=True)
                with self.assertRaisesRegex(HELPER.Refuse, 'parameters_differ'):
                    HELPER.new_pair_snapshot()

    def test_unowned_secret_is_refused(self):
        self.save([dict(self.client, VerifyKey='different-test-key')], key=True)
        with self.assertRaisesRegex(HELPER.Refuse, 'not_owned_by_helper'):
            HELPER.new_pair_snapshot()

    def test_duplicate_remark_is_refused(self):
        self.save([self.client, dict(self.client, Id=2003)])
        with self.assertRaisesRegex(HELPER.Refuse, 'ambiguous'):
            HELPER.new_pair_snapshot()

    def test_reserved_remark_on_other_port_is_refused(self):
        self.save([self.client], [dict(self.task, Port=15559)], key=True)
        with self.assertRaisesRegex(HELPER.Refuse, 'other_port'):
            HELPER.new_pair_snapshot()

    def test_unowned_live_listener_is_refused(self):
        self.save()
        with patch.object(HELPER.subprocess, 'run', return_value=types.SimpleNamespace(stdout='LISTEN\n')):
            with self.assertRaisesRegex(HELPER.Refuse, 'listener_conflict'):
                HELPER.new_pair_snapshot()


if __name__ == '__main__':
    unittest.main()
