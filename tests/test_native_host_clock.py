"""Deadline clock contract, including simulated accumulated host sleep."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.probes.verify_native_host_clock import verify, DIRECTORY, ROOT


class NativeHostClockChecks(unittest.TestCase):
    def test_darwin_uses_uptime_when_continuous_clock_has_accumulated_sleep(self):
        with tempfile.TemporaryDirectory(prefix='huoguo-clock-contract-') as folder:
            binary = Path(folder) / 'contract'
            subprocess.run(['c++', '-std=c++20', '-Wall', '-Wextra', '-Werror',
                            '-I', str(DIRECTORY), str(ROOT/'tests/native/host_clock_contract.cpp'),
                            '-o', str(binary)], check=True, capture_output=True, timeout=30)
            subprocess.run([str(binary)], check=True, capture_output=True, timeout=2)

    def test_real_native_clock_read_is_inside_python_send_receive_bracket(self):
        result = verify(samples=16)
        self.assertEqual(result['sample_count'], 16)
        self.assertTrue(result['all_inside_python_bracket'])

    def test_reader_sample_limit_is_bounded(self):
        for count in (0, 65, True):
            with self.assertRaises(ValueError):
                verify(samples=count)


if __name__ == '__main__':
    unittest.main()
