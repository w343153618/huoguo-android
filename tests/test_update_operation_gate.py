"""Execute the production updater operation gate; no Android or network work.

The concurrency fixture races real synchronized gate calls, not a model of the
implementation. Installer and permission UI behavior still requires Android
acceptance; these checks only establish token ownership and phase boundaries.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'app/src/main/java/local/remoteandroid/direct'
JDK = Path('/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home/bin')


class UpdateOperationGateChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.javac = str(JDK / 'javac') if (JDK / 'javac').is_file() else shutil.which('javac')
        cls.java = str(JDK / 'java') if (JDK / 'java').is_file() else shutil.which('java')
        if not cls.javac or not cls.java:
            raise RuntimeError('Existing JDK required')
        cls.folder = tempfile.TemporaryDirectory(prefix='huoguo-update-operation-')
        built = subprocess.run([cls.javac, '-d', cls.folder.name,
            str(PACKAGE / 'UpdateOperationGate.java'),
            str(ROOT / 'tests/java/local/remoteandroid/direct/UpdateOperationGateCheck.java')],
            capture_output=True, text=True, timeout=30)
        if built.returncode:
            cls.folder.cleanup()
            raise AssertionError(built.stdout + built.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def gate(self, mode):
        result = subprocess.run([self.java, '-cp', self.folder.name,
            'local.remoteandroid.direct.UpdateOperationGateCheck', mode],
            capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('actual updater operation checks passed', result.stdout)

    def test_single_owner_and_wrong_phase_do_not_change_live_operation(self): self.gate('lease')
    def test_stale_worker_cannot_release_or_transition_a_new_operation(self): self.gate('stale')
    def test_full_check_dialog_download_verification_lifecycle(self): self.gate('flow')
    def test_permission_resume_changes_token_and_cannot_repeat_during_verification(self): self.gate('permission')
    def test_installer_resume_changes_token_and_old_activity_cannot_clear_it(self): self.gate('installer')
    def test_live_network_and_verification_cannot_be_adopted_by_resume(self): self.gate('no_steal')
    def test_real_concurrent_acquisition_has_only_one_owner(self): self.gate('concurrency')


if __name__ == '__main__':
    unittest.main()
