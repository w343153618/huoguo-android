#!/usr/bin/env python3
"""Offline actual Java feedback/inbox + native FEC metadata checks; never touches a device."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]

def run(command, timeout=60):
    result = subprocess.run(list(map(str, command)), capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise SystemExit(result.stdout + result.stderr)
    return result.stdout.strip()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', type=Path, default=Path(os.environ.get('ANDROID_HOME', str(Path.home()/'Library/Android/sdk'))))
    parser.add_argument('--java-home', type=Path, default=Path(os.environ.get('JAVA_HOME', '/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home')))
    parser.add_argument('--app-classes', type=Path, default=ROOT/'app/build/intermediates/javac/release/compileReleaseJavaWithJavac/classes')
    parser.add_argument('--source', type=Path, default=Path.home()/'.cache/huoguo-v2-sources/moonlight-common-c')
    args = parser.parse_args()
    android = args.sdk/'platforms/android-37.0/android.jar'
    if not android.is_file() or not (args.app_classes/'local/remoteandroid/direct/MainActivity.class').is_file():
        parser.error('Existing Android 37 SDK and matching app classes are required; nothing is installed')
    with tempfile.TemporaryDirectory(prefix='huoguo-feedback-check-', dir='/private/tmp') as directory:
        temporary = Path(directory)
        sources = sorted((ROOT/'experiments/nps-transport/phone').glob('*.java'))
        sources += [ROOT/'app/src/main/java/local/remoteandroid/direct/PlaybackClock.java', ROOT/'tests/java/local/remoteandroid/direct/UdpFeedbackDiagnosticProbe.java']
        run([args.java_home/'bin/javac', '-source', '8', '-target', '8', '-cp', str(android)+os.pathsep+str(args.app_classes), '-d', temporary/'classes', *sources])
        java_result = run([args.java_home/'bin/java', '-cp', os.pathsep.join(map(str,(temporary/'classes',android,args.app_classes))), 'local.remoteandroid.direct.UdpFeedbackDiagnosticProbe'])
        if not java_result.startswith('PASS ') or not java_result.endswith('UDP feedback and bounded metadata checks (offline)'):
            raise SystemExit('Unexpected Java verification output: '+java_result)
        # The build helper verifies both external dependency pins and cleanliness.
        run(['python3', ROOT/'experiments/moonlight-v2/transport/android-udp/build.py', '--source', args.source, '--build', temporary/'native'], timeout=120)
        run(['c++', '-std=c++20', '-Wall', '-Wextra', '-Werror', '-I', ROOT/'experiments/moonlight-v2/transport/android-udp', '-isystem', args.source/'nanors', '-isystem', args.source/'nanors/deps/obl', ROOT/'tests/native/udp_feedback_diagnostics.cpp', temporary/'native/host/libpinned_nanors.a', '-o', temporary/'metadata-check'])
        native_result = json.loads(run([temporary/'metadata-check']))
    print(json.dumps({'java':java_result,'native':native_result,'devices_or_services_modified':False,'not_measured':['codec behavior','screen FPS','real video','network capacity','WAN','physical AV skew']}))

if __name__ == '__main__':
    main()
