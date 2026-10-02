#!/usr/bin/env python3
"""Prepare a fresh LocalOnly DNS-SD delegation canary; default is describe only.

--run is reserved for a separately pinned, administrator-owned private copy.
It registers one random LocalOnly proxy service with a public random TXT marker,
then queries exactly that TXT/IN record as UID501, unsandboxed UID600 and
sandboxed UID600. No browsing, LAN/public query, VM, ADB or profile edits occur.
Apple dns-sd.c sets opinterface to LocalOnly before both -P and -Q. The -P
numeric IP argument is always 127.0.0.1; its getaddrinfo call is not given a name.

This observes one host-process DNS-SD delegation capability, not guest escape,
LAN isolation or physical egress. A missing answer/timeout is inconclusive.
Daemon record deregistration and detached/launchd processes are not independently
accepted. No raw dns-sd output or arbitrary local service names enter reports.
"""
from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass, field
import errno
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import selectors
import signal
import stat
import subprocess
import sys
import time


ROOT = Path('/private/var/lib/huoguo-android-isolation')
PROFILE = ROOT / 'profiles/guest.sb'
VM_NAME, VM_UID, VM_GID = '_huoguo_vm', 600, 600
OWNER_UID = 501
DNS_SD, SANDBOX = '/usr/bin/dns-sd', '/usr/bin/sandbox-exec'
MAX_OUTPUT = 65536
_LIBC = _GETGROUPS = None
POLICY_ERRORS = {-65555, -65570, -65571}
SOURCE = 'https://raw.githubusercontent.com/apple-oss-distributions/mDNSResponder/main/Clients/dns-sd.c'


def describe() -> dict:
    return {'schema': 'huoguo.localonly_resolver_canary.v1', 'mode': 'describe',
            'scope': 'Fresh exact LocalOnly TXT/IN host-process delegation only',
            'requires': ['root on Darwin, invoked only by a pinned private driver',
                         'root-owned private script copy and full source/profile SHA256',
                         'fixed non-admin UID600/GID600 identity readback',
                         'local dns-sd help confirms -lo, -P, -Q and -t'],
            'commands': ['dns-sd -lo -t 25 -P FRESH_NAME FRESH_TYPE local. 9 FRESH_HOST 127.0.0.1 FRESH_TXT',
                         'dns-sd -lo -t 3 -Q EXACT_FRESH_FULLNAME TXT IN'],
            'controls': ['UID501 unsandboxed', 'UID600 unsandboxed'],
            'candidate': 'UID600 under pinned guest.sb',
            'source': SOURCE, 'production_changed': False, 'profiles_changed': False,
            'actual_uid600_completed': False, 'isolation_accepted': False,
            'unaccepted': ['guest behavior', 'LAN/public queries', 'physical DNS exit',
                           'record deregistration independent readback',
                           'detached/launchd processes and scan/signal race']}


@dataclass(frozen=True)
class Record:
    nonce: str
    marker: str

    def __post_init__(self):
        if not re.fullmatch('[0-9a-f]{32}', self.nonce) or not re.fullmatch('[0-9a-f]{64}', self.marker):
            raise ValueError('Fresh fixture components must be fixed-length lowercase hex')

    @property
    def name(self):
        return 'huoguo-canary-' + self.nonce

    @property
    def kind(self):
        return '_hg' + self.nonce[:8] + '._tcp'

    @property
    def fullname(self):
        return f'{self.name}.{self.kind}.local.'

    @property
    def txt(self):
        return 'marker=' + self.marker

    def register_command(self):
        return [DNS_SD, '-lo', '-t', '25', '-P', self.name, self.kind, 'local.',
                '9', 'huoguo-' + self.nonce + '.local.', '127.0.0.1', self.txt]

    def query_command(self, sandboxed=False):
        command = [DNS_SD, '-lo', '-t', '3', '-Q', self.fullname, 'TXT', 'IN']
        return [SANDBOX, '-f', str(PROFILE), *command] if sandboxed else command


def localonly_help_supported(output: str) -> bool:
    """Recognize known local grammar, not just an incidental mention of -lo."""
    patterns = (r'^dns-sd -lo\s+\(Run dns-sd cmd using local only interface\)$',
                r'^dns-sd -P <Name> <Type> <Domain> <Port> <Host> <IP>',
                r'^dns-sd -Q <name> <rrtype> <rrclass>',
                r'^dns-sd -t <seconds>\s+\(Exit after <seconds>\)$')
    return all(re.search(pattern, output, re.MULTILINE) for pattern in patterns)


