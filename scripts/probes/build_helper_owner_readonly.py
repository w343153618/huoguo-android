#!/usr/bin/env python3
"""Private compile of the inert native readonly query component; no device ops."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import re
import subprocess

ROOT=Path(__file__).resolve().parents[2]
DIRECTORY=ROOT/'experiments/moonlight-v2/source-snapshot'
SOURCE=DIRECTORY/'helper_owner_readonly.c'
INPUTS=[SOURCE,DIRECTORY/'helper_scope_crypto.h']
REVISION='29.0.14206865'

def build(output,ndk):
    output,ndk=Path(output).resolve(),Path(ndk).resolve()
    if output==ROOT or ROOT in output.parents: raise ValueError('helper_read_private_build_required')
    tag={'Darwin':'darwin-x86_64','Linux':'linux-x86_64'}.get(platform.system())
    if not tag: raise ValueError('helper_read_host_rejected')
    compiler=ndk/'toolchains/llvm/prebuilt'/tag/'bin/aarch64-linux-android30-clang';props=ndk/'source.properties'
    if (any(not p.is_file() for p in INPUTS) or not compiler.is_file() or not props.is_file()
            or re.findall(r'^Pkg.Revision\s*=\s*(\S+)\s*$',props.read_text(),re.M)!=[REVISION]):
        raise ValueError('helper_read_existing_locked_NDK_required')
    output.mkdir(mode=0o700,parents=False,exist_ok=False);binary=output/'helper-owner-readonly'
    r=subprocess.run([str(compiler),'-std=c11','-O2','-Wall','-Wextra','-Werror','-fPIE','-pie',
        '-fvisibility=hidden','-Wl,-z,relro,-z,now',str(SOURCE),'-o',str(binary)],
        stdin=subprocess.DEVNULL,capture_output=True,timeout=30)
    if r.returncode: raise RuntimeError('helper_read_compile_failed')
    binary.chmod(0o700);data=binary.read_bytes()
    if data[:6]!=b'\x7fELF\x02\x01' or int.from_bytes(data[18:20],'little')!=183: raise RuntimeError('helper_read_ELF_rejected')
    if b'--fixture' in data or b'public host APK standin' in data: raise RuntimeError('helper_read_fixture_macro_in_Android_build')
    v=dict(schema='inert-helper-readonly-build-v1',NDK_revision=REVISION,API=30,ABI='arm64-v8a',
        source_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in INPUTS},
        compiler_sha256=hashlib.sha256(compiler.read_bytes()).hexdigest(),binary_sha256=hashlib.sha256(data).hexdigest(),
        binary_bytes=len(data),ELF_AArch64_verified=True,fixture_macro_enabled=False,
        production_activation_available=False,actual_device_or_PM_execution=False,
        complete_native_qualifier_or_live_bridge=False,App_helper_JNI_or_release_changed=False,reservation_release_authorized=False)
    receipt=output/'build.json';receipt.write_text(json.dumps(v,indent=2)+'\n');receipt.chmod(0o600);return v

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--build',action='store_true');p.add_argument('--output',type=Path)
    p.add_argument('--ndk',type=Path,default=Path.home()/'Library/Android/sdk/ndk'/REVISION);args=p.parse_args(argv)
    if not args.build:
        print(json.dumps(dict(phase='prepared_not_built',device_operations=0,production_activation_available=False)));return 0
    if args.output is None: p.error('--output required for explicit private build')
    print(json.dumps(build(args.output,args.ndk),sort_keys=True));return 0

if __name__=='__main__': raise SystemExit(main())
