#!/usr/bin/env python3
"""Bounded administrator staging for the isolated M1 candidate.

This does not change PF, production launchers, credentials or public routing.
Source AVD must be cold before cloning. It never deletes the source or retries
against an unknown existing destination. Launch/acceptance is a separate step.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = Path('/private/var/lib/huoguo-android-isolation')
NAMES = {'vm': '_huoguo_vm', 'media': '_huoguo_media', 'egress': '_huoguo_egress'}
SDK_ITEMS = ('emulator', 'platform-tools', 'system-images/android-37.0/google_apis/arm64-v8a')
AVD_NAME = 'RemoteAndroid17Isolated'


def command(*args: str) -> str:
    result = subprocess.run(args, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def require_root() -> None:
    if os.geteuid() != 0:
        raise PermissionError('This staging operation requires macOS administrator authentication')
    if sys.platform != 'darwin':
        raise RuntimeError('Only this Darwin candidate is supported')


def ensure_real_directory(path: Path, mode: int, uid: int = 0, gid: int = 0) -> None:
    reject_symlink_parents(path)
    with open_directory(path, create=True) as fd:
        os.fchown(fd, uid, gid)
        os.fchmod(fd, mode)


def reject_symlink_parents(path: Path) -> None:
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('An absolute canonical path is required')
    for candidate in reversed((path, *path.parents)):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise ValueError('Refusing a symbolic-link path component')


@contextmanager
def open_directory(path: Path, *, create: bool = False):
    """Walk with directory FDs: O_NOFOLLOW alone protects only the final leaf."""
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('An absolute canonical path is required')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open('/', flags)
    try:
        for part in path.parts[1:]:
            try:
                child = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, 0o755, dir_fd=fd)
                child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def read_regular_file(path: Path, limit: int = 1024 * 1024) -> bytes:
    reject_symlink_parents(path)
    with open_directory(path.parent) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise ValueError('Expected a bounded single-link ordinary file')
        with os.fdopen(os.dup(fd), 'rb') as source:
            data = source.read(limit + 1)
        if len(data) > limit:
            raise ValueError('Input exceeded its size limit')
        return data
    finally:
        os.close(fd)


def write_new_file(path: Path, data: bytes, mode: int = 0o644,
                   uid: int = 0, gid: int = 0) -> None:
    reject_symlink_parents(path)
    with open_directory(path.parent) as parent:
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     mode, dir_fd=parent)
    try:
        os.fchown(fd, uid, gid)
        os.fchmod(fd, mode)
        with os.fdopen(os.dup(fd), 'wb') as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
    finally:
        os.close(fd)


def update_state(state: dict) -> None:
    """Replace only the root-owned journal, never follow an existing target."""
    marker = ROOT/'candidate-layout.json'
    reject_symlink_parents(marker)
    info = marker.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
            or info.st_mode & 0o022):
        raise ValueError('Candidate journal is not root-owned and immutable to services')
    fd, temporary = tempfile.mkstemp(prefix='.candidate-state-', dir=ROOT)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write((json.dumps(state, indent=2)+'\n').encode())
            out.flush()
            os.fsync(out.fileno())
            os.fchmod(out.fileno(), 0o644)
        os.replace(temporary, marker)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_state() -> dict:
    reject_symlink_parents(ROOT)
    info = ROOT.stat()
    marker = ROOT/'candidate-layout.json'
    journal = marker.lstat()
    if (info.st_uid != 0 or info.st_mode & 0o022 or not ROOT.is_dir()
            or journal.st_uid != 0 or journal.st_mode & 0o022):
        raise ValueError('Candidate root and journal must be administrator-owned')
    state = json.loads(read_regular_file(marker))
    if state.get('schema') != 1 or state.get('root') != str(ROOT):
        raise ValueError('Unknown candidate layout')
    return state


def account(role: str) -> dict:
    name = NAMES[role]
    home = ROOT / 'homes' / role
    try:
        existing = pwd.getpwnam(name)
    except KeyError:
        existing = None
    if existing:
        # Do not adopt an unknown account, including a partially created one.
        # A failed staging journal is retained for explicit repair instead.
        raise ValueError('Candidate account already exists; automatic adoption refused')
    try:
        grp.getgrnam(name)
    except KeyError:
        pass
    else:
        raise ValueError('Candidate group already exists; automatic adoption refused')
    used = {entry.pw_uid for entry in pwd.getpwall()} | {entry.gr_gid for entry in grp.getgrall()}
    number = next((value for value in range(600, 700) if value not in used), None)
    if number is None:
        raise RuntimeError('No unused candidate UID/GID available')
    group_path = '/Groups/' + name
    user_path = '/Users/' + name
    command('/usr/bin/dscl', '.', '-create', group_path)
    command('/usr/bin/dscl', '.', '-create', group_path, 'PrimaryGroupID', str(number))
    command('/usr/bin/dscl', '.', '-create', user_path)
    for key, value in (
        ('UniqueID', str(number)), ('PrimaryGroupID', str(number)),
        ('NFSHomeDirectory', str(home)), ('UserShell', '/usr/bin/false'),
        ('RealName', 'Huoguo isolated ' + role + ' service'),
        ('IsHidden', '1'), ('Password', '*'),
        ('AuthenticationAuthority', ';DisabledUser;'),
    ):
        command('/usr/bin/dscl', '.', '-create', user_path, key, value)
    actual = pwd.getpwnam(name)
    group = grp.getgrnam(name)
    if (actual.pw_uid != number or actual.pw_gid != number or group.gr_gid != number
            or actual.pw_dir != str(home) or actual.pw_shell != '/usr/bin/false'
            or number == grp.getgrnam('admin').gr_gid
            or name in grp.getgrnam('admin').gr_mem):
        raise ValueError('New service identity readback failed')
    member = command('/usr/bin/dsmemberutil', 'checkmembership', '-U', name, '-G', 'admin')
    if 'is not a member' not in member:
        raise ValueError('New service account unexpectedly belongs to administrators')
    return {'name': name, 'uid': number, 'gid': number, 'home': str(home)}


def clone_directory(source: Path, destination: Path) -> None:
    reject_symlink_parents(source)
    reject_symlink_parents(destination)
    if not source.is_dir() or source.is_symlink():
        raise ValueError('Clone source must be a real directory')
    if destination.exists() or destination.is_symlink():
        raise ValueError('Candidate clone destination already exists; not overwriting')
    destination.parent.mkdir(parents=True, exist_ok=True)
    # APFS copy-on-write; no writable links into the personal source tree.
    command('/bin/cp', '-cR', str(source), str(destination))


def protect_tree(path: Path, owner: int = 0, group: int = 0, writable: bool = False) -> None:
    reject_symlink_parents(path)
    for directory, subdirs, files in os.walk(path, followlinks=False):
        for candidate in [Path(directory), *(Path(directory)/n for n in subdirs + files)]:
            if candidate.is_symlink():
                if writable:
                    raise ValueError('Writable AVD clone must not contain symbolic links')
                # Only relative, in-tree links are accepted in the shared SDK.
                if os.path.isabs(os.readlink(candidate)):
                    raise ValueError('Shared SDK symbolic links must be relative')
                resolved = candidate.resolve(strict=True)
                if not resolved.is_relative_to(path.resolve()):
                    raise ValueError('Candidate clone contains an out-of-tree symbolic link')
                os.lchown(candidate, owner, group)
                continue
            info = candidate.stat()
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise ValueError('Candidate tree contains a non-file object')
            os.chown(candidate, owner, group)
            mode = 0o700 if writable else (0o755 if stat.S_ISDIR(info.st_mode) or info.st_mode & 0o111 else 0o644)
            os.chmod(candidate, mode)


def provision(source: Path, sdk: Path) -> dict:
    reject_symlink_parents(ROOT)
    if ROOT.exists():
        info = ROOT.stat()
        if (not ROOT.is_dir() or info.st_uid != 0 or info.st_mode & 0o022
                or any(ROOT.iterdir())):
            raise ValueError('Candidate root is not empty and protected; use status or explicit repair')
    ensure_real_directory(ROOT, 0o755)
    marker = ROOT / 'candidate-layout.json'
    state = {'schema': 1, 'root': str(ROOT), 'phase': 'preparing', 'identities': {},
             'production_changed': False, 'pf_changed': False,
             'avd_cloned': False, 'isolation_accepted': False}
    write_new_file(marker, (json.dumps(state, indent=2)+'\n').encode())
    try:
        return _provision(source, sdk, state)
    except BaseException as error:
        state['phase'] = 'staging_failed'
        state['failure_class'] = type(error).__name__
        update_state(state)
        raise


def _provision(source: Path, sdk: Path, state: dict) -> dict:
    ensure_real_directory(ROOT/'homes', 0o755)
    ensure_real_directory(ROOT/'shared', 0o755)
    ensure_real_directory(ROOT/'profiles', 0o755)
    identities = state['identities']
    for role in NAMES:
        identities[role] = account(role)
        update_state(state)
    for role, record in identities.items():
        home = Path(record['home'])
        ensure_real_directory(home, 0o700, record['uid'], record['gid'])
        for suffix in ('.android', '.android/avd', 'Library', 'Library/Android',
                       'Library/Caches', 'Library/Caches/TemporaryItems',
                       'Library/Caches/TemporaryItems/avd',
                       'Library/Caches/TemporaryItems/avd/running', 'tmp', 'logs'):
            ensure_real_directory(home/suffix, 0o700, record['uid'], record['gid'])
    sdk_target = ROOT/'shared/sdk'
    ensure_real_directory(sdk_target, 0o755)
    for item in SDK_ITEMS:
        clone_directory(sdk/item, sdk_target/item)
    protect_tree(sdk_target)
    code = ROOT/'shared/code'
    ensure_real_directory(code, 0o755)
    copied = {}
    for item in source.glob('*.py'):
        if item.is_symlink():
            raise ValueError('Source module must not be a symbolic link')
        data = read_regular_file(item)
        target = code/item.name
        write_new_file(target, data)
        copied[item.name] = hashlib.sha256(data).hexdigest()
    for record in identities.values():
        link = Path(record['home'])/'Library/Android/sdk'
        if link.exists() or link.is_symlink():
            raise ValueError('Candidate SDK link already exists')
        link.symlink_to(sdk_target, target_is_directory=True)
    state.update(phase='staged', module_sha256=copied, sdk_items=list(SDK_ITEMS))
    update_state(state)
    return state


def cold_clone(source: Path) -> dict:
    state = load_state()
    if state.get('phase') != 'staged' or state.get('avd_cloned'):
        raise ValueError('Only a staged candidate with no AVD can be cloned')
    vm = state['identities']['vm']
    actual = pwd.getpwnam(NAMES['vm'])
    if (vm != {'name': NAMES['vm'], 'uid': actual.pw_uid, 'gid': actual.pw_gid,
               'home': str(ROOT/'homes/vm')} or actual.pw_uid < 600
            or actual.pw_shell != '/usr/bin/false' or actual.pw_dir != vm['home']):
        raise ValueError('Candidate guest identity no longer matches its staged record')
    target = Path(vm['home'])/'.android/avd'/f'{AVD_NAME}.avd'
    # No candidate UID process may race publication into its writable Home.
    # The controlled launcher must remain disabled throughout this operation.
    if any(line.strip() == str(vm['uid']) for line in command('/bin/ps', '-axo', 'uid=').splitlines()):
        raise RuntimeError('Candidate UID has a running process; cold clone refused')
    # Verify no process currently has any source AVD file open. Only source paths
    # are inspected; no full process arguments or credential values are printed.
    def confirm_no_open_source() -> None:
        result = subprocess.run(['/usr/sbin/lsof', '-t', '+D', str(source)],
                                capture_output=True, text=True, timeout=30)
        if result.returncode not in (0, 1) or result.stdout.strip():
            raise RuntimeError('Source AVD is not confirmed cold; clone refused')

    def source_inventory() -> dict:
        inventory = {}
        for directory, subdirs, files in os.walk(source, followlinks=False):
            for item in [Path(directory), *(Path(directory)/n for n in subdirs + files)]:
                info = item.lstat()
                if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                    raise ValueError('Source AVD must have only regular files and directories')
                inventory[str(item.relative_to(source))] = (
                    info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                    info.st_ctime_ns, info.st_mode)
        return inventory

    confirm_no_open_source()
    before = source_inventory()
    clone_directory(source, target)
    confirm_no_open_source()
    if source_inventory() != before:
        raise RuntimeError('Source AVD changed during copy; clone acceptance refused')
    # Check the entire writable tree BEFORE reading/writing config or cleanup.
    # Copied symlinks could otherwise make a privileged write escape the clone.
    for directory, subdirs, files in os.walk(target, followlinks=False):
        for name in subdirs + files:
            candidate = Path(directory)/name
            info = candidate.lstat()
            if (stat.S_ISLNK(info.st_mode)
                    or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
                    or stat.S_ISREG(info.st_mode) and info.st_nlink != 1):
                raise ValueError('AVD clone contains a symbolic link or nonordinary object')
    for item in list(target.iterdir()):
        if item.name.endswith('.lock') or item.name.startswith('snapshot.lock.tmp'):
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
    # Runtime-generated launch metadata contains old absolute source paths.
    # Delete only the candidate copies and let the new instance regenerate them.
    for name in ('hardware-qemu.ini', 'emu-launch-params.txt', 'bootcompleted.ini',
                 'read-snapshot.txt', 'netsim.ini'):
        item = target/name
        if item.exists():
            if not item.is_file():
                raise ValueError('Derived AVD metadata must be an ordinary file')
            item.unlink()
    config = target/'config.ini'
    values = dict(line.split('=',1) for line in read_regular_file(config).decode().splitlines() if '=' in line)
    # Config data must not point back into the original home or AVD. <temp> and
    # relative SDK system-images paths are supported; unexpected paths fail.
    if any(value.startswith('/') or '..' in value.replace('\\', '/').split('/')
           for value in values.values()):
        raise ValueError('AVD has absolute external paths; explicit migration required')
    values.update({'AvdId': AVD_NAME, 'avd.ini.displayname': AVD_NAME,
                   'hw.cpu.ncore': '6', 'hw.ramSize': '16384',
                   'hw.lcd.width': '1080', 'hw.lcd.height': '1920',
                   'hw.audioInput': 'no', 'hw.camera.back': 'none', 'hw.camera.front': 'none'})
    config.unlink()
    write_new_file(config, ''.join(f'{key}={value}\n' for key,value in sorted(values.items())).encode())
    protect_tree(target, vm['uid'], vm['gid'], writable=True)
    ini = target.parent/f'{AVD_NAME}.ini'
    write_new_file(ini, f'avd.ini.encoding=UTF-8\npath={target}\npath.rel=avd/{AVD_NAME}.avd\n'.encode(),
                   mode=0o600, uid=vm['uid'], gid=vm['gid'])
    state['avd_cloned'] = True
    state['source_clone_boundary'] = ('lsof empty before/after and source metadata unchanged; '
                                     'APFS separate COW files; controlled launcher gating required')
    update_state(state)
    return {'avd_cloned': True, 'candidate_name': AVD_NAME, 'production_source_modified': False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('provision', 'cold-clone', 'status'))
    parser.add_argument('--source', type=Path)
    parser.add_argument('--sdk', type=Path)
    parser.add_argument('--avd', type=Path)
    args = parser.parse_args()
    require_root()
    if args.operation == 'provision':
        if not args.source or not args.sdk:parser.error('source and sdk are required')
        result = provision(args.source.resolve(strict=True), args.sdk.resolve(strict=True))
    elif args.operation == 'cold-clone':
        if not args.avd:parser.error('avd is required')
        result = cold_clone(args.avd.resolve(strict=True))
    else:
        result = load_state()
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
