"""Actual host query wait/EOF and nofollow reads; Android metadata synthetic."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'experiments/moonlight-v2/source-snapshot/helper_owner_readonly.c'
FIXTURE=ROOT/'tests/fixtures/helper_owner_readonly_fixture.c'
PAYLOAD=b'public host APK standin\n'

class HelperReadonlyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler=shutil.which('clang') or shutil.which('cc')
        if not compiler: raise RuntimeError('host compiler required')
        cls.build=tempfile.TemporaryDirectory(prefix='huoguo-helper-readonly-build-')
        cls.production=Path(cls.build.name)/'production';cls.fixture=Path(cls.build.name)/'fixture'
        for source,binary in ((SOURCE,cls.production),(FIXTURE,cls.fixture)):
            subprocess.run([compiler,'-std=c11','-O2','-Wall','-Wextra','-Werror',str(source),'-o',str(binary)],
                           capture_output=True,check=True,timeout=20)
    @classmethod
    def tearDownClass(cls): cls.build.cleanup()
    def setUp(self):
        self.base=tempfile.TemporaryDirectory(prefix='huoguo-helper-readonly-host-')
        self.path=Path(self.base.name);self.parent=self.path/'~~fixture/name'
        self.parent.mkdir(parents=True);self.file=self.parent/'base.apk';self.file.write_bytes(PAYLOAD)
        # Only this new host fixture's nodes. No Android/existing base chmod/chown.
        for p in (self.path,self.parent.parent,self.parent,self.file):
            os.chown(p,os.geteuid(),os.getegid());p.chmod(0o644 if p==self.file else 0o755)
    def tearDown(self): self.base.cleanup()
    def run_fixture(self,mode='valid',accepted=True):
        r=subprocess.run([str(self.fixture),'--fixture',str(self.path),mode],capture_output=True,timeout=4)
        self.assertEqual(r.stderr,b'');row=json.loads(r.stdout)
        self.assertEqual(row['host_query_snapshot_accepted'],accepted,row)
        self.assertEqual(r.returncode,0 if accepted else 2,row)
        for key in ('actual_Android_queries','operator_Attempt_server_lease_verified',
                    'remote_PM_quiescence_verified','scope_retirement_or_release'):
            self.assertFalse(row[key])
        self.assertTrue(row['readonly_handles_closed']);return row
    def test_production_inert_and_all_activation_args_refused(self):
        for args,code in (([],0),(['--execute'],2),(['--fixture'],2)):
            r=subprocess.run([str(self.production),*args],capture_output=True,timeout=1)
            self.assertEqual(r.returncode,code)
            if not args: self.assertFalse(json.loads(r.stdout)['operations_started'])
    def test_exact_package_hash_and_path_with_actual_four_owned_queries(self):
        row=self.run_fixture();self.assertEqual(row['queries_natural'],4)
        self.assertTrue(row['initial_dual_EOF']);self.assertEqual(self.file.read_bytes(),PAYLOAD)
    def test_query_child_closes_inherited_FD_above_lowered_soft_limit(self):
        row=self.run_fixture('above_soft_limit');self.assertEqual(row['queries_natural'],4)
    def test_fixed_App_snapshot_is_separate_from_helper_operation(self): self.run_fixture('App')
    def test_helper_absence_requires_two_empty_PM_reads_and_process_bracket(self):
        row=self.run_fixture('absent');self.assertEqual(row['queries_natural'],4)
    def test_process_only_snapshot_requires_two_complete_tables(self):
        row=self.run_fixture('idle');self.assertEqual(row['queries_natural'],2)
    def test_nonempty_package_does_not_claim_absence(self):
        # Wrong path and multiple package sections fail before hash.
        for mode in ('wrong_path','traversal','multiple'): self.run_fixture(mode,False)
    def test_later_package_path_rejected(self): self.run_fixture('later_path',False)
    def test_helper_and_secondary_App_processes_block_idle(self):
        for mode in ('active_App','active_helper','later_active'): self.run_fixture(mode,False)
    def test_malformed_numeric_inventory_and_duplicate_PID_rejected(self):
        for mode in ('duplicate_PID','bad_UID','embedded_NUL'): self.run_fixture(mode,False)
    def test_actual_android_NAME_padding_and_kernel_spaces_are_full_columns(self):
        self.assertEqual(self.run_fixture('kernel_NAME')['queries_natural'],4)
    def test_clone_profile_inventory_UID_is_distinct_from_user0_package_qualification(self):
        self.assertEqual(self.run_fixture('clone_UID')['queries_natural'],4)
        for mode in ('clone_App','clone_helper'): self.run_fixture(mode,False)
    def test_expanded_full_column_still_refuses_ambiguous_or_malformed_names_and_UIDs(self):
        for mode in ('bad_UID_range','control_NAME','target_space','bad_header_tail'):
            self.run_fixture(mode,False)
    def test_child_nonzero_or_stderr_does_not_qualify(self):
        for mode in ('nonzero','stderr'): self.run_fixture(mode,False)
    def test_overflow_drains_actual_child_to_EOF_then_refuses(self):
        row=self.run_fixture('overflow',False);self.assertTrue(row['overflow']);self.assertTrue(row['initial_dual_EOF'])
    def test_unreaped_timeout_retains_object_without_automatic_signal(self):
        row=self.run_fixture('ignore_TERM',False);self.assertTrue(row['unreaped_at_failure'])
        self.assertTrue(row['close_initially_refused']);self.assertEqual(row['initial_TERM_calls'],0)
        self.assertEqual(row['initial_KILL_calls'],0);self.assertEqual(row['final_KILL_calls'],1)
        self.assertTrue(row['actual_child_reaped']);self.assertTrue(row['unknown'])
    def test_natural_exit_without_dual_EOF_cannot_close_or_qualify(self):
        row=self.run_fixture('held_EOF',False);self.assertTrue(row['actual_child_reaped'])
        self.assertFalse(row['initial_dual_EOF']);self.assertTrue(row['close_initially_refused'])
    def test_unverified_parent_signal_policy_refuses_before_fork(self): self.run_fixture('autoreap',False)
    def test_cancel_and_invalid_budget_or_operation_refuse_before_fork(self):
        for mode in ('cancel','deadline','invalid_kind'):
            self.assertEqual(self.run_fixture(mode,False)['queries_natural'],0)
    def test_query_object_cannot_be_reused_for_later_metadata(self):
        row=self.run_fixture('repeat');self.assertTrue(row['repeat_sticky_refused']);self.assertTrue(row['unknown'])
    def test_wrong_hash_is_unknown_with_file_preserved(self):
        self.file.write_bytes(b'X'+PAYLOAD[1:]);self.run_fixture('valid',False)
        self.assertEqual(self.file.read_bytes(),b'X'+PAYLOAD[1:])
    def test_wrong_size_or_mode_or_multiple_links_rejected(self):
        self.file.write_bytes(PAYLOAD+b'X');self.run_fixture('valid',False)
        self.file.write_bytes(PAYLOAD);self.file.chmod(0o666);self.run_fixture('valid',False)
        self.file.chmod(0o644);os.link(self.file,self.path/'other');self.run_fixture('valid',False)
    def test_file_symlink_never_followed(self):
        target=self.path/'target';target.write_bytes(PAYLOAD);self.file.unlink();self.file.symlink_to(target)
        self.run_fixture('valid',False);self.assertTrue(self.file.is_symlink())
    def test_parent_symlink_never_followed(self):
        target=self.path/'target';self.parent.rename(target);self.parent.symlink_to(target,target_is_directory=True)
        self.run_fixture('valid',False);self.assertTrue(self.parent.is_symlink())
    def test_hash_metadata_growth_and_mode_change_after_read_rejected(self):
        for mode in ('contents_changed','mode_changed','growth'):
            self.file.write_bytes(PAYLOAD);self.file.chmod(0o644);self.run_fixture(mode,False)
    def test_later_file_or_parent_identity_not_adopted(self):
        self.run_fixture('file_replaced',False);self.run_fixture('parent_replaced',False)

if __name__=='__main__': unittest.main()
