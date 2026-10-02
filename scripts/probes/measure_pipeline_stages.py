#!/usr/bin/env python3
"""Measure the current M1 local hardware pipeline without server credentials.

Run only while phone streaming is stopped. The caller controls the already
running emulator's video content; this probe never launches a player, changes
display settings, wakes/sleeps Android, or connects to a public gateway.
HostHardwareSession uses its existing external runtime and private socketpairs.
Pixels, AAC and compressed H.264 are consumed then discarded. Only explicitly
whitelisted numerical pipeline metadata are written to the report. This is not
phone/WAN acceptance. SurfaceFlinger cadence has its own independent probe.
"""
import argparse
import io
import json
import math
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from hardware_stream import CONFIG_FLAG, PTS_MASK, HostHardwareSession, read_exact

KEY_FLAG = 1 << 61
MAX_LOG_READ = 8 * 1024 * 1024
MAX_VIDEO_FRAMES = 25000
PHASES = ('capture_age', 'capture_gap', 'source_pts_gap', 'raw_queue', 'raw_pipe')


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def distribution(values):
    values = sorted(x for x in values if finite_number(x))
    if not values:
        return {'count': 0}
    def p(q):
        at = (len(values) - 1) * q
        lo = int(at)
        hi = min(lo + 1, len(values) - 1)
        return round(values[lo] + (values[hi] - values[lo]) * (at - lo), 3)
    return {'count': len(values), 'p50': p(.5), 'p95': p(.95), 'p99': p(.99),
            'min': round(values[0], 3), 'max': round(values[-1], 3)}


def nal_types(payload):
    """Annex B NAL headers only; payload is never retained in the report."""
    types = set()
    for marker in re.finditer(rb'\x00\x00(?:\x00)?\x01', payload):
        if marker.end() < len(payload):
            types.add(payload[marker.end()] & 31)
    return sorted(types)


def video_packets(stream, stop):
    if read_exact(stream, 4) != b'h264':
        raise ValueError('unexpected_video_codec')
    while not stop.is_set():
        # The local host protocol reserves bit 63 for geometry markers, bit 62
        # for codec configuration, bit 61 for keyframes, and bits 0..60 for PTS.
        try:
            first = stream.recv(4)
        except OSError:
            if stop.is_set():
                return
            raise
        if not first:
            return
        if len(first) < 4:
            first += read_exact(stream, 4 - len(first))
        hi = struct.unpack('>I', first)[0]
        if hi & 0x80000000:
            width, height = struct.unpack('>II', read_exact(stream, 8))
            if not 2 <= width <= 8192 or not 2 <= height <= 8192:
                raise ValueError('invalid_video_geometry')
            yield {'kind': 'geometry', 'width': width, 'height': height}
            continue
        low, size = struct.unpack('>II', read_exact(stream, 8))
        if not 1 <= size <= 8 * 1024 * 1024:
            raise ValueError('invalid_video_packet_size')
        flagged = (hi << 32) | low
        payload = read_exact(stream, size)
        arrival_ns, arrival_us = time.monotonic_ns(), time.time_ns() // 1000
        yield {'kind': 'config' if flagged & CONFIG_FLAG else 'frame',
               'pts_us': flagged & PTS_MASK, 'keyframe': bool(flagged & KEY_FLAG),
               'bytes': size, 'nal_types': nal_types(payload),
               'arrival_ns': arrival_ns, 'arrival_unix_us': arrival_us}


