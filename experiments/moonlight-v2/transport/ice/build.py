#!/usr/bin/env python3
"""Build only. No network downloads, cloud service, device or APK mutations."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
from prepare_source import ROOT, prepare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('/private/tmp/huoguo-libjuice-upstream'))
    parser.add_argument('--build', type=Path, default=Path('/private/tmp/huoguo-ice-build'))
    parser.add_argument('--android', action='store_true')
    parser.add_argument('--ndk', type=Path, default=Path.home()/'Library/Android/sdk/ndk/29.0.14206865')
    args = parser.parse_args()
    args.build.mkdir(parents=True, exist_ok=True)
    source = args.build/'patched-source'
    prepare(args.source.resolve(), source.resolve())
    cmake = shutil.which('cmake') or str(Path.home()/'.cache/huoguo-v2-tools/bin/cmake')
    output = args.build/('android-arm64' if args.android else 'host-arm64')
    configure = [cmake, '-S', str(ROOT), '-B', str(output), '-DCMAKE_BUILD_TYPE=Release',
                 '-DLIBJUICE_SOURCE_DIR='+str(source.resolve())]
    if args.android:
        configure += ['-DCMAKE_TOOLCHAIN_FILE='+str(args.ndk/'build/cmake/android.toolchain.cmake'),
                      '-DANDROID_ABI=arm64-v8a', '-DANDROID_PLATFORM=android-23']
    else:
        configure += ['-DCMAKE_OSX_ARCHITECTURES=arm64']
    subprocess.run(configure, check=True)
    subprocess.run([cmake, '--build', str(output), '--parallel', '4'], check=True)
    print(json.dumps({'scope': 'build_only_no_device', 'android': args.android, 'arm64': True,
                      'library': str(output/('libhuoguo_ice_udp.so' if args.android else 'libhuoguo_ice_udp.dylib'))}))


if __name__ == '__main__':
    main()
