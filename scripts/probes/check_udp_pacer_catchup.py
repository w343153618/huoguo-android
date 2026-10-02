#!/usr/bin/env python3
"""Compile actual common Pacer pure-timing checks; never uses sockets or devices."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=Path.home()/'.cache/huoguo-v2-sources/moonlight-common-c')
    args=parser.parse_args()
    pins=json.loads((ROOT/'experiments/moonlight-v2/upstreams.json').read_text())
    expected=next(x['commit'] for x in pins['upstreams'] if x['name']=='moonlight-common-c')
    for subdir,commit in {'':expected,'nanors':pins['core_dependencies']['nanors']}.items():
        directory=args.source/subdir
        def git(*parts):
            return subprocess.run(['git','-C',str(directory),*parts],check=True,capture_output=True,text=True).stdout.strip()
        if git('rev-parse','HEAD')!=commit or git('status','--porcelain'):
            raise SystemExit('Dependency pin/clean check failed: '+subdir)
    with tempfile.TemporaryDirectory(prefix='huoguo-pacer-check-',dir='/private/tmp') as folder:
        binary=Path(folder)/'pacer-check'
        subprocess.run(['c++','-std=c++20','-Wall','-Wextra','-Werror','-I',str(ROOT/'experiments/moonlight-v2/transport'),'-isystem',str(args.source/'nanors'),'-isystem',str(args.source/'nanors/deps/obl'),str(ROOT/'tests/native/udp_pacer_catchup.cpp'),'-o',str(binary)],check=True,timeout=30)
        result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=20)
        if result.returncode:raise SystemExit(result.stderr)
        evidence=json.loads(result.stdout)
    print(json.dumps(evidence))

if __name__=='__main__':main()
