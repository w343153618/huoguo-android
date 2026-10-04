"""Post-framework-return order and six-file identity checks, not Android UI runs."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from scripts.probes import build_source_snapshot as build
from scripts.probes import source_snapshot_retirement as m
from tests.test_source_owned_snapshot_binding import case
from tests.test_source_snapshot_protocol import SDK, JAVA_HOME, ROOT


def six_case():
    args = case()
    args['retired_raw'] = b'1 123 2000 567 8 90 1\n'
    args['files']['retired'] = (2000, 0o100600, 8, 96, len(args['retired_raw']), 1)
    return args


class RetirementBindingChecks(unittest.TestCase):
    def test_six_file_consistency_is_not_live_authority_or_global_quiescence(self):
        result = m.bind(**six_case())
        self.assertTrue(result['normal_framework_start_return_reported'])
        self.assertTrue(result['six_file_metadata_consistent'])
        self.assertNotIn('five_file_metadata_consistent', result)
        for name in ('actual_execution_ownership_verified', 'remote_UI_quiescence_verified',
                     'owned_scope_removed_verified', 'source_qualification_verified',
                     'permission_lease_verified', 'installed_framework_execution_verified'):
            self.assertFalse(result[name])

    def test_old_five_file_chain_and_extra_files_cannot_claim_retirement(self):
        for name in m.FILES:
            args = six_case(); del args['files'][name]
            with self.assertRaises(ValueError): m.bind(**args)
        args = six_case(); args['files']['foreign'] = args['files']['retired']
        with self.assertRaises(ValueError): m.bind(**args)

    def test_retired_identity_must_match_completed_child_and_owned_directory(self):
        for raw in (b'1 124 2000 567 8 90 1\n', b'1 123 2000 568 8 90 1\n',
                    b'1 123 0 567 8 90 1\n', b'1 123 2000 567 9 90 1\n',
                    b'1 123 2000 567 8 99 1\n'):
            args = six_case(); args['retired_raw'] = raw
            args['files']['retired'] = (2000, 0o100600, 8, 96, len(raw), 1)
            with self.assertRaises(ValueError): m.bind(**args)

    def test_retired_metadata_size_links_mode_alias_rejected(self):
        for column, value in ((0, 0), (1, 0o100644), (1, 0o120600), (2, 9),
                              (3, 90), (3, 91), (3, 0), (4, 0), (5, 2)):
            args = six_case(); meta = list(args['files']['retired']); meta[column] = value
            args['files']['retired'] = tuple(meta)
            with self.assertRaises(ValueError): m.bind(**args)

    def test_closed_numeric_receipt_no_false_return_or_overflow(self):
        for raw in (b'', b'1 123 2000 567 8 90 0\n', b'1 123 2000 567 8 90 1 extra\n',
                    b'1 123 2000 9223372036854775808 8 90 1\n',
                    b'1 123 2000 0 8 90 1\n', b'1 123 2000 567 8 90 1\n\n'):
            with self.assertRaises(ValueError): m.receipt(raw, expected_uid=2000)
        with self.assertRaises(ValueError): m.receipt(six_case()['retired_raw'], expected_uid=True)

    def test_closed_distinct_namespaces_select_explicit_runner(self):
        a, b = '1' * 24, '2' * 24
        argv = m.runner_arguments(a, b)
        self.assertEqual(argv[-3:], ('-e', 'runner', m.RUNNER))
        self.assertEqual(argv[2], f'huoguo-source-ui-{b}/snapshot.jar')
        for x, y in ((a, a), ('../'+a, b), (a, b+'\n'), (True, b)):
            with self.assertRaises(ValueError): m.runner_arguments(x, y)

    def test_wrong_framework_pin_fails_before_build_directory_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); wrong = root/'wrong.dex'; wrong.write_bytes(b'foreign')
            with self.assertRaises(ValueError):
                build.build(root/'output', SDK, JAVA_HOME, retirement_framework_dex=wrong)
            self.assertFalse((root/'output').exists())


class RetirementJavaChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='huoguo-retirement-jvm-')
        cls.root = Path(cls.temp.name)
        cls.java = str(JAVA_HOME/'bin/java') if (JAVA_HOME/'bin/java').is_file() else shutil.which('java')
        cls.javac = str(JAVA_HOME/'bin/javac') if (JAVA_HOME/'bin/javac').is_file() else shutil.which('javac')
        jars = [SDK/'platforms/android-37.0'/path for path in
                ('android.jar', 'uiautomator.jar', 'optional/android.test.base.jar')]
        if not all(path.is_file() for path in jars): raise RuntimeError('Existing API37 required')
        abi = cls.root/'abi'; abi.mkdir()
        production = cls.root/'production'; production.mkdir()
        cls.command([cls.javac, '-source', '8', '-target', '8', '-cp', str(jars[0]),
                     '-d', str(abi), str(build.ABI_SHIM)])
        cls.command([cls.javac, '-source', '8', '-target', '8', '-cp',
                     os.pathsep.join(map(str, jars+[abi])), '-d', str(production),
                     *map(str, build.SOURCES+build.RETIREMENT_SOURCES)])
        if (production/'com').exists(): raise AssertionError('ABI compiled into production output')
        fake = cls.root/'fake'; fake.mkdir()
        texts = {
            'android/os/Bundle.java': '''package android.os;
public class Bundle { public String value; public String getString(String key){return value;} }''',
            'com/android/uiautomator/testrunner/UiAutomatorTestRunner.java': '''package com.android.uiautomator.testrunner;
import android.os.Bundle; import java.util.List;
public class UiAutomatorTestRunner {
 public static int starts; public static boolean returned; public static String mode;
 public void run(List<String> c,Bundle p,boolean d,boolean m){start();
  if(!"returns".equals(mode))throw new IllegalStateException("fixture_exit_zero");}
 protected void start(){starts++; if("throws".equals(mode))throw new IllegalStateException("fixture_cleanup_failure"); returned=true;}
}''',
            'local/huoguo/sourceprobe/RetirementReceipt.java': '''package local.huoguo.sourceprobe;
import com.android.uiautomator.testrunner.UiAutomatorTestRunner;
final class RetirementReceipt {
 static int writes;
 static RetirementReceipt prepare(String p){SnapshotPath.directory(p);return new RetirementReceipt();}
 void normalStartReturned(){if(!UiAutomatorTestRunner.returned)throw new AssertionError("early_receipt"); writes++;}
}''',
            'local/huoguo/sourceprobe/RetirementCheck.java': '''package local.huoguo.sourceprobe;
import android.os.Bundle; import java.util.Arrays;
import com.android.uiautomator.testrunner.UiAutomatorTestRunner;
public class RetirementCheck { public static void main(String[] args){
 String mode=args[0]; UiAutomatorTestRunner.mode=mode;
 Bundle p=new Bundle(); p.value="huoguo-source-ui-0123456789abcdef01234567/window.xml";
 if(mode.equals("badpath"))p.value="../"+p.value;
 String c=mode.equals("foreign")?"foreign":"local.huoguo.sourceprobe.SourceSnapshot#testSnapshot";
 try{new RetirementRunner().run(Arrays.asList(c),p,false,false);throw new AssertionError("returned");}
 catch(IllegalStateException|IllegalArgumentException e){
 int starts=UiAutomatorTestRunner.starts,writes=RetirementReceipt.writes;
 boolean before=mode.equals("badpath")||mode.equals("foreign");
 if(starts!=(before?0:1)||writes!=((before||mode.equals("throws"))?0:1))throw new AssertionError(e);
 if(mode.equals("returns")&&!e.getMessage().equals("framework_run_unexpected_return"))throw new AssertionError(e);
 System.out.println("order_verified_"+mode);}}
}'''}
        sources=[]
        for name, body in texts.items():
            path=fake/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(body); sources.append(path)
        cls.command([cls.javac, '-source', '8', '-target', '8', '-d', str(fake),
                     *map(str, sources), str(build.RETIREMENT_SOURCES[0]), str(build.SOURCES[0])])
        cls.fake = fake

    @classmethod
    def command(cls, args):
        result=subprocess.run(args, capture_output=True, text=True, timeout=30)
        if result.returncode: raise AssertionError(result.stdout+result.stderr)
        return result

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def test_actual_runner_normal_return_then_receipt_and_throw_has_no_receipt(self):
        for mode in ('normal', 'throws', 'returns', 'foreign', 'badpath'):
            result=self.command([self.java, '-cp', str(self.fake),
                                 'local.huoguo.sourceprobe.RetirementCheck', mode])
            self.assertIn('order_verified_'+mode, result.stdout)


if __name__ == '__main__': unittest.main()
