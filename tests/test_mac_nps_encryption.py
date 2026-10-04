"""Scoped NPS encryption preservation checks; no network or production writes."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts.deployment import enable_mac_nps_encryption as H
from tests.test_dual_udp_nps_tasks import fixture, install_udp


def snapshot():
    value = fixture()
    for i, scope in enumerate(H.base.SCOPES):
        install_udp(value, scope, 1412 + i)
    for table in ('clients', 'runtime_clients'):
        for row in value[table].values():
            row.update(RateLimit=0, MaxConn=0, WebUserName='', WebPassword='', WebTotpSecret='',
                       BlackIpList=[''])
            row.setdefault('NowConn', 0)
    for table in ('tasks', 'runtime_tasks'):
        for row in value[table].values():
            row['Client'] = copy.deepcopy(value['clients'][row['Client']['Id']])
            row.setdefault('NowConn', 0)
    return value


class EncryptionChecks(unittest.TestCase):
    def test_complete_form_preserves_existing_fields_and_counters(self):
        row = snapshot()['clients'][1466]
        row.update(RateLimit=80, MaxConn=7, MaxTunnelNum=9, WebUserName='existing-user',
                   WebPassword='offline-secret', ConfigConnAllow=True)
        row['Flow']['FlowLimit'] = 100
        row['Cnf'].update(U='original-user', P='original-password', Compress=True)
        form = H.edit_fields(row, True)
        self.assertEqual(form['vkey'], row['VerifyKey'])
        self.assertEqual((form['crypt'], form['compress'], form['flow_reset']), ('true', 'true', 'false'))
        self.assertEqual((form['flow_limit'], form['rate_limit'], form['max_conn'], form['max_tunnel']),
                         ('100', '80', '7', '9'))
        self.assertEqual((form['web_username'], form['web_password']), ('existing-user', 'offline-secret'))

    def test_side_effect_form_cases_refused(self):
        for key, value in (('Id', 999), ('BlackIpList', []), ('BlackIpList', ['a', 'a']),
                           ('WebTotpSecret', 'nonempty'), ('WebPassword', 'prefix-totp:secret'),
                           ('Remark', 'raw&unescaped'), ('MaxConn', True)):
            row = snapshot()['clients'][1466]
            row[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(H.Refuse):
                H.edit_fields(row, True)
        row = snapshot()['clients'][1466]
        row['Flow']['TimeLimit'] = '2027-01-01T00:00:00Z'
        with self.assertRaises(H.Refuse):
            H.edit_fields(row, True)

    def test_canonical_escaped_strings_round_trip(self):
        text = "a&'<>\"中文"
        self.assertEqual(H.go_escape(H.edit_string(H.go_escape(text))), H.go_escape(text))

    def test_only_requested_crypt_changes_are_accepted(self):
        before, after = snapshot(), snapshot()
        for table in ('clients', 'runtime_clients'):
            after[table][1466]['Cnf']['Crypt'] = True
        for row in after['runtime_tasks'].values():
            if row['Client']['Id'] == 1466:
                row['Client']['Cnf']['Crypt'] = True
        # Disk task snapshots may still embed the old client; authoritative
        # clients and runtime shared pointers must both read true.
        H.preservation(before, after, {1466})
        for change in ('vkey', 'compress', 'target', 'process', 'geo', 'missing-online', 'runtime-crypt'):
            bad = copy.deepcopy(after)
            if change == 'vkey': bad['clients'][1466]['VerifyKey'] = 'different'
            if change == 'compress': bad['clients'][1466]['Cnf']['Compress'] = True
            if change == 'target': bad['tasks'][1409]['Target']['TargetStr'] = '127.0.0.1:1'
            if change == 'process': bad['process']['pid'] += 1
            if change == 'geo': bad['geo']['geo_in']['rules_sha256'] = 'different'
            if change == 'missing-online': bad['online_ids'] -= {999}
            if change == 'runtime-crypt': bad['runtime_tasks'][1409]['Client']['Cnf']['Crypt'] = False
            with self.subTest(change=change), self.assertRaises(H.Refuse):
                H.preservation(before, bad, {1466})

    def test_active_client_or_task_refused(self):
        value = snapshot()
        H.idle(value)
        for table, ident in (('runtime_clients', 1466), ('runtime_tasks', 1413)):
            busy = copy.deepcopy(value)
            busy[table][ident]['NowConn'] = 1
            with self.subTest(table=table), self.assertRaises(H.Refuse): H.idle(busy)

    def test_ui_requires_exact_unique_selected_crypt_option(self):
        class Admin:
            body = b'<select name="crypt"><option value="0"></option><option selected value="1"></option></select>'
            def read(self, path): return self.body
        admin = Admin()
        self.assertTrue(H.ui_verified(admin, 1466, True))
        for raw in (b'', admin.body + admin.body,
                    b'<select name="crypt"><option selected value="0"></option></select>'):
            admin.body = raw
            with self.assertRaises(H.Refuse): H.ui_verified(admin, 1466, True)

    def test_default_inspection_performs_no_edit_or_backup(self):
        value = snapshot()
        class Admin:
            def json(self, *args): raise AssertionError('unexpected write')
        with patch.object(H.base, 'capture', return_value=value), patch.object(H, 'ui_verified', return_value=True), \
             patch.object(H, 'core_hash', return_value=H.CORE_SHA), \
             patch.object(H, 'backup', side_effect=AssertionError('unexpected backup')):
            result = H.execute(Admin())
        self.assertFalse(result['write_requested'])
        self.assertEqual(result['changed_client_ids'], [])

    def test_backup_precedes_only_two_scoped_edits_and_idempotent_retry(self):
        live, events = snapshot(), []
        class Admin:
            def json(self, endpoint, form):
                if endpoint != '/client/edit': raise AssertionError('unexpected endpoint')
                events.append(('edit', form['id']))
                self.assert_form(form)
                for table in ('clients', 'runtime_clients'):
                    live[table][form['id']]['Cnf']['Crypt'] = True
                for row in live['runtime_tasks'].values():
                    if row['Client']['Id'] == form['id']: row['Client']['Cnf']['Crypt'] = True
                return {'status': 1}
            def assert_form(self, form):
                if form != H.edit_fields(live['clients'][form['id']], True):
                    raise AssertionError('original complete form not preserved')
        def backup(before):
            events.append(('backup',))
            return Path('/offline/backup'), 9
        with patch.object(H.base, 'capture', side_effect=lambda _: copy.deepcopy(live)), \
             patch.object(H, 'ui_verified', return_value=True), \
             patch.object(H, 'core_hash', return_value=H.CORE_SHA), \
             patch.object(H, 'backup', side_effect=backup), patch.object(H.base, 'private_write'):
            first = H.execute(Admin(), True)
            again = H.execute(Admin(), True)
        self.assertEqual(events, [('backup',), ('edit', 1466), ('edit', 1468)])
        self.assertEqual(first['changed_client_ids'], [1466, 1468])
        self.assertEqual(again['changed_client_ids'], [])

    def test_unexpected_first_edit_prevents_second_edit(self):
        live, calls = snapshot(), []
        class Admin:
            def json(self, endpoint, form):
                calls.append(form['id'])
                for table in ('clients', 'runtime_clients'):
                    live[table][form['id']]['Cnf'].update(Crypt=True, Compress=True)
                return {'status': 1}
        with patch.object(H.base, 'capture', side_effect=lambda _: copy.deepcopy(live)), \
             patch.object(H, 'ui_verified', return_value=True), \
             patch.object(H, 'core_hash', return_value=H.CORE_SHA), \
             patch.object(H, 'backup', return_value=(Path('/offline/backup'), 9)), \
             patch.object(H.base, 'private_write'), self.assertRaises(H.Refuse):
            H.execute(Admin(), True)
        self.assertEqual(calls, [1466])


if __name__ == '__main__':
    unittest.main()