def safe_fields(event):
    """Never copy a whole runtime log object: arbitrary strings can be secrets."""
    kind = event.get('event')
    out = {'event': kind}
    if kind == 'pipeline_sample':
        names = ('unix_ms', 'interval_ms', 'fps_cap', 'raw_fps', 'submitted_fps',
                 'replaced_fps', 'idle_repeats')
    elif kind == 'phase_sample':
        names = ('unix_ms',)
        if event.get('raw_queue_policy') in ('fifo', 'latest'):
            out['raw_queue_policy'] = event['raw_queue_policy']
        timings = event.get('timings')
        if isinstance(timings, dict):
            out['timings'] = {}
            for phase in PHASES:
                source = timings.get(phase)
                if isinstance(source, dict):
                    out['timings'][phase] = {name: source[name] for name in
                        ('count', 'p50_ms', 'p95_ms', 'max_ms') if finite_number(source.get(name))}
    elif kind == 'ready':
        names = ('width', 'height', 'vbr_rate_limit_status')
        if isinstance(event.get('using_hardware'), bool):
            out['using_hardware'] = event['using_hardware']
        if event.get('bitrate_mode') in ('CBR', 'VBR'):
            out['bitrate_mode'] = event['bitrate_mode']
        value = event.get('encoder_id')
        if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,160}', value):
            out['encoder_id'] = value
    elif kind == 'audio_clock_mapping':
        names = ('uncertainty_us', 'guest_wall_skew_ms')
    elif kind == 'session_closed':
        names = ('raw_frames', 'pending_frames_replaced', 'frames_submitted', 'idle_repeats',
                 'audio_packets', 'audio_startup_discarded')
    elif event.get('probe') == 'emulator-hardware-rgba-v1':
        out = {'probe': 'emulator-hardware-rgba-v1'}
        names = ('input_frames', 'output_frames', 'dropped_or_failed', 'elapsed_seconds',
                 'output_fps', 'output_bitrate_bps', 'pending_peak', 'keyframes')
        for name in ('using_hardware', 'required_hardware', 'frame_reordering_readback'):
            if isinstance(event.get(name), bool):
                out[name] = event[name]
        for name in ('vimage_and_pool_ms', 'submit_to_callback_ms', 'complete_rgba_to_callback_ms',
                     'stdout_write_ms', 'output_callback_gap_ms',
                     'host_capture_timestamp_age_at_submit_ms'):
            source = event.get(name)
            if isinstance(source, dict):
                out[name] = {key: source[key] for key in ('count', 'p50', 'p95', 'p99', 'max')
                             if finite_number(source.get(key))}
    else:
        return None
    for name in names:
        if finite_number(event.get(name)):
            out[name] = event[name]
    return out


def read_safe_log(path, start):
    if not path.exists():
        return [], {'status': 'missing'}
    size = path.stat().st_size
    if size < start:
        return [], {'status': 'log_rotated_or_truncated'}
    count = size - start
    if count > MAX_LOG_READ:
        return [], {'status': 'window_exceeds_bounded_read', 'new_bytes': count}
    selected, malformed = [], 0
    with path.open('rb') as source:
        source.seek(start)
        for row in source.read(count).splitlines():
            try:
                event = json.loads(row)
            except (ValueError, UnicodeDecodeError):
                malformed += 1
                continue
            if isinstance(event, dict):
                sanitized = safe_fields(event)
                if sanitized:
                    selected.append(sanitized)
    return selected, {'status': 'ok', 'new_bytes': count, 'malformed_rows': malformed,
                      'selected_events': len(selected), 'unredacted_log_copied': False}


def annotate_windows(events, begin_us, end_us):
    intervals = []
    for event in events:
        if event.get('event') == 'pipeline_sample' and finite_number(event.get('unix_ms')):
            stop_us = event['unix_ms'] * 1000
            span = event.get('interval_ms', 0) * 1000
            start_us = stop_us - span
            event['fully_inside_measurement'] = begin_us <= start_us and stop_us <= end_us
            intervals.append((stop_us, start_us))
    for event in events:
        if event.get('event') == 'phase_sample' and finite_number(event.get('unix_ms')):
            stop_us = event['unix_ms'] * 1000
            match = min(intervals, key=lambda item: abs(item[0] - stop_us), default=None)
            if match and abs(match[0] - stop_us) <= 1_000_000:
                event['inferred_interval_start_unix_ms'] = round(match[1] / 1000)
                event['fully_inside_measurement'] = begin_us <= match[1] and stop_us <= end_us
            else:
                event['fully_inside_measurement'] = None
    return events


