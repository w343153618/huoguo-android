import subprocess,pathlib,json,sys,statistics,time
b=pathlib.Path(__file__).parent
adb=['/Users/wyw/Library/Android/sdk/platform-tools/adb','-s','3B15AL00M9U00000']
label,host,mode,size,rate=sys.argv[1:6]
seconds=sys.argv[6] if len(sys.argv)>6 else '20'
cmd=adb+['shell','am','instrument','-w','-e','phase','stream','-e','host',host,'-e','mode',mode,'-e','max_size',size,'-e','bit_rate',rate,'-e','seconds',seconds,'local.remoteandroid.phoneprobe/local.remoteandroid.direct.PhoneProbe']
cmd[-1:-1]=['-e','fps',sys.argv[7] if len(sys.argv)>7 else '60','-e','buffer_ms',sys.argv[8] if len(sys.argv)>8 else '120']
if 'shortvideo' in label:cmd[-1:-1]=['-e','touch','false']
r=subprocess.run(cmd,capture_output=True,text=True,timeout=int(seconds)+180)
report=None
for line in r.stdout.splitlines():
 if line.startswith('INSTRUMENTATION_RESULT: report='):report=json.loads(line.split('report=',1)[1])
if report is None:
 (b/(label+'-failure.txt')).write_text(r.stdout+r.stderr)
 print((r.stdout+r.stderr)[:2000]);raise SystemExit(1)
(b/(label+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2))
samples=report['samples'][3:];gaps=sorted(report['render_gaps_ms'])
summary={k:report[k] for k in ['host','mode','requested_bps','decoder','hardware','size','startup_ms','buffer_ms','audio_pcm_bytes','adaptive_rejected','running_at_end','fps_limit','received_total_mbps','received_video_mbps']}
summary['label']=label
for field in ['rx_fps','render_fps','rtt_ms','target_bps']:
 values=[s[field] for s in samples if s[field]>=0]
 if values:summary[field+'_mean']=round(statistics.mean(values),2);summary[field+'_min']=round(min(values),2);summary[field+'_max']=round(max(values),2)
if gaps:
 summary.update(render_gap_p95_ms=round(gaps[int((len(gaps)-1)*.95)],2),render_gap_max_ms=round(max(gaps),2),render_gaps_over_100ms=sum(g>100 for g in gaps))
(b/(label+'-summary.json')).write_text(json.dumps(summary,ensure_ascii=False,indent=2))
print(json.dumps(summary,ensure_ascii=False))