def exact_answer(output: str, record: Record) -> bool:
    """Accept only a LocalOnly Add callback with the exact TXT wire payload.

    Apple qr_reply prints unknown types (including TXT) as length + hex bytes.
    Unknown versions/formats fail closed. Marker echoes and registration output
    do not match the callback shape; query argv never contains the marker.
    """
    row = re.compile(r'^\s*\d{1,2}:\d{2}:\d{2}(?:\.\d+)?\s+Add\s+[0-9A-Fa-f]+'
                     r'\s+\S\s+(-1|4294967295)\s+(\S+)\s+TXT\s+IN\s+(\d+)'
                     r'\s+bytes:\s*((?:[0-9A-Fa-f]{2}(?:\s+|$))+)\s*$')
    expected = record.txt.encode('ascii')
    payload = bytes([len(expected)]) + expected
    for line in output.splitlines():
        match = row.fullmatch(line)
        if not match or match[2] != record.fullname or int(match[3]) != len(payload):
            continue
        if bytes.fromhex(match[4]) == payload:
            return True
    return False


def explicit_policy_error(output: str) -> int | None:
    # Only exact DNSServiceQueryRecord API failures count, not unrelated stderr.
    for line in output.splitlines():
        match = re.fullmatch(r'DNSServiceQueryRecord failed (-\d+)', line.strip())
        if match and int(match[1]) in POLICY_ERRORS:
            return int(match[1])
    return None


