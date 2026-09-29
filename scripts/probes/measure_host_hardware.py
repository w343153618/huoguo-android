#!/usr/bin/env python3
"""Bounded synthetic Emulator RGBA -> required VideoToolbox -> H264 measurement.

Runs only on the emulator host, with local discovery authentication. Requires
the synthetic DiagnosticSourceActivity in front and no existing scrcpy stream.
Pixels and compressed video stay in memory; reports contain metrics only.
This does not enable a production backend or measure Internet/phone latency.
"""
import argparse
import hashlib
import json
import os
import pathlib
import re
import resource
import statistics
import struct
import subprocess
import threading
import time

import grpc
import emulator_controller_pb2 as proto
import emulator_controller_pb2_grpc as proto_grpc
import emulator_grpc_probe as capture
import measure_local_encoder as software


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--discovery', required=True, type=pathlib.Path)
    parser.add_argument('--encoder', required=True, type=pathlib.Path)
    parser.add_argument('--duration', type=float, default=10)
    parser.add_argument('--fps', type=int, choices=(30, 60), default=30)
    parser.add_argument('--serial', default='emulator-5554')
    parser.add_argument('--avd', default='phone17-root')
    args = parser.parse_args()
    if not 3 <= args.duration <= 20 or not args.encoder.is_file():
        parser.error('requires a compiled encoder and a 3-20 second duration')
    if not re.fullmatch(r'emulator-[0-9]+', args.serial) or not re.fullmatch(r'[a-zA-Z0-9_-]+', args.avd):
        parser.error('requires a local emulator serial and plain AVD name')
    software.SERIAL = args.serial
    pid = software.qemu_pid(args.avd, args.serial)
    properties = capture.read_discovery(args.discovery)
    target = capture.target_from_discovery(properties, None)
    token = properties.get('grpc.token')
    if not token:
        raise RuntimeError('requires authenticated localhost discovery')
    if 'com.genymobile.scrcpy.Server' in software.adb('shell', 'ps', '-A', '-o', 'NAME,ARGS'):
        raise RuntimeError('existing stream active; measurement refused')
    if not capture.synthetic_foreground(pathlib.Path(software.ADB), software.SERIAL):
        raise RuntimeError('synthetic scene must be focused')
    metadata = (('authorization', 'Bearer ' + token),)
    channel = grpc.insecure_channel(target, options=[('grpc.max_receive_message_length', 64 * 1024 * 1024)])
    process = subprocess.Popen([str(args.encoder), '--fps', str(args.fps), '--bitrate', '4000000',
                                '--max-frames', '2000', '--max-seconds', str(args.duration + 5)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    packets = []
    compressed = bytearray()
    dimensions = []
    reader_errors = []

    def receive():
        def read(count):
            data = process.stdout.read(count)
            if len(data) != count:
                raise EOFError('video pipe ended')
            return data
        try:
            if read(4) != b'h264':
                raise ValueError('invalid H264 codec header')
            marker, width, height = struct.unpack('>III', read(12))
            if marker != 0x80000000 or [width, height] != [540, 1200]:
                raise ValueError('unexpected physical video dimensions')
            dimensions.extend([width, height])
            while True:
                header = process.stdout.read(12)
                if not header:
                    break
                if len(header) != 12:
                    raise EOFError('truncated video header')
                flagged_pts, length = struct.unpack('>QI', header)
                if length > 8 * 1024 * 1024:
                    raise ValueError('oversized video packet')
                payload = read(length)
                if len(compressed) + length > 32 * 1024 * 1024:
                    raise ValueError('bounded in-memory decode validation limit exceeded')
                compressed.extend(payload)
                if not flagged_pts & (1 << 62):
                    pts = flagged_pts & ((1 << 61) - 1)
                    packets.append((time.monotonic(), pts, length, (time.time_ns() // 1000 - pts) / 1000))
        except Exception as exc:
            reader_errors.append(type(exc).__name__ + ': ' + str(exc))

    reader = threading.Thread(target=receive, daemon=True)
    reader.start()
    stub = proto_grpc.EmulatorControllerStub(channel)
    request = proto.ImageFormat(format=proto.ImageFormat.RGBA8888, width=540, height=1200, display=0)
    begin = time.monotonic()
    start = begin + 2
    end = start + args.duration
    samples = []
    source_hashes = set()
    duplicate_timestamps = 0
    last_pts = -1
    cpu_start = None
    call = stub.streamScreenshot(request, timeout=args.duration + 5, metadata=metadata)
    try:
        for image in call:
            now = time.monotonic()
            if now >= end:
                break
            width, height, size, seq, pts, age = capture.frame_info(image)
            if not size:
                continue
            if [width, height] != [540, 1200] or size != 540 * 1200 * 4:
                raise ValueError('invalid complete RGBA capture')
            if pts <= last_pts:
                duplicate_timestamps += 1
                continue
            if now >= start and cpu_start is None:
                cpu_start = (software.guest_cpu(), software.process_cpu_seconds(pid),
                             resource.getrusage(resource.RUSAGE_SELF), time.monotonic())
            process.stdin.write(struct.pack('>IIQI', width, height, pts, size))
            process.stdin.write(image.image)
            process.stdin.flush()
            last_pts = pts
            if start <= now <= end:
                samples.append((now, seq, pts, age))
                source_hashes.add(hashlib.blake2b(image.image, digest_size=8).digest())
    except grpc.RpcError as exc:
        if exc.code() != grpc.StatusCode.DEADLINE_EXCEEDED:
            raise RuntimeError('localhost screenshot stream failed: ' + exc.code().name) from None
    finally:
        call.cancel()
        channel.close()
        process.stdin.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        reader.join(timeout=3)
    stderr = process.stderr.read().decode()
    native = [json.loads(line) for line in stderr.splitlines() if line.startswith('{')]
    if process.returncode or reader_errors or not native or not native[0].get('using_hardware'):
        raise RuntimeError('native encoder validation failed: ' + json.dumps({'native': native, 'reader': reader_errors}))
    selected = [p for p in packets if start <= p[0] <= end]
    gaps = [(b[0] - a[0]) * 1000 for a, b in zip(selected, selected[1:])]
    report = {
        'probe': 'emulator-real-capture-hardware-v1', 'video_size': dimensions,
        'source_scene_fps': 30, 'hardware_encoder_expected_fps': args.fps,
        'warmup_seconds': 2, 'measurement_seconds': args.duration,
        'capture_frames': len(samples), 'capture_fps': round(len(samples) / args.duration, 3),
        'unique_captured_images': len(source_hashes), 'duplicate_capture_timestamps': duplicate_timestamps,
        'encoded_frames': len(selected), 'encoded_fps': round(len(selected) / args.duration, 3),
        'capture_age_p50_ms': capture.percentile([p[3] for p in samples if p[3] is not None], .5),
        'capture_age_p95_ms': capture.percentile([p[3] for p in samples if p[3] is not None], .95),
        'estimated_source_to_h264_pipe_age_p50_ms': capture.percentile([p[3] for p in selected], .5),
        'estimated_source_to_h264_pipe_age_p95_ms': capture.percentile([p[3] for p in selected], .95),
        'encoded_gap_p50_ms': capture.percentile(gaps, .5), 'encoded_gap_p95_ms': capture.percentile(gaps, .95),
        'encoded_gaps_over_50ms': sum(p > 50 for p in gaps),
        'actual_mbps': round(sum(p[2] for p in selected) * 8 / args.duration / 1e6, 3),
        'native_encoder_metrics_include_warmup': native[0],
        'pixel_or_video_persistence': False,
        'scope': 'local raw capture and hardware encode only; excludes audio, network transit, phone decoding and display',
    }
    if cpu_start:
        (total0, idle0), qemu0, usage0, time0 = cpu_start
        total1, idle1 = software.guest_cpu()
        qemu1 = software.process_cpu_seconds(pid)
        usage1 = resource.getrusage(resource.RUSAGE_SELF)
        elapsed = time.monotonic() - time0
        report['cpu_observation_seconds_including_drain'] = round(elapsed, 3)
        report['guest_cpu_busy_percent_all_vcpus'] = round(100 * (1 - (idle1 - idle0) / (total1 - total0)), 3)
        report['host_qemu_cpu_percent_one_core_equals_100'] = round((qemu1 - qemu0) * 100 / elapsed, 3)
        report['capture_process_cpu_percent_one_core_equals_100'] = round(100 * (
            usage1.ru_utime + usage1.ru_stime - usage0.ru_utime - usage0.ru_stime) / elapsed, 3)
    for name, command in [('ffprobe', ['-v', 'error', '-f', 'h264', '-i', 'pipe:0',
                                     '-show_entries', 'stream=width,height,codec_name', '-of', 'json']),
                          ('ffmpeg', ['-v', 'error', '-f', 'h264', '-i', 'pipe:0', '-f', 'null', '-'])]:
        executable = pathlib.Path('/opt/homebrew/bin') / name
        result = subprocess.run([str(executable), *command], input=bytes(compressed), capture_output=True, timeout=30)
        report[name + '_decode_exit'] = result.returncode
        if name == 'ffprobe' and not result.returncode:
            report['decoded_stream'] = json.loads(result.stdout)
        if result.returncode:
            raise RuntimeError('local H264 decode validation failed')
    if not capture.synthetic_foreground(pathlib.Path(software.ADB), software.SERIAL):
        raise RuntimeError('synthetic scene changed during measurement')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
