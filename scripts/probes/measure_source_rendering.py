#!/usr/bin/env python3
"""Bounded source drawing / frame-stage comparison on the emulator host.

Uses only the existing synthetic Activity and loopback capture. Refuses active
scrcpy sessions. Optional renderer changes are transient and always restored.
Does not change root modules, credentials, VM resources or networking.
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys
import time
import uuid

import measure_local_encoder as local
from emulator_grpc_probe import percentile

PACKAGE = 'local.remoteandroid.benchmark'


def frame_stages(text):
    rows = []
    columns = None
    for line in text.splitlines():
        if line.startswith('Flags,'):
            columns = line.rstrip(',').split(',')
        elif columns and re.match(r'^\d+,', line):
            values = line.rstrip(',').split(',')
            if len(values) == len(columns):
                row = dict(zip(columns, map(int, values)))
                if row['Flags'] == 0:
                    rows.append(row)
        elif line.startswith('---PROFILEDATA---'):
            columns = None
    pairs = {
        'total': ('IntendedVsync', 'FrameCompleted'),
        'vsync_adjustment': ('IntendedVsync', 'Vsync'),
        'ui_work': ('HandleInputStart', 'SyncQueued'),
        'render_queue': ('SyncQueued', 'SyncStart'),
        'draw_commands': ('IssueDrawCommandsStart', 'SwapBuffers'),
        'dequeue': (None, 'DequeueBufferDuration'),
        'gpu_fence': ('CommandSubmissionCompleted', 'GpuCompleted'),
    }
    result = {'valid_recent_rows': len(rows), 'stages_ms': {}}
    for name, (start, end) in pairs.items():
        samples = []
        for row in rows:
            if end not in row or (start and start not in row):
                continue
            delta = row[end] - row[start] if start else row[end]
            # Exclude absent / pending fences and uninitialized sentinel values.
            if 0 <= delta < 1_000_000_000:
                samples.append(delta / 1e6)
        result['stages_ms'][name] = {
            'count': len(samples), 'p50': percentile(samples, .5),
            'p95': percentile(samples, .95), 'p99': percentile(samples, .99),
        }
    summary = {}
    for line in text.splitlines():
        if re.match(r'^(Total frames rendered|Janky frames:|Number |\d+th percentile:|Pipeline=)', line):
            key, _, value = line.partition(':')
            summary[key] = value.strip() if value else line
    result['summary'] = summary
    return result


def source_events(run_id):
    logs = local.adb('shell', 'logcat', '-d', '-v', 'epoch', '-s', 'DiagnosticSource:I', '*:S')
    lines = [x for x in logs.splitlines() if run_id in x]
    starts = [x for x in lines if 'synthetic_start' in x]
    stops = [x for x in lines if 'synthetic_stop' in x]
    result = {'run_id': run_id, 'lifecycle_complete': bool(starts and stops)}
    if starts and stops:
        elapsed = float(stops[-1].split()[0]) - float(starts[-1].split()[0])
        fields = dict(re.findall(r'(source_frame|unique_drawn_frames|skipped_source_ticks|callback_frames|repeated_target_ticks|hardware_canvas)=([\w]+)', stops[-1]))
        result.update({k: (v == 'true' if k == 'hardware_canvas' else int(v)) for k, v in fields.items()})
        result['source_elapsed_seconds'] = round(elapsed, 3)
        result['source_draw_fps_including_startup'] = round(result['unique_drawn_frames'] / elapsed, 3)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', required=True)
    parser.add_argument('--duration', type=float, default=10)
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--fps', type=int, choices=(30, 60, 120), default=30)
    parser.add_argument('--avd', default='phone17-root', help='running AVD name for hardware capture')
    parser.add_argument('--source-clock', choices=('floor', 'nearest'), default='floor')
    parser.add_argument('--capture', choices=('none', 'hardware'), default='none')
    parser.add_argument('--renderer', choices=('skiagl', 'skiavk'))
    parser.add_argument('--encoder', type=pathlib.Path)
    parser.add_argument('--discovery', type=pathlib.Path)
    args = parser.parse_args()
    if not 3 <= args.duration <= 20 or not 1 <= args.repeats <= 3:
        parser.error('requires 1-3 bounded samples of 3-20 seconds')
    if args.capture == 'hardware' and (not args.encoder or not args.discovery):
        parser.error('hardware capture needs local encoder and discovery paths')
    if 'com.genymobile.scrcpy.Server' in local.adb('shell', 'ps', '-A', '-o', 'NAME,ARGS'):
        raise RuntimeError('active stream; source comparison refused')
    original_renderer = local.adb('shell', 'getprop', 'debug.hwui.renderer').strip()
    report = {'label': args.label, 'capture': args.capture, 'scene_target_fps': args.fps,
              'source_clock': args.source_clock, 'requested_renderer': args.renderer,
              'original_renderer': original_renderer, 'physical_size': local.adb('shell', 'wm', 'size').strip(),
              'physical_density': local.adb('shell', 'wm', 'density').strip(),
              'scope': 'synthetic source and local video only; excludes phone/WAN/audio/touch', 'samples': []}
    try:
        if args.renderer:
            local.adb('shell', 'su', '0', 'setprop', 'debug.hwui.renderer', args.renderer)
        for _ in range(args.repeats):
            if 'com.genymobile.scrcpy.Server' in local.adb('shell', 'ps', '-A', '-o', 'NAME,ARGS'):
                raise RuntimeError('stream started; source comparison stopped')
            run_id = str(uuid.uuid4())
            local.adb('shell', 'input', 'keyevent', '224')
            local.adb('shell', 'am', 'force-stop', PACKAGE)
            local.adb('shell', 'am', 'start', '-W', '-n', local.SCENE, '--es', 'run_id', run_id,
                      '--ei', 'source_fps', str(args.fps), '--es', 'source_clock', args.source_clock)
            time.sleep(1)
            local.adb('shell', 'dumpsys', 'gfxinfo', PACKAGE, 'reset')
            sample = {}
            if args.capture == 'hardware':
                child = subprocess.run([sys.executable, str(pathlib.Path(__file__).with_name('measure_host_hardware.py')),
                                        '--discovery', str(args.discovery), '--encoder', str(args.encoder),
                                        '--duration', str(args.duration), '--fps', str(args.fps), '--avd', args.avd,
                                        '--source-fps', str(args.fps)],
                                       capture_output=True, text=True, timeout=args.duration + 35)
                if child.returncode:
                    raise RuntimeError('local hardware capture failed: ' + child.stderr[-800:])
                sample['hardware_capture'] = json.loads(child.stdout)
            else:
                time.sleep(args.duration)
            sample['frame_stages'] = frame_stages(local.adb('shell', 'dumpsys', 'gfxinfo', PACKAGE, 'framestats'))
            local.adb('shell', 'input', 'keyevent', '4')
            time.sleep(.3)
            sample['source'] = source_events(run_id)
            report['samples'].append(sample)
    finally:
        local.adb('shell', 'am', 'force-stop', PACKAGE)
        if args.renderer:
            # One shell string is necessary when restoring an originally empty property.
            if not re.fullmatch(r'[a-zA-Z0-9_-]*', original_renderer):
                raise RuntimeError('unexpected original renderer; refusing unsafe restoration')
            local.adb('shell', 'su 0 setprop debug.hwui.renderer "' + original_renderer + '"')
        report['renderer_restored'] = local.adb('shell', 'getprop', 'debug.hwui.renderer').strip() == original_renderer
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
