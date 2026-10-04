"""Explicit private deployment descriptor and reader factory; default is inert.

File ownership is a configuration check, never a guest lease or a live JAR
readback. Each DirectDumpReader still checks the deployed program before use.
Callers persist possibly-retained scopes and must resolve real remote ownership.
"""
import json
import os
from pathlib import Path
import stat

from scripts.probes.source_snapshot_reader import DeployedJar, DirectDumpReader

KEYS = frozenset(('nonce', 'sha256', 'uid', 'device', 'inode', 'size',
                  'parent_device', 'parent_inode'))


def parse(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= 4096:
        raise ValueError('snapshot_selection_rejected')
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value: raise ValueError('snapshot_selection_rejected')
            value[key] = item
        return value
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('snapshot_selection_rejected')))
        if type(value) is not dict or set(value) != KEYS:
            raise ValueError('snapshot_selection_rejected')
        return DeployedJar(**value)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError('snapshot_selection_rejected') from None


def read_private(path):
    path = Path(path)
    if not path.is_absolute(): raise ValueError('snapshot_selection_rejected')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_uid != os.getuid() or before.st_nlink != 1
                or not 0 < before.st_size <= 4096):
            raise ValueError('snapshot_selection_rejected')
        raw = os.read(fd, 4097)
        after = os.fstat(fd)
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_size', 'st_nlink', 'st_mtime_ns', 'st_ctime_ns')
        if len(raw) != before.st_size or any(getattr(before, key) != getattr(after, key) for key in fields):
            raise ValueError('snapshot_selection_rejected')
        return parse(raw)
    finally:
        os.close(fd)


class Selection:
    def __init__(self, deployed):
        if type(deployed) is not DeployedJar: raise ValueError('snapshot_selection_rejected')
        self.deployed, self.readers = deployed, []

    def factory(self, adb, serial, timeout):
        if serial != 'emulator-5556': raise ValueError('snapshot_selection_rejected')
        reader = DirectDumpReader(adb, serial, timeout, self.deployed)
        self.readers.append(reader)
        return reader

    def status(self):
        scopes = sorted(set(scope for reader in self.readers for scope in reader.possibly_retained))
        return {'reader_count': len(self.readers), 'possibly_retained_scopes': scopes,
                'remote_scope_clear_verified': not scopes,
                'readonly_qualification_is_permission_or_ownership': False}
