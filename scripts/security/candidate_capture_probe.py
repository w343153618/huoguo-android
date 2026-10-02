#!/usr/bin/env python3
"""Bounded candidate-only boot probe and private discovery-token broker.

Default: describe the plan. No VM/server is started, no guest setting changed,
no personal HOME/key is read. Boot uses only an ALREADY-RUNNING dedicated ADB
smart socket; avoiding adb CLI also prevents its implicit server/key creation.
The broker is for a pinned root driver only, and emits its secret exclusively
to an inherited anonymous pipe, never to stdout/a report. Screenshot execution
is deliberately absent until a separate trusted immutable gRPC runtime exists.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import re
import select
import socket
import stat
import subprocess
import time


ROOT = Path('/private/var/lib/huoguo-android-isolation')
VM_HOME = ROOT/'homes/vm'
VM_UID, MEDIA_UID = 600, 601
AVD = 'RemoteAndroid17Isolated'
ADB_SERVER = ('127.0.0.1', 15037)
ADB_TARGET = '127.0.0.1:5567'
ADB_SERIALS = (ADB_TARGET, 'emulator-5566')
GRPC_ENDPOINT = '127.0.0.1:8566'
DISCOVERY_DIRS = {'tmp': VM_HOME/'tmp/avd/running',
                  'cache': VM_HOME/'Library/Caches/TemporaryItems/avd/running'}
MAX_REPLY = 4096
MAX_DISCOVERY = 16384
_HEX = re.compile(br'[0-9a-fA-F]{4}\Z')
_TOKEN = re.compile(r'[A-Za-z0-9._~+/=-]{16,512}\Z')


class Refused(RuntimeError):
    """Only fixed error codes, never raw server/discovery content."""


class CandidateAdb:
    def __init__(self, timeout: float = 2.0, connector=None):
        self.timeout = timeout
        self.connector = connector or socket.create_connection

    def _open(self):
        sock = self.connector(ADB_SERVER, timeout=self.timeout)
        sock.settimeout(self.timeout)
        return sock

    def _recv(self, sock, size, deadline):
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            raise Refused('adb_command_deadline')
        sock.settimeout(remaining)
        return sock.recv(size)

    def _exact(self, sock, size, deadline=None):
        deadline = deadline or time.monotonic()+self.timeout
        result = bytearray()
        while len(result) < size:
            block = self._recv(sock, size-len(result), deadline)
            if not block:
                raise Refused('adb_truncated_reply')
            result.extend(block)
        return bytes(result)

    def _length_reply(self, sock):
        deadline = time.monotonic()+self.timeout
        length = self._exact(sock, 4, deadline)
        if not _HEX.fullmatch(length) or int(length, 16) > MAX_REPLY:
            raise Refused('adb_reply_size')
        return self._exact(sock, int(length, 16), deadline)

    def _request(self, sock, service):
        data = service.encode('ascii')
        if len(data) > 1024:
            raise Refused('adb_request_size')
        sock.sendall(('%04x' % len(data)).encode('ascii') + data)
        if self._exact(sock, 4) != b'OKAY':
            # Do not print FAIL payloads: an untrusted server can include data.
            raise Refused('adb_service_refused')

    def _host(self, service):
        sock = self._open()
        try:
            self._request(sock, service)
            return self._length_reply(sock)
        finally:
            sock.close()

    def connect_candidate(self):
        # Fixed HOST connect only; no pairing, mDNS, scan or server startup.
        self._host('host:connect:' + ADB_TARGET)
        lines = self._host('host:devices').decode('ascii', errors='strict').splitlines()
        found = []
        for line in lines:
            parts = line.split()
            if len(parts) != 2 or parts[0] not in ADB_SERIALS:
                raise Refused('dedicated_adb_has_other_transport')
            if parts[1] == 'device':
                found.append(parts[0])
            else:
                raise Refused('candidate_adb_not_ready')
        if not found:
            raise Refused('candidate_adb_not_present')
        if len(found) != 1:
            raise Refused('candidate_adb_transport_ambiguous')
        return found[0]

    def read(self, serial, kind):
        commands = {'boot': 'getprop sys.boot_completed', 'size': 'wm size',
                    'gles': "dumpsys SurfaceFlinger | grep -m 1 'GLES:'"}
        if serial not in ADB_SERIALS or kind not in commands:
            raise Refused('adb_command_not_fixed')
        sock = self._open()
        try:
            self._request(sock, 'host:transport:' + serial)
            self._request(sock, 'shell:' + commands[kind])
            deadline = time.monotonic()+self.timeout
            output = bytearray()
            while len(output) <= MAX_REPLY:
                block = self._recv(sock, min(1024, MAX_REPLY+1-len(output)), deadline)
                if not block:
                    return bytes(output).decode('utf-8', errors='strict').strip()
                output.extend(block)
            raise Refused('adb_shell_reply_size')
        finally:
            sock.close()


def probe_boot(seconds=20.0, client=None, ownership_fd=None) -> dict:
    if os.getuid() != MEDIA_UID or os.geteuid() != MEDIA_UID:
        raise Refused('trusted_media_uid_required')
    if not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 1 <= seconds <= 60:
        raise Refused('boot_probe_duration')
    ownership = _read_attestation(ownership_fd)
    _check_live_pids(ownership)
    client = client or CandidateAdb(timeout=min(2.0, seconds))
    started = time.monotonic()
    serial = None
    ready, attempts = False, 0
    for _ in range(int(math.ceil(seconds * 4))):
        if time.monotonic()-started >= seconds:
            break
        attempts += 1
        if serial is None:
            try:
                serial = client.connect_candidate()
            except Refused as error:
                if str(error) not in ('candidate_adb_not_present', 'candidate_adb_not_ready'):
                    raise
                time.sleep(.25)
                continue
        value = client.read(serial, 'boot')
        if value == '1':
            ready = True
            break
        if value not in ('', '0'):
            raise Refused('unexpected_boot_property')
        time.sleep(.25)
    result = {'schema': 1, 'scope': 'candidate boot readback only', 'adb_server': '127.0.0.1:15037',
              'serial': serial, 'boot_completed': ready, 'attempts': attempts,
              'verified_server_pid': ownership['server_pid'], 'managed_vm_pid': ownership['vm_pid'],
              'elapsed_ms': round((time.monotonic()-started)*1000, 3),
              'hvf_accepted': False, 'capture_accepted': False, 'services_started': False,
              'guest_changed': False, 'production_changed': False}
    if ready:
        size = client.read(serial, 'size')
        match = re.fullmatch(r'Physical size: (\d{1,4})x(\d{1,4})(?:\s+Override size: (\d{1,4})x(\d{1,4}))?', size)
        if not match:
            raise Refused('candidate_size_reply')
        result['physical_size'] = [int(match[1]), int(match[2])]
        if match[3]:
            result['override_size'] = [int(match[3]), int(match[4])]
        gles = client.read(serial, 'gles')
        # Renderer capabilities only, never include arbitrary guest text in reports.
        result['gles_markers'] = {name: name.lower() in gles.lower()
                                  for name in ('GLES', 'Metal', 'ANGLE', 'SwiftShader')}
    _check_live_pids(ownership)
    return result


@contextmanager
def _directory(path):
    if not path.is_absolute() or any(part in ('.', '..') for part in str(path).split('/')):
        raise Refused('discovery_path_not_canonical')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open('/', flags)
    try:
        for part in path.parts[1:]:
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _fixed_process(pid, uid, executables):
    result = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'uid=,comm='],
                            capture_output=True, text=True, timeout=2, check=False,
                            env={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LANG': 'C'})
    parts = result.stdout.strip().split(maxsplit=1)
    if (result.returncode or len(parts) != 2 or parts[0] != str(uid)
            or parts[1] not in executables):
        raise Refused('managed_candidate_pid_not_alive')


def _candidate_process(pid):
    _fixed_process(pid, VM_UID, (str(ROOT/'shared/sdk/emulator/emulator'),
                   str(ROOT/'shared/sdk/emulator/qemu/darwin-aarch64/qemu-system-aarch64-headless')))


def _check_live_pids(ownership):
    _fixed_process(ownership['server_pid'], MEDIA_UID, (str(ROOT/'shared/sdk/platform-tools/adb'),))
    _candidate_process(ownership['vm_pid'])


def _listener_owner(port, pid, uid):
    result = subprocess.run(['/usr/sbin/lsof', '-nP', '-iTCP:%d' % port, '-sTCP:LISTEN', '-Fpun'],
                            capture_output=True, text=True, timeout=3, check=False,
                            env={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LANG': 'C'})
    seen, current_pid, current_uid = [], None, None
    if result.returncode:
        raise Refused('fixed_endpoint_not_listening')
    for line in result.stdout.splitlines():
        if not line:
            continue
        if line[0] == 'p':
            current_pid, current_uid = line[1:], None
        elif line[0] == 'u':
            current_uid = line[1:]
        elif line[0] == 'n':
            seen.append((current_pid, current_uid, line[1:]))
    allowed = ('127.0.0.1:%d' % port, '[::1]:%d' % port)
    if (not seen or any(item[0] != str(pid) or item[1] != str(uid) or item[2] not in allowed
                        for item in seen)):
        raise Refused('fixed_endpoint_owner_or_address')


def _root_attestation_file(fd, readonly=False):
    if isinstance(fd, bool) or not isinstance(fd, int) or fd < 3:
        raise Refused('root_attestation_fd_required')
    info = os.fstat(fd)
    # The controller receives a read-only, unlinked root-owned ordinary file,
    # so it cannot fabricate or rewrite the endpoint ownership descriptor.
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 0 or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) != 0o600):
        raise Refused('root_private_unlinked_attestation_required')
    flags = fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE
    if readonly and flags != os.O_RDONLY:
        raise Refused('root_attestation_must_be_readonly')
    if not readonly and flags not in (os.O_WRONLY, os.O_RDWR):
        raise Refused('root_attestation_writer_required')


def _write_pipe(fd, payload):
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    try:
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        deadline, cursor = time.monotonic()+2, 0
        while cursor < len(payload):
            remaining = deadline-time.monotonic()
            if remaining <= 0 or not select.select([], [fd], [], remaining)[1]:
                raise Refused('private_pipe_timeout')
            try:
                written = os.write(fd, payload[cursor:])
            except BlockingIOError:
                continue
            if not written:
                raise Refused('private_pipe_closed')
            cursor += written
    finally:
        fcntl.fcntl(fd, fcntl.F_SETFL, flags)


def attest_endpoints(server_pid, vm_pid, output_fd):
    if os.getuid() != 0 or os.geteuid() != 0:
        raise Refused('root_driver_attester_required')
    for pid in (server_pid, vm_pid):
        if isinstance(pid, bool) or not isinstance(pid, int) or not 1 <= pid <= 999999:
            raise Refused('fixed_endpoint_pid_required')
    _root_attestation_file(output_fd)
    record = {'schema': 1, 'server_pid': server_pid, 'server_uid': MEDIA_UID,
              'vm_pid': vm_pid, 'vm_uid': VM_UID, 'adb_server': '127.0.0.1:15037',
              'adb_target': ADB_TARGET, 'avd': AVD, 'clock_domain': 'host_unix_ns',
              'issued_unix_ns': time.time_ns()}
    _check_live_pids(record)
    _listener_owner(15037, server_pid, MEDIA_UID)
    _listener_owner(5567, vm_pid, VM_UID)
    _check_live_pids(record)
    record['issued_unix_ns'] = time.time_ns()
    payload = (json.dumps(record)+'\n').encode()
    os.ftruncate(output_fd, 0)
    os.lseek(output_fd, 0, os.SEEK_SET)
    cursor = 0
    while cursor < len(payload):
        cursor += os.write(output_fd, payload[cursor:])
    os.fsync(output_fd)
    return {'schema': 1, 'ownership_attested': True, 'server_pid': server_pid,
            'managed_vm_pid': vm_pid, 'root_execution_started_service': False}


def _read_attestation(fd):
    _root_attestation_file(fd, readonly=True)
    before = os.fstat(fd)
    if before.st_size > MAX_REPLY:
        raise Refused('root_attestation_size')
    os.lseek(fd, 0, os.SEEK_SET)
    data = os.read(fd, MAX_REPLY+1)
    after = os.fstat(fd)
    if ((before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
        raise Refused('root_attestation_changed')
    if len(data) > MAX_REPLY or not data.endswith(b'\n'):
        raise Refused('root_attestation_size')
    try:
        record = json.loads(data)
    except (ValueError, UnicodeError):
        raise Refused('root_attestation_format') from None
    fixed = {'schema': 1, 'server_uid': MEDIA_UID, 'vm_uid': VM_UID,
             'adb_server': '127.0.0.1:15037', 'adb_target': ADB_TARGET, 'avd': AVD,
             'clock_domain': 'host_unix_ns'}
    if not isinstance(record, dict) or set(record) != set(fixed) | {'server_pid', 'vm_pid', 'issued_unix_ns'}:
        raise Refused('root_attestation_keys')
    if any(record.get(key) != value for key, value in fixed.items()):
        raise Refused('root_attestation_identity')
    for key in ('schema', 'server_uid', 'vm_uid', 'server_pid', 'vm_pid', 'issued_unix_ns'):
        if isinstance(record[key], bool) or not isinstance(record[key], int) or record[key] <= 0:
            raise Refused('root_attestation_numbers')
    # Python3.9 on macOS uses a process-relative monotonic reference. Use the
    # shared host Unix clock for this short cross-process descriptor; a clock
    # step producing future/stale data fails closed. Media timing is unrelated.
    age = time.time_ns()-record['issued_unix_ns']
    if not 0 <= age <= 10_000_000_000:
        raise Refused('root_attestation_stale')
    return record


def _read_discovery(pid, location):
    if (isinstance(pid, bool) or not isinstance(pid, int) or not 1 <= pid <= 999999
            or location not in DISCOVERY_DIRS):
        raise Refused('fixed_candidate_discovery_required')
    _candidate_process(pid)
    _listener_owner(8566, pid, VM_UID)
    with _directory(VM_HOME) as home:
        info = os.fstat(home)
        if info.st_uid != VM_UID or stat.S_IMODE(info.st_mode) != 0o700:
            raise Refused('candidate_home_not_private')
    with _directory(DISCOVERY_DIRS[location]) as parent:
        info = os.fstat(parent)
        if info.st_uid != VM_UID or info.st_mode & 0o022:
            raise Refused('candidate_discovery_directory_owner')
        fd = os.open('pid_%d.ini' % pid, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != VM_UID or before.st_nlink != 1
                or before.st_mode & 0o022 or not 1 <= before.st_size <= MAX_DISCOVERY):
            raise Refused('candidate_discovery_file_owner_or_size')
        data = os.read(fd, MAX_DISCOVERY+1)
        after = os.fstat(fd)
        if (len(data) != before.st_size or len(data) > MAX_DISCOVERY
                or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise Refused('candidate_discovery_changed')
    finally:
        os.close(fd)
    try:
        rows = data.decode('ascii').splitlines()
    except UnicodeError:
        raise Refused('candidate_discovery_encoding') from None
    if len(rows) > 128:
        raise Refused('candidate_discovery_rows')
    values = {}
    for row in rows:
        if not row or row.startswith(('#', ';')):
            continue
        if '=' not in row:
            raise Refused('candidate_discovery_format')
        key, value = row.split('=', 1)
        if key in values or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', key):
            raise Refused('candidate_discovery_duplicate_key')
        values[key] = value
    if values.get('avd.name') != AVD or values.get('grpc.port') != '8566':
        raise Refused('candidate_discovery_identity')
    token = values.get('grpc.token', '')
    if not _TOKEN.fullmatch(token):
        raise Refused('candidate_discovery_token_shape')
    _candidate_process(pid)
    _listener_owner(8566, pid, VM_UID)
    return token


def broker_token(pid, output_fd, location='tmp') -> dict:
    if os.getuid() != 0 or os.geteuid() != 0:
        raise Refused('root_driver_broker_required')
    if isinstance(output_fd, bool) or not isinstance(output_fd, int) or output_fd < 3:
        raise Refused('private_token_pipe_required')
    info = os.fstat(output_fd)
    if not stat.S_ISFIFO(info.st_mode) or info.st_nlink != 0:
        raise Refused('anonymous_token_pipe_required')
    if fcntl.fcntl(output_fd, fcntl.F_GETFL) & os.O_ACCMODE not in (os.O_WRONLY, os.O_RDWR):
        raise Refused('token_pipe_writer_required')
    token = _read_discovery(pid, location)
    payload = (json.dumps({'endpoint': GRPC_ENDPOINT, 'avd': AVD, 'pid': pid, 'token': token})+'\n').encode()
    _write_pipe(output_fd, payload)
    return {'schema': 1, 'scope': 'candidate-only token delivered to private pipe',
            'pid': pid, 'endpoint': GRPC_ENDPOINT, 'token_delivered': True,
            'capture_accepted': False, 'token_in_report': False}


def plan() -> dict:
    return {'schema': 1, 'executed': False, 'adb_server': '127.0.0.1:15037',
            'adb_target': ADB_TARGET, 'controller_uid': MEDIA_UID, 'vm_uid': VM_UID,
            'candidate_avd': AVD, 'grpc_endpoint': GRPC_ENDPOINT,
            'discovery_locations': {key: str(path) for key, path in DISCOVERY_DIRS.items()},
            'server_prerequisites': ['trusted controller starts dedicated server separately',
                                     'loopback only; ADB_LOCAL_TRANSPORT_MAX_PORT=0; ADB_MDNS_AUTO_CONNECT=0',
                                     'root lsof/ps attests fixed UID601 server PID + managed UID600 VM PID',
                                     'boot requires fresh root-owned0600 unlinked ordinary file FD opened O_RDONLY'],
            'screenshot_executable': False,
            'screenshot_prerequisites': ['pinned trusted immutable Python>=3.10 + grpcio/protobuf + emulator proto',
                                        'fixed managed PID broker pipe, no HOME glob or token report',
                                        'bounded getScreenshot/streamScreenshot; actual RGBA size + seq + source/arrival gaps'],
            'services_started': False, 'guest_changed': False, 'production_changed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='operation')
    commands.add_parser('describe')
    boot = commands.add_parser('boot')
    boot.add_argument('--seconds', type=float, default=20)
    boot.add_argument('--ownership-fd', type=int, required=True)
    attest = commands.add_parser('attest')
    attest.add_argument('--server-pid', type=int, required=True)
    attest.add_argument('--vm-pid', type=int, required=True)
    attest.add_argument('--ownership-fd', type=int, required=True)
    broker = commands.add_parser('broker')
    broker.add_argument('--pid', type=int, required=True)
    broker.add_argument('--token-fd', type=int, required=True)
    broker.add_argument('--location', choices=tuple(DISCOVERY_DIRS), default='tmp')
    args = parser.parse_args()
    try:
        if args.operation == 'boot':
            result = probe_boot(args.seconds, ownership_fd=args.ownership_fd)
        elif args.operation == 'attest':
            result = attest_endpoints(args.server_pid, args.vm_pid, args.ownership_fd)
        elif args.operation == 'broker':
            result = broker_token(args.pid, args.token_fd, args.location)
        else:
            result = plan()
    except Refused as error:
        print(json.dumps({'schema': 1, 'status': 'refused', 'accepted': False,
                          'error_class': str(error)}))
        raise SystemExit(1)
    except (OSError, UnicodeError, subprocess.SubprocessError):
        # Generic fixed failure: never disclose a raw error from guest/metadata.
        print(json.dumps({'schema': 1, 'status': 'refused', 'accepted': False,
                          'error_class': 'system_io_or_dependency_unavailable'}))
        raise SystemExit(1)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
