"""Synthetic Android reads through the actual bounded callback contract."""
from pathlib import Path
import shlex
import unittest
from unittest.mock import patch

from scripts.probes import owner_native_phone_probes as probes
from scripts.probes.owner_native_gateway_probes import ProbeError
from scripts.probes.owner_native_diagnostic_preflight import Plan, SIGNER, PACKAGE_BINDINGS
from scripts.probes.owner_native_window import Window, APP_SHA256, JNI_SHA256, HELPER_SHA256


def window():
    return Window(Plan('3d476566ee8b4f2c9126713f0f3559e637a0958a', APP_SHA256, 40,
        JNI_SHA256, HELPER_SHA256, SIGNER, **PACKAGE_BINDINGS,
        process_max_seconds=300, sample_seconds=30))


APP_PATH = '/data/app/~~fixture/new_app/base.apk'
HELPER_PATH = '/data/app/~~fixture/new_helper/base.apk'
PS = '  PID   UID NAME\n 1 0 init\n 2 2000 sh\n'
CPU = 'policy0 364800 672000 902400 \npolicy2 499200 960000 960000 \npolicy5 499200 960000 614400 \npolicy7 499200 902400 499200 \n'


class Reads:
    def __init__(self):
        self.started = self.reaped = 0; self.pending = []; self.calls = []
        self.present_helper = False; self.private_input = False
        self.active_on_last_read = False; self.ps_count = 0
        self.bad_hash = False; self.bad_version = False; self.changed_path = False
        self.version = 40; self.root_uid = '0'; self.hz = '120.0'; self.unavailable = False
        self.app_paths = 0

    def run(self, argv, **kw):
        self.calls.append((argv, kw)); self.started += 1; self.reaped += 1
        words = argv[3:]
        if self.unavailable:
            raise ProbeError('native_probe_command_unverified')
        if words == ('get-state',): return 'device\n'
        if words == ('shell', 'ps', '-A', '-o', 'PID,UID,NAME'):
            self.ps_count += 1
            return PS + ('9 10235 '+probes.APP+':receiver\n' if self.active_on_last_read and self.ps_count > 1 else '')
        if words[:4] == ('shell', 'pm', 'path', '--user'):
            if words[-1] == probes.HELPER:
                return 'package:'+HELPER_PATH+'\n' if self.present_helper else ''
            self.app_paths += 1
            return 'package:'+('/data/app/changed/base.apk' if self.changed_path and self.app_paths>1 else APP_PATH)+'\n'
        if words[:3] == ('shell', 'dumpsys', 'package'):
            return '  versionCode='+str(self.version)+' minSdk=26 targetSdk=37\n  versionName=1.31-alpha.9\n'+('  versionCode=40\n' if self.bad_version else '')
        if words[:3] == ('shell', 'settings', 'get'): return self.hz+'\n'
        if len(words) != 2 or words[0] != 'shell' or not words[1].startswith('su -c '):
            raise AssertionError('unexpected command')
        root = shlex.split(shlex.split(words[1])[2])
        if root == ['id','-u']: return self.root_uid+'\n'
        if root[:1] == ['sha256sum']:
            digest = HELPER_SHA256 if root[1] == HELPER_PATH else APP_SHA256
            return ('0'*64 if self.bad_hash else digest)+'  '+root[1]+'\n'
        if root[:2] == ['sh','-c'] and root[2] == probes.CPU_READ: return CPU
        if root[:2] == ['sh','-c'] and 'udp-test-login.json' in root[2]:
            return 'present\n' if self.private_input else 'absent\n'
        raise AssertionError('unexpected root command')


