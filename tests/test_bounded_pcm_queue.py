"""Offline PCM ownership/capacity/deadline checks; never a phone performance claim."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BoundedPcmQueueCheck(unittest.TestCase):
    def test_fixed_pool_ownership_loss_reasons_and_bounded_shutdown(self):
        java_home = Path(os.environ.get('JAVA_HOME', '/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home'))
        javac, java = java_home/'bin/javac', java_home/'bin/java'
        if not javac.is_file() or not java.is_file():
            compiler, runner = shutil.which('javac'), shutil.which('java')
            if not compiler or not runner:
                self.skipTest('Existing JDK required; this check installs no toolchain')
            javac, java = Path(compiler), Path(runner)
        with tempfile.TemporaryDirectory(prefix='huoguo-pcm-queue-offline-') as temporary:
            subprocess.run([str(javac), '-d', temporary,
                str(ROOT/'experiments/nps-transport/phone/BoundedPcmQueue.java'),
                str(ROOT/'tests/java/local/remoteandroid/direct/BoundedPcmQueueCheck.java')],
                check=True, capture_output=True, text=True, timeout=20)
            result = subprocess.run([str(java), '-cp', temporary, 'local.remoteandroid.direct.BoundedPcmQueueCheck'],
                check=True, capture_output=True, text=True, timeout=10)
        self.assertIn('bounded PCM queue checks (offline JVM; no codec, phone or acoustic measurement)', result.stdout)

if __name__ == '__main__':
    unittest.main()
