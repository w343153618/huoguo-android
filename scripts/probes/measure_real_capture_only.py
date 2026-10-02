#!/usr/bin/env python3
"""Bounded real-video gRPC capture without VideoToolbox, audio or phone media.

Reads the already playing Morphe YouTube session and the existing local AVD
discovery. Never starts a player, changes VM/display settings, saves pixels or
prints discovery credentials. Its gRPC ImageFormat matches hardware_stream:
RGBA8888, a square min(max-size, physical longest side) cap and display zero.
The FPS option labels the comparison target; streamScreenshot has no FPS limit.
"""
import argparse
from datetime import datetime, timezone
import importlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.probes.emulator_grpc_probe import read_discovery, target_from_discovery
from scripts.probes.source_playback_state import collect, TARGET_PACKAGE

MAX_FRAMES = 15000


def distribution(values):
    values = sorted(value for value in values if type(value) in (int, float) and math.isfinite(value))
    if not values:
        return {'count': 0}
    def percentile(q):
        index = (len(values)-1)*q
        low = int(index)
        high = min(low+1, len(values)-1)
        return round(values[low]+(values[high]-values[low])*(index-low), 3)
    return dict(count=len(values), p50=percentile(.5), p95=percentile(.95),
                p99=percentile(.99), max=round(values[-1], 3),
                over_50_ms=sum(value > 50 for value in values),
                over_100_ms=sum(value > 100 for value in values))


def playing(state):
    return (state.get('command_ok') is True and state.get('state_known') is True
            and state.get('state') == 3 and type(state.get('speed')) in (int, float)
            and math.isfinite(state['speed']) and state['speed'] > 0)


def focus_matches(raw):
    """Return only a boolean; unrelated window text never enters the report."""
    if not isinstance(raw, bytes) or len(raw) > 1024*1024:
        return False
    for row in raw.decode(errors='replace').splitlines():
        if ('mCurrentFocus=' in row or 'mFocusedApp=' in row) and re.search(
                r'(?<![\w.])'+re.escape(TARGET_PACKAGE)+r'/', row):
            return True
    return False


def frame_row(image, return_ns, return_unix_us, expected_format):
    """Whitelist numeric metadata only; reject malformed RGBA before recording."""
    width = int(image.format.width or image.width)
    height = int(image.format.height or image.height)
    size = len(image.image)
    if (not 2 <= width <= 8192 or not 2 <= height <= 8192 or width % 2 or height % 2
            or size != width*height*4 or image.format.format != expected_format):
        raise ValueError('invalid_rgba')
    pts, seq = int(image.timestampUs), int(image.seq)
    if not 0 <= pts <= 2**63-1 or not 0 <= seq <= 2**32-1:
        raise ValueError('invalid_frame_metadata')
    return dict(seq=seq, source_pts_us=pts, grpc_return_monotonic_ns=return_ns,
                grpc_return_unix_us=return_unix_us, width=width, height=height,
                pixel_bytes=size)


def summarize(rows, started_ns, ended_ns):
    elapsed = max(0, (ended_ns-started_ns)/1e9)
    gap_ns = [right['grpc_return_monotonic_ns']-left['grpc_return_monotonic_ns']
              for left, right in zip(rows, rows[1:])]
    pts_gaps = [(right['source_pts_us']-left['source_pts_us'])/1000
                for left, right in zip(rows, rows[1:]) if left['source_pts_us'] and right['source_pts_us']]
    age = [(row['grpc_return_unix_us']-row['source_pts_us'])/1000
           for row in rows if row['source_pts_us']]
    duplicates = missing = resets = 0
    for left, right in zip(rows, rows[1:]):
        delta = (right['seq']-left['seq']) & 0xffffffff
        if delta == 0:
            duplicates += 1
        elif delta >= 0x80000000:
            resets += 1
        elif delta > 1:
            missing += delta-1
    return dict(seconds=round(elapsed, 6), frames=len(rows),
                capture_fps=round(len(rows)/elapsed, 3) if elapsed else None,
                first_frame_after_start_ms=round((rows[0]['grpc_return_monotonic_ns']-started_ns)/1e6, 3) if rows else None,
                grpc_return_gaps_ms=distribution([value/1e6 for value in gap_ns]),
                source_screenshot_pts_gaps_ms=distribution(pts_gaps),
                estimated_screenshot_age_ms=distribution(age),
                negative_screenshot_age_count=sum(value < 0 for value in age),
                duplicate_screenshot_sequence_count=duplicates,
                missing_screenshot_sequence_numbers=missing,
                screenshot_sequence_reset_count=resets)


