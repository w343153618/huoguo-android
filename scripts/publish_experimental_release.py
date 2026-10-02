"""Prepare or publish an isolated signed experimental APK, never formal updates.

Preparation is the default. ``--publish`` additionally pushes the current reviewed
experimental branch and an immutable tag, then creates a GitHub prerelease. APKs
and signing material never enter a Git tree; the formal ``updates`` branch is not
read or modified. First installation is standalone; the candidate uses its own
manifest and same-package updater thereafter.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
APPLICATION_ID = 'local.remoteandroid.direct.experiment'
EXPECTED_SIGNER = '0d54d7cedd794e5beb96a27a69fbc57ae6453013a240dd3c5c7804baeff682da'
APK_ASSET = 'HuoguoAndroidExperimental.apk'
MANIFEST_ASSET = 'experiment.json'
MAX_APK_SIZE = 64 * 1024 * 1024
UDP_CLASS = b'Llocal/remoteandroid/direct/AuthenticatedLanUdpUi;'
UDP_NATIVE = 'lib/arm64-v8a/libhuoguo_udp_fec.so'


def run(argv, **kwargs):
    return subprocess.run(argv, cwd=ROOT, check=True, **kwargs)


def checked_version(name, code):
    if not re.fullmatch(r'\d+\.\d+(?:\.\d+)?-(?:alpha|beta|rc)\.[1-9]\d*', name):
        raise ValueError('Experimental version must include alpha.N, beta.N or rc.N')
    if isinstance(code, bool) or not isinstance(code, int) or not 1 <= code <= 2100000000:
        raise ValueError('Invalid experimental version code')
    return 'experimental-v' + name


def checked_repository(repository):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Invalid GitHub repository')
    return repository


def source_identity(repository):
    """Require committed source, allowing unrelated untracked historical evidence."""
    branch = run(['git', 'branch', '--show-current'], capture_output=True, text=True).stdout.strip()
    if not re.fullmatch(r'codex/experimental-[A-Za-z0-9._/-]+', branch) or '..' in branch:
        raise ValueError('Publish experimental builds from a codex/experimental-* branch')
    dirty = run(['git', 'status', '--porcelain', '--untracked-files=no'],
                capture_output=True, text=True).stdout.strip()
    if dirty:
        raise ValueError('Commit tracked source changes before preparing an experimental release')
    sha = run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
    if not re.fullmatch(r'[0-9a-f]{40}', sha):
        raise ValueError('Invalid source commit')
    origin = run(['git', 'remote', 'get-url', 'origin'], capture_output=True, text=True).stdout.strip()
    allowed = {'https://github.com/' + repository, 'https://github.com/' + repository + '.git',
               'git@github.com:' + repository + '.git', 'ssh://git@github.com/' + repository + '.git'}
    if origin not in allowed:
        raise ValueError('origin does not match the requested GitHub repository')
    for file in ('scripts/publish_experimental_release.py', 'app/build.gradle',
                 'app/src/udp/java/local/remoteandroid/direct/AuthenticatedLanUdpUi.java'):
        run(['git', 'cat-file', '-e', sha + ':' + file], capture_output=True)
    return branch, sha


def apk_identity(badging, certificates, version_name, version_code):
    match = re.search(r"^package: name='([^']+)' versionCode='(\d+)' versionName='([^']+)'", badging, re.M)
    if not match or match.groups() != (APPLICATION_ID, str(version_code), version_name):
        raise ValueError('APK package/version does not match the isolated experimental channel')
    signers = re.findall(r'Signer #\d+ certificate SHA-256 digest: ([0-9a-f]+)', certificates)
    if signers != [EXPECTED_SIGNER]:
        raise ValueError('APK must have the original single upgrade signing identity')
    label = re.search(r"^application-label:'([^']+)'", badging, re.M)
    if not label or '实验' not in label.group(1):
        raise ValueError('Experimental launcher label must clearly include 实验')
    return label.group(1)


def validate_apk(apk, sdk, version_name, version_code):
    apk = Path(apk)
    size = apk.stat().st_size
    if not apk.is_file() or not 0 < size <= MAX_APK_SIZE:
        raise ValueError('Invalid experimental APK size')
    tools = Path(sdk) / 'build-tools/36.0.0'
    badging = run([str(tools / 'aapt2'), 'dump', 'badging', str(apk)],
                  capture_output=True, text=True).stdout
    certificates = run([str(tools / 'apksigner'), 'verify', '--print-certs', str(apk)],
                       capture_output=True, text=True).stdout
    label = apk_identity(badging, certificates, version_name, version_code)
    with zipfile.ZipFile(apk) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError('APK contains duplicate entries')
        native = [name for name in names if name.endswith('/libhuoguo_udp_fec.so')]
        if native != [UDP_NATIVE]:
            raise ValueError('Experimental APK must contain only the arm64 authenticated UDP native library')
        dex = [info for info in archive.infolist() if re.fullmatch(r'classes(?:[2-9]|[1-9]\d+)?\.dex', info.filename)]
        if not dex or sum(info.file_size for info in dex) > MAX_APK_SIZE:
            raise ValueError('Missing or oversized executable code')
        if not any(UDP_CLASS in archive.read(info) for info in dex):
            raise ValueError('Authenticated UDP UI is missing from experimental APK')
    return dict(application_id=APPLICATION_ID, application_label=label,
                apk_size=size, sha256=hashlib.sha256(apk.read_bytes()).hexdigest(),
                signing_certificate_sha256=EXPECTED_SIGNER)


def manifest(identity, repository, branch, sha, version_name, version_code, notes):
    tag = checked_version(version_name, version_code)
    return dict(schema=1, channel='experimental', prerelease=True,
                version_name=version_name, version_code=version_code, release_tag=tag,
                source_commit=sha, source_branch=branch,
                apk_url='https://github.com/' + repository + '/releases/download/' + tag + '/' + APK_ASSET,
                installation='standalone_signed_apk', automatic_formal_update=False,
                media_transport='authenticated_udp_lan_and_registered_tailnet_experiment',
                public_udp_acceptance=False, changelog=notes[:4000], **identity)


def prepare(folder, source_apk, sdk, repository, branch, sha, version_name, version_code, notes):
    """Freeze and validate the copied artifact before writing its isolated manifest."""
    folder = Path(folder).resolve()
    if folder == ROOT or ROOT in folder.parents:
        raise ValueError('Experimental APK output must be outside the source tree')
    folder.mkdir(mode=0o700, parents=False, exist_ok=False)
    try:
        apk = folder / APK_ASSET
        shutil.copyfile(source_apk, apk)
        apk.chmod(0o600)
        identity = validate_apk(apk, sdk, version_name, version_code)
        metadata = manifest(identity, repository, branch, sha, version_name, version_code, notes)
        (folder / MANIFEST_ASSET).write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
        (folder / 'SHA256SUMS.txt').write_text(identity['sha256'] + '  ' + APK_ASSET + '\n')
        (folder / 'release-notes.md').write_text(notes)
        for asset in (MANIFEST_ASSET, 'SHA256SUMS.txt', 'release-notes.md'):
            (folder / asset).chmod(0o600)
        return metadata
    except BaseException:
        shutil.rmtree(folder)
        raise


def publish_commands(folder, metadata, repository):
    """No force push, no formal tag and no formal update manifest mutation."""
    tag, branch, sha = (metadata[field] for field in ('release_tag', 'source_branch', 'source_commit'))
    return [
        ['git', '-c', 'credential.helper=!gh auth git-credential', 'push', 'origin',
         sha + ':refs/heads/' + branch],
        ['git', '-c', 'credential.helper=!gh auth git-credential', 'push', 'origin',
         sha + ':refs/tags/' + tag],
        ['gh', 'release', 'create', tag, '--repo', repository, '--verify-tag',
         '--prerelease', '--latest=false', '--title', '给火锅的安卓 · 实验版 ' + metadata['version_name'],
         '--notes-file', str(Path(folder) / 'release-notes.md'), *[str(Path(folder) / asset)
         for asset in (APK_ASSET, MANIFEST_ASSET, 'SHA256SUMS.txt')]],
    ]


def assert_not_published(repository, tag):
    release = subprocess.run(['gh', 'release', 'view', tag, '--repo', repository],
                             cwd=ROOT, capture_output=True, text=True)
    if release.returncode == 0:
        raise ValueError('Experimental release already exists; increase its version instead of overwriting')
    if 'release not found' not in release.stderr.lower():
        raise ValueError('Cannot verify absence of the experimental release')
    ref = subprocess.run(['git', '-c', 'credential.helper=!gh auth git-credential',
                          'ls-remote', '--exit-code', '--tags', 'origin', 'refs/tags/' + tag],
                         cwd=ROOT, capture_output=True, text=True)
    if ref.returncode != 2 or ref.stdout.strip():
        raise ValueError('Experimental tag exists or cannot be checked; inspect partial publication without overwriting')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', default='w343153618/huoguo-android')
    parser.add_argument('--version-name', required=True)
    parser.add_argument('--version-code', required=True, type=int)
    parser.add_argument('--notes-file', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--apk', type=Path, help='Validate an already built isolated signed APK; otherwise build it')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--publish', action='store_true', help='Push the reviewed experimental branch and create a prerelease')
    mode.add_argument('--dry-run', action='store_true', help='Default: build/validate/freeze without publishing')
    args = parser.parse_args(argv)
    repository = checked_repository(args.repository)
    tag = checked_version(args.version_name, args.version_code)
    branch, sha = source_identity(repository)
    if args.notes_file.stat().st_size > 65536:
        raise ValueError('Release notes exceed the bounded limit')
    notes = args.notes_file.read_text()
    if not notes.strip():
        raise ValueError('Provide meaningful experimental release notes')
    sdk = Path(os.environ.get('ANDROID_HOME', str(Path.home() / 'Library/Android/sdk')))
    if args.publish:
        assert_not_published(repository, tag)
    if args.apk is None:
        run(['./gradlew', ':app:assembleRelease',
             '-PprobeApplicationId=' + APPLICATION_ID, '-PauthenticatedLanUdp=true',
             '-PexperimentalVersionName=' + args.version_name,
             '-PexperimentalVersionCode=' + str(args.version_code), '--console=plain'])
        source_apk = ROOT / 'app/build/outputs/apk/release/app-release.apk'
    else:
        source_apk = args.apk
    metadata = prepare(args.output_dir, source_apk, sdk, repository, branch, sha,
                       args.version_name, args.version_code, notes)
    # Building must not make tracked source dirty or change the requested source revision.
    if source_identity(repository) != (branch, sha):
        raise ValueError('Source identity changed while preparing the release')
    if args.publish:
        # Validate the frozen file again immediately before any publication mutation.
        identity = validate_apk(args.output_dir / APK_ASSET, sdk, args.version_name, args.version_code)
        if any(metadata[key] != identity[key] for key in identity):
            raise ValueError('Frozen experimental artifact changed before publication')
        for command in publish_commands(args.output_dir, metadata, repository):
            run(command)
        print('Published ' + tag + '; independent experimental package; formal updates untouched')
    else:
        print('Prepared ' + tag + ' in ' + str(args.output_dir.resolve())
              + '; no branch, tag, release or formal update mutation')
    return metadata


if __name__ == '__main__':
    main()
