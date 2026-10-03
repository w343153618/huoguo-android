#!/usr/bin/env python3
"""Add two scoped UDP tasks to the existing NPS clients; read-only by default.

Run only as root on the Linux cloud host. Copy create_m5_nps_tunnel.py beside
this file: its existing local settings/login/record readers are reused. --create
is the only write mode. It backs up every regular conf file before /index/add;
it never edits database files, clients, firewall rules or service state.

TCP 15556/15558 stay intact. The added UDP tasks are diagnostic ingress to each
NPC's loopback 45965, not a claim that the released App has UDP media support.
"""
import argparse
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import stat
import subprocess
import sys
import time

_SOURCE = Path(__file__).resolve().with_name('create_m5_nps_tunnel.py')
_SPEC = importlib.util.spec_from_file_location('_huoguo_nps_existing_admin', _SOURCE)
_EXISTING = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_EXISTING)
settings, records, Refuse = _EXISTING.settings, _EXISTING.records, _EXISTING.Refuse

CONF_DIR = Path('/etc/nps/conf')
CLIENTS = CONF_DIR / 'clients.json'
TASKS = CONF_DIR / 'tasks.json'
BACKUP_ROOT = Path('/root/nps-backups')
LOCK = Path('/root/.config/huoguo-dual-udp-nps/creation.lock')
TARGET = '127.0.0.1:45965'
SCOPES = ((1466, 1409, 15556, 'HuoguoAndroid-M1-UDP-15556'),
          (1468, 1411, 15558, 'HuoguoAndroid-M5-UDP-15558'))
PAGE_SIZE, MAX_ROWS = 10000, 50000
READINESS_SECONDS, READINESS_POLL_SECONDS = 5.0, 0.1
_INSPECTION_DEADLINE = ContextVar('dual_udp_inspection_deadline', default=None)

# These are live counters/derived state, not client configuration. Everything
# else (including vkeys, Cnf, limits, auth and unknown fields) stays in the
# comparison, in memory only. Flow limits/expiry are deliberately retained.
CLIENT_RUNTIME = frozenset(('Mode', 'Addr', 'LocalAddr', 'IsConnect',
                           'ExportFlow', 'InletFlow', 'Rate', 'NowConn',
                           'Version', 'LastOnlineTime'))
TASK_RUNTIME = frozenset(('RunStatus', 'NowConn', 'TargetAddr', 'HealthNextTime',
                         'HealthMap', 'HealthRemoveArr'))


def remaining_timeout(maximum=15):
    deadline = _INSPECTION_DEADLINE.get()
    if deadline is None:
        return maximum
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise Refuse('inspection_deadline_elapsed')
    return min(maximum, remaining)


class _DeadlineOpener:
    def __init__(self, original):
        self.original = original

    def open(self, request, timeout=15):
        return self.original.open(request, timeout=remaining_timeout(timeout))


class Admin(_EXISTING.Admin):
    """Reuse the existing authenticated reader, with a clipped read-only budget."""
    def read(self, path, fields=None):
        original = self.opener
        self.opener = _DeadlineOpener(original)
        try:
            value = super().read(path, fields)
            remaining_timeout()
            return value
        finally:
            self.opener = original


@contextmanager
def inspection_budget(deadline):
    token = _INSPECTION_DEADLINE.set(deadline)
    try:
        yield
    finally:
        _INSPECTION_DEADLINE.reset(token)


def require_host():
    if not sys.platform.startswith('linux') or os.getuid() != 0 or os.geteuid() != 0:
        raise Refuse('linux_cloud_root_required')


def integer(value, minimum=1):
    if type(value) is not int or value < minimum:
        raise Refuse('numeric_contract_invalid')
    return value


def indexed(rows):
    result = {}
    for row in rows:
        if not isinstance(row, dict) or type(row.get('Id')) is not int or row['Id'] in result:
            raise Refuse('record_identity_ambiguous')
        result[row['Id']] = row
    return result


