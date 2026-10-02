#!/usr/bin/env python3
"""Poll a selected app's SurfaceFlinger video layer, without saving screen pixels.

The present timestamps measure the source layer, not the phone or network. The
first poll is a baseline: historical ring entries are excluded from the window.
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import time
from pathlib import Path


def percentile(values, q):
    if not values:
        return None
    a = sorted(values)
    n = (len(a) - 1) * q
    i = int(n)
    return round(a[i] + (a[min(i + 1, len(a) - 1)] - a[i]) * (n - i), 3)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--serial', default='emulator-5556')
    p.add_argument('--package', default='com.google.android.youtube')
    p.add_argument('--seconds', type=float, default=45)
    p.add_argument('--wait-layer', type=float, default=0, help='bounded wait for the selected app stream to start')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if not 2 <= args.seconds <= 300:
        p.error('duration must be between 2 and 300 seconds')
    if not 0 <= args.wait_layer <= 30:
        p.error('layer wait must be between 0 and 30 seconds')
    adb = Path(os.environ.get('ANDROID_HOME', str(Path.home() / 'Library/Android/sdk'))) / 'platform-tools/adb'

    def shell(command):
        return subprocess.run([str(adb), '-s', args.serial, 'shell', command],
                              capture_output=True, text=True, check=True, timeout=8).stdout

    def pick_layer():
        candidates = []
        for line in shell('dumpsys SurfaceFlinger --list').splitlines():
            if args.package in line and 'SurfaceView' in line and '(BLAST)' in line:
                # New Android prints RequestedLayerState{NAME parentId=...}; old
                # Android emits NAME directly. Only the actual layer name is sent.
                if line.startswith('RequestedLayerState{'):
                    line = line[len('RequestedLayerState{'):].split(' parentId=', 1)[0]
                candidates.append(line.strip())
        return candidates[-1] if candidates else None

    layer = pick_layer()
    wait_until = time.monotonic() + args.wait_layer
    while not layer and time.monotonic() < wait_until:
        time.sleep(.5)
        layer = pick_layer()
    if not layer:
        raise SystemExit('No selected app video SurfaceView layer found')
    start_ns = time.monotonic_ns()
    baseline_max = None
    seen = set()
    polls = []
    vsync_ns = None
    while (time.monotonic_ns() - start_ns) / 1e9 < args.seconds:
        lines = shell('dumpsys SurfaceFlinger --latency ' + shlex.quote(layer)).splitlines()
        stamps = set()
        if lines and lines[0].strip().isdigit():
            vsync_ns = int(lines[0].strip())
        for line in lines[1:]:
            parts = line.split()
            if len(parts) != 3 or not all(re.fullmatch(r'\d+', x) for x in parts):
                continue
            actual = int(parts[1])
            if 0 < actual < 2**63 - 1:
                stamps.add(actual)
        if baseline_max is None and stamps:
            baseline_max = max(stamps)
        fresh = {x for x in stamps if baseline_max is not None and x > baseline_max}
        before = len(seen)
        seen.update(fresh)
        polls.append({'elapsed_s': round((time.monotonic_ns()-start_ns)/1e9, 3),
                      'new_presentations': len(seen)-before, 'ring_presentations': len(stamps)})
        time.sleep(.5)
    ordered = sorted(seen)
    gaps = [(b-a)/1e6 for a,b in zip(ordered, ordered[1:])]
    elapsed = (time.monotonic_ns()-start_ns)/1e9
    report = {'scope':'source app SurfaceFlinger layer actual present timestamps; NOT remote display FPS',
              'serial':args.serial, 'package':args.package, 'layer':layer,
              'unix_ms':time.time_ns()//1_000_000, 'seconds':round(elapsed,3),
              'display_vsync_ns':vsync_ns, 'presented_frames':len(seen),
              'fps':round(len(seen)/elapsed,3),
              'cadence_fps':round((len(ordered)-1)*1e9/(ordered[-1]-ordered[0]),3) if len(ordered)>1 else None,
              'gaps_ms': {'p50':percentile(gaps,.5),'p95':percentile(gaps,.95),
                          'p99':percentile(gaps,.99),'max':max(gaps) if gaps else None,
                          'over_50':sum(x>50 for x in gaps), 'over_100':sum(x>100 for x in gaps)},
              'polls':polls, 'presentation_ns':ordered,
              'limitations':'A changed/recreated layer needs a fresh run. Ring capacity and blocked ADB can miss entries. No content hash or media file frame-rate assertion.'}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('polls','presentation_ns')},ensure_ascii=False))


if __name__ == '__main__':
    main()
