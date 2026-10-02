"""Read-only Git deployment key pulls signed release artifacts for the HTTPS gateway."""
import hashlib,json,os,pathlib,re,subprocess,tempfile
from download_page import sync_downloads
root=pathlib.Path(os.environ.get('DIRECT_STATE_DIR',str(pathlib.Path.home()/'Documents/ChatGPT/others/android-remote/m1-compare')))
key_root=pathlib.Path(os.environ.get('UPDATE_KEY_DIR',str(pathlib.Path.home()/'Library/Application Support/AndroidRemote/direct')))
use_gh=os.environ.get('UPDATE_USE_GH')=='1'
cache=root/'github-update-cache';cache.mkdir(mode=0o700,exist_ok=True)
repo='w343153618/huoguo-android'
ssh=['/usr/bin/ssh','-p','443','-o','HostKeyAlias=github.com','-o','StrictHostKeyChecking=yes',
     '-o','BatchMode=yes','-o','ConnectTimeout=10','-o','IdentitiesOnly=yes',
     '-o','UserKnownHostsFile="'+str(key_root/'github-known_hosts')+'"','-i',str(key_root/'github-update-key')]
import shlex
env=dict(os.environ) if use_gh else dict(os.environ,GIT_SSH_COMMAND=shlex.join(ssh))
def git(*args):
    command=['/usr/bin/git','-C',str(cache)]+(['-c','credential.helper=!gh auth git-credential'] if use_gh else [])
    result=subprocess.run([*command,*args],env=env,capture_output=True,timeout=90)
    if result.returncode:raise RuntimeError('Git sync failed: '+result.stderr.decode(errors='replace')[-1500:])
    return result.stdout
if not (cache/'.git').exists():
    git('init');git('remote','add','origin',('https://github.com/' if use_gh else 'ssh://git@ssh.github.com/')+repo+'.git')
if use_gh:
    git('remote','set-url','origin','https://github.com/'+repo+'.git')
git('fetch','--depth=1','origin','updates')
metadata=json.loads(git('show','FETCH_HEAD:update.json'))
version=metadata['version_name'];code=metadata['version_code']
if not re.fullmatch(r'[0-9]+(?:\.[0-9]+){1,3}',version) or type(code)!=int or code<1:raise RuntimeError('Invalid release version')
name='HuoguoAndroid-v'+version+'.apk'
base=os.environ.get('UPDATE_BASE_URL','https://146.56.249.175:15556/updates')
if metadata['apk_url']!=base+'/'+name:raise RuntimeError('Unexpected download URL')
updates=root/'updates';updates.mkdir(mode=0o700,exist_ok=True)
current=updates/'update.json'
if current.exists():
    before=json.loads(current.read_text())
    if code<before['version_code']:raise RuntimeError('Refuse downgrade')
    if code==before['version_code']:
        if metadata!=before:raise RuntimeError('Same-version release must be immutable')
        sync_downloads(updates, root/'download', metadata)
        print('Updates unchanged; manual download entrypoint synchronized');raise SystemExit(0)
apk=git('show','FETCH_HEAD:HuoguoAndroid.apk')
if not 0<len(apk)<=67108864 or len(apk)!=metadata['apk_size'] or hashlib.sha256(apk).hexdigest()!=metadata['sha256']:raise RuntimeError('Release digest/size mismatch')
def atomic(path,data):
    fd,temp=tempfile.mkstemp(prefix='.incoming-',dir=updates)
    try:
        with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)
atomic(updates/name,apk)
atomic(current,(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n').encode())
sync_downloads(updates, root/'download', metadata)
print('Published signed update version '+version+' to HTTPS gateway; client verifies APK signing identity')
