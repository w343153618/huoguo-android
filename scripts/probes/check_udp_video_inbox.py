#!/usr/bin/env python3
"""Compile the actual experimental video inbox and run offline state checks.

Uses existing SDK/JDK/app classes only. It does not build/install an APK, open
ADB, configure a codec, restart any service, or access a network.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', type=Path, default=Path(os.environ.get(
        'ANDROID_HOME', str(Path.home() / 'Library/Android/sdk'))))
    parser.add_argument('--java-home', type=Path, default=Path(os.environ.get(
        'JAVA_HOME', '/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home')))
    parser.add_argument('--app-classes', type=Path, default=ROOT /
        'app/build/intermediates/javac/release/compileReleaseJavaWithJavac/classes')
    args = parser.parse_args()
    android = args.sdk / 'platforms/android-37.0/android.jar'
    java, javac = args.java_home / 'bin/java', args.java_home / 'bin/javac'
    if not all(path.is_file() for path in (android, java, javac)):
        parser.error('Existing Android 37 SDK jar and JDK are required; no install attempted')
    if not (args.app_classes / 'local/remoteandroid/direct/MainActivity.class').is_file():
        parser.error('Existing matching app classes required; build the app separately or pass --app-classes')
    sources = sorted((ROOT / 'experiments/nps-transport/phone').glob('*.java'))
    sources.extend((ROOT / 'app/src/main/java/local/remoteandroid/direct/PlaybackClock.java',
                    ROOT / 'app/src/main/java/local/remoteandroid/direct/MediaPresentationMetrics.java',
                    ROOT / 'tests/java/local/remoteandroid/direct/AsyncVideoInboxProbe.java'))
    with tempfile.TemporaryDirectory(prefix='huoguo-video-inbox-check-', dir='/private/tmp') as folder:
        build = Path(folder)
        compile_result = subprocess.run([str(javac), '-source', '8', '-target', '8',
            '-cp', str(android) + os.pathsep + str(args.app_classes), '-d', str(build),
            *map(str, sources)], capture_output=True, text=True, timeout=30)
        if compile_result.returncode:
            raise SystemExit('Offline probe compile failed:\n' + compile_result.stderr)
        result = subprocess.run([str(java), '-cp', str(build) + os.pathsep + str(android),
            'local.remoteandroid.direct.AsyncVideoInboxProbe'], capture_output=True,
            text=True, timeout=10)
        expected = 'PASS 27 bounded FIFO/reference recovery checks (offline, no codecs or phone)'
        if result.returncode or result.stdout.strip() != expected:
            raise SystemExit('Offline inbox checks failed:\n' + result.stdout + result.stderr)
    print(json.dumps({'scope': 'offline_actual_probe_FIFO_epoch_reference_state_only',
                      'checks': 27, 'passed': True,
                      'not_measured': ['MediaCodec behavior', 'concurrent device execution',
                                       'UDP sockets', 'real video', 'phone FPS', 'WAN', 'AV sync']}))


if __name__ == '__main__':
    main()
