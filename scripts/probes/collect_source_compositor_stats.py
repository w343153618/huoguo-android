#!/usr/bin/env python3
"""Numeric SurfaceFlinger/TimeStats snapshot for the existing source video only.

Does not enable/disable/reset statistics. Raw dumps stay in memory. Counters
are cumulative and must be differenced between matching snapshots of one boot.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

PACKAGE = 'app.morphe.android.youtube'
FIELDS = {'totalFrames', 'droppedFrames', 'lateAcquireFrames', 'badDesiredPresentFrames',
          'totalTimelineFrames', 'jankyFrames', 'sfLongCpuJankyFrames', 'sfLongGpuJankyFrames',
          'sfUnattributedJankyFrames', 'appUnattributedJankyFrames', 'sfSchedulingJankyFrames',
          'sfPredictionErrorJankyFrames', 'appBufferStuffingJankyFrames', 'frameRate', 'averageFPS'}
HISTOGRAMS = {'present2present', 'latch2present', 'desired2present', 'present2presentDelta',
              'acquire2present', 'post2present', 'post2acquire'}
GLOBAL_FIELDS = {'totalFrames', 'missedFrames', 'clientCompositionFrames',
                 'clientCompositionReusedFrames', 'compositionStrategyChanges',
                 'refreshRateSwitches', 'displayOnTime', 'presentToPresentTotal'}


def parse_global_timestats(raw):
    if not isinstance(raw, str) or len(raw.encode()) > 4*1024*1024:
        raise ValueError('bounded_timestats_required')
    result = {}
    for line in raw.splitlines():
        if re.match(r'\s*layerName\s*=', line):
            break
        match = re.fullmatch(r'\s*([A-Za-z][A-Za-z0-9]*)\s*=\s*([0-9]+)\s*', line)
        if match and match[1] in GLOBAL_FIELDS:
            result[match[1]] = int(match[2])
    return result


def parse_timestats(raw):
    if not isinstance(raw, str) or len(raw.encode()) > 4*1024*1024:
        raise ValueError('bounded_timestats_required')
    layers, layer, histogram = [], None, None
    for line in raw.splitlines():
        match = re.match(r'\s*layerName\s*=\s*(.*)$', line)
        if match:
            value = match[1]
            selected = PACKAGE in value and 'SurfaceView' in value
            layer = {'layer_identity_sha256': hashlib.sha256(value.encode()).hexdigest(),
                     'fields': {}, 'histograms_ms': {}} if selected else None
            if layer is not None:
                layers.append(layer)
            histogram = None
            continue
        if layer is None:
            continue
        match = re.match(r'\s*([A-Za-z][A-Za-z0-9]*)\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*$', line)
        if match and match[1] in FIELDS:
            layer['fields'][match[1]] = float(match[2]) if '.' in match[2] else int(match[2])
        match = re.match(r'\s*([A-Za-z0-9]+) histogram is as below:\s*$', line)
        if match:
            histogram = match[1] if match[1] in HISTOGRAMS else None
            continue
        if histogram and line.strip():
            if re.fullmatch(r'(?:\s*[0-9]+ms=[0-9]+\s*)+', line):
                layer['histograms_ms'][histogram] = {str(int(k)): int(v) for k,v in re.findall(r'([0-9]+)ms=([0-9]+)',line)}
            histogram = None
    return layers


def parse_compositor(raw):
    # The full dump may contain window/layer names. Only fixed numeric fields
    # from display scheduling escape this function.
    result = {}
    for name, pattern in {
        'render_hz': r'\brenderRate=([0-9.]+) Hz',
        'physical_vsync_hz': r'activeMode=\{[^\n]*\bvsyncRate=([0-9.]+) Hz',
        'missed_total': r'Total missed frame count:\s*([0-9]+)',
        'missed_hwc': r'HWC missed frame count:\s*([0-9]+)',
        'missed_gpu': r'GPU missed frame count:\s*([0-9]+)',
    }.items():
        match = re.search(pattern, raw)
        result[name] = (float(match[1]) if name.endswith('_hz') else int(match[1])) if match else None
    return result


def collect(adb, serial):
    report = {'schema':1, 'scope':'Source video TimeStats cumulative counters; not phone/WAN/optical metrics',
              'host_start_monotonic_ns':time.monotonic_ns(), 'raw_dumps_saved':False}
    for key, command in [('compositor', ['dumpsys','SurfaceFlinger']),
                         ('video_layers', ['dumpsys','SurfaceFlinger','--timestats','-dump'])]:
        output = subprocess.run([str(adb),'-s',serial,'shell',*command],
                                capture_output=True,text=True,timeout=15)
        if output.returncode or len(output.stdout.encode()) > 4*1024*1024:
            raise ValueError('bounded_compositor_query_failed')
        report[key] = parse_compositor(output.stdout) if key=='compositor' else parse_timestats(output.stdout)
        if key == 'video_layers':
            report['timestats_global'] = parse_global_timestats(output.stdout)
    report['host_end_monotonic_ns'] = time.monotonic_ns()
    report['limitations'] = ['TimeStats is optional instrumentation with observer overhead',
        'Cumulative counters need same-boot, same-layer subtraction',
        'Histogram buckets are quantized and cannot identify a specific decoded PTS',
        'A zero timeline-frame counter means no supported timeline classification for this layer, not zero video jank']
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--serial',default='emulator-5556')
    args=parser.parse_args()
    if args.output.exists() or not re.fullmatch(r'emulator-[0-9]+',args.serial):
        parser.error('fresh output and local emulator required')
    adb=Path.home()/'Library/Android/sdk/platform-tools/adb'
    report=collect(adb,args.serial)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as out:
        json.dump(report,out,indent=2);out.write('\n')
    print(json.dumps({'compositor':report['compositor'],'video_layers':len(report['video_layers']),
                      'video_fields':[x['fields'] for x in report['video_layers']]}))


if __name__=='__main__':
    main()
