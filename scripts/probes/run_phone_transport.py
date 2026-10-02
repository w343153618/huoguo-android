#!/usr/bin/env python3
"""Collect autonomous, real installed-app playback metrics via rooted USB ADB."""
import argparse
import json
import os
import shlex
import statistics
import subprocess
import ipaddress
import math
from datetime import datetime, timezone
from pathlib import Path

REPORT_BASENAME='transport-test-report.json'
REPORT_REMOTE='/data/user/0/local.remoteandroid.direct/files/'+REPORT_BASENAME

def read_instrumentation_report(stdout,read_private_report):
    """Accept an old inline result or this probe's one fixed private-report name."""
    inline=None;report_file=None;report_bytes=None
    for line in stdout.decode(errors='replace').splitlines():
        if line.startswith('INSTRUMENTATION_RESULT: report='):
            inline=json.loads(line.split('report=',1)[1])
        elif line.startswith('INSTRUMENTATION_RESULT: report_file='):
            report_file=line.split('report_file=',1)[1].strip()
        elif line.startswith('INSTRUMENTATION_RESULT: report_bytes='):
            report_bytes=line.split('report_bytes=',1)[1].strip()
    if report_file is not None:
        if report_file!=REPORT_BASENAME:raise ValueError('Unexpected instrumentation report filename')
        if report_bytes is None or not report_bytes.isdecimal():raise ValueError('Missing report byte count')
        expected=int(report_bytes)
        if not 0<expected<=64*1024*1024:raise ValueError('Invalid report byte count')
        # The callback receives no remote path; it can read only REPORT_REMOTE.
        payload=read_private_report()
        if len(payload)!=expected:raise ValueError('Private report byte count mismatch')
        report=json.loads(payload.decode('utf-8'))
    elif inline is not None:
        report=inline
    else:
        raise RuntimeError(stdout.decode(errors='replace')[:2000])
    if not isinstance(report,dict):raise ValueError('Instrumentation report must be an object')
    return report

def percentile(values, q):
    if not values:return None
    a=sorted(values);n=(len(a)-1)*q;i=int(n);j=min(i+1,len(a)-1)
    return round(a[i]+(a[j]-a[i])*(n-i),2)

def codec_timestamp_validity(report):
    """Validate raw vendor observations; a requested target is not display proof."""
    frames=report.get('presentation_frames',[])
    total=future=before_release=invalid=matched=echoes=0
    for frame in frames:
        vendor=frame.get('vendor_render_ns',frame.get('surface_ns'))
        callback=frame.get('callback_ns');released=frame.get('released_ns');target=frame.get('scheduled_ns')
        total+=1
        times_valid=isinstance(vendor,int) and isinstance(callback,int) and vendor>0 and callback>0
        is_future=times_valid and vendor>callback
        is_before=times_valid and isinstance(released,int) and vendor<released
        future+=bool(is_future);before_release+=bool(is_before)
        invalid+=not times_valid or is_future or is_before
        if isinstance(target,int) and isinstance(vendor,int):
            matched+=1;echoes+=vendor==target
    echo_suspected=matched>=30 and echoes/matched>=0.95
    usable=total>0 and invalid==0 and not echo_suspected
    status='invalid_vendor_timestamp' if invalid else ('unverified_requested_target_echo' if echo_suspected
           else 'causally_plausible_vendor_timestamp_unverified_display' if total else 'missing_vendor_timing_records')
    return dict(status=status,callback_records=total,invalid_causal_timestamps=invalid,future_timestamps=future,
        before_release_timestamps=before_release,matched_requested_targets=matched,exact_requested_target_echoes=echoes,
        requested_target_echo_suspected=echo_suspected,usable_for_presentation_timestamp_estimate=usable,
        independent_display_presentation_measured=False,derived_from_raw_frames=True)

def distribution(values):
    values=[value for value in values if isinstance(value,(int,float)) and math.isfinite(value)]
    if not values:return None
    return {'count':len(values),'mean':round(statistics.mean(values),2),'p50':percentile(values,.5),
            'p95':percentile(values,.95),'p99':percentile(values,.99),'min':round(min(values),2),'max':round(max(values),2)}

