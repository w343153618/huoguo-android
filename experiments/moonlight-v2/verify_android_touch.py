#!/usr/bin/env python3
"""Send native C++ touch packets into an isolated M1 emulator test scene.

Requires built native probes and installed diagnostic-source APK. This is an
Android input integration test, not a phone/WAN video-performance measurement.
The legacy external directory holds runtime dependencies, not editable source.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import uuid

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from hardware_stream import HostHardwareSession

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--serial',default='emulator-5556')
    parser.add_argument('--runtime',type=Path,default=Path.home()/'Documents/ChatGPT/others/android-remote/m1-compare')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    adb=Path(os.environ.get('ANDROID_HOME',str(Path.home()/'Library/Android/sdk')))/'platform-tools/adb'
    native=Path(os.environ.get('HUOGUO_V2_CACHE',str(Path.home()/'.cache/huoguo-v2-sources')))/'prototype-build/touch_probe'
    def command(*words):
        return subprocess.run([str(adb),'-s',args.serial,*words],check=True,capture_output=True,text=True).stdout
    size=command('shell','wm','size')
    if not re.search(r'(Physical|Override) size: 540x1200',size):
        raise SystemExit('This integration probe expects an existing 540x1200 test emulator; it never changes its resolution')
    run_id=str(uuid.uuid4())
    session=None
    try:
        command('shell','input','keyevent','KEYCODE_WAKEUP')
        command('shell','am','start','-n','local.remoteandroid.benchmark/.DiagnosticSourceActivity',
                '--es','run_id',run_id,'--ei','source_fps','30')
        session=HostHardwareSession(args.runtime,args.serial,'RemoteAndroid17Compare',1200,4000000,30,'VBR')
        session.start()
        def drain(sock):
            try:
                while sock.recv(65536):pass
            except OSError:pass
        for role in ('video','audio'):
            threading.Thread(target=drain,args=(session.channel(role),),daemon=True).start()
        time.sleep(1)
        # Two independent fingers move apart then lift separately.
        events='1 11 .30 .50 .8\n1 22 .70 .50 .7\n3 11 .20 .50 .8\n3 22 .80 .50 .7\n2 11 .20 .50 .8\n2 22 .80 .50 .7\n'
        packets=subprocess.run([str(native),'--emit'],input=events.encode(),capture_output=True,check=True).stdout
        if len(packets)!=6*32:raise RuntimeError('Unexpected native packet count')
        for at in range(0,len(packets),32):
            session.channel('control').sendall(packets[at:at+32]);time.sleep(.075)
        time.sleep(.4)
        logs=command('logcat','-d','-v','brief','-s','DiagnosticSource:I')
        selected=[line.split('synthetic_touch ',1)[1] for line in logs.splitlines()
                  if 'synthetic_touch ' in line and 'run_id='+run_id in line]
        received=[{key:val for key,val in re.findall(r'(\w+)=([^ ]+)',line)} for line in selected]
        actions=[int(row['action']) for row in received]
        if actions!=[0,5,2,2,6,1]:raise RuntimeError('Unexpected Android gesture actions: '+str(actions))
        if any(int(row['source'])!=4098 for row in received):raise RuntimeError('Android did not receive SOURCE_TOUCHSCREEN')
        if [int(row['count']) for row in received]!=[1,2,2,2,2,1]:raise RuntimeError('Android multi-pointer state is incorrect')
        report={'status':'PASS','layer':'native C++ to scrcpy control to Android MotionEvent',
                'serial':args.serial,'display':'540x1200','transport':'local private socket pairs',
                'source':'diagnostic touch scene, not real video','events':received,
                'limitations':['No Moonlight host connection','No phone or WAN input measurement']}
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print('PASS Android received native two-finger pinch: SOURCE_TOUCHSCREEN, DOWN/POINTER_DOWN/MOVE/POINTER_UP/UP')
    finally:
        if session:session.close()
        command('shell','input','keyevent','KEYCODE_BACK')

if __name__=='__main__':main()
