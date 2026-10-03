"""Gateway/App/worker resolution agreement using the real, non-launching CLI."""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from stream_settings import DEFAULT_BIT_RATE, RESOLUTION_MAX_SIZES, parse_settings

ROOT = Path(__file__).resolve().parents[1]


class HardwareResolutionContractChecks(unittest.TestCase):
    def cli(self, size):
        # A deliberately invalid emulator serial must stop before worker().
        # The fresh grpc sentinel also prevents any worker dependency/channel
        # work if a future regression accidentally removes that validation.
        with tempfile.TemporaryDirectory(prefix='huoguo-resolution-cli-') as directory:
            temporary = Path(directory)
            (temporary / 'grpc.py').write_text("raise AssertionError('unexpected_worker_dependency_load')\n")
            env = dict(os.environ, PYTHONPATH=str(temporary), PYTHONNOUSERSITE='1')
            result = subprocess.run([
                sys.executable, str(ROOT / 'hardware_stream.py'),
                '--serial', 'fixture-invalid-serial', '--avd', 'OfflineFixture',
                '--directory', str(temporary / 'absent-runtime'),
                '--max-size', str(size), '--bitrate', str(DEFAULT_BIT_RATE),
                '--fps', '60', '--mode', 'VBR',
                '--video-fd', '-1', '--audio-fd', '-1', '--control-fd', '-1',
            ], cwd=temporary, env=env, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=5, close_fds=True)
            self.assertFalse((temporary / 'absent-runtime').exists())
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertNotIn('worker_error', result.stderr)
        self.assertNotIn('unexpected_worker_dependency_load', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        return result.stderr

    def test_shared_presets_include_all_standard_and_accepted_historical_values(self):
        self.assertIs(type(RESOLUTION_MAX_SIZES), tuple)
        self.assertEqual(RESOLUTION_MAX_SIZES, (768, 960, 1200, 1280, 1600, 1920, 2400))

    def test_each_gateway_accepted_resolution_passes_real_cli_size_parser(self):
        for size in RESOLUTION_MAX_SIZES:
            with self.subTest(size=size):
                self.assertEqual(parse_settings({'max_size': size}, 960), (size, DEFAULT_BIT_RATE))
                self.assertEqual(parse_settings({}, size), (size, DEFAULT_BIT_RATE))
                stderr = self.cli(size)
                self.assertIn('error: requires a local emulator and plain AVD name', stderr)
                self.assertNotIn("argument --max-size: invalid", stderr)

    def test_unsupported_cli_resolutions_fail_at_size_before_serial_check(self):
        for size in (720, 2560, 0, -1):
            with self.subTest(size=size):
                stderr = self.cli(size)
                self.assertIn('error: argument --max-size: invalid choice:', stderr)
                self.assertNotIn('error: requires a local emulator', stderr)

    def test_non_integer_cli_values_fail_without_worker_entry(self):
        for size in ('True', 'False', '768.0', 'None'):
            with self.subTest(size=size):
                stderr = self.cli(size)
                self.assertIn('error: argument --max-size: invalid int value:', stderr)
                self.assertNotIn('error: requires a local emulator', stderr)

    def test_gateway_rejects_bool_float_string_and_unsupported_size_or_default(self):
        for size in (True, False, None, '768', 768.0, 720, 2560, 0, -1):
            with self.subTest(size=size):
                with self.assertRaisesRegex(ValueError, '^Invalid resolution$'):
                    parse_settings({'max_size': size}, 960)
                with self.assertRaisesRegex(ValueError, '^Invalid resolution$'):
                    parse_settings({}, size)

    def test_formal_app_standard_historical_and_legacy_sizes_are_gateway_subset(self):
        # Fixed source contract: this formal Java class has no values() method.
        # Parse all three literal size sources, not a test-side copy of its UI.
        source = (ROOT / 'app/src/main/java/local/remoteandroid/direct/StreamQuality.java').read_text()
        standard = re.findall(r'\bSTANDARD\s*=\s*\{([\d,\s]+)\}\s*;', source)
        legacy = re.findall(r'return\s+new\s+int\[\]\s*\{([\d,\s]+)\}', source)
        historical = re.findall(r'boolean\s+historical\s*=([^;]+);', source)
        self.assertEqual(len(standard), 1, 'formal standard size contract changed')
        self.assertEqual(len(legacy), 1, 'formal legacy size contract changed')
        self.assertEqual(len(historical), 1, 'formal historical size contract changed')
        historical_values = re.findall(r'\bremembered\s*==\s*(\d+)\b', historical[0])
        remainder = re.sub(r'\bremembered\s*==\s*\d+\b', '', historical[0])
        self.assertRegex(remainder, r'^\s*(?:\|\|\s*)*$', 'historical sizes are no longer fixed literal alternatives')
        size_sources = {
            'standard': [int(value) for value in standard[0].split(',')],
            'historical': [int(value) for value in historical_values],
            'legacy': [int(value) for value in legacy[0].split(',')],
        }
        for name, values in size_sources.items():
            with self.subTest(source=name):
                self.assertTrue(values, 'formal size source is empty')
                self.assertTrue(set(values).issubset(RESOLUTION_MAX_SIZES), values)
                for size in values:
                    self.assertEqual(parse_settings({'max_size': size}, 960)[0], size)


if __name__ == '__main__':
    unittest.main()
