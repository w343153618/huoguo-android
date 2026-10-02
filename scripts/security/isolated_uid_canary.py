#!/usr/bin/env python3
"""Fresh-fixture tests of the staged guest UID/profile, never of a live VM.

Default invocation prints a plan and performs no writes, binds or UID changes.
--run requires root and an explicit SHA256 of the fixed staged guest.sb. It does
not request administrator authentication. Only the operator invokes that mode.

All files and listeners are newly created by this run. No personal files, SDK
programs, AVD disks, production listeners or LAN peers are read or contacted.
UID-600 unsandboxed controls distinguish the profile's denials from plain DAC.
Root-owned listener/file descriptors are not passed to children; only ordinary
subprocess standard-I/O pipes are inherited. Files/network sockets are opened
after setgroups/setgid/setuid, and children inherit a minimal fixed environment.

Passing these synthetic cases is NOT emulator, GPU, Hypervisor, root-guest,
proxy destination-policy, runtime/reboot or whole-host isolation acceptance.
"""
from __future__ import annotations

import argparse
import ctypes
import errno
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import select
import socket
import stat
import subprocess
import sys
import time


ROOT = Path('/private/var/lib/huoguo-android-isolation')
PROFILE = ROOT / 'profiles/guest.sb'
JOURNAL = ROOT / 'candidate-layout.json'
VM_HOME = ROOT / 'homes/vm'
SDK = ROOT / 'shared/sdk'
VM_NAME = '_huoguo_vm'
VM_UID = VM_GID = 600
PROXY_PORT, DNS_PORT = 18131, 53
SHELL_WRITE = 'printf "%s" "$2" > "$1"'
ALLOWED_PROGRAMS = {'/bin/cat', '/bin/sh', '/usr/bin/nc'}
MAX_KERNEL_GROUPS = 64
_KERNEL_LIBC = None
_KERNEL_GETGROUPS = None
REQUIRED_CASES = {
    'baseline_uid600_world_read', 'sandbox_world_read_denied',
    'baseline_world_write', 'sandbox_world_write_denied',
    'baseline_world_create', 'sandbox_world_create_denied',
    'sandbox_home_write', 'sandbox_home_read', 'sandbox_sdk_read',
    'baseline_sdk_write_dac_denied', 'sandbox_sdk_write_denied',
    'guarded_proxy_only', 'guarded_dns_only', 'other_loopback_tcp_denied',
    'other_loopback_udp_denied', 'ipv6_loopback_denied',
    'ipv4_mapped_loopback_denied', 'own_lan_tcp_denied',
}


def plan() -> dict:
    return {'schema': 'huoguo.isolated_uid_canary.v1', 'mode': 'plan',
            'required_uid': VM_UID, 'required_gid': VM_GID,
            'profile': str(PROFILE), 'journal': str(JOURNAL),
            'requires': ['explicit --run', 'root', 'explicit --profile-sha256',
                         'administrator-owned single-link profile and journal',
                         'fixed UID/GID/name/home readback'],
            'creates_only': ['fresh /private/var/tmp fixture directory',
                             'fresh vm-home tmp child directory',
                             'fresh root-owned SDK canary file',
                             'fresh owned TCP/UDP listeners'],
            'checks': ['unsandboxed UID600 DAC read/write/create controls',
                       'sandbox denies outside-file read/write/create',
                       'sandbox allows own-home read/write/create',
                       'sandbox allows fresh SDK file read and denies write',
                       'owned proxy/DNS endpoints only if free; never reuse services',
                       'other loopback TCP/UDP, IPv6, mapped IPv6 and own LAN denial'],
            'runtime_changed': False, 'host_isolation_accepted': False,
            'scope': 'Preparation only; no files, listeners, identities or services changed'}


def open_directory(path: Path) -> int:
    """Pin every path component using O_NOFOLLOW, not a racy path precheck."""
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('An absolute path without parent components is required')
    current = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in path.parts[1:]:
            following = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=current)
            os.close(current)
            current = following
        return current
    except BaseException:
        os.close(current)
        raise


