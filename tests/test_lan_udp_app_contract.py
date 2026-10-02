"""Offline App descriptor boundaries; does not touch phones, accounts or hosts."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
JDK = Path('/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home/bin')

class LanUdpAppContractCheck(unittest.TestCase):
    def test_login_peer_binding_media_protocol_and_numeric_descriptor_are_fail_closed(self):
        javac = str(JDK/'javac') if (JDK/'javac').exists() else shutil.which('javac')
        java = str(JDK/'java') if (JDK/'java').exists() else shutil.which('java')
        with tempfile.TemporaryDirectory(prefix='huoguo-app-contract-') as folder:
            subprocess.run([javac, '-d', folder,
                str(ROOT/'app/src/udp/java/local/remoteandroid/direct/LanUdpContract.java'),
                str(ROOT/'tests/java/local/remoteandroid/direct/LanUdpContractCheck.java')],
                check=True, capture_output=True, timeout=30)
            result = subprocess.run([java, '-cp', folder, 'local.remoteandroid.direct.LanUdpContractCheck'],
                check=True, capture_output=True, text=True, timeout=5)
            self.assertIn('passed', result.stdout)

if __name__ == '__main__':
    unittest.main()
