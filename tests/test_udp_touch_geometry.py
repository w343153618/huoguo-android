from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch


DIRECTORY = Path(__file__).resolve().parents[1] / 'experiments/moonlight-v2/transport/android-udp'
sys.path.insert(0, str(DIRECTORY))
from run_phone_udp import parse_touch_geometry, read_touch_geometry


class UdpTouchGeometryCheck(unittest.TestCase):
    def test_physical_720_and_1080_are_read_back(self):
        for width, height in ((720, 1280), (1080, 1920)):
            report = parse_touch_geometry(f'Physical size: {width}x{height}\n'.encode())
            self.assertEqual(report, {'physical_width': width, 'physical_height': height,
                                      'effective_width': width, 'effective_height': height,
                                      'override_present': False})

    def test_override_is_effective_geometry_and_physical_is_retained(self):
        report = parse_touch_geometry('Physical size: 1080x1920\r\nOverride size: 720x1280\r\n')
        self.assertEqual((report['physical_width'], report['physical_height']), (1080, 1920))
        self.assertEqual((report['effective_width'], report['effective_height']), (720, 1280))
        self.assertTrue(report['override_present'])
        # Differing aspect ratios are reported without silently substituting
        # the physical dimensions; downstream display mapping needs this fact.
        report = parse_touch_geometry('Physical size: 1080x1920\nOverride size: 1080x1800\n')
        self.assertEqual(report['effective_height'], 1800)

    def test_invalid_missing_ambiguous_or_unbounded_dimensions_fail_closed(self):
        cases = ('', 'Override size: 720x1280\n', 'Physical size: 0x1920\n',
                 'Physical size: -1080x1920\n', 'Physical size: 1080x0\n',
                 'Physical size: 1080x65536\n', 'Physical size: 100000x1920\n',
                 'Physical size: 1080.0x1920\n', 'Physical size: 01080x1920\n',
                 'Physical size: １０８０x1920\n',
                 'Physical size: 1080x1920\nOverride size: 0x1280\n',
                 'Physical size: 1080x1920\nPhysical size: 720x1280\n',
                 'Physical size: 1080x1920\nOverride size: 720x1280\nOverride size: 720x1280\n',
                 'Physical size: 1080x1920\nprivate account token\n',
                 'error: device offline\n', 'Physical size: 1080x1920 trailing\n',
                 ' ' * 1025, b'Physical size: 1080x1920\xff', None)
        for value in cases:
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(ValueError) as error:
                    parse_touch_geometry(value)
                self.assertNotIn('private account token', str(error.exception))

    def test_source_query_is_explicit_bounded_and_read_only(self):
        with patch('run_phone_udp.subprocess.run', return_value=SimpleNamespace(stdout=b'Physical size: 1080x1920\n')) as run:
            report = read_touch_geometry('/independent/platform-tools/adb')
        self.assertEqual(report['effective_width'], 1080)
        run.assert_called_once_with(['/independent/platform-tools/adb', '-s', 'emulator-5556',
                                     'shell', 'wm', 'size'],
                                    stdin=subprocess.DEVNULL, check=True,
                                    capture_output=True, timeout=5)

    def test_read_failure_or_timeout_does_not_fall_back_to_720(self):
        for error in (subprocess.CalledProcessError(1, ['adb']),
                      subprocess.TimeoutExpired(['adb'], 5)):
            with patch('run_phone_udp.subprocess.run', side_effect=error):
                with self.assertRaises(type(error)):
                    read_touch_geometry('/independent/platform-tools/adb')

    def test_explicit_m5_emulator_queries_only_that_source_and_retains_parser(self):
        with patch('run_phone_udp.subprocess.run', return_value=SimpleNamespace(
                stdout=b'Physical size: 720x1280\n')) as run:
            report = read_touch_geometry('/independent/platform-tools/adb',
                                         source_serial='emulator-5554')
        self.assertEqual((report['effective_width'], report['effective_height']), (720, 1280))
        run.assert_called_once_with(['/independent/platform-tools/adb', '-s', 'emulator-5554',
                                     'shell', 'wm', 'size'], stdin=subprocess.DEVNULL,
                                    check=True, capture_output=True, timeout=5)

    def test_untrusted_phone_or_malformed_source_serial_never_queries_adb(self):
        for serial in ('physical-phone', '127.0.0.1:5555', '', 'emulator-0',
                       'emulator-05554', 'emulator-65536', 'emulator-5554;id',
                       'emulator-5554\n', None, True, 5554):
            with self.subTest(serial=serial), patch('run_phone_udp.subprocess.run') as run:
                with self.assertRaises(ValueError):
                    read_touch_geometry('/independent/platform-tools/adb', source_serial=serial)
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
