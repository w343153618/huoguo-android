#!/usr/bin/env python3
"""Bounded normal-UI LAN acceptance; preplaced owner-only account input required.

Does not create accounts, read a host credential or provision a UDP key. Outputs
only bounded numeric test reports, never instrumentation/system raw logs.
"""
import argparse
import json
import math
from pathlib import Path
import shlex
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = '/data/user/0/local.remoteandroid.direct.experiment/files/'


def verify_surface_submit_readback(result, expected_lead):
    """Require actual per-session execution evidence, never descriptor/request echo alone."""
    if (type(expected_lead) is not int or expected_lead not in (0, 16)
            or not isinstance(result, dict) or 'failure_class' in result
            or type(result.get('requested_surface_submit_lead_ms')) is not int
            or result['requested_surface_submit_lead_ms'] != expected_lead):
        return False
    for stage in ('first', 'second'):
        if result.get(stage+'_surface_submit_execution_verified') is not True:
            return False
        keys = ('surface_submit_lead_ms', 'surface_submit_status_code',
                'surface_submit_wait_count', 'surface_submit_applications')
        values = [result.get(stage+'_'+key) for key in keys]
        if any(type(value) is not int for value in values):
            return False
        lead, status, waits, applications = values
        if lead != expected_lead or (expected_lead == 0 and (status, waits, applications) != (0, 0, 0)):
            return False
        if expected_lead == 16 and (status != 1 or waits < 1 or applications < 1):
            return False
    return True


def verify_steady_window_readback(result, expected_seconds):
    """The sampler completion marker is required before the UI may leave steady media."""
    if (type(expected_seconds) is not int or not 20 <= expected_seconds <= 30
            or not isinstance(result,dict) or 'failure_class' in result
            or type(result.get('requested_steady_seconds')) is not int
            or result['requested_steady_seconds']!=expected_seconds
            or result.get('steady_sampler_completion_observed') is not True):
        return False
    started,finished,wait_ms=(result.get(key) for key in (
        'steady_media_started_ns','steady_media_finished_ns','steady_media_wait_ms'))
    return bool(type(started) is int and type(finished) is int and 0 < started <= finished
        and type(wait_ms) in (int,float) and math.isfinite(wait_ms)
        and wait_ms >= (expected_seconds+2)*1000
        and abs(wait_ms-(finished-started)/1e6) <= .001)


def verify_stage_diagnostics_readback(result, expected_enabled):
    if (type(expected_enabled) is not bool or not isinstance(result,dict)
            or 'failure_class' in result or result.get('requested_stage_diagnostics_enabled') is not expected_enabled):
        return False
    for stage in ('first','second'):
        value=result.get(stage+'_stage_diagnostics_enabled')
        if (type(value) is not int or value != int(expected_enabled)
                or result.get(stage+'_stage_diagnostics_verified') is not True):
            return False
    return True


def verify_codec_startup_readback(result, expected_enabled):
    """Require ended sessions' mode and committed gate execution, not an option echo."""
    if (type(expected_enabled) is not bool or not isinstance(result, dict)
            or 'failure_class' in result
            or result.get('requested_codec_startup_ready_enabled') is not expected_enabled):
        return False
    for stage in ('first', 'second'):
        if (type(result.get(stage+'_codec_startup_ready_enabled')) is not int
                or result[stage+'_codec_startup_ready_enabled']!=int(expected_enabled)
                or result.get(stage+'_codec_startup_readback_verified') is not True):
            return False
        gate=result.get(stage+'_codec_startup_gate')
        fields=('enabled','phase_before_close','failure_code','ready_ns',
                'fresh_received_ns','committed_ns','bootstrap_pts_us','fresh_pts_us')
        if not isinstance(gate,dict) or any(type(gate.get(key)) is not int for key in fields):
            return False
        if expected_enabled:
            if (gate['enabled']!=1 or gate['phase_before_close']!=5 or gate['failure_code']!=0
                    or not 0<gate['ready_ns']<=gate['fresh_received_ns']<=gate['committed_ns']
                    or not 0<=gate['bootstrap_pts_us']<gate['fresh_pts_us']):
                return False
        elif (gate['enabled'],gate['phase_before_close'],gate['committed_ns'])!=(0,0,0):
            return False
    return True


