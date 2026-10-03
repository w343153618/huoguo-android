"""Bounded first-capture prefix from a fresh, owner-private trace directory.

This reads diagnostic metadata only. It neither starts media nor replaces the
continuous owner admission/quiescence contract. No historical file discovery.
"""
import os
from pathlib import Path
import re
import stat

MAX_PREFIX_BYTES = 65536
MAX_COMPLETE_LINES = 256
LABELS = frozenset((
    'source_trace_root_invalid', 'source_trace_root_not_empty',
    'source_trace_attempt_invalid', 'source_trace_file_invalid',
    'source_trace_identity_changed', 'source_trace_prefix_bound',
    'source_trace_read_failed', 'source_trace_reader_closed',
))


class SourceTraceError(ValueError):
    def __init__(self, label):
        super().__init__(label if label in LABELS else 'source_trace_read_failed')


def _identity(info):
    return info.st_dev, info.st_ino


def _private(info, directory):
    return (stat.S_ISDIR(info.st_mode) if directory else
            stat.S_ISREG(info.st_mode) and info.st_nlink == 1) and (
        info.st_uid == os.geteuid() and
        stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600))


def _open_path(directory):
    """Walk from / through directory FDs; no parent symlink is followed."""
    path = Path(directory).absolute()
    if '..' in path.parts:
        raise SourceTraceError('source_trace_root_invalid')
    descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in path.parts[1:]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=descriptor)
            old, descriptor = descriptor, child
            os.close(old)
        result, descriptor = descriptor, None
        return path, result
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _cleanup_preserving_primary(reader, primary):
    try:
        reader.close()
    except SourceTraceError:
        # Never let descriptor cleanup erase the original admission failure.
        if hasattr(primary, 'add_note'):
            primary.add_note('source_trace_cleanup_failed')


class TracePrefixReader:
    """Pin only this attempt's first directory/file; never switch on reconnect."""
    def __init__(self, directory):
        self.root_fd = self.attempt_fd = self.file_fd = None
        self.attempt_name = None
        self.attempt_identity = self.file_identity = None
        try:
            self.root_path, self.root_fd = _open_path(directory)
            actual = os.fstat(self.root_fd)
            if not _private(actual, True):
                raise SourceTraceError('source_trace_root_invalid')
            self.root_identity = _identity(actual)
            if os.listdir(self.root_fd):
                raise SourceTraceError('source_trace_root_not_empty')
        except BaseException as error:
            _cleanup_preserving_primary(self, error)
            if isinstance(error, SourceTraceError):
                raise
            if not isinstance(error, Exception):
                raise
            raise SourceTraceError('source_trace_root_invalid') from None

    def read(self):
        if self.root_fd is None:
            raise SourceTraceError('source_trace_reader_closed')
        try:
            _, current_root = _open_path(self.root_path)
            try:
                current_info = os.fstat(current_root)
                if (_identity(current_info) != self.root_identity or
                        not _private(current_info, True)):
                    raise SourceTraceError('source_trace_identity_changed')
            finally:
                os.close(current_root)
            names = os.listdir(self.root_fd)
            if not names:
                if self.attempt_fd is not None:
                    raise SourceTraceError('source_trace_identity_changed')
                return b''
            if len(names) != 1 or not re.fullmatch(r'attempt-[a-f0-9]{16}', names[0]):
                raise SourceTraceError('source_trace_attempt_invalid')
            name = names[0]
            info = os.stat(name, dir_fd=self.root_fd, follow_symlinks=False)
            if not _private(info, True):
                raise SourceTraceError('source_trace_attempt_invalid')
            if self.attempt_fd is None:
                self.attempt_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                          dir_fd=self.root_fd)
                opened = os.fstat(self.attempt_fd)
                if _identity(opened) != _identity(info) or not _private(opened, True):
                    raise SourceTraceError('source_trace_identity_changed')
                self.attempt_identity, self.attempt_name = _identity(opened), name
            if name != self.attempt_name or _identity(info) != self.attempt_identity:
                raise SourceTraceError('source_trace_identity_changed')
            try:
                info = os.stat('capture.jsonl', dir_fd=self.attempt_fd, follow_symlinks=False)
            except FileNotFoundError:
                if self.file_fd is not None:
                    raise SourceTraceError('source_trace_identity_changed')
                return b''
            if not _private(info, False):
                raise SourceTraceError('source_trace_file_invalid')
            if self.file_fd is None:
                self.file_fd = os.open('capture.jsonl', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                       dir_fd=self.attempt_fd)
                opened = os.fstat(self.file_fd)
                if _identity(opened) != _identity(info) or not _private(opened, False):
                    raise SourceTraceError('source_trace_identity_changed')
                self.file_identity = _identity(opened)
            if _identity(info) != self.file_identity:
                raise SourceTraceError('source_trace_identity_changed')
            # Always the prefix of the pinned file, not a tail/historical scan.
            data = os.pread(self.file_fd, MAX_PREFIX_BYTES, 0)
            boundary = data.rfind(b'\n')
            if boundary < 0:
                if len(data) == MAX_PREFIX_BYTES:
                    raise SourceTraceError('source_trace_prefix_bound')
                return b''
            complete = data[:boundary+1]
            if complete.count(b'\n') > MAX_COMPLETE_LINES:
                raise SourceTraceError('source_trace_prefix_bound')
            return complete
        except SourceTraceError:
            raise
        except (OSError, ValueError, TypeError):
            raise SourceTraceError('source_trace_read_failed') from None

    def close(self):
        failure = None
        for field in ('file_fd', 'attempt_fd', 'root_fd'):
            descriptor = getattr(self, field, None)
            setattr(self, field, None)
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    failure = SourceTraceError('source_trace_read_failed')
        if failure is not None:
            raise failure

    def __enter__(self):
        return self

    def __exit__(self, error_type, error, traceback):
        if error is None:
            self.close()
        else:
            _cleanup_preserving_primary(self, error)
