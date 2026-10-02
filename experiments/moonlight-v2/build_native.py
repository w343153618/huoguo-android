#!/usr/bin/env python3
"""Build isolated native prototypes; no live service or phone is changed."""
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent
CACHE = Path(os.environ.get('HUOGUO_V2_CACHE', str(Path.home()/'.cache/huoguo-v2-sources')))
SOURCE = CACHE/'moonlight-common-c'
BUILD = CACHE/'prototype-build'
PINS = json.loads((ROOT/'upstreams.json').read_text())
CORE_PIN = next(p['commit'] for p in PINS['upstreams'] if p['name']=='moonlight-common-c')

def run(*args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)

def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    if not SOURCE.exists():
        run('git','clone','https://github.com/moonlight-stream/moonlight-common-c.git',SOURCE)
        run('git','-C',SOURCE,'checkout','--detach',CORE_PIN)
        run('git','-C',SOURCE,'submodule','update','--init','--recursive')
    expected={'':CORE_PIN, **PINS['core_dependencies']}
    for directory, pin in expected.items():
        got=run('git','-C',SOURCE/directory,'rev-parse','HEAD',capture_output=True,text=True).stdout.strip()
        if got!=pin:
            raise SystemExit('Dependency pin differs; preserve the checkout and resolve it explicitly: '+directory)
        if run('git','-C',SOURCE/directory,'status','--porcelain',capture_output=True,text=True).stdout:
            raise SystemExit('Dependency checkout contains modifications: '+directory)
    cmake=shutil.which('cmake')
    if not cmake:
        candidate=Path.home()/'.cache/huoguo-v2-tools/bin/cmake'
        if candidate.exists():cmake=str(candidate)
    if not cmake:raise SystemExit('CMake is required; a project-external tools venv may supply it')
    args=[cmake,'-S',ROOT,'-B',BUILD,'-DCMAKE_BUILD_TYPE=Release','-DMOONLIGHT_SOURCE_DIR='+str(SOURCE)]
    openssl=Path('/opt/homebrew/opt/openssl@3')
    if openssl.exists():args.append('-DOPENSSL_ROOT_DIR='+str(openssl))
    run(*args);run(cmake,'--build',BUILD,'--parallel','4')
    run(str(Path(cmake).with_name('ctest')),'--test-dir',BUILD,'--output-on-failure')
    print('Native prototypes built at '+str(BUILD))

if __name__=='__main__':main()
