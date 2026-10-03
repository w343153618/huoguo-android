"""Deliver one immutable experimental release using existing gh authorization.

The gateway exposes only the verified signed APK and rewritten public manifest.
No GitHub token is placed in the App, manifest or served directory. The formal
updates directory/channel is neither read nor modified.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

from publish_experimental_release import (
    APPLICATION_ID, EXPECTED_SIGNER, APK_ASSET, MANIFEST_ASSET,
    MAX_APK_SIZE, PUBLIC_OWNER_RELEASES, is_public_owner_release,
    validate_apk as inspect_apk,
)

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'w343153618/huoguo-android'
PUBLIC_BASE = 'https://146.56.249.175:15556/experimental'
MAX_MANIFEST_SIZE = 65536
VERSION = r'[0-9]+(?:\.[0-9]+){1,2}-alpha\.[1-9][0-9]*'
FIELDS = frozenset((
    'schema', 'channel', 'prerelease', 'version_name', 'version_code', 'release_tag',
    'source_commit', 'source_branch', 'apk_url', 'installation',
    'automatic_formal_update', 'media_transport', 'public_udp_acceptance', 'changelog',
    'application_id', 'application_label', 'apk_size', 'sha256',
    'signing_certificate_sha256',
))
CAPABILITY_FIELDS = FIELDS | frozenset((
    'public_owner_profiles', 'public_cellular_acceptance', 'friend_isolation_acceptance',
))
MIRROR_FIELDS = CAPABILITY_FIELDS | frozenset(('github_asset_url',))
LAN_TRANSPORT = 'authenticated_udp_lan_and_registered_tailnet_experiment'
PUBLIC_OWNER_TRANSPORT = 'authenticated_udp_public_NPS_owner_and_LAN_tailnet_experiment'


def checked_repository(repository):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*', repository):
        raise RuntimeError('Invalid release repository')
    return repository


def checked_tag(tag):
    if not isinstance(tag, str) or not re.fullmatch('experimental-v' + VERSION, tag):
        raise RuntimeError('Only immutable experimental alpha tags are allowed')
    return tag


def public_name(version):
    return 'HuoguoAndroidExperiment-v' + version + '.apk'


def validate_metadata(metadata, repository, tag, *, published=False):
    checked_repository(repository)
    checked_tag(tag)
    if not isinstance(metadata, dict) or set(metadata) not in (FIELDS, CAPABILITY_FIELDS, MIRROR_FIELDS):
        raise RuntimeError('Unexpected experimental manifest schema')
    if type(metadata['schema']) is not int or metadata['schema'] != 1:
        raise RuntimeError('Unsupported experimental manifest schema')
    if (metadata['channel'] != 'experimental' or metadata['prerelease'] is not True
            or metadata['application_id'] != APPLICATION_ID
            or metadata['signing_certificate_sha256'] != EXPECTED_SIGNER
            or metadata['automatic_formal_update'] is not False
            or metadata['public_udp_acceptance'] is not False):
        raise RuntimeError('Experimental channel/package/signature/transport boundary mismatch')
    version, code = metadata['version_name'], metadata['version_code']
    if not isinstance(version, str) or not re.fullmatch(VERSION, version) or tag != 'experimental-v' + version:
        raise RuntimeError('Experimental release version/tag mismatch')
    if metadata['release_tag'] != tag or type(code) is not int or not 1 <= code <= 2100000000:
        raise RuntimeError('Invalid experimental release version code')
    try:
        public_owner = is_public_owner_release(version, code)
    except ValueError as error:
        raise RuntimeError('Public owner version/code contract mismatch') from error
    capabilities = set(metadata) != FIELDS
    mirror = set(metadata) == MIRROR_FIELDS
    if metadata['media_transport'] != (PUBLIC_OWNER_TRANSPORT if public_owner else LAN_TRANSPORT):
        raise RuntimeError('Experimental transport does not match its reviewed version')
    if public_owner and not capabilities:
        raise RuntimeError('Public owner release requires explicit capability boundaries')
    if capabilities and (type(metadata['public_owner_profiles']) is not list
            or metadata['public_owner_profiles'] != (['m1', 'm5'] if public_owner else [])
            or metadata['public_cellular_acceptance'] is not False
            or metadata['friend_isolation_acceptance'] is not False):
        raise RuntimeError('Experimental owner profiles or acceptance boundary mismatch')
    if mirror and not public_owner:
        raise RuntimeError('Only reviewed public owner releases allow a mirrored asset manifest')
    installation = ('standalone_signed_apk_or_manual_channel_picker' if mirror else 'standalone_signed_apk')
    if metadata['installation'] != installation:
        raise RuntimeError('Unexpected experimental installation boundary')
    if (not isinstance(metadata['application_label'], str) or '实验' not in metadata['application_label']
            or len(metadata['application_label']) > 128):
        raise RuntimeError('Invalid experimental launcher label')
    if (not isinstance(metadata['source_commit'], str) or not re.fullmatch(r'[0-9a-f]{40}', metadata['source_commit'])
            or not isinstance(metadata['source_branch'], str)
            or not re.fullmatch(r'codex/experimental-[A-Za-z0-9._/-]+', metadata['source_branch'])
            or '..' in metadata['source_branch']):
        raise RuntimeError('Invalid experimental source identity')
    if not isinstance(metadata['changelog'], str) or len(metadata['changelog']) > 4000:
        raise RuntimeError('Invalid bounded experimental changelog')
    if type(metadata['apk_size']) is not int or not 0 < metadata['apk_size'] <= MAX_APK_SIZE:
        raise RuntimeError('Invalid experimental APK size')
    if not isinstance(metadata['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', metadata['sha256']):
        raise RuntimeError('Invalid experimental APK digest')
    name = public_name(version)
    github_asset = 'https://github.com/' + repository + '/releases/download/' + tag + '/' + APK_ASSET
    if mirror and metadata['github_asset_url'] != github_asset:
        raise RuntimeError('Unexpected immutable experimental GitHub asset URL')
    expected = PUBLIC_BASE + '/' + name if published or mirror else github_asset
    if metadata['apk_url'] != expected:
        raise RuntimeError('Unexpected experimental download URL')
    return name


def read_file(path, limit):
    """Read bounded regular files, rejecting symlinks/devices/FIFOs."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
            raise RuntimeError('Invalid release file size/type')
        data = source.read(limit + 1)
        if len(data) != info.st_size or len(data) > limit:
            raise RuntimeError('Release file changed or exceeds the bounded limit')
        return data


