#!/usr/bin/env python3
"""Prepare, or explicitly supervise ONE bounded isolated candidate experiment.

Default mode only reads the staged journal, immutable inputs and SDK/runtime
inventories, then prints a reviewable launch plan. It never registers a service,
changes PF/Clash, clones an AVD, reads credentials or touches production PIDs.

--run requires root Darwin, a root-owned copy of this supervisor in shared/code,
and the exact SHA256 of a freshly reviewed plan. Children drop all supplementary
groups and real/effective UID before exec. Root binds DNS sockets but never runs
the DNS parser. Only those two audited FDs reach the DNS child; other children
inherit none. Guards have a file whitelist, but their explicitly pinned Homebrew
runtime remains writable by trusted host UID 501. This is an experiment, NOT
immutable-runtime, supported App Sandbox, emulator, GPU or PF acceptance.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import errno
import grp
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import resource
import secrets
import signal
import socket
import stat
import subprocess
import sys
import time


ROOT = Path('/private/var/lib/huoguo-android-isolation')
TRUSTED_ROOT_UID = 0
TRUSTED_HOST_UID = 501
CELLAR = Path('/opt/homebrew/Cellar')
PYTHON_PACKAGE = CELLAR / 'python@3.14/3.14.7'
OPENSSL_PACKAGE = CELLAR / 'openssl@3/3.6.4'
GUARD_PYTHON = PYTHON_PACKAGE / 'Frameworks/Python.framework/Versions/3.14/bin/python3.14'
SYSTEM_PYTHON = Path('/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework')
ROLES = {'vm': ('_huoguo_vm', 600), 'media': ('_huoguo_media', 601),
         'egress': ('_huoguo_egress', 602)}
AVD_NAME = 'RemoteAndroid17Isolated'
WEB_PORT, DNS_PORT = 18131, 53
FORMAL_PORT, FORMAL_POLL_SECONDS = 15556, 2.0
GROUP_TERM_SECONDS, GROUP_KILL_SECONDS, GROUP_POLL_SECONDS = 5.0, 2.0, 0.05
SDK_ITEMS = ('emulator', 'platform-tools', 'system-images/android-37.0/google_apis/arm64-v8a')
OS_READ = ('/System/Library', '/System/Cryptexes',
           '/System/Volumes/Preboot/Cryptexes/OS', '/usr/lib', '/usr/bin',
           '/usr/sbin', '/bin', '/sbin')
OS_FILES = ('/', '/dev/null', '/dev/random', '/dev/urandom',
            '/private/etc/hosts', '/private/etc/resolv.conf', '/private/var/run/resolv.conf')
DENIED = ('/Users', '/Volumes', '/Network', '/private/var/root',
          '/System/Volumes/Data/Users', '/System/Volumes/Data/Volumes',
          '/System/Volumes/Data/Network', '/System/Volumes/Data/private/var/root')
MAX_FILE_BYTES, MAX_TREE_BYTES, MAX_TREE_ENTRIES = 16 * 1024**3, 32 * 1024**3, 50000
_HEX = re.compile(r'[0-9a-f]{64}\Z')
_MODULE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,100}\.py\Z')
_ROLE_LOG_NAME = re.compile(r'candidate-(guest|web|dns)-[0-9a-f]{24}\.log\Z')
_RECEIPT_PHASES = frozenset(('started', 'role_started', 'experiment_completed', 'failed',
                             'cleanup_started', 'cleanup_finished', 'result'))
_RECEIPT_CLEANUP = frozenset(('not_started', 'requested', 'stop_returned', 'stop_failed'))
_RECEIPT_REASONS = frozenset((
    'candidate_completed', 'candidate_stop_requested', 'candidate_cleanup_failed',
    'candidate_os_error', 'candidate_internal_error', 'candidate_interrupted', 'candidate_refused',
    'formal_session_check_failed', 'formal_session_active_stop_candidate',
    'root_formal_session_check_required', 'root_identity_revalidation_required',
    'identity_journal_mismatch', 'identity_directory_or_group_mismatch',
    'identity_membership_check_failed', 'service_is_administrator',
    'guard_web_not_owned_or_ready', 'guard_dns_not_owned_or_ready',
    'guards_changed_before_guest_start', 'candidate_child_exited_stop_all',
    'existing_guard_profile_differs', 'root_fixed_role_log_required',
    'role_log_directory_not_private', 'role_log_not_private_regular'))
_RECEIPT_REASONS = _RECEIPT_REASONS | frozenset((
    'candidate_group_not_recorded', 'candidate_group_probe_failed',
    'candidate_group_members_unverified', 'candidate_group_identity_changed',
    'candidate_group_signal_failed', 'candidate_group_cleanup_unverified'))


class Refused(ValueError):
    """A fixed reason; no credentials, personal contents or process argv."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def _canonical(path: Path) -> None:
    if (not path.is_absolute() or '..' in path.parts
            or any(ord(char) < 32 or ord(char) == 127 for char in str(path))):
        raise Refused('noncanonical_path')
    if path.resolve(strict=True) != path:
        raise Refused('symlink_path_component')


def _ownership(info, owners: set[int], *, immutable: bool = True) -> None:
    if info.st_uid not in owners:
        raise Refused('unexpected_owner_or_world_write')
    if stat.S_ISLNK(info.st_mode):
        return  # Symlink permission bits are not an access grant; target is pinned.
    if info.st_mode & 0o002:
        raise Refused('unexpected_owner_or_world_write')
    if immutable and info.st_mode & 0o020:
        raise Refused('immutable_input_group_writable')


