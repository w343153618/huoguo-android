#!/usr/bin/env python3
"""Scoped djylb NPS v0.34.7 localhost admin helper; read-only unless --create.

Official API/source contracts:
https://github.com/djylb/nps/blob/v0.34.7/docs/api.md
https://github.com/djylb/nps/blob/v0.34.7/web/controllers/login.go
https://github.com/djylb/nps/blob/v0.34.7/web/controllers/client.go
https://github.com/djylb/nps/blob/v0.34.7/web/controllers/index_tunnel.go

No credentials, cookies, vkeys or their digests are printed. This helper never
edits NPS config/database directly, opens an admin listener, changes filters,
or changes client 1466/task 1409. It creates only the scoped independent M5 pair.
"""
import argparse
import base64
import hashlib
import html
import http.cookiejar
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

CONF = Path('/etc/nps/conf/nps.conf')
CLIENTS = Path('/etc/nps/conf/clients.json')
TASKS = Path('/etc/nps/conf/tasks.json')
STATE = Path('/root/.config/huoguo-m5-npc')
VKEY_FILE = STATE / 'vkey'
PORT = 15558
TARGET = '127.0.0.1:15556'
CLIENT_REMARK = 'HuoguoAndroid-M5-independent'
TASK_REMARK = 'HuoguoAndroid-M5-15558'


class Refuse(RuntimeError):
    pass


def checked_file(path):
    if path.is_symlink() or not path.is_file():
        raise Refuse('required_regular_file_missing')
    return path


def settings():
    values = {}
    for line in checked_file(CONF).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith(('#', ';')) or '=' not in line:
            continue
        key, value = line.split('=', 1)
        values[key.strip()] = value.strip()
    if values.get('web_port') != '18080' or values.get('web_open_ssl', 'false').lower() != 'false':
        raise Refuse('unexpected_local_admin_endpoint')
    if not values.get('web_username') or not values.get('web_password'):
        raise Refuse('existing_admin_credentials_unavailable')
    prefix = values.get('web_base_url', '').rstrip('/')
    if prefix and not re.fullmatch(r'/[A-Za-z0-9_/-]+', prefix):
        raise Refuse('unexpected_admin_base_path')
    return values, 'http://127.0.0.1:18080' + prefix


def records(path):
    raw = checked_file(path).read_text()
    try:
        value = json.loads(raw)
        result = value if isinstance(value, list) else list(value.values()) if isinstance(value, dict) else []
    except json.JSONDecodeError:
        result, decoder, offset = [], json.JSONDecoder(), 0
        while offset < len(raw):
            while offset < len(raw) and raw[offset].isspace():
                offset += 1
            if offset >= len(raw):
                break
            value, offset = decoder.raw_decode(raw, offset)
            result.extend(value if isinstance(value, list) else [value])
    if not result or not all(isinstance(row, dict) for row in result):
        raise Refuse('unsupported_nps_database_format')
    return result


def regular_secret(path):
    checked_file(path)
    if path.stat().st_uid != 0 or stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise Refuse('existing_vkey_permissions_invalid')
    key = path.read_text().strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', key):
        raise Refuse('existing_vkey_format_invalid')
    return key


def new_pair_snapshot():
    clients, tasks = records(CLIENTS), records(TASKS)
    matched = [row for row in clients if row.get('Remark') == CLIENT_REMARK]
    port_tasks = [row for row in tasks if str(row.get('Port')) == str(PORT)]
    named_tasks = [row for row in tasks if row.get('Remark') == TASK_REMARK]
    if len(matched) > 1 or len(port_tasks) > 1 or len(named_tasks) > 1:
        raise Refuse('ambiguous_existing_m5_pair')
    client = matched[0] if matched else None
    task = port_tasks[0] if port_tasks else None
    if client and (type(client.get('Id')) is not int or client['Id'] == 1466):
        raise Refuse('existing_client_is_not_independent')
    if named_tasks and (not task or named_tasks[0].get('Id') != task.get('Id')):
        raise Refuse('named_task_uses_other_port')
    if task:
        if not client or task.get('Client', {}).get('Id') != client['Id']:
            raise Refuse('public_port_owned_by_other_client')
        if task.get('Id') == 1409 or task.get('Remark') != TASK_REMARK or task.get('Mode') != 'tcp':
            raise Refuse('public_port_has_conflicting_task')
        target = task.get('Target', {})
        if target.get('TargetStr') != TARGET or target.get('LocalProxy') is not False or task.get('ServerIp') != '0.0.0.0':
            raise Refuse('existing_task_parameters_differ')
        if task.get('Status') is not True:
            raise Refuse('existing_task_disabled_requires_review')
    key = regular_secret(VKEY_FILE) if VKEY_FILE.exists() else None
    if client:
        if not key or not secrets.compare_digest(str(client.get('VerifyKey', '')), key):
            raise Refuse('existing_client_vkey_not_owned_by_helper')
        if client.get('Status') is not True or client.get('ConfigConnAllow') is not False:
            raise Refuse('existing_client_scope_differ')
    listener = subprocess.run(['ss', '-H', '-ltn', '( sport = :15558 )'], capture_output=True, text=True, check=True)
    if listener.stdout.strip() and not task:
        raise Refuse('public_port_listener_conflict')
    return client, task, key


