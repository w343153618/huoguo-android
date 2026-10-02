import subprocess
import unittest
from unittest.mock import patch
from scripts.probes.source_probe_guards import active_source_capture, require_no_source_capture


class SourceProbeGuardCheck(unittest.TestCase):
    def test_m1_capture_is_blocked_for_both_flag_forms(self):
        self.assertTrue(active_source_capture('python /repo/hardware_stream.py --serial emulator-5556 --fps 60'))
        self.assertTrue(active_source_capture('python hardware_stream.py --serial=emulator-5556'))

    def test_other_emulator_and_unrelated_filename_do_not_match(self):
        self.assertFalse(active_source_capture('python /repo/hardware_stream.py --serial emulator-5554'))
        self.assertFalse(active_source_capture('python hardware_stream.py.backup --serial emulator-5556'))

    def test_unknown_process_inventory_blocks_changes(self):
        with patch('scripts.probes.source_probe_guards.subprocess.run',
                   return_value=subprocess.CompletedProcess([], 1, '', 'private error')):
            with self.assertRaisesRegex(RuntimeError, '^active_or_unknown_source_capture_retained$'):
                require_no_source_capture()


if __name__ == '__main__':
    unittest.main()
