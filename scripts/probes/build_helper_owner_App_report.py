#!/usr/bin/env python3
"""Compile the inert native owned App-report FD reader candidate privately; no Android operations."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess

ROOT=Path(__file__).resolve().parents[2]
DIRECTORY=ROOT/'experiments/moonlight-v2/source-snapshot'
SOURCE=DIRECTORY/'helper_owner_App_report.c'
INPUTS=[SOURCE,*[DIRECTORY/name for name in ('helper_owner_report.c','helper_report_json.h','helper_owner_output.c','helper_owner_control.c','helper_owner_native_driver.c','helper_owner_read_gate.c',
    'helper_owner_readonly.c','helper_owner_lifecycle.c','helper_owner_channel.c',
    'helper_scope_owner.c','helper_scope_crypto.h')]]
REVISION='29.0.14206865'
COMPILER_SHA256='cf2d40be93fb6051d3a66f5d6bdd24ccbe6595913040460f3607a241d562af76'

def build(output,ndk):
    output,ndk=Path(output).resolve(),Path(ndk).resolve()
    if output==ROOT or ROOT in output.parents: raise ValueError('native_App_report_private_build_required')
    tag={'Darwin':'darwin-x86_64','Linux':'linux-x86_64'}.get(platform.system())
    if not tag: raise ValueError('native_App_report_host_rejected')
    compiler=ndk/'toolchains/llvm/prebuilt'/tag/'bin/aarch64-linux-android30-clang'
    props=ndk/'source.properties'
    if (any(not p.is_file() or p.is_symlink() for p in INPUTS) or not compiler.is_file()
            or not props.is_file() or re.findall(r'^Pkg.Revision\s*=\s*(\S+)\s*$',props.read_text(),re.M)!=[REVISION]):
        raise ValueError('native_App_report_locked_NDK_and_sources_required')
    # The observed Mac compiler pin is not asserted for a different Linux host.
    compiler_sha=hashlib.sha256(compiler.read_bytes()).hexdigest()
    if platform.system()=='Darwin' and compiler_sha!=COMPILER_SHA256:
        raise ValueError('native_App_report_compiler_changed')
    pins={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in INPUTS}
    output.mkdir(mode=0o700,parents=False,exist_ok=False);binary=output/'helper-owner-App-report'
    result=subprocess.run([str(compiler),'-std=c11','-O2','-Wall','-Wextra','-Werror',
        '-fPIE','-pie','-fvisibility=hidden','-Wl,-z,relro,-z,now',str(SOURCE),'-o',str(binary)],
        stdin=subprocess.DEVNULL,capture_output=True,timeout=30,
        env={'PATH':os.defpath,'HOME':str(Path.home()),'LANG':'C'})
    if result.returncode: raise RuntimeError('native_App_report_compile_failed')
    if pins!={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in INPUTS}:
        raise RuntimeError('native_App_report_source_changed_during_build')
    binary.chmod(0o700);raw=binary.read_bytes()
    if raw[:6]!=b'\x7fELF\x02\x01' or int.from_bytes(raw[18:20],'little')!=183:
        raise RuntimeError('native_App_report_ELF_rejected')
    if any(marker in raw for marker in (b'--fixture',b'--control-fixture',b'--parse-fixture',b'--owned-fixture',
            b'host_control_fixture_footer',b'public host APK standin')):
        raise RuntimeError('native_App_report_fixture_macro_in_Android_build')
    receipt=dict(schema='inert-native-App-report-build-v1',NDK_revision=REVISION,API=30,
        ABI='arm64-v8a',compiler_sha256=compiler_sha,source_pins=pins,binary_bytes=len(raw),
        binary_sha256=hashlib.sha256(raw).hexdigest(),fixture_macro_enabled=False,
        production_activation_available=False,actual_Android_stage_PM_or_driver=False,
        actual_Mac_coordinator_bridge=False,App_helper_JNI_or_release_changed=False,
        reservation_release_authorized=False)
    path=output/'build.json';path.write_text(json.dumps(receipt,indent=2)+'\n');path.chmod(0o600)
    return receipt

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build',action='store_true');parser.add_argument('--output',type=Path)
    parser.add_argument('--ndk',type=Path,default=Path.home()/'Library/Android/sdk/ndk'/REVISION)
    args=parser.parse_args(argv)
    if not args.build:
        print(json.dumps(dict(phase='prepared_not_built',device_operations=0,production_activation_available=False)));return 0
    if args.output is None: parser.error('--output required for explicit private compile')
    print(json.dumps(build(args.output,args.ndk),sort_keys=True));return 0

if __name__=='__main__':raise SystemExit(main())
