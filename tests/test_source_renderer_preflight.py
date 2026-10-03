"""Offline fixtures only: no real adb, emulator, phone, renderer or stream."""
import importlib.util
import json
from pathlib import Path
import tempfile
import textwrap
import time
import unittest


PATH = Path(__file__).resolve().parents[1]/'scripts/probes/read_source_renderer_preflight.py'
SPEC = importlib.util.spec_from_file_location('renderer_preflight', PATH)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)
PACKAGE = 'app.morphe.android.youtube'
BOOT = '01234567-89ab-cdef-0123-456789abcdef'
GFX = f'** Graphics info for pid 3391 [{PACKAGE}] **\nPipeline=Skia (OpenGL)\nsecret-title-never-emitted\n'
MODE = 'Active mode for display 0:\n  Mode ID: 1, Resolution: 1080x1920, Refresh Rate: 30.00 Hz'
ROTATION = ('Display: mDisplayId=0 (organized)\n'
    '  winConfig mDisplayRotation=ROTATION_0 mRotation=ROTATION_0\n'
    '  DisplayRotation\n    mRotation=0 mDeferredRotationPauseCount=0\nprivate-window-title')


class Reader:
    def __init__(self, *, renderer='skiagl', pids=('3391', '3391'), gfx=GFX, overrides=None):
        self.calls = []
        self.pid_values = iter(pids)
        self.values = {
            ('get-state',): 'device', ('shell', 'getprop', 'sys.boot_completed'): '1',
            ('shell', 'cat', '/proc/sys/kernel/random/boot_id'): BOOT,
            ('shell', 'getprop', 'debug.hwui.renderer'): renderer,
            ('shell', 'getprop', 'debug.renderengine.backend'): 'skiaglthreaded',
            ('shell', 'dumpsys', 'gfxinfo', PACKAGE): gfx,
            ('shell', 'wm', 'size'): 'Physical size: 1080x1920\nOverride size: 540x960',
            ('shell', 'wm', 'density'): 'Physical density: 480\nOverride density: 240',
            ('shell', 'cmd', 'display', 'get-active-mode', '0'): MODE,
            ('shell', 'dumpsys', 'window', 'displays'): ROTATION,
        }
        self.values.update(overrides or {})

    def read(self, arguments, limit):
        self.calls.append((arguments, limit))
        value = next(self.pid_values) if arguments == ('shell', 'pidof', PACKAGE) else self.values[arguments]
        return value if isinstance(value, tuple) else ('ok', value)


def collect(reader):
    return probe.collect(reader, 'emulator-5556', PACKAGE, 'skiavk')


