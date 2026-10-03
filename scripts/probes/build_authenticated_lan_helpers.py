#!/usr/bin/env python3
"""Build test UI instrumentation and dedicated guest touch receipt; no devices."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    sdk = Path(os.environ.get('ANDROID_HOME', Path.home()/'Library/Android/sdk'))
    tools = sdk/'build-tools/36.0.0'
    android = sdk/'platforms/android-37.0/android.jar'
    java_home = Path(os.environ.get('JAVA_HOME', '/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home'))
    classes = ROOT/'app/build/intermediates/javac/debug/compileDebugJavaWithJavac/classes'
    if not (classes/'local/remoteandroid/direct/UdpAudioReceiver.class').is_file():
        parser.error('Build the opt-in authenticatedLanUdp debug candidate first')
    definitions = [
        ('ui', 'LanUiAcceptance.java', 'local.huoguo.lanuitest',
         '<instrumentation android:name="local.remoteandroid.direct.LanUiAcceptance" android:targetPackage="local.remoteandroid.direct.experiment"/>'),
        ('v50ui', 'UdpV50UiAcceptance.java', 'local.huoguo.v50uitest',
         '<instrumentation android:name="local.remoteandroid.direct.UdpV50UiAcceptance" android:targetPackage="local.remoteandroid.direct.experiment"/>'),
        ('receipt', 'TouchReceiptActivity.java', 'local.huoguo.touchreceipt',
         '<application android:debuggable="true" android:theme="@android:style/Theme.Material.Light.NoActionBar"><activity android:name=".TouchReceiptActivity" android:exported="true"/></application>')]
    results = []
    for name, filename, package, extra in definitions:
        out = args.output/name
        (out/'classes').mkdir(parents=True, exist_ok=True)
        (out/'dex').mkdir(exist_ok=True)
        manifest = out/'AndroidManifest.xml'
        manifest.write_text('<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="'+package+'"><uses-sdk android:minSdkVersion="30" android:targetSdkVersion="37"/>'+extra+'</manifest>')
        source = ROOT/'experiments/moonlight-v2/authenticated-lan'/filename
        def run(values):
            result = subprocess.run([str(v) for v in values], env=dict(os.environ, JAVA_HOME=str(java_home)),
                capture_output=True, text=True, timeout=45)
            if result.returncode:
                raise RuntimeError('Helper build failed in '+Path(values[0]).name+': '+result.stderr[:4096])
        run([java_home/'bin/javac','-source','8','-target','8','-cp',str(android)+os.pathsep+str(classes),'-d',out/'classes',source])
        run([tools/'d8','--lib',android,'--classpath',classes,'--output',out/'dex',*sorted((out/'classes').rglob('*.class'))])
        run([tools/'aapt2','link','-I',android,'--manifest',manifest,'-o',out/'unsigned.apk'])
        with zipfile.ZipFile(out/'unsigned.apk','a') as apk:
            apk.write(out/'dex/classes.dex','classes.dex')
        run([tools/'zipalign','-f','4',out/'unsigned.apk',out/'aligned.apk'])
        run([tools/'apksigner','sign','--ks',Path.home()/'.android/debug.keystore','--ks-pass','pass:android',
             '--out',out/'helper.apk',out/'aligned.apk'])
        os.chmod(out/'helper.apk', 0o600)
        results.append({'name':name,'output':str(out/'helper.apk'),'sha256':hashlib.sha256((out/'helper.apk').read_bytes()).hexdigest()})
    print(json.dumps({'scope':'test_helper_build_no_device_credentials_or_media_keys','artifacts':results}))


if __name__ == '__main__':
    main()
