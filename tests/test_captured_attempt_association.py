"""Cooperative association JVM candidate. No production adapter/ART or permission."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
JDK = Path('/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home/bin')
SOURCE = ROOT / 'experiments/moonlight-v2/authenticated-lan/association/CapturedAttemptAssociation.java'

class CapturedAssociationChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix='huoguo-association-JVM-')
        cls.java = str(JDK/'java') if (JDK/'java').is_file() else shutil.which('java')
        javac = str(JDK/'javac') if (JDK/'javac').is_file() else shutil.which('javac')
        cls.env = {'PATH':str(Path(javac).parent)+':/usr/bin:/bin', 'LANG':'C',
            'JAVA_HOME':str(Path(javac).parent.parent)}
        result = subprocess.run([javac, '-Xlint:all', '-Werror', '-d', cls.folder.name,
            str(SOURCE), str(ROOT/'tests/fixtures/CapturedAttemptAssociationFixture.java')],
            capture_output=True, text=True, timeout=30, env=cls.env)
        if result.returncode:
            cls.folder.cleanup()
            raise AssertionError(result.stdout+result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def case(self, mode):
        result = subprocess.run([self.java, '-cp', self.folder.name,
            'local.remoteandroid.direct.CapturedAttemptAssociationFixture', mode],
            capture_output=True, text=True, timeout=8, env=self.env)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertIn('release false', result.stdout)

for _mode in ['inert','identity','real_workers','later_attempt','receiver','surface',
        'clock_domain','mutation_inside','slow_callback','expired','backward','escaped',
        'foreign_thread','reentrant','callback_exception','peer_death','missing_close',
        'foreign_close','later_before_close','death_join','later_after_close','interrupted_join',
        'initial_invalid','clock_exception','death_callback','slow_close',
        'pending_worker_deadline','repeat_capture','lock_wait']:
    setattr(CapturedAssociationChecks, 'test_'+_mode, lambda self, mode=_mode: self.case(mode))

if __name__ == '__main__':
    unittest.main()
