from pathlib import Path
import subprocess,os
b=Path(__file__).parent;sdk=Path('/Users/wyw/Library/Android/sdk');bt=sdk/'build-tools/36.0.0';jar=sdk/'platforms/android-37.0/android.jar';java=Path('/opt/homebrew/opt/openjdk@21/bin');app=b.parent/'github/huoguo-android/app/build/intermediates/javac/release/compileReleaseJavaWithJavac/classes';env=dict(os.environ,JAVA_HOME='/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home')
def run(a):
 r=subprocess.run([str(x) for x in a],capture_output=True,text=True,env=env)
 if r.returncode: print(r.stderr[:2000]);r.check_returncode()
for name,src,manifest in [('probe','PhoneProbe.java','AndroidManifest.xml'),('fixture','MotionActivity.java','FixtureManifest.xml')]:
 out=b/name;classes=out/'classes';dex=out/'dex';classes.mkdir(parents=True,exist_ok=True);dex.mkdir(exist_ok=True)
 run([java/'javac','-source','8','-target','8','-cp',str(jar)+':'+str(app),'-d',classes,b/src])
 run([bt/'d8','--lib',jar,'--classpath',app,'--output',dex,*sorted(classes.rglob('*.class'))])
 run([bt/'aapt2','link','-I',jar,'--manifest',b/manifest,'-o',out/'unsigned.apk'])
 run(['zip','-j',out/'unsigned.apk',dex/'classes.dex']);run([bt/'zipalign','-f','4',out/'unsigned.apk',out/'aligned.apk'])
 run([bt/'apksigner','sign','--ks','/Users/wyw/.android/debug.keystore','--ks-pass','pass:android','--key-pass','pass:android','--out',b/(name+'.apk'),out/'aligned.apk'])
 print(name+' built')
