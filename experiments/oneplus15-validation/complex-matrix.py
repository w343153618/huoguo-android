import subprocess,pathlib,sys,re
b=pathlib.Path(__file__).parent;adb=['/Users/wyw/Library/Android/sdk/platform-tools/adb','-s','3B15AL00M9U00000']
subprocess.run([sys.executable,str(b.parent/'android17-validation/m5-adb.py'),'shell','am','force-stop','local.remoteandroid.fixture'],check=True)
subprocess.run([sys.executable,str(b.parent/'android17-validation/m5-adb.py'),'shell','am','start','-n','local.remoteandroid.fixture/.MotionActivity','--ez','complex','true'],check=True)
for n,rates in enumerate([[4000000,8000000,12000000],[12000000,4000000,8000000]],1):
 for rate in rates:
  s=subprocess.check_output(adb+['shell','dumpsys','connectivity'],text=True);m=re.search(r'Active default network: (\d+)',s);line=next((x for x in s.splitlines() if m and 'network{'+m.group(1)+'}' in x),'');assert 'ni{MOBILE' in line
  label=f'cellular-complex-r{n}-{rate//1000}k'
  subprocess.run([sys.executable,str(b/'run-case.py'),label,'146.56.249.175','VBR','1600',str(rate),'25','60','120'],check=True)
  print('CASE_COMPLETE',label,flush=True)
