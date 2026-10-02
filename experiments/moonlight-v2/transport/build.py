#!/usr/bin/env python3
"""Build/test a loopback-only UDP prototype; no download or production changes."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent


def run(*args, **kwargs):
    return subprocess.run([str(value) for value in args], check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(os.environ.get(
        'HUOGUO_V2_CACHE', str(Path.home()/'.cache/huoguo-v2-sources')))/'moonlight-common-c')
    parser.add_argument('--build', type=Path, default=Path(tempfile.gettempdir())/'huoguo-udp-native-build')
    args = parser.parse_args()
    pins = json.loads((ROOT.parent/'upstreams.json').read_text())
    core = next(value['commit'] for value in pins['upstreams'] if value['name'] == 'moonlight-common-c')
    for directory, expected in {'': core, 'nanors': pins['core_dependencies']['nanors']}.items():
        checkout = args.source/directory
        actual = run('git', '-C', checkout, 'rev-parse', 'HEAD', capture_output=True, text=True).stdout.strip()
        if actual != expected or run('git', '-C', checkout, 'status', '--porcelain',
                                     capture_output=True, text=True).stdout:
            raise SystemExit('Dependency pin or clean checkout check failed: '+directory)
    cmake = shutil.which('cmake') or str(Path.home()/'.cache/huoguo-v2-tools/bin/cmake')
    run(cmake, '-S', ROOT, '-B', args.build, '-DCMAKE_BUILD_TYPE=Release',
        '-DMOONLIGHT_SOURCE_DIR='+str(args.source.resolve()))
    run(cmake, '--build', args.build, '--parallel', '4')
    run(Path(cmake).with_name('ctest'), '--test-dir', args.build, '--output-on-failure')
    print('Loopback prototype executable: '+str(args.build/'udp_media_probe'))


if __name__ == '__main__':
    main()