def summarize(report):
    """Summarize observations; keep missing/legacy measurements distinguishable."""
    samples=report['samples'][3:]
    summary={k:report[k] for k in ('transport','host','source','mode','requested_bps','decoder','hardware','size','fps_limit','buffer_ms','late_total','running_at_end','adaptive_rejected')}
    validity=codec_timestamp_validity(report);vendor_observations={}
    summary['codec_timestamp_validity']=validity
    summary['actual_display_fps_measured']=False
    summary['actual_audio_video_skew_measured']=False
    for name in ('video_release_mode','requested_audio_buffer_frames','actual_audio_buffer_frames','audio_buffer_frames_observed'):
        if name in report:summary[name]=report[name]
    for name in ('rx_fps','render_fps','late_fps','rtt_ms','video_mbps','decoder_hold_ms',
                 'audio_queued_ms','audio_queued_estimate_ms','audio_timestamp_age_ms'):
        values=[s[name] for s in samples if isinstance(s.get(name),(int,float))
                and math.isfinite(s[name]) and (name=='audio_timestamp_age_ms' or s[name]>=0)]
        if values:summary[name]={'mean':round(statistics.mean(values),2),'p10':percentile(values,.1),'p95':percentile(values,.95),'min':round(min(values),2),'max':round(max(values),2)}
    underruns=[s['audio_underrun_count'] for s in report['samples'] if 'audio_underrun_count' in s]
    if underruns:summary['audio_underrun_observations']={'first':underruns[0],'last':underruns[-1],
                                                      'increase':max(0,underruns[-1]-underruns[0])}
    gaps=report['render_gaps_ms']
    raw_gaps={'p95':percentile(gaps,.95),'p99':percentile(gaps,.99),'max':max(gaps) if gaps else None,'over_100ms':sum(x>100 for x in gaps)}
    vendor_observations['render_gaps_ms']=raw_gaps
    summary['render_gaps_ms']={'measurement_valid':validity['usable_for_presentation_timestamp_estimate'],
        'physical_display_cadence_measured':False,'source':'unverified_vendor_codec_timestamp',
        'raw_vendor_statistics':raw_gaps}
    for name in ('surface_render_vs_scheduled_ms','surface_callback_delay_ms','decoder_ready_to_surface_ms',
                 'receive_to_decoder_ready_ms','receive_to_input_queue_ms','input_queue_to_decoder_ready_ms',
                 'release_to_surface_ms','audio_minus_video_pts_estimate_ms',
                 'decoder_ready_to_callback_receipt_ms','requested_target_lead_ms','codec_callback_receipt_gaps_ms'):
        stats=distribution(report.get(name,[]))
        if not stats:continue
        if name in ('surface_render_vs_scheduled_ms','surface_callback_delay_ms','decoder_ready_to_surface_ms',
                    'release_to_surface_ms','audio_minus_video_pts_estimate_ms'):
            vendor_observations[name]=stats
            if validity['usable_for_presentation_timestamp_estimate']:summary[name]=stats
        else:summary[name]=stats
    summary['vendor_callback_observations']=vendor_observations
    if 'render_fps' in summary:
        summary['codec_callback_fps']=dict(summary['render_fps'])
        summary['render_fps']['source']='legacy_codec_callback_count_not_display_fps'
    summary['presentation_hooks_available']=report.get('presentation_hooks_available',False)
    summary['metric_definitions']=dict(report.get('metric_definitions',{}))
    summary['metric_definitions'].update(audio_queued_ms='Legacy written minus timestamp position; includes timestamp age; NOT remaining queue',
        render_fps='Codec callback count; NOT independently measured display FPS',
        surface_ns='Unverified vendor codec timestamp, sometimes requested-target echo; NOT actual display proof')
    if 'av_limitation' in report:summary['av_limitation']=report['av_limitation']
    if not validity['usable_for_presentation_timestamp_estimate']:
        summary['av_limitation']='Vendor video timestamps are invalid or unverified; actual display FPS, presentation error and A/V skew were not measured.'
    return summary

