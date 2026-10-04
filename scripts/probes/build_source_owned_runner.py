#!/usr/bin/env python3
"""Explicit private Android arm64 launcher build; default inert, no device I/O.

Uses the existing locked NDK only. Host fixture macro is never included here.
Building is not deployment or approval to run a UiAutomation session.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'experiments/moonlight-v2/source-snapshot/owned_runner.c'
REVISION = '29.0.14206865'


def build(output, ndk):
    output, ndk = Path(output).resolve(), Path(ndk).resolve()
    if output == ROOT or ROOT in output.parents:
        raise ValueError('owned_runner_artifact_outside_source_required')
    system = platform.system().lower()
    tag = {'darwin': 'darwin-x86_64', 'linux': 'linux-x86_64'}.get(system)
    if tag is None:
        raise ValueError('owned_runner_toolchain_host_rejected')
    properties = ndk / 'source.properties'
    compiler = ndk / 'toolchains/llvm/prebuilt' / tag / 'bin/aarch64-linux-android30-clang'
    if (not SOURCE.is_file() or not properties.is_file() or not compiler.is_file()
            or re.findall(r'^Pkg.Revision\s*=\s*(\S+)\s*$', properties.read_text(), re.M) != [REVISION]):
        raise ValueError('owned_runner_existing_locked_ndk_required')
    # A fresh private destination; never write a binary/JAR into the repository.
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    binary = output / 'source-owned-runner'
    command = [str(compiler), '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
               '-fPIE', '-pie', '-Wl,-z,relro,-z,now', str(SOURCE), '-o', str(binary)]
    result = subprocess.run(command, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError('owned_runner_compile_failed')
    binary.chmod(0o700)
    def pin(path): return hashlib.sha256(path.read_bytes()).hexdigest()
    value = {'schema': 'source-owned-runner-build-v1', 'NDK_revision': REVISION,
             'ABI': 'arm64-v8a', 'API': 30,
             'source_sha256': pin(SOURCE), 'compiler_sha256': pin(compiler),
             'binary_sha256': pin(binary), 'binary_bytes': binary.stat().st_size,
             'fixture_macro_enabled': False, 'device_operations': 0,
             'UI_sessions_started': 0, 'App_artifact_changed': False}
    receipt = output / 'build.json'
    receipt.write_text(json.dumps(value, indent=2) + '\n'); receipt.chmod(0o600)
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--output', type=Path)
    sdk = Path(os.environ.get('ANDROID_HOME', Path.home() / 'Library/Android/sdk'))
    parser.add_argument('--ndk', type=Path,
        default=Path(os.environ.get('ANDROID_NDK_ROOT', sdk / 'ndk' / REVISION)))
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps({'phase': 'prepared_not_built', 'device_operations': 0}))
        return 0
    if args.output is None: parser.error('--output required for explicit build')
    print(json.dumps(build(args.output, args.ndk), sort_keys=True))
    return 0


if __name__ == '__main__': raise SystemExit(main())
