"""Fetch metadata from private Git; fetch APKs from immutable release assets or pinned M1 TLS.

New updates commits contain metadata only. M1 uses its existing gh authentication;
M5 can retain its read-only Git SSH key and download the public APK through a
certificate-pinned, physical-interface-bound HTTPS socket without proxy fallback.
"""
import hashlib
import http.client
import ipaddress
import json
import os
import pathlib
import re
import shlex
import socket
import ssl
import subprocess
import sys
import tempfile
import urllib.parse

from download_page import sync_downloads

REPOSITORY = 'w343153618/huoguo-android'
RELEASE_ASSET = 'HuoguoAndroid.apk'
MAX_APK_SIZE = 67108864


def validate_metadata(metadata, base):
    if not isinstance(metadata, dict):
        raise RuntimeError('Release metadata must be an object')
    source = urllib.parse.urlsplit(base)
    if source.scheme != 'https' or not source.hostname or source.username or source.password or source.query or source.fragment:
        raise RuntimeError('Update base URL requires HTTPS without credentials or redirects')
    version, code = metadata.get('version_name'), metadata.get('version_code')
    if not isinstance(version, str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]+){1,3}', version):
        raise RuntimeError('Invalid release version')
    if type(code) is not int or code < 1:
        raise RuntimeError('Invalid release version code')
    name = 'HuoguoAndroid-v' + version + '.apk'
    if metadata.get('apk_url') != base.rstrip('/') + '/' + name:
        raise RuntimeError('Unexpected download URL')
    size = metadata.get('apk_size')
    if type(size) is not int or not 0 < size <= MAX_APK_SIZE:
        raise RuntimeError('Invalid release size')
    if not isinstance(metadata.get('sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', metadata['sha256']):
        raise RuntimeError('Invalid release digest')
    if 'release_tag' in metadata and metadata['release_tag'] != 'v' + version:
        raise RuntimeError('Release tag must match version')
    return name


def validate_apk(apk, metadata):
    if len(apk) != metadata['apk_size'] or hashlib.sha256(apk).hexdigest() != metadata['sha256']:
        raise RuntimeError('Release digest/size mismatch')
    return apk


def download_release_asset(metadata, cache, repository=REPOSITORY):
    """The authenticated gh process writes only into an owner-only temporary directory."""
    with tempfile.TemporaryDirectory(prefix='.release-', dir=cache) as folder:
        subprocess.run(['gh', 'release', 'download', metadata['release_tag'], '--repo', repository,
                        '--pattern', RELEASE_ASSET, '--dir', folder],
                       check=True, capture_output=True, timeout=180)
        candidate = pathlib.Path(folder) / RELEASE_ASSET
        if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size != metadata['apk_size']:
            raise RuntimeError('Release asset size/type mismatch')
        return validate_apk(candidate.read_bytes(), metadata)


def physical_ipv4(interface):
    if not re.fullmatch(r'en[0-9]+', interface):
        raise RuntimeError('Update source interface must be a physical en interface')
    result = subprocess.run(['/sbin/ifconfig', interface], capture_output=True, text=True, timeout=5)
    if result.returncode:
        return None
    for address in re.findall(r'^\s*inet ([0-9.]+)\b', result.stdout, re.MULTILINE):
        candidate = ipaddress.IPv4Address(address)
        if not candidate.is_loopback and not candidate.is_link_local and not candidate.is_unspecified:
            return str(candidate)
    return None


def pinned_context(certificate):
    pem = pathlib.Path(certificate).read_text()
    certs = re.findall(r'-----BEGIN CERTIFICATE-----[\s\S]*?-----END CERTIFICATE-----', pem)
    if len(certs) != 1:
        raise RuntimeError('Update source requires exactly one pinned public certificate')
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False  # Exact public certificate identity is checked below.
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_verify_locations(cadata=certs[0])
    pin = hashlib.sha256(ssl.PEM_cert_to_DER_cert(certs[0])).digest()
    return context, pin


def download_pinned_apk(metadata, certificate, interfaces=('en11', 'en0')):
    """No requests/urllib proxy behavior, redirects, unbound fallback or credential transfer."""
    if sys.platform != 'darwin':
        raise RuntimeError('Physical-interface update download requires Darwin IP_BOUND_IF')
    source = urllib.parse.urlsplit(metadata['apk_url'])
    if source.scheme != 'https' or source.username or source.password or source.query or source.fragment:
        raise RuntimeError('Update source requires an explicit HTTPS APK URL')
    try:
        destination = str(ipaddress.IPv4Address(source.hostname))
    except (ValueError, TypeError):
        raise RuntimeError('Physical update source must be an explicit IPv4 address') from None
    if not interfaces:
        raise RuntimeError('No physical update source interfaces configured')
    # Validate the complete allowlist before opening a socket.
    if any(not re.fullmatch(r'en[0-9]+', interface) for interface in interfaces):
        raise RuntimeError('Update source interface must be a physical en interface')
    context, pin = pinned_context(certificate)
    errors = []
    for interface in interfaces:
        address = physical_ipv4(interface)
        if not address:
            errors.append(interface + ':no_ipv4')
            continue
        raw = secure = connection = response = None
        try:
            raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            raw.settimeout(30)
            raw.setsockopt(socket.IPPROTO_IP, 25, socket.if_nametoindex(interface))  # Darwin IP_BOUND_IF
            raw.bind((address, 0))
            raw.connect((destination, source.port or 443))
            secure = context.wrap_socket(raw, server_hostname=source.hostname)
            actual = hashlib.sha256(secure.getpeercert(binary_form=True)).digest()
            if actual != pin:
                raise RuntimeError('Update source public certificate mismatch')
            connection = http.client.HTTPConnection(source.hostname, source.port or 443, timeout=30)
            connection.sock = secure
            connection.request('GET', source.path, headers={'Connection': 'close', 'User-Agent': 'HuoguoUpdate/1'})
            response = connection.getresponse()
            if response.status != 200:
                raise RuntimeError('Update source HTTP status ' + str(response.status))
            content_length = response.getheader('Content-Length')
            if content_length is not None and content_length != str(metadata['apk_size']):
                raise RuntimeError('Update source Content-Length mismatch')
            data = bytearray()
            while len(data) <= metadata['apk_size']:
                piece = response.read(min(65536, metadata['apk_size'] + 1 - len(data)))
                if not piece:
                    break
                data.extend(piece)
            return validate_apk(bytes(data), metadata)
        except (OSError, RuntimeError, http.client.HTTPException) as error:
            # Do not include response bodies, credentials, environment or exception arguments.
            errors.append(interface + ':' + type(error).__name__)
        finally:
            if response is not None:
                response.close()
            if connection is not None:
                connection.close()
            if secure is not None:
                secure.close()
            if raw is not None:
                raw.close()
    raise RuntimeError('Pinned physical update download failed (' + ', '.join(errors) + ')')


def fetch_apk(metadata, git, cache, use_gh, environ):
    if 'release_tag' not in metadata:
        # Read-only compatibility with already published legacy Git-blob releases.
        return validate_apk(git('show', 'FETCH_HEAD:' + RELEASE_ASSET), metadata)
    if use_gh:
        return download_release_asset(metadata, cache)
    certificate = environ.get('UPDATE_SOURCE_CERT')
    if not certificate:
        raise RuntimeError('Metadata-only updates require UPDATE_USE_GH=1 or UPDATE_SOURCE_CERT')
    interfaces = tuple(part.strip() for part in environ.get('UPDATE_SOURCE_INTERFACES', 'en11,en0').split(',') if part.strip())
    return download_pinned_apk(metadata, certificate, interfaces)


def atomic(updates, path, data):
    fd, temporary = tempfile.mkstemp(prefix='.incoming-', dir=updates)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    root = pathlib.Path(os.environ.get('DIRECT_STATE_DIR', str(pathlib.Path.home() / 'Documents/ChatGPT/others/android-remote/m1-compare')))
    key_root = pathlib.Path(os.environ.get('UPDATE_KEY_DIR', str(pathlib.Path.home() / 'Library/Application Support/AndroidRemote/direct')))
    use_gh = os.environ.get('UPDATE_USE_GH') == '1'
    cache = root / 'github-update-cache'
    cache.mkdir(mode=0o700, exist_ok=True)
    ssh = ['/usr/bin/ssh', '-p', '443', '-o', 'HostKeyAlias=github.com', '-o', 'StrictHostKeyChecking=yes',
           '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'IdentitiesOnly=yes',
           '-o', 'UserKnownHostsFile="' + str(key_root / 'github-known_hosts') + '"', '-i', str(key_root / 'github-update-key')]
    environment = dict(os.environ) if use_gh else dict(os.environ, GIT_SSH_COMMAND=shlex.join(ssh))

    def git(*arguments):
        command = ['/usr/bin/git', '-C', str(cache)] + (['-c', 'credential.helper=!gh auth git-credential'] if use_gh else [])
        result = subprocess.run([*command, *arguments], env=environment, capture_output=True, timeout=90)
        if result.returncode:
            # Git stderr can include remote data; keep failures bounded and secret-free.
            raise RuntimeError('Git sync failed (exit ' + str(result.returncode) + ')')
        return result.stdout

    if not (cache / '.git').exists():
        git('init')
        git('remote', 'add', 'origin', ('https://github.com/' if use_gh else 'ssh://git@ssh.github.com/') + REPOSITORY + '.git')
    if use_gh:
        git('remote', 'set-url', 'origin', 'https://github.com/' + REPOSITORY + '.git')
    git('fetch', '--depth=1', 'origin', 'updates')
    metadata = json.loads(git('show', 'FETCH_HEAD:update.json'))
    name = validate_metadata(metadata, os.environ.get('UPDATE_BASE_URL', 'https://146.56.249.175:15556/updates'))
    updates = root / 'updates'
    updates.mkdir(mode=0o700, exist_ok=True)
    current = updates / 'update.json'
    if current.exists():
        before = json.loads(current.read_text())
        if metadata['version_code'] < before['version_code']:
            raise RuntimeError('Refuse downgrade')
        if metadata['version_code'] == before['version_code']:
            if metadata != before:
                raise RuntimeError('Same-version release must be immutable')
            sync_downloads(updates, root / 'download', metadata)
            print('Updates unchanged; manual download entrypoint synchronized')
            return
    apk = fetch_apk(metadata, git, cache, use_gh, os.environ)
    atomic(updates, updates / name, apk)
    atomic(updates, current, (json.dumps(metadata, ensure_ascii=False, indent=2) + '\n').encode())
    sync_downloads(updates, root / 'download', metadata)
    print('Published signed update version ' + metadata['version_name'] + ' to HTTPS gateway; client verifies APK signing identity')


if __name__ == '__main__':
    main()
