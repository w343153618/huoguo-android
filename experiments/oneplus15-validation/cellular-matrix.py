import subprocess,pathlib,json,time,sys
b=pathlib.Path(__file__).parent
adb=['/Users/wyw/Library/Android/sdk/platform-tools/adb','-s','3B15AL00M9U00000']
orders=[[2500000,4000000,6000000,8000000,12000000],[8000000,6000000,4000000,12000000,2500000],[12000000,2500000,8000000,4000000,6000000]]
for roundno,rates in enumerate(orders,1):
 for rate in rates:
  s=subprocess.check_output(adb+['shell','dumpsys','connectivity'],text=True)
  import re
  m=re.search(r'Active default network: (\d+)',s);n=m.group(1) if m else '?'
  line=next((x for x in s.splitlines() if 'network{'+n+'}' in x),'')
  if 'ni{MOBILE' not in line:raise RuntimeError('Not cellular; aborting benchmark')
  label=f'cellular-after-r{roundno}-{rate//1000}k'
  r=subprocess.run([sys.executable,str(b/'run-case.py'),label,'146.56.249.175','VBR','1600',str(rate),'30','60','120'],text=True)
  if r.returncode:raise SystemExit(r.returncode)
  print('CASE_COMPLETE',label,flush=True)
 label=f'cellular-v50-r{roundno}'
 r=subprocess.run([sys.executable,str(b/'run-case.py'),label,'146.56.249.175','VBR','1200','2500000','30','30','120'],text=True)
 if r.returncode:raise SystemExit(r.returncode)
 print('CASE_COMPLETE',label,flush=True)
