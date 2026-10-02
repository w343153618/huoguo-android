#!/usr/bin/env python3
"""Read-only bounded final health; only cleans this suite's orphan testsrc process."""
import datetime,hashlib,json,os,plistlib,re,shlex,signal,ssl,subprocess,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'docs/evidence/overnight-20261002/final-health.json'
ADB=Path.home()/'Library/Android/sdk/platform-tools/adb'
def run(cmd,timeout=12):
    r=subprocess.run([str(x) for x in cmd],capture_output=True,text=True,timeout=timeout)
    if r.returncode:raise RuntimeError('readback_exit_'+str(r.returncode))
    return r.stdout.strip()
def shell(serial,cmd):return run([ADB,'-s',serial,'shell',cmd])
def http(url):
    try:
        with urllib.request.urlopen(url,timeout=5,context=ssl._create_unverified_context() if url.startswith('https://127.0.0.1') else None) as r:
            body=r.read(512)
            d={'http_status':r.status}
            if url.endswith('/ping'):
                p=json.loads(body);d['guest_serial']=p.get('serial');d['video_backend']=p.get('video_backend')
            return d
    except Exception as e:return {'available':False,'failure_type':type(e).__name__}
def main():
    if OUT.exists():raise RuntimeError('Preserve completed readback')
    d={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scope':'Final local health/setting metadata only; no auth or cloud mutations','phone_cpu_policy_written':False,'m5_changed':False,'formal_app_udp_published':False,'devices':{}}
    ps=run(['ps','-axo','pid=,comm='])
    cleanup=[];capture=[]
    for line in ps.splitlines():
        parts=line.strip().split(None,1)
        if len(parts)!=2:continue
        pid=int(parts[0]);comm=Path(parts[1]).name
        if comm=='ffmpeg':
            argv=run(['ps','-p',str(pid),'-o','command='])
            if 'testsrc2=size=720x1280:rate=60' in argv and '-frames:v 120' in argv and '-f rawvideo pipe:1' in argv:
                os.kill(pid,signal.SIGTERM);cleanup.append({'pid':pid,'kind':'own_suite_synthetic_fixture_generator','signal':'SIGTERM'})
        if comm in ('huoguo-session-pool-encoder','h264_udp_packetizer'):capture.append({'pid':pid,'kind':comm})
    d['own_orphan_cleanup']=cleanup;d['active_experimental_capture_processes']=capture
    for serial in ('emulator-5556','f7fc9469'):
        row={}
        for k,cmd in {'sdk':'getprop ro.build.version.sdk','release':'getprop ro.build.version.release','size':'wm size','density':'wm density','renderer':'getprop debug.hwui.renderer','min_refresh':'settings get system min_refresh_rate','peak_refresh':'settings get system peak_refresh_rate','stay_on_while_plugged':'settings get global stay_on_while_plugged_in','screen_off_timeout':'settings get system screen_off_timeout'}.items():row[k]=shell(serial,cmd)
        display=shell(serial,'dumpsys display');row['mode_ids']=[int(x) for x in re.findall(r'mActiveModeId=(\d+)',display)];row['refresh_hz']=[float(x) for x in re.findall(r'renderFrameRate ([0-9.]+)',display)]
        power=shell(serial,'dumpsys power');row['wakefulness']=re.findall(r'mWakefulness=([A-Za-z]+)',power)
        if serial=='emulator-5556':
            row['online_cpus']=shell(serial,'cat /sys/devices/system/cpu/online')
            mem=shell(serial,'cat /proc/meminfo');row['mem_total_kib']=int(re.search(r'MemTotal:\s+([0-9]+)',mem)[1])
        else:
            vals=[int(x) for x in shell(serial,'su -c '+shlex.quote('for p in 0 2 5 7; do cat /sys/devices/system/cpu/cpufreq/policy$p/scaling_max_freq /sys/devices/system/cpu/cpufreq/policy$p/scaling_cur_freq; done')).split()]
            row['cpu_policies']={str(p):{'max_khz':vals[i*2],'current_khz':vals[i*2+1]} for i,p in enumerate((0,2,5,7))}
            text=shell(serial,'ip -4 addr show wlan0');row['wifi_ipv4']=re.findall(r'inet ([0-9.]+)/',text)
            row['experimental_packages_installed']={pkg:bool(shell(serial,'pm path '+pkg)) for pkg in ('local.remoteandroid.direct.experiment','local.remoteandroid.phoneprobe.experiment')}
        d['devices'][serial]=row
    path=Path.home()/'Library/LaunchAgents/local.remoteandroid.m1compare.emulator.plist'
    p=plistlib.loads(path.read_bytes());d['emulator_launchagent']={'process_type':p.get('ProcessType'),'plist_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'private_full_config_not_exported':True}
    avd_index=Path.home()/'.android/avd/RemoteAndroid17Compare.ini'
    index={k.strip():v.strip() for k,v in (line.split('=',1) for line in avd_index.read_text().splitlines() if '=' in line)}
    avd=Path(index['path'])/'config.ini'
    config={k.strip():v.strip() for k,v in (line.split('=',1) for line in avd.read_text().splitlines() if '=' in line)}
    d['avd_config']={k:config.get(k) for k in ('hw.cpu.ncore','hw.ramSize','hw.lcd.width','hw.lcd.height','hw.lcd.density','hw.lcd.vsync','hw.gpu.enabled','hw.gpu.mode')}
    d['avd_config']['sha256']=hashlib.sha256(avd.read_bytes()).hexdigest()
    d['services']={'gateway':http('https://127.0.0.1:15556/ping'),'download':http('http://127.0.0.1:8089/')}
    d['host_ipv4']=run(['ipconfig','getifaddr','en7']);d['host_physical_cores']=int(run(['sysctl','-n','hw.physicalcpu']))
    emulator=run([Path.home()/'Library/Android/sdk/emulator/emulator','-version'])
    d['sdk_emulator_version_lines']=[x for x in emulator.splitlines() if 'version' in x.lower() or 'build_id' in x.lower()][:2]
    OUT.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'output':str(OUT),'services':d['services'],'capture_processes':capture,'own_orphans_cleaned':len(cleanup),'cpu_limits_preserved':True},ensure_ascii=False))
if __name__=='__main__':main()
