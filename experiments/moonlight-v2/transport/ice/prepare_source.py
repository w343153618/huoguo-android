#!/usr/bin/env python3
"""Verify a public pin, copy outside Git, apply a small checked local patch."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent


def prepare(source, destination):
    pin = json.loads((ROOT/'upstream.json').read_text())
    def git(*args):
        return subprocess.run(['git', '-C', str(source), *args], check=True,
                              capture_output=True, text=True).stdout.strip()
    if git('rev-parse', 'HEAD') != pin['commit'] or git('status', '--porcelain'):
        raise SystemExit('Pinned public source must be clean and exact')
    if destination.exists():
        # Only our previous build copy may be replaced, never the pinned clone.
        marker = destination/'.huoguo-ice-copy'
        if not marker.is_file() or marker.read_text().strip() != pin['commit']:
            raise SystemExit('Refusing to replace unmarked source directory')
        shutil.rmtree(destination)
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns('.git', 'build*'))
    patch = ROOT/'libjuice-physical-ipv4.patch'
    for check in (True, False):
        cmd = ['git', 'apply', '--unsafe-paths'] + (['--check'] if check else []) + [str(patch)]
        subprocess.run(cmd, cwd=destination, check=True, capture_output=True)
    (destination/'.huoguo-ice-copy').write_text(pin['commit']+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('/private/tmp/huoguo-libjuice-upstream'))
    parser.add_argument('--destination', type=Path, default=Path('/private/tmp/huoguo-ice-build/patched-source'))
    args = parser.parse_args()
    prepare(args.source.resolve(), args.destination.resolve())
    print(json.dumps({'source_pin_verified': True, 'patch_applied': True}))


if __name__ == '__main__':
    main()
