import subprocess,pathlib,time
b=pathlib.Path(__file__).parent
ssh=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8','-o','StrictHostKeyChecking=yes','-i','/Users/wyw/.ssh/id_ed25519','root@146.56.249.175']
code='''import pathlib,subprocess,time,urllib.request,os
p=pathlib.Path('/etc/nps/conf/nps.conf');original=p.read_bytes();backup=pathlib.Path('/root/nps-before-androidremote-profile.conf');backup.write_bytes(original);backup.chmod(0o600)
s=original.decode();s='\\n'.join(x for x in s.splitlines() if not x.strip().startswith(('pprof_ip=','pprof_port=')))+'\\npprof_ip=127.0.0.1\\npprof_port=19999\\n'
try:
 p.write_text(s);subprocess.run(['systemctl','restart','nps'],check=True)
 for i in range(30):
  time.sleep(1)
  try:
   urllib.request.urlopen('http://127.0.0.1:19999/debug/pprof/',timeout=2).close();break
  except Exception:pass
 else:raise RuntimeError('local profiler did not start')
 time.sleep(3);data=urllib.request.urlopen('http://127.0.0.1:19999/debug/pprof/profile?seconds=15',timeout=25).read();pathlib.Path('/tmp/androidremote-nps-cpu.pb.gz').write_bytes(data);print('CPU profile saved, bytes',len(data))
finally:
 p.write_bytes(original);subprocess.run(['systemctl','restart','nps'],check=True);print('Original config restored; profiler closed')
'''
r=subprocess.run(ssh+['python3 -'],input=code,text=True,capture_output=True,timeout=100);print(r.stdout);print(r.stderr[:1000]);r.check_returncode()
subprocess.run(['scp',*ssh[1:-1],ssh[-1]+':/tmp/androidremote-nps-cpu.pb.gz',str(b/'nps-cpu.pb.gz')],check=True)
subprocess.run(['python3',str(b/'parse-pprof.py'),str(b/'nps-cpu.pb.gz')],check=True)
