#!/usr/bin/env python3
"""Bounded read-only SF frame/fence metadata, never pixels or playback control.

Root runs this alongside one existing capture/Perfetto measurement. This helper
does not create screenshot subscribers or start/change the player or services.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import select
import shlex
import subprocess
import time

INT64_MAX = 2**63-1
MAX_REPLY_BYTES = 1024*1024
MAX_FRAME_RECORDS = 15000
MAX_INVALID_RECORDS = 2048
HOST_CLOCK = 'host_clock_gettime_CLOCK_MONOTONIC_ns'


def host_clock_ns():
    return time.clock_gettime_ns(time.CLOCK_MONOTONIC)


def wall_snapshot():
    before=host_clock_ns()
    wall=time.time_ns()
    after=host_clock_ns()
    return {'host_before_ns':before,'host_realtime_ns':wall,'host_after_ns':after,
            'host_clock':HOST_CLOCK}


def timestamp_valid(value):
    return type(value) is int and 0<value<INT64_MAX


def invalid_reason(value):
    if timestamp_valid(value):
        return None
    if value==INT64_MAX:
        return 'int64_max_pending_or_unsignaled'
    if value==0:
        return 'zero_no_data'
    return 'negative_or_out_of_range'


def parse_latency(raw):
    """Only numeric whitelist survives; keep sentinel values and validity."""
    if not isinstance(raw,str) or len(raw.encode())>MAX_REPLY_BYTES:
        raise ValueError('latency_reply_size')
    lines=raw.splitlines()
    period=None
    if lines and re.fullmatch(r'\d+',lines[0].strip()):
        candidate=int(lines[0].strip())
        if 1_000_000<=candidate<=100_000_000:
            period=candidate
    rows=[]
    malformed=0
    for line in lines[1:]:
        if not line.strip():
            continue
        parts=line.split()
        if len(parts)!=3 or not all(re.fullmatch(r'-?\d+',x) for x in parts):
            malformed+=1
            continue
        values=[int(x) for x in parts]
        if any(not -2**63<=x<=INT64_MAX for x in values):
            malformed+=1
            continue
        row={}
        for key,value in zip(('desired_present_ns','actual_present_ns','frame_ready_ns'),values):
            row[key]=value
            row[key.removesuffix('_ns')+'_valid']=timestamp_valid(value)
            row[key.removesuffix('_ns')+'_invalid_reason']=invalid_reason(value)
        rows.append(row)
        if len(rows)>256:
            raise ValueError('latency_ring_size')
    return {'display_vsync_ns':period,'rows':rows,'malformed_rows':malformed}


def select_layer(raw,package):
    if not isinstance(raw,str) or len(raw.encode())>MAX_REPLY_BYTES:
        raise ValueError('layer_reply_size')
    layers=[]
    for line in raw.splitlines():
        if package not in line or 'SurfaceView' not in line or '(BLAST)' not in line:
            continue
        if line.startswith('RequestedLayerState{'):
            line=line[len('RequestedLayerState{'):].split(' parentId=',1)[0]
        line=line.strip()
        if '\x00' in line or len(line)>2048:
            raise ValueError('layer_identity_invalid')
        # Require the package component, not a substring of a different app.
        if not re.search(r'(?<![\w.])'+re.escape(package)+r'/',line):
            continue
        layers.append(line)
    return layers[-1] if layers else None


def layer_identity(layer):
    match=re.search(r'#(\d+)$',layer)
    return {'sha256':hashlib.sha256(layer.encode()).hexdigest(),
            'generation_suffix':int(match.group(1)) if match else None,
            'selection':'exact initial selected video SurfaceView layer; never switches generation'}


def clock_mapping_sample(guest,host_before,host_after):
    required=('monotonic_us','unix_us','sample_span_us')
    if not isinstance(guest,dict) or any(type(guest.get(x)) is not int for x in required):
        raise ValueError('guest_clock_schema')
    if not all(0<=guest[x]<=INT64_MAX//1000 for x in required) or guest['sample_span_us']>5000:
        raise ValueError('guest_clock_range')
    if host_after<host_before:
        raise ValueError('host_clock_order')
    # StreamClock truncates nanoTime to us and wall time to ms. No guessed
    # same-epoch equality; cross-machine bounds retain the entire ADB RTT.
    guest_ns=guest['monotonic_us']*1000
    half_span=(guest['sample_span_us']*1000+1)//2+1000
    return {'host_query_before_ns':host_before,'host_query_after_ns':host_after,
            'host_roundtrip_ns':host_after-host_before,'host_clock':HOST_CLOCK,
            'guest_monotonic_us':guest['monotonic_us'],'guest_realtime_us':guest['unix_us'],
            'guest_sample_span_us':guest['sample_span_us'],
            'guest_monotonic_precision_ns':1000,'guest_realtime_precision_ns':1_000_000,
            'guest_boottime_available':False,
            'guest_to_host_monotonic_offset_bounds_ns':[
                host_before-(guest_ns+half_span),host_after-(guest_ns-half_span)],
            'offset_is_exact':False,
            'scope':'Cross-machine offset bounds from one guest sample bracketed by host send/receive. No path symmetry assumption; Perfetto guest ClockSnapshot still required for BOOTTIME mapping.'}


def distribution(values):
    values=sorted(x for x in values if math.isfinite(x))
    if not values:
        return {'count':0}
    def q(p):
        at=(len(values)-1)*p
        i=int(at)
        return round(values[i]+(values[min(i+1,len(values)-1)]-values[i])*(at-i),6)
    return {'count':len(values),'min_ms':round(values[0],6),'p50_ms':q(.5),
            'p95_ms':q(.95),'p99_ms':q(.99),'max_ms':round(values[-1],6),
            'negative_count':sum(x<0 for x in values),
            'over_50_ms':sum(x>50 for x in values),'over_100_ms':sum(x>100 for x in values)}


class FrameCollector:
    def __init__(self,max_frames=MAX_FRAME_RECORDS,max_invalid=MAX_INVALID_RECORDS):
        self.max_frames=max_frames
        self.max_invalid=max_invalid
        self.baseline_max=None
        self.baseline_rows=[]
        self.frames={}
        self.invalid={}
        self.dropped_frames=0
        self.dropped_invalid=0
        self.conflicting_desired=0

    def accept(self,parsed,poll_index):
        rows=parsed['rows']
        if self.baseline_max is None:
            self.baseline_rows=[dict(x) for x in rows]
            valid=[x['actual_present_ns'] for x in rows if x['actual_present_valid']]
            if valid:
                self.baseline_max=max(valid)
            return {'baseline_only':True,'new_frames':0,'fresh_invalid_records':0}
        new=invalid=0
        for row in rows:
            if row['actual_present_valid']:
                key=row['actual_present_ns']
                if key<=self.baseline_max:
                    continue
                current=self.frames.get(key)
                if current is None:
                    if len(self.frames)>=self.max_frames:
                        self.dropped_frames+=1
                        continue
                    self.frames[key]={**row,'first_poll_index':poll_index,'last_poll_index':poll_index,
                        'ready_invalid_seen':not row['frame_ready_valid'],'desired_conflict':False,
                        'ready_conflict':False}
                    new+=1
                else:
                    current['last_poll_index']=poll_index
                    current['ready_invalid_seen'] |= not row['frame_ready_valid']
                    if current['desired_present_ns']!=row['desired_present_ns']:
                        if not current['desired_conflict']:
                            self.conflicting_desired+=1
                        current['desired_conflict']=True
                    # Preserve initially missing ready time when it later resolves.
                    if row['frame_ready_valid']:
                        if current['frame_ready_valid'] and current['frame_ready_ns']!=row['frame_ready_ns']:
                            current['ready_conflict']=True
                        for name in ('frame_ready_ns','frame_ready_valid','frame_ready_invalid_reason'):
                            current[name]=row[name]
            else:
                key=tuple(row[x] for x in ('desired_present_ns','actual_present_ns','frame_ready_ns'))
                if key not in self.invalid:
                    if len(self.invalid)>=self.max_invalid:
                        self.dropped_invalid+=1
                        continue
                    self.invalid[key]={**row,'first_poll_index':poll_index,'last_poll_index':poll_index,
                                       'measurement_scope':'observed sentinel row; not counted as an actual presented frame'}
                    invalid+=1
                else:
                    self.invalid[key]['last_poll_index']=poll_index
        return {'baseline_only':False,'new_frames':new,'fresh_invalid_records':invalid}

    def report(self):
        frames=sorted(self.frames.values(),key=lambda x:x['actual_present_ns'])
        actual=[x['actual_present_ns'] for x in frames]
        ready=sorted(set(x['frame_ready_ns'] for x in frames if x['frame_ready_valid']))
        comparable=[x for x in frames if x['frame_ready_valid'] and not x['desired_conflict'] and not x['ready_conflict']]
        latency=[(x['actual_present_ns']-x['frame_ready_ns'])/1e6 for x in comparable]
        desired=[(x['actual_present_ns']-x['desired_present_ns'])/1e6 for x in frames
                 if x['desired_present_valid'] and not x['desired_conflict']]
        return {'baseline_actual_max_ns':self.baseline_max,'baseline_ring':self.baseline_rows,
                'frame_records':frames,'invalid_actual_rows':list(self.invalid.values()),
                'record_limits':{'frames':self.max_frames,'invalid_actual_rows':self.max_invalid},
                'dropped_metadata':{'frames':self.dropped_frames,'invalid_actual_rows':self.dropped_invalid},
                'summary':{'actual_unique_present_times':len(actual),
                    'ready_valid_frame_count':sum(x['frame_ready_valid'] for x in frames),
                    'ready_invalid_frame_count':sum(not x['frame_ready_valid'] for x in frames),
                    'ready_to_actual_comparable_count':len(comparable),
                    'ready_identity_conflict_count':sum(x['desired_conflict'] or x['ready_conflict'] for x in frames),
                    'conflicting_desired_present_records':self.conflicting_desired,
                    'actual_present_gap_ms':distribution([(b-a)/1e6 for a,b in zip(actual,actual[1:])]),
                    'ready_unique_gap_ms':distribution([(b-a)/1e6 for a,b in zip(ready,ready[1:])]),
                    'ready_to_actual_present_ms':distribution(latency),
                    'desired_to_actual_present_ms':distribution(desired)}}


class GuestClockSampler:
    def __init__(self,adb,serial,classpath):
        self.proc=None
        self.failure=None
        try:
            command=('CLASSPATH='+shlex.quote(classpath)+' app_process / '
                     'com.genymobile.scrcpy.util.StreamClock interactive')
            self.proc=subprocess.Popen([str(adb),'-s',serial,'shell','-T',command],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        except OSError:
            self.failure='guest_clock_process_unavailable'

    def sample(self):
        if self.proc is None or self.failure:
            return {'available':False,'failure_code':self.failure or 'guest_clock_unavailable'}
        before=host_clock_ns()
        try:
            self.proc.stdin.write(b'p');self.proc.stdin.flush()
            line=read_bounded_line(self.proc.stdout.fileno(),host_clock_ns()+2_000_000_000)
            after=host_clock_ns()
            if not line:
                raise ValueError('guest_clock_reply_size')
            result=clock_mapping_sample(json.loads(line),before,after)
            return {'available':True,**result}
        except (OSError,ValueError,TimeoutError):
            self.failure='guest_clock_query_failed'
            return {'available':False,'failure_code':self.failure,
                    'host_query_before_ns':before,'host_query_after_ns':host_clock_ns()}

    def close(self):
        if self.proc is None:
            return
        if self.proc.stdin:
            try:self.proc.stdin.close()
            except OSError:pass
        try:self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill();self.proc.wait(timeout=2)
        if self.proc.stdout:self.proc.stdout.close()


def read_bounded_line(fd,deadline_ns):
    """Do not allow a readable partial pipe line to bypass the query timeout."""
    result=bytearray()
    while host_clock_ns()<deadline_ns:
        remaining=max(0,(deadline_ns-host_clock_ns())/1e9)
        if not select.select([fd],[],[],remaining)[0]:
            raise TimeoutError()
        chunk=os.read(fd,min(256,2048-len(result)))
        if not chunk:
            raise ValueError('guest_clock_reply_eof')
        result.extend(chunk)
        if len(result)>=2048:
            raise ValueError('guest_clock_reply_size')
        newline=result.find(b'\n')
        if newline>=0:
            if newline!=len(result)-1:
                raise ValueError('unexpected_multiple_clock_replies')
            return bytes(result)
    raise TimeoutError()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial',default='emulator-5556')
    parser.add_argument('--package',default='app.morphe.android.youtube')
    parser.add_argument('--seconds',type=float,default=35)
    parser.add_argument('--poll-ms',type=int,default=500)
    parser.add_argument('--wait-layer',type=float,default=0)
    parser.add_argument('--guest-clock-classpath',default='/data/local/tmp/remoteandroid-host-control.jar',
        help='existing StreamClock jar in guest; never installed by this helper')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not math.isfinite(args.seconds) or not 5<=args.seconds<=90 or not 250<=args.poll_ms<=1000:
        parser.error('bounded duration5..90s and poll250..1000ms required')
    if not math.isfinite(args.wait_layer) or not 0<=args.wait_layer<=10:
        parser.error('wait-layer must be0..10s')
    if not re.fullmatch(r'emulator-\d+',args.serial) or not re.fullmatch(r'[A-Za-z][\w]*(?:\.[A-Za-z][\w]*)+',args.package):
        parser.error('requires local emulator selector and valid app package')
    if not args.guest_clock_classpath.startswith('/data/local/tmp/') or any(ord(x)<32 for x in args.guest_clock_classpath):
        parser.error('guest clock must use an existing /data/local/tmp classpath')
    if args.output.exists():
        parser.error('preserve existing evidence; choose a fresh output')
    adb=Path(os.environ.get('ANDROID_HOME',str(Path.home()/'Library/Android/sdk')))/'platform-tools/adb'

    def query(command):
        before=host_clock_ns()
        result=subprocess.run([str(adb),'-s',args.serial,'shell',command],capture_output=True,timeout=8,check=False)
        after=host_clock_ns()
        if result.returncode or len(result.stdout)>MAX_REPLY_BYTES:
            raise RuntimeError('bounded_adb_query_failed')
        return result.stdout.decode(errors='replace'),{'host_query_before_ns':before,'host_query_after_ns':after,
                'host_query_duration_ms':round((after-before)/1e6,6),'host_clock':HOST_CLOCK}

    report={'schema':1,'probe':'source_sf_frame_fences_readonly','timestamp_utc':datetime.now(timezone.utc).isoformat(),
            'scope':'Guest selected video SF frameReady/actual present numeric metadata, no capture/VT/phone/network performance claim',
            'package':args.package,'requested_seconds':args.seconds,'poll_ms':args.poll_ms,
            'fixed_layer_generation':True,'host_clock':HOST_CLOCK,'polls':[],'layer_checks':[],
            'guest_clock_queries':[],'host_wall_snapshots':[],'errors':[],
            'clock_offset_exactly_known':False,'limitations':[
                'Desired time is presentation scheduling metadata, not decoder media PTS.',
                'FrameReady may be absent/unsignaled; missing fences cannot prove decoder starvation.',
                'Ready→actual is same guest monotonic interval; cross-domain clocks require snapshots and bounded mappings.',
                'No direct subtraction of guestBOOTTIME,guestMONOTONIC,hostwall orhostMONOTONIC.',
                'SF ring polling can miss records and have repeated actual timestamps; no optical display or content uniqueness claim.',
                'Header period is retained per poll; not a continuous hardware-mode guarantee.',
            ]}
    collector=FrameCollector();clock=None;started=ended=host_clock_ns()
    try:
        wait_until=host_clock_ns()+int(args.wait_layer*1e9)
        raw,bracket=query('dumpsys SurfaceFlinger --list')
        layer=select_layer(raw,args.package)
        while layer is None and host_clock_ns()<wait_until:
            time.sleep(.5)
            raw,bracket=query('dumpsys SurfaceFlinger --list')
            layer=select_layer(raw,args.package)
        if layer is None:
            raise RuntimeError('selected_video_layer_missing')
        identity=layer_identity(layer);report['layer_identity']=identity
        report['layer_checks'].append({**bracket,'matches_initial':True,'identity_sha256':identity['sha256']})
        clock=GuestClockSampler(adb,args.serial,args.guest_clock_classpath)
        report['host_wall_snapshots'].append(wall_snapshot())
        for _ in range(3):report['guest_clock_queries'].append(clock.sample())
        started=host_clock_ns();next_clock=started+5_000_000_000;next_layer=started+2_000_000_000
        while host_clock_ns()-started<int(args.seconds*1e9):
            raw,bracket=query('dumpsys SurfaceFlinger --latency '+shlex.quote(layer))
            parsed=parse_latency(raw);index=len(report['polls'])
            result=collector.accept(parsed,index)
            report['polls'].append({**bracket,'poll_index':index,'seconds_from_measurement_start':round((bracket['host_query_after_ns']-started)/1e9,6),
                'display_vsync_ns':parsed['display_vsync_ns'],'ring_rows':len(parsed['rows']),
                'valid_actual_rows':sum(x['actual_present_valid'] for x in parsed['rows']),
                'valid_ready_rows':sum(x['frame_ready_valid'] for x in parsed['rows']),
                'malformed_rows':parsed['malformed_rows'],**result})
            now=host_clock_ns()
            if now>=next_layer:
                layer_raw,layer_bracket=query('dumpsys SurfaceFlinger --list')
                chosen=select_layer(layer_raw,args.package);same=chosen==layer
                report['layer_checks'].append({**layer_bracket,'matches_initial':same,
                    'selected_identity_sha256':layer_identity(chosen)['sha256'] if chosen else None})
                if not same:
                    raise RuntimeError('selected_video_layer_generation_changed')
                next_layer=now+2_000_000_000
            if now>=next_clock:
                report['guest_clock_queries'].append(clock.sample())
                report['host_wall_snapshots'].append(wall_snapshot());next_clock=now+5_000_000_000
            time.sleep(args.poll_ms/1000)
        ended=host_clock_ns()
        final_raw,final_bracket=query('dumpsys SurfaceFlinger --list')
        same=select_layer(final_raw,args.package)==layer
        report['layer_checks'].append({**final_bracket,'matches_initial':same})
        if not same:raise RuntimeError('selected_video_layer_generation_changed')
        report['status']='complete' if collector.baseline_max is not None else 'no_valid_actual_baseline'
    except (OSError,RuntimeError,ValueError,subprocess.SubprocessError) as error:
        ended=host_clock_ns();report['status']='partial_or_failed'
        report['errors'].append(str(error) if str(error) in ('bounded_adb_query_failed','selected_video_layer_missing',
            'selected_video_layer_generation_changed','latency_reply_size','layer_reply_size','layer_identity_invalid','latency_ring_size') else type(error).__name__)
    finally:
        if clock:
            for _ in range(3):report['guest_clock_queries'].append(clock.sample())
            clock.close()
        report['host_wall_snapshots'].append(wall_snapshot())
        report.update(collector.report())
        report['measurement_host_before_ns']=started;report['measurement_host_after_ns']=ended
        report['measurement_seconds']=round(max(0,ended-started)/1e9,6)
        report['complete_fixed_generation_verified']=report.get('status')=='complete' and all(x['matches_initial'] for x in report['layer_checks'])
        report['guest_clock_available']=any(x.get('available') for x in report['guest_clock_queries'])
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with args.output.open('x') as output:json.dump(report,output,ensure_ascii=False,indent=2);output.write('\n')
    print(json.dumps({'output':str(args.output),'status':report['status'],'actual_present_times':report['summary']['actual_unique_present_times'],
        'ready_valid':report['summary']['ready_valid_frame_count'],'guest_clock_available':report['guest_clock_available'],
        'complete_fixed_generation_verified':report['complete_fixed_generation_verified']},ensure_ascii=False))
    return 0 if report['status']=='complete' else 2


if __name__=='__main__':
    raise SystemExit(main())
