#!/usr/bin/env python3
"""Build only: pinned external nanors, Mac stdio producer and Android JNI consumer."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent


def run(*args):
    return subprocess.run([str(x) for x in args], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path.home()/'.cache/huoguo-v2-sources/moonlight-common-c')
    parser.add_argument('--build', type=Path, default=Path('/private/tmp/huoguo-android-udp-build'))
    parser.add_argument('--ndk', type=Path, default=Path.home()/'Library/Android/sdk/ndk/29.0.14206865')
    parser.add_argument('--android', action='store_true')
    args = parser.parse_args()
    pins = json.loads((ROOT.parent.parent/'upstreams.json').read_text())
    core = next(x['commit'] for x in pins['upstreams'] if x['name'] == 'moonlight-common-c')
    for directory, expected in {'': core, 'nanors': pins['core_dependencies']['nanors']}.items():
        checkout = args.source/directory
        def git(*values):
            return subprocess.run(['git', '-C', str(checkout), *values], check=True, capture_output=True, text=True).stdout.strip()
        if git('rev-parse', 'HEAD') != expected or git('status', '--porcelain'):
            raise SystemExit('Dependency pin/clean check failed: '+directory)
    cmake = shutil.which('cmake') or str(Path.home()/'.cache/huoguo-v2-tools/bin/cmake')
    build = args.build/('android-arm64' if args.android else 'host')
    configure = [cmake, '-S', ROOT, '-B', build, '-DCMAKE_BUILD_TYPE=Release',
                 '-DMOONLIGHT_SOURCE_DIR='+str(args.source.resolve())]
    if args.android:
        configure += ['-DCMAKE_TOOLCHAIN_FILE='+str(args.ndk/'build/cmake/android.toolchain.cmake'),
                      '-DANDROID_ABI=arm64-v8a', '-DANDROID_PLATFORM=android-23', '-DANDROID_STL=c++_static']
    run(*configure)
    run(cmake, '--build', build, '--parallel', '4')
    if not args.android:
        run(Path(cmake).with_name('ctest'), '--test-dir', build, '--output-on-failure')
    print(json.dumps({'scope': 'build_only_no_device', 'output': str(build/('libhuoguo_udp_fec.so' if args.android else 'h264_udp_packetizer'))}))


if __name__ == '__main__':
    main()