def trusted_file(path: Path, limit: int, expected_sha: str | None = None) -> bytes:
    """Pin each real directory and the final regular single-link file with FDs."""
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Trusted path must be absolute without parent traversal')
    directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    descriptor = None
    try:
        for index, component in enumerate(path.parts[1:-1]):
            following = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=directory)
            os.close(directory)
            directory = following
            info = os.fstat(directory)
            # /private/var/tmp is root-owned sticky, permitted only as ancestor.
            final_parent = index == len(path.parts[1:-1]) - 1
            if info.st_uid != 0 or (info.st_mode & 0o022 and
                                   (final_parent or not info.st_mode & stat.S_ISVTX)):
                raise ValueError('Untrusted artifact directory')
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                or info.st_mode & 0o022 or info.st_size > limit):
            raise ValueError('Untrusted or oversized artifact')
        data = bytearray()
        while len(data) <= limit:
            chunk = os.read(descriptor, min(65536, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > limit:
            raise ValueError('Artifact grew beyond its size limit')
        digest = hashlib.sha256(data).hexdigest()
        if expected_sha is not None and digest != expected_sha:
            raise ValueError('Artifact SHA256 mismatch')
        return bytes(data)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)


def validate_run(source_sha: str, profile_sha: str) -> int:
    if sys.platform != 'darwin' or os.getuid() != 0 or os.geteuid() != 0:
        raise PermissionError('--run requires real/effective root on Darwin; no authentication is requested')
    if not all(re.fullmatch('[0-9a-f]{64}', value) for value in (source_sha, profile_sha)):
        raise ValueError('Full lowercase source/profile SHA256 pins required')
    source_path = Path(__file__).absolute()
    if not re.fullmatch(r'/private/var/tmp/huoguo-localonly-pinned-[0-9a-f]{32}/localonly_resolver_canary.py',
                        str(source_path)):
        raise ValueError('Only the fixed private pinned-copy layout may execute --run')
    trusted_file(source_path, 128 * 1024, source_sha)
    parent_info = source_path.parent.stat()
    if stat.S_IMODE(parent_info.st_mode) != 0o700:
        raise ValueError('Private driver directory must be mode0700')
    trusted_file(PROFILE, 128 * 1024, profile_sha)
    # Read-only system executables, never arbitrary paths supplied by a caller.
    for program in (DNS_SD, SANDBOX):
        trusted_file(Path(program), 16 * 1024 * 1024)
    vm = pwd.getpwnam(VM_NAME)
    administrators = grp.getgrnam('admin')
    vm_group = grp.getgrnam(VM_NAME)
    if ((vm.pw_uid, vm.pw_gid, vm.pw_dir, vm.pw_shell)
            != (VM_UID, VM_GID, str(ROOT / 'homes/vm'), '/usr/bin/false')
            or vm_group.gr_gid != VM_GID or VM_NAME in administrators.gr_mem
            or administrators.gr_gid in os.getgrouplist(VM_NAME, VM_GID)):
        raise ValueError('Fixed service identity is inconsistent or an administrator')
    owner = pwd.getpwuid(OWNER_UID)
    if owner.pw_uid != OWNER_UID or owner.pw_gid < 1:
        raise ValueError('Owner control identity unavailable')
    return owner.pw_gid


def prepare_group_reader():
    global _LIBC, _GETGROUPS
    _LIBC = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    _GETGROUPS = _LIBC.getgroups  # Legacy XNU credentials; not Directory Services.
    _GETGROUPS.argtypes = (ctypes.c_int, ctypes.POINTER(ctypes.c_uint32))
    _GETGROUPS.restype = ctypes.c_int


def drop_identity(uid: int, gid: int):
    os.setgroups([])
    os.setgid(gid)
    os.setuid(uid)
    if (os.getuid(), os.geteuid(), os.getgid(), os.getegid()) != (uid, uid, gid, gid):
        raise PermissionError('Identity readback mismatch')
    if _GETGROUPS is None:
        raise PermissionError('Legacy kernel group reader was not prepared')
    count = _GETGROUPS(0, None)
    if not 0 <= count <= 64:
        raise PermissionError('Kernel group count unavailable or excessive')
    groups = (ctypes.c_uint32 * max(1, count))()
    actual = _GETGROUPS(count, groups)
    if actual != count or any(group != gid for group in groups[:actual]):
        raise PermissionError('Unexpected kernel groups')


@dataclass
class Child:
    process: subprocess.Popen
    uid: int
    gid: int
    role: str
    stdout: bytearray = field(default_factory=bytearray)
    stderr: bytearray = field(default_factory=bytearray)


class Children:
    """Single-threaded finite reader and ownership-checked own-process cleanup."""
    def __init__(self):
        self.selector = selectors.DefaultSelector()
        self.children: list[Child] = []

    def spawn(self, command: list[str], uid: int, gid: int, role: str) -> Child:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   close_fds=True, start_new_session=True, cwd='/',
                                   env={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin',
                                        'HOME': '/private/var/empty', 'TMPDIR': '/private/var/empty',
                                        'LANG': 'C', 'LC_ALL': 'C'},
                                   preexec_fn=lambda: drop_identity(uid, gid))
        child = Child(process, uid, gid, role)
        self.children.append(child)
        for stream, destination in ((process.stdout, child.stdout), (process.stderr, child.stderr)):
            os.set_blocking(stream.fileno(), False)
            self.selector.register(stream, selectors.EVENT_READ, destination)
        return child

    def pump(self, timeout: float):
        for key, _ in self.selector.select(timeout):
            chunk = os.read(key.fileobj.fileno(), 4096)
            if not chunk:
                self.selector.unregister(key.fileobj)
                continue
            if len(key.data) + len(chunk) > MAX_OUTPUT:
                raise ValueError('Fresh process output exceeded its fixed budget')
            key.data.extend(chunk)

    def wait(self, child: Child, seconds: float, predicate=None) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.pump(min(0.1, max(0, deadline - time.monotonic())))
            if predicate is not None and predicate(child.stdout.decode('ascii', errors='replace')):
                return True
            if child.process.poll() is not None:
                self.pump(0)
                return predicate is None or predicate(child.stdout.decode('ascii', errors='replace'))
        return False

    def group_exists(self, child: Child) -> bool:
        if child.process.pid <= 1 or child.process.pid == os.getpgrp():
            raise ValueError('Unsafe process group identifier')
        try:
            os.killpg(child.process.pid, 0)
            return True
        except ProcessLookupError:
            return False

    def verified_signal(self, child: Child, value: int):
        if not self.group_exists(child):
            return
        scan = subprocess.run(['/bin/ps', '-g', str(child.process.pid), '-o',
                               'pid=,pgid=,uid=,ruid=,gid=,rgid='],
                              capture_output=True, timeout=2, close_fds=True,
                              stdin=subprocess.DEVNULL,
                              env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})
        if len(scan.stdout) > 8192 or len(scan.stderr) > 8192:
            raise ValueError('Group metadata budget exceeded')
        lines = scan.stdout.decode('ascii', errors='strict').splitlines()
        if not lines:
            if not self.group_exists(child):
                return
            raise ValueError('Live group metadata unavailable')
        if scan.returncode:
            raise ValueError('Group metadata command failed')
        for line in lines:
            fields = line.split()
            if len(fields) != 6 or not all(re.fullmatch('[0-9]+', value) for value in fields):
                raise ValueError('Group metadata malformed')
            pid, pgid, uid, ruid, gid, rgid = map(int, fields)
            if pid <= 1 or pgid != child.process.pid or (uid, ruid, gid, rgid) != (child.uid, child.uid, child.gid, child.gid):
                raise ValueError('Foreign identity in recorded group; refusing signal')
        os.killpg(child.process.pid, value)

    def cleanup(self, grace: float = 2) -> dict:
        errors = []
        for value in (signal.SIGTERM, signal.SIGKILL):
            for child in self.children:
                child.process.poll()
                try:
                    self.verified_signal(child, value)
                except (OSError, ValueError, subprocess.SubprocessError):
                    errors.append({'role': child.role, 'reason': 'recorded_group_signal_refused_or_failed'})
            deadline = time.monotonic() + grace
            while time.monotonic() < deadline:
                for child in self.children:
                    child.process.poll()
                try:
                    # Darwin may report the group absent during SIGKILL before
                    # its leader is waitable. Require both reap and group exit.
                    if all(child.process.poll() is not None and not self.group_exists(child)
                           for child in self.children):
                        break
                except (OSError, ValueError):
                    break
                time.sleep(0.02)
        gone = []
        for child in self.children:
            try:
                child.process.poll()
                gone.append(child.process.poll() is not None and not self.group_exists(child))
            except (OSError, ValueError):
                gone.append(False)
            for stream in (child.process.stdout, child.process.stderr):
                if stream is not None:
                    stream.close()
        self.selector.close()
        return {'recorded_groups_gone': bool(self.children) and all(gone),
                'created_processes': len(self.children), 'errors': errors,
                'scan_signal_toctou_remaining': True,
                'detached_or_launchd_checked': False,
                'record_deregistration_independently_verified': False}


