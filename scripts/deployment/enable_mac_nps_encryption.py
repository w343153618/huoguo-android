#!/usr/bin/env python3
"""Enable only Cnf.Crypt on existing M1/M5 NPS clients; inspect by default.

Linux cloud root only, with create_dual_udp_nps_tasks.py and its existing admin
helper beside this file. --enable uses the official v0.34.7 /client/edit API,
after a complete restricted conf backup. It never signals/restarts NPS/NPC,
rewrites database files, changes compression, or adds/replaces identities.
Secrets and full before/after snapshots remain in memory or the cloud backup.
"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import secrets
import stat
import sys

try:
    from . import create_dual_udp_nps_tasks as base
except ImportError:
    import create_dual_udp_nps_tasks as base

Refuse = base.Refuse
CLIENT_IDS = frozenset((1466, 1468))
CORE_SHA = '9b9a36a2cae50c5e66f35c3450c9c4ddccc2ff04c839cb0df03592069c674b12'


def core_hash(pid):
    # The existing official executable retains its archive owner's UID1001;
    # do not apply the root-owned *configuration* reader or change ownership.
    # Bind the pinned file to the running executable's actual inode instead.
    path = Path('/etc/nps/nps')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        actual = os.stat(Path('/proc') / str(pid) / 'exe')
        if (not stat.S_ISREG(before.st_mode) or before.st_size > 128 * 1024 * 1024
                or (before.st_dev, before.st_ino) != (actual.st_dev, actual.st_ino)):
            raise Refuse('official_core_not_actual_executable')
        digest = hashlib.sha256()
        while data := os.read(fd, 256 * 1024):
            digest.update(data)
        after = os.fstat(fd)
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise Refuse('core_changed_during_read')
        return digest.hexdigest()
    finally:
        os.close(fd)


def go_escape(value):
    for old, new in (('&', '&amp;'), ("'", '&#39;'), ('<', '&lt;'),
                     ('>', '&gt;'), ('"', '&#34;')):
        value = value.replace(old, new)
    return value


def edit_string(value):
    if not isinstance(value, str) or '\x00' in value:
        raise Refuse('edit_string_contract_invalid')
    raw = html.unescape(value)
    if go_escape(raw) != value:
        raise Refuse('edit_would_change_html_escaped_field')
    return raw


def edit_fields(client, enabled):
    if client.get('Id') not in CLIENT_IDS or type(enabled) is not bool:
        raise Refuse('edit_scope_invalid')
    conf, flow = client.get('Cnf'), client.get('Flow')
    if not isinstance(conf, dict) or not isinstance(flow, dict):
        raise Refuse('edit_config_contract_invalid')
    # EnsureWebPassword can rewrite embedded/invalid TOTP state. This narrow
    # helper refuses that case rather than altering account security settings.
    if client.get('WebTotpSecret') != '' or 'totp:' in client.get('WebPassword', ''):
        raise Refuse('totp_edit_requires_separate_contract')
    black = client.get('BlackIpList')
    if (not isinstance(black, list) or not black or len(set(black)) != len(black)
            or any(not isinstance(s, str) or '\r' in s or '\n' in s for s in black)):
        raise Refuse('blacklist_edit_would_change_structure')
    fields = {'id': client['Id'], 'crypt': str(enabled).lower(), 'flow_reset': 'false'}
    for form, source in (('remark', 'Remark'), ('vkey', 'VerifyKey'),
                         ('web_username', 'WebUserName'), ('web_password', 'WebPassword'),
                         ('web_totp_secret', 'WebTotpSecret')):
        fields[form] = edit_string(client.get(source))
    fields['u'], fields['p'] = edit_string(conf.get('U')), edit_string(conf.get('P'))
    fields['blackiplist'] = edit_string('\r\n'.join(black))
    for form, value in (('compress', conf.get('Compress')),
                        ('config_conn_allow', client.get('ConfigConnAllow'))):
        if type(value) is not bool:
            raise Refuse('edit_boolean_contract_invalid')
        fields[form] = str(value).lower()
    for form, value in (('flow_limit', flow.get('FlowLimit')), ('rate_limit', client.get('RateLimit')),
                        ('max_conn', client.get('MaxConn')), ('max_tunnel', client.get('MaxTunnelNum'))):
        fields[form] = str(base.integer(value, minimum=0))
    # Current clients have no expiry. Refuse a date conversion rather than
    # risking a timezone or dateparse change to a nonzero expiry.
    if flow.get('TimeLimit') != '0001-01-01T00:00:00Z':
        raise Refuse('nonzero_expiry_requires_separate_contract')
    fields['time_limit'] = ''
    return fields


def normalize_client(row, changed):
    value = copy.deepcopy(base.stable_client(row))
    if row.get('Id') in changed:
        value['Cnf']['Crypt'] = False
    return value


def normalize_task(row, changed):
    value = copy.deepcopy(base.stable_task(row))
    if isinstance(value.get('Client'), dict):
        value['Client'] = normalize_client(row['Client'], changed)
    return value


def preservation(before, after, changed):
    if before['process'] != after['process'] or before['geo'] != after['geo']:
        raise Refuse('formal_process_or_domestic_rules_changed')
    if before['conf_hashes'] != after['conf_hashes']:
        raise Refuse('unrelated_config_files_changed')
    if not before['online_ids'].issubset(after['online_ids']):
        raise Refuse('original_online_client_missing')
    for source, normalizer in (('clients', normalize_client), ('tasks', normalize_task),
                               ('runtime_clients', normalize_client), ('runtime_tasks', normalize_task)):
        if set(before[source]) != set(after[source]):
            raise Refuse('client_or_task_identity_set_changed')
        for ident, old in before[source].items():
            if normalizer(old, changed) != normalizer(after[source][ident], changed):
                raise Refuse('unexpected_static_client_or_task_change')
    base.plan(after)
    if before['listeners'] != after['listeners']:
        raise Refuse('formal_udp_listener_owner_changed')
    for ident in CLIENT_IDS:
        expected = True if ident in changed else before['clients'][ident]['Cnf']['Crypt']
        for source in ('clients', 'runtime_clients'):
            if after[source][ident]['Cnf']['Crypt'] is not expected:
                raise Refuse('crypt_readback_differs')
        for task in after['runtime_tasks'].values():
            if task.get('Client', {}).get('Id') == ident:
                if task['Client']['Cnf']['Crypt'] is not expected or task.get('RunStatus') is not True:
                    raise Refuse('runtime_task_crypt_or_running_differs')


def idle(snapshot):
    base.plan(snapshot)
    for ident in CLIENT_IDS:
        if snapshot['runtime_clients'][ident].get('NowConn') != 0:
            raise Refuse('selected_client_active_skip')
    if any(row.get('Client', {}).get('Id') in CLIENT_IDS and row.get('NowConn') != 0
           for row in snapshot['runtime_tasks'].values()):
        raise Refuse('selected_task_active_skip')


class CryptSelection(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inside, self.matches, self.values = False, 0, []

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag == 'select' and fields.get('name') == 'crypt':
            self.inside = True
            self.matches += 1
        if tag == 'option' and self.inside and 'selected' in fields:
            self.values.append(fields.get('value'))

    def handle_endtag(self, tag):
        if tag == 'select':
            self.inside = False


def ui_verified(admin, ident, enabled):
    reader = CryptSelection()
    reader.feed(admin.read('/client/edit?id=' + str(ident)).decode('utf-8', errors='strict'))
    if reader.matches != 1 or reader.values != ['1' if enabled else '0']:
        raise Refuse('web_crypt_selection_readback_failed')
    return True


def backup(snapshot):
    base.private_dir(base.BACKUP_ROOT)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    path = base.BACKUP_ROOT / ('client-encryption-' + stamp + '-' + secrets.token_hex(4))
    base.private_dir(path)
    files, hashes = base.conf_files(), {}
    for source in files:
        name = str(source.relative_to(base.CONF_DIR))
        data = base.read_regular(source)
        target = path / 'conf' / name
        base.private_dir(target.parent)
        base.private_write(target, data)
        digest = hashlib.sha256(data).hexdigest()
        if hashlib.sha256(base.read_regular(target)).hexdigest() != digest:
            raise Refuse('backup_checksum_failed')
        hashes[name] = digest
    if files != base.conf_files() or any(hashlib.sha256(base.read_regular(base.CONF_DIR / name)).hexdigest() != digest
                                         for name, digest in hashes.items()):
        raise Refuse('configuration_changed_during_backup')
    base.private_write(path / 'manifest.json', json.dumps({'process': snapshot['process'],
        'online_ids': sorted(snapshot['online_ids']), 'conf_sha256': hashes}, sort_keys=True).encode())
    return path, len(files)


def execute(admin, enable=False):
    before = base.capture(admin)
    idle(before)
    if core_hash(before['process']['pid']) != CORE_SHA:
        raise Refuse('official_core_sha_differs')
    fields = {ident: edit_fields(before['clients'][ident], True) for ident in sorted(CLIENT_IDS)}
    for ident in CLIENT_IDS:
        ui_verified(admin, ident, before['clients'][ident]['Cnf']['Crypt'])
    changed, destination, count = set(), None, 0
    if enable and any(not before['clients'][i]['Cnf']['Crypt'] for i in CLIENT_IDS):
        destination, count = backup(before)
        # API edits preserve the original complete form fields. Recheck between
        # clients; never blanket-restore a database or an unrelated client.
        for ident in sorted(CLIENT_IDS):
            current = base.capture(admin)
            preservation(before, current, changed)
            idle(current)
            if current['clients'][ident]['Cnf']['Crypt']:
                continue
            result = admin.json('/client/edit', fields[ident])
            if result.get('status') != 1:
                raise Refuse('scoped_client_edit_rejected_or_outcome_unknown')
            changed.add(ident)
    after = base.capture(admin)
    preservation(before, after, changed)
    if core_hash(after['process']['pid']) != CORE_SHA:
        raise Refuse('official_core_changed')
    for ident in CLIENT_IDS:
        ui_verified(admin, ident, after['clients'][ident]['Cnf']['Crypt'])
    result = {'status': 'verified', 'write_requested': enable,
        'changed_client_ids': sorted(changed), 'backup_path': str(destination) if destination else None,
        'backup_regular_files': count, 'backup_checksums_verified': destination is not None,
        'formal_nps_process': after['process'], 'core_sha256': CORE_SHA,
        'original_online_count': len(before['online_ids']), 'after_online_count': len(after['online_ids']),
        'missing_original_online_count': len(before['online_ids'] - after['online_ids']),
        'preservation_verified': True, 'service_signals': 0,
        'clients': [{'id': i, 'crypt': after['clients'][i]['Cnf']['Crypt'],
            'compress': after['clients'][i]['Cnf']['Compress'],
            'mode': after['runtime_clients'][i]['Mode'], 'web_selection_verified': True}
            for i in sorted(CLIENT_IDS)],
        'tasks': [{'id': t['Id'], 'client_id': t['Client']['Id'], 'mode': t['Mode'],
            'port': t['Port'], 'target': t['Target']['TargetStr'], 'running': t['RunStatus'],
            'crypt': t['Client']['Cnf']['Crypt']} for t in after['runtime_tasks'].values()
            if t.get('Client', {}).get('Id') in CLIENT_IDS]}
    if destination:
        base.private_write(destination / 'result.json', json.dumps(result, sort_keys=True).encode())
    return result


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument('--enable', action='store_true')
    options = args.parse_args()
    base.require_host()
    with base.creation_lock():
        admin = base.Admin(*base.settings())
        print(json.dumps(execute(admin, options.enable), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Refuse as exc:
        print(json.dumps({'status': 'refused', 'reason': str(exc)}))
        sys.exit(2)
    except Exception:
        # Do not expose credentials/HTML or request details via tracebacks.
        print(json.dumps({'status': 'failed', 'reason': 'unexpected_reinspect_required'}))
        sys.exit(2)
