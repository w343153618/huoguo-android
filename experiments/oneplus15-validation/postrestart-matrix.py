import subprocess,pathlib,sys,re
b=pathlib.Path(__file__).parent;adb=['/Users/wyw/Library/Android/sdk/platform-tools/adb','-s','3B15AL00M9U00000']
for n in range(1,4):
 for tag,size,rate,fps in [('4m','1600','4000000','60'),('8m','1600','8000000','60'),('v50','1200','2500000','30')]:
  s=subprocess.check_output(adb+['shell','dumpsys','connectivity'],text=True);m=re.search(r'Active default network: (\d+)',s);line=next((x for x in s.splitlines() if m and 'network{'+m.group(1)+'}' in x),'');assert 'ni{MOBILE' in line
  label=f'cellular-restart-r{n}-{tag}'
  subprocess.run([sys.executable,str(b/'run-case.py'),label,'146.56.249.175','VBR',size,rate,'30',fps,'120'],check=True)
  print('CASE_COMPLETE',label,flush=True)