def classify(controls: list[dict], candidate: dict) -> str:
    if not controls or not all(item.get('exact_answer') for item in controls):
        return 'inconclusive_positive_control_failed'
    if candidate.get('exact_answer'):
        return 'localonly_delegation_observed'
    if candidate.get('policy_error') in POLICY_ERRORS:
        return 'scoped_localonly_query_denied'
    return 'inconclusive_no_explicit_policy_result'


def execute(source_sha: str, profile_sha: str) -> dict:
    owner_gid = validate_run(source_sha, profile_sha)
    help_result = subprocess.run([DNS_SD, '-H'], stdin=subprocess.DEVNULL,
                                 capture_output=True, timeout=3, close_fds=True,
                                 env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})
    help_output = help_result.stdout + help_result.stderr
    if len(help_output) > MAX_OUTPUT or not localonly_help_supported(help_output.decode('ascii', errors='replace')):
        raise ValueError('LocalOnly semantics/argument grammar unsupported; refusing registration')
    prepare_group_reader()
    record = Record(secrets.token_hex(16), secrets.token_hex(32))
    children = Children()
    report = describe()
    report.update(mode='run', source_sha256=source_sha, profile_sha256=profile_sha,
                  cases=[], outcome='inconclusive_runtime_failure')
    try:
        registrar = children.spawn(record.register_command(), OWNER_UID, owner_gid, 'registrar')
        ready = lambda output: ('Using LocalOnly' in output and
                                f'Got a reply for service {record.fullname}: Name now registered and active' in output)
        if not children.wait(registrar, 3, ready):
            raise ValueError('Fresh LocalOnly registration did not become active')
        for uid, gid, role, sandboxed in ((OWNER_UID, owner_gid, 'owner_control', False),
                                         (VM_UID, VM_GID, 'uid600_control', False),
                                         (VM_UID, VM_GID, 'uid600_profile', True)):
            if registrar.process.poll() is not None:
                raise ValueError('Fresh registration closed before queries completed')
            child = children.spawn(record.query_command(sandboxed), uid, gid, role)
            finished = children.wait(child, 4)
            output = child.stdout.decode('ascii', errors='replace')
            error = child.stderr.decode('ascii', errors='replace')
            report['cases'].append({'role': role, 'uid': uid, 'gid': gid,
                                    'sandboxed': sandboxed, 'process_exited': finished,
                                    'returncode': child.process.poll(),
                                    'exact_answer': exact_answer(output, record),
                                    'policy_error': explicit_policy_error(error),
                                    'raw_output_retained': False})
            if not finished:
                raise ValueError('Fresh exact query exceeded its deadline')
        report['actual_uid600_completed'] = True
        report['outcome'] = classify(report['cases'][:2], report['cases'][2])
    except (OSError, ValueError, subprocess.SubprocessError):
        report['failure'] = 'bounded_canary_setup_or_process_failure'
    finally:
        report['cleanup'] = children.cleanup()
    if not report['cleanup']['recorded_groups_gone']:
        report['outcome'] = 'inconclusive_cleanup_failed'
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--run', action='store_true')
    mode.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--source-sha256')
    parser.add_argument('--profile-sha256')
    args = parser.parse_args()
    if args.run and (not args.source_sha256 or not args.profile_sha256):
        parser.error('--run requires --source-sha256 and --profile-sha256')
    print(json.dumps(execute(args.source_sha256, args.profile_sha256) if args.run else describe(),
                     indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
