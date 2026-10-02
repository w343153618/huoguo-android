#!/usr/bin/env python3
"""Verified 60/120Hz trial with private exact config backup; no disk reset.

Uses the existing KeepAlive LaunchAgent for the same AVD. Does not edit it.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

EXPECTED = {'hw.cpu.ncore':'6','hw.ramSize':'16384','hw.gpu.mode':'host'}
DISPLAY_PROFILES = {('720', '1280', '320'), ('1080', '1920', '480')}


def replace_vsync(raw, hz):
    if hz not in (60,120):
        raise ValueError('unsupported_physical_vsync')
    lines=raw.decode('utf-8').splitlines(keepends=True)
    found=[];actual={}
    for index,line in enumerate(lines):
        match=re.match(r'^([A-Za-z0-9_.]+)\s*=\s*([^\r\n]*)',line)
        if match:
            actual[match[1]]=match[2].strip()
            if match[1]=='hw.lcd.vsync':found.append(index)
    display = tuple(actual.get(k) for k in ('hw.lcd.width', 'hw.lcd.height', 'hw.lcd.density'))
    if (len(found)!=1 or any(actual.get(k)!=v for k,v in EXPECTED.items())
            or display not in DISPLAY_PROFILES):
        raise ValueError('unexpected_avd_configuration')
    original=int(actual['hw.lcd.vsync'])
    if original not in (60,120):
        raise ValueError('unexpected_physical_vsync')
    index=found[0]
    lines[index]=re.sub(r'(=\s*)[0-9]+',lambda m:m[1]+str(hz),lines[index],count=1)
    return ''.join(lines).encode('utf-8'),original


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hz',type=int,choices=(60,120),required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--restore-exact-config',type=Path)
    args=parser.parse_args()
    if args.output.exists():parser.error('fresh evidence required')
    adb=Path.home()/'Library/Android/sdk/platform-tools/adb'
    config=Path.home()/'Documents/ChatGPT/others/android-remote/avd/RemoteAndroid17Compare.avd/config.ini'
    raw=config.read_bytes();replacement,old=replace_vsync(raw,args.hz)
    if args.restore_exact_config:
        backup=args.restore_exact_config.resolve(strict=True)
        if not backup.is_relative_to(Path('/private/tmp')) or backup.stat().st_mode&0o077:
            parser.error('private exact backup required')
        replacement=backup.read_bytes()
        _,restored=replace_vsync(replacement,args.hz)
        if restored!=args.hz:parser.error('backup has a different requested rate')
    processes=subprocess.run(['ps','-axo','command='],capture_output=True,text=True,check=True,timeout=5).stdout
    if any('hardware_stream.py' in row and '--serial emulator-5556' in row for row in processes.splitlines()):
        parser.error('existing capture worker retained; cannot reboot now')
    identity=subprocess.run([str(adb),'-s','emulator-5556','emu','avd','name'],capture_output=True,text=True,timeout=8)
    if 'RemoteAndroid17Compare' not in identity.stdout.splitlines():
        parser.error('selected AVD not confirmed')
    directory=Path(tempfile.mkdtemp(prefix='huoguo-physical-vsync-',dir='/private/tmp'));os.chmod(directory,0o700)
    backup=directory/'original-config.ini'
    fd=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as out:out.write(raw)
    report=dict(schema=1,requested_hz=args.hz,previous_hz=old,
                previous_config_sha256=hashlib.sha256(raw).hexdigest(),
                trial_config_sha256=hashlib.sha256(replacement).hexdigest(),
                disk_data_deleted=False,launch_agent_changed=False,host_start_monotonic_ns=time.monotonic_ns())
    config.write_bytes(replacement)
    stopped=subprocess.run([str(adb),'-s','emulator-5556','emu','kill'],
                           stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
    report['graceful_stop_command_ok']=stopped.returncode==0
    deadline=time.monotonic()+100;boot=False
    time.sleep(4)
    while time.monotonic()<deadline:
        p=subprocess.run([str(adb),'-s','emulator-5556','shell','getprop','sys.boot_completed'],
                         capture_output=True,text=True,timeout=5)
        if p.returncode==0 and p.stdout.strip()=='1':boot=True;break
        time.sleep(1)
    report.update(boot_completed=boot,host_end_monotonic_ns=time.monotonic_ns())
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as out:json.dump(report,out,indent=2);out.write('\n')
    print(json.dumps({'boot_completed':boot,'requested_hz':args.hz,'private_exact_backup':str(backup)}))
    return 0 if boot else 1


if __name__=='__main__':
    raise SystemExit(main())
