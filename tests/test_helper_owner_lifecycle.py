"""Actual host same-parent PM stand-ins, driver EOF and own-FD retirement.

Android callbacks are synthetic; nothing here establishes a phone/server lease.
"""
import hashlib
import json
from pathlib import Path
import shutil
import signal
import struct
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'experiments/moonlight-v2/source-snapshot/helper_owner_lifecycle.c'
FIXTURE=ROOT/'tests/fixtures/helper_owner_lifecycle_fixture.c'

class HelperLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler=shutil.which('clang') or shutil.which('cc')
        if not compiler: raise RuntimeError('host compiler required')
        cls.build=tempfile.TemporaryDirectory(prefix='huoguo-helper-lifecycle-build-')
        cls.fixture=Path(cls.build.name)/'fixture';cls.production=Path(cls.build.name)/'production'
        for source,binary in [(SOURCE,cls.production),(FIXTURE,cls.fixture)]:
            subprocess.run([compiler,'-std=c11','-O2','-Wall','-Wextra','-Werror',
                            str(source),'-o',str(binary)],capture_output=True,check=True,timeout=20)
    @classmethod
    def tearDownClass(cls): cls.build.cleanup()
    def setUp(self):
        self.base=tempfile.TemporaryDirectory(prefix='huoguo-helper-lifecycle-scope-')
        self.path=Path(self.base.name);self.nonce=b'a'*24
        self.parent=self.path/('huoguo-helper-owner-'+self.nonce.decode())
        self.data=b'host public payload, no Android APK\n'*8
    def tearDown(self): self.base.cleanup()
    def header(self,kind,sequence,size=0,sha=None):
        return b'HGHC0001'+bytes([kind,0,0,0])+struct.pack('!I',size)+self.nonce+struct.pack('!Q',sequence)+(sha or b'0'*64)
    def run_fixture(self,mode='natural',bad_driver_wire=False):
        st=self.path.stat();sha=hashlib.sha256(self.data).hexdigest()
        words=[str(self.fixture),'--fixture',str(self.path),self.nonce.decode(),
               str(st.st_dev),str(st.st_ino),str(len(self.data)),sha,mode]
        wire=self.header(1,0,len(self.data),sha.encode())+self.data+self.header(2,1)
        wire+=self.header(3,2)+self.header(5 if bad_driver_wire else 4,3)+self.header(5,4)+self.header(6,5)
        result=subprocess.run(words,input=wire,capture_output=True,timeout=4)
        self.assertEqual(result.stderr,b'')
        rows=[json.loads(line) for line in result.stdout.splitlines()]
        footer=[row for row in rows if row.get('event')=='helper_lifecycle_fixture_footer']
        self.assertEqual(len(footer),1,rows);row=footer[0]
        for key in ('complete_live_binding_implemented','phone_operator_server_lease_verified','idle_for_release','release_authorized'):
            self.assertFalse(row[key])
        self.assertEqual(row['actual_Android_PM_or_scope_operations'],0)
        return result.returncode,row
    def refused(self,mode):
        code,row=self.run_fixture(mode)
        self.assertNotEqual(code,0);self.assertFalse(row['host_same_owner_retirement_completed'])
        self.assertFalse(row['bound_close_completed']);self.assertTrue(row['unknown_keeps_handles'])
        return row
    def test_production_no_activation(self):
        for args,code in [([],0),(['--execute'],2),(['--fixture'],2)]:
            r=subprocess.run([str(self.production),*args],capture_output=True,timeout=1)
            self.assertEqual(r.returncode,code);self.assertFalse(self.parent.exists())
            if not args: self.assertFalse(json.loads(r.stdout)['production_activation_available'])
    def test_same_parent_actual_PM_driver_EOF_and_own_scope_retirement(self):
        code,row=self.run_fixture();self.assertEqual(code,0)
        for key in ('install_standin_verified','driver_standin_verified','uninstall_standin_verified',
                    'file_removed','stage_removed','parent_removed','bound_close_completed'):
            self.assertTrue(row[key])
        self.assertFalse(self.parent.exists())
    def test_missing_scope_callback_no_creation(self):
        code,row=self.run_fixture('missing_scope_qualification');self.assertNotEqual(code,0)
        self.assertFalse(row['upload_verified']);self.assertFalse(self.parent.exists())
    def test_operator_unknown_before_install(self):
        row=self.refused('missing_operator');self.assertFalse(row['install_standin_verified'])
        self.assertTrue((self.parent/'stage/owned.apk').exists())
    def test_installed_snapshot_unknown_no_driver_or_uninstall(self):
        row=self.refused('installed_package_unknown');self.assertFalse(row['driver_standin_verified'])
        self.assertFalse(row['uninstall_standin_verified'])
    def test_DRIVER_DONE_wire_is_not_actual_driver(self):
        row=self.refused('wire_driver_only');self.assertFalse(row['driver_standin_verified'])
    def test_actual_driver_exit_without_stderr_EOF_blocks_uninstall(self):
        row=self.refused('driver_pipe_unknown');self.assertFalse(row['driver_standin_verified'])
        self.assertFalse(row['uninstall_standin_verified'])
    def test_later_Attempt_cannot_uninstall(self):
        row=self.refused('later_Attempt');self.assertTrue(row['driver_standin_verified'])
        self.assertFalse(row['uninstall_standin_verified'])
    def test_later_package_cannot_uninstall(self):
        self.assertFalse(self.refused('later_package')['uninstall_standin_verified'])
    def test_unknown_input_cleanup_cannot_uninstall(self):
        self.assertFalse(self.refused('input_unknown')['uninstall_standin_verified'])
    def test_local_PM_Success_not_independent_absence(self):
        row=self.refused('absence_unknown');self.assertTrue(row['last_PM_reaped'])
        self.assertFalse(row['file_removed'])
    def test_remaining_privileged_writer_unknown_preserves_every_node(self):
        self.assertFalse(self.refused('privileged_writer_unknown')['file_removed'])
        self.assertEqual((self.parent/'stage/owned.apk').read_bytes(),self.data)
    def test_forced_uninstall_wait_reaped_but_retirement_forbidden(self):
        row=self.refused('uninstall_ignore_TERM');self.assertTrue(row['last_PM_reaped'])
        self.assertEqual(row['last_PM_signal'],signal.SIGKILL);self.assertFalse(row['file_removed'])
    def test_failed_PM_clients_do_not_bless_cleanup(self):
        for index,mode in enumerate(('install_failure','uninstall_failure')):
            if index: shutil.rmtree(self.parent)
            self.assertFalse(self.refused(mode)['file_removed'])
    def test_cancel_before_delete_retains_all_nodes(self):
        self.assertFalse(self.refused('cancel')['file_removed'])
    def test_mutated_mode_hash_link_and_foreign_entry_refuse_before_delete(self):
        for index,mode in enumerate(('mode','contents','file_link','foreign')):
            if index: shutil.rmtree(self.parent)
            self.assertFalse(self.refused(mode)['file_removed'])
    def test_replaced_file_and_parent_are_never_adopted(self):
        for index,mode in enumerate(('file_replaced','parent_replaced')):
            if index:
                shutil.rmtree(self.parent);shutil.rmtree(self.path/'old-parent',ignore_errors=True)
            self.assertFalse(self.refused(mode)['file_removed'])
    def test_partial_retirement_keeps_foreign_entry_and_live_FDs(self):
        row=self.refused('partial_foreign')
        self.assertTrue(row['file_removed']);self.assertFalse(row['stage_removed'])
        self.assertEqual((self.parent/'stage/foreign').read_bytes(),b'foreign')
    def test_complete_retirement_cannot_delete_later_same_name(self):
        code,row=self.run_fixture('second_call');self.assertEqual(code,0)
        self.assertTrue(row['second_call_blocked']);self.assertTrue(self.parent.is_dir())
        self.assertFalse(row['bound_close_completed'])
    def test_skipped_driver_wire_never_uninstalls(self):
        code,row=self.run_fixture(bad_driver_wire=True);self.assertNotEqual(code,0)
        self.assertFalse(row['uninstall_standin_verified']);self.assertFalse(row['file_removed'])

if __name__=='__main__': unittest.main()