def stable_client(row):
    value = {k: v for k, v in row.items() if k not in CLIENT_RUNTIME}
    if isinstance(value.get('Flow'), dict):
        value['Flow'] = {k: v for k, v in value['Flow'].items()
                         if k not in ('ExportFlow', 'InletFlow')}
    return value


def stable_task(row):
    value = {k: v for k, v in row.items() if k not in TASK_RUNTIME}
    if isinstance(value.get('Client'), dict):
        value['Client'] = stable_client(value['Client'])
    if isinstance(value.get('Flow'), dict):
        value['Flow'] = {k: v for k, v in value['Flow'].items()
                         if k not in ('ExportFlow', 'InletFlow')}
    if isinstance(value.get('Target'), dict):
        value['Target'] = {k: v for k, v in value['Target'].items() if k != 'TargetArr'}
    return value


def runtime_rows(admin, endpoint, fields=None):
    rows, total = [], None
    while total is None or len(rows) < total:
        response = admin.json(endpoint, dict(fields or {}, offset=len(rows), limit=PAGE_SIZE))
        count, page = response.get('total'), response.get('rows')
        if type(count) is not int or not 0 <= count <= MAX_ROWS or not isinstance(page, list):
            raise Refuse('runtime_table_contract_invalid')
        if total is not None and total != count:
            raise Refuse('runtime_table_changed_during_read')
        total = count
        if len(page) > PAGE_SIZE or len(rows) + len(page) > total or (not page and len(rows) < total):
            raise Refuse('runtime_table_incomplete')
        rows.extend(page)
    indexed(rows)
    return rows


def command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, check=True, timeout=remaining_timeout())
    except (subprocess.SubprocessError, OSError) as exc:
        raise Refuse('readonly_system_inspection_failed') from exc
    if len(result.stdout.encode()) > 8 * 1024 * 1024:
        raise Refuse('readonly_system_output_too_large')
    return result.stdout


def process_identity():
    if command(['systemctl', 'is-active', 'nps.service']).strip() != 'active':
        raise Refuse('formal_nps_not_active')
    raw = command(['systemctl', 'show', 'nps.service', '--property=MainPID', '--value']).strip()
    if not re.fullmatch(r'[1-9][0-9]*', raw):
        raise Refuse('formal_nps_pid_invalid')
    pid = int(raw)
    proc = Path('/proc') / raw
    try:
        if proc.stat().st_uid != 0 or Path(os.readlink(proc / 'exe')).name != 'nps':
            raise Refuse('formal_nps_process_not_recognized')
        # Fields after '(comm)' start with Linux stat field 3; starttime is 22.
        start_ticks = integer(int((proc / 'stat').read_text().rsplit(')', 1)[1].split()[19]))
    except (OSError, ValueError, IndexError) as exc:
        raise Refuse('formal_nps_process_not_recognized') from exc
    return {'pid': pid, 'start_ticks': start_ticks}


def geo_reference():
    full = command(['iptables', '-w', '5', '-t', 'filter', '-S'])
    result = {}
    for chain in ('geo_in', 'geo_fwd'):
        rules, references = [], []
        for line in full.splitlines():
            try:
                tokens = shlex.split(line)
            except ValueError as exc:
                raise Refuse('geo_reference_invalid') from exc
            if len(tokens) >= 2 and tokens[0] in ('-N', '-A') and tokens[1] == chain:
                rules.append(line)
            for flag in ('-j', '-g'):
                if flag in tokens and tokens.index(flag) + 1 < len(tokens) and tokens[tokens.index(flag) + 1] == chain:
                    references.append(line)
                    break
        if not rules or not references:
            raise Refuse('existing_geo_chain_or_reference_missing')
        text, refs = '\n'.join(rules) + '\n', '\n'.join(references) + '\n'
        result[chain] = {'rules': text, 'references': refs,
                         'rules_sha256': hashlib.sha256(text.encode()).hexdigest(),
                         'references_sha256': hashlib.sha256(refs.encode()).hexdigest()}
    return result


