#!/usr/bin/env python3
"""Explicit private arm64 partial helper-owner library build; no device I/O.

Default prepared only. Android CLI has no mutation/PM activation; this build
cannot establish remote ownership or complete helper/supervisor acceptance.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'experiments/moonlight-v2/source-snapshot/helper_scope_owner.c'
HEADER = SOURCE.with_name('helper_scope_crypto.h')
REVISION = '29.0.14206865'


def build(output, ndk):
    output, ndk = Path(output).resolve(), Path(ndk).resolve()
    if output == ROOT or ROOT in output.parents:
        raise ValueError('helper_owner_build_outside_source_required')
    tag = {'Darwin': 'darwin-x86_64', 'Linux': 'linux-x86_64'}.get(platform.system())
    if tag is None:
        raise ValueError('helper_owner_build_host_rejected')
    compiler = ndk / 'toolchains/llvm/prebuilt' / tag / 'bin/aarch64-linux-android30-clang'
    props = ndk / 'source.properties'
    if (not SOURCE.is_file() or not HEADER.is_file() or not compiler.is_file() or not props.is_file()
            or re.findall(r'^Pkg.Revision\s*=\s*(\S+)\s*$', props.read_text(), re.M) != [REVISION]):
        raise ValueError('helper_owner_existing_locked_NDK_required')
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    binary = output / 'helper-scope-owner'
    r = subprocess.run([str(compiler), '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                        '-fPIE', '-pie', '-fvisibility=hidden', '-Wl,-z,relro,-z,now',
                        str(SOURCE), '-o', str(binary)], stdin=subprocess.DEVNULL,
                       capture_output=True, timeout=30)
    if r.returncode:
        raise RuntimeError('helper_owner_compile_failed')
    binary.chmod(0o700)
    data = binary.read_bytes()
    if data[:6] != b'\x7fELF\x02\x01' or int.from_bytes(data[18:20], 'little') != 183:
        raise RuntimeError('helper_owner_ELF_architecture_rejected')
    if b'--fixture' in data or b'helper_owner_fixture_footer' in data:
        raise RuntimeError('helper_owner_host_macro_in_Android_binary')
    def pin(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    value = {
        'schema': 'partial-helper-owner-build-v1', 'NDK_revision': REVISION,
        'API': 30, 'ABI': 'arm64-v8a', 'ELF_AArch64_verified': True,
        'source_sha256': pin(SOURCE), 'header_sha256': pin(HEADER), 'compiler_sha256': pin(compiler),
        'binary_sha256': hashlib.sha256(data).hexdigest(), 'binary_bytes': len(data),
        'fixture_macro_enabled': False, 'production_CLI_activation_available': False,
        'NEW_scope_layout': 'root-only parent/stage0700 and APK0600',
        'prior_shell_tmp_or_root_shell_finalizer_compatible': False,
        'default_PM_operations': 0, 'device_operations': 0,
        'remote_writer_PM_DAC_or_ART_verified': False, 'complete_live_helper_supervisor': False,
        'App_helper_JNI_or_release_changed': False, 'reservation_release_authorized': False,
    }
    receipt = output / 'build.json'
    receipt.write_text(json.dumps(value, indent=2) + '\n')
    receipt.chmod(0o600)
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--ndk', type=Path, default=Path.home()/'Library/Android/sdk/ndk'/REVISION)
    args = parser.parse_args(argv)
    if not args.build:
        print(json.dumps({'phase': 'prepared_not_built', 'device_operations': 0,
                          'production_activation_available': False}))
        return 0
    if args.output is None:
        parser.error('--output required for explicit build')
    print(json.dumps(build(args.output, args.ndk), sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