def main():
    p=argparse.ArgumentParser()
    p.add_argument('transport',choices=('tcp','quic','kcp','tailscale','lan'))
    p.add_argument('--host',required=True);p.add_argument('--serial',default='3B15AL00M9U00000')
    p.add_argument('--seconds',type=int,default=45);p.add_argument('--fps',type=int,default=60)
    p.add_argument('--bitrate',type=int,default=4000000);p.add_argument('--buffer',type=int,default=80)
    p.add_argument('--mode',default='ADAPTIVE_VBR');p.add_argument('--source',required=True)
    p.add_argument('--av-sync',type=int,default=0)
    p.add_argument('--video-release',choices=('scheduled','immediate'),default='scheduled')
    p.add_argument('--audio-buffer-frames',type=int,choices=(0,2048,3072,4096),default=0)
    p.add_argument('--credential-file',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if not 30<=a.buffer<=80:raise SystemExit('Buffer must respect the 80 ms user limit')
    if a.credential_file and a.credential_file.stat().st_mode&0o077:raise SystemExit('Credential file must be mode 600')
    credential=a.credential_file.read_bytes().strip() if a.credential_file else None
    adb=[str(Path(os.environ.get('ANDROID_HOME',str(Path.home()/'Library/Android/sdk')))/'platform-tools/adb'),'-s',a.serial]
    remote='/data/user/0/local.remoteandroid.direct/files/transport-test-credential'
    def root(command,**kw):
        return subprocess.run(adb+['shell','su -c '+shlex.quote(command)],check=True,capture_output=True,**kw)
    uid=root('stat -c %u /data/user/0/local.remoteandroid.direct').stdout.decode().strip()
    if not uid.isdigit():raise SystemExit('Cannot determine target application owner')
    host=a.host
    redirect=None
    if ':' in host:
        host,port=host.rsplit(':',1)
        ipaddress.IPv4Address(host)
        port=int(port)
        if not 1<=port<=65535:raise SystemExit('Invalid test port')
        if port!=15556:
            # The installed v1.21 client uses fixed port 15556. Redirect only this
            # app UID and destination during the isolated test; preserve all other traffic.
            redirect=['OUTPUT','-p','tcp','-d',host,'--dport','15556','-m','owner',
                      '--uid-owner',uid,'-m','comment','--comment','huoguo-transport-test',
                      '-j','DNAT','--to-destination',host+':'+str(port)]
    try:
        root('rm -f '+REPORT_REMOTE)
        if redirect:root(shlex.join(['iptables','-t','nat','-A',*redirect]))
        if credential is not None:root('umask 077; cat > '+remote+'; chown '+uid+':'+uid+' '+remote+'; chmod 600 '+remote+'; restorecon '+remote,input=credential)
        credential=None
        args=['am','instrument','-w','-e','host',host,'-e','mode',a.mode,'-e','max_size','1200',
              '-e','bit_rate',str(a.bitrate),'-e','seconds',str(a.seconds),'-e','fps',str(a.fps),
              '-e','video_release',a.video_release,'-e','audio_buffer_frames',str(a.audio_buffer_frames),
              '-e','av_sync_ms',str(a.av_sync),'-e','buffer_ms',str(a.buffer),'local.remoteandroid.phoneprobe/local.remoteandroid.direct.PhoneProbe']
        result=root(shlex.join(args),timeout=a.seconds+100)
        report=read_instrumentation_report(result.stdout,lambda:root('cat '+REPORT_REMOTE).stdout)
        report.update(transport=a.transport,endpoint=a.host,source=a.source,timestamp_utc=datetime.now(timezone.utc).isoformat())
        summary=summarize(report)
        a.output.parent.mkdir(parents=True,exist_ok=True)
        a.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        a.output.with_name(a.output.stem+'-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(summary,ensure_ascii=False))
    finally:
        root('rm -f '+remote+' '+REPORT_REMOTE)
        if redirect:root(shlex.join(['iptables','-t','nat','-D',*redirect]))

if __name__=='__main__':main()
