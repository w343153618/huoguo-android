#!/usr/bin/env python3
"""Explicit private Android compile for an inert native helper channel candidate.

Default prepared only; no ADB, ART, PM or production activation. Existing locked
NDK is required. All actual compilation inputs are pinned in the private receipt.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import re
import subprocess

ROOT=Path(__file__).resolve().parents[2]
DIRECTORY=ROOT/'experiments/moonlight-v2/source-snapshot'
SOURCE=DIRECTORY/'helper_owner_channel.c'
INPUTS=[SOURCE,DIRECTORY/'helper_scope_owner.c',DIRECTORY/'helper_scope_crypto.h']
REVISION='29.0.14206865'

def build(output,ndk):
    output,ndk=Path(output).resolve(),Path(ndk).resolve()
    if output==ROOT or ROOT in output.parents:
        raise ValueError('helper_channel_private_build_outside_source_required')
    tag={'Darwin':'darwin-x86_64','Linux':'linux-x86_64'}.get(platform.system())
    if not tag: raise ValueError('helper_channel_host_rejected')
    compiler=ndk/'toolchains/llvm/prebuilt'/tag/'bin/aarch64-linux-android30-clang'
    props=ndk/'source.properties'
    if (any(not p.is_file() for p in INPUTS) or not compiler.is_file() or not props.is_file()
            or re.findall(r'^Pkg.Revision\s*=\s*(\S+)\s*$',props.read_text(),re.M)!=[REVISION]):
        raise ValueError('helper_channel_existing_locked_NDK_required')
    output.mkdir(mode=0o700,parents=False,exist_ok=False)
    binary=output/'helper-owner-channel'
    result=subprocess.run([str(compiler),'-std=c11','-O2','-Wall','-Wextra','-Werror',
                           '-fPIE','-pie','-fvisibility=hidden','-Wl,-z,relro,-z,now',
                           str(SOURCE),'-o',str(binary)],stdin=subprocess.DEVNULL,
                          capture_output=True,timeout=30)
    if result.returncode: raise RuntimeError('helper_channel_compile_failed')
    binary.chmod(0o700);data=binary.read_bytes()
    if data[:6]!=b'\x7fELF\x02\x01' or int.from_bytes(data[18:20],'little')!=183:
        raise RuntimeError('helper_channel_ELF_rejected')
    if b'--fixture' in data or b'helper_channel_fixture_footer' in data:
        raise RuntimeError('helper_channel_host_macro_in_Android_build')
    value={'schema':'partial-helper-channel-build-v1','NDK_revision':REVISION,'API':30,'ABI':'arm64-v8a',
           'source_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in INPUTS},
           'compiler_sha256':hashlib.sha256(compiler.read_bytes()).hexdigest(),
           'binary_sha256':hashlib.sha256(data).hexdigest(),'binary_bytes':len(data),
           'ELF_AArch64_verified':True,'fixture_macro_enabled':False,'production_activation_available':False,
           'device_PM_or_native_Android_execution':False,'wire_requests_grant_operator_lease_or_cleanup':False,
           'complete_installer_or_supervisor':False,'App_helper_JNI_or_release_changed':False,
           'reservation_release_authorized':False}
    receipt=output/'build.json';receipt.write_text(json.dumps(value,indent=2)+'\n');receipt.chmod(0o600)
    return value

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build',action='store_true');parser.add_argument('--output',type=Path)
    parser.add_argument('--ndk',type=Path,default=Path.home()/'Library/Android/sdk/ndk'/REVISION)
    args=parser.parse_args(argv)
    if not args.build:
        print(json.dumps({'phase':'prepared_not_built','device_operations':0,'production_activation_available':False}));return 0
    if args.output is None: parser.error('--output required for explicit private build')
    print(json.dumps(build(args.output,args.ndk),sort_keys=True));return 0

if __name__=='__main__': raise SystemExit(main())
