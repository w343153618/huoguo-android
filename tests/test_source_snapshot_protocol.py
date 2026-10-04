import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.probes import build_source_snapshot as build
from scripts.probes import source_snapshot_protocol as m

ROOT = Path(__file__).resolve().parents[1]
JAVA_HOME = Path(os.environ.get('JAVA_HOME',
    '/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home'))
SDK = Path(os.environ.get('ANDROID_HOME', Path.home() / 'Library/Android/sdk'))


def status():
    return b'''INSTRUMENTATION_STATUS: class=local.huoguo.sourceprobe.SourceSnapshot
INSTRUMENTATION_STATUS: test=testSnapshot
INSTRUMENTATION_STATUS_CODE: 1
INSTRUMENTATION_STATUS_CODE: 0
INSTRUMENTATION_RESULT: stream=
Time: 0.25

OK (1 test)
INSTRUMENTATION_CODE: -1
'''


class SnapshotProtocolChecks(unittest.TestCase):
    def test_default_build_cli_never_compiles_or_deploys(self):
        with patch.object(build, 'build', side_effect=AssertionError('build')), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(build.main([]), 0)
        self.assertEqual(json.loads(output.getvalue())['device_operations'], 0)

    def test_namespace_refuses_foreign_alias_traversal_or_shell_selectors(self):
        for value in ('a'*23, 'a'*25, 'A'*24, '../foreign', 'a;rm', True, None,
                      'a'*24+'\n', 'a'*24+'\x00'):
            with self.assertRaises(ValueError): m.runner_arguments(value)
        nonce = 'a' * 24
        argv = m.runner_arguments(nonce)
        self.assertEqual(argv[0:2], ('uiautomator', 'runtest'))
        self.assertEqual(argv[2], m.namespace(nonce)+'/snapshot.jar')
        self.assertEqual(argv[-1], m.relative_path(nonce))
        self.assertNotIn('--nohup', argv)

    def test_serialization_receipt_never_claims_runner_exit_or_source_ownership(self):
        value = m.receipt(b'1 123 2000 567 8 90 8 91 169211\n', expected_uid=2000)
        self.assertEqual(value['xml_bytes'], 169211)
        self.assertFalse(value['runner_exit_verified'])
        self.assertFalse(value['source_identity_verified'])

    def test_bad_or_foreign_receipts_are_rejected(self):
        raw = b'1 123 2000 567 8 90 8 91 169211\n'
        for changed in (raw+b'x', raw*2, raw.replace(b'2000', b'0'), raw[:-1],
                        raw.replace(b'123', b'0'), raw.replace(b'567', b'0'),
                        raw.replace(b'169211', b'1048576'), raw.replace(b'90', b'0'),
                        raw.replace(b'567', b'9223372036854775808'), b'\xff', b'x'*257):
            with self.assertRaises(ValueError): m.receipt(changed, expected_uid=2000)
        with self.assertRaises(ValueError): m.receipt(raw, expected_uid=True)

    def test_file_receipt_without_actual_runner_success_is_not_completion(self):
        self.assertTrue(m.runner_status(status()))
        repeated = status().replace(b'INSTRUMENTATION_STATUS_CODE: 0',
            b'INSTRUMENTATION_STATUS: class=local.huoguo.sourceprobe.SourceSnapshot\n'
            b'INSTRUMENTATION_STATUS: test=testSnapshot\nINSTRUMENTATION_STATUS_CODE: 0')
        self.assertTrue(m.runner_status(repeated))
        for raw in (b'', status().replace(b'-1', b'0'), status()+status(),
                    status().replace(b'testSnapshot', b'foreignTest'),
                    status().replace(b'(1 test)', b'(2 test)'),
                    status().replace(b'CODE: 0', b'CODE: -2'),
                    status().replace(b'SourceSnapshot', b'ForeignSnapshot'),
                    status()+b'java.lang.IllegalStateException\n', b'x'*8192,
                    b'\xff'+status()):
            self.assertFalse(m.runner_status(raw))

    def test_jar_builder_refuses_source_tree_destination_before_creating_files(self):
        with self.assertRaises(ValueError): build.build(ROOT/'snapshot-artifact', SDK, JAVA_HOME)
        self.assertFalse((ROOT/'snapshot-artifact').exists())

    def test_actual_runner_source_has_no_input_idle_or_source_attachment(self):
        source = build.SOURCES[1].read_text()
        self.assertIn('getUiDevice().dumpWindowHierarchy(relative)', source)
        for text in ('.click(', '.swipe(', '.press', '.waitForIdle(', 'registerWatcher(',
                     '.getCurrentPackageName(', 'Runtime.getRuntime', 'ProcessBuilder',
                     'setCompressedLayout', 'setWaitForIdleTimeout', 'loadLibrary(', 'sourceApp'):
            self.assertNotIn(text, source)
        for text in ('O_NOFOLLOW', 'O_EXCL', 'st_nlink == 1', 'MAX_BYTES = 1048576',
                     'start == startTicks()', 'same(before, after)', 'getCanonicalPath()'):
            self.assertIn(text, source)


class SnapshotJavaChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix='huoguo-snapshot-api37-')
        cls.java = str(JAVA_HOME/'bin/java') if (JAVA_HOME/'bin/java').is_file() else shutil.which('java')
        cls.javac = str(JAVA_HOME/'bin/javac') if (JAVA_HOME/'bin/javac').is_file() else shutil.which('javac')
        jars = [SDK/'platforms/android-37.0'/path for path in
                ('android.jar', 'uiautomator.jar', 'optional/android.test.base.jar')]
        if not all(p.is_file() for p in jars):
            cls.folder.cleanup()
            raise RuntimeError('Existing SDK API37 required for standalone snapshot compile')
        result = subprocess.run([cls.javac, '-source', '8', '-target', '8',
            '-cp', os.pathsep.join(map(str, jars)), '-d', cls.folder.name,
            *map(str, build.SOURCES)], capture_output=True, text=True, timeout=30)
        if result.returncode:
            cls.folder.cleanup()
            raise AssertionError(result.stdout+result.stderr)
        harness = Path(cls.folder.name)/'PathCheck.java'
        harness.write_text('''import local.huoguo.sourceprobe.SnapshotPath;
public class PathCheck { public static void main(String[] args) {
String valid="huoguo-source-ui-0123456789abcdef01234567/window.xml";
if(!SnapshotPath.directory(valid).equals(valid.split("/")[0]))throw new AssertionError();
String[] bad={null,"/data/local/tmp/"+valid,"../"+valid,valid+"\\n",valid+"\\u0000",valid.replace("window.xml","other.xml"),valid.replace("012345", "ABCDEF"),valid.replace("/window", "//window")};
for(String s:bad){try{SnapshotPath.directory(s);throw new AssertionError("accepted");}catch(IllegalArgumentException expected){}}
System.out.println("actual closed snapshot path passed"); }}''')
        result = subprocess.run([cls.javac, '-cp', cls.folder.name, '-d', cls.folder.name,
                                str(harness)], capture_output=True, text=True, timeout=30)
        if result.returncode: raise AssertionError(result.stdout+result.stderr)

    @classmethod
    def tearDownClass(cls): cls.folder.cleanup()

    def test_actual_api37_compile_and_executed_closed_path_validation(self):
        result = subprocess.run([self.java, '-cp', self.folder.name, 'PathCheck'],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertIn('actual closed snapshot path passed', result.stdout)


if __name__ == '__main__': unittest.main()