def gh(argv, *, timeout):
    result = subprocess.run(['gh', *argv], check=True, capture_output=True, timeout=timeout)
    return result.stdout


def download_release(tag, repository, temporary):
    """Only exact immutable assets, in a private temporary staging directory."""
    raw = gh(['release', 'view', tag, '--repo', repository, '--json', 'tagName,isPrerelease,assets'], timeout=60)
    if len(raw) > MAX_MANIFEST_SIZE:
        raise RuntimeError('Unexpected GitHub release response size')
    info = json.loads(raw)
    if not isinstance(info, dict) or info.get('tagName') != tag or info.get('isPrerelease') is not True:
        raise RuntimeError('GitHub release is not the requested prerelease')
    assets = info.get('assets')
    if not isinstance(assets, list):
        raise RuntimeError('Missing GitHub experimental release assets')
    expected_sizes = {}
    for name, limit in ((MANIFEST_ASSET, MAX_MANIFEST_SIZE), (APK_ASSET, MAX_APK_SIZE)):
        matches = [asset for asset in assets if isinstance(asset, dict) and asset.get('name') == name]
        if len(matches) != 1 or type(matches[0].get('size')) is not int or not 0 < matches[0]['size'] <= limit:
            raise RuntimeError('Missing, duplicate or oversized experimental release asset')
        expected_sizes[name] = matches[0]['size']
    gh(['release', 'download', tag, '--repo', repository,
        '--pattern', MANIFEST_ASSET, '--pattern', APK_ASSET, '--dir', str(temporary)], timeout=180)
    metadata = json.loads(read_file(Path(temporary) / MANIFEST_ASSET, MAX_MANIFEST_SIZE))
    validate_metadata(metadata, repository, tag)
    if metadata['apk_size'] != expected_sizes[APK_ASSET]:
        raise RuntimeError('GitHub experimental asset size differs from its manifest')
    apk = read_file(Path(temporary) / APK_ASSET, MAX_APK_SIZE)
    if len(apk) != metadata['apk_size'] or hashlib.sha256(apk).hexdigest() != metadata['sha256']:
        raise RuntimeError('Experimental release APK digest/size mismatch')
    return metadata, apk