def trusted_bytes(path: Path, limit: int) -> bytes:
    parent = open_directory(path.parent)
    descriptor = None
    try:
        directory_info = os.fstat(parent)
        if directory_info.st_uid != 0 or directory_info.st_mode & 0o022:
            raise ValueError('Trusted artifact parent must be administrator-owned and protected')
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=parent)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != 0
                or info.st_mode & 0o022 or info.st_size > limit):
            raise ValueError('Trusted artifact must be bounded, root-owned and immutable to services')
        data = bytearray()
        while len(data) <= limit:
            chunk = os.read(descriptor, min(65536, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > limit:
            raise ValueError('Trusted artifact changed beyond its size limit')
        return bytes(data)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def validate_staging(expected_sha256: str) -> dict:
    if sys.platform != 'darwin' or os.geteuid() != 0:
        raise PermissionError('--run requires root on Darwin; this program never asks for authentication')
    if not re.fullmatch('[0-9a-fA-F]{64}', expected_sha256):
        raise ValueError('An explicit full profile SHA256 is required')
    root_descriptor = open_directory(ROOT)
    try:
        info = os.fstat(root_descriptor)
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('The staging root must be protected and administrator-owned')
    finally:
        os.close(root_descriptor)
    state = json.loads(trusted_bytes(JOURNAL, 512 * 1024))
    expected_vm = {'name': VM_NAME, 'uid': VM_UID, 'gid': VM_GID, 'home': str(VM_HOME)}
    if (state.get('schema') != 1 or state.get('root') != str(ROOT)
            or state.get('phase') != 'staged'
            or state.get('identities', {}).get('vm') != expected_vm):
        raise ValueError('Journal does not describe the fixed staged UID600 guest')
    actual = pwd.getpwnam(VM_NAME)
    group = grp.getgrnam(VM_NAME)
    administrators = grp.getgrnam('admin')
    if ((actual.pw_uid, actual.pw_gid, actual.pw_dir, actual.pw_shell)
            != (VM_UID, VM_GID, str(VM_HOME), '/usr/bin/false')
            or group.gr_gid != VM_GID or VM_NAME in administrators.gr_mem
            or administrators.gr_gid in os.getgrouplist(VM_NAME, VM_GID)):
        raise ValueError('Service identity readback is inconsistent or privileged')
    for path, uid, gid in ((SDK, 0, 0), (VM_HOME, VM_UID, VM_GID),
                           (VM_HOME / 'tmp', VM_UID, VM_GID)):
        descriptor = open_directory(path)
        try:
            info = os.fstat(descriptor)
            if (info.st_uid, info.st_gid) != (uid, gid) or info.st_mode & 0o022:
                raise ValueError('Canary directories have inconsistent ownership or permissions')
        finally:
            os.close(descriptor)
    profile = trusted_bytes(PROFILE, 128 * 1024)
    actual_sha = hashlib.sha256(profile).hexdigest()
    if actual_sha != expected_sha256.lower():
        raise ValueError('Staged profile SHA256 mismatch')
    text = profile.decode('utf-8')
    endpoints = re.findall(r'\(remote\s+(tcp|udp)\s+"([^"]+)"\)', text)
    if sorted(endpoints) != [('tcp', f'localhost:{PROXY_PORT}'), ('udp', f'localhost:{DNS_PORT}')]:
        raise ValueError('This canary only supports the fixed guarded proxy/DNS profile')
    return {'profile_sha256': actual_sha, 'uid': VM_UID, 'gid': VM_GID,
            'identity_readback_verified': True}


def prepare_darwin_group_reader() -> None:
    """Load the exact legacy libc symbol in the parent, before any fork.

    Darwin's _DARWIN_C_SOURCE getgroups$DARWIN_EXTSN (used by this Python's
    os.getgroups) looks up default Directory Services groups. Its manpage says
    it does NOT return the setgroups-modified process group list. The legacy
    un-suffixed symbol queries XNU credentials, including the effective GID.
    Never broaden the permitted group set to accommodate directory defaults.
    """
    global _KERNEL_LIBC, _KERNEL_GETGROUPS
    if sys.platform != 'darwin':
        raise OSError(errno.ENOTSUP, 'The legacy group verifier is Darwin-only')
    if _KERNEL_GETGROUPS is None:
        library = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
        reader = library.getgroups
        reader.argtypes = (ctypes.c_int, ctypes.POINTER(ctypes.c_uint32))
        reader.restype = ctypes.c_int
        _KERNEL_LIBC, _KERNEL_GETGROUPS = library, reader


def kernel_groups() -> list[int]:
    """Bounded legacy process-credential readback; every error fails closed."""
    if _KERNEL_GETGROUPS is None:
        raise RuntimeError('Kernel group reader must be initialized before fork')
    ctypes.set_errno(0)
    count = _KERNEL_GETGROUPS(0, None)
    if count < 0:
        raise OSError(ctypes.get_errno() or errno.EIO, 'Kernel group count failed')
    # XNU includes the effective GID, so an empty legacy list is not accepted.
    if not 1 <= count <= MAX_KERNEL_GROUPS:
        raise ValueError('Kernel group count exceeds the fixed verification budget')
    values = (ctypes.c_uint32 * count)()
    ctypes.set_errno(0)
    returned = _KERNEL_GETGROUPS(count, values)
    if returned < 0:
        raise OSError(ctypes.get_errno() or errno.EIO, 'Kernel group readback failed')
    if returned != count:
        raise ValueError('Kernel group count changed during verification')
    return list(values)


def drop_privileges() -> None:
    os.setgroups([])
    os.setgid(VM_GID)
    os.setuid(VM_UID)
    os.umask(0o077)
    if ((os.getuid(), os.geteuid(), os.getgid(), os.getegid())
            != (VM_UID, VM_UID, VM_GID, VM_GID)
            or any(group != VM_GID for group in kernel_groups())):
        raise PermissionError('UID/GID drop or supplementary-group removal failed')


def child(command: list[str], *, sandboxed: bool, payload: bytes = b'',
          listener: OwnedListener | None = None) -> dict:
    if not command or command[0] not in ALLOWED_PROGRAMS:
        raise ValueError('Only fixed cat/sh/nc fixture children are supported')
    arguments = ['/usr/bin/sandbox-exec', '-f', str(PROFILE), *command] if sandboxed else command
    start = time.monotonic()
    try:
        prepare_darwin_group_reader()
        options = dict(cwd='/', close_fds=True, preexec_fn=drop_privileges,
                       env={'HOME': str(VM_HOME), 'TMPDIR': str(VM_HOME / 'tmp'),
                            'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})
        if listener is None:
            result = subprocess.run(arguments, input=payload, capture_output=True, timeout=4, **options)
        else:
            # Keep this root runner single-threaded: Python preexec_fn must not
            # fork while a listener thread might own interpreter/library locks.
            process = subprocess.Popen(arguments, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
            try:
                try:
                    process.stdin.write(payload)
                    process.stdin.close()
                except BrokenPipeError:
                    # A denied nc may exit before accepting its harmless input.
                    pass
                finally:
                    process.stdin = None
                deadline = time.monotonic() + 4
                while process.poll() is None:
                    if time.monotonic() > deadline:
                        raise subprocess.TimeoutExpired(arguments, 4)
                    listener.pump(0.05)
                listener.pump(0)
                stdout, stderr = process.communicate(timeout=1)
                result = subprocess.CompletedProcess(arguments, process.returncode, stdout, stderr)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=1)
        return {'exit_code': result.returncode, 'stdout': result.stdout,
                'stderr_nonempty': bool(result.stderr), 'launched': True,
                'identity_verified_before_exec': True,
                'elapsed_ms': round((time.monotonic() - start) * 1000, 2)}
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError) as error:
        return {'exit_code': None, 'stdout': b'', 'launched': False,
                'identity_verified_before_exec': False, 'error_class': type(error).__name__}


class Fixtures:
    """Keep exact created inodes and clean only those fresh paths, never recurse."""
    def __init__(self):
        self.items: list[tuple[Path, int, int, bool]] = []
        self.cleanup_results = []

    def remember(self, path: Path, directory: bool = False) -> None:
        descriptor = open_directory(path.parent)
        try:
            info = os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
            if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
                raise ValueError('A fresh fixture must be an ordinary file/directory')
            self.items.append((path, info.st_dev, info.st_ino, directory))
        finally:
            os.close(descriptor)

    def directory(self, path: Path, mode: int, uid: int = 0) -> Path:
        parent = open_directory(path.parent)
        descriptor = None
        try:
            os.mkdir(path.name, mode=mode, dir_fd=parent)
            self.remember(path, True)
            descriptor = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                 dir_fd=parent)
            os.fchown(descriptor, uid, VM_GID if uid else 0)
            os.fchmod(descriptor, mode)
            return path
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(parent)

    def file(self, path: Path, content: bytes, mode: int, uid: int = 0) -> Path:
        parent = open_directory(path.parent)
        descriptor = None
        try:
            descriptor = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 mode, dir_fd=parent)
            self.remember(path)
            os.fchown(descriptor, uid, VM_GID if uid else 0)
            os.fchmod(descriptor, mode)
            remaining = memoryview(content)
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise OSError('Canary write made no progress')
                remaining = remaining[written:]
            return path
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(parent)

    def cleanup(self) -> list[dict]:
        for path, device, inode, directory in reversed(self.items):
            parent = None
            try:
                parent = open_directory(path.parent)
                current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
                if (current.st_dev, current.st_ino) != (device, inode):
                    raise ValueError('Fixture path was replaced; refusing cleanup of a different inode')
                if directory:
                    os.rmdir(path.name, dir_fd=parent)
                else:
                    os.unlink(path.name, dir_fd=parent)
                result = {'fixture': path.name, 'removed': True}
            except FileNotFoundError:
                result = {'fixture': path.name, 'removed': True, 'already_absent': True}
            except (ValueError, OSError) as error:
                result = {'fixture': path.name, 'removed': False, 'error_class': type(error).__name__}
            finally:
                if parent is not None:
                    os.close(parent)
            self.cleanup_results.append(result)
        return self.cleanup_results


class OwnedListener:
    def __init__(self, host: str, port: int = 0, *, udp: bool = False):
        family = socket.AF_INET6 if ':' in host else socket.AF_INET
        self.socket = socket.socket(family, socket.SOCK_DGRAM if udp else socket.SOCK_STREAM)
        self.marker = ('owned-canary-' + secrets.token_hex(12)).encode()
        self.received = 0
        try:
            self.socket.bind((host, port))
            self.port = self.socket.getsockname()[1]
            self.socket.setblocking(False)
            if not udp:
                self.socket.listen(4)
            self.udp = udp
        except BaseException:
            self.socket.close()
            raise

    def pump(self, timeout: float):
        if not select.select([self.socket], [], [], timeout)[0]:
            return
        if self.udp:
            _, address = self.socket.recvfrom(1024)
            self.received += 1
            self.socket.sendto(self.marker, address)
        else:
            peer, _ = self.socket.accept()
            self.received += 1
            with peer:
                peer.settimeout(0.5)
                peer.sendall(self.marker)

    def close(self):
        self.socket.close()
        return True


def owned_file_contents(path: Path) -> bytes:
    parent = open_directory(path.parent)
    descriptor = None
    try:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=parent)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 4096:
            raise ValueError('Canary must remain a bounded single-link fixture')
        return os.read(descriptor, 4096)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def file_case(name: str, command: list[str], *, sandboxed: bool,
              expected_stdout: bytes | None = None, expected_denied: bool = False) -> dict:
    outcome = child(command, sandboxed=sandboxed)
    passed = outcome['launched'] and (
        outcome['exit_code'] != 0 if expected_denied else outcome['exit_code'] == 0)
    if expected_stdout is not None:
        passed = passed and outcome['stdout'] == expected_stdout
    outcome.pop('stdout')
    return {'case': name, 'sandboxed': sandboxed, 'expected_denied': expected_denied,
            'passed': bool(passed), **outcome}


