#!/usr/bin/env python3
"""Measure local emulator frame changes during an already playing test video.

Only timing and hashes are kept. Pixels and gRPC credentials are never printed.
"""
import argparse
import hashlib
import json
import pathlib
import subprocess
import time

import grpc
import emulator_controller_pb2 as proto
import emulator_controller_pb2_grpc as proto_grpc
from emulator_grpc_probe import frame_info, percentile, read_discovery, target_from_discovery


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--discovery', type=pathlib.Path, required=True)
    parser.add_argument('--duration', type=float, default=10)
    parser.add_argument('--label', required=True)
    parser.add_argument('--start-local-video', action='store_true')
    args = parser.parse_args()
    if not 3 <= args.duration <= 15:
        parser.error('duration must be 3 to 15 seconds')
    adb = str(pathlib.Path.home() / 'Library/Android/sdk/platform-tools/adb')
    if args.start_local_video:
        subprocess.run([adb, '-s', 'emulator-5554', 'shell', 'am', 'force-stop', 'org.videolan.vlc'],
                       check=True, capture_output=True, timeout=5)
        subprocess.run([adb, '-s', 'emulator-5554', 'shell', 'am', 'start', '-a',
                        'android.intent.action.VIEW', '-d',
                        'file:///sdcard/Movies/local-video-loop-24s.mp4', '-t', 'video/mp4',
                        '-p', 'org.videolan.vlc'], check=True, capture_output=True, timeout=5)
        time.sleep(1)
    focus = subprocess.run([adb, '-s', 'emulator-5554', 'shell', 'dumpsys', 'window'],
                           capture_output=True, text=True, check=True, timeout=5).stdout
    if not any('org.videolan.vlc' in row for row in focus.splitlines() if 'mCurrentFocus=' in row):
        raise RuntimeError('VLC must be foreground')
    media = subprocess.run([adb, '-s', 'emulator-5554', 'shell', 'dumpsys', 'media_session'],
                           capture_output=True, text=True, check=True, timeout=5).stdout
    if not any('state=PlaybackState {state=PLAYING' in row for row in media.splitlines()):
        raise RuntimeError('VLC playback is not active')
    props = read_discovery(args.discovery)
    token = props.get('grpc.token')
    if not token:
        raise RuntimeError('authenticated local emulator endpoint required')
    metadata = (('authorization', 'Bearer ' + token),)
    channel = grpc.insecure_channel(target_from_discovery(props, None),
                                    options=[('grpc.max_receive_message_length', 64 * 1024 * 1024)])
    stub = proto_grpc.EmulatorControllerStub(channel)
    request = proto.ImageFormat(format=proto.ImageFormat.RGBA8888, width=720, height=1280, display=0)
    start = time.monotonic()
    end = start + args.duration
    call = stub.streamScreenshot(request, timeout=args.duration + 2, metadata=metadata)
    arrivals, changes, capture_ages = [], [], []
    last_hash = None
    count = duplicate = 0
    try:
        for image in call:
            now = time.monotonic()
            if now > end:
                break
            width, height, size, _, _, age = frame_info(image)
            if (width, height, size) != (720, 1280, 720 * 1280 * 4):
                raise ValueError('incomplete or unexpected screenshot frame')
            count += 1
            arrivals.append(now)
            if age is not None:
                capture_ages.append(age)
            frame_hash = hashlib.blake2b(image.image, digest_size=8).digest()
            if frame_hash == last_hash:
                duplicate += 1
            else:
                changes.append(now)
                last_hash = frame_hash
    except grpc.RpcError as exc:
        if exc.code() != grpc.StatusCode.DEADLINE_EXCEEDED:
            raise
    finally:
        call.cancel()
        channel.close()
    gaps = [(right - left) * 1000 for left, right in zip(changes, changes[1:])]
    report = {
        'label': args.label, 'scope': 'local 720x1280 VLC video capture, no encoder or network',
        'duration_seconds': args.duration, 'captured_frames': count,
        'capture_fps': round(count / args.duration, 3),
        'consecutive_changed_frames': len(changes),
        'changed_fps': round(len(changes) / args.duration, 3),
        'consecutive_duplicates': duplicate,
        'change_gap_p50_ms': percentile(gaps, .5),
        'change_gap_p95_ms': percentile(gaps, .95),
        'change_gaps_over_50ms': sum(gap > 50 for gap in gaps),
        'capture_age_p95_ms': percentile(capture_ages, .95),
        'pixel_persistence': False,
    }
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
