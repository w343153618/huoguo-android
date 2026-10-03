"""Owned, inert file fixtures only; no device, socket, media or production IO."""
import errno
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from scripts.probes import owner_trace_prefix as prefix


PRIVATE = 'SYNTHETIC_PRIVATE_TEXT_MUST_NOT_EXPORT'
ATTEMPT = 'attempt-' + 'a' * 16
OTHER_ATTEMPT = 'attempt-' + 'b' * 16


def changed_metadata(info, **changes):
    values = {name: getattr(info, name) for name in
              ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_nlink', 'st_size')}
    values.update(changes)
    return SimpleNamespace(**values)


class OwnerTracePrefixTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='owned-source-prefix-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.directory = self.base / 'trace'
        self.directory.mkdir(mode=0o700)
        self.directory.chmod(0o700)

    def reader(self, directory=None):
        value = prefix.TracePrefixReader(directory or self.directory)
        self.addCleanup(value.close)
        return value

    def attempt(self, name=ATTEMPT, directory=None):
        path = (directory or self.directory) / name
        path.mkdir(mode=0o700)
        path.chmod(0o700)
        return path

    def capture(self, attempt, data=b'{}\n'):
        path = attempt / 'capture.jsonl'
        path.write_bytes(data)
        path.chmod(0o600)
        return path

    def constructor_refused(self, path, label):
        value = None
        try:
            with self.assertRaisesRegex(prefix.SourceTraceError, '^' + label + '$'):
                value = prefix.TracePrefixReader(path)
        finally:
            if value is not None:
                value.close()

    def read_refused(self, reader, label=None):
        with self.assertRaises(prefix.SourceTraceError) as caught:
            reader.read()
        text = str(caught.exception)
        self.assertIn(text, prefix.LABELS)
        self.assertNotIn(PRIVATE, text)
        if label is not None:
            self.assertEqual(text, label)

    def assert_no_fds(self, reader, descriptors):
        self.assertIsNone(reader.root_fd)
        self.assertIsNone(reader.attempt_fd)
        self.assertIsNone(reader.file_fd)
        for descriptor in set(descriptors):
            with self.assertRaises(OSError) as caught:
                os.fstat(descriptor)
            self.assertEqual(caught.exception.errno, errno.EBADF)

    def test_empty_owned_0700_root_waits_without_discovery_or_child_open(self):
        reader = self.reader()
        self.assertEqual(reader.read(), b'')
        self.assertIsNone(reader.attempt_fd)
        self.assertIsNone(reader.file_fd)

    def test_root_exact_mode_and_owner_are_required(self):
        for mode in (0o500, 0o750, 0o770, 0o777):
            with self.subTest(mode=mode):
                self.directory.chmod(mode)
                self.constructor_refused(self.directory, 'source_trace_root_invalid')
        self.directory.chmod(0o700)
        with mock.patch.object(prefix.os, 'geteuid', return_value=os.geteuid() + 1):
            self.constructor_refused(self.directory, 'source_trace_root_invalid')

    def test_nonempty_root_refuses_even_a_plausible_historical_attempt(self):
        self.attempt()
        self.constructor_refused(self.directory, 'source_trace_root_not_empty')

    def test_root_file_and_leaf_symlink_are_refused(self):
        ordinary = self.base / PRIVATE
        ordinary.write_bytes(b'fixture')
        self.constructor_refused(ordinary, 'source_trace_root_invalid')
        alias = self.base / 'alias'
        alias.symlink_to(self.directory, target_is_directory=True)
        self.constructor_refused(alias, 'source_trace_root_invalid')

    def test_existing_scaffold_symlink_is_not_followed(self):
        scaffold = self.base / 'scaffold'
        scaffold.mkdir()
        root = scaffold / 'trace'
        root.mkdir(mode=0o700)
        alias = self.base / 'alias'
        alias.symlink_to(scaffold, target_is_directory=True)
        self.constructor_refused(alias / 'trace', 'source_trace_root_invalid')

    def test_parent_traversal_refuses_before_open(self):
        with mock.patch.object(prefix.os, 'open', side_effect=AssertionError('no traversal open')):
            self.constructor_refused(self.directory / '..' / 'trace', 'source_trace_root_invalid')

    def test_scaffold_symlink_swap_between_check_and_open_is_refused(self):
        scaffold = self.base / 'scaffold'
        scaffold.mkdir()
        root = scaffold / 'trace'
        root.mkdir(mode=0o700)
        moved = self.base / 'moved-scaffold'
        real_open = os.open
        opened = []
        swapped = False

        def open_after_swap(path, flags, *args, **kwargs):
            nonlocal swapped
            if not swapped and os.fspath(path) in (str(root), 'scaffold'):
                scaffold.rename(moved)
                scaffold.symlink_to(moved, target_is_directory=True)
                swapped = True
            fd = real_open(path, flags, *args, **kwargs)
            opened.append(fd)
            return fd

        with mock.patch.object(prefix.os, 'open', side_effect=open_after_swap):
            self.constructor_refused(root, 'source_trace_root_invalid')
        self.assertTrue(swapped)
        for fd in set(opened):
            with self.assertRaises(OSError) as caught:
                os.fstat(fd)
            self.assertEqual(caught.exception.errno, errno.EBADF)

    def test_single_new_attempt_and_missing_capture_wait_without_scanning(self):
        reader = self.reader()
        directory = self.attempt()
        (directory / 'old-capture.jsonl').write_bytes(PRIVATE.encode() + b'\n')
        with mock.patch.object(prefix.os, 'pread', wraps=os.pread) as reads:
            self.assertEqual(reader.read(), b'')
        reads.assert_not_called()
        self.assertEqual(reader.attempt_name, ATTEMPT)
        self.assertIsNone(reader.file_fd)

    def test_attempt_name_count_mode_and_symlink_are_fixed(self):
        cases = ('bad-name', 'extra-attempt', 'private-mode', 'symlink')
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory(dir=self.base) as owned:
                root = Path(owned)
                root.chmod(0o700)
                reader = self.reader(root)
                if case == 'bad-name':
                    self.attempt('attempt-XYZ', root)
                elif case == 'extra-attempt':
                    self.attempt(ATTEMPT, root)
                    self.attempt(OTHER_ATTEMPT, root)
                elif case == 'private-mode':
                    self.attempt(ATTEMPT, root).chmod(0o500)
                else:
                    target = self.base / 'outside-attempt'
                    target.mkdir(mode=0o700)
                    (root / ATTEMPT).symlink_to(target, target_is_directory=True)
                self.read_refused(reader, 'source_trace_attempt_invalid')
                reader.close()

    def test_attempt_foreign_owner_metadata_is_refused(self):
        reader = self.reader()
        self.attempt()
        real_stat = os.stat

        def foreign(path, *args, **kwargs):
            info = real_stat(path, *args, **kwargs)
            return changed_metadata(info, st_uid=os.geteuid() + 1) if path == ATTEMPT else info

        with mock.patch.object(prefix.os, 'stat', side_effect=foreign):
            self.read_refused(reader, 'source_trace_attempt_invalid')

    def test_file_exact_0600_and_private_owner_are_required(self):
        reader = self.reader()
        path = self.capture(self.attempt())
        for mode in (0o400, 0o640, 0o666):
            with self.subTest(mode=mode):
                path.chmod(mode)
                self.read_refused(reader, 'source_trace_file_invalid')
        path.chmod(0o600)
        real_stat = os.stat

        def foreign(path, *args, **kwargs):
            info = real_stat(path, *args, **kwargs)
            return changed_metadata(info, st_uid=os.geteuid() + 1) if path == 'capture.jsonl' else info

        with mock.patch.object(prefix.os, 'stat', side_effect=foreign):
            self.read_refused(reader, 'source_trace_file_invalid')

    def test_capture_symlink_hardlink_fifo_and_directory_are_refused_before_read(self):
        for kind in ('symlink', 'hardlink', 'fifo', 'directory'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(dir=self.base) as owned:
                root = Path(owned)
                root.chmod(0o700)
                reader = self.reader(root)
                attempt = self.attempt(ATTEMPT, root)
                path = attempt / 'capture.jsonl'
                if kind in ('symlink', 'hardlink'):
                    target = self.base / ('outside-' + kind)
                    target.write_bytes(PRIVATE.encode())
                    target.chmod(0o600)
                    path.symlink_to(target) if kind == 'symlink' else os.link(target, path)
                elif kind == 'fifo':
                    os.mkfifo(path, 0o600)
                else:
                    path.mkdir(mode=0o700)
                with mock.patch.object(prefix.os, 'pread', side_effect=AssertionError('no content read')):
                    self.read_refused(reader, 'source_trace_file_invalid')
                self.assertIsNone(reader.file_fd)
                reader.close()

    def test_partial_tail_waits_and_repeated_reads_use_only_offset_zero(self):
        reader = self.reader()
        path = self.capture(self.attempt(), b'{"first":1')
        self.assertEqual(reader.read(), b'')
        path.write_bytes(b'{"first":1}\n{"second":2')
        with mock.patch.object(prefix.os, 'pread', wraps=os.pread) as reads:
            self.assertEqual(reader.read(), b'{"first":1}\n')
            path.write_bytes(b'{"first":1}\n{"second":2}\n')
            self.assertEqual(reader.read(), b'{"first":1}\n{"second":2}\n')
            self.assertEqual(reader.read(), b'{"first":1}\n{"second":2}\n')
        self.assertEqual(reads.call_count, 3)
        for call in reads.call_args_list:
            self.assertEqual(call.args[1:], (prefix.MAX_PREFIX_BYTES, 0))

    def test_byte_and_complete_line_caps_are_bounded_without_tail_scans(self):
        reader = self.reader()
        path = self.capture(self.attempt(), b'x' * prefix.MAX_PREFIX_BYTES)
        self.read_refused(reader, 'source_trace_prefix_bound')
        path.write_bytes(b'x' * (prefix.MAX_PREFIX_BYTES - 1) + b'\n')
        self.assertEqual(len(reader.read()), prefix.MAX_PREFIX_BYTES)
        path.write_bytes(b'{}\n' * prefix.MAX_COMPLETE_LINES + b'partial')
        self.assertEqual(reader.read(), b'{}\n' * prefix.MAX_COMPLETE_LINES)
        path.write_bytes(b'{}\n' * (prefix.MAX_COMPLETE_LINES + 1))
        self.read_refused(reader, 'source_trace_prefix_bound')
        # A newline beyond the bounded prefix is never discovered by seeking.
        path.write_bytes(b'{}\n' + b'x' * prefix.MAX_PREFIX_BYTES + b'\n')
        self.assertEqual(reader.read(), b'{}\n')

    def test_second_attempt_never_replaces_the_pinned_first_one(self):
        reader = self.reader()
        first = self.attempt()
        self.capture(first, b'first\n')
        self.assertEqual(reader.read(), b'first\n')
        self.capture(self.attempt(OTHER_ATTEMPT), b'second\n')
        with mock.patch.object(prefix.os, 'pread', side_effect=AssertionError('no second attempt read')):
            self.read_refused(reader, 'source_trace_attempt_invalid')
        self.assertEqual(reader.attempt_name, ATTEMPT)

    def test_attempt_rename_delete_and_inode_swap_are_refused(self):
        for change in ('rename', 'delete', 'swap'):
            with self.subTest(change=change), tempfile.TemporaryDirectory(dir=self.base) as owned:
                root = Path(owned)
                root.chmod(0o700)
                reader = self.reader(root)
                attempt = self.attempt(ATTEMPT, root)
                path = self.capture(attempt)
                self.assertEqual(reader.read(), b'{}\n')
                if change == 'rename':
                    attempt.rename(root / OTHER_ATTEMPT)
                elif change == 'delete':
                    path.unlink()
                    attempt.rmdir()
                else:
                    attempt.rename(self.base / 'old-attempt')
                    self.capture(self.attempt(ATTEMPT, root), b'new\n')
                with mock.patch.object(prefix.os, 'pread', side_effect=AssertionError('no changed inode read')):
                    self.read_refused(reader, 'source_trace_identity_changed')
                reader.close()

    def test_capture_rename_delete_and_inode_swap_are_refused(self):
        for change in ('rename', 'delete', 'swap'):
            with self.subTest(change=change), tempfile.TemporaryDirectory(dir=self.base) as owned:
                root = Path(owned)
                root.chmod(0o700)
                reader = self.reader(root)
                attempt = self.attempt(ATTEMPT, root)
                path = self.capture(attempt)
                self.assertEqual(reader.read(), b'{}\n')
                if change == 'delete':
                    path.unlink()
                else:
                    path.rename(attempt / 'old-capture.jsonl')
                    if change == 'swap':
                        self.capture(attempt, b'new\n')
                with mock.patch.object(prefix.os, 'pread', side_effect=AssertionError('no changed inode read')):
                    self.read_refused(reader, 'source_trace_identity_changed')
                reader.close()

    def test_root_path_rename_delete_or_swap_is_refused_before_prefix_read(self):
        for change in ('rename', 'delete', 'swap'):
            with self.subTest(change=change), tempfile.TemporaryDirectory(dir=self.base) as owned:
                root = Path(owned) / 'trace'
                root.mkdir(mode=0o700)
                reader = self.reader(root)
                if change == 'delete':
                    root.rmdir()
                else:
                    root.rename(Path(owned) / 'moved')
                    if change == 'swap':
                        root.mkdir(mode=0o700)
                with mock.patch.object(prefix.os, 'pread', side_effect=AssertionError('no replaced root read')):
                    self.read_refused(reader)
                reader.close()

    def test_scaffold_changed_to_symlink_after_root_pin_is_not_followed(self):
        scaffold = self.base / 'scaffold'
        scaffold.mkdir()
        root = scaffold / 'trace'
        root.mkdir(mode=0o700)
        reader = self.reader(root)
        scaffold.rename(self.base / 'moved-scaffold')
        scaffold.symlink_to(self.base / 'moved-scaffold', target_is_directory=True)
        with mock.patch.object(prefix.os, 'pread', side_effect=AssertionError('no symlink scaffold read')):
            self.read_refused(reader)

    def test_attempt_swap_during_open_cannot_pass_preopen_identity(self):
        reader = self.reader()
        attempt = self.attempt()
        real_open = os.open
        swapped = False

        def swap(pathname, flags, *args, **kwargs):
            nonlocal swapped
            if pathname == ATTEMPT and not swapped:
                attempt.rename(self.base / 'old-attempt')
                self.attempt()
                swapped = True
            return real_open(pathname, flags, *args, **kwargs)

        with mock.patch.object(prefix.os, 'open', side_effect=swap), \
                mock.patch.object(prefix.os, 'pread', side_effect=AssertionError('no swapped attempt read')):
            self.read_refused(reader, 'source_trace_identity_changed')
        self.assertTrue(swapped)

    def test_file_swap_during_open_cannot_pass_the_preopen_inode_check(self):
        reader = self.reader()
        attempt = self.attempt()
        path = self.capture(attempt)
        real_open = os.open
        swapped = False

        def swap(pathname, flags, *args, **kwargs):
            nonlocal swapped
            if pathname == 'capture.jsonl' and not swapped:
                path.rename(attempt / 'old-capture.jsonl')
                self.capture(attempt, b'new\n')
                swapped = True
            return real_open(pathname, flags, *args, **kwargs)

        with mock.patch.object(prefix.os, 'open', side_effect=swap), \
                mock.patch.object(prefix.os, 'pread', side_effect=AssertionError('no swapped file read')):
            self.read_refused(reader, 'source_trace_identity_changed')
        self.assertTrue(swapped)

    def test_foreign_opened_file_metadata_refuses_even_if_preopen_stat_was_valid(self):
        reader = self.reader()
        path = self.capture(self.attempt())
        wanted = (path.stat().st_dev, path.stat().st_ino)
        real_fstat = os.fstat

        def foreign(fd):
            info = real_fstat(fd)
            return changed_metadata(info, st_uid=os.geteuid() + 1) if (info.st_dev, info.st_ino) == wanted else info

        with mock.patch.object(prefix.os, 'fstat', side_effect=foreign), \
                mock.patch.object(prefix.os, 'pread', side_effect=AssertionError('no foreign file read')):
            self.read_refused(reader, 'source_trace_identity_changed')

    def test_os_errors_and_unknown_labels_never_export_arbitrary_text(self):
        self.assertEqual(str(prefix.SourceTraceError(PRIVATE)), 'source_trace_read_failed')
        reader = self.reader()
        self.capture(self.attempt())
        with mock.patch.object(prefix.os, 'pread', side_effect=OSError(PRIVATE)):
            self.read_refused(reader, 'source_trace_read_failed')

    def test_close_is_idempotent_and_read_after_close_has_a_fixed_label(self):
        reader = self.reader()
        self.capture(self.attempt())
        reader.read()
        descriptors = (reader.file_fd, reader.attempt_fd, reader.root_fd)
        reader.close()
        reader.close()
        self.assert_no_fds(reader, descriptors)
        self.read_refused(reader, 'source_trace_reader_closed')

    def test_close_failure_drops_all_fields_and_attempts_all_owned_descriptors(self):
        reader = self.reader()
        self.capture(self.attempt())
        reader.read()
        descriptors = (reader.file_fd, reader.attempt_fd, reader.root_fd)
        real_close = os.close
        closed = []

        def close_then_report(fd):
            real_close(fd)
            closed.append(fd)
            raise OSError(PRIVATE)

        with mock.patch.object(prefix.os, 'close', side_effect=close_then_report), \
                self.assertRaisesRegex(prefix.SourceTraceError, '^source_trace_read_failed$'):
            reader.close()
        self.assertCountEqual(closed, descriptors)
        self.assert_no_fds(reader, descriptors)
        reader.close()

    def test_context_close_failure_preserves_primary_and_no_owned_fd_remains(self):
        reader = self.reader()
        self.capture(self.attempt())
        reader.read()
        descriptors = (reader.file_fd, reader.attempt_fd, reader.root_fd)
        primary = prefix.SourceTraceError('source_trace_identity_changed')
        real_close = os.close

        def close_then_report(fd):
            real_close(fd)
            raise OSError(PRIVATE)

        with self.assertRaises(prefix.SourceTraceError) as caught:
            with mock.patch.object(prefix.os, 'close', side_effect=close_then_report):
                with reader:
                    raise primary
        self.assertIs(caught.exception, primary)
        self.assertNotIn(PRIVATE, ' '.join(getattr(primary, '__notes__', ())))
        self.assert_no_fds(reader, descriptors)

    def test_constructor_cleanup_failure_preserves_original_refusal(self):
        (self.directory / 'preexisting').write_bytes(b'owned')
        real_open, real_close = os.open, os.close
        opened = []

        def track_open(*args, **kwargs):
            fd = real_open(*args, **kwargs)
            opened.append(fd)
            return fd

        def close_then_report(fd):
            real_close(fd)
            raise OSError(PRIVATE)

        # Do not inject failure into intermediate scaffold closes: this fixture
        # targets cleanup after the constructor's nonempty-root primary error.
        real_listdir = os.listdir

        def fail_cleanup_after_listing(fd):
            value = real_listdir(fd)
            close_patch.start()
            return value

        close_patch = mock.patch.object(prefix.os, 'close', side_effect=close_then_report)
        try:
            with mock.patch.object(prefix.os, 'open', side_effect=track_open), \
                    mock.patch.object(prefix.os, 'listdir', side_effect=fail_cleanup_after_listing):
                self.constructor_refused(self.directory, 'source_trace_root_not_empty')
        finally:
            close_patch.stop()
        for fd in set(opened):
            with self.assertRaises(OSError) as caught:
                os.fstat(fd)
            self.assertEqual(caught.exception.errno, errno.EBADF)


if __name__ == '__main__':
    unittest.main()
