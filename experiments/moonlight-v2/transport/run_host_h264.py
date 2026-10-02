#!/usr/bin/env python3
"""Existing M1 HostSession -> loopback UDP -> ffmpeg, without changing source UI."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from hardware_stream import CONFIG_FLAG, PTS_MASK, HostHardwareSession, read_exact


def distribution(values):
    ordered = sorted(values)
    if not ordered:
        return {'count': 0}
    return {'count': len(ordered), 'p50_ms': ordered[(len(ordered)-1)//2],
            'p95_ms': ordered[min(len(ordered)-1, int(len(ordered)*.95))], 'max_ms': ordered[-1]}


def decoded(path, ffmpeg):
    completed = subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'h264', '-i',
                                str(path), '-f', 'framemd5', '-'], capture_output=True, text=True, timeout=45)
    hashes = [line.rsplit(',', 1)[1].strip() for line in completed.stdout.splitlines()
              if line and not line.startswith('#') and ',' in line]
    diagnostic_lines = [line for line in completed.stderr.splitlines() if line.strip()]
    # Classify messages without storing an unredacted decoder log or pixels.
    kinds = ('error', 'invalid', 'missing', 'reference', 'conceal', 'corrupt', 'no frame')
    return {'exit_code': completed.returncode, 'decoded_frames': len(hashes),
            'unique_frame_md5_count': len(set(hashes)),
            'consecutive_equal_frame_digests': sum(a == b for a, b in zip(hashes, hashes[1:])),
            'frame_md5_sequence_sha256': hashlib.sha256('\n'.join(hashes).encode()).hexdigest(),
            'decoder_diagnostic_line_count': len(diagnostic_lines),
            'decoder_diagnostic_categories': {word: sum(word in line.lower() for line in diagnostic_lines)
                                              for word in kinds}}, hashes


def trace_sps(path, ffmpeg):
    completed = subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'info', '-f', 'h264', '-i',
                                str(path), '-map', '0:v:0', '-c:v', 'copy', '-bsf:v', 'trace_headers',
                                '-f', 'null', '-'], capture_output=True, text=True, timeout=45)
    fields = ('profile_idc', 'level_idc', 'max_num_ref_frames', 'max_num_reorder_frames',
              'max_dec_frame_buffering', 'bitstream_restriction_flag', 'pic_order_cnt_type')
    result = {}
    for field in fields:
        values = re.findall(r'\b'+field+r'\s+[^\r\n=]*=\s*(\d+)', completed.stderr)
        result[field] = sorted(set(map(int, values)))
    return {'trace_exit_code': completed.returncode, 'fields': result,
            'missing_fields': [field for field in fields if not result[field]],
            'interpretation': 'Empty means not found/not signalled, never an inferred zero'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, default=Path.home()/'Documents/ChatGPT/others/android-remote/m1-compare')
    parser.add_argument('--bridge', type=Path, default=Path(tempfile.gettempdir())/'huoguo-udp-native-build/h264_udp_bridge')
    parser.add_argument('--serial', default='emulator-5556')
    parser.add_argument('--avd', default='RemoteAndroid17Compare')
    parser.add_argument('--duration', type=int, default=20, choices=range(5, 61))
    parser.add_argument('--fps', type=int, default=60, choices=(30, 60, 120))
    parser.add_argument('--video-bitrate', type=int, default=4_000_000)
    parser.add_argument('--wire-bitrate', type=int, default=8_000_000)
    parser.add_argument('--mode', default='VBR', choices=('VBR', 'CBR', 'ADAPTIVE_VBR'))
    parser.add_argument('--raw-queue-policy', default='latest', choices=('fifo', 'latest'))
    parser.add_argument('--native-encoder', type=Path, help='isolated compiled encoder; never overwrites the runtime binary')
    parser.add_argument('--low-latency-mode', choices=('true','false'), help='requires an isolated encoder supporting this flag')
    parser.add_argument('--loss', type=float, default=0)
    parser.add_argument('--reorder', type=int, default=0)
    parser.add_argument('--feedback', type=int, default=1, choices=(0, 1))
    parser.add_argument('--source-label', required=True, help='Caller declaration, e.g. real YouTube 60FPS clip')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not (.5e6 <= args.video_bitrate <= 40e6 and .5e6 <= args.wire_bitrate <= 40e6 and
            0 <= args.loss <= 20 and args.reorder >= 0):
        parser.error('bitrate/impairment bounds')
    if args.low_latency_mode is not None and args.native_encoder is None:
        parser.error('low-latency-mode requires explicit native-encoder')
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg or not args.bridge.is_file():
        parser.error('Build bridge and provide existing local ffmpeg first')
    done = threading.Event()
    errors, events, source_frames, sizes = [], [], [], []
    audio_packets = control_bytes = idr_forwarded = source_configs = 0
    session = bridge = None
    threads = []
    control_lock = threading.Lock()
    start = finish = None
    log_path = args.runtime/'hardware/worker.log'
    log_start = log_path.stat().st_size if log_path.exists() else 0

    def launch(name, function):
        def guarded():
            try:
                function()
            except Exception as exc:
                if not done.is_set():
                    errors.append({'component': name, 'exception_type': type(exc).__name__})
                    done.set()
        thread = threading.Thread(target=guarded, name=name, daemon=True)
        thread.start(); threads.append(thread)
        return thread

    # All captured media stays in a private temporary directory and is deleted
    # after decode/hash/header checks. Only statistics go to the project tree.
    with tempfile.TemporaryDirectory(prefix='huoguo-real-host-udp-', dir='/private/tmp') as directory:
        directory = Path(directory)
        original_path, returned_path = directory/'source.h264', directory/'udp-returned.h264'
        original = original_path.open('wb')
        returned = returned_path.open('wb')
        try:
            session = HostHardwareSession(args.runtime, args.serial, args.avd, 1200,
                                          args.video_bitrate, args.fps, args.mode,
                                          raw_queue_policy=args.raw_queue_policy,
                                          native_encoder=args.native_encoder,
                                          low_latency_mode=None if args.low_latency_mode is None else args.low_latency_mode=='true')
            channels = {role: session.channel(role) for role in ('video', 'audio', 'control')}
            bridge = subprocess.Popen([str(args.bridge), str(args.wire_bitrate), str(args.loss),
                                       str(args.reorder), str(args.feedback)],
                                      stdin=subprocess.PIPE, stdout=returned, stderr=subprocess.PIPE)

            def video():
                nonlocal source_configs
                stream = channels['video']
                codec = read_exact(stream, 4)
                if codec != b'h264':
                    raise ValueError('Unexpected codec')
                bridge.stdin.write(codec); bridge.stdin.flush()
                while not done.is_set():
                    first = read_exact(stream, 4)
                    hi = struct.unpack('>I', first)[0]
                    tail = read_exact(stream, 8)
                    if hi & 0x80000000:
                        width, height = struct.unpack('>II', tail)
                        if not 1 <= width <= 8192 or not 1 <= height <= 8192:
                            raise ValueError('Geometry bounds')
                        sizes.append([width, height]); bridge.stdin.write(first+tail)
                    else:
                        low, size = struct.unpack('>II', tail)
                        if not 1 <= size <= 1024*1024:
                            raise ValueError('Access-unit size bounds')
                        payload = read_exact(stream, size)
                        flagged = (hi << 32) | low
                        original.write(payload)
                        if flagged & CONFIG_FLAG:
                            source_configs += 1
                        else:
                            stamp = time.monotonic()
                            source_frames.append({'arrived': stamp, 'pts': flagged & PTS_MASK, 'size': size,
                                                  'source_age_ms': (time.time_ns()//1000-(flagged & PTS_MASK))/1000})
                        bridge.stdin.write(first+tail+payload)
                    bridge.stdin.flush()

            def audio():
                nonlocal audio_packets
                stream = channels['audio']
                codec = struct.unpack('>I', read_exact(stream, 4))[0]
                if codec in (0, 1):
                    return # An audio-disabled source must not stop video measurement.
                while not done.is_set():
                    pts, size = struct.unpack('>QI', read_exact(stream, 12))
                    if size > 1024*1024:
                        raise ValueError('Audio packet bounds')
                    read_exact(stream, size)
                    if not pts & CONFIG_FLAG:
                        audio_packets += 1

            def control():
                nonlocal control_bytes
                while not done.is_set():
                    data = channels['control'].recv(4096)
                    if not data:
                        return
                    control_bytes += len(data) # Never retain clipboard/control contents.

            def bridge_events():
                nonlocal idr_forwarded
                for line in bridge.stderr:
                    if len(line) > 8192:
                        raise ValueError('Native event bounds')
                    value = json.loads(line)
                    if value.get('event') == 'request_idr':
                        if not done.is_set():
                            with control_lock:
                                channels['control'].sendall(b'\x11')
                            idr_forwarded += 1
                    elif value.get('event') == 'summary':
                        events.append(value)

            video_thread = launch('source_video', video)
            launch('audio_drain', audio); launch('control_drain', control)
            launch('native_events', bridge_events)
            start = time.monotonic(); session.start()
            done.wait(args.duration); finish = time.monotonic()
        finally:
            done.set()
            if session:
                session.close()
            for thread in threads:
                if thread.name != 'native_events':
                    thread.join(timeout=5)
            if bridge:
                if bridge.stdin and not bridge.stdin.closed:
                    bridge.stdin.close()
                try:
                    bridge.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    bridge.kill(); bridge.wait(timeout=2)
                for thread in threads:
                    if thread.name == 'native_events':
                        thread.join(timeout=3)
            original.close(); returned.close()

        source_decode, source_hashes = decoded(original_path, ffmpeg)
        returned_decode, returned_hashes = decoded(returned_path, ffmpeg)
        header_trace = trace_sps(original_path, ffmpeg)
        hardware = []
        if log_path.exists():
            with log_path.open('rb') as log:
                log.seek(log_start)
                for line in log:
                    try:
                        value = json.loads(line)
                    except (ValueError, UnicodeDecodeError):
                        continue
                    if value.get('event') == 'ready':
                        hardware.append({key: value[key] for key in ('event', 'using_hardware', 'width', 'height',
                                                                     'profile', 'fps', 'bitrate', 'mode') if key in value})
        elapsed = finish-start if start is not None and finish is not None else None
        gaps = [(b['arrived']-a['arrived'])*1000 for a, b in zip(source_frames, source_frames[1:])]
        pts_gaps = [(b['pts']-a['pts'])/1000 for a, b in zip(source_frames, source_frames[1:])]
        result = {'validation_layer': 'existing_M1_real_source_HostHardwareSession_to_actual_localhost_UDP_to_ffmpeg',
                  'source_label': args.source_label, 'source_label_authority': 'caller declaration; source UI unchanged',
                  'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  'serial': args.serial, 'avd': args.avd, 'raw_queue_policy': args.raw_queue_policy,
                  'target_fps': args.fps, 'video_bitrate_bps': args.video_bitrate,
                  'inner_IP_wire_budget_bps': args.wire_bitrate, 'bitrate_mode': args.mode,
                  'injected_packet_loss_percent': args.loss, 'reorder_every_frames': args.reorder,
                  'idr_feedback_enabled': bool(args.feedback), 'idr_requests_forwarded': idr_forwarded,
                  'measurement_seconds': elapsed, 'source_frames': len(source_frames),
                  'source_encoded_fps': len(source_frames)/elapsed if elapsed else None,
                  'source_codec_configs': source_configs, 'dimensions': sizes,
                  'source_frame_gap': distribution(gaps), 'source_pts_gap': distribution(pts_gaps),
                  'source_age_at_driver': distribution([value['source_age_ms'] for value in source_frames]),
                  'audio_packets_drained': audio_packets, 'control_reply_bytes_drained': control_bytes,
                  'native_events': events, 'native_bridge_exit': bridge.returncode if bridge else None,
                  'source_decode': source_decode, 'udp_returned_decode': returned_decode,
                  'exact_decoded_frame_sequence_match': source_hashes == returned_hashes,
                  'source_byte_sha256': hashlib.sha256(original_path.read_bytes()).hexdigest(),
                  'returned_byte_sha256': hashlib.sha256(returned_path.read_bytes()).hexdigest(),
                  'h264_sps': header_trace, 'hardware_readback': hardware, 'errors': errors,
                  'media_retained': False, 'not_measured': ['phone rendering', 'public transport', 'cellular/V50',
                                                         'audio/video synchronization', 'NAT traversal']}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if errors or (bridge and bridge.returncode) or source_decode['exit_code'] or returned_decode['exit_code']:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
