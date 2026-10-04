#!/usr/bin/env python3
"""Build an explicit standalone readonly UI runner JAR; default CLI is inert.

Uses existing SDK/JDK only, no download, signer, App package or device access.
Output must be a new directory outside the source tree. The JAR is not an APK.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SOURCES = tuple(ROOT / 'experiments/moonlight-v2/source-snapshot' / name
                for name in ('SnapshotPath.java', 'SourceSnapshot.java'))
RETIREMENT_SOURCES = tuple(ROOT / 'experiments/moonlight-v2/source-snapshot' / name
                          for name in ('RetirementRunner.java', 'RetirementReceipt.java'))
ABI_SHIM = ROOT / 'experiments/moonlight-v2/source-snapshot/abi/com/android/uiautomator/testrunner/UiAutomatorTestRunner.java'
INSTALLED_DEX_SHA256 = 'a7697e858f8a343f378b4b1578406d2932ec2636e6a89813081d26208fa85689'


def build(output, sdk, java_home, *, retirement_framework_dex=None):
    output = Path(output).resolve()
    if output == ROOT or ROOT in output.parents:
        raise ValueError('artifact_outside_source_required')
    android = sdk / 'platforms/android-37.0/android.jar'
    uia = sdk / 'platforms/android-37.0/uiautomator.jar'
    test_base = sdk / 'platforms/android-37.0/optional/android.test.base.jar'
    d8 = sdk / 'build-tools/36.0.0/d8'
    javac = java_home / 'bin/javac'
    dependencies = (android, uia, test_base, d8, javac)
    retirement = retirement_framework_dex is not None
    sources = SOURCES + (RETIREMENT_SOURCES if retirement else ())
    if retirement:
        framework = Path(retirement_framework_dex)
        if (not framework.is_file() or framework.stat().st_size > 1048576
                or hashlib.sha256(framework.read_bytes()).hexdigest() != INSTALLED_DEX_SHA256):
            raise ValueError('exact_installed_framework_dex_required')
    if not all(path.is_file() for path in (*dependencies, *sources, *([ABI_SHIM] if retirement else []))):
        raise ValueError('existing_sdk37_jdk_required')
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    classes, dex = output / 'classes', output / 'dex'
    classes.mkdir(mode=0o700); dex.mkdir(mode=0o700)
    env = dict(os.environ, JAVA_HOME=str(java_home))
    classpath = list((android, uia, test_base))
    if retirement:
        abi = output / 'compile-only-abi'; abi.mkdir(mode=0o700)
        result = subprocess.run([str(javac), '-source', '8', '-target', '8', '-cp',
            str(android), '-d', str(abi), str(ABI_SHIM)], env=env, capture_output=True, timeout=30)
        if result.returncode: raise RuntimeError('retirement_abi_compile_failed')
        classpath.append(abi)
    commands = (
        [str(javac), '-source', '8', '-target', '8', '-cp',
         os.pathsep.join(map(str, classpath)), '-d', str(classes),
         *map(str, sources)],
        [str(d8), '--min-api', '30', '--lib', str(android), '--classpath', str(uia),
         '--classpath', str(test_base), '--output', str(dex)],
    )
    for index, command in enumerate(commands):
        if index:
            if retirement:
                command.extend(['--classpath', str(abi)])
                if any(p.relative_to(classes).parts[0] != 'local' for p in classes.rglob('*.class')):
                    raise RuntimeError('compile_only_abi_packaging_rejected')
            command.extend(map(str, sorted(classes.rglob('*.class'))))
        result = subprocess.run(command, env=env, capture_output=True, timeout=30)
        if result.returncode:
            raise RuntimeError('snapshot_compile_failed' if not index else 'snapshot_dex_failed')
    with zipfile.ZipFile(output / 'snapshot.jar', 'x', compression=zipfile.ZIP_STORED) as jar:
        for path in sorted(dex.glob('*.dex')):
            entry = zipfile.ZipInfo(path.name, date_time=(2000, 1, 1, 0, 0, 0))
            jar.writestr(entry, path.read_bytes())
    (output / 'snapshot.jar').chmod(0o600)
    def pin(path): return hashlib.sha256(path.read_bytes()).hexdigest()
    record = {'schema': 'source-snapshot-build-v1',
              'source_sha256': {p.relative_to(ROOT).as_posix(): pin(p) for p in sources},
              'dependency_sha256': {p.name: pin(p) for p in dependencies},
              'jar_sha256': pin(output / 'snapshot.jar'),
              'jar_bytes': (output / 'snapshot.jar').stat().st_size,
              'device_operations': 0, 'App_artifact_changed': False}
    if retirement:
        record.update(schema='source-retirement-snapshot-build-v1',
                      installed_framework_dex_sha256=pin(framework),
                      compile_only_abi_source_sha256=pin(ABI_SHIM),
                      compile_only_abi_packaged=False,
                      required_runner='local.huoguo.sourceprobe.RetirementRunner',
                      actual_UI_execution_verified=False)
    receipt = output / 'build.json'
    receipt.write_text(json.dumps(record, indent=2) + '\n'); receipt.chmod(0o600)
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--sdk', type=Path, default=Path(os.environ.get(
        'ANDROID_HOME', Path.home() / 'Library/Android/sdk')))
    parser.add_argument('--java-home', type=Path, default=Path(os.environ.get(
        'JAVA_HOME', '/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home')))
    parser.add_argument('--retirement-framework-dex', type=Path,
                        help='Explicit exact installed DEX opt-in; no default reader changes')
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps({'phase': 'prepared_not_built', 'device_operations': 0}))
        return 0
    if args.output is None: parser.error('--output required for explicit build')
    print(json.dumps(build(args.output, args.sdk, args.java_home,
                           retirement_framework_dex=args.retirement_framework_dex), sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