def network_case(name: str, bind_host: str, target: str, *, port: int = 0,
                 udp: bool = False, allowed: bool = False) -> dict:
    listener = None
    outcome = None
    try:
        listener = OwnedListener(bind_host, port, udp=udp)
        command = ['/usr/bin/nc', *(['-6'] if ':' in target else []),
                   *(['-u'] if udp else []), '-w', '1', target, str(listener.port)]
        baseline = child(command, sandboxed=False, payload=b'owned UDP fixture' if udp else b'',
                         listener=listener)
        if (not baseline['launched'] or baseline['exit_code'] != 0
                or baseline['stdout'] != listener.marker):
            outcome = {'case': name, 'passed': False, 'inconclusive': True,
                       'reason': 'unsandboxed UID600 could not reach the owned fixture'}
            return outcome
        before = listener.received
        result = child(command, sandboxed=True, payload=b'owned UDP fixture' if udp else b'',
                       listener=listener)
        listener.pump(0)
        reached = listener.received > before
        passed = result['launched'] and (
            result['exit_code'] == 0 and reached and result['stdout'] == listener.marker
            if allowed else result['exit_code'] != 0 and not reached and not result['stdout'])
        result.pop('stdout')
        outcome = {'case': name, 'transport': 'UDP' if udp else 'TCP',
                   'target': target, 'owned_port': listener.port, 'baseline_passed': True,
                   'sandbox_host_received': reached, 'expected_allowed': allowed,
                   'passed': bool(passed), **result}
        return outcome
    except OSError as error:
        outcome = {'case': name, 'passed': False, 'inconclusive': True,
                   'reason': 'owned fixture bind unavailable; existing services are never contacted',
                   'error_class': type(error).__name__, 'errno': error.errno}
        return outcome
    finally:
        if listener is not None:
            closed = listener.close()
            if outcome is not None:
                outcome['listener_closed'] = closed


