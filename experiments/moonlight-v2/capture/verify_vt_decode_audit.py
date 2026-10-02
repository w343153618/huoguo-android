#!/usr/bin/env python3
"""Build and explicitly inject ONLY an owned synthetic VT session helper, never an AVD."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
AUDIT_KEYS = {'vt_h264_create_audit', 'ordinal', 'require_requested', 'require_applied',
              'force_prepare_status', 'create_status', 'session_created', 'property_read_status',
              'property_is_boolean', 'hardware_known', 'hardware'}
DRIVER_KEYS = {'synthetic_driver', 'format_status', 'create_status', 'property_status', 'hardware_known',
               'hardware', 'original_spec_preserved', 'jpeg'}


def build(output):
    common = ['xcrun', 'clang', '-std=c11', '-arch', 'arm64', '-mmacosx-version-min=11.0', '-O2',
              '-Wall', '-Wextra', '-Werror', '-framework', 'VideoToolbox', '-framework', 'CoreMedia',
              '-framework', 'CoreFoundation']
    library, helper = output/'huoguo-vt-decode-audit.dylib', output/'huoguo-vt-decode-audit-selftest'
    subprocess.run(common+['-fvisibility=hidden', '-dynamiclib', str(ROOT/'vt_decode_audit.c'), '-o', str(library)], check=True)
    subprocess.run(common+[str(ROOT/'vt_decode_audit_selftest.c'), '-o', str(helper)], check=True)
    return library, helper


def objects(data, allowed):
    result = []
    for line in data.decode(errors='replace').splitlines():
        if not line.startswith('{'):
            continue
        value = json.loads(line)
        if set(value) != allowed or any(type(v) not in (bool, int) for v in value.values()):
            raise RuntimeError('Unexpected self-test metadata schema; raw output suppressed')
        result.append(value)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=Path('/private/tmp'))
    parser.add_argument('--evidence', type=Path, default=ROOT/'vt-decode-audit-selftest.json')
    parser.add_argument('--build-only', action='store_true')
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if not output.is_relative_to(Path('/private/tmp')):
        raise SystemExit('Generated binaries must remain under /private/tmp')
    output.mkdir(parents=True, exist_ok=True)
    library, helper = build(output)
    if args.build_only:
        print(json.dumps({'compiled_arm64': True, 'selftest_executed': False}))
        return
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise SystemExit('Existing CPU ffmpeg is required; no download/install attempted')
    evidence = {'synthetic_helper_only': True, 'avd_loaded': False, 'frames_decoded': 0, 'tests': []}
    with tempfile.TemporaryDirectory(prefix='huoguo-vt-audit-fixture-', dir='/private/tmp') as temporary:
        folder = Path(temporary); source, configuration = folder/'cpu-synthetic.h264', folder/'parameters.bin'
        subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
                        'color=black:size=320x240:rate=1', '-frames:v', '1', '-c:v', 'libx264', '-preset', 'ultrafast',
                        '-tune', 'zerolatency', '-profile:v', 'baseline', '-refs', '1', '-bf', '0', '-pix_fmt', 'yuv420p',
                        '-f', 'h264', str(source)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        data=source.read_bytes(); starts=list(re.finditer(b'\x00\x00\x00?\x01',data)); parameters={}
        for index, match in enumerate(starts):
            payload=data[match.end():starts[index+1].start() if index+1<len(starts) else len(data)]
            if payload[0]&31 in (7,8): parameters[payload[0]&31]=payload
        if set(parameters)!={7,8} or len(parameters[7])+len(parameters[8])>4092:
            raise RuntimeError('Synthetic parameter bounds')
        configuration.write_bytes(struct.pack('>HH',len(parameters[7]),len(parameters[8]))+parameters[7]+parameters[8])
        for label, mode, injected, require in (
            ('baseline', 'default', False, None),
            ('audit_default', 'default', True, None),
            ('audit_disabled_negative_control', 'disable', True, None),
            ('require_hardware', 'default', True, '1'),
            ('require_overrides_disabled', 'disable', True, '1'),
            ('literal_one_only', 'disable', True, 'true'),
            ('jpeg_passthrough', 'jpeg', True, None)):
            environment=dict(os.environ)
            # Only this owned helper receives the injection. Do not chain any
            # ambient libraries or preserve an ambient force setting in tests.
            environment.pop('DYLD_INSERT_LIBRARIES',None)
            environment.pop('HUOGUO_REQUIRE_HW_DECODE',None)
            if injected: environment['DYLD_INSERT_LIBRARIES']=str(library)
            if require is not None: environment['HUOGUO_REQUIRE_HW_DECODE']=require
            completed=subprocess.run([str(helper),str(configuration),mode],env=environment,capture_output=True,timeout=15)
            driver=objects(completed.stdout,DRIVER_KEYS)
            audits=objects(completed.stderr,AUDIT_KEYS)
            if completed.returncode or len(driver)!=1:
                raise RuntimeError('Synthetic VT helper failed; raw output suppressed')
            expected=1 if injected and mode!='jpeg' else 0
            if len(audits)!=expected or not driver[0]['original_spec_preserved']:
                raise RuntimeError('Interposition count or source specification preservation failed')
            if audits:
                audit=audits[0]
                if audit['require_requested']!=(require=='1') or audit['require_applied']!=(require=='1'):
                    raise RuntimeError('Exact opt-in contract failed')
                if audit['create_status']!=driver[0]['create_status'] or audit['hardware_known']!=driver[0]['hardware_known'] or audit['hardware']!=driver[0]['hardware']:
                    raise RuntimeError('Actual VT property audit disagrees with independent helper query')
                if require=='1' and audit['create_status']==0 and (not audit['hardware_known'] or not audit['hardware']):
                    raise RuntimeError('Required hardware returned successful known software')
            if mode=='disable' and require!='1' and driver[0]['create_status']==0 and driver[0]['hardware_known'] and driver[0]['hardware']:
                raise RuntimeError('Audit-only silently enabled hardware over caller disable')
            evidence['tests'].append({'case':label,'driver':driver[0],'audit':audits})
    evidence['checks_passed']=True
    args.evidence.parent.mkdir(parents=True,exist_ok=True)
    args.evidence.write_text(json.dumps(evidence,indent=2)+'\n')
    print(json.dumps(evidence))


if __name__=='__main__':
    main()