def verify_steady_media_progress(result):
    if (not isinstance(result,dict) or result.get('steady_progress_monitor_enabled') is not True
            or result.get('steady_media_progress_healthy') is not True):return False
    limit=result.get('steady_progress_stall_threshold_ns');idle=result.get('steady_progress_max_idle_ns')
    rows=result.get('steady_progress_samples')
    if (type(limit) is not int or limit!=3000000000 or type(idle) is not int or not 0<=idle<limit
            or not isinstance(rows,list) or not 20<=len(rows)<=48):return False
    for i,row in enumerate(rows):
        if not isinstance(row,dict):return False
        for key in ('phone_ns','worker_received_frames','codec_callback_count'):
            if type(row.get(key)) is not int or row[key]<0:return False
        if i and (row['phone_ns']<=rows[i-1]['phone_ns'] or
                  any(row[k]<rows[i-1][k] for k in ('worker_received_frames','codec_callback_count'))):return False
    return (rows[-1]['phone_ns']-rows[0]['phone_ns']>=19000000000 and
            any(rows[-1][k]>rows[0][k] for k in ('worker_received_frames','codec_callback_count')))


def record_cleanup_failure(report, operation, failure=None, returncode=None):
    """Never include subprocess output or exception text in numeric evidence."""
    item = {'operation': operation}
    if failure is not None:
        item['failure_class'] = type(failure).__name__
    if returncode is not None:
        item['return_code'] = returncode
    report.setdefault('cleanup_failures', []).append(item)


def attempt_cleanup(report, operation, call):
    try:
        result = call()
        if result.returncode:
            record_cleanup_failure(report, operation, returncode=result.returncode)
    except Exception as failure:
        record_cleanup_failure(report, operation, failure=failure)