def atomic(directory, target, data):
    fd, temporary = tempfile.mkstemp(prefix='.verified-', dir=directory)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def install(directory, metadata, apk, repository):
    """APK first, manifest last; reject downgrade and any same-version mutation."""
    name = validate_metadata(metadata, repository, metadata['release_tag'])
    if len(apk) != metadata['apk_size'] or hashlib.sha256(apk).hexdigest() != metadata['sha256']:
        raise RuntimeError('Experimental release APK digest/size mismatch')
    exposed = dict(metadata, apk_url=PUBLIC_BASE + '/' + name)
    validate_metadata(exposed, repository, metadata['release_tag'], published=True)
    payload = (json.dumps(exposed, ensure_ascii=False, indent=2) + '\n').encode()
    if len(payload) > MAX_MANIFEST_SIZE:
        raise RuntimeError('Public experimental manifest exceeds the bounded limit')
    current = directory / MANIFEST_ASSET
    if current.exists() or current.is_symlink():
        before = json.loads(read_file(current, MAX_MANIFEST_SIZE))
        if not isinstance(before, dict):
            raise RuntimeError('Existing experimental manifest must be an object')
        validate_metadata(before, repository, before.get('release_tag'), published=True)
        if exposed['version_code'] < before['version_code']:
            raise RuntimeError('Refuse experimental downgrade')
        if exposed['version_code'] == before['version_code'] and exposed != before:
            raise RuntimeError('Same-version experimental release must be immutable')
    target = directory / name
    if target.exists() or target.is_symlink():
        if read_file(target, MAX_APK_SIZE) != apk:
            raise RuntimeError('Immutable experimental APK already exists with different content')
    else:
        atomic(directory, target, apk)
    # Existing readers see either the old complete manifest or the new complete one.
    atomic(directory, current, payload)
    return exposed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--repository', default=REPOSITORY)
    parser.add_argument('--directory', type=Path)
    args = parser.parse_args(argv)
    tag, repository = checked_tag(args.tag), checked_repository(args.repository)
    base = Path(os.environ.get('DIRECT_STATE_DIR', str(ROOT)))
    directory = args.directory or Path(os.environ.get('DIRECT_EXPERIMENTAL_DIR', str(base / 'experimental')))
    if directory.is_symlink():
        raise RuntimeError('Experimental delivery directory cannot be a symlink')
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_fd = os.open(directory / '.pull.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
            raise RuntimeError('Invalid experimental delivery lock')
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with tempfile.TemporaryDirectory(prefix='.release-', dir=directory) as temporary:
            metadata, apk = download_release(tag, repository, temporary)
            sdk = Path(os.environ.get('ANDROID_HOME', str(Path.home() / 'Library/Android/sdk')))
            actual = inspect_apk(Path(temporary) / APK_ASSET, sdk,
                                 metadata['version_name'], metadata['version_code'])
            if any(metadata.get(key) != value for key, value in actual.items()):
                raise RuntimeError('Actual experimental APK package/version/signature differs from its manifest')
            exposed = install(directory, metadata, apk, repository)
        print('Delivered experimental ' + exposed['version_name'] + '; formal updates untouched')
        return exposed
    finally:
        os.close(lock_fd)


if __name__ == '__main__':
    main()
