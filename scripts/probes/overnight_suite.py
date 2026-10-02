#!/usr/bin/env python3
"""Bounded M1 / user-limited OnePlus12 experiments; never writes CPU policies.

Evidence-only runner, no publication. Keeps failed runs; sequential device owner.
External runtime dependencies are read from explicit local paths below.
"""
import argparse,datetime,hashlib,json,os,re,shlex,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
OUT=ROOT/'docs/evidence/overnight-20261002'
ADB=Path.home()/'Library/Android/sdk/platform-tools/adb'
PHONE='f7fc9469'; GUEST='emulator-5556'
ENCODER=Path('/private/tmp/huoguo-session-pool-encoder')
PACKETIZER=Path('/private/tmp/huoguo-udp-paced-credit/host/h264_udp_packetizer')
def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def command(argv,timeout=15):
 r=subprocess.run([str(x) for x in argv],capture_output=True,text=True,timeout=timeout)
 if r.returncode:raise RuntimeError('scoped_command_failed_'+str(r.returncode))
 return r.stdout.strip()
def shell(serial,words,timeout=12):return command([ADB,'-s',serial,'shell',words],timeout)
def save(path,data):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
def preflight():
 report={'utc':utc(),'scope':'Explicit read-only live preflight; CPU policies never written','devices':{}}
 for serial in (GUEST,PHONE):
  d={}
  for name,cmd in [('sdk','getprop ro.build.version.sdk'),('release','getprop ro.build.version.release'),('size','wm size'),('density','wm density'),('min_hz','settings get system min_refresh_rate'),('peak_hz','settings get system peak_refresh_rate')]:d[name]=shell(serial,cmd)
  text=shell(serial,'dumpsys display')
  d['active_mode_ids']=[int(x) for x in re.findall(r'mActiveModeId=(\d+)',text)]
  d['active_refresh_hz']=[float(x) for x in re.findall(r'renderFrameRate ([0-9.]+)',text)]
  if serial==PHONE:
   text=shell(serial,'ip -4 addr show wlan0');d['wifi_ipv4']=re.findall(r'inet ([0-9.]+)/',text)
   d['experiment_installed']=bool(shell(serial,'pm path local.remoteandroid.direct.experiment'))
   cpu='for p in 0 2 5 7; do cat /sys/devices/system/cpu/cpufreq/policy$p/scaling_max_freq /sys/devices/system/cpu/cpufreq/policy$p/scaling_cur_freq; done'
   vals=[int(x) for x in shell(serial,'su -c '+shlex.quote(cpu)).split()]
   d['cpu_policies']={f'policy{p}':{'max_khz':vals[i*2],'cur_khz':vals[i*2+1]} for i,p in enumerate((0,2,5,7))} if len(vals)==8 else {'unavailable':True}
  report['devices'][serial]=d
 report['source_package_installed']=bool(shell(GUEST,'pm path app.morphe.android.youtube'))
 report['host_ipv4']=command(['ipconfig','getifaddr','en7'])
 report['binaries']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (ENCODER,PACKETIZER)}
 save(OUT/'preflight-live.json',report);print(json.dumps(report,ensure_ascii=False),flush=True)
 return report

def real_run(label,*,fps=60,size=1280,bitrate=8000000,buffer=80,seconds=35,restart=True,rounds=1,raw_submit_fps=None,drop_video_every=0,exercise_touch=False,disable_socket_bundle=False):
 dest=OUT/label
 if dest.exists():raise RuntimeError('Keep existing evidence; choose new label')
 p=json.loads((OUT/'preflight-live.json').read_text());peer=p['devices'][PHONE]['wifi_ipv4'][0];bind=p['host_ipv4']
 cmd=[sys.executable,ROOT/'scripts/probes/run_surface_hint_matrix.py','--experimental-client','--serial',PHONE,'--phone-label','OnePlus12 user CPU limited overnight','--bind-ip',bind,'--peer-ip',peer,'--interface','en7','--fps',str(fps),'--display-hz','120','--hints','120','--buffers',str(buffer),'--raw-policies','fifo','--surface-submit-leads','0','--packetizer',PACKETIZER,'--encoder',ENCODER,'--max-size',str(size),'--video-bitrate',str(bitrate),'--wire-bitrate','32000000','--rounds',str(rounds),'--seconds',str(seconds),'--source-warmup-seconds','15','--source-quality-label','unverified','--output-dir',dest]
 if restart:cmd.append('--restart-source')
 if raw_submit_fps is not None:cmd.extend(['--raw-submit-fps',str(raw_submit_fps)])
 if drop_video_every:cmd.extend(['--drop-video-every',str(drop_video_every)])
 if exercise_touch:cmd.append('--exercise-touch')
 if disable_socket_bundle:cmd.append('--disable-socket-pacing-bundle')
 row={'utc_start':utc(),'requested':{'fps_cap':fps,'raw_submit_fps':raw_submit_fps,'size':size,'bitrate':bitrate,'buffer':buffer,'seconds':seconds,'drop_video_every':drop_video_every,'exercise_touch':exercise_touch,'disable_socket_pacing_and_guard_bundle':disable_socket_bundle},'command':[str(x) for x in cmd]}
 r=subprocess.run([str(x) for x in cmd],cwd=ROOT,capture_output=True,text=True,timeout=rounds*(seconds+100))
 row.update(exit_code=r.returncode,utc_end=utc())
 if (dest/'matrix.json').is_file():
  m=json.loads((dest/'matrix.json').read_text());row['runs']=[{'report':x.get('report'), 'label':x.get('label'),'valid_real_video_test':x.get('valid_real_video_test'),'playback_validation':x.get('playback_validation'),'real_source_validation':x.get('real_source_validation')} for x in m.get('runs',[])]
 save(OUT/(label+'-invocation.json'),row)
 print(json.dumps({'completed':label,'exit_code':r.returncode,'runs':row.get('runs')}),flush=True)
 return r.returncode

