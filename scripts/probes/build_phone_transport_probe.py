#!/usr/bin/env python3
"""Build a separate instrumentation APK; does not replace the installed client."""
import os
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'experiments/nps-transport/phone'
SDK = Path(os.environ.get('ANDROID_HOME', str(Path.home() / 'Library/Android/sdk')))
JAVA = Path(os.environ.get('JAVA_HOME', '/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home'))
TOOLS = SDK / 'build-tools/36.0.0'
ANDROID = SDK / 'platforms/android-37.0/android.jar'
APP = ROOT / 'app/build/intermediates/javac/debug/compileDebugJavaWithJavac/classes'
OUT = SOURCE / 'build'

def run(args):
    subprocess.run([str(x) for x in args], check=True, env=dict(os.environ, JAVA_HOME=str(JAVA)),
                   stdout=subprocess.DEVNULL)

def main():
    if not APP.is_dir():
        raise SystemExit('Build :app:assembleDebug first to provide the matching source classpath')
    for name in ('classes', 'dex'):
        (OUT/name).mkdir(parents=True, exist_ok=True)
    run([JAVA/'bin/javac','-source','8','-target','8','-cp',str(ANDROID)+os.pathsep+str(APP),
         '-d',OUT/'classes',SOURCE/'PhoneProbe.java'])
    run([TOOLS/'d8','--lib',ANDROID,'--classpath',APP,'--output',OUT/'dex',*sorted((OUT/'classes').rglob('*.class'))])
    run([TOOLS/'aapt2','link','-I',ANDROID,'--manifest',SOURCE/'AndroidManifest.xml','-o',OUT/'unsigned.apk'])
    with zipfile.ZipFile(OUT/'unsigned.apk','a') as apk:
        apk.write(OUT/'dex/classes.dex','classes.dex')
    run([TOOLS/'zipalign','-f','4',OUT/'unsigned.apk',OUT/'aligned.apk'])
    run([TOOLS/'apksigner','sign','--ks',Path.home()/'.android/debug.keystore','--ks-pass','pass:android',
         '--key-pass','pass:android','--out',OUT/'phoneprobe.apk',OUT/'aligned.apk'])
    print(OUT/'phoneprobe.apk')

if __name__=='__main__':
    main()
