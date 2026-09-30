#!/usr/bin/env python3
"""Collect autonomous, real installed-app playback metrics via rooted USB ADB."""
import argparse
import json
import os
import shlex
import statistics
import subprocess
import ipaddress
from datetime import datetime, timezone
from pathlib import Path

def percentile(values, q):
    if not values:return None
    a=sorted(values);n=(len(a)-1)*q;i=int(n);j=min(i+1,len(a)-1)
    return round(a[i]+(a[j]-a[i])*(n-i),2)

def main():
    p=argparse.ArgumentParser()
    p.add_argument('transport',choices=('tcp','quic','kcp','tailscale','lan'))
    p.add_argument('--host',required=True);p.add_argument('--serial',default='3B15AL00M9U00000')
    p.add_argument('--seconds',type=int,default=45);p.add_argument('--fps',type=int,default=60)
    p.add_argument('--bitrate',type=int,default=4000000);p.add_argument('--buffer',type=int,default=80)
    p.add_argument('--mode',default='ADAPTIVE_VBR');p.add_argument('--source',required=True)
    p.add_argument('--credential-file',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if not 30<=a.buffer<=80:raise SystemExit('Buffer must respect the 80 ms user limit')
    if a.credential_file.stat().st_mode&0o077:raise SystemExit('Credential file must be mode 600')
    credential=a.credential_file.read_bytes().strip()
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
        if redirect:root(shlex.join(['iptables','-t','nat','-A',*redirect]))
        root('umask 077; cat > '+remote+'; chown '+uid+':'+uid+' '+remote+'; chmod 600 '+remote+'; restorecon '+remote,input=credential)
        credential=None
        args=['am','instrument','-w','-e','host',host,'-e','mode',a.mode,'-e','max_size','1200',
              '-e','bit_rate',str(a.bitrate),'-e','seconds',str(a.seconds),'-e','fps',str(a.fps),
              '-e','buffer_ms',str(a.buffer),'local.remoteandroid.phoneprobe/local.remoteandroid.direct.PhoneProbe']
        result=root(shlex.join(args),timeout=a.seconds+100)
        report=None
        for line in result.stdout.decode(errors='replace').splitlines():
            if line.startswith('INSTRUMENTATION_RESULT: report='):report=json.loads(line.split('report=',1)[1])
        if report is None:
            # Instrumentation output has no credentials; avoid unrelated logcat or private UI dumps.
            raise RuntimeError(result.stdout.decode(errors='replace')[:2000])
        report.update(transport=a.transport,endpoint=a.host,source=a.source,timestamp_utc=datetime.now(timezone.utc).isoformat())
        samples=report['samples'][3:]
        summary={k:report[k] for k in ('transport','host','source','mode','requested_bps','decoder','hardware','size','fps_limit','buffer_ms','late_total','running_at_end','adaptive_rejected')}
        for name in ('rx_fps','render_fps','late_fps','rtt_ms','video_mbps','decoder_hold_ms','audio_queued_ms'):
            values=[s[name] for s in samples if name in s and s[name]>=0]
            if values:summary[name]={'mean':round(statistics.mean(values),2),'p10':percentile(values,.1),'p95':percentile(values,.95),'min':round(min(values),2),'max':round(max(values),2)}
        gaps=report['render_gaps_ms']
        summary['render_gaps_ms']={'p95':percentile(gaps,.95),'p99':percentile(gaps,.99),'max':max(gaps) if gaps else None,'over_100ms':sum(x>100 for x in gaps)}
        a.output.parent.mkdir(parents=True,exist_ok=True)
        a.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        a.output.with_name(a.output.stem+'-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(summary,ensure_ascii=False))
    finally:
        root('rm -f '+remote)
        if redirect:root(shlex.join(['iptables','-t','nat','-D',*redirect]))

if __name__=='__main__':main()