def codec_matrix(patched=False):
 probe=ROOT/'experiments/nps-transport/phone/build/experimental/phoneprobe.apk'
 fixture=Path('/private/tmp/huoguo-overnight-720-60'+('-vui' if patched else '')+'.framed')
 prefix='codec720-vui' if patched else 'codec720'
 if not fixture.is_file():raise RuntimeError('Private controlled synthetic fixture missing')
 remote='/data/local/tmp/huoguo-overnight-codec-probe.apk'
 command([ADB,'-s',PHONE,'push',probe,remote],30)
 try:shell(PHONE,'su -c '+shlex.quote('pm install -r '+remote),30)
 finally:shell(PHONE,'su -c '+shlex.quote('rm -f '+remote))
 remote='/data/local/tmp/huoguo-overnight-fixed.framed'
 command([ADB,'-s',PHONE,'push',fixture,remote],30)
 dest='/data/user/0/local.remoteandroid.direct.experiment/files/codec-test.h264framed'
 owner=shell(PHONE,'su -c '+shlex.quote('stat -c %u:%g /data/user/0/local.remoteandroid.direct.experiment/files'))
 if not re.fullmatch(r'[0-9]+:[0-9]+',owner):raise RuntimeError('Experiment app UID readback invalid')
 shell(PHONE,'su -c '+shlex.quote('cp '+remote+' '+dest+' && chown '+owner+' '+dest+' && chmod 600 '+dest+' && rm -f '+remote),15)
 records=[]
 for i,(release,buffer) in enumerate([('scheduled',80),('immediate',80),('immediate',80),('scheduled',80),('scheduled',80),('scheduled',100),('scheduled',100),('scheduled',80)],1):
  output=OUT/f'{prefix}-{i:02}-{release}-{buffer}.json'
  if output.exists():raise RuntimeError('Preserve previous codec evidence')
  sf=output.with_name(output.stem+'-surface.json')
  # Independent SF observer waits for the probe Surface; neither callbacks nor
  # its full 22s wall duration are used as display throughput.
  observer=subprocess.Popen([sys.executable,ROOT/'scripts/probes/measure_surface_cadence.py','--serial',PHONE,'--package','local.remoteandroid.direct.experiment','--seconds','22','--wait-layer','8','--output',sf],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  cmd=[sys.executable,ROOT/'scripts/probes/run_codec_file_probe.py','--serial',PHONE,'--experimental-client','--fps','120','--display-hz','120','--buffer',str(buffer),'--arrival-clock','--video-release',release,'--profile',f'same720-60fps-fixture-{release}-{buffer}','--output',output]
  r=subprocess.run([str(x) for x in cmd],cwd=ROOT,capture_output=True,text=True,timeout=70)
  try:observer.wait(timeout=35)
  except subprocess.TimeoutExpired:observer.terminate();observer.wait(timeout=5)
  row={'index':i,'release':release,'buffer_ms':buffer,'exit_code':r.returncode,'surface_probe_exit_code':observer.returncode,'fixture_sha256':hashlib.sha256(fixture.read_bytes()).hexdigest(),'probe_sha256':hashlib.sha256(probe.read_bytes()).hexdigest()};records.append(row)
  save(OUT/(prefix+'-matrix.json'),{'scope':'Synthetic fixed file capacity isolation; no streaming network or audio; all timing same phone clock','runs':records})
  print(json.dumps({'codec_complete':row}),flush=True)
  if r.returncode:break

def network_readback():
 report={'utc':utc(),'scope':'Local route metadata only, no STUN/netcheck/join or credential access','media_udp_tailnet_test_completed':False}
 r=subprocess.run(['/opt/homebrew/bin/tailscale','status','--json'],capture_output=True,text=True,timeout=15)
 if r.returncode==0:
  d=json.loads(r.stdout);report['host_backend_state']=d.get('BackendState');report['host_tailnet_ipv4']=[x for x in d.get('TailscaleIPs',[]) if '.' in x]
  report['peer_ipv4']=[{'ip':[x for x in p.get('TailscaleIPs',[]) if '.' in x],'online':p.get('Online'),'active':p.get('Active'),'relay':p.get('Relay'),'direct_endpoint_observed':bool(p.get('CurAddr'))} for p in d.get('Peer',{}).values() if any(x.startswith('100.') for x in p.get('TailscaleIPs',[]))]
 else:report['host_backend_query_failed']=True
 report['phone_tailscale_installed']=bool(shell(PHONE,'pm path com.tailscale.ipn'))
 text=shell(PHONE,'ip -4 addr show')
 report['phone_tailnet_ipv4']=re.findall(r'inet (100\.[0-9.]+)/',text)
 if not report['phone_tailnet_ipv4']:report['tailnet_media_probe_blocker']='Current substitute phone has no active tailnet IPv4; existing tests use explicit physical LAN sockets'
 save(OUT/'network-readback.json',report);print(json.dumps(report),flush=True)

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stage',choices=('preflight','cap-matrix','raw-budget-matrix','bitrate-matrix','pacing-matrix','resolution-matrix','buffer-real-matrix','long-matrix','followup-suite','real-single','codec-matrix','codec-vui-matrix','network-readback'),required=True);p.add_argument('--label');p.add_argument('--fps',type=int,choices=(60,120),default=60);p.add_argument('--size',type=int,choices=(960,1280,1920),default=1280);p.add_argument('--bitrate',type=int,choices=(4000000,8000000,12000000,16000000),default=8000000);p.add_argument('--buffer',type=int,choices=(60,80,100),default=80);p.add_argument('--seconds',type=int,choices=(35,45,60,120),default=35)
 args=p.parse_args();OUT.mkdir(exist_ok=True)
 if args.stage=='preflight':preflight();return
 if args.stage=='codec-matrix':codec_matrix();return
 if args.stage=='codec-vui-matrix':codec_matrix(True);return
 if args.stage=='network-readback':network_readback();return
 if args.stage=='real-single':
  if not args.label:p.error('--label required')
  raise SystemExit(real_run(args.label,fps=args.fps,size=args.size,bitrate=args.bitrate,buffer=args.buffer,seconds=args.seconds))
 if args.stage=='raw-budget-matrix':
  for i,budget in enumerate((60,120,120,60),1):real_run(f'budget-{i:02}-{budget}',fps=120,raw_submit_fps=budget)
  return
 if args.stage=='bitrate-matrix':
  for i,rate in enumerate((4000000,8000000,12000000,12000000,8000000,4000000),1):real_run(f'bitrate-{i:02}-{rate//1000000}M',fps=120,bitrate=rate,raw_submit_fps=120)
  return
 if args.stage=='pacing-matrix':
  for i,disabled in enumerate((False,True,True,False),1):real_run(f'pacing-{i:02}-'+('nativeonly' if disabled else 'double'),fps=120,raw_submit_fps=120,disable_socket_bundle=disabled)
  return
 if args.stage=='followup-suite':
  for i,(size,rate) in enumerate(((960,4000000),(1280,8000000),(1920,12000000),(1920,12000000),(1280,8000000),(960,4000000)),1):real_run(f'resolution-{i:02}-{size}-{rate//1000000}M',fps=120,size=size,bitrate=rate,raw_submit_fps=120)
  for i,buffer in enumerate((80,100,100,80),1):real_run(f'buffer-real-{i:02}-{buffer}',fps=120,buffer=buffer,raw_submit_fps=120)
  for i,buffer in enumerate((80,100),1):real_run(f'long-{i:02}-{buffer}',fps=120,buffer=buffer,seconds=120,raw_submit_fps=120)
  for i,loss in enumerate((100,50,0),1):real_run(f'loss-{i:02}-{loss}',fps=120,buffer=80,raw_submit_fps=120,drop_video_every=loss,exercise_touch=(loss==0))
  return
 if args.stage=='resolution-matrix':
  for i,(size,rate) in enumerate(((960,4000000),(1280,8000000),(1920,12000000),(1920,12000000),(1280,8000000),(960,4000000)),1):real_run(f'resolution-{i:02}-{size}-{rate//1000000}M',fps=120,size=size,bitrate=rate,raw_submit_fps=120)
  return
 if args.stage=='buffer-real-matrix':
  for i,buffer in enumerate((80,100,100,80),1):real_run(f'buffer-real-{i:02}-{buffer}',fps=120,buffer=buffer,raw_submit_fps=120)
  return
 if args.stage=='long-matrix':
  for i,buffer in enumerate((80,100),1):real_run(f'long-{i:02}-{buffer}',fps=120,buffer=buffer,seconds=120,raw_submit_fps=120)
  return
 for i,fps in enumerate((60,120,120,60),1):
  code=real_run(f'cap-{i:02}-{fps}',fps=fps)
  if code:print(json.dumps({'failed_window_retained':i}),flush=True)
if __name__=='__main__':main()
