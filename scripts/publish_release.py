"""Build/sign on the owner's Mac, publish private releases, push updates for M5 polling.
The existing signing key never leaves the local machine.
"""
import argparse,hashlib,json,os,pathlib,re,shutil,subprocess,tempfile
root=pathlib.Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--repository',default='w343153618/huoguo-android')
parser.add_argument('--base-url',default='https://146.56.249.175:15556/updates')
args=parser.parse_args()
def run(argv,**kw):return subprocess.run(argv,cwd=root,check=True,**kw)
def git(*argv,**kw):return run(['git','-c','credential.helper=!gh auth git-credential',*argv],**kw)
if run(['git','status','--porcelain'],capture_output=True,text=True).stdout.strip():raise SystemExit('Commit source changes before publishing')
if run(['git','branch','--show-current'],capture_output=True,text=True).stdout.strip()!='main':raise SystemExit('Publish from main')
text=(root/'app/build.gradle').read_text();version=re.search(r"versionName '([^']+)'",text).group(1);tag='v'+version
existing=subprocess.run(['gh','release','view',tag,'--repo',args.repository],capture_output=True)
if existing.returncode==0:raise SystemExit('Version already released; increase versionCode/versionName')
run(['./gradlew','assembleRelease','-PupdateManifestUrl='+args.base_url+'/update.json'])
apk=root/'app/build/outputs/apk/release/app-release.apk'
sdk=pathlib.Path(os.environ.get('ANDROID_HOME',str(pathlib.Path.home()/'Library/Android/sdk')))
check=run([str(sdk/'build-tools/36.0.0/apksigner'),'verify','--print-certs',str(apk)],capture_output=True,text=True)
expected='0d54d7cedd794e5beb96a27a69fbc57ae6453013a240dd3c5c7804baeff682da'
if 'certificate SHA-256 digest: '+expected not in check.stdout:raise SystemExit('Wrong upgrade signing identity; do not publish')
git('push','origin','main')
sha=run(['git','rev-parse','HEAD'],capture_output=True,text=True).stdout.strip()
with tempfile.TemporaryDirectory(prefix='huoguo-release-') as directory:
    temp=pathlib.Path(directory)
    shutil.copy2(apk,temp/'HuoguoAndroid.apk')
    run(['python3','scripts/release_manifest.py','--repository',args.repository,'--tag',tag,'--base-url',args.base_url,'--apk',str(temp/'HuoguoAndroid.apk'),'--output',str(temp/'update.json')])
    digest=hashlib.sha256(apk.read_bytes()).hexdigest();(temp/'SHA256SUMS.txt').write_text(digest+'  HuoguoAndroid.apk\n')
    run(['gh','release','create',tag,'--repo',args.repository,'--target',sha,'--title','给火锅的安卓 '+tag,'--notes-file','release-notes.md',str(temp/'HuoguoAndroid.apk'),str(temp/'update.json'),str(temp/'SHA256SUMS.txt')])
    # A separate tiny repository keeps the source working tree unchanged.
    subprocess.run(['git','init','-b','updates',directory],check=True,capture_output=True)
    for argv in (['config','user.name','Huoguo release'],['config','user.email','release@users.noreply.github.com'],['add','HuoguoAndroid.apk','update.json'],['commit','-m','Signed release '+tag],['remote','add','origin','https://github.com/'+args.repository+'.git']):
        subprocess.run(['git','-C',directory,*argv],check=True,capture_output=True)
    subprocess.run(['git','-C',directory,'-c','credential.helper=!gh auth git-credential','push','origin','HEAD:updates','--force'],check=True)
print('Published '+tag+'; M5 polls the private update branch; signing key remained on the Mac')
