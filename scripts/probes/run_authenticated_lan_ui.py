#!/usr/bin/env python3
"""Bounded normal-UI LAN acceptance; preplaced owner-only account input required.

Does not create accounts, read a host credential or provision a UDP key. Outputs
only bounded numeric test reports, never instrumentation/system raw logs.
"""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = '/data/user/0/local.remoteandroid.direct.experiment/files/'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--phone', default='f7fc9469')
    p.add_argument('--guest', default='emulator-5556')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    def adb(serial, command, check=True):
        return subprocess.run(['adb', '-s', serial, 'shell', command],
                              capture_output=True, text=True, timeout=8, check=check)

    def root(command, check=True):
        return adb(args.phone, 'su -c '+shlex.quote(command), check)

    if subprocess.run(['lsof','-nP','-iTCP:15556','-sTCP:ESTABLISHED','-t'],
                      capture_output=True, timeout=2).stdout.strip():
        p.error('formal session active; skipped')
    root('test -s '+PRIVATE+'udp-test-login.json')
    root('rm -f '+PRIVATE+'udp-app-last-report.json '+PRIVATE+'udp-app-first-report.json '+PRIVATE+'udp-ui-phase-ready-touch '+PRIVATE+'udp-ui-phase-touch-ready')
    proc = subprocess.Popen(['adb','-s',args.phone,'shell','su -c '+shlex.quote(
        'am instrument -w local.huoguo.lanuitest/local.remoteandroid.direct.LanUiAcceptance')],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline=time.monotonic()+120
    samplers=[]
    report={'scope':'normal App UI existing account; isolated physical LAN UDP',
            'source':'Morphe YouTube public Big Buck Bunny aqz-KE-bpKQ; dedicated receipt only during touch phase',
            'phone_sampler_started':False,'touch_source_switched':False}
    try:
        while proc.poll() is None and time.monotonic()<deadline:
            if not report['phone_sampler_started']:
                layers=adb(args.phone,'dumpsys SurfaceFlinger --list').stdout
                if 'SurfaceView' in layers and 'local.remoteandroid.direct.experiment' in layers:
                    for name,serial,package in [('phone',args.phone,'local.remoteandroid.direct.experiment'),
                                               ('source',args.guest,'app.morphe.android.youtube')]:
                        samplers.append(subprocess.Popen([sys.executable,str(ROOT/'scripts/probes/measure_surface_cadence.py'),
                            '--serial',serial,'--package',package,'--seconds','20','--wait-layer','3',
                            '--output',str(args.output/(name+'-cadence.json'))],stdout=subprocess.PIPE,stderr=subprocess.PIPE))
                    report['phone_sampler_started']=True
            if not report['touch_source_switched'] and root('test -f '+PRIVATE+'udp-ui-phase-ready-touch',False).returncode==0:
                adb(args.guest,'am force-stop local.huoguo.touchreceipt')
                adb(args.guest,'am start -n local.huoguo.touchreceipt/.TouchReceiptActivity')
                uid=adb(args.phone,'cmd package list packages -U local.remoteandroid.direct.experiment').stdout.split('uid:',1)[1].split(',',1)[0].strip()
                if not uid.isdigit():
                    raise ValueError('isolated_App_uid_readback')
                ready=PRIVATE+'udp-ui-phase-touch-ready'
                staged=ready+'.tmp'
                root('touch '+staged+' && chown '+uid+':'+uid+' '+staged+' && chmod 600 '+staged+' && restorecon '+staged+' && mv '+staged+' '+ready)
                report['touch_source_switched']=True
            time.sleep(.5)
        if proc.poll() is None:
            proc.terminate()
            report['timeout']=True
        stdout,stderr=proc.communicate(timeout=5)
        report['instrumentation_exit_code']=proc.returncode
        marker='INSTRUMENTATION_RESULT: numeric_result='
        numeric=next((line[len(marker):] for line in stdout.splitlines() if line.startswith(marker)),None)
        if numeric is not None and len(numeric)<=65536:
            report['ui_result']=json.loads(numeric)
        else:
            report['numeric_result_missing']=True
        for name,serial,path in [('App',args.phone,PRIVATE+'udp-app-last-report.json'),
                                 ('App-first',args.phone,PRIVATE+'udp-app-first-report.json'),
                                 ('guest_touch',args.guest,'/data/user/0/local.huoguo.touchreceipt/files/touch-receipt.json')]:
            result=root('cat '+path,False) if serial==args.phone else adb(serial,'run-as local.huoguo.touchreceipt cat files/touch-receipt.json',False)
            if result.returncode==0 and 0<len(result.stdout)<=65536:
                (args.output/(name+'-report.json')).write_text(json.dumps(json.loads(result.stdout),indent=2)+'\n')
                report[name+'_report_read']=True
        for sampler in samplers:
            try:
                sampler.communicate(timeout=25)
            except subprocess.TimeoutExpired:
                sampler.terminate();sampler.communicate(timeout=5)
            report.setdefault('sampler_exit_codes',[]).append(sampler.returncode)
    except Exception as failure:
        report['driver_failure_class']=type(failure).__name__
        # Leave no unbounded instrumentation if a phase setup failed. App
        # background cancellation owns cleanup; no production component stops.
        adb(args.phone,'am force-stop local.huoguo.lanuitest',False)
        adb(args.phone,'am force-stop local.remoteandroid.direct.experiment',False)
        if proc.poll() is None:
            proc.terminate()
        proc.communicate(timeout=5)
    finally:
        root('rm -f '+PRIVATE+'udp-test-login.json '+PRIVATE+'udp-ui-phase-ready-touch '+PRIVATE+'udp-ui-phase-touch-ready '+PRIVATE+'udp-ui-phase-touch-ready.tmp',False)
    (args.output/'ui-acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__':
    main()
