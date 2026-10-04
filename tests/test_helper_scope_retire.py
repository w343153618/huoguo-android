"""Actual host FD lifecycle fixtures; synthetic Android UID/DAC boundary.

No Android execution, APK bytes, phone, PM or reservation release. The host
macro uses the test user's UID/GID; it does not establish Android root sealing.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'experiments/moonlight-v2/source-snapshot/helper_scope_retire.c'


class HelperScopeRetireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = shutil.which('clang') or shutil.which('cc')
        if cls.compiler is None:
            raise RuntimeError('helper_retirement_host_compiler_required')
        cls.build = tempfile.TemporaryDirectory(prefix='huoguo-helper-retire-host-')
        cls.fixture = Path(cls.build.name) / 'fixture'
        cls.production = Path(cls.build.name) / 'production'
        for binary, macros in ((cls.fixture, ['-DHG_HELPER_RETIRE_FIXTURE']),
                               (cls.production, [])):
            subprocess.run([cls.compiler, '-std=c11', '-O2', '-Wall', '-Wextra',
                            '-Werror', *macros, str(SOURCE), '-o', str(binary)],
                           check=True, capture_output=True, timeout=20)
        cls.fixture.chmod(0o700)
        cls.production.chmod(0o700)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix='huoguo-helper-retire-scope-')
        self.parent = Path(self.area.name) / ('huoguo-helper-root-' + 'a' * 24)
        self.parent.mkdir(mode=0o710)
        self.parent.chmod(0o710)
        self.stage = self.parent / 'stage'
        self.stage.mkdir(mode=0o700)
        self.apk = self.stage / 'owned.apk'
        self.content = b'public host fixture, never a signed APK\n' * 9
        self.write(self.content)
        self.capture()

    def tearDown(self):
        # Host-only test-owned scopes. Production has no recursive deletion.
        self.area.cleanup()

    def write(self, content):
        self.apk.write_bytes(content)
        self.apk.chmod(0o600)

    def capture(self):
        b, p, d, f = (path.stat() for path in (self.parent.parent, self.parent, self.stage, self.apk))
        self.args = ['--retire', str(self.parent), str(b.st_dev), str(b.st_ino), str(p.st_dev), str(p.st_ino),
                     str(d.st_dev), str(d.st_ino), str(f.st_dev), str(f.st_ino),
                     str(f.st_size), hashlib.sha256(self.apk.read_bytes()).hexdigest()]

    def run_kernel(self, args=None, *, hook=None, production=False):
        env = dict(os.environ)
        env.pop('HG_HELPER_FIXTURE_HOOK', None)
        if hook is not None:
            env['HG_HELPER_FIXTURE_HOOK'] = hook
        # These are actual bounded host children. No shell, inherited input or
        # Android client is involved; subprocess timeout is a host fixture only.
        result = subprocess.run([str(self.production if production else self.fixture),
                                 *(self.args if args is None else args)],
                                stdin=subprocess.DEVNULL, capture_output=True,
                                timeout=3, env=env)
        value = json.loads(result.stdout) if result.stdout else None
        return result, value

    def refused_preserved(self, *, hook=None):
        result, receipt = self.run_kernel(hook=hook)
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNotNone(receipt)
        self.assertFalse(receipt['completed'])
        self.assertTrue(receipt['scope_may_remain'])
        self.assertFalse(receipt['release_authorized'])
        self.assertFalse(receipt['remote_PM_quiescence'])
        self.assertTrue(self.stage.is_dir())
        return receipt

    def test_default_inert_both_builds(self):
        for production in (False, True):
            result, receipt = self.run_kernel([], production=production)
            self.assertEqual(result.returncode, 0)
            self.assertFalse(receipt['operations_started'])
            self.assertFalse(receipt['release_authorized'])
            self.assertEqual(self.apk.read_bytes(), self.content)

    def test_exact_scope_success_preserves_base_and_no_release(self):
        result, receipt = self.run_kernel()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(receipt['completed'])
        self.assertTrue(receipt['fixture_build'])
        self.assertTrue(receipt['directory_sealed'])
        self.assertTrue(receipt['APK_unlinked'])
        self.assertTrue(receipt['stage_removed'])
        self.assertFalse(receipt['atomic_unlink_claimed'])
        self.assertFalse(receipt['remote_PM_quiescence'])
        self.assertFalse(receipt['release_authorized'])
        self.assertFalse(receipt['scope_may_remain'])
        self.assertTrue(receipt['protected_parent_removed'])
        self.assertFalse(self.parent.exists())
        self.assertTrue(Path(self.area.name).is_dir())

    def test_sha256_reference_block_boundaries(self):
        # Independent hashlib reference, including SHA padding and 4KiB reads.
        for size in (1, 55, 56, 63, 64, 65, 127, 128, 4095, 4096, 4097, 131072):
            with self.subTest(size=size):
                if not self.stage.exists():
                    self.parent.mkdir(mode=0o710)
                    self.parent.chmod(0o710)
                    self.stage.mkdir(mode=0o700)
                self.write(bytes(i % 251 for i in range(size)))
                self.capture()
                result, receipt = self.run_kernel()
                self.assertEqual(result.returncode, 0, (size, receipt, result.stderr))
                self.assertTrue(receipt['completed'])

    def test_wrong_inode_each_bound_node_preserves(self):
        for index in (3, 5, 7, 9):
            with self.subTest(index=index):
                args = self.args.copy()
                args[index] = str(int(args[index]) + 1)
                result, receipt = self.run_kernel(args)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(receipt['completed'])
                self.assertFalse(receipt['directory_sealed'])
                self.assertEqual(self.apk.read_bytes(), self.content)

    def test_wrong_device_each_bound_node_preserves(self):
        for index in (2, 4, 6, 8):
            args = self.args.copy()
            args[index] = str(int(args[index]) + 1)
            result, receipt = self.run_kernel(args)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(receipt['completed'])
            self.assertEqual(self.apk.read_bytes(), self.content)

    def test_writable_parent_old_layout_cannot_retire(self):
        self.parent.chmod(0o770)
        receipt = self.refused_preserved()
        self.assertEqual(receipt['failure'], 'protected_parent_required')
        self.assertFalse(receipt['directory_sealed'])
        self.assertEqual(self.apk.read_bytes(), self.content)

    def test_writable_base_old_tmp_layout_cannot_retire(self):
        Path(self.area.name).chmod(0o770)
        receipt = self.refused_preserved()
        self.assertEqual(receipt['failure'], 'protected_base_required')
        self.assertFalse(receipt['directory_sealed'])
        self.assertEqual(self.apk.read_bytes(), self.content)

    def test_wrong_stage_or_file_mode_preserves(self):
        for path, mode, original in ((self.stage, 0o750, 0o700),
                                      (self.apk, 0o640, 0o600)):
            path.chmod(mode)
            receipt = self.refused_preserved()
            self.assertFalse(receipt['directory_sealed'])
            self.assertEqual(self.apk.read_bytes(), self.content)
            path.chmod(original)

    def test_wrong_hash_or_size_refuses_before_sealing(self):
        for index, value in ((11, '0' * 64), (10, str(len(self.content) + 1))):
            args = self.args.copy()
            args[index] = value
            result, receipt = self.run_kernel(args)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(receipt['directory_sealed'])
            self.assertEqual(self.apk.read_bytes(), self.content)

    def test_extra_child_entry_preserved(self):
        extra = self.stage / 'foreign'
        extra.write_bytes(b'foreign')
        self.refused_preserved()
        self.assertEqual(extra.read_bytes(), b'foreign')
        self.assertEqual(self.apk.read_bytes(), self.content)

    def test_extra_parent_entry_preserved(self):
        extra = self.parent / 'foreign'
        extra.write_bytes(b'foreign')
        self.refused_preserved()
        self.assertEqual(extra.read_bytes(), b'foreign')
        self.assertEqual(self.apk.read_bytes(), self.content)

    def test_hardlinked_apk_refused(self):
        other = Path(self.area.name) / 'other'
        os.link(self.apk, other)
        self.refused_preserved()
        self.assertEqual(other.read_bytes(), self.content)

    def test_symlink_apk_does_not_touch_target(self):
        other = Path(self.area.name) / 'other'
        self.apk.rename(other)
        self.apk.symlink_to(other)
        self.refused_preserved()
        self.assertTrue(self.apk.is_symlink())
        self.assertEqual(other.read_bytes(), self.content)

    def test_symlink_stage_does_not_touch_target(self):
        other = self.parent / 'elsewhere'
        self.stage.rename(other)
        self.stage.symlink_to(other, target_is_directory=True)
        result, receipt = self.run_kernel()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(receipt['completed'])
        self.assertTrue(self.stage.is_symlink())
        self.assertEqual((other / 'owned.apk').read_bytes(), self.content)

    def test_symlink_parent_does_not_touch_target(self):
        other = Path(self.area.name) / 'elsewhere'
        self.parent.rename(other)
        self.parent.symlink_to(other, target_is_directory=True)
        result, receipt = self.run_kernel()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(receipt['completed'])
        self.assertTrue(self.parent.is_symlink())
        self.assertEqual((other / 'stage/owned.apk').read_bytes(), self.content)

    def test_symlink_base_does_not_touch_target(self):
        base = Path(self.area.name) / 'base'
        base.mkdir(mode=0o700)
        self.parent.rename(base / self.parent.name)
        self.parent = base / self.parent.name
        self.stage = self.parent / 'stage'
        self.apk = self.stage / 'owned.apk'
        self.capture()
        other = Path(self.area.name) / 'other-base'
        base.rename(other)
        base.symlink_to(other, target_is_directory=True)
        result, receipt = self.run_kernel()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(receipt['failure'], 'protected_base_required')
        self.assertFalse(receipt['directory_sealing_started'])
        self.assertEqual((other / self.parent.name / 'stage/owned.apk').read_bytes(), self.content)

    def test_fifo_is_nonblocking_and_refused(self):
        self.apk.unlink()
        os.mkfifo(self.apk, 0o600)
        self.refused_preserved()
        self.assertTrue(stat.S_ISFIFO(self.apk.lstat().st_mode))

    def test_stage_replacement_is_not_deleted_or_reclaimed(self):
        receipt = self.refused_preserved(hook='stage_replace')
        self.assertFalse(receipt['directory_sealed'])
        self.assertTrue((self.stage / 'foreign').is_file())
        self.assertEqual((self.parent / 'held-original/owned.apk').read_bytes(), self.content)

    def test_file_replacement_is_not_deleted(self):
        receipt = self.refused_preserved(hook='file_replace')
        self.assertTrue(receipt['directory_sealed'])
        self.assertFalse(receipt['APK_unlinked'])
        self.assertEqual(self.apk.read_bytes(), b'foreign')

    def test_same_inode_growth_after_seal_is_bounded_and_preserved(self):
        receipt = self.refused_preserved(hook='file_growth_after_seal')
        self.assertTrue(receipt['directory_sealed'])
        self.assertTrue(receipt['file_sealing_started'])
        self.assertFalse(receipt['APK_unlinked'])
        self.assertEqual(self.apk.stat().st_size, 131073)
        self.assertEqual(self.apk.stat().st_ino, int(self.args[9]))

    def test_partial_unlink_with_foreign_entry_cannot_pass(self):
        receipt = self.refused_preserved(hook='foreign_after_unlink')
        self.assertTrue(receipt['APK_unlinked'])
        self.assertFalse(receipt['stage_removed'])
        self.assertFalse(self.apk.exists())
        self.assertTrue((self.stage / 'foreign').is_file())

    def test_closed_argument_rejection_no_operations(self):
        cases = []
        for index, value in ((0, '--delete'), (1, str(self.parent) + '/stage'),
                             (2, '-1'), (3, '0'), (10, '131073'),
                             (11, 'A' * 64)):
            args = self.args.copy()
            args[index] = value
            cases.append(args)
        cases.extend((self.args[:-1], self.args + ['extra']))
        for args in cases:
            result, receipt = self.run_kernel(args)
            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(receipt)
            self.assertEqual(self.apk.read_bytes(), self.content)

    def test_production_cannot_use_host_fixture_path_or_hook(self):
        result, receipt = self.run_kernel(hook='stage_replace', production=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(receipt)
        self.assertEqual(self.apk.read_bytes(), self.content)


if __name__ == '__main__':
    unittest.main()
