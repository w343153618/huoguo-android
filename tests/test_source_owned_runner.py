"""Actual host-owned fork lifetime, separate from Android/phone UI acceptance."""
import os
import contextlib
import io
import json
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from scripts.probes import source_owned_runner_protocol as p
from scripts.probes import build_source_owned_runner as builder

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'experiments/moonlight-v2/source-snapshot/owned_runner.c'
NONCE = '0123456789abcdef01234567'
SCOPE = 'huoguo-source-ui-' + NONCE


class OwnedRunnerHostChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.TemporaryDirectory(prefix='huoguo-owned-fork-host-')
        cls.binary = Path(cls.work.name) / 'host-fixture'
        compiler = shutil.which('cc')
        if not compiler:
            cls.work.cleanup()
            raise RuntimeError('host C compiler required for actual fork fixtures')
        result = subprocess.run([compiler, '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
            '-DHG_HOST_FIXTURE', str(SOURCE), '-o', str(cls.binary)],
            capture_output=True, text=True, timeout=20)
        if result.returncode:
            cls.work.cleanup()
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls): cls.work.cleanup()

    def environment_check(self, updates=None, remove=()):
        env = dict(os.environ)
        env.update(ANDROID_ART_ROOT='/apex/com.android.art',
                   ANDROID_I18N_ROOT='/apex/com.android.i18n',
                   ANDROID_TZDATA_ROOT='/apex/com.android.tzdata',
                   BOOTCLASSPATH='/apex/com.android.art/javalib/core-oj.jar:/system/framework/framework.jar',
                   DEX2OATBOOTCLASSPATH='/apex/com.android.art/javalib/core-oj.jar')
        env.update(updates or {})
        for key in remove: env.pop(key, None)
        with tempfile.TemporaryDirectory(prefix='huoguo-owned-env-') as folder:
            result = subprocess.run([str(self.binary), '--host-env-check'], cwd=folder,
                env=env, capture_output=True, text=True, timeout=2)
            self.assertEqual(list(Path(folder).iterdir()), [])
            self.assertEqual(result.stderr, '')
        return result

    def test_ART_projection_keeps_only_closed_core_runtime_fields(self):
        result = self.environment_check({'CLASSPATH': '/data/foreign.jar',
            'HOME': '/data/foreign', 'PATH': '/foreign', 'ANDROID_DATA': '/foreign',
            'SYSTEMSERVERCLASSPATH': '/data/private.jar', 'EXTRA_SECRET': 'do-not-forward'})
        self.assertEqual(result.returncode, 0)
        actual = dict(line.split('=', 1) for line in result.stdout.splitlines())
        self.assertEqual(set(actual), {'PATH', 'ANDROID_DATA', 'ANDROID_ROOT',
            'ANDROID_ART_ROOT', 'ANDROID_I18N_ROOT', 'ANDROID_TZDATA_ROOT',
            'BOOTCLASSPATH', 'DEX2OATBOOTCLASSPATH'})
        self.assertEqual(actual['PATH'], '/system/bin')
        self.assertEqual(actual['ANDROID_DATA'], '/data')
        self.assertEqual(actual['ANDROID_ROOT'], '/system')
        self.assertNotIn('do-not-forward', result.stdout)
        self.assertEqual(actual['BOOTCLASSPATH'],
            '/apex/com.android.art/javalib/core-oj.jar:/system/framework/framework.jar')

    def test_missing_or_foreign_ART_roots_fail_before_scope_or_fork(self):
        for key in ('ANDROID_ART_ROOT', 'ANDROID_I18N_ROOT', 'ANDROID_TZDATA_ROOT',
                    'BOOTCLASSPATH', 'DEX2OATBOOTCLASSPATH'):
            result = self.environment_check(remove=(key,))
            self.assertEqual(result.returncode, 78); self.assertEqual(result.stdout, '')
        for key in ('ANDROID_ART_ROOT', 'ANDROID_I18N_ROOT', 'ANDROID_TZDATA_ROOT'):
            for value in ('', '/data/private', '/apex/com.android.art/../art',
                          '/apex/com.android.art\n'):
                result = self.environment_check({key: value})
                self.assertEqual(result.returncode, 78); self.assertEqual(result.stdout, '')

    def test_classpath_traversal_expansion_empty_duplicates_and_foreign_roots_rejected(self):
        valid = '/system/framework/framework.jar'
        for value in ('', valid + ':', ':' + valid, valid + '::' + valid,
                      valid + ':' + valid, '/data/local/tmp/foreign.jar',
                      '/system/framework/../private.jar', '/system/framework/nested/private.jar',
                      '/system/framework/$HOME.jar', '/system/framework/$(id).jar',
                      '/system/framework/ok.jar\n', '/system/framework/ok.jar;id',
                      '/apex/com.android.art/javalib/../../foreign.jar',
                      '/apex/foreign/javalib/foreign.jar', '/system/framework/not-jar.so'):
            for key in ('BOOTCLASSPATH', 'DEX2OATBOOTCLASSPATH'):
                result = self.environment_check({key: value})
                self.assertEqual(result.returncode, 78); self.assertEqual(result.stdout, '')

    def test_classpath_byte_and_entry_limits_remain_bounded(self):
        for value in ('/system/framework/' + 'a' * 4096 + '.jar',
                      ':'.join('/system/framework/a%d.jar' % i for i in range(129))):
            self.assertEqual(self.environment_check({'BOOTCLASSPATH': value}).returncode, 78)

    def test_default_Android_environment_is_validated_before_owned_scope_creation(self):
        source = SOURCE.read_text().split('int main(', 1)[1]
        self.assertLess(source.index('if (prepare_art_environment(&env)) return 78; /* Before mkdir/fork. */'),
                        source.index('mkdirat(base, namespace'))
        self.assertIn('execve("/system/bin/uiautomator", args, environment)', SOURCE.read_text())
        self.assertNotIn('execve("/system/bin/uiautomator", args, environ)', SOURCE.read_text())

    def birth_wait(self, directory):
        scope = directory / SCOPE
        meta = scope.stat()
        self.assertEqual(meta.st_uid, os.getuid())
        self.assertEqual(meta.st_mode & 0o7777, 0o700)
        for name in ('started', 'waited', 'runner.log'):
            self.assertEqual((scope / name).stat().st_mode & 0o7777, 0o600)
        birth = p.started((scope / 'started').read_bytes(), expected_uid=os.getuid(),
            expected_device=meta.st_dev, expected_inode=meta.st_ino)
        result = p.waited((scope / 'waited').read_bytes(), birth)
        self.assertEqual(result['log_bytes'], (scope / 'runner.log').stat().st_size)
        self.assertFalse(result['remote_UI_quiescence_verified'])
        self.assertFalse(result['permission_lease_verified'])
        return birth, result

    def run_mode(self, mode, *, cancel=False):
        with tempfile.TemporaryDirectory(prefix='huoguo-owned-child-') as folder:
            root = Path(folder); root.chmod(0o700)
            began = time.monotonic()
            child = subprocess.Popen([str(self.binary), '--host-fixture', NONCE, mode],
                cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                if cancel:
                    deadline = time.monotonic() + 2
                    while not (root / SCOPE / 'started').exists():
                        if child.poll() is not None or time.monotonic() > deadline:
                            self.fail('own parent did not persist startup identity')
                        time.sleep(.002)
                    child.send_signal(signal.SIGTERM)  # actual Popen owner only
                out, err = child.communicate(timeout=3)
                self.assertEqual(out, b''); self.assertEqual(err, b'')
                self.assertLess(time.monotonic() - began, 2.5)
                birth, result = self.birth_wait(root)
                self.assertEqual(birth['parent_pid'], child.pid)
                self.assertTrue(result['main_child_wait_confirmed'])
                return child.returncode, result
            finally:
                if child.poll() is None:
                    child.terminate(); child.communicate(timeout=4)
                # The retained-pipe fixture's descendant exits on its own in
                # 500ms. No borrowed PID/group signal is used for it.
                if mode == 'retained-pipe': time.sleep(.55)

    def test_natural_zero_exit_is_own_wait_but_not_source_or_UI_acceptance(self):
        code, result = self.run_mode('exit0')
        self.assertEqual(code, 0); self.assertTrue(result['natural_zero_exit'])
        self.assertEqual(result['term_sent'], 0); self.assertEqual(result['kill_sent'], 0)

    def test_natural_nonzero_and_exec_failure_still_confirm_actual_child_wait(self):
        for mode, value in (('exit7', 7), ('execfail', 127)):
            code, result = self.run_mode(mode)
            self.assertEqual(code, 73); self.assertFalse(result['natural_zero_exit'])
            self.assertEqual(result['reason'], 0); self.assertEqual(result['exit_value'], value)
            self.assertEqual(result['exit_kind'], 1); self.assertEqual(result['kill_sent'], 0)

    def test_deadline_TERM_reaps_only_actual_fork_child(self):
        code, result = self.run_mode('term')
        self.assertEqual(code, 73); self.assertEqual(result['reason'], 1)
        self.assertEqual(result['exit_kind'], 2); self.assertEqual(result['exit_value'], signal.SIGTERM)
        self.assertEqual(result['term_sent'], 1); self.assertEqual(result['kill_sent'], 0)

    def test_ignored_TERM_escalates_then_reaps_before_parent_returns(self):
        code, result = self.run_mode('ignore-term')
        self.assertEqual(code, 73); self.assertEqual(result['reason'], 1)
        self.assertEqual(result['exit_value'], signal.SIGKILL)
        self.assertEqual(result['term_sent'], 1); self.assertEqual(result['kill_sent'], 1)

    def test_actual_parent_cancel_flag_uses_owned_loop_not_handler_signal(self):
        for _ in range(3):
            code, result = self.run_mode('term', cancel=True)
            self.assertEqual(code, 73); self.assertEqual(result['reason'], 2)
            self.assertFalse(result['natural_zero_exit'])

    def test_output_is_capped_and_overflow_not_success(self):
        code, result = self.run_mode('output')
        self.assertEqual(result['reason'], 3)
        # The child is reaped, but its remaining output may not reach EOF
        # within the existing drain deadline on a loaded host. Preserve the
        # stricter incomplete-output result instead of requiring quiescence.
        self.assertEqual(code, 73 if result['log_eof'] and not result['ownership_error'] else 72)
        self.assertLessEqual(result['log_bytes'], 8191)
        self.assertFalse(result['natural_zero_exit'])

    def test_failed_clock_cannot_prevent_owned_child_cancel_and_wait(self):
        code, result = self.run_mode('clockfail')
        self.assertIn(code, (72, 73)); self.assertEqual(result['reason'], 4)
        self.assertEqual(result['term_sent'], 1); self.assertEqual(result['kill_sent'], 1)
        self.assertFalse(result['natural_zero_exit'])

    def test_descendant_holding_pipe_cannot_be_sold_as_remote_quiescence(self):
        code, result = self.run_mode('retained-pipe')
        self.assertEqual(code, 72); self.assertEqual(result['log_eof'], 0)
        self.assertTrue(result['main_child_wait_confirmed'])
        self.assertFalse(result['natural_zero_exit'])
        self.assertEqual(result['term_sent'], 0); self.assertEqual(result['kill_sent'], 0)

    def test_foreign_arguments_namespace_alias_or_existing_scope_no_fork(self):
        with tempfile.TemporaryDirectory(prefix='huoguo-owned-reject-') as folder:
            root = Path(folder); root.chmod(0o700)
            for args in ([], ['--snapshot', NONCE, NONCE], ['--host-fixture', NONCE, 'sh'],
                         ['--host-fixture', '../foreign', 'exit0'],
                         ['--host-fixture', NONCE + '\n', 'exit0'],
                         ['--host-fixture', NONCE.upper(), 'exit0']):
                result = subprocess.run([str(self.binary), *args], cwd=root,
                    capture_output=True, timeout=2)
                self.assertEqual(result.returncode, 64)
                self.assertEqual(list(root.iterdir()), [])
            original = root / 'unrelated'; original.mkdir()
            (root / SCOPE).symlink_to(original, target_is_directory=True)
            result = subprocess.run([str(self.binary), '--host-fixture', NONCE, 'exit0'],
                cwd=root, capture_output=True, timeout=2)
            self.assertEqual(result.returncode, 67)
            self.assertEqual(list(original.iterdir()), [])

    def test_only_parent_actual_fork_ownership_and_no_unrelated_signal(self):
        sentinel = subprocess.Popen([shutil.which('python3'), '-c', 'import time;time.sleep(3)'])
        try:
            self.run_mode('ignore-term')
            self.assertIsNone(sentinel.poll())
        finally:
            sentinel.terminate(); sentinel.wait(timeout=3)
        source = SOURCE.read_text()
        self.assertIn('waitpid(child, &status, WNOHANG)', source)
        self.assertIn('if (child <= 1 || reaped) return -1', source)
        self.assertNotIn('kill(-', source)
        handler = source.split('static void cancel_handler', 1)[1].split('\n', 1)[0]
        self.assertNotIn('kill(', handler); self.assertNotIn('waitpid(', handler)


class OwnedRunnerProtocolChecks(unittest.TestCase):
    def test_default_build_is_inert_and_source_tree_destination_refused(self):
        with patch.object(builder, 'build', side_effect=AssertionError('unexpected build')), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(builder.main([]), 0)
        self.assertEqual(json.loads(output.getvalue())['device_operations'], 0)
        with self.assertRaises(ValueError): builder.build(ROOT / 'forbidden-owned-binary', Path('/inert/ndk'))
        self.assertFalse((ROOT / 'forbidden-owned-binary').exists())

    def test_numeric_receipts_require_exact_birth_and_fresh_file_identity(self):
        birth = p.started(b'1 123 98 124 2000 99 8 9\n', expected_uid=2000,
                          expected_device=8, expected_inode=9)
        row = b'1 123 98 124 2000 99 8 9 0 1 0 0 0 1 1 0 1 0 1000\n'
        result = p.waited(row, birth)
        self.assertTrue(result['natural_zero_exit'])
        self.assertFalse(result['permission_lease_verified'])
        for raw in (row * 2, row + b'x', row[:-1], row.replace(b'124', b'125'),
                    row.replace(b'8 9', b'8 10'), row.replace(b'0 1000', b'0 9223372036854775808')):
            with self.assertRaises(ValueError): p.waited(raw, birth)
        for uid in (True, -1, '2000'):
            with self.assertRaises(ValueError):
                p.started(b'1 123 98 124 2000 99 8 9\n', expected_uid=uid, expected_device=8, expected_inode=9)

    def test_impossible_flags_and_false_success_rejected(self):
        birth = p.started(b'1 123 98 124 2000 99 8 9\n', expected_uid=2000,
                          expected_device=8, expected_inode=9)
        values = [0, 1, 0, 0, 0, 1, 1, 0, 1, 0, 1000]
        for index, value in ((0, 9), (1, 9), (2, 256), (3, 2), (4, 1),
                             (5, 0), (6, 2), (7, 8192), (8, 2), (9, 1)):
            copy = values.copy(); copy[index] = value
            raw = ('1 123 98 124 2000 99 8 9 ' + ' '.join(map(str, copy)) + '\n').encode()
            with self.assertRaises(ValueError): p.waited(raw, birth)


if __name__ == '__main__': unittest.main()
