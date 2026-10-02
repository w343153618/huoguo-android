#!/usr/bin/env python3
"""Bounded source-composition ABA trial on the rooted M1 test guest.

Uses the AOSP developer-option transactions, checks both settings readback and
TimeStats composition counters, and restores the original switch in finally.
No emulator reboot, APK install or production service change.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.probes.collect_source_compositor_stats import collect
from scripts.probes.source_probe_guards import require_no_source_capture


def parse_disable_overlays(raw):
    if not isinstance(raw, str) or len(raw) > 2048 or 'Error:' in raw:
        raise ValueError('surfaceflinger_read_rejected')
    words = []
    for line in raw.splitlines():
        match = re.fullmatch(r"0x[0-9a-fA-F]{8}:\s+((?:[0-9a-fA-F]{8}\s+)+)'[^']*'\)?", line)
        if match:
            words.extend(int(x, 16) for x in match[1].split())
    if len(words) != 5 or any(x not in (0, 1) for x in words):
        raise ValueError('unexpected_surfaceflinger_read_layout')
    return words[4]


def read_flag(adb):
    result = subprocess.run([str(adb), '-s', 'emulator-5556', 'shell', 'su', '0',
                             'service', 'call', 'SurfaceFlinger', '1010'],
                            capture_output=True, text=True, timeout=10, check=True)
    return parse_disable_overlays(result.stdout)


def write_flag(adb, value):
    if value not in (0, 1):
        raise ValueError('invalid_composition_flag')
    result = subprocess.run([str(adb), '-s', 'emulator-5556', 'shell', 'su', '0',
                             'service', 'call', 'SurfaceFlinger', '1008', 'i32', str(value)],
                            capture_output=True, text=True, timeout=10, check=True)
    if 'Error:' in result.stdout or read_flag(adb) != value:
        raise ValueError('composition_setting_not_verified')


def with_restored_flag(adb, original, operation):
    try:
        return operation()
    finally:
        write_flag(adb, original)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packetizer', type=Path, required=True)
    parser.add_argument('--encoder', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    adb = Path.home()/'Library/Android/sdk/platform-tools/adb'
    original = read_flag(adb)
    if original != 0:
        parser.error('original composition is not the default; keep existing state')
    manifest = {'schema': 1, 'scope': 'Real M1 YouTube LAN UDP OnePlus15; source composition ABA',
                'original_disable_overlays': original, 'runs': [], 'restoration_verified': False,
                'official_option_source': 'https://android.googlesource.com/platform/packages/apps/Settings/+/refs/heads/main/src/com/android/settings/development/HardwareOverlaysPreferenceController.java',
                'limitations': ['Sequential live playback, not identical decoded buffers',
                                'TimeStats counters span complete runner plus sampling overhead',
                                'Client composition is a path choice, not proof of physical GPU utilization',
                                'No WAN, V50, optical touch or acoustic AV timing']}
    output = args.output_dir/'trial.json'

    def operation():
        for label, flag in [('default-before', 0), ('client-gpu', 1), ('default-restored', 0)]:
            require_no_source_capture()
            write_flag(adb, flag)
            time.sleep(1)
            before = collect(adb, 'emulator-5556')
            path = args.output_dir/label
            command = [sys.executable, str(ROOT/'scripts/probes/run_surface_hint_matrix.py'),
                       '--packetizer', str(args.packetizer), '--encoder', str(args.encoder),
                       '--output-dir', str(path), '--rounds', '1', '--seconds', '35',
                       '--display-hz', '90', '--buffers', '60', '--hints', '60',
                       '--raw-policies', 'fifo', '--wire-bitrate', '32000000', '--no-trace',
                       '--restart-source', '--source-warmup-seconds', '8',
                       '--source-quality-label', '1080p60-ui']
            start = time.monotonic_ns()
            result = subprocess.run(command, cwd=ROOT, timeout=150)
            after = collect(adb, 'emulator-5556')
            row = {'label': label, 'disable_overlays': read_flag(adb), 'exit_code': result.returncode,
                   'host_start_monotonic_ns': start, 'host_end_monotonic_ns': time.monotonic_ns(),
                   'before': before, 'after': after}
            manifest['runs'].append(row)
            output.write_text(json.dumps(manifest, indent=2)+'\n')
            if result.returncode:
                raise RuntimeError('retained_failed_composition_trial')
        return 0

    try:
        return with_restored_flag(adb, original, operation)
    finally:
        manifest['restoration_verified'] = read_flag(adb) == original
        output.write_text(json.dumps(manifest, indent=2)+'\n')
        print(json.dumps({'restoration_verified': manifest['restoration_verified'],
                          'runs': len(manifest['runs'])}), flush=True)


if __name__ == '__main__':
    raise SystemExit(main())
