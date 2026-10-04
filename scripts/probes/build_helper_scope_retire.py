#!/usr/bin/env python3
"""Explicit private arm64 helper-scope finalizer build; default inert.

No device I/O, installation, remote deletion, reservation or PM operation.
The host-only fixture macro is never passed to the Android compiler.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'experiments/moonlight-v2/source-snapshot/helper_scope_retire.c'
REVISION = '29.0.14206865'


def build(output, ndk):
    output, ndk = Path(output).resolve(), Path(ndk).resolve()
    if output == ROOT or ROOT in output.parents:
        raise ValueError('helper_scope_binary_outside_source_required')
    tag = {'Darwin': 'darwin-x86_64', 'Linux': 'linux-x86_64'}.get(platform.system())
    if tag is None:
        raise ValueError('helper_scope_build_host_rejected')
    properties = ndk / 'source.properties'
    compiler = ndk / 'toolchains/llvm/prebuilt' / tag / 'bin/aarch64-linux-android30-clang'
    if (not SOURCE.is_file() or not properties.is_file() or not compiler.is_file()
            or re.findall(r'^Pkg.Revision\s*=\s*(\S+)\s*$', properties.read_text(), re.M) != [REVISION]):
        raise ValueError('helper_scope_existing_locked_NDK_required')
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    binary = output / 'helper-scope-retire'
    command = [str(compiler), '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
               '-fPIE', '-pie', '-Wl,-z,relro,-z,now', str(SOURCE), '-o', str(binary)]
    result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError('helper_scope_compile_failed')
    binary.chmod(0o700)
    data = binary.read_bytes()
    # Independently check ELF64/little-endian/AArch64 rather than a compiler arg.
    if data[:6] != b'\x7fELF\x02\x01' or int.from_bytes(data[18:20], 'little') != 183:
        raise RuntimeError('helper_scope_ELF_architecture_rejected')
    if b'HG_HELPER_FIXTURE_HOOK' in data or b'file_growth_after_seal' in data:
        raise RuntimeError('helper_scope_host_fixture_in_Android_binary')
    def pin(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    receipt = {
        'schema': 'helper-scope-retirement-build-v1',
        'source_sha256': pin(SOURCE), 'compiler_sha256': pin(compiler),
        'binary_sha256': hashlib.sha256(data).hexdigest(), 'binary_bytes': len(data),
        'NDK_revision': REVISION, 'API': 30, 'ABI': 'arm64-v8a',
        'ELF_AArch64_verified': True, 'fixture_macro_enabled': False,
        'required_base': '/data/local',
        'old_shell_writable_tmp_scope_compatible': False,
        'remote_DAC_or_PM_verified': False, 'device_operations': 0,
        'remote_deletions': 0, 'App_helper_JNI_artifacts_changed': False,
        'reservation_release_authorized': False,
    }
    path = output / 'build.json'
    path.write_text(json.dumps(receipt, indent=2) + '\n')
    path.chmod(0o600)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--ndk', type=Path, default=Path.home() / 'Library/Android/sdk/ndk' / REVISION)
    args = parser.parse_args(argv)
    if not args.build:
        print(json.dumps({'phase': 'prepared_not_built', 'device_operations': 0,
                          'reservation_release_authorized': False}))
        return 0
    if args.output is None:
        parser.error('--output required for explicit build')
    print(json.dumps(build(args.output, args.ndk), sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
