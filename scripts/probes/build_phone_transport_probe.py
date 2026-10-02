#!/usr/bin/env python3
"""Build a separate instrumentation APK; does not replace the installed client."""
import os
import argparse
import struct
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'experiments/nps-transport/phone'
SDK = Path(os.environ.get('ANDROID_HOME', str(Path.home() / 'Library/Android/sdk')))
JAVA = Path(os.environ.get('JAVA_HOME', '/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home'))
TOOLS = SDK / 'build-tools/36.0.0'
ANDROID = SDK / 'platforms/android-37.0/android.jar'
APP = ROOT / 'app/build/intermediates/javac/release/compileReleaseJavaWithJavac/classes'
OUT = SOURCE / 'build'
ANDROID_NAMESPACE = 'http://schemas.android.com/apk/res/android'


def experimental_manifest(text):
    """Retarget the fixed probe classes without changing the source manifest."""
    manifest = ET.fromstring(text)
    if manifest.tag != 'manifest' or manifest.get('package') != 'local.remoteandroid.phoneprobe':
        raise ValueError('Unexpected phone probe manifest package')
    instruments = manifest.findall('instrumentation')
    expected = {'local.remoteandroid.direct.PhoneProbe', 'local.remoteandroid.direct.CodecFileProbe',
                'local.remoteandroid.direct.UdpVideoProbe'}
    name_key = '{' + ANDROID_NAMESPACE + '}name'
    target_key = '{' + ANDROID_NAMESPACE + '}targetPackage'
    if (len(instruments) != len(expected) or {item.get(name_key) for item in instruments} != expected
            or any(item.get(target_key) != 'local.remoteandroid.direct' for item in instruments)):
        raise ValueError('Unexpected phone probe instrumentation targets')
    manifest.set('package', 'local.remoteandroid.phoneprobe.experiment')
    for item in instruments:
        item.set(target_key, 'local.remoteandroid.direct.experiment')
    ET.register_namespace('android', ANDROID_NAMESPACE)
    return ET.tostring(manifest, encoding='unicode') + '\n'

def run(args):
    subprocess.run([str(x) for x in args], check=True, env=dict(os.environ, JAVA_HOME=str(JAVA)),
                   stdout=subprocess.DEVNULL)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--udp-native-library',type=Path,
                        help='Android arm64 libhuoguo_udp_fec.so to package in this instrumentation APK')
    parser.add_argument('--experimental-client', action='store_true',
                        help='Build an isolated probe targeting local.remoteandroid.direct.experiment')
    args=parser.parse_args()
    out = OUT/'experimental' if args.experimental_client else OUT
    manifest = SOURCE/'AndroidManifest.xml'
    native_library=args.udp_native_library
    if native_library is not None:
        if not native_library.is_file() or native_library.name!='libhuoguo_udp_fec.so':
            raise SystemExit('Expected compiled Android arm64 libhuoguo_udp_fec.so')
        with native_library.open('rb') as source:
            elf=source.read(20)
        if len(elf)<20 or elf[:4]!=b'\x7fELF' or elf[4]!=2 or elf[5]!=1 or struct.unpack('<H',elf[18:20])[0]!=183:
            raise SystemExit('UDP native component must be a little-endian ELF64 AArch64 library')
    if not APP.is_dir():
        raise SystemExit('Build :app:assembleRelease first to provide the matching source classpath')
    for name in ('classes', 'dex'):
        (out/name).mkdir(parents=True, exist_ok=True)
    if args.experimental_client:
        manifest = out/'AndroidManifest.xml'
        manifest.write_text(experimental_manifest((SOURCE/'AndroidManifest.xml').read_text()))
    run([JAVA/'bin/javac','-source','8','-target','8','-cp',str(ANDROID)+os.pathsep+str(APP),
         '-d',out/'classes',*sorted(SOURCE.glob('*.java'))])
    run([TOOLS/'d8','--lib',ANDROID,'--classpath',APP,'--output',out/'dex',*sorted((out/'classes').rglob('*.class'))])
    run([TOOLS/'aapt2','link','-I',ANDROID,'--manifest',manifest,'-o',out/'unsigned.apk'])
    with zipfile.ZipFile(out/'unsigned.apk','a') as apk:
        apk.write(out/'dex/classes.dex','classes.dex')
        if native_library is not None:
            apk.write(native_library,'lib/arm64-v8a/libhuoguo_udp_fec.so')
    run([TOOLS/'zipalign','-f','4',out/'unsigned.apk',out/'aligned.apk'])
    run([TOOLS/'apksigner','sign','--ks',Path.home()/'.android/debug.keystore','--ks-pass','pass:android',
         '--key-pass','pass:android','--out',out/'phoneprobe.apk',out/'aligned.apk'])
    print(out/'phoneprobe.apk')

if __name__=='__main__':
    main()
