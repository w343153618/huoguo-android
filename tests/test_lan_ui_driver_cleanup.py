"""Owned-process and private-input teardown, with no phone or host operations."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('lan_ui_driver', ROOT/'scripts/probes/run_authenticated_lan_ui.py')
DRIVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIVER)


class FakeChild:
    def __init__(self, timeouts=0):
        self.timeouts = timeouts
        self.returncode = None
        self.signals = []
        self.waits = []

    def poll(self):
        return self.returncode

    def terminate(self):
        self.signals.append('terminate')

    def kill(self):
        self.signals.append('kill')
        self.returncode = -9

    def communicate(self, timeout):
        self.waits.append(timeout)
        if self.timeouts:
            self.timeouts -= 1
            raise subprocess.TimeoutExpired('owned-child', timeout, output='private output')
        if self.returncode is None:
            self.returncode = -15
        return '', ''


class LanUiDriverCleanupCheck(unittest.TestCase):
    def invoke(self, folder, run, children=(), clock=None):
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(DRIVER.sys, 'argv', ['probe', '--output', str(folder)]))
            stack.enter_context(patch.object(DRIVER.subprocess, 'run', side_effect=run))
            spawned = stack.enter_context(patch.object(DRIVER.subprocess, 'Popen', side_effect=children))
            if clock is not None:
                stack.enter_context(patch.object(DRIVER.time, 'monotonic', side_effect=clock))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            exit_code = DRIVER.main()
        return exit_code, json.loads((folder/'ui-acceptance.json').read_text()), spawned.call_count

    def test_gate_timeout_removes_one_use_input_and_records_failed_cleanup(self):
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            if command[0] == 'lsof':
                raise subprocess.TimeoutExpired(command, kwargs['timeout'], output='sensitive output')
            return subprocess.CompletedProcess(command, 7, stdout='sensitive cleanup output', stderr='')
        with tempfile.TemporaryDirectory() as folder:
            code, report, spawned = self.invoke(Path(folder), run)
        self.assertEqual((code, spawned), (1, 0))
        self.assertEqual(report['driver_failure_class'], 'TimeoutExpired')
        self.assertEqual(report['cleanup_failures'], [{'operation':'remove_private_test_input','return_code':7}])
        self.assertIn('udp-test-login.json', calls[1][-1])
        self.assertNotIn('force-stop', calls[1][-1])
        self.assertNotIn('sensitive', json.dumps(report))

    def test_busy_gate_does_not_stop_any_app_but_removes_input(self):
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, stdout=b'123\n' if command[0]=='lsof' else '', stderr='')
        with tempfile.TemporaryDirectory() as folder:
            code, report, spawned = self.invoke(Path(folder), run)
        self.assertEqual((code, spawned), (1, 0))
        self.assertEqual(report['driver_failure_label'], 'formal_session_active')
        self.assertEqual(len(calls), 2)
        self.assertNotIn('force-stop', calls[-1][-1])

    def test_adb_preflight_error_still_removes_input_without_exception_text(self):
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            if command[0]=='lsof':
                return subprocess.CompletedProcess(command, 1, stdout=b'', stderr=b'')
            if 'test -s' in command[-1]:
                raise RuntimeError('do-not-record-secret')
            raise subprocess.TimeoutExpired(command, kwargs['timeout'], output='more private output')
        with tempfile.TemporaryDirectory() as folder:
            code, report, spawned = self.invoke(Path(folder), run)
        self.assertEqual((code, spawned), (1, 0))
        self.assertEqual(report['driver_failure_class'], 'RuntimeError')
        self.assertNotIn('driver_failure_label', report)
        self.assertEqual(report['cleanup_failures'][0]['failure_class'], 'TimeoutExpired')
        self.assertIn('udp-test-login.json', calls[-1][-1])
        self.assertNotIn('secret', json.dumps(report))

    def test_instrumentation_timeout_stops_only_isolated_packages_and_reaps_after_kill(self):
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 1 if command[0]=='lsof' else 0, stdout=b'' if command[0]=='lsof' else '', stderr='')
        child = FakeChild(timeouts=2)
        with tempfile.TemporaryDirectory() as folder:
            code, report, spawned = self.invoke(Path(folder), run, [child], clock=[0,121])
        self.assertEqual((code, spawned), (1, 1))
        self.assertTrue(report['timeout'])
        self.assertEqual(report['driver_failure_label'], 'instrumentation_timeout')
        stops = [command[-1] for command in calls if 'force-stop' in command[-1]]
        self.assertEqual(stops, ['am force-stop local.huoguo.lanuitest',
                                'am force-stop local.remoteandroid.direct.experiment'])
        self.assertEqual(child.signals, ['terminate','terminate','kill'])
        self.assertEqual(child.waits, [2,2,2])
        self.assertEqual(report['instrumentation_exit_code'], -9)
        self.assertIn('udp-test-login.json', calls[-1][-1])
        self.assertNotIn('private output', json.dumps(report))

    def test_phase_failure_reaps_both_already_started_samplers(self):
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 1 if command[0]=='lsof' else 0,
                                               stdout=b'' if command[0]=='lsof' else '', stderr='')
        children = [FakeChild(), FakeChild(), FakeChild()]
        with tempfile.TemporaryDirectory() as folder:
            code, report, spawned = self.invoke(Path(folder), run, children, clock=[0,1,2,7])
        self.assertEqual((code, spawned), (1, 3))
        self.assertEqual(report['driver_failure_label'], 'guest_receipt_not_focused')
        self.assertTrue(report['phone_sampler_started'])
        for child in children:
            self.assertEqual(child.signals, ['terminate'])
            self.assertEqual(len(child.waits), 1)
        self.assertEqual(report['sampler_exit_codes'], [-15,-15])
        self.assertIn('udp-test-login.json', calls[-1][-1])


if __name__ == '__main__':
    unittest.main()