def own_lan_address(interface: str) -> str | None:
    if not re.fullmatch(r'en[0-9]+', interface):
        raise ValueError('Only an explicitly named local en interface is supported')
    result = subprocess.run(['/usr/sbin/ipconfig', 'getifaddr', interface],
                            capture_output=True, text=True, timeout=5, close_fds=True)
    address = result.stdout.strip()
    if result.returncode or not re.fullmatch(r'(?:[0-9]{1,3}\.){3}[0-9]{1,3}', address):
        return None
    if address.startswith('127.') or any(int(part) > 255 for part in address.split('.')):
        return None
    return address  # Binding the listener below additionally proves it is owned locally.


def execute_canaries(expected_sha256: str, interface: str = 'en7') -> dict:
    validation = validate_staging(expected_sha256)
    if not re.fullmatch(r'en[0-9]+', interface):
        raise ValueError('Only an explicitly named local en interface is supported')
    registry, cases = Fixtures(), []
    token = secrets.token_hex(12)
    marker = ('fresh-world-readable-canary-' + token).encode()
    report = {'schema': 'huoguo.isolated_uid_canary.v1', 'mode': 'run', **validation,
              'scope': 'Fresh-fixture UID600 and fixed profile synthetic tests only',
              'production_changed': False, 'vm_launched': False,
              'personal_files_read': False, 'host_isolation_accepted': False,
              'group_verifier': 'Darwin legacy libc getgroups; process credentials, not directory defaults',
              'not_accepted': ['live guest/root', 'GPU/Hypervisor', 'proxy destination restrictions',
                               'P2P/media/phone', 'reboot/interface/TUN lifecycle', 'VM escape'],
              'cases': cases, 'cleanup': []}
    try:
        base = registry.directory(Path('/private/var/tmp') / ('huoguo-uid-canary-' + token), 0o755)
        outside = registry.directory(base / 'outside', 0o777)
        read_file = registry.file(outside / 'read-canary', marker, 0o644)
        write_control = registry.file(outside / 'write-control', marker, 0o666)
        write_denied = registry.file(outside / 'write-denied', marker, 0o666)
        control_create, denied_create = outside / 'create-control', outside / 'create-denied'
        home = registry.directory(VM_HOME / 'tmp' / ('uid-canary-' + token), 0o700, VM_UID)
        home_file = home / 'home-write'
        sdk_file = registry.file(SDK / ('.uid-canary-' + token), marker, 0o644)
        cases.append(file_case('baseline_uid600_world_read', ['/bin/cat', str(read_file)],
                               sandboxed=False, expected_stdout=marker))
        cases.append(file_case('sandbox_world_read_denied', ['/bin/cat', str(read_file)],
                               sandboxed=True, expected_denied=True, expected_stdout=b''))
        for name, target, sandboxed, denied in (
            ('baseline_world_write', write_control, False, False),
            ('sandbox_world_write_denied', write_denied, True, True),
            ('baseline_world_create', control_create, False, False),
            ('sandbox_world_create_denied', denied_create, True, True),
            ('sandbox_home_write', home_file, True, False),
            ('sandbox_sdk_write_denied', sdk_file, True, True),
        ):
            result = file_case(name, ['/bin/sh', '-c', SHELL_WRITE, 'fixture', str(target), token],
                               sandboxed=sandboxed, expected_denied=denied)
            if target.exists() and target not in (write_control, write_denied, sdk_file):
                registry.remember(target)
            if denied:
                result['passed'] = result['passed'] and (
                    owned_file_contents(target) == marker if target in (write_denied, sdk_file)
                    else not target.exists())
            else:
                result['passed'] = result['passed'] and owned_file_contents(target) == token.encode()
            cases.append(result)
        cases.append(file_case('sandbox_home_read', ['/bin/cat', str(home_file)],
                               sandboxed=True, expected_stdout=token.encode()))
        cases.append(file_case('sandbox_sdk_read', ['/bin/cat', str(sdk_file)],
                               sandboxed=True, expected_stdout=marker))
        sdk_dac = file_case('baseline_sdk_write_dac_denied',
                           ['/bin/sh', '-c', SHELL_WRITE, 'fixture', str(sdk_file), token],
                           sandboxed=False, expected_denied=True)
        sdk_dac['passed'] = sdk_dac['passed'] and owned_file_contents(sdk_file) == marker
        sdk_dac['boundary'] = 'SDK write denial includes DAC; SBPL layer not independently separated'
        cases.append(sdk_dac)
        cases.extend([
            network_case('guarded_proxy_only', '127.0.0.1', '127.0.0.1', port=PROXY_PORT, allowed=True),
            network_case('guarded_dns_only', '127.0.0.1', '127.0.0.1', port=DNS_PORT, udp=True, allowed=True),
            network_case('other_loopback_tcp_denied', '127.0.0.1', '127.0.0.1'),
            network_case('other_loopback_udp_denied', '127.0.0.1', '127.0.0.1', udp=True),
            network_case('ipv6_loopback_denied', '::1', '::1'),
            network_case('ipv4_mapped_loopback_denied', '127.0.0.1', '::ffff:127.0.0.1'),
        ])
        address = own_lan_address(interface)
        if address:
            cases.append(network_case('own_lan_tcp_denied', address, address))
        else:
            cases.append({'case': 'own_lan_tcp_denied', 'passed': False, 'inconclusive': True,
                          'reason': 'Requested local interface has no usable IPv4 address'})
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        report['setup_or_case_error'] = type(error).__name__
    finally:
        report['cleanup'] = registry.cleanup()
    report['all_required_cases_passed'] = (
        {item['case'] for item in cases} == REQUIRED_CASES
        and len(cases) == len(REQUIRED_CASES) and all(item['passed'] for item in cases)
        and all(item['removed'] for item in report['cleanup'])
        and 'setup_or_case_error' not in report)
    report['cleanup_complete'] = all(item['removed'] for item in report['cleanup'])
    report['boundary'] = 'No live emulator/root-guest or complete host-isolation acceptance claim'
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--prepare-only', action='store_true')
    mode.add_argument('--run', action='store_true')
    parser.add_argument('--profile-sha256')
    parser.add_argument('--lan-interface', default='en7')
    args = parser.parse_args()
    if not args.run:
        print(json.dumps(plan(), indent=2))
        return
    if not args.profile_sha256:
        parser.error('--run requires --profile-sha256')
    try:
        report = execute_canaries(args.profile_sha256, args.lan_interface)
    except (ValueError, OSError, KeyError) as error:
        parser.exit(2, 'Canary preflight refused: ' + type(error).__name__ + '\n')
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report['all_required_cases_passed'] else 1)


if __name__ == '__main__':
    main()
