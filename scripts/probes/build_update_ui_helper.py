#!/usr/bin/env python3
from pathlib import Path
import hashlib,json,os,subprocess,zipfile
import argparse
root=Path(__file__).resolve().parents[2]
parser=argparse.ArgumentParser(description='Build the bounded alpha8 update UI helper, no phone actions')
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
out=args.output.resolve()
if out==root or root in out.parents:raise SystemExit('helper_APKs_must_stay_outside_source_tree')
out.mkdir(mode=0o700,exist_ok=False)
source=root/'experiments/moonlight-v2/authenticated-lan/UpdateUiAcceptance.java'
sdk=Path(os.environ.get('ANDROID_HOME',Path.home()/'Library/Android/sdk'))
android=sdk/'platforms/android-37.0/android.jar'; tools=sdk/'build-tools/36.0.0'
java=Path(os.environ.get('JAVA_HOME','/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home'))
app=root/'app/build/intermediates/javac/debug/compileDebugJavaWithJavac/classes'
for name in ('MainActivity','AppUpdater','UpdateChannelPolicy','AuthenticatedLanUdpUi'):
 if not (app/'local/remoteandroid/direct'/(name+'.class')).is_file():raise SystemExit('compiled_udp_debug_classes_required')
for part in ('classes','dex'):(out/part).mkdir(exist_ok=True)
manifest=out/'AndroidManifest.xml'
manifest.write_text('<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="local.huoguo.updateuitest"><uses-sdk android:minSdkVersion="30" android:targetSdkVersion="37"/><instrumentation android:name="local.remoteandroid.direct.UpdateUiAcceptance" android:targetPackage="local.remoteandroid.direct.experiment"/></manifest>')
def run(args):
 p=subprocess.run([str(x) for x in args],env=dict(os.environ,JAVA_HOME=str(java)),capture_output=True,text=True,timeout=60)
 if p.returncode:raise RuntimeError(Path(args[0]).name+': '+p.stderr[:4096])
run([java/'bin/javac','-source','8','-target','8','-cp',str(android)+os.pathsep+str(app),'-d',out/'classes',source])
run([tools/'d8','--lib',android,'--classpath',app,'--output',out/'dex',*sorted((out/'classes').rglob('*.class'))])
run([tools/'aapt2','link','-I',android,'--manifest',manifest,'-o',out/'unsigned.apk'])
with zipfile.ZipFile(out/'unsigned.apk','a') as apk:apk.write(out/'dex/classes.dex','classes.dex')
run([tools/'zipalign','-f','4',out/'unsigned.apk',out/'aligned.apk'])
run([tools/'apksigner','sign','--ks',Path.home()/'.android/debug.keystore','--ks-pass','pass:android','--out',out/'helper.apk',out/'aligned.apk'])
os.chmod(out/'helper.apk',0o600)
receipt={'scope':'independent_update_UI_only_no_device_run','apk':str(out/'helper.apk'),'apk_sha256':hashlib.sha256((out/'helper.apk').read_bytes()).hexdigest(),'source_sha256':hashlib.sha256((source).read_bytes()).hexdigest(),'component':'local.huoguo.updateuitest/local.remoteandroid.direct.UpdateUiAcceptance','target':'local.remoteandroid.direct.experiment','classpath':str(app)}
(out/'build-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(receipt))