def _root_parents(path: Path) -> None:
    _canonical(path)
    for parent in (path, *path.parents):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise Refused('expected_directory')
        _ownership(info, {TRUSTED_ROOT_UID})


def _read_regular(path: Path, *, owners: set[int] | None = None,
                  limit: int = 1024**2, immutable: bool = True) -> bytes:
    _canonical(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        _ownership(before, owners or {TRUSTED_ROOT_UID}, immutable=immutable)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or not 0 <= before.st_size <= limit):
            raise Refused('nonordinary_or_oversized_file')
        chunks, count = [], 0
        while count <= limit:
            block = os.read(fd, min(1024**2, limit + 1 - count))
            if not block:
                break
            chunks.append(block)
            count += len(block)
        after = os.fstat(fd)
        if (count != before.st_size or count > limit
                or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise Refused('file_changed_during_read')
        return b''.join(chunks)
    finally:
        os.close(fd)


def inventory(path: Path, *, owners: set[int], immutable: bool = True) -> dict:
    """Hash all bounded ordinary tree data; internal relative symlinks are pinned."""
    _canonical(path)
    records, total = [], 0
    for directory, directories, files in os.walk(path, followlinks=False):
        directory = Path(directory)
        entries = [directory, *(directory/name for name in sorted(directories + files))]
        for item in entries:
            info = item.lstat()
            _ownership(info, owners, immutable=immutable)
            relative = str(item.relative_to(path))
            if stat.S_ISLNK(info.st_mode):
                target = os.readlink(item)
                if os.path.isabs(target) or not item.resolve(strict=True).is_relative_to(path):
                    raise Refused('out_of_tree_runtime_symlink')
                records.append([relative, 'link', target])
            elif stat.S_ISDIR(info.st_mode):
                records.append([relative, 'directory', stat.S_IMODE(info.st_mode)])
            elif stat.S_ISREG(info.st_mode):
                if info.st_nlink != 1 or not 0 <= info.st_size <= MAX_FILE_BYTES:
                    raise Refused('nonordinary_tree_file')
                # Stream big SDK images; never keep their bytes in memory.
                fd = os.open(item, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                try:
                    before = os.fstat(fd)
                    if (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino):
                        raise Refused('tree_file_swapped')
                    checksum = hashlib.sha256()
                    count = 0
                    while True:
                        block = os.read(fd, 1024**2)
                        if not block:
                            break
                        checksum.update(block)
                        count += len(block)
                        if count > MAX_FILE_BYTES:
                            raise Refused('tree_file_too_large')
                    after = os.fstat(fd)
                    if (count != before.st_size or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                            != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                        raise Refused('tree_changed_during_read')
                finally:
                    os.close(fd)
                total += count
                records.append([relative, 'file', stat.S_IMODE(info.st_mode), checksum.hexdigest()])
            else:
                raise Refused('nonordinary_tree_object')
            if len(records) > MAX_TREE_ENTRIES or total > MAX_TREE_BYTES:
                raise Refused('tree_budget_exceeded')
    # os.walk emits each directory again. Deduplicate identical records and pin
    # deterministic ordering; a different duplicate is a concurrent mutation.
    unique = {}
    for row in records:
        if row[0] in unique and unique[row[0]] != row:
            raise Refused('tree_entry_changed')
        unique[row[0]] = row
    return {'sha256': digest(_json_bytes({'entries': sorted(unique.values())})),
            'entries': len(unique), 'bytes': total}


def _assert_runtime_identity(role: str, record: dict) -> None:
    name, uid = ROLES[role]
    expected = {'name': name, 'uid': uid, 'gid': uid, 'home': str(ROOT/'homes'/role)}
    if record != expected:
        raise Refused('identity_journal_mismatch')
    actual, group, administrators = pwd.getpwnam(name), grp.getgrnam(name), grp.getgrnam('admin')
    if (actual.pw_uid != uid or actual.pw_gid != uid or actual.pw_dir != expected['home']
            or actual.pw_shell != '/usr/bin/false' or uid in (0, 501)
            or group.gr_name != name or group.gr_gid != uid):
        raise Refused('identity_directory_or_group_mismatch')
    try:
        memberships = os.getgrouplist(name, uid)
    except OSError:
        raise Refused('identity_membership_check_failed') from None
    if (not memberships or uid not in memberships
            or any(isinstance(gid, bool) or not isinstance(gid, int) for gid in memberships)):
        raise Refused('identity_membership_check_failed')
    if (administrators.gr_gid == uid or name in administrators.gr_mem
            or administrators.gr_gid in memberships or 0 in memberships):
        raise Refused('service_is_administrator')


def _identities(state: dict) -> dict:
    if set(state.get('identities', {})) != set(ROLES):
        raise Refused('identity_roles_not_fixed')
    result = {}
    for role, (name, uid) in ROLES.items():
        expected = {'name': name, 'uid': uid, 'gid': uid, 'home': str(ROOT/'homes'/role)}
        if state['identities'][role] != expected:
            raise Refused('identity_journal_mismatch')
        _assert_runtime_identity(role, expected)
        home = Path(expected['home'])
        _canonical(home)
        info = home.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
            raise Refused('service_home_not_private')
        result[role] = expected
    return result


def _quote(value: str) -> str:
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise Refused('profile_control_character')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _except(operation: str, filters: list[str]) -> str:
    return '(deny ' + operation + '\n (require-not (require-any ' + ' '.join(filters) + ')))'


def guard_profile(packages: tuple[Path, ...]) -> str:
    home, code = ROOT/'homes/egress', ROOT/'shared/code'
    reads = (*OS_READ, str(SYSTEM_PYTHON), str(code), str(home), *(str(path) for path in packages))
    filters = ['(subpath ' + _quote(path) + ')' for path in reads]
    filters += ['(literal ' + _quote(path) + ')' for path in OS_FILES]
    metadata = list(filters)
    for path in (home, code, SYSTEM_PYTHON, *packages):
        metadata += ['(literal ' + _quote(str(parent)) + ')' for parent in path.parents]
    # Explicit aliases used by the trusted host's Mach-O load commands. Their
    # canonical targets are checked below; no blanket /opt or /Library access.
    metadata += ['(literal ' + _quote('/opt/homebrew/opt') + ')']
    metadata += ['(literal ' + _quote('/opt/homebrew/opt/' + path.parent.name) + ')' for path in packages]
    return '\n'.join((
        '; EXPERIMENTAL guard file whitelist; host UID 501 Python runtime remains mutable.',
        '; UDP53 destination IP is fixed by DNS code, NOT by this OS filter. PF remains unaccepted.',
        '; DNS replies require local UDP localhost:53 AND remote UDP localhost:*; not peer authentication.',
        '(version 1)', '(allow default)',
        _except('file-read-data file-read-xattr', filters),
        _except('file-read-metadata', list(dict.fromkeys(metadata))),
        _except('file-write*', ['(subpath ' + _quote(str(home)) + ')', '(literal "/dev/null")']),
        _except('file-map-executable', ['(subpath ' + _quote(path) + ')' for path in reads if path != str(home)]),
        '(deny file-read* file-write* file-map-executable ' + ' '.join('(subpath ' + _quote(path) + ')' for path in DENIED) + ')',
        _except('network-outbound', ['(remote tcp "localhost:7897")', '(remote udp "*:53")',
                                    '(require-all (local udp "localhost:53") (remote udp "localhost:*"))']),
        _except('network-bind', ['(local tcp "localhost:18131")', '(local udp "localhost:53")']),
        _except('network-inbound', ['(local tcp "localhost:18131")', '(local udp "localhost:53")']),
        '(deny appleevent-send)',
        '(deny mach-lookup (global-name "com.apple.SecurityServer") (global-name-regex #"^com\\.apple\\.(securityd|tccd|cfprefsd)(\\.|$)"))',
        '; No complete Mach/IOKit/process or inherited-descriptor acceptance.', ''))


def child_environment(role: str) -> dict[str, str]:
    home = ROOT/'homes'/role
    result = {'HOME': str(home), 'USER': ROLES[role][0], 'LOGNAME': ROLES[role][0],
              'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'TMPDIR': str(home/'tmp'), 'LANG': 'en_US.UTF-8'}
    if role == 'vm':
        result.update(ANDROID_HOME=str(ROOT/'shared/sdk'), ANDROID_SDK_ROOT=str(ROOT/'shared/sdk'),
                      ANDROID_AVD_HOME=str(home/'.android/avd'), ANDROID_EMU_MEDIA_DECODER_VTB='1')
    return result


def guest_argv() -> list[str]:
    return ['/usr/bin/sandbox-exec', '-f', str(ROOT/'profiles/guest.sb'),
            str(ROOT/'shared/sdk/emulator/emulator'), '-avd', AVD_NAME,
            '-ports', '5566,5567', '-grpc', '8566', '-grpc-use-token',
            '-accel', 'on', '-gpu', 'host', '-no-window',
            '-no-snapshot', '-crash-report-mode', 'disabled', '-no-metrics', '-no-boot-anim',
            '-feature', '-Bluetooth,-WifiPacketStream,-Uwb,-Nfc,-NetsimUI',
            '-http-proxy', '127.0.0.1:18131', '-dns-server', '127.0.0.1']


def prepare_plan(interface: str, deny_self_ips: tuple[str, ...] = ()) -> dict:
    if not re.fullmatch(r'en[0-9]+', interface):
        raise Refused('physical_interface_required')
    interface_index = socket.if_nametoindex(interface)
    if not interface_index:
        raise Refused('physical_interface_missing')
    _root_parents(ROOT)
    for path in (ROOT/'shared', ROOT/'shared/code', ROOT/'shared/sdk', ROOT/'profiles', ROOT/'homes'):
        _root_parents(path)
    state = json.loads(_read_regular(ROOT/'candidate-layout.json', limit=65536))
    if (state.get('schema') != 1 or state.get('root') != str(ROOT) or state.get('phase') != 'staged'
            or state.get('production_changed') is not False or state.get('pf_changed') is not False):
        raise Refused('candidate_journal_not_staged')
    identities = _identities(state)
    hashes = state.get('module_sha256', {})
    if not isinstance(hashes, dict) or not 1 <= len(hashes) <= 64:
        raise Refused('missing_module_pins')
    for name, expected in hashes.items():
        if not _MODULE.fullmatch(name) or not isinstance(expected, str) or not _HEX.fullmatch(expected):
            raise Refused('invalid_module_pin')
        if digest(_read_regular(ROOT/'shared/code'/name)) != expected:
            raise Refused('staged_module_sha_mismatch')
    for name in ('restricted_egress.py', 'restricted_dns.py'):
        if name not in hashes:
            raise Refused('missing_guard_code_pin')
    profile = state.get('guest_profile', {})
    if profile.get('path') != str(ROOT/'profiles/guest.sb') or not _HEX.fullmatch(profile.get('sha256', '')):
        raise Refused('guest_profile_not_pinned')
    if digest(_read_regular(ROOT/'profiles/guest.sb')) != profile['sha256']:
        raise Refused('guest_profile_sha_mismatch')
    if state.get('sdk_items') != list(SDK_ITEMS):
        raise Refused('sdk_layout_not_fixed')
    _canonical(GUARD_PYTHON)
    packages = (PYTHON_PACKAGE, OPENSSL_PACKAGE)
    runtime = {}
    for package in packages:
        _canonical(package)
        if not package.is_relative_to(CELLAR) or len(package.relative_to(CELLAR).parts) != 2:
            raise Refused('python_runtime_not_canonical_cellar_version')
        runtime[str(package)] = inventory(package, owners={TRUSTED_ROOT_UID, TRUSTED_HOST_UID}, immutable=False)
        alias = Path('/opt/homebrew/opt')/package.parent.name
        if alias.resolve(strict=True) != package:
            raise Refused('runtime_load_alias_changed')
    _canonical(SYSTEM_PYTHON)
    _ownership(SYSTEM_PYTHON.lstat(), {TRUSTED_ROOT_UID})
    denial = sorted({str(ipaddress.ip_address(address)) for address in deny_self_ips})
    profile_bytes = guard_profile(packages).encode()
    code = ROOT/'shared/code'
    web = ['/usr/bin/sandbox-exec', '-f', str(ROOT/'profiles/egress.sb'),
           str(GUARD_PYTHON), '-I', '-B', str(code/'restricted_egress.py'),
           '--listen-port', '18131', '--upstream-socks', '127.0.0.1:7897']
    for address in denial:
        web += ['--deny-self-ip', address]
    dns = ['/usr/bin/sandbox-exec', '-f', str(ROOT/'profiles/egress.sb'),
           str(GUARD_PYTHON), '-I', '-B', str(code/'restricted_dns.py'),
           '--bound-fd', '<audited-ipv4-udp-fd>', '--bound-ipv6-fd', '<audited-ipv6-udp-fd>',
           '--interface-index', str(interface_index)]
    blockers = []
    if state.get('avd_cloned') is not True:
        blockers.append('candidate_avd_not_cold_cloned')
    else:
        avd = Path(identities['vm']['home'])/'.android/avd'/f'{AVD_NAME}.avd'
        values = dict(line.split('=', 1) for line in _read_regular(avd/'config.ini', owners={600}).decode().splitlines() if '=' in line)
        required = {'AvdId': AVD_NAME, 'hw.cpu.ncore': '6', 'hw.ramSize': '16384',
                    'hw.lcd.width': '1080', 'hw.lcd.height': '1920'}
        if any(values.get(key) != value for key, value in required.items()):
            raise Refused('candidate_avd_configuration_changed')
        if any(value.startswith('/') or '..' in value.replace('\\', '/').split('/') for value in values.values()):
            raise Refused('candidate_avd_external_path')
    supervisor_source = _read_regular(Path(__file__).resolve(), owners={TRUSTED_ROOT_UID, TRUSTED_HOST_UID})
    plan = {'schema': 1, 'mode': 'prepare_only', 'root': str(ROOT), 'identities': identities,
            'interface': interface, 'interface_index': interface_index, 'deny_self_ips': denial,
            'commands': {'web': web, 'dns': dns, 'guest': guest_argv()},
            'environments': {role: child_environment(role) for role in ('vm', 'egress')},
            'pins': {'supervisor_sha256': digest(supervisor_source), 'guest_profile_sha256': profile['sha256'],
                     'module_sha256': hashes, 'sdk_inventory': inventory(ROOT/'shared/sdk', owners={TRUSTED_ROOT_UID}),
                     'python_inventories': runtime, 'egress_profile_sha256': digest(profile_bytes)},
            'egress_profile': profile_bytes.decode(), 'blockers': blockers,
            'limits': {'vm': {'nofile': 1024, 'nproc': 128, 'core': 0},
                       'egress': {'nofile': 128, 'nproc': 32, 'core': 0}},
            'formal_session_guard': {'fixed_port': FORMAL_PORT, 'state': 'TCP ESTABLISHED',
                                     'poll_seconds': FORMAL_POLL_SECONDS, 'root_lsof': True,
                                     'pre_start_check': True, 'atomic': False,
                                     'boundary': 'Up to polling interval plus check latency can overlap; no formal-service lock or modification'},
            'trial_receipt': {'directory': str(ROOT/'reports'), 'directory_uid': 0,
                              'directory_mode': '0755', 'file_uid': 0, 'file_mode': '0644',
                              'format': 'new_nonce_jsonl_fsync_each_event', 'prepare_writes': False,
                              'group_failure_fields': ['errno', 'pgid', 'stage'],
                              'boundary': 'Stop returned verifies recorded PGID disappearance only; detached/new-session or launchd processes remain unaccepted'},
            'candidate_group_cleanup': {'term_wait_seconds': GROUP_TERM_SECONDS,
                                        'kill_wait_seconds': GROUP_KILL_SECONDS,
                                        'poll_seconds': GROUP_POLL_SECONDS,
                                        'signal_only_recorded_pgids': True,
                                        'bounded_wait_eperm_is_pending_not_absence': True,
                                        'ps_fields': ['pid', 'pgid', 'uid'], 'ps_timeout_seconds': 1,
                                        'boundary': 'UID scan and signal are non-atomic; detached/new-session or launchd processes are not enumerated'},
            'source_only_guest_flags': {'real_candidate_restart_validated': False,
                                       'crash_report_mode': 'disabled', 'no_metrics': True,
                                       'no_boot_anim': True, 'explicit_vtb_decoder': '1',
                                       'boundary': 'VTB was already default-on; these flags are not a measured performance fix'},
            'warnings': ['trusted_host_uid_501_can_update_pinned_python_runtime',
                         'udp53_os_filter_is_not_destination_ip_enforcement',
                         'dns_loopback_reply_rule_is_source_port_not_peer_authentication',
                         'guard_file_whitelist_and_real_emulator_not_accepted',
                         'default_allowed_mach_iokit_process_capabilities_unaccepted',
                         'darwin_address_space_limit_unsupported_no_os_memory_ceiling',
                         'self_public_addresses_and_nat_rewrites_need_independent_acceptance'],
            'production_changed': False, 'pf_changed': False, 'isolation_accepted': False}
    plan['plan_sha256'] = digest(_json_bytes(plan))
    return plan


def _child_limits(role: str):
    def apply():
        # Fixed reviewed values only. The supervisor is single-threaded at fork.
        values = ((resource.RLIMIT_NOFILE, 1024 if role == 'vm' else 128),
                  (resource.RLIMIT_NPROC, 128 if role == 'vm' else 32),
                  (resource.RLIMIT_CORE, 0))
        for kind, value in values:
            resource.setrlimit(kind, (value, value))
    return apply


def bind_dns_pair() -> tuple[socket.socket, socket.socket]:
    listeners = []
    try:
        ipv4 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        listeners.append(ipv4)
        ipv4.bind(('127.0.0.1', DNS_PORT))
        ipv6 = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        listeners.append(ipv6)
        ipv6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        ipv6.bind(('::1', DNS_PORT, 0, 0))
        return ipv4, ipv6
    except BaseException:
        for listener in listeners:
            listener.close()
        raise


def _materialize_profile(plan: dict) -> None:
    path = ROOT/'profiles/egress.sb'
    data = plan['egress_profile'].encode()
    if path.exists() or path.is_symlink():
        if digest(_read_regular(path)) != plan['pins']['egress_profile_sha256']:
            raise Refused('existing_guard_profile_differs')
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    try:
        os.fchmod(fd, 0o644)
        cursor = 0
        while cursor < len(data):
            cursor += os.write(fd, data[cursor:])
        os.fsync(fd)
    finally:
        os.close(fd)


def _assert_listener_owner(port: int, pid: int, *, udp: bool = False) -> bool:
    protocol = 'UDP' if udp else 'TCP'
    for address in ('127.0.0.1', '[::1]'):
        args = ['/usr/sbin/lsof', '-nP', '-a', '-i' + protocol + '@' + address + ':' + str(port), '-Fp']
        if not udp:
            args.append('-sTCP:LISTEN')
        result = subprocess.run(args, capture_output=True, text=True, timeout=2,
                                env={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin'}, cwd='/')
        pids = {int(line[1:]) for line in result.stdout.splitlines() if re.fullmatch(r'p[0-9]+', line)}
        if result.returncode != 0 or pids != {pid}:
            return False
    return True


def assert_formal_idle() -> None:
    """Observe only fixed formal-port connection PIDs; failure blocks candidate."""
    if os.getuid() != 0 or os.geteuid() != 0:
        raise Refused('root_formal_session_check_required')
    try:
        result = subprocess.run(
            ['/usr/sbin/lsof', '-nP', '-a', '-iTCP:15556', '-sTCP:ESTABLISHED', '-Fp'],
            capture_output=True, text=True, timeout=2,
            env={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin'}, cwd='/')
    except (OSError, subprocess.TimeoutExpired):
        raise Refused('formal_session_check_failed') from None
    lines = result.stdout.splitlines()
    if result.returncode == 1 and not lines and not result.stderr:
        return
    if (result.returncode != 0 or result.stderr or len(result.stdout) > 4096
            or not lines or any(not re.fullmatch(r'p[0-9]+', line) for line in lines)):
        raise Refused('formal_session_check_failed')
    raise Refused('formal_session_active_stop_candidate')


def _open_role_log_directory(role: str) -> int:
    """Pin all ancestors with directory FDs before touching a service-owned Home."""
    path = ROOT/'homes'/role/'logs'
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open('/', flags)
    current = Path('/')
    try:
        for part in path.parts[1:]:
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd); fd = child
            current /= part
            info = os.fstat(fd)
            home = ROOT/'homes'/role
            expected_uid = ROLES[role][1] if current == home or current == path else TRUSTED_ROOT_UID
            _ownership(info, {expected_uid})
            if current in (home, path) and stat.S_IMODE(info.st_mode) != 0o700:
                raise Refused('role_log_directory_not_private')
        return fd
    except BaseException:
        os.close(fd)
        raise


def create_role_log(role: str, key: str) -> tuple[int, str]:
    if os.geteuid() != 0 or role not in ('vm', 'egress') or key not in ('guest', 'web', 'dns'):
        raise Refused('root_fixed_role_log_required')
    name = 'candidate-' + key + '-' + secrets.token_hex(12) + '.log'
    parent = _open_role_log_directory(role)
    try:
        fd = os.open(name, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent)
    finally:
        os.close(parent)
    try:
        uid = ROLES[role][1]
        os.fchown(fd, uid, uid)
        os.fchmod(fd, 0o600)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != uid or info.st_gid != uid):
            raise Refused('role_log_not_private_regular')
        return fd, str(ROOT/'homes'/role/'logs'/name)
    except BaseException:
        os.close(fd)
        raise


def _open_receipt_directory() -> int:
    """Pin root-owned components; create only the fixed reports leaf."""
    path = ROOT/'reports'
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open('/', flags)
    current = Path('/')
    try:
        for part in path.parts[1:]:
            current /= part
            created = False
            if current == path:
                try:
                    os.mkdir(part, 0o755, dir_fd=fd)
                    created = True
                except FileExistsError:
                    pass
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd); fd = child
            info = os.fstat(fd)
            _ownership(info, {TRUSTED_ROOT_UID})
            if not stat.S_ISDIR(info.st_mode):
                raise Refused('receipt_directory_not_root_owned')
            if current == path:
                if created:
                    os.fchmod(fd, 0o755)
                if stat.S_IMODE(os.fstat(fd).st_mode) != 0o755:
                    raise Refused('receipt_directory_not_0755')
        return fd
    except BaseException:
        os.close(fd)
        raise


class TrialReceipt:
    """Root-only append receipt containing no argv, tokens, payload or errors."""
    def __init__(self):
        if os.getuid() != 0 or os.geteuid() != 0:
            raise Refused('root_trial_receipt_required')
        self.fd, self.sequence = None, 0
        name = 'candidate-trial-' + secrets.token_hex(12) + '.jsonl'
        parent = _open_receipt_directory()
        try:
            fd = os.open(name, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o644, dir_fd=parent)
            try:
                os.fchown(fd, 0, 0)
                os.fchmod(fd, 0o644)
                os.set_inheritable(fd, False)
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_uid != 0 or info.st_gid != 0 or stat.S_IMODE(info.st_mode) != 0o644):
                    raise Refused('receipt_not_root_ordinary')
                os.fsync(parent)
                self.fd = fd
            except BaseException:
                os.close(fd)
                raise
        finally:
            os.close(parent)
        self.path = str(ROOT/'reports'/name)

    def record(self, phase: str, *, key: str | None = None, pid: int | None = None,
               log_path: str | None = None, reason: str | None = None,
               cleanup: str | None = None, group_failure: dict | None = None) -> None:
        if self.fd is None or self.sequence >= 32 or phase not in _RECEIPT_PHASES:
            raise Refused('receipt_event_not_fixed')
        event = {'schema': 1, 'sequence': self.sequence + 1, 'phase': phase}
        if key is not None:
            if key not in ('guest', 'web', 'dns') or isinstance(pid, bool) or not isinstance(pid, int) or not 0 < pid < 2**31:
                raise Refused('receipt_role_not_fixed')
            role = 'vm' if key == 'guest' else 'egress'
            path = Path(log_path or '')
            if (path.parent != ROOT/'homes'/role/'logs' or not _ROLE_LOG_NAME.fullmatch(path.name)
                    or not path.name.startswith('candidate-' + key + '-')):
                raise Refused('receipt_log_path_not_fixed')
            event.update(role=key, pid=pid, uid=ROLES[role][1], private_log_path=str(path))
        elif pid is not None or log_path is not None:
            raise Refused('receipt_role_not_fixed')
        if reason is not None:
            if reason not in _RECEIPT_REASONS:
                raise Refused('receipt_reason_not_fixed')
            event['reason'] = reason
        if cleanup is not None:
            if cleanup not in _RECEIPT_CLEANUP:
                raise Refused('receipt_cleanup_not_fixed')
            event['cleanup'] = cleanup
        if group_failure is not None:
            if (not isinstance(group_failure, dict) or set(group_failure) != {'errno', 'pgid', 'stage'}
                    or isinstance(group_failure['errno'], bool) or not isinstance(group_failure['errno'], int)
                    or not 0 < group_failure['errno'] < 4096
                    or isinstance(group_failure['pgid'], bool) or not isinstance(group_failure['pgid'], int)
                    or not 1 < group_failure['pgid'] < 2**31
                    or group_failure['stage'] not in ('probe', 'signal')):
                raise Refused('receipt_group_failure_not_fixed')
            event['group_failure'] = dict(group_failure)
        data = _json_bytes(event) + b'\n'
        if len(data) > 4096:
            raise Refused('receipt_event_oversized')
        cursor = 0
        while cursor < len(data):
            written = os.write(self.fd, data[cursor:])
            if written <= 0:
                raise OSError('receipt_write_failed')
            cursor += written
        os.fsync(self.fd)
        self.sequence += 1

    def close(self):
        if self.fd is not None:
            fd, self.fd = self.fd, None
            os.close(fd)


def _receipt_failure_reason(error: BaseException) -> str:
    if isinstance(error, Refused):
        return str(error) if str(error) in _RECEIPT_REASONS else 'candidate_refused'
    if isinstance(error, OSError):
        return 'candidate_os_error'
    if isinstance(error, (KeyboardInterrupt, SystemExit)):
        return 'candidate_interrupted'
    return 'candidate_internal_error'


def _group_os_failure(reason: str, pgid: int, stage: str, error: OSError) -> Refused:
    result = Refused(reason)
    if isinstance(error.errno, int) and not isinstance(error.errno, bool) and 0 < error.errno < 4096:
        result.group_failure = {'errno': error.errno, 'pgid': pgid, 'stage': stage}
    return result


def _recorded_group_exists(pgid: int, *, permission_pending: bool = False) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError as error:
        # Only bounded waits opt into pending. EPERM is never evidence of
        # disappearance or permission to signal; deadline and UID checks remain.
        if permission_pending and error.errno == errno.EPERM:
            return True
        raise _group_os_failure('candidate_group_probe_failed', pgid, 'probe', error) from None


def _recorded_group_members_match(pgid: int, uid: int) -> bool:
    """Read only selected group's numeric metadata, never names or argv."""
    try:
        result = subprocess.run(['/bin/ps', '-g', str(pgid), '-o', 'pid=,pgid=,uid='],
                                capture_output=True, text=True, timeout=1,
                                env={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin'}, cwd='/')
    except OSError as error:
        raise _group_os_failure('candidate_group_members_unverified', pgid, 'probe', error) from None
    except subprocess.TimeoutExpired:
        raise Refused('candidate_group_members_unverified') from None
    if result.returncode == 1 and not result.stdout and not result.stderr:
        return False
    if result.returncode != 0 or result.stderr or len(result.stdout) > 65536:
        raise Refused('candidate_group_members_unverified')
    rows = result.stdout.splitlines()
    if not 1 <= len(rows) <= 512:
        raise Refused('candidate_group_members_unverified')
    for row in rows:
        values = row.split()
        if len(values) != 3 or any(not re.fullmatch(r'[0-9]{1,10}', value) for value in values):
            raise Refused('candidate_group_members_unverified')
        pid, actual_pgid, actual_uid = map(int, values)
        if not 1 < pid < 2**31 or actual_pgid != pgid:
            raise Refused('candidate_group_members_unverified')
        if actual_uid != uid:
            raise Refused('candidate_group_identity_changed')
    return True


def _signal_recorded_group(pgid: int, uid: int, signum: int) -> None:
    if signum not in (signal.SIGTERM, signal.SIGKILL):
        raise Refused('candidate_group_signal_failed')
    if not _recorded_group_exists(pgid):
        return
    if not _recorded_group_members_match(pgid, uid):
        if not _recorded_group_exists(pgid):
            return
        raise Refused('candidate_group_members_unverified')
    try:
        os.killpg(pgid, signum)
    except ProcessLookupError:
        pass
    except OSError as error:
        raise _group_os_failure('candidate_group_signal_failed', pgid, 'signal', error) from None


def _wait_recorded_groups(records: list[tuple[int, int, object]], seconds: float) -> list[tuple[int, int, object]]:
    """Reap known leaders, but a live/zombie descendant keeps its group pending."""
    deadline, pending = time.monotonic() + seconds, list(records)
    while pending:
        for _, _, child in records:
            try:
                child.poll()
            except OSError as error:
                raise _group_os_failure('candidate_group_probe_failed', child.pid, 'probe', error) from None
        pending = [record for record in pending if _recorded_group_exists(record[0], permission_pending=True)]
        if not pending:
            return []
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return pending
        time.sleep(min(GROUP_POLL_SECONDS, remaining))
    return []


class CandidateSupervisor:
    def __init__(self, plan: dict):
        self.plan, self.children, self.stopping = plan, [], False
        self.log_paths = {}
        self.receipt = None
        self.last_formal_check = float('-inf')

    def check_formal(self, *, force: bool = False):
        if force or time.monotonic() - self.last_formal_check >= FORMAL_POLL_SECONDS:
            assert_formal_idle()
            self.last_formal_check = time.monotonic()

    def spawn(self, key: str, argv: list[str], pass_fds: tuple[int, ...] = ()):
        role = 'vm' if key == 'guest' else 'egress'
        record = self.plan['identities'][role]
        if os.getuid() != 0 or os.geteuid() != 0:
            raise Refused('root_identity_revalidation_required')
        self.check_formal(force=True)
        _assert_runtime_identity(role, record)
        log_fd, log_path = create_role_log(role, key)
        try:
            child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log_fd,
                                     stderr=log_fd, cwd=record['home'],
                                     env=self.plan['environments'][role], close_fds=True,
                                     pass_fds=pass_fds, user=record['uid'], group=record['gid'],
                                     extra_groups=[], umask=0o077, start_new_session=True,
                                     preexec_fn=_child_limits(role))
        finally:
            os.close(log_fd)
        self.log_paths[key] = log_path
        self.children.append((key, child))
        if self.receipt is not None:
            self.receipt.record('role_started', key=key, pid=child.pid, log_path=log_path)
        return child

    def stop(self):
        # Only these start_new_session child PGIDs; never a UID-wide kill.
        records = []
        for key, child in reversed(self.children):
            pgid = child.pid
            if (key not in ('guest', 'web', 'dns') or isinstance(pgid, bool)
                    or not isinstance(pgid, int) or not 1 < pgid < 2**31
                    or pgid == os.getpgrp() or any(record[0] == pgid for record in records)):
                raise Refused('candidate_group_not_recorded')
            records.append((pgid, ROLES['vm' if key == 'guest' else 'egress'][1], child))
        first_error = None
        for pgid, uid, _ in records:
            try:
                _signal_recorded_group(pgid, uid, signal.SIGTERM)
            except Refused as error:
                first_error = first_error or error
        try:
            survivors = _wait_recorded_groups(records, GROUP_TERM_SECONDS)
        except Refused as error:
            first_error, survivors = first_error or error, records
        for pgid, uid, _ in survivors:
            try:
                _signal_recorded_group(pgid, uid, signal.SIGKILL)
            except Refused as error:
                first_error = first_error or error
        try:
            remaining = _wait_recorded_groups(survivors, GROUP_KILL_SECONDS)
            for pgid, _, _ in remaining:
                try:
                    if _recorded_group_exists(pgid):
                        first_error = first_error or Refused('candidate_group_cleanup_unverified')
                except Refused as error:
                    first_error = first_error or error
        except Refused as error:
            first_error = first_error or error
        if first_error is not None:
            raise first_error

    def run(self, seconds: int) -> dict:
        if not 1 <= seconds <= 300:
            raise Refused('bounded_duration_required')
        self.receipt = TrialReceipt()
        old_handlers = {}
        reason = 'candidate_completed'
        group_failure = None
        try:
            self.receipt.record('started', cleanup='not_started')
            self.check_formal(force=True)
            _materialize_profile(self.plan)
            for signum in (signal.SIGINT, signal.SIGTERM):
                old_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, lambda *_: setattr(self, 'stopping', True))
            web = self.spawn('web', self.plan['commands']['web'])
            deadline = time.monotonic() + 8
            while not _assert_listener_owner(WEB_PORT, web.pid):
                self.check_formal()
                if self.stopping or web.poll() is not None or time.monotonic() >= deadline:
                    raise Refused('guard_web_not_owned_or_ready')
                time.sleep(0.1)
            ipv4, ipv6 = bind_dns_pair()
            try:
                argv = list(self.plan['commands']['dns'])
                argv[argv.index('<audited-ipv4-udp-fd>')] = str(ipv4.fileno())
                argv[argv.index('<audited-ipv6-udp-fd>')] = str(ipv6.fileno())
                dns = self.spawn('dns', argv, (ipv4.fileno(), ipv6.fileno()))
            finally:
                ipv4.close(); ipv6.close()
            deadline = time.monotonic() + 8
            while not _assert_listener_owner(DNS_PORT, dns.pid, udp=True):
                self.check_formal()
                if self.stopping or dns.poll() is not None or time.monotonic() >= deadline:
                    raise Refused('guard_dns_not_owned_or_ready')
                time.sleep(0.1)
            if self.stopping or web.poll() is not None or dns.poll() is not None:
                raise Refused('guards_changed_before_guest_start')
            guest = self.spawn('guest', self.plan['commands']['guest'])
            deadline = time.monotonic() + seconds
            while not self.stopping and time.monotonic() < deadline:
                self.check_formal()
                if any(child.poll() is not None for _, child in self.children):
                    raise Refused('candidate_child_exited_stop_all')
                time.sleep(0.1)
            reason = 'candidate_stop_requested' if self.stopping else 'candidate_completed'
            self.receipt.record('failed' if self.stopping else 'experiment_completed', reason=reason)
            return {'candidate_experiment_seconds': seconds, 'production_changed': False,
                    'pf_changed': False, 'guest_boot_accepted': False, 'isolation_accepted': False,
                    'private_candidate_log_paths': dict(self.log_paths), 'trial_receipt_path': self.receipt.path}
        except BaseException as error:
            reason = _receipt_failure_reason(error)
            group_failure = getattr(error, 'group_failure', None)
            try:
                self.receipt.record('failed', reason=reason, **({'group_failure': group_failure} if group_failure else {}))
            except BaseException:
                pass  # A receipt failure must never prevent stopping candidate groups.
            error.private_candidate_log_paths = dict(self.log_paths)
            error.trial_receipt_path = self.receipt.path
            raise
        finally:
            cleanup, cleanup_error = 'requested', None
            try:
                try:
                    self.receipt.record('cleanup_started', cleanup=cleanup)
                except BaseException as error:
                    cleanup_error = error
                try:
                    self.stop()
                    cleanup = 'stop_returned'
                except BaseException as error:
                    cleanup, cleanup_error = 'stop_failed', error
                    reason = _receipt_failure_reason(error) if isinstance(error, Refused) else 'candidate_cleanup_failed'
                    group_failure = getattr(error, 'group_failure', None)
                try:
                    metadata = {'group_failure': group_failure} if group_failure else {}
                    self.receipt.record('cleanup_finished', cleanup=cleanup, **metadata)
                    self.receipt.record('result', reason=reason, cleanup=cleanup, **metadata)
                except BaseException as error:
                    if cleanup_error is None:
                        cleanup_error = error
            finally:
                try:
                    for signum, old in old_handlers.items():
                        signal.signal(signum, old)
                finally:
                    self.receipt.close()
            if cleanup_error is not None:
                cleanup_error.private_candidate_log_paths = dict(self.log_paths)
                cleanup_error.trial_receipt_path = self.receipt.path
                raise cleanup_error


def execute_plan(plan: dict, expected_sha256: str, seconds: int) -> dict:
    if os.getuid() != 0 or os.geteuid() != 0 or sys.platform != 'darwin':
        raise Refused('root_darwin_supervisor_required')
    executable = Path(sys.executable).resolve(strict=True)
    if not executable.is_relative_to(SYSTEM_PYTHON) or not sys.flags.isolated:
        raise Refused('isolated_system_python_supervisor_required')
    _ownership(executable.lstat(), {TRUSTED_ROOT_UID})
    if not _HEX.fullmatch(expected_sha256 or '') or plan['plan_sha256'] != expected_sha256:
        raise Refused('reviewed_plan_sha_mismatch')
    actual = dict(plan)
    actual.pop('plan_sha256', None)
    if digest(_json_bytes(actual)) != expected_sha256:
        raise Refused('launch_plan_mutated')
    own = Path(__file__).resolve()
    if own != ROOT/'shared/code/isolation_candidate_supervisor.py':
        raise Refused('supervisor_must_be_staged_root_owned')
    if digest(_read_regular(own)) != plan['pins']['supervisor_sha256']:
        raise Refused('staged_supervisor_sha_mismatch')
    if plan['blockers']:
        raise Refused('candidate_plan_has_blockers')
    return CandidateSupervisor(plan).run(seconds)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--interface', default='en7')
    parser.add_argument('--deny-self-ip', action='append', default=[])
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--expected-plan-sha256')
    parser.add_argument('--seconds', type=int, default=120)
    args = parser.parse_args(argv)
    try:
        plan = prepare_plan(args.interface, tuple(args.deny_self_ip))
        result = execute_plan(plan, args.expected_plan_sha256, args.seconds) if args.run else plan
    except (Refused, OSError, KeyError, ValueError) as error:
        if getattr(error, 'private_candidate_log_paths', None):
            print(json.dumps({'private_candidate_log_paths': error.private_candidate_log_paths}), file=sys.stderr)
        if getattr(error, 'trial_receipt_path', None):
            print(json.dumps({'trial_receipt_path': error.trial_receipt_path}), file=sys.stderr)
        parser.error(str(error))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