def self_test():
    class Fragments:
        def __init__(self, data): self.buffer = io.BytesIO(data)
        def recv(self, count): return self.buffer.read(min(count, 3))
    def packet(flags, payload): return struct.pack('>QI', flags, len(payload)) + payload
    wire = (b'h264' + struct.pack('>III', 0x80000000, 540, 1200)
            + packet(CONFIG_FLAG, b'\0\0\0\1\x67abc')
            + packet(KEY_FLAG | 123456789, b'\0\0\0\1\x65abc')
            + packet(123473456, b'\0\0\1\x41abc'))
    rows = list(video_packets(Fragments(wire), threading.Event()))
    assert rows[0] == {'kind': 'geometry', 'width': 540, 'height': 1200}
    assert rows[1]['kind'] == 'config'
    assert rows[2]['pts_us'] == 123456789 and rows[2]['keyframe'] and rows[2]['nal_types'] == [5]
    assert rows[3]['nal_types'] == [1] and not rows[3]['keyframe']
    event = safe_fields({'event': 'phase_sample', 'unix_ms': 100, 'password': 'must_not_copy',
                        'raw_queue_policy': 'latest', 'timings': {'raw_queue':
                        {'count': 4, 'p95_ms': 3.0, 'secret': 'must_not_copy'}, 'unknown': {'x': 8}}})
    assert 'password' not in event and 'secret' not in event['timings']['raw_queue']
    assert 'unknown' not in event['timings']
    assert safe_fields({'event': 'auth', 'token': 'must_not_copy'}) is None
    assert distribution([1, 2, 3])['p50'] == 2
    return {'status': 'PASS', 'checks': ['fragmented_H264_framing', 'geometry_config_keyframe_flags',
            'NAL_header_only', 'runtime_log_whitelist', 'quantiles'],
            'live_session_started': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--runtime', type=Path, default=Path(os.environ.get('DIRECT_STATE_DIR',
        str(Path.home() / 'Documents/ChatGPT/others/android-remote/m1-compare'))))
    parser.add_argument('--serial', default='emulator-5556')
    parser.add_argument('--avd', default='RemoteAndroid17Compare')
    parser.add_argument('--fps', type=int, choices=(30, 60, 120), default=60)
    parser.add_argument('--bitrate', type=int, default=4000000)
    parser.add_argument('--mode', choices=('CBR', 'VBR', 'ADAPTIVE_VBR'), default='VBR')
    parser.add_argument('--max-size', type=int, choices=(960, 1200, 1600, 1920, 2400), default=1200)
    parser.add_argument('--duration', type=float, default=30)
    parser.add_argument('--warmup', type=float, default=2)
    parser.add_argument('--raw-queue-policy', choices=('fifo', 'latest'), default='fifo')
    parser.add_argument('--source-label', default='caller-controlled, unverified content')
    parser.add_argument('--source-kind', choices=('real-video', 'synthetic', 'unverified'), default='unverified')
    parser.add_argument('--verified-content-fps', type=float)
    parser.add_argument('--frame-detail', choices=('all', 'none'), default='all')
    parser.add_argument('--output', type=Path, help='optional metric-only JSON file; defaults to stdout')
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), indent=2))
        return 0
    if not 5 <= args.duration <= 120 or not 0 <= args.warmup <= 10:
        parser.error('duration must be 5-120 seconds and warmup 0-10 seconds')
    if not 500000 <= args.bitrate <= 40000000:
        parser.error('bitrate must be 0.5-40 Mbps')
    if not re.fullmatch(r'emulator-\d+', args.serial) or not re.fullmatch(r'[A-Za-z0-9_-]+', args.avd):
        parser.error('only an existing local emulator serial and plain AVD name are supported')
    if len(args.source_label) > 200 or any(ord(c) < 32 for c in args.source_label):
        parser.error('source label must be a short non-sensitive single line')
    if args.verified_content_fps is not None and not 1 <= args.verified_content_fps <= 240:
        parser.error('content FPS must be an independently verified value in 1-240')
    for name in ('hardware/venv/bin/python', 'hardware/macos-h264', 'hardware/scrcpy-audio-control'):
        if not (args.runtime / name).is_file():
            raise RuntimeError('existing_external_hardware_runtime_unavailable')

    adb = Path(os.environ.get('ANDROID_HOME', str(Path.home() / 'Library/Android/sdk'))) / 'platform-tools/adb'
    def command(*words, timeout=5):
        result = subprocess.run([str(adb), '-s', args.serial, *words], capture_output=True,
                                text=True, timeout=timeout, check=False)
        if result.returncode:
            raise RuntimeError('local_adb_read_failed')
        return result.stdout

    # Never kill an existing stream. Refuse another hardware worker because the
    # shared runtime log otherwise cannot attribute its events to this session.
    process_list = subprocess.run(['ps', '-axo', 'pid=,command='], capture_output=True,
                                  text=True, check=False)
    if process_list.returncode:
        raise RuntimeError('cannot_verify_exclusive_worker')
    active = [line for line in process_list.stdout.splitlines() if 'hardware_stream.py' in line
              and '--serial ' + args.serial in line]
    if active:
        raise RuntimeError('hardware_stream_active_stop_phone_stream_before_probe')
    preview_count = command('shell', 'ps', '-A', '-o', 'ARGS').count('com.genymobile.scrcpy.Server')
    physical_size = command('shell', 'wm', 'size').strip()
    if not re.fullmatch(r'(?:Physical size: \d+x\d+)(?:\s+Override size: \d+x\d+)?', physical_size):
        physical_size = 'unrecognized_size_readback'

    log_file = args.runtime / 'hardware/worker.log'
    log_start = log_file.stat().st_size if log_file.exists() else 0
    session = None
    stop = threading.Event()
    lock = threading.Lock()
    frames, configs, geometry, audio = [], [], [], []
    errors, threads = [], []
    audio_state = {'codec': None, 'disabled_or_unavailable': None}
    control_bytes = 0
    begin_ns = end_ns = begin_us = end_us = None
    startup_us = None

    def launch(role, function):
        def guarded():
            try:
                function()
                if not stop.is_set():
                    with lock: errors.append({'role': role, 'error_type': 'UnexpectedEndOfStream'})
                    stop.set()
            except Exception as exc:
                if not stop.is_set():
                    with lock: errors.append({'role': role, 'error_type': type(exc).__name__})
                    stop.set()
        thread = threading.Thread(target=guarded, name='stage-probe-' + role, daemon=True)
        thread.start(); threads.append(thread)

    try:
        session = HostHardwareSession(args.runtime, args.serial, args.avd, args.max_size,
                                      args.bitrate, args.fps, args.mode, args.raw_queue_policy)
        channels = {role: session.channel(role) for role in ('video', 'audio', 'control')}
        def receive_video():
            for row in video_packets(channels['video'], stop):
                with lock:
                    if row['kind'] == 'geometry': geometry.append(row)
                    elif row['kind'] == 'config': configs.append({k: row[k] for k in ('bytes', 'nal_types')})
                    else:
                        if len(frames) >= MAX_VIDEO_FRAMES: raise RuntimeError('bounded_frame_count_exceeded')
                        frames.append(row)
        def receive_audio():
            codec = struct.unpack('>I', read_exact(channels['audio'], 4))[0]
            with lock:
                audio_state['codec'] = codec
                audio_state['disabled_or_unavailable'] = codec in (0, 1)
            if codec in (0, 1):
                # Disabled/unavailable audio is allowed; there is no stream left
                # to drain. It must not stop a video-only source experiment.
                while not stop.wait(.25): pass
                return
            if codec != 0x00616163: raise ValueError('unexpected_audio_codec')
            while not stop.is_set():
                flagged, size = struct.unpack('>QI', read_exact(channels['audio'], 12))
                if not 1 <= size <= 1024 * 1024: raise ValueError('invalid_audio_packet_size')
                read_exact(channels['audio'], size)
                if not flagged & CONFIG_FLAG:
                    pts = flagged & PTS_MASK
                    with lock:
                        audio.append((time.monotonic_ns(), pts, size, (time.time_ns() // 1000 - pts) / 1000))
        def receive_control():
            nonlocal control_bytes
            while not stop.is_set():
                data = channels['control'].recv(65536)
                if not data: return
                with lock: control_bytes += len(data)
        for role, function in (('video', receive_video), ('audio', receive_audio), ('control', receive_control)):
            launch(role, function)
        session.start()
        startup = time.monotonic()
        startup_us = time.time_ns() // 1000
        while not stop.is_set() and time.monotonic() - startup < 15:
            with lock: ready = bool(frames)
            if ready: break
            stop.wait(.05)
        with lock: ready = bool(frames)
        if not ready or stop.is_set(): raise RuntimeError('first_encoded_frame_unavailable')
        if stop.wait(args.warmup): raise RuntimeError('pipeline_stopped_during_warmup')
        begin_ns, begin_us = time.monotonic_ns(), time.time_ns() // 1000
        if stop.wait(args.duration): raise RuntimeError('pipeline_stopped_during_measurement')
        end_ns, end_us = time.monotonic_ns(), time.time_ns() // 1000
    except Exception as exc:
        with lock: errors.append({'role': 'probe', 'error_type': type(exc).__name__})
        if end_ns is None: end_ns, end_us = time.monotonic_ns(), time.time_ns() // 1000
    finally:
        stop.set()
        if session: session.close()
        for thread in threads: thread.join(timeout=4)

    events, log_readback = read_safe_log(log_file, log_start)
    hardware = [event for event in events if event.get('event') == 'ready']
    hardware_verified = bool(hardware) and all(event.get('using_hardware') is True for event in hardware)
    if not hardware_verified:
        errors.append({'role': 'hardware_readback', 'error_type': 'RequiredHardwareNotVerified'})
    if begin_ns is None:
        begin_ns, begin_us = end_ns, end_us
    elapsed = max(0, (end_ns - begin_ns) / 1e9)
    selected = [row for row in frames if begin_ns <= row['arrival_ns'] < end_ns]
    arrival_gaps = [(b['arrival_ns'] - a['arrival_ns']) / 1e6 for a, b in zip(selected, selected[1:])]
    pts_gaps = [(b['pts_us'] - a['pts_us']) / 1000 for a, b in zip(selected, selected[1:])]
    ages = [(row['arrival_unix_us'] - row['pts_us']) / 1000 for row in selected]
    audio_selected = [row for row in audio if begin_ns <= row[0] < end_ns]
    windows = [sum(begin_ns + i * 1e9 <= row['arrival_ns'] < begin_ns + (i + 5) * 1e9
                   for row in selected) / 5 for i in range(0, int(elapsed) - 4, 5)]
    report = {'probe': 'M1-local-pipeline-stages-v1', 'status': 'FAIL' if errors else 'PASS',
        'source': {'kind': args.source_kind, 'label': args.source_label,
                   'content_fps_caller_verified': args.verified_content_fps,
                   'content_fps_measured_by_probe': False},
        'transport': 'private_local_socketpairs; no WAN or phone', 'serial': args.serial, 'avd': args.avd,
        'physical_size_readback': physical_size, 'preview_guest_servers_before_probe': preview_count,
        'requested': {'fps_cap': args.fps, 'bitrate_bps': args.bitrate, 'mode': args.mode,
                      'raw_queue_policy': args.raw_queue_policy, 'max_size': args.max_size},
        'warmup_after_first_encoded_frame_seconds': args.warmup,
        'measurement_seconds': round(elapsed, 6), 'measurement_start_unix_us': begin_us,
        'measurement_end_unix_us': end_us, 'session_start_unix_us': startup_us,
        'encoded_received_frames': len(selected),
        'encoded_received_fps': round(len(selected) / elapsed, 3) if elapsed else None,
        'keyframes': sum(row['keyframe'] for row in selected),
        'encoded_megabits_per_second': round(sum(row['bytes'] for row in selected) * 8 / elapsed / 1e6, 3) if elapsed else None,
        'encoded_frame_bytes': distribution([row['bytes'] for row in selected]),
        'output_arrival_gap_ms': distribution(arrival_gaps), 'output_source_pts_gap_ms': distribution(pts_gaps),
        'estimated_emulator_timestamp_to_local_H264_receipt_ms': distribution(ages),
        'pts_duplicate_transitions': sum(x == 0 for x in pts_gaps),
        'pts_nonmonotonic_transitions': sum(x < 0 for x in pts_gaps),
        'gaps_over_50ms': sum(x > 50 for x in arrival_gaps),
        'gaps_over_two_requested_frame_intervals': sum(x > 2000 / args.fps for x in arrival_gaps),
        'complete_five_second_window_fps': windows, 'geometry_events': geometry, 'codec_config_packets': configs,
        'audio_packets_drained': len(audio_selected), 'audio_timestamp_age_ms': distribution([row[3] for row in audio_selected]),
        'audio_codec_readback': audio_state,
        'control_bytes_drained': control_bytes, 'hardware_verified': hardware_verified,
        'worker_events': annotate_windows(events, begin_us, end_us), 'worker_log_readback': log_readback,
        'native_finish_timing_summary_status': 'present' if any(event.get('probe') == 'emulator-hardware-rgba-v1'
            for event in events) else 'unavailable_on_bounded_worker_close',
        'errors': errors, 'pixels_audio_or_encoded_video_saved': False,
        'scope': 'Existing M1 capture/queue/RGBA pipe/VT/local H264 receive only. No phone decoder, display, WAN, touch or objective A/V synchronization. Frame PTS is emulator estimated generation time, not proof of unique video content. Per-window phase percentiles cannot be merged into global percentiles. Worker events assume exclusive hardware worker; local scrcpy preview is recorded and preserved.'}
    if args.frame_detail == 'all':
        report['video_frames'] = [{'source_pts_us': row['pts_us'],
             'arrival_elapsed_ms': round((row['arrival_ns'] - begin_ns) / 1e6, 3),
             'arrival_unix_us': row['arrival_unix_us'], 'bytes': row['bytes'],
             'keyframe': row['keyframe'], 'nal_types': row['nal_types']} for row in selected]
    output = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output)
        print(json.dumps({'status': report['status'], 'output': str(args.output),
                          'encoded_received_fps': report['encoded_received_fps'],
                          'output_arrival_gap_ms': report['output_arrival_gap_ms'],
                          'hardware_verified': hardware_verified, 'errors': errors}, ensure_ascii=False))
    else:
        print(output, end='')
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