def discovery_for_avd(avd, selected):
    candidates = [selected] if selected else sorted((Path.home()/'Library/Caches/TemporaryItems/avd/running').glob('pid_*.ini'))
    matches = []
    for candidate in candidates:
        values = read_discovery(candidate)
        if values.get('avd.name') == avd and values.get('grpc.token'):
            matches.append(values)
    if len(matches) != 1:
        raise ValueError('unique_authenticated_avd_discovery_required')
    values = matches[0]
    token = values['grpc.token']
    if not token or any(not 32 <= ord(char) <= 126 for char in token):
        raise ValueError('invalid_discovery_token')
    return target_from_discovery(values, None), token


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, default=Path.home()/'Documents/ChatGPT/others/android-remote/m1-compare')
    parser.add_argument('--discovery', type=Path, help='optional exact existing discovery; never printed or copied')
    parser.add_argument('--serial', default='emulator-5556')
    parser.add_argument('--avd', default='RemoteAndroid17Compare')
    parser.add_argument('--seconds', type=float, default=30)
    parser.add_argument('--max-size', type=int, choices=(960, 1200, 1280, 1600), default=960)
    parser.add_argument('--fps', type=int, choices=(60, 120), default=60,
                        help='comparison target only; no capture FPS throttling or content-FPS claim')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or not 5 <= args.seconds <= 90:
        parser.error('requires bounded 5..90 seconds')
    if not re.fullmatch(r'emulator-\d+', args.serial) or not re.fullmatch(r'[A-Za-z0-9_-]+', args.avd):
        parser.error('requires an existing local emulator selector')
    if args.output.exists():
        parser.error('existing evidence is retained; choose a fresh output')
    adb = Path(os.environ.get('ANDROID_HOME', str(Path.home()/'Library/Android/sdk')))/'platform-tools/adb'
    report = dict(schema=1, probe='real_video_capture_only', timestamp_utc=datetime.now(timezone.utc).isoformat(),
                  scope='Local real-video emulator RGBA capture only; no VT, audio service, network media or phone display',
                  comparison_target_fps=args.fps, max_size=args.max_size,
                  requested_format='RGBA8888', frame_metadata=[], errors=[],
                  limitations=['No content-file FPS verification; requested 60/120 is a comparison target',
                               'No VideoToolbox/audio/phone load; differences do not isolate VT alone',
                               'Source timestamp is emulator screenshot production estimate, not video decoder PTS',
                               'Wall clock screenshot age is not end-to-end latency',
                               'Screenshot sequence gaps are not Internet packet loss',
                               'Use concurrent source SurfaceFlinger sampling to assess real layer cadence'])
    channel = call = None
    token = ''
    rows = report['frame_metadata']
    begin_ns = end_ns = time.monotonic_ns()
    stage = 'preflight'
    try:
        # Refuse existing capture/VT workers rather than disturbing them or
        # silently measuring two screenshot subscribers at once.
        process_list = subprocess.run(['ps', '-axo', 'command='], check=True,
                                      capture_output=True, text=True, timeout=5).stdout
        if any('hardware_stream.py' in row and '--serial '+args.serial in row
               for row in process_list.splitlines()):
            raise RuntimeError('active_hardware_worker')
        report['source_state_before'] = collect(adb, args.serial, 8)
        if not playing(report['source_state_before']):
            raise RuntimeError('selected_real_video_not_playing')
        focus = subprocess.run([str(adb), '-s', args.serial, 'shell', 'dumpsys', 'window'],
                               check=True, capture_output=True, timeout=8)
        report['source_foreground_before'] = focus_matches(focus.stdout)
        if not report['source_foreground_before']:
            raise RuntimeError('selected_player_not_foreground')
        size_read = subprocess.run([str(adb), '-s', args.serial, 'shell', 'wm', 'size'],
                                  check=True, capture_output=True, text=True, timeout=5).stdout
        size = re.search(r'Physical size: (\d+)x(\d+)', size_read)
        if not size:
            raise RuntimeError('physical_size_unavailable')
        width, height = map(int, size.groups())
        if not 2 <= width <= 8192 or not 2 <= height <= 8192:
            raise ValueError('invalid_physical_size')
        cap = min(args.max_size, max(width, height))
        report['physical_size_readback'] = [width, height]
        report['requested_square_cap'] = cap
        target, token = discovery_for_avd(args.avd, args.discovery)
        sys.path.insert(0, str(args.runtime/'hardware/proto'))
        grpc = importlib.import_module('grpc')
        proto = importlib.import_module('emulator_controller_pb2')
        proto_grpc = importlib.import_module('emulator_controller_pb2_grpc')
        channel = grpc.insecure_channel(target, options=[('grpc.max_receive_message_length', 64*1024*1024)])
        grpc.channel_ready_future(channel).result(timeout=4)
        stub = proto_grpc.EmulatorControllerStub(channel)
        request = proto.ImageFormat(format=proto.ImageFormat.RGBA8888, width=cap, height=cap, display=0)
        stage = 'capture'
        begin_ns = time.monotonic_ns()
        call = stub.streamScreenshot(request, timeout=args.seconds,
                                     metadata=(('authorization', 'Bearer '+token),))
        try:
            for image in call:
                returned_ns, returned_unix_us = time.monotonic_ns(), time.time_ns()//1000
                if returned_ns-begin_ns > args.seconds*1e9:
                    break
                rows.append(frame_row(image, returned_ns, returned_unix_us, request.format))
                # Do not retain the protobuf/image after numeric extraction.
                del image
                if len(rows) >= MAX_FRAMES:
                    raise RuntimeError('frame_limit_reached')
        except grpc.RpcError as error:
            if error.code() != grpc.StatusCode.DEADLINE_EXCEEDED:
                raise
        finally:
            end_ns = time.monotonic_ns()
            call.cancel()
        stage = 'source_after'
        report['source_state_after'] = collect(adb, args.serial, 8)
        if not playing(report['source_state_after']):
            raise RuntimeError('selected_real_video_not_playing_after')
        report['status'] = 'PASS' if rows and end_ns-begin_ns >= args.seconds*.95*1e9 else 'FAIL'
    except Exception as error:
        # grpc details, arbitrary filesystem errors and discovery text may
        # contain credentials. Never export them; stage+type are sufficient.
        report['errors'].append(dict(stage=stage, error_type=type(error).__name__))
        report['status'] = 'FAIL'
        if stage == 'capture':
            end_ns = time.monotonic_ns()
    finally:
        if call is not None:
            call.cancel()
        if channel is not None:
            channel.close()
        token = None
    report['measurement_start_monotonic_ns'] = begin_ns
    report['measurement_end_monotonic_ns'] = end_ns
    report['summary'] = summarize(rows, begin_ns, end_ns)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as output:
        output.write(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    print(json.dumps(dict(status=report['status'], errors=report['errors'], summary=report['summary']), allow_nan=False))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