class SourceRendererMetadata(unittest.TestCase):
    def test_coldboot_property_drift_is_distinct_from_live_process_mismatch(self):
        reader = Reader()
        result = collect(reader)
        self.assertEqual(result['sample_status'], 'complete')
        self.assertEqual(result['source_pid'], 3391)
        self.assertEqual(result['source_pipeline'], 'opengl')
        self.assertEqual(result['flags'], {
            'renderer_property_target_drift': True,
            'target_vs_process_pipeline_mismatch': True,
            'property_vs_process_pipeline_mismatch': False})
        self.assertEqual(result['active_mode_display0']['refresh_hz'], 30.0)
        self.assertEqual(result['display_rotation'], 0)
        self.assertEqual(result['size']['physical'], {'width': 1080, 'height': 1920})
        self.assertEqual(result['size']['override'], {'width': 540, 'height': 960})
        self.assertFalse(result['device_mutated'])
        self.assertFalse(result['source_app_restarted'])
        self.assertFalse(result['media_started'])
        serialized = json.dumps(result)
        self.assertNotIn('secret-title', serialized)
        self.assertNotIn('private-window-title', serialized)
        self.assertNotIn(BOOT, serialized)
        self.assertEqual(len(reader.calls), 13)
        for command, _ in reader.calls:
            self.assertNotIn('reset', command)
            self.assertNotIn('setprop', command)
            self.assertNotIn('am', command)
            self.assertNotIn('input', command)
            self.assertNotIn('su', command)

    def test_setprop_readback_alone_cannot_claim_live_vulkan(self):
        result = collect(Reader(renderer='skiavk'))
        self.assertFalse(result['flags']['renderer_property_target_drift'])
        self.assertTrue(result['flags']['target_vs_process_pipeline_mismatch'])
        self.assertTrue(result['flags']['property_vs_process_pipeline_mismatch'])

    def test_matching_live_vulkan_is_metadata_not_visual_or_fps_acceptance(self):
        result = collect(Reader(renderer='skiavk', gfx=GFX.replace('(OpenGL)', '(Vulkan)')))
        self.assertEqual(result['source_pipeline'], 'vulkan')
        self.assertEqual(set(result['flags'].values()), {False})
        self.assertIn('not_media_performance_or_visual_acceptance', result['scope'])
        self.assertNotIn('fps', result)

    def test_process_churn_invalidates_pipeline_attribution(self):
        result = collect(Reader(pids=('3391', '4455')))
        self.assertEqual(result['process_status'], 'process_changed')
        self.assertEqual(result['source_pipeline'], 'unavailable')
        self.assertIsNone(result['flags']['target_vs_process_pipeline_mismatch'])
        self.assertEqual(result['sample_status'], 'partial')

    def test_wrong_gfx_pid_or_package_is_not_the_selected_process(self):
        for bad in (GFX.replace('3391', '999'), GFX.replace(PACKAGE, 'other.private.package')):
            with self.subTest(bad=bad[:40]):
                result = collect(Reader(gfx=bad))
                self.assertEqual(result['source_pipeline'], 'unavailable')
                self.assertIsNone(result['flags']['target_vs_process_pipeline_mismatch'])
                self.assertNotIn('other.private.package', json.dumps(result))

    def test_no_process_and_multiple_processes_are_explicit_unavailable(self):
        for values, expected in ((('', ''), 'not_running'), (('3391 4455', '3391 4455'), 'multiple_pids')):
            result = collect(Reader(pids=values))
            self.assertEqual(result['process_status'], expected)
            self.assertIsNone(result['source_pid'])
            self.assertEqual(result['source_pipeline'], 'unavailable')

    def test_unknown_property_and_pipeline_do_not_escape_fixed_vocabulary(self):
        result = collect(Reader(renderer='secret-unexpected-property', gfx=GFX.replace('(OpenGL)', '(Secret)')))
        self.assertEqual(result['hwui_property'], 'unrecognized')
        self.assertEqual(result['source_pipeline'], 'unavailable')
        self.assertIsNone(result['flags']['renderer_property_target_drift'])
        self.assertNotIn('Secret', json.dumps(result))
        self.assertNotIn('secret-unexpected', json.dumps(result))

    def test_failed_command_is_unavailable_and_not_an_empty_property_success(self):
        result = collect(Reader(overrides={('shell', 'getprop', 'debug.hwui.renderer'): ('query_timeout', 'private-detail')}))
        self.assertEqual(result['hwui_property'], 'unavailable')
        self.assertEqual(result['query_status']['hwui'], 'query_timeout')
        self.assertNotIn('private-detail', json.dumps(result))

    def test_boot_change_invalidates_process_readback(self):
        reader = Reader()
        original = reader.read
        count = 0
        def read(arguments, limit):
            nonlocal count
            status, value = original(arguments, limit)
            if arguments[-1] == '/proc/sys/kernel/random/boot_id':
                count += 1
                if count == 2:
                    value = '11234567-89ab-cdef-0123-456789abcdef'
            return status, value
        reader.read = read
        result = collect(reader)
        self.assertFalse(result['same_boot_verified'])
        self.assertEqual(result['process_status'], 'boot_unverified_or_changed')
        self.assertEqual(result['source_pipeline'], 'unavailable')

    def test_closed_selection_rejects_phone_or_arbitrary_package_before_read(self):
        reader = Reader()
        for args in (('phone-serial', PACKAGE, 'skiavk'), ('emulator-5556', 'private.package', 'skiavk'),
                     ('emulator-5556', PACKAGE, 'unknown-renderer')):
            with self.assertRaises(ValueError):
                probe.collect(reader, *args)
        self.assertEqual(reader.calls, [])

    def test_numeric_bounds_and_ambiguous_rotation_fail_without_raw_output(self):
        self.assertIsNone(probe.pids('999999999999'))
        self.assertIsNone(probe.pids('3391 secret'))
        self.assertIsNone(probe.pids('3391 3391'))
        self.assertEqual(probe.dimensions('Physical size: 999999999999x1920'), {})
        self.assertEqual(probe.densities('Physical density: 999999999999'), {})
        self.assertIsNone(probe.active_mode('Unknown command'))
        self.assertIsNone(probe.active_mode(MODE.replace('30.00', '999.00')))
        self.assertIsNone(probe.active_mode(MODE.replace('display 0', 'display 1')))
        self.assertIsNone(probe.rotation('mRotation=ROTATION_0\nmRotation=ROTATION_1'))
        self.assertIsNone(probe.rotation('user_rotation=0'))

    def test_actual_rotation_selects_display0_and_does_not_use_config_rotation(self):
        other = 'Display: mDisplayId=1 (organized)\n    mRotation=2 mDeferredRotationPauseCount=0'
        self.assertEqual(probe.rotation(ROTATION + '\n' + other), 0)
        self.assertEqual(probe.rotation(other + '\n' + ROTATION), 0)
        self.assertIsNone(probe.rotation(ROTATION + '\n' + ROTATION))
        self.assertIsNone(probe.rotation(ROTATION.replace('mRotation=0 mDeferredRotationPauseCount=0', '')))
        self.assertIsNone(probe.rotation(ROTATION + '\n    mRotation=3 mDeferredRotationPauseCount=0'))


