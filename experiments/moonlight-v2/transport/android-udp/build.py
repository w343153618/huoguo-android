#!/usr/bin/env python3
"""Build pinned native candidates; downloads require explicit fresh bootstrap.

Existing caches and frozen host binaries are never rewritten automatically.
The tracked public dependency lock is independent of historical upstream notes.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
LOCK = ROOT/'native-dependencies.lock.json'
PUBLIC_REPOSITORIES = {
    'moonlight-common-c': ('https://github.com/moonlight-stream/moonlight-common-c.git', '.'),
    'nanors': ('https://github.com/sleepybishop/nanors.git', 'nanors'),
}


class BuildBoundaryError(Exception):
    """A fixed build boundary failure, without printing dependency file data."""


def load_lock(path=LOCK):
    try:
        pins = json.loads(Path(path).read_text())
        dependencies = pins['dependencies']
        if (set(pins) != {'schema', 'scope', 'dependencies', 'android'}
                or type(pins['schema']) is not int or pins['schema'] != 1
                or pins['scope'] != 'public_native_candidate_build_dependencies_only'
                or not isinstance(dependencies, list)
                or len(dependencies) != len(PUBLIC_REPOSITORIES)):
            raise BuildBoundaryError('dependency_lock_schema_invalid')
        names = set()
        for dependency in dependencies:
            if set(dependency) != {'name', 'path', 'repository', 'commit'}:
                raise BuildBoundaryError('dependency_lock_schema_invalid')
            name = dependency['name']
            if name in names or name not in PUBLIC_REPOSITORIES:
                raise BuildBoundaryError('dependency_lock_identity_invalid')
            names.add(name)
            expected_repository, expected_path = PUBLIC_REPOSITORIES[name]
            if (dependency['repository'] != expected_repository
                    or dependency['path'] != expected_path
                    or not re.fullmatch(r'[0-9a-f]{40}', dependency['commit'])):
                raise BuildBoundaryError('dependency_lock_public_pin_invalid')
        if [d['name'] for d in dependencies] != ['moonlight-common-c', 'nanors']:
            raise BuildBoundaryError('dependency_lock_bootstrap_order_invalid')
        android = pins['android']
        if (set(android) != {'ndk_version', 'abi', 'platform', 'stl'}
                or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', android['ndk_version'])
                or android['abi'] != 'arm64-v8a'
                or android['platform'] != 'android-23'
                or android['stl'] != 'c++_static'):
            raise BuildBoundaryError('dependency_lock_android_contract_invalid')
        return pins
    except (OSError, ValueError, KeyError, TypeError):
        raise BuildBoundaryError('dependency_lock_unavailable_or_invalid') from None


def capture(*args):
    return subprocess.run([str(x) for x in args], check=True, capture_output=True,
                          text=True).stdout.strip()


def run(*args):
    return subprocess.run([str(x) for x in args], check=True)


def verify_dependencies(source, pins, git=capture):
    source = Path(source).resolve()
    for dependency in pins['dependencies']:
        checkout = (source/dependency['path']).resolve()
        if checkout != source and source not in checkout.parents:
            raise BuildBoundaryError('dependency_checkout_escaped_source')
        try:
            head = git('git', '-C', checkout, 'rev-parse', 'HEAD')
            dirty = git('git', '-C', checkout, 'status', '--porcelain',
                        '--untracked-files=all', '--ignore-submodules=none')
        except (OSError, subprocess.SubprocessError):
            raise BuildBoundaryError('dependency_checkout_unavailable') from None
        if head != dependency['commit'] or dirty:
            raise BuildBoundaryError('dependency_pin_or_clean_check_failed_' + dependency['name'])
    nanors = next(d for d in pins['dependencies'] if d['name'] == 'nanors')
    try:
        link = git('git', '-C', source, 'ls-tree', 'HEAD', 'nanors')
    except (OSError, subprocess.SubprocessError):
        raise BuildBoundaryError('dependency_gitlink_unavailable') from None
    if link != '160000 commit ' + nanors['commit'] + '\tnanors':
        raise BuildBoundaryError('dependency_nanors_gitlink_mismatch')


def bootstrap_dependencies(source, pins, command=run, verifier=verify_dependencies):
    """Explicitly create a new checkout; never reset or delete an existing path.

    A failed download leaves the newly created directory for inspection. This
    command deliberately refuses even an existing empty directory on retry.
    """
    source = Path(source).absolute()
    if source.exists() or source.is_symlink():
        raise BuildBoundaryError('bootstrap_requires_new_source_directory')
    source.parent.mkdir(parents=True, exist_ok=True)
    try:
        source.mkdir()  # Exclusive reservation; never overwrite a racing path.
    except FileExistsError:
        raise BuildBoundaryError('bootstrap_requires_new_source_directory') from None
    for dependency in pins['dependencies']:
        checkout = source/dependency['path']
        command('git', 'init', checkout)
        command('git', '-C', checkout, 'remote', 'add', 'origin', dependency['repository'])
        command('git', '-C', checkout, '-c', 'core.hooksPath=/dev/null',
                'fetch', '--no-tags', '--no-recurse-submodules', '--depth', '1',
                'origin', dependency['commit'])
        command('git', '-C', checkout, '-c', 'core.hooksPath=/dev/null',
                'checkout', '--detach', dependency['commit'])
    verifier(source, pins)
    return source


def default_source(environ=None):
    environ = os.environ if environ is None else environ
    return Path(environ.get('HUOGUO_UDP_SOURCE') or
                Path.home()/'.cache/huoguo-v2-sources/moonlight-common-c')


def resolve_ndk(explicit, android, environ=None, home=None, platform=None):
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else Path(home)
    platform = sys.platform if platform is None else platform
    sdk = environ.get('ANDROID_HOME') or environ.get('ANDROID_SDK_ROOT')
    if explicit is not None:
        ndk = Path(explicit)
    elif environ.get('ANDROID_NDK_ROOT') or environ.get('ANDROID_NDK_HOME'):
        ndk = Path(environ.get('ANDROID_NDK_ROOT') or environ['ANDROID_NDK_HOME'])
    elif sdk:
        ndk = Path(sdk)/'ndk'/android['ndk_version']
    else:
        sdk = home/'Library/Android/sdk' if platform == 'darwin' else home/'Android/Sdk'
        ndk = sdk/'ndk'/android['ndk_version']
    try:
        properties = (ndk/'source.properties').read_text()
    except OSError:
        raise BuildBoundaryError('locked_Android_NDK_not_installed') from None
    revision = re.search(r'^Pkg\.Revision\s*=\s*(\S+)\s*$', properties, re.MULTILINE)
    if (revision is None or revision.group(1) != android['ndk_version']
            or not (ndk/'build/cmake/android.toolchain.cmake').is_file()):
        raise BuildBoundaryError('Android_NDK_revision_or_toolchain_mismatch')
    return ndk.resolve()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=default_source())
    parser.add_argument('--build', type=Path, default=Path('/private/tmp/huoguo-android-udp-build')
                        if sys.platform == 'darwin' else Path(tempfile.gettempdir())/'huoguo-android-udp-build')
    parser.add_argument('--ndk', type=Path)
    parser.add_argument('--android', action='store_true')
    parser.add_argument('--bootstrap-dependencies', action='store_true',
                        help='Download locked public dependencies into a new --source directory only; no build')
    parser.add_argument('--check-only', action='store_true', help='Verify dependencies/toolchain; no configure or build')
    args = parser.parse_args(argv)
    try:
        pins = load_lock()
        if args.bootstrap_dependencies:
            if args.android or args.check_only:
                raise BuildBoundaryError('bootstrap_is_a_separate_download_only_step')
            bootstrap_dependencies(args.source, pins)
            print(json.dumps({'scope': 'explicit_new_dependency_checkout_only',
                              'source': str(args.source),
                              'dependency_lock_sha256': hashlib.sha256(LOCK.read_bytes()).hexdigest()}))
            return
        verify_dependencies(args.source, pins)
        ndk = resolve_ndk(args.ndk, pins['android']) if args.android else None
        if args.check_only:
            print(json.dumps({'scope': 'dependency_checks_only_no_build_or_device',
                              'dependency_lock_sha256': hashlib.sha256(LOCK.read_bytes()).hexdigest()}))
            return
        cmake = shutil.which('cmake')
        if cmake is None:
            fallback = Path.home()/'.cache/huoguo-v2-tools/bin/cmake'
            if not fallback.is_file():
                raise BuildBoundaryError('existing_CMake_required_no_automatic_install')
            cmake = str(fallback)
        build = args.build/('android-arm64' if args.android else 'host')
        configure = [cmake, '-S', ROOT, '-B', build, '-DCMAKE_BUILD_TYPE=Release',
                     '-DMOONLIGHT_SOURCE_DIR='+str(args.source.resolve())]
        if args.android:
            configure += ['-DCMAKE_TOOLCHAIN_FILE='+str(ndk/'build/cmake/android.toolchain.cmake'),
                          '-DANDROID_ABI='+pins['android']['abi'],
                          '-DANDROID_PLATFORM='+pins['android']['platform'],
                          '-DANDROID_STL='+pins['android']['stl']]
        run(*configure)
        run(cmake, '--build', build, '--parallel', '4')
        if not args.android:
            run(Path(cmake).with_name('ctest'), '--test-dir', build, '--output-on-failure')
        output = build/('libhuoguo_udp_fec.so' if args.android else 'h264_udp_packetizer')
        manifest = {'scope': 'build_only_no_device', 'output': str(output),
                    'dependency_lock_sha256': hashlib.sha256(LOCK.read_bytes()).hexdigest(),
                    'dependencies': pins['dependencies'],
                    'android': pins['android'] if args.android else None,
                    'binary_sha256': hashlib.sha256(output.read_bytes()).hexdigest()}
        (build/'native-build-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
        print(json.dumps(manifest))
    except BuildBoundaryError as error:
        parser.exit(2, str(error)+'\n')


if __name__ == '__main__':
    main()