def udp_listeners():
    result = {}
    for _, _, port, _ in SCOPES:
        raw = command(['ss', '-H', '-l', '-u', '-n', '-p', '( sport = :%d )' % port])
        owners = []
        for line in raw.splitlines():
            matches = re.findall(r'\bpid=([1-9][0-9]*)\b', line)
            if not matches:
                raise Refuse('udp_listener_ownership_unavailable')
            owners.extend(int(value) for value in matches)
        result[port] = frozenset(owners)
    return result


def conf_files():
    if CONF_DIR.is_symlink() or not CONF_DIR.is_dir():
        raise Refuse('conf_directory_invalid')
    files = []
    for base, dirs, names in os.walk(CONF_DIR, followlinks=False):
        for name in dirs + names:
            path = Path(base) / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) or (not stat.S_ISDIR(mode) and not stat.S_ISREG(mode)):
                raise Refuse('conf_contains_nonregular_entry')
            if stat.S_ISREG(mode):
                files.append(path)
    if not files or CLIENTS not in files or TASKS not in files or CONF_DIR / 'nps.conf' not in files:
        raise Refuse('required_conf_files_missing')
    return sorted(files)


def read_regular(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_size > 64 * 1024 * 1024:
            raise Refuse('conf_file_ownership_or_size_invalid')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            data = stream.read(64 * 1024 * 1024 + 1)
        end = os.fstat(fd)
        if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (end.st_size, end.st_mtime_ns, end.st_ctime_ns):
            raise Refuse('conf_changed_during_read')
        return data
    finally:
        os.close(fd)


def capture(admin):
    # All captures are read-only. Secret-bearing maps are never serialized to stdout.
    source_files = conf_files()
    clients, tasks = indexed(records(CLIENTS)), indexed(records(TASKS))
    # This API's type='' / type='all' returns no admin-global tasks. Query the
    # actual persisted modes separately, including UDP when it has no rows yet.
    modes = {row.get('Mode') for row in tasks.values()} | {'udp'}
    if any(not isinstance(mode, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,31}', mode) for mode in modes):
        raise Refuse('persisted_task_mode_contract_invalid')
    runtime_tasks = indexed([row for mode in sorted(modes)
                             for row in runtime_rows(admin, '/index/gettunnel', {'client_id': 0, 'type': mode})])
    if not set(tasks).issubset(runtime_tasks):
        raise Refuse('original_runtime_task_table_incomplete')
    snapshot = {'process': process_identity(), 'geo': geo_reference(),
                'clients': clients, 'tasks': tasks,
                'runtime_clients': indexed(runtime_rows(admin, '/client/list')),
                'runtime_tasks': runtime_tasks,
                'listeners': udp_listeners(), 'conf_hashes': {}}
    snapshot['online_ids'] = frozenset(key for key, row in snapshot['runtime_clients'].items()
                                      if row.get('IsConnect') is True)
    for path in source_files:
        if path not in (CLIENTS, TASKS):
            snapshot['conf_hashes'][str(path.relative_to(CONF_DIR))] = hashlib.sha256(read_regular(path)).hexdigest()
    return snapshot


def exact_udp(row, scope):
    client, _, port, remark = scope
    target, flow = row.get('Target'), row.get('Flow')
    if not isinstance(target, dict) or (flow is not None and not isinstance(flow, dict)):
        return False
    auth = row.get('UserAuth')
    auth_empty = auth is None or (isinstance(auth, dict) and auth.get('Content', '') == ''
                                  and auth.get('AccountMap') in (None, {})
                                  and not set(auth) - {'Content', 'AccountMap'})
    return (type(row.get('Id')) is int and row['Id'] > 0 and row.get('Mode') == 'udp'
            and type(row.get('Port')) is int and row['Port'] == port
            and row.get('Client', {}).get('Id') == client and row.get('Remark') == remark
            and row.get('ServerIp') == '0.0.0.0' and row.get('Status') is True
            and target.get('TargetStr') == TARGET and target.get('LocalProxy') is False
            and target.get('ProxyProtocol', 0) == 0 and row.get('DestAclMode', 0) == 0
            and row.get('DestAclRules', '') == '' and row.get('Password', '') == ''
            and row.get('LocalPath', '') == '' and row.get('StripPre', '') == ''
            and row.get('HttpProxy', False) is False and row.get('Socks5Proxy', False) is False
            and row.get('TargetType', '') in ('', 'all')
            and (flow or {}).get('FlowLimit', 0) == 0
            and (flow or {}).get('TimeLimit', '0001-01-01T00:00:00Z') == '0001-01-01T00:00:00Z'
            and auth_empty)


def plan(snapshot, starting_ids=()):
    result, keys = [], []
    for scope in SCOPES:
        client_id, task_id, port, remark = scope
        client, live = snapshot['clients'].get(client_id), snapshot['runtime_clients'].get(client_id)
        if not client or not live or client.get('Status') is not True or live.get('Status') is not True:
            raise Refuse('required_client_missing_or_disabled')
        config = client.get('Cnf')
        if not isinstance(config, dict) or type(config.get('Compress')) is not bool or type(config.get('Crypt')) is not bool:
            raise Refuse('client_config_contract_invalid')
        key = client.get('VerifyKey')
        if not isinstance(key, str) or not key or type(client.get('MaxTunnelNum')) is not int or client['MaxTunnelNum'] < 0:
            raise Refuse('client_identity_or_limit_contract_invalid')
        keys.append(key)
        if (live.get('IsConnect') is not True or not isinstance(live.get('Mode'), str)
                or not live['Mode'] or any(mode != 'quic' for mode in live['Mode'].split(','))):
            raise Refuse('required_client_not_online_quic')
        if live.get('Cnf') != config or (live.get('VerifyKey') is not None and live['VerifyKey'] != key):
            raise Refuse('runtime_client_identity_or_config_differs')
        for source in ('tasks', 'runtime_tasks'):
            old = snapshot[source].get(task_id)
            if (not old or old.get('Client', {}).get('Id') != client_id or old.get('Mode') != 'tcp'
                    or type(old.get('Port')) is not int or old['Port'] != port or old.get('Status') is not True
                    or old.get('ServerIp') != '0.0.0.0' or old.get('Target', {}).get('TargetStr') != '127.0.0.1:15556'
                    or old.get('Target', {}).get('LocalProxy') is not False):
                raise Refuse('formal_tcp_task_ownership_or_parameters_differ')
            if source == 'runtime_tasks' and old.get('RunStatus') is not True:
                raise Refuse('formal_tcp_task_not_running')
        candidates = [row for row in snapshot['tasks'].values()
                      if (row.get('Mode') == 'udp' and row.get('Port') == port) or row.get('Remark') == remark]
        if len(candidates) > 1 or (candidates and not exact_udp(candidates[0], scope)):
            raise Refuse('udp_task_conflict_or_parameters_differ')
        task = candidates[0] if candidates else None
        live_matches = [row for row in snapshot['runtime_tasks'].values()
                        if (row.get('Mode') == 'udp' and row.get('Port') == port) or row.get('Remark') == remark]
        if not task:
            if live_matches or snapshot['listeners'][port]:
                raise Refuse('udp_port_or_runtime_task_conflict')
        else:
            if (len(live_matches) != 1 or live_matches[0].get('Id') != task['Id']
                    or not exact_udp(live_matches[0], scope) or type(live_matches[0].get('RunStatus')) is not bool):
                raise Refuse('owned_udp_task_not_running_or_readback_differs')
            if live_matches[0]['RunStatus'] is not True and task['Id'] not in starting_ids:
                raise Refuse('owned_udp_task_not_running_or_readback_differs')
            allowed_listeners = (frozenset(), frozenset((snapshot['process']['pid'],))) if task['Id'] in starting_ids else (frozenset((snapshot['process']['pid'],)),)
            if snapshot['listeners'][port] not in allowed_listeners:
                raise Refuse('owned_udp_listener_not_formal_nps')
        result.append({'scope': scope, 'task': task})
    if secrets.compare_digest(keys[0], keys[1]):
        raise Refuse('m1_m5_client_identity_not_independent')
    return result


def preserved(before, after, created_ids=(), starting_ids=()):
    if before['process'] != after['process']:
        raise Refuse('formal_nps_process_changed')
    if before['geo'] != after['geo']:
        raise Refuse('domestic_geo_reference_changed')
    if before['conf_hashes'] != after['conf_hashes']:
        raise Refuse('unrelated_conf_file_changed')
    if not before['online_ids'].issubset(after['online_ids']):
        raise Refuse('original_online_client_missing')
    for source, transform in (('clients', stable_client), ('tasks', stable_task)):
        old, new = before[source], after[source]
        permitted = set(created_ids) if source == 'tasks' else set()
        if set(new) != set(old) | permitted or any(transform(row) != transform(new.get(key, {})) for key, row in old.items()):
            raise Refuse('original_client_or_task_config_changed')
    for source, transform in (('runtime_clients', stable_client), ('runtime_tasks', stable_task)):
        for key, row in before[source].items():
            if key not in after[source] or transform(row) != transform(after[source][key]):
                raise Refuse('original_runtime_config_changed')
    plan(after, starting_ids)


def await_created_ready(admin, before, created_ids, task_id):
    deadline = time.monotonic() + READINESS_SECONDS
    while True:
        try:
            with inspection_budget(deadline):
                current = capture(admin)
                preserved(before, current, created_ids, (task_id,))
                row = current['runtime_tasks'].get(task_id)
                if row is not None and row.get('RunStatus') is True and current['listeners'][row['Port']] == frozenset((current['process']['pid'],)):
                    remaining_timeout()
                    return current
        except Refuse as exc:
            if time.monotonic() < deadline:
                raise
            raise Refuse('new_udp_task_start_outcome_unknown_reinspect_required') from exc
        except Exception as exc:
            raise Refuse('new_udp_task_readback_failed_reinspect_required') from exc
        if time.monotonic() >= deadline:
            raise Refuse('new_udp_task_start_outcome_unknown_reinspect_required')
        time.sleep(min(READINESS_POLL_SECONDS, max(0, deadline - time.monotonic())))


def private_dir(path):
    if path.is_symlink():
        raise Refuse('private_directory_symlink')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700 or not stat.S_ISDIR(info.st_mode):
        raise Refuse('private_directory_permissions_invalid')


def private_write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def backup(snapshot):
    private_dir(BACKUP_ROOT)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    destination = BACKUP_ROOT / ('dual-udp-' + stamp + '-' + secrets.token_hex(4))
    private_dir(destination)
    files, hashes = conf_files(), {}
    for source in files:
        relative = source.relative_to(CONF_DIR)
        target = destination / 'conf' / relative
        private_dir(target.parent)
        data = read_regular(source)
        private_write(target, data)
        digest = hashlib.sha256(data).hexdigest()
        if hashlib.sha256(read_regular(target)).hexdigest() != digest:
            raise Refuse('backup_copy_checksum_failed')
        hashes[str(relative)] = digest
    if files != conf_files() or any(hashlib.sha256(read_regular(CONF_DIR / name)).hexdigest() != digest for name, digest in hashes.items()):
        raise Refuse('conf_changed_during_backup')
    metadata = {'created_utc': stamp, 'process': snapshot['process'],
                'online_ids': sorted(snapshot['online_ids']), 'geo': snapshot['geo'],
                'conf_sha256': hashes, 'verified_regular_files': len(hashes)}
    private_write(destination / 'manifest.json', json.dumps(metadata, sort_keys=True).encode() + b'\n')
    return destination, len(hashes)


@contextmanager
def creation_lock():
    private_dir(LOCK.parent)
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600 or not stat.S_ISREG(info.st_mode):
            raise Refuse('creation_lock_permissions_invalid')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Refuse('another_dual_udp_helper_active') from exc
        yield
    finally:
        os.close(fd)


def add_fields(scope):
    client, _, port, remark = scope
    return {'client_id': client, 'type': 'udp', 'server_ip': '0.0.0.0',
            'port': port, 'target': TARGET, 'local_proxy': 'false',
            'proxy_protocol': 0, 'remark': remark, 'flow_limit': 0, 'dest_acl_mode': 0}


def summary(snapshot, tasks, created=(), backup_path=None, backup_files=0):
    return {'status': 'verified', 'read_only': backup_path is None,
            'formal_nps_pid': snapshot['process']['pid'], 'online_count': len(snapshot['online_ids']),
            'backup_path': str(backup_path) if backup_path else None, 'backup_regular_files': backup_files,
            'backup_checksums_verified': backup_path is not None,
            'created_task_ids': list(created), 'preservation_verified': True,
            'tasks': [{'client_id': item['scope'][0], 'tcp_task_id': item['scope'][1],
                       'port': item['scope'][2], 'udp_task_id': item['task']['Id'] if item['task'] else None,
                       'udp_present': item['task'] is not None, 'target': TARGET}
                      for item in tasks]}


def execute(admin, create=False):
    before = capture(admin)
    planned = plan(before)
    if not create or all(item['task'] for item in planned):
        # Even read-only/idempotent runs verify observations did not drift.
        after = capture(admin)
        preserved(before, after)
        return summary(after, plan(after))
    with creation_lock():
        current = capture(admin)
        preserved(before, current)
        destination, count = backup(current)
        current = capture(admin)
        preserved(before, current)
        created = []
        try:
            for item in plan(current):
                if item['task']:
                    continue
                scope, client = item['scope'], current['clients'][item['scope'][0]]
                limit = client['MaxTunnelNum']
                task_count = sum(row.get('Client', {}).get('Id') == scope[0] for row in current['tasks'].values())
                if limit and task_count >= limit:
                    raise Refuse('client_max_tunnel_limit_rejected_no_client_change')
                # Revalidate immediately before each mutation, including all online IDs.
                fresh = capture(admin)
                preserved(before, fresh, created)
                current = fresh
                if plan(current)[SCOPES.index(scope)]['task'] is not None:
                    raise Refuse('udp_task_appeared_concurrently_reinspect_required')
                try:
                    response = admin.json('/index/add', add_fields(scope))
                except Exception as exc:
                    # No blind retry: the API may have persisted a task before a lost response.
                    raise Refuse('udp_task_add_outcome_unknown_reinspect_required') from exc
                if type(response.get('status')) is not int or response['status'] != 1:
                    if 'number of tunnels exceeds the limit' in str(response.get('msg', '')).lower():
                        raise Refuse('client_max_tunnel_limit_rejected_no_client_change')
                    raise Refuse('udp_task_add_rejected_reinspect_required')
                created.append(integer(response.get('id')))
                current = await_created_ready(admin, before, created, created[-1])
                matched = plan(current)[SCOPES.index(scope)]['task']
                if matched is None or matched['Id'] != created[-1]:
                    raise Refuse('created_udp_task_identity_not_verified')
            after = capture(admin)
            preserved(before, after, created)
            private_write(destination / 'result.json', json.dumps(summary(after, plan(after), created, destination, count), sort_keys=True).encode() + b'\n')
            return summary(after, plan(after), created, destination, count)
        except Refuse as exc:
            exc.created_task_ids, exc.backup_path = tuple(created), str(destination)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--create', action='store_true', help='Back up, then add missing scoped UDP tasks via localhost API.')
    args = parser.parse_args()
    require_host()
    config, base = settings()
    result = execute(Admin(config, base), create=args.create)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Refuse as exc:
        receipt = {'status': 'refused', 'reason': str(exc), 'credentials_printed': False}
        if hasattr(exc, 'created_task_ids'):
            receipt.update(created_task_ids=list(exc.created_task_ids), backup_path=exc.backup_path)
        print(json.dumps(receipt), file=sys.stderr)
        raise SystemExit(2)
    except Exception as exc:
        print(json.dumps({'status': 'failed', 'reason': type(exc).__name__, 'credentials_printed': False}), file=sys.stderr)
        raise SystemExit(3)