class Admin:
    def __init__(self, config, base):
        self.base = base
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        page = self.read('/login/index').decode()
        nonce_match = re.search(r'loginNonce\s*:\s*"([^"\r\n]+)"', page)
        cert_match = re.search(r'publicKey\s*:\s*`([^`]+)`', page, re.S)
        pow_match = re.search(r'powEnable\s*:\s*(true|false)', page)
        if not nonce_match or not cert_match:
            raise Refuse('login_contract_not_recognized')
        if pow_match and pow_match.group(1) == 'true':
            raise Refuse('forced_pow_requires_review')
        # Do not weaken captcha/TOTP/PoW. This server currently has ordinary admin login.
        if 'captcha-img' in page:
            raise Refuse('captcha_requires_interactive_login')
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        # Go's HTML template emits JS escapes inside the backtick literal.
        # PEM uses only ASCII, so JSON escape decoding safely restores that public data.
        cert_text = html.unescape(cert_match.group(1))
        try:
            cert_text = json.loads('"' + cert_text.replace('\n', '\\n').replace('\r', '\\r') + '"')
            pub = serialization.load_pem_public_key(cert_text.encode())
        except (ValueError, TypeError) as exc:
            raise Refuse('login_public_certificate_not_recognized') from exc
        server_time = self.json('/auth/gettime', {})
        stamp = int(server_time.get('time', time.time())) * 1000
        payload = json.dumps({'n': nonce_match.group(1), 't': stamp, 'p': config['web_password']},
                             separators=(',', ':')).encode()
        encrypted = base64.b64encode(pub.encrypt(payload, padding.PKCS1v15())).decode()
        result = self.json('/login/verify', {'username': config['web_username'], 'password': encrypted})
        if result.get('status') != 1:
            raise Refuse('admin_login_rejected')
        listing = self.json('/client/list', {'offset': 0, 'limit': 1, 'search': CLIENT_REMARK})
        if not isinstance(listing.get('rows'), list):
            raise Refuse('admin_readback_not_verified')

    def read(self, path, fields=None):
        data = None if fields is None else urllib.parse.urlencode(fields).encode()
        request = urllib.request.Request(self.base + path, data=data,
            headers={'Cache-Control': 'no-store', 'User-Agent': 'HuoguoScopedM5Provision/1'})
        with self.opener.open(request, timeout=15) as response:
            if not response.geturl().startswith(self.base + '/'):
                raise Refuse('admin_redirect_outside_local_endpoint')
            raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise Refuse('admin_response_too_large')
            return raw

    def json(self, path, fields):
        try:
            value = json.loads(self.read(path, fields))
        except json.JSONDecodeError as exc:
            raise Refuse('admin_response_not_json') from exc
        if not isinstance(value, dict):
            raise Refuse('admin_response_shape_invalid')
        return value


def backup():
    if STATE.is_symlink():
        raise Refuse('state_directory_symlink')
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    if STATE.stat().st_uid != 0 or stat.S_IMODE(STATE.stat().st_mode) != 0o700:
        raise Refuse('state_directory_permissions_invalid')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    dest = STATE / ('backup-' + stamp + '-' + secrets.token_hex(4))
    dest.mkdir(mode=0o700)
    for source in [CLIENTS, TASKS, CONF]:
        target = dest / source.name
        shutil.copyfile(checked_file(source), target)
        target.chmod(0o600)
    return dest


def store_key(key):
    fd = os.open(VKEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(key + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--create', action='store_true', help='Create only the scoped M5 client/tunnel after private backups')
    args = parser.parse_args()
    if os.getuid() != 0:
        raise Refuse('run_on_cloud_as_root')
    config, base = settings()
    client, task, key = new_pair_snapshot()
    admin = Admin(config, base)
    if not args.create:
        print(json.dumps({'status': 'preflight_ok', 'admin_login': True,
            'public_port': PORT, 'target': TARGET,
            'client_id': client['Id'] if client else None,
            'task_id': task['Id'] if task else None,
            'vkey_file_path': str(VKEY_FILE), 'key_file_exists': VKEY_FILE.exists()}))
        return
    backup_dir = backup()
    if key is None:
        key = secrets.token_urlsafe(32)
        store_key(key)
    if client is None:
        result = admin.json('/client/add', {'remark': CLIENT_REMARK, 'vkey': key,
            'config_conn_allow': 'false', 'compress': 'false', 'crypt': 'false',
            'max_tunnel': 1, 'max_conn': 0, 'rate_limit': 0, 'flow_limit': 0,
            'web_username': '', 'web_password': '', 'web_totp_secret': '', 'u': '', 'p': ''})
        if result.get('status') != 1 or type(result.get('id')) is not int:
            raise Refuse('client_create_rejected_or_unknown_recheck_before_retry')
        client, task, key = new_pair_snapshot()
        if not client or client['Id'] != result['id']:
            raise Refuse('client_created_but_readback_differs')
    if task is None:
        result = admin.json('/index/add', {'client_id': client['Id'], 'type': 'tcp',
            'server_ip': '0.0.0.0', 'port': PORT, 'target': TARGET,
            'local_proxy': 'false', 'proxy_protocol': 0,
            'remark': TASK_REMARK, 'flow_limit': 0, 'dest_acl_mode': 0})
        if result.get('status') != 1 or type(result.get('id')) is not int:
            raise Refuse('tunnel_create_rejected_or_unknown_recheck_before_retry')
        client, task, key = new_pair_snapshot()
        if not task or task['Id'] != result['id']:
            raise Refuse('tunnel_created_but_readback_differs')
    print(json.dumps({'status': 'created_or_already_valid', 'client_id': client['Id'],
        'task_id': task['Id'], 'public_port': PORT, 'target': TARGET,
        'vkey_file_path': str(VKEY_FILE), 'backup_path': str(backup_dir)}))


if __name__ == '__main__':
    try:
        main()
    except Refuse as exc:
        print(json.dumps({'status': 'refused', 'reason': str(exc)}))
        raise SystemExit(2)
    except Exception as exc:
        # Runtime/HTTP exception details can include credential-adjacent server text.
        print(json.dumps({'status': 'failed', 'error_type': type(exc).__name__}))
        raise SystemExit(3)
