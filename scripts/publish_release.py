"""Build/sign on the owner's Mac, publish private APK assets and a metadata-only updates branch.

Signing material remains local. APKs never enter the source or updates Git trees.
"""
import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXPECTED_SIGNER = '0d54d7cedd794e5beb96a27a69fbc57ae6453013a240dd3c5c7804baeff682da'


def create_metadata_channel(folder, metadata, checksum, repository, tag):
    """Create a separate orphan Git tree containing only the update manifest and checksum."""
    folder = pathlib.Path(folder)
    folder.mkdir(mode=0o700)
    (folder / 'update.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
    (folder / 'SHA256SUMS.txt').write_text(checksum)
    commands = [
        ['init', '-b', 'updates'],
        ['config', 'user.name', 'Huoguo release'],
        ['config', 'user.email', 'release@users.noreply.github.com'],
        ['add', '--', 'update.json', 'SHA256SUMS.txt'],
        ['commit', '-m', 'Signed release ' + tag + ' metadata'],
        ['remote', 'add', 'origin', 'https://github.com/' + repository + '.git'],
    ]
    for arguments in commands:
        subprocess.run(['git', '-C', str(folder), *arguments], check=True, capture_output=True)
    return folder


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repository', default='w343153618/huoguo-android')
    parser.add_argument('--base-url', default='https://146.56.249.175:15556/updates')
    parser.add_argument('--resume', action='store_true', help='Resume metadata delivery of an already published immutable release')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', args.repository):
        raise SystemExit('Invalid repository')
    args.base_url = args.base_url.rstrip('/')

    def run(argv, **kwargs):
        return subprocess.run(argv, cwd=ROOT, check=True, **kwargs)

    def git(*arguments, **kwargs):
        return run(['git', '-c', 'credential.helper=!gh auth git-credential', *arguments], **kwargs)

    if run(['git', 'status', '--porcelain'], capture_output=True, text=True).stdout.strip():
        raise SystemExit('Commit source changes before publishing')
    if run(['git', 'branch', '--show-current'], capture_output=True, text=True).stdout.strip() != 'main':
        raise SystemExit('Publish from main')
    source = (ROOT / 'app/build.gradle').read_text()
    version = re.search(r"versionName '([^']+)'", source).group(1)
    code = int(re.search(r'versionCode (\d+)', source).group(1))
    tag = 'v' + version
    existing = subprocess.run(['gh', 'release', 'view', tag, '--repo', args.repository], capture_output=True)
    if existing.returncode == 0 and not args.resume:
        raise SystemExit('Version already released; increase versionCode/versionName, or use --resume for delivery only')
    if args.resume and existing.returncode != 0:
        raise SystemExit('No existing release to resume')
    sdk = pathlib.Path(os.environ.get('ANDROID_HOME', str(pathlib.Path.home() / 'Library/Android/sdk')))

    def validate(apk, metadata):
        check = run([str(sdk / 'build-tools/36.0.0/apksigner'), 'verify', '--print-certs', str(apk)], capture_output=True, text=True)
        signers = re.findall(r'Signer #\d+ certificate SHA-256 digest: ([0-9a-f]+)', check.stdout)
        if signers != [EXPECTED_SIGNER]:
            raise SystemExit('Wrong upgrade signing identity; do not publish')
        data = apk.read_bytes()
        if (metadata.get('release_tag') != tag or metadata['version_code'] != code or metadata['version_name'] != version
                or metadata['apk_url'] != args.base_url + '/HuoguoAndroid-v' + version + '.apk'
                or metadata['apk_size'] != len(data) or metadata['sha256'] != hashlib.sha256(data).hexdigest()):
            raise SystemExit('Release metadata/digest mismatch; legacy releases need a new version for metadata-only delivery')

    with tempfile.TemporaryDirectory(prefix='huoguo-release-') as directory:
        temporary = pathlib.Path(directory)
        if args.resume:
            run(['gh', 'release', 'download', tag, '--repo', args.repository, '--pattern', 'HuoguoAndroid.apk',
                 '--pattern', 'update.json', '--dir', directory])
        else:
            run(['./gradlew', ':app:assembleRelease', '-PupdateManifestUrl=' + args.base_url + '/update.json'])
            shutil.copy2(ROOT / 'app/build/outputs/apk/release/app-release.apk', temporary / 'HuoguoAndroid.apk')
            run(['python3', 'scripts/release_manifest.py', '--repository', args.repository, '--tag', tag,
                 '--base-url', args.base_url, '--apk', str(temporary / 'HuoguoAndroid.apk'), '--output', str(temporary / 'update.json')])
        metadata = json.loads((temporary / 'update.json').read_text())
        validate(temporary / 'HuoguoAndroid.apk', metadata)
        digest = hashlib.sha256((temporary / 'HuoguoAndroid.apk').read_bytes()).hexdigest()
        checksum = digest + '  HuoguoAndroid.apk\n'
        (temporary / 'SHA256SUMS.txt').write_text(checksum)
        if not args.resume:
            git('push', 'origin', 'main')
            sha = run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
            run(['gh', 'release', 'create', tag, '--repo', args.repository, '--target', sha,
                 '--title', '给火锅的安卓 ' + tag, '--notes-file', 'release-notes.md',
                 str(temporary / 'HuoguoAndroid.apk'), str(temporary / 'update.json'), str(temporary / 'SHA256SUMS.txt')])
        # The asset directory deliberately stays outside the metadata Git directory.
        channel = create_metadata_channel(temporary / 'channel', metadata, checksum, args.repository, tag)
        subprocess.run(['git', '-C', str(channel), '-c', 'credential.helper=!gh auth git-credential',
                        'push', 'origin', 'HEAD:updates', '--force'], check=True)
    print('Published ' + tag + '; APK is a private release asset, updates Git contains metadata only; signing key remained on the Mac')


if __name__ == '__main__':
    main()