class PhoneProbeChecks(unittest.TestCase):
    def make(self):
        reads = Reads()
        return probes.PhoneProbes(window(), Path('/inert/adb'), reads), reads

    def test_inert_construction_and_invalid_phase_never_access_device(self):
        with patch.object(probes.Commands, 'run') as run:
            reader = probes.PhoneProbes(window(), Path('/inert/adb'))
            with self.assertRaises(ValueError): reader.snapshot(helper_installed=1)
            run.assert_not_called(); self.assertIsNone(reader.last_snapshot)

    def test_fresh_exact_App_absent_helper_and_settings_are_snapshot_not_permission(self):
        reader, reads = self.make(); result = reader.snapshot()
        self.assertTrue(result['actual_installed_APK_bytes_match'])
        for key in ('remote_JNI_or_signer_extracted','atomic_App_hold','operator_permission',
                    'saved_UI_credential_verified','server_guest_lease','ART_native_events_verified'):
            self.assertFalse(result[key])
        self.assertGreater(result['CPU_readback']['policy0'][2], result['CPU_readback']['policy0'][1])
        self.assertEqual(reads.started, reads.reaped)
        for argv, kw in reads.calls:
            self.assertEqual(argv[:3], ('/inert/adb','-s',probes.PHONE))
            self.assertLessEqual(kw['seconds'], 3)
            self.assertNotIn('install', shlex.join(argv)); self.assertNotIn('uninstall', shlex.join(argv))

    def test_new_matching_installed_helper_phase_is_not_install_ownership(self):
        reader, reads = self.make(); reads.present_helper = True
        self.assertTrue(reader.snapshot(helper_installed=True)['helper_installed'])
        with self.assertRaisesRegex(ProbeError, 'preexisting_helper'):
            reader.absent_after_owned_cleanup()
        self.assertIsNone(reader.last_snapshot)

    def test_missing_device_never_becomes_process_absence_or_installs_helper(self):
        reader, reads = self.make(); reads.unavailable = True
        with self.assertRaises(ProbeError): reader.snapshot()
        self.assertEqual(len(reads.calls), 1); self.assertIsNone(reader.last_snapshot)

    def test_reject_old_or_different_installed_APK_before_any_helper_mutation(self):
        reader, reads = self.make(); reads.bad_hash = True
        with self.assertRaisesRegex(ProbeError, 'APK_mismatch'): reader.snapshot()
        self.assertFalse(any(probes.HELPER in argv for argv,_ in reads.calls))

    def test_reject_duplicate_foreign_or_wrong_version(self):
        for bad in ('duplicate', 'old'):
            reader, reads = self.make()
            reads.bad_version = bad == 'duplicate'; reads.version = 39 if bad == 'old' else 40
            with self.assertRaisesRegex(ProbeError, 'version_unknown'): reader.snapshot()

    def test_pm_path_change_bracket_refuses_same_hash_on_replaced_package(self):
        reader, reads = self.make(); reads.changed_path = True
        with self.assertRaisesRegex(ProbeError, 'path_changed'): reader.snapshot()

    def test_App_secondary_process_appearing_at_final_read_blocks_qualification(self):
        reader, reads = self.make(); reads.active_on_last_read = True
        with self.assertRaisesRegex(ProbeError, 'App_or_helper_active'): reader.snapshot()
        self.assertIsNone(reader.last_snapshot)

    def test_input_presence_only_no_file_read_delete_or_secret_fallback(self):
        reader, reads = self.make(); reads.private_input = True
        with self.assertRaisesRegex(ProbeError, 'private_input'): reader.snapshot()
        words = shlex.join(reads.calls[-1][0])
        self.assertNotIn('cat', words); self.assertNotIn('rm ', words)

    def test_root_or_refresh_unknown_refuses_without_writes(self):
        for key, value in (('root_uid','2000'), ('hz','60')):
            reader, reads = self.make(); setattr(reads,key,value)
            with self.assertRaises(ProbeError): reader.snapshot()

    def test_process_and_path_parsers_reject_incomplete_duplicate_and_active_subprocess(self):
        for raw in ('','PID UID NAME','PID UID NAME\n1 0 init\n1 0 sh','PID USER NAME\n1 root init'):
            with self.assertRaises(ProbeError): probes.processes(raw)
        self.assertFalse(probes.idle(probes.processes(PS+'10 10235 '+probes.HELPER+':other\n')))
        for raw in ('', 'package:/etc/base.apk', 'package:'+APP_PATH+'\npackage:'+HELPER_PATH):
            with self.assertRaises(ProbeError): probes.package_path(raw)

    def test_actual_kernel_thread_NAME_keeps_spaces_in_the_final_column(self):
        raw = PS + '1032 0 [irq/260-q6v5 wdog]\n1285 0 [Surge kthread]\n'
        rows = probes.processes(raw)
        self.assertEqual(rows[1032], (0, '[irq/260-q6v5 wdog]'))
        self.assertEqual(rows[1285], (0, '[Surge kthread]'))
        self.assertTrue(probes.idle(rows))

    def test_full_NAME_cannot_hide_ambiguous_App_or_helper_prefix(self):
        for package in (probes.APP, probes.HELPER):
            for suffix in (':receiver', ' ambiguous suffix'):
                rows = probes.processes(PS + '99 10316 ' + package + suffix + '\n')
                self.assertFalse(probes.idle(rows))

    def test_full_NAME_still_refuses_control_oversized_and_duplicate_rows(self):
        for tail in ('99 0 [kernel\tthread]\n', '99 0 [kernel\x00thread]\n',
                     '99 0 ' + 'x' * 4096 + '\n',
                     '99 0 [irq/260-q6v5 wdog]\n99 0 [Surge kthread]\n'):
            with self.assertRaisesRegex(ProbeError, 'inventory_unknown'):
                probes.processes(PS + tail)

    def test_full_inventory_retains_clone_profile_UID_without_user0_assumption(self):
        rows = probes.processes(PS + '9007 99910234 cloned.process\n')
        self.assertEqual(rows[9007], (99910234, 'cloned.process'))
        self.assertTrue(probes.idle(rows))
        for package in (probes.APP, probes.HELPER):
            rows = probes.processes(PS + '16137 99910267 ' + package + ':receiver\n')
            self.assertFalse(probes.idle(rows))
        for uid in ('-1', '4294967295', '4294967296'):
            with self.assertRaisesRegex(ProbeError, 'inventory_unknown'):
                probes.processes(PS + '99 ' + uid + ' invalid.uid\n')

    def test_CPU_unknown_policy_duplicate_or_reversed_caps_refuses(self):
        self.assertEqual(set(probes.cpu(CPU)), {'policy0','policy2','policy5','policy7'})
        for raw in ('', CPU+'policy0 1 2 1\n', CPU.replace('policy7','policy8'), CPU.replace('364800 672000','672000 364800')):
            with self.assertRaises(ProbeError): probes.cpu(raw)

    def test_budget_exhausted_does_not_add_call_or_reuse_previous_snapshot(self):
        reader, reads = self.make()
        with patch.object(probes.time, 'monotonic', side_effect=(1, 20)):
            with self.assertRaisesRegex(ProbeError, 'budget_expired'): reader.snapshot()
        self.assertEqual(reads.calls, []); self.assertIsNone(reader.last_snapshot)

    def test_pending_local_client_blocks_success_without_claiming_remote_quiescence(self):
        reader, reads = self.make(); reads.pending = [object()]
        with self.assertRaisesRegex(ProbeError, 'local_reap_unverified'): reader.snapshot()


if __name__ == '__main__': unittest.main()
