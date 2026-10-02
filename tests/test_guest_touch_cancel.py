"""Whole-gesture cancellation contract; pure/offline, never a phone acceptance claim."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
GUEST = ROOT / 'stream-server/src/main/java'
TOUCH = ROOT / 'experiments/moonlight-v2/transport/android-udp'


class GuestTouchCancelCheck(unittest.TestCase):
    def test_host_loss_retry_cancel_rotation_and_ten_pointer_contract(self):
        result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(TOUCH),
                                 '-p', 'test_udp_touch_control.py'],
                                cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('OK', result.stderr)

    def test_real_guest_pointer_state_cancellation_resets_all_ids_offline(self):
        java_home = Path(os.environ.get('JAVA_HOME', '/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home'))
        javac = java_home / 'bin/javac'
        java = java_home / 'bin/java'
        if not javac.is_file() or not java.is_file():
            compiler, runner = shutil.which('javac'), shutil.which('java')
            if not compiler or not runner:
                self.skipTest('Existing JDK required; no toolchain is installed by this check')
            javac, java = Path(compiler), Path(runner)
        sources = [ROOT / 'tests/java/android/view/MotionEvent.java',
                   GUEST / 'com/genymobile/scrcpy/model/Point.java',
                   GUEST / 'com/genymobile/scrcpy/control/Pointer.java',
                   GUEST / 'com/genymobile/scrcpy/control/PointersState.java',
                   GUEST / 'com/genymobile/scrcpy/control/TouchCapabilities.java',
                   ROOT / 'tests/java/com/genymobile/scrcpy/control/PointersStateCancelCheck.java']
        with tempfile.TemporaryDirectory(prefix='huoguo-guest-cancel-offline-') as temporary:
            subprocess.run([str(javac), '-d', temporary, *map(str, sources)],
                           check=True, capture_output=True, text=True, timeout=20)
            result = subprocess.run([str(java), '-cp', temporary,
                                     'com.genymobile.scrcpy.control.PointersStateCancelCheck'],
                                    check=True, capture_output=True, text=True, timeout=5)
            capability = subprocess.run([str(java), '-cp', temporary,
                                         'com.genymobile.scrcpy.control.TouchCapabilities'],
                                        check=True, capture_output=True, text=True, timeout=5)
        self.assertIn('guest pointer cancel bookkeeping checks (offline; no Android injection)', result.stdout)
        self.assertEqual(capability.stdout, 'touch_cancel_clears_pointers_v1\n')


if __name__ == '__main__':
    unittest.main()