class LocalClientBounds(unittest.TestCase):
    def fake_client(self, folder, body):
        executable = Path(folder)/'fake-client'
        executable.write_text('#!/usr/bin/env python3\n' + textwrap.dedent(body))
        executable.chmod(0o700)
        return executable

    def test_large_stdout_is_refused_and_owned_client_reaped(self):
        with tempfile.TemporaryDirectory() as folder:
            executable = self.fake_client(folder, 'import os\nos.write(1, b"x" * 8192)\n')
            reader = probe.ReadOnlyAdb(executable, 'emulator-5556')
            self.assertEqual(reader.read(('get-state',), 512), ('output_limit', ''))

    def test_timeout_and_stderr_never_export_private_details(self):
        with tempfile.TemporaryDirectory() as folder:
            executable = self.fake_client(folder, 'import sys, time\nsys.stderr.write("private-token")\ntime.sleep(5)\n')
            reader = probe.ReadOnlyAdb(executable, 'emulator-5556', query_seconds=.1)
            started = time.monotonic()
            self.assertEqual(reader.read(('get-state',), 512), ('query_timeout', ''))
            self.assertLess(time.monotonic() - started, 1.5)

    def test_nonzero_exit_and_invalid_encoding_are_fixed_statuses(self):
        with tempfile.TemporaryDirectory() as folder:
            executable = self.fake_client(folder, 'import sys\nprint("private-token")\nsys.exit(7)\n')
            self.assertEqual(probe.ReadOnlyAdb(executable, 'emulator-5556').read(('get-state',), 512), ('command_failed', ''))
            executable = self.fake_client(folder, 'import os\nos.write(1, b"\\xff")\n')
            self.assertEqual(probe.ReadOnlyAdb(executable, 'emulator-5556').read(('get-state',), 512), ('invalid_encoding', ''))

    def test_total_sample_deadline_skips_client_spawn(self):
        reader = probe.ReadOnlyAdb('/nonexistent-private-detail', 'emulator-5556', total_seconds=-1)
        self.assertEqual(reader.read(('get-state',), 512), ('sample_deadline', ''))


if __name__ == '__main__':
    unittest.main()
