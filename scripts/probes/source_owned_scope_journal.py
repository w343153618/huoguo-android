"""Explicit local private journal, inert on construction; never starts device I/O.

Register a possibly-created namespace DURABLY before any remote command. A
failed write/check retains the file and must prevent launch. Observed receipt
consistency does not authorize release or removal. No deletion/recovery by PID.
"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat

from scripts.probes.source_snapshot_protocol import namespace

LIMIT = 2048
MAX_RECORDS = 128
RESULT_FLAGS = (
    'receipt_chain_consistent', 'expected_parent_identity_matched',
    'sole_child_serialization_identity_matched', 'main_child_natural_zero_wait_reported',
    'complete_runner_success_reported', 'five_file_metadata_consistent',
    'actual_execution_ownership_verified', 'remote_UI_quiescence_verified',
    'owned_scope_removed_verified', 'source_qualification_verified', 'permission_lease_verified',
)
UNVERIFIED = RESULT_FLAGS[6:]


def _encoded(value):
    raw = (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode('ascii')
    if len(raw) > LIMIT: raise ValueError('scope_journal_record_rejected')
    return raw


def _possible(nonce):
    return {'schema': 'owned-source-possible-scope-v1', 'nonce': nonce,
            'remote_scope': namespace(nonce), 'remote_scope_may_exist': True,
            'remote_UI_quiescence_verified': False, 'permission_lease_verified': False}


@dataclass(frozen=True)
class Registration:
    nonce: str
    directory_device: int
    directory_inode: int
    record_device: int
    record_inode: int


class ScopeJournal:
    def __init__(self, directory):
        # No resolve/stat/mkdir/open in constructor. Caller prepares a NEW
        # canonical private directory; existing runtime locations are not used.
        if type(directory) is not type(Path()) or not directory.is_absolute():
            raise ValueError('scope_journal_private_directory_required')
        self.directory = directory

    def _open(self, ticket=None):
        if self.directory.resolve(strict=True) != self.directory:
            raise ValueError('scope_journal_canonical_directory_required')
        fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            info = os.fstat(fd)
            if (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700
                    or not stat.S_ISDIR(info.st_mode)
                    or (ticket is not None and (info.st_dev, info.st_ino) !=
                        (ticket.directory_device, ticket.directory_inode))):
                raise ValueError('scope_journal_directory_identity_rejected')
            return fd, info
        except BaseException:
            os.close(fd); raise

    @staticmethod
    def _write(fd, name, raw):
        # Stop enumeration at the fixed limit, without an unbounded inventory.
        with os.scandir(fd) as entries:
            for count, _ in enumerate(entries, 1):
                if count >= MAX_RECORDS:
                    raise ValueError('scope_journal_capacity_rejected')
        child = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                        | os.O_CLOEXEC, 0o600, dir_fd=fd)
        try:
            offset = 0
            while offset < len(raw):
                count = os.write(child, raw[offset:])
                if count <= 0: raise OSError('scope_journal_write_unconfirmed')
                offset += count
            os.fsync(child)
            info = os.fstat(child)
            seen = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if (info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode)
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                    or (seen.st_dev, seen.st_ino) != (info.st_dev, info.st_ino)
                    or info.st_size != len(raw)):
                raise ValueError('scope_journal_record_identity_rejected')
        finally:
            os.close(child)
        os.fsync(fd)
        return info

    def _still_named(self, info):
        seen = os.stat(self.directory, follow_symlinks=False)
        if not stat.S_ISDIR(seen.st_mode) or (seen.st_dev, seen.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError('scope_journal_directory_identity_rejected')

    def register(self, nonce):
        raw = _encoded(_possible(nonce))  # Closed nonce before filesystem work.
        fd, directory = self._open()
        try:
            record = self._write(fd, 'scope-' + nonce + '.json', raw)
            self._still_named(directory)
            return Registration(nonce, directory.st_dev, directory.st_ino,
                                record.st_dev, record.st_ino)
        finally:
            os.close(fd)

    def assert_registered(self, ticket):
        # Exact local ticket establishes a retained scope record, not a device
        # permission lease. Failure must prevent a caller's remote dispatch.
        if type(ticket) is not Registration:
            raise ValueError('scope_journal_registration_required')
        expected = _encoded(_possible(ticket.nonce))
        fd, directory = self._open(ticket)
        try:
            child = os.open('scope-' + ticket.nonce + '.json',
                            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            try:
                meta = os.fstat(child)
                if (meta.st_uid != os.getuid() or not stat.S_ISREG(meta.st_mode)
                        or stat.S_IMODE(meta.st_mode) != 0o600 or meta.st_nlink != 1
                        or (meta.st_dev, meta.st_ino) != (ticket.record_device, ticket.record_inode)
                        or meta.st_size != len(expected)):
                    raise ValueError('scope_journal_registration_rejected')
                raw = bytearray()
                while len(raw) <= LIMIT:
                    part = os.read(child, min(512, LIMIT + 1 - len(raw)))
                    if not part: break
                    raw.extend(part)
                if raw != expected:
                    raise ValueError('scope_journal_registration_rejected')
                named = os.stat('scope-' + ticket.nonce + '.json', dir_fd=fd, follow_symlinks=False)
                if (named.st_dev, named.st_ino) != (meta.st_dev, meta.st_ino):
                    raise ValueError('scope_journal_registration_rejected')
                self._still_named(directory)
            finally:
                os.close(child)
        finally:
            os.close(fd)

    def record_consistency(self, ticket, result):
        # Cannot save arbitrary reports/credentials/raw XML. No caller boolean
        # may promote consistency to actual ownership, lease or cleanup.
        if type(result) is not dict:
            raise ValueError('scope_journal_consistency_rejected')
        result = dict(result)  # Snapshot flat scalars before checking/writing.
        keys = {'schema', 'xml_bytes', *RESULT_FLAGS}
        if (set(result) != keys
                or result['schema'] != 'owned-source-snapshot-binding-v1'
                or any(type(result[k]) is not bool for k in RESULT_FLAGS)
                or not all(result[k] for k in RESULT_FLAGS[:6])
                or any(result[k] for k in UNVERIFIED)
                or type(result['xml_bytes']) is not int
                or not 0 < result['xml_bytes'] < 1048576):
            raise ValueError('scope_journal_consistency_rejected')
        self._record_consistency(ticket, result, 'owned-source-consistency-observed-v1')

    def record_retirement_consistency(self, ticket, result):
        # Explicit six-file schema only; no promotion of normal return to a lease.
        if type(result) is not dict:
            raise ValueError('scope_journal_retirement_rejected')
        result = dict(result)
        true_flags = (*RESULT_FLAGS[:5], 'six_file_metadata_consistent',
                      'normal_framework_start_return_reported')
        false_flags = (*UNVERIFIED, 'installed_framework_execution_verified')
        if (set(result) != {'schema', 'xml_bytes', *true_flags, *false_flags}
                or result['schema'] != 'owned-source-snapshot-retirement-binding-v1'
                or any(type(result[k]) is not bool for k in (*true_flags, *false_flags))
                or not all(result[k] for k in true_flags)
                or any(result[k] for k in false_flags)
                or type(result['xml_bytes']) is not int
                or not 0 < result['xml_bytes'] < 1048576):
            raise ValueError('scope_journal_retirement_rejected')
        self._record_consistency(ticket, result, 'owned-source-retirement-consistency-observed-v1')

    def _record_consistency(self, ticket, result, schema):
        self.assert_registered(ticket)
        raw = _encoded({'schema': schema,
                        'nonce': ticket.nonce, 'binding': result,
                        'remote_scope_may_exist': True})
        fd, directory = self._open(ticket)
        try:
            self._write(fd, 'observed-' + ticket.nonce + '.json', raw)
            self._still_named(directory)
        finally:
            os.close(fd)