def reap_owned_process(proc, report, operation, wait_timeout, terminate=False):
    """Bound every owned child wait, including a child ignoring SIGTERM."""
    def signal(method):
        try:
            if proc.poll() is None:
                getattr(proc, method)()
        except Exception as failure:
            record_cleanup_failure(report, operation+'_'+method, failure=failure)

    if terminate:
        signal('terminate')
    for timeout, escalation in [(wait_timeout, 'terminate'), (2, 'kill'), (2, None)]:
        try:
            return proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as failure:
            record_cleanup_failure(report, operation+'_wait', failure=failure)
            if escalation is not None:
                signal(escalation)
        except Exception as failure:
            record_cleanup_failure(report, operation+'_reap', failure=failure)
            signal('kill')
            # A failed pipe read can still leave a running child. Reap it with
            # wait, without depending on those broken pipes or printing them.
            try:
                proc.wait(timeout=2)
            except Exception as wait_failure:
                record_cleanup_failure(report, operation+'_final_wait', failure=wait_failure)
            return None
    return None


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--phone', default='f7fc9469')
    p.add_argument('--guest', default='emulator-5556')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--touch-mode', choices=['direct','os','adb','kernel'], default='direct')
    p.add_argument('--rate-index', type=int, choices=range(5), default=2)
    p.add_argument('--network-scope', choices=['lan','tailnet'], default='lan')
    p.add_argument('--pcm-queue', choices=['off','on'], default='off')
    p.add_argument('--stage-diagnostics', choices=['off','on'], default='on',
                   help='Owner instrumentation only: stage sampling on/off, frozen before media startup')
    p.add_argument('--codec-startup', choices=['off','on'], default='off',
                   help='Explicit one-attempt codec-ready startup admission candidate; steady queue policy retained')
    p.add_argument('--surface-submit-lead-ms', type=int, choices=[0,16], default=0,
                   help='Explicit owner-only Surface submission experiment; does not change playback target/buffer')
    p.add_argument('--steady-seconds', type=int, choices=range(20,31), default=20,
                   help='Bounded SF steady window; helper waits this duration plus 2 seconds before reconnect')
    p.add_argument('--media-only', action='store_true')
    p.add_argument('--source-description', default='Morphe YouTube real video; selected content format requires separate readback')
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    def adb(serial, command, check=True):
        return subprocess.run(['adb', '-s', serial, 'shell', command],
                              capture_output=True, text=True, timeout=8, check=check)

    def root(command, check=True):
        return adb(args.phone, 'su -c '+shlex.quote(command), check)

    flags=['udp-ui-phase-ready-touch','udp-ui-phase-touch-ready','udp-ui-phase-touch-ready.tmp','udp-ui-phase-steady-media','udp-ui-phase-steady-sampled','udp-ui-phase-steady-sampled.tmp','udp-ui-phase-adb-tap','udp-ui-phase-adb-tap-done','udp-ui-phase-adb-tap-done.tmp']
    proc = None
    instrumentation_reaped = False
    samplers=[]
    report={'scope':'normal App UI existing account; isolated '+('physical LAN' if args.network_scope=='lan' else 'registered Tailnet')+' UDP; outer path requires separate evidence',
            'source':args.source_description+('' if args.media_only else '; dedicated receipt only during touch phase'),
            'phone_sampler_started':False,'touch_source_switched':False}
    report.update(touch_mode=args.touch_mode, video_target_bps=[4000000,8000000,12000000,16000000,24000000][args.rate_index],network_scope=args.network_scope,pcm_queue_enabled=args.pcm_queue=='on',media_only=args.media_only,requested_surface_submit_lead_ms=args.surface_submit_lead_ms,requested_steady_seconds=args.steady_seconds,requested_stage_diagnostics_enabled=args.stage_diagnostics=='on',requested_codec_startup_ready_enabled=args.codec_startup=='on')
    adb_tap_done=False
    try:
        gate=subprocess.run(['lsof','-nP','-iTCP:15556','-sTCP:ESTABLISHED','-t'],
                          capture_output=True, timeout=2)
        if gate.returncode not in (0,1):
            raise RuntimeError('formal_gate_failed')
        if gate.stdout.strip():
            raise RuntimeError('formal_session_active')
        if root('test -s '+PRIVATE+'udp-test-login.json',False).returncode:
            raise RuntimeError('private_login_missing')
        root('rm -f '+PRIVATE+'udp-app-last-report.json '+PRIVATE+'udp-app-first-report.json '+' '.join(PRIVATE+f for f in flags))
        proc = subprocess.Popen(['adb','-s',args.phone,'shell','su -c '+shlex.quote(
            'am instrument -w -e touch_mode '+args.touch_mode+' -e rate_index '+str(args.rate_index)+' -e network_scope '+args.network_scope+' -e pcm_queue '+args.pcm_queue+' -e stage_diagnostics '+args.stage_diagnostics+' -e codec_startup '+args.codec_startup+' -e surface_submit_lead_ms '+str(args.surface_submit_lead_ms)+' -e steady_seconds '+str(args.steady_seconds)+' -e media_only '+str(args.media_only).lower()+' local.huoguo.lanuitest/local.remoteandroid.direct.LanUiAcceptance')],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline=time.monotonic()+120+(args.steady_seconds-20)
        while proc.poll() is None and time.monotonic()<deadline:
            if not report['phone_sampler_started']:
                if root('test -f '+PRIVATE+'udp-ui-phase-steady-media',False).returncode==0:
                    for name,serial,package in [('phone',args.phone,'local.remoteandroid.direct.experiment'),
                                               ('source',args.guest,'app.morphe.android.youtube')]:
                        samplers.append(subprocess.Popen([sys.executable,str(ROOT/'scripts/probes/measure_surface_cadence.py'),
                            '--serial',serial,'--package',package,'--seconds',str(args.steady_seconds),'--wait-layer','3',
                            '--output',str(args.output/(name+'-cadence.json'))],stdout=subprocess.PIPE,stderr=subprocess.PIPE))
                    report['phone_sampler_started']=True
            if (report['phone_sampler_started'] and not report.get('steady_samplers_completed_before_leave')
                    and len(samplers)==2 and all(child.poll() is not None for child in samplers)):
                if any(child.returncode!=0 for child in samplers):
                    raise RuntimeError('steady_sampler_failed')
                uid=adb(args.phone,'cmd package list packages -U local.remoteandroid.direct.experiment').stdout.split('uid:',1)[1].split(',',1)[0].strip()
                if not uid.isdigit():
                    raise ValueError('isolated_App_uid_readback')
                done=PRIVATE+'udp-ui-phase-steady-sampled';staged=done+'.tmp'
                root('touch '+staged+' && chown '+uid+':'+uid+' '+staged+' && chmod 600 '+staged+' && restorecon '+staged+' && mv '+staged+' '+done)
                report['steady_samplers_completed_before_leave']=True
            if not report['touch_source_switched'] and root('test -f '+PRIVATE+'udp-ui-phase-ready-touch',False).returncode==0:
                adb(args.guest,'am force-stop local.huoguo.touchreceipt')
                adb(args.guest,'am start -n local.huoguo.touchreceipt/.TouchReceiptActivity')
                focus_deadline=time.monotonic()+4
                while time.monotonic()<focus_deadline:
                    focus=adb(args.guest,'dumpsys window | sed -n "/mCurrentFocus=/p"').stdout
                    if 'local.huoguo.touchreceipt/' in focus and 'TouchReceiptActivity' in focus:break
                    time.sleep(.25)
                else:raise RuntimeError('guest_receipt_not_focused')
                # Focus can precede a usable fullscreen input window. This is
                # test setup, outside the media sampling window.
                time.sleep(4)
                uid=adb(args.phone,'cmd package list packages -U local.remoteandroid.direct.experiment').stdout.split('uid:',1)[1].split(',',1)[0].strip()
                if not uid.isdigit():
                    raise ValueError('isolated_App_uid_readback')
                ready=PRIVATE+'udp-ui-phase-touch-ready'
                staged=ready+'.tmp'
                root('touch '+staged+' && chown '+uid+':'+uid+' '+staged+' && chmod 600 '+staged+' && restorecon '+staged+' && mv '+staged+' '+ready)
                report['touch_source_switched']=True
            if args.touch_mode in ('adb','kernel') and not adb_tap_done and root('test -f '+PRIVATE+'udp-ui-phase-adb-tap',False).returncode==0:
                raw=root('cat '+PRIVATE+'udp-ui-phase-adb-tap').stdout
                if len(raw)>64:raise ValueError('test_coordinate_file_bound')
                x,y=json.loads(raw)
                if type(x)!=int or type(y)!=int or not 0<x<10000 or not 0<y<10000:raise ValueError('test_coordinate_bound')
                if args.touch_mode=='adb':
                    report['adb_touch_command_exit']=adb(args.phone,'input touchscreen -d 0 tap '+str(x)+' '+str(y),False).returncode
                else:
                    if args.phone!='f7fc9469':raise ValueError('kernel_test_dedicated_phone_only')
                    capability=root('getevent -lp /dev/input/event8').stdout
                    if not ('"touchpanel"' in capability and 'max 23040' in capability and 'max 50688' in capability and 'ABS_MT_SLOT' in capability and 'max 9' in capability):
                        raise ValueError('kernel_touch_capability_mismatch')
                    device='/dev/input/event8'
                    def send(rows):
                        command='; '.join('sendevent '+device+' '+str(t)+' '+str(c)+' '+str(v) for t,c,v in rows)
                        root(command)
                    def release():
                        send([(3,47,0),(3,57,-1),(3,47,1),(3,57,-1),(1,330,0),(1,325,0),(0,0,0)])
                    try:
                        # Fixed calibrated root test only: evdev -> InputReader
                        # -> phone Window -> UDP. No physical-finger latency claim.
                        send([(3,47,0),(3,57,54320),(3,53,x*16),(3,54,y*16),(3,48,30),(3,49,30),(1,330,1),(1,325,1),(0,0,0)])
                        time.sleep(.08);release();time.sleep(.2)
                        send([(3,47,0),(3,57,54321),(3,53,(x-60)*16),(3,54,y*16),(3,48,30),(3,49,30),(1,330,1),(1,325,1),(0,0,0)])
                        time.sleep(.08)
                        send([(3,47,1),(3,57,54322),(3,53,(x+60)*16),(3,54,y*16),(3,48,30),(3,49,30),(0,0,0)])
                        time.sleep(.08)
                        send([(3,47,0),(3,53,(x-55)*16),(3,54,(y+5)*16),(3,47,1),(3,53,(x+55)*16),(3,54,(y+5)*16),(0,0,0)])
                        time.sleep(.08)
                    finally:release()
                    report['kernel_touch_two_contacts_attempted']=True
                done=PRIVATE+'udp-ui-phase-adb-tap-done';staged=done+'.tmp'
                root('touch '+staged+' && chown '+uid+':'+uid+' '+staged+' && chmod 600 '+staged+' && restorecon '+staged+' && mv '+staged+' '+done)
                adb_tap_done=True
            time.sleep(.5)
        if proc.poll() is None:
            report['timeout']=True
            raise TimeoutError('instrumentation_timeout')
        stdout,stderr=proc.communicate(timeout=5)
        instrumentation_reaped = True
        report['instrumentation_exit_code']=proc.returncode
        marker='INSTRUMENTATION_RESULT: numeric_result='
        numeric=next((line[len(marker):] for line in stdout.splitlines() if line.startswith(marker)),None)
        if numeric is not None and len(numeric)<=65536:
            report['ui_result']=json.loads(numeric)
        else:
            report['numeric_result_missing']=True
        report['surface_submit_execution_verified']=verify_surface_submit_readback(report.get('ui_result'),args.surface_submit_lead_ms)
        report['stage_diagnostics_readback_verified']=verify_stage_diagnostics_readback(report.get('ui_result'),args.stage_diagnostics=='on')
        report['codec_startup_readback_verified']=verify_codec_startup_readback(report.get('ui_result'),args.codec_startup=='on')
        report['steady_media_progress_verified']=verify_steady_media_progress(report.get('ui_result'))
        report['steady_window_readback_verified']=verify_steady_window_readback(report.get('ui_result'),args.steady_seconds)
        reports=[('App',args.phone,PRIVATE+'udp-app-last-report.json'),
                 ('App-first',args.phone,PRIVATE+'udp-app-first-report.json')]
        if not args.media_only:
            reports.append(('guest_touch',args.guest,'/data/user/0/local.huoguo.touchreceipt/files/touch-receipt.json'))
        else:
            report['guest_touch_not_exercised']=True
        for name,serial,path in reports:
            result=root('cat '+path,False) if serial==args.phone else adb(serial,'run-as local.huoguo.touchreceipt cat files/touch-receipt.json',False)
            if result.returncode==0 and 0<len(result.stdout)<=65536:
                report[name+'_actual_json_bytes']=len(result.stdout.encode('utf-8'))
                (args.output/(name+'-report.json')).write_text(json.dumps(json.loads(result.stdout),indent=2)+'\n')
                report[name+'_report_read']=True
        if not report['surface_submit_execution_verified']:
            raise RuntimeError('surface_submit_readback_unverified')
        if not report['steady_window_readback_verified']:
            raise RuntimeError('steady_window_readback_unverified')
        if not report['stage_diagnostics_readback_verified']:
            raise RuntimeError('stage_diagnostics_readback_unverified')
        if not report['codec_startup_readback_verified']:
            raise RuntimeError('codec_startup_readback_unverified')
        if not report['steady_media_progress_verified']:
            raise RuntimeError('steady_media_progress_stalled_or_unverified')
    except (Exception, KeyboardInterrupt) as failure:
        report['driver_failure_class']=type(failure).__name__
        labels = {'formal_gate_failed','formal_session_active','private_login_missing',
                  'instrumentation_timeout','guest_receipt_not_focused',
                  'isolated_App_uid_readback','test_coordinate_file_bound',
                  'test_coordinate_bound','kernel_test_dedicated_phone_only',
                  'kernel_touch_capability_mismatch','surface_submit_readback_unverified',
                  'steady_sampler_failed','steady_window_readback_unverified','stage_diagnostics_readback_unverified',
                  'steady_media_progress_stalled_or_unverified','codec_startup_readback_unverified'}
        if str(failure) in labels:report['driver_failure_label']=str(failure)
    finally:
        failed='driver_failure_class' in report
        if proc is not None and failed:
            # Terminating the local adb client alone does not end Android
            # instrumentation. Stop only these two known isolated packages.
            for package in ('local.huoguo.lanuitest','local.remoteandroid.direct.experiment'):
                attempt_cleanup(report, 'stop_'+package,
                                lambda package=package: adb(args.phone,'am force-stop '+package,False))
        if proc is not None and not instrumentation_reaped:
            reap_owned_process(proc, report, 'instrumentation', 2, terminate=failed)
            report['instrumentation_exit_code']=proc.returncode
        for sampler in samplers:
            reap_owned_process(sampler, report, 'sampler', 1 if failed else args.steady_seconds+5, terminate=failed)
            report.setdefault('sampler_exit_codes',[]).append(sampler.returncode)
        attempt_cleanup(report, 'remove_private_test_input',
                        lambda: root('rm -f '+PRIVATE+'udp-test-login.json '+' '.join(PRIVATE+f for f in flags),False))
    (args.output/'ui-acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))
    return 1 if 'driver_failure_class' in report or report.get('cleanup_failures') else 0


if __name__=='__main__':
    sys.exit(main())
