#!/usr/bin/env python3
"""Generate a bounded, paced synthetic VT fixture for isolated phone replay.

No emulator, screen, phone, account, network or deployed binary is touched.
The retained binary fixture must be under /private/tmp; reports contain metadata
only. --self-test needs neither ffmpeg nor a native encoder and starts no codec.
"""
import argparse
from collections import Counter
import hashlib
import io
import json
import math
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time


ROOT = Path(__file__).resolve().parents[2]
WIDTH, HEIGHT, FPS, BITRATE = 540, 1200, 60, 4_000_000
PTS_STEP_US = 16_666
CONFIG_FLAG, KEY_FLAG, PTS_MASK = 1 << 62, 1 << 61, (1 << 61) - 1
MAX_PACKET = 8 * 1024 * 1024
DEFAULT_NATIVE = Path.home() / 'Documents/ChatGPT/others/android-remote/m1-compare/hardware/macos-h264'
SPS_FIELDS = {
    'profile_idc', 'level_idc', 'seq_parameter_set_id', 'chroma_format_idc',
    'bit_depth_luma_minus8', 'bit_depth_chroma_minus8', 'log2_max_frame_num_minus4',
    'pic_order_cnt_type', 'log2_max_pic_order_cnt_lsb_minus4', 'max_num_ref_frames',
    'gaps_in_frame_num_allowed_flag', 'pic_width_in_mbs_minus1',
    'pic_height_in_map_units_minus1', 'frame_mbs_only_flag', 'frame_cropping_flag',
    'frame_crop_left_offset', 'frame_crop_right_offset', 'frame_crop_top_offset',
    'frame_crop_bottom_offset', 'vui_parameters_present_flag',
    'num_units_in_tick', 'time_scale', 'fixed_frame_rate_flag',
    'bitstream_restriction_flag', 'max_num_reorder_frames', 'num_reorder_frames',
    'max_dec_frame_buffering', *(f'constraint_set{i}_flag' for i in range(6))
}
SLICE_FIELDS = {'nal_unit_type', 'nal_ref_idc', 'first_mb_in_slice', 'slice_type',
                'pic_parameter_set_id', 'frame_num', 'idr_pic_id', 'pic_order_cnt_lsb',
                'delta_pic_order_cnt_bottom'}
NATIVE_NUMBERS = {
    'width', 'height', 'fps_expected', 'bitrate_target_bps', 'input_frames',
    'output_frames', 'dropped_or_failed', 'pending_at_end', 'pending_peak',
    'keyframes', 'elapsed_seconds', 'output_fps', 'output_bitrate_bps',
    'process_cpu_percent_of_one_core', 'final_drain_ms', 'vbr_rate_limit_status',
    'max_frame_delay_read_status', 'max_frame_delay_raw_readback',
    'max_frame_delay_count_readback', 'low_latency_mode_read_status',
    'profile_level_read_status', 'frame_reordering_read_status',
    'expected_frame_rate_read_status', 'expected_frame_rate_readback', 'hardware_property_read_status'
}
NATIVE_BOOLEANS = {'using_hardware', 'required_hardware', 'frame_reordering_readback',
                   'max_frame_delay_applied', 'low_latency_mode_requested',
                   'low_latency_mode_readback', 'hardware_property_readback', 'encoder_registry_hardware'}
NATIVE_STRINGS = {'event', 'probe', 'encoder_id', 'profile_level_requested',
                  'profile_level_readback', 'bitrate_mode', 'finish_reason', 'hardware_verification_method'}
NATIVE_DISTRIBUTIONS = {'vimage_and_pool_ms', 'submit_to_callback_ms',
                        'complete_rgba_to_callback_ms', 'stdout_write_ms',
                        'output_callback_gap_ms', 'host_capture_timestamp_age_at_submit_ms'}


def distribution(values):
    values = sorted(values)
    if not values:
        return {'count': 0}
    def p(q):
        at = (len(values) - 1) * q
        lo = int(at)
        hi = min(lo + 1, len(values) - 1)
        return round(values[lo] + (values[hi] - values[lo]) * (at - lo), 3)
    return {'count': len(values), 'p50': p(.5), 'p95': p(.95), 'p99': p(.99),
            'max': round(values[-1], 3)}


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def private_media_path(value):
    original = Path(value).expanduser()
    if not original.is_absolute():
        raise ValueError('framed-output must be an absolute path under /private/tmp')
    resolved = original.resolve()
    if not resolved.is_relative_to(Path('/private/tmp').resolve()):
        raise ValueError('binary fixtures are permitted only under /private/tmp')
    if resolved == Path('/private/tmp') or resolved.is_relative_to(ROOT):
        raise ValueError('binary fixture cannot be the temp directory or a repository path')
    if not resolved.parent.is_dir():
        raise ValueError('framed-output parent directory must already exist')
    return resolved


def exact(stream, count, clean_eof=False):
    result = bytearray()
    while len(result) < count:
        chunk = stream.read(count - len(result))
        if not chunk:
            if clean_eof and not result:
                return None
            raise EOFError('truncated fixture framing or RGBA frame')
        result.extend(chunk)
    return bytes(result)


def write_all(stream, data):
    view = memoryview(data)
    while view:
        amount = stream.write(view)
        if not amount:
            raise BrokenPipeError('native stdin accepted no data')
        view = view[amount:]


def raw_header(index, start_us, length=WIDTH * HEIGHT * 4):
    return struct.pack('>IIQI', WIDTH, HEIGHT, start_us + index * PTS_STEP_US, length)


def native_command(binary, low_latency_mode, frames):
    # Omit the new option entirely for a legacy executable, rather than guessing
    # that an unknown-option error should permit a lower-performance fallback.
    command = [str(binary), '--fps', str(FPS), '--bitrate', str(BITRATE), '--mode', 'VBR',
               '--max-frames', str(frames), '--max-seconds', str(math.ceil(frames / FPS) + 10),
               '--idle-seconds', '10']
    if low_latency_mode != 'omit':
        command.extend(['--low-latency-mode', low_latency_mode])
    return command


def nal_types(payload):
    return sorted({payload[match.end()] & 31
                   for match in re.finditer(rb'\x00\x00(?:\x00)?\x01', payload)
                   if match.end() < len(payload)})


def collect_framed(stream, retained, annexb, packets):
    prefix = exact(stream, 16)
    codec, marker, width, height = struct.unpack('>4sIII', prefix)
    if codec != b'h264' or marker != 0x80000000 or (width, height) != (WIDTH, HEIGHT):
        raise ValueError('unexpected codec/geometry in native fixture')
    write_all(retained, prefix)
    total_bytes = len(prefix)
    while True:
        header = exact(stream, 12, clean_eof=True)
        if header is None:
            return total_bytes
        flagged, size = struct.unpack('>QI', header)
        if flagged & (1 << 63) or size <= 0 or size > MAX_PACKET:
            raise ValueError('unexpected resize or invalid packet size in fixed fixture')
        payload = exact(stream, size)
        write_all(retained, header)
        write_all(retained, payload)
        write_all(annexb, payload)
        total_bytes += len(header) + size
        if total_bytes > 128 * 1024 * 1024:
            raise ValueError('fixture exceeds 128 MiB bound')
        packets.append({'kind': 'config' if flagged & CONFIG_FLAG else 'frame',
                        'pts_us': flagged & PTS_MASK, 'bytes': size,
                        'keyframe': bool(flagged & KEY_FLAG), 'nal_types': nal_types(payload),
                        'arrival_ns': time.monotonic_ns()})
        if len(packets) > 650:
            raise ValueError('fixture packet count exceeds bound')


def safe_native_event(source):
    if not isinstance(source, dict) or not (source.get('event') == 'ready' or
            source.get('probe') == 'emulator-hardware-rgba-v1'):
        return None
    result = {}
    for key in NATIVE_NUMBERS:
        value = source.get(key)
        if finite_number(value) or key in source and value is None:
            result[key] = value
    for key in NATIVE_BOOLEANS:
        value = source.get(key)
        if isinstance(value, bool) or key in source and value is None:
            result[key] = value
    for key in NATIVE_STRINGS:
        value = source.get(key)
        if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,160}', value):
            result[key] = value
    for key in NATIVE_DISTRIBUTIONS:
        value = source.get(key)
        if isinstance(value, dict):
            result[key] = {name: number for name, number in value.items()
                           if name in ('count', 'p50', 'p95', 'p99', 'max') and finite_number(number)}
    statuses = source.get('max_frame_delay_set_status')
    if isinstance(statuses, dict):
        result['max_frame_delay_set_status'] = {key: value for key, value in statuses.items()
                                               if key in ('0', '1') and finite_number(value)}
    # Only this new, synthetic-data process's own structured error is considered;
    # arbitrary stderr/log contents are never put in the report.
    error = source.get('error')
    if isinstance(error, str):
        result['error'] = error[:400]
    return result


def parse_trace(text):
    """Numeric H.264 metadata only: omit FFmpeg banners, log text and bitstrings."""
    sections, kind, fields = [], None, {}
    def finish():
        if kind and fields:
            sections.append((kind, dict(fields)))
    for line in text.splitlines():
        found = next((name for name in ('Sequence Parameter Set', 'Picture Parameter Set', 'Slice Header')
                      if re.search(r'\]\s+' + re.escape(name) + r'\s*$', line)), None)
        if found:
            finish()
            kind, fields = found, {}
            continue
        match = re.search(r'\]\s+\d+\s+([a-zA-Z_][a-zA-Z_0-9]*(?:\[\d+\])?)\s+[01]+\s+=\s+(-?\d+)\s*$', line)
        if not match:
            continue
        key, value = match[1], int(match[2])
        if (kind == 'Sequence Parameter Set' and key in SPS_FIELDS or
                kind == 'Slice Header' and key in SLICE_FIELDS):
            fields[key] = value
    finish()
    unique_sps, seen, slices = [], set(), []
    for section, value in sections:
        if section == 'Sequence Parameter Set':
            token = json.dumps(value, sort_keys=True)
            if token not in seen:
                unique_sps.append(value)
                seen.add(token)
        elif section == 'Slice Header':
            slices.append(value)
    histogram = Counter(row['slice_type'] % 5 for row in slices if 'slice_type' in row)
    return {'unique_sps': unique_sps, 'slice_headers': slices,
            'slice_type_mod5_counts': {str(key): count for key, count in sorted(histogram.items())},
            'slice_type_mod5_names': {'0': 'P', '1': 'B', '2': 'I', '3': 'SP', '4': 'SI'},
            'raw_trace_saved': False}


def analyze_annexb(ffmpeg, path):
    traced = subprocess.run([ffmpeg, '-hide_banner', '-nostdin', '-v', 'info', '-i', str(path),
                             '-map', '0:v:0', '-c:v', 'copy', '-bsf:v', 'trace_headers',
                             '-f', 'null', '-'], capture_output=True, timeout=20)
    if len(traced.stderr) > 8 * 1024 * 1024:
        raise ValueError('FFmpeg trace exceeds bounded 8 MiB read')
    trace = parse_trace(traced.stderr.decode('utf-8', errors='replace'))
    trace['ffmpeg_trace_exit_code'] = traced.returncode
    decoded = subprocess.run([ffmpeg, '-hide_banner', '-nostdin', '-v', 'error', '-threads', '1',
                              '-i', str(path), '-map', '0:v:0', '-an', '-fps_mode', 'passthrough',
                              '-progress', 'pipe:1', '-nostats', '-f', 'null', '-'],
                             capture_output=True, timeout=20)
    counts = [int(match[1]) for match in re.finditer(rb'(?m)^frame=(\d+)\s*$', decoded.stdout)]
    trace.update({'ffmpeg_decode_exit_code': decoded.returncode,
                  'software_decoded_frame_count': counts[-1] if counts else None,
                  'software_decode_had_stderr': bool(decoded.stderr)})
    return trace


def terminate(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


def generate(args, framed_path):
    binary = Path(args.native_encoder).expanduser().resolve()
    ffmpeg = shutil.which(args.ffmpeg)
    if not binary.is_file() or not ffmpeg:
        raise ValueError('native encoder or ffmpeg is unavailable')
    command = native_command(binary, args.low_latency_mode, args.frames)
    report = {'probe': 'paced-vt-codec-fixture-v1', 'label': args.label,
              'scope': 'synthetic RGBA -> local Apple VT H264 fixture; no emulator, network or phone',
              'source': {'generator': 'ffmpeg testsrc2', 'width': WIDTH, 'height': HEIGHT,
                         'fps': FPS, 'requested_frames': args.frames, 'pts_start_us': args.pts_start_us,
                         'pts_step_us': PTS_STEP_US, 'raw_pixels_saved': False},
              'encoder': {'binary': str(binary), 'fps': FPS, 'bitrate_bps': BITRATE, 'mode': 'VBR',
                          'low_latency_option': args.low_latency_mode,
                          'legacy_option_omitted': args.low_latency_mode == 'omit'},
              'framed_output': str(framed_path), 'status': 'FAIL', 'errors': []}
    native, source, writer, metrics_reader = None, None, None, None
    packets, native_events, errors, feeds = [], [], [], []
    digest = hashlib.sha256()
    stop = threading.Event()
    timer = None
    with tempfile.TemporaryDirectory(prefix='huoguo-vt-fixture-', dir='/private/tmp') as scratch:
        annexb_path = Path(scratch) / 'encoded.h264'
        try:
            # Exclusive creation prevents replacing a retained earlier experiment.
            with framed_path.open('xb') as retained, annexb_path.open('xb') as annexb:
                native = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE, bufsize=0)
                def read_output():
                    try:
                        collect_framed(native.stdout, retained, annexb, packets)
                    except Exception as exc:
                        errors.append('native framing: ' + str(exc))
                def read_metrics():
                    count = 0
                    for line in native.stderr:
                        count += len(line)
                        if count > 512 * 1024:
                            errors.append('native stderr exceeds 512 KiB bound')
                            stop.set()
                            return
                        try:
                            value = safe_native_event(json.loads(line))
                        except (ValueError, UnicodeDecodeError):
                            continue
                        if value:
                            native_events.append(value)
                writer = threading.Thread(target=read_output, daemon=True)
                metrics_reader = threading.Thread(target=read_metrics, daemon=True)
                writer.start()
                metrics_reader.start()
                source = subprocess.Popen([ffmpeg, '-hide_banner', '-nostdin', '-v', 'error',
                                           '-filter_threads', '1', '-f', 'lavfi', '-i',
                                           f'testsrc2=size={WIDTH}x{HEIGHT}:rate={FPS}',
                                           '-frames:v', str(args.frames), '-threads', '1',
                                           '-pix_fmt', 'rgba', '-f', 'rawvideo', 'pipe:1'],
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
                def deadline():
                    stop.set()
                    errors.append('bounded native/source generation deadline exceeded')
                    terminate(native)
                    terminate(source)
                timer = threading.Timer(args.frames / FPS + 15, deadline)
                timer.daemon = True
                timer.start()
                begin = None
                for index in range(args.frames):
                    rgba = exact(source.stdout, WIDTH * HEIGHT * 4)
                    if begin is None:
                        begin = time.monotonic_ns()
                    target = begin + round(index * 1e9 / FPS)
                    remaining = (target - time.monotonic_ns()) / 1e9
                    if remaining > 0:
                        time.sleep(remaining)
                    if stop.is_set():
                        raise TimeoutError('generation stopped by bounded watchdog')
                    digest.update(rgba)
                    started = time.monotonic_ns()
                    write_all(native.stdin, raw_header(index, args.pts_start_us))
                    write_all(native.stdin, rgba)
                    feeds.append({'frame': index, 'pts_us': args.pts_start_us + index * PTS_STEP_US,
                                  'feed_start_ns': started, 'feed_end_ns': time.monotonic_ns(),
                                  'target_ns': target})
                native.stdin.close()
                source.wait(timeout=3)
                native.wait(timeout=10)
                writer.join(timeout=3)
                metrics_reader.join(timeout=3)
                if writer.is_alive() or metrics_reader.is_alive():
                    raise TimeoutError('native readers failed to finish')
                report['native_exit_code'] = native.returncode
                report['ffmpeg_source_exit_code'] = source.returncode
                if native.returncode != 0 or source.returncode != 0:
                    errors.append('native or deterministic source returned nonzero')
            if annexb_path.stat().st_size:
                report['bitstream'] = analyze_annexb(ffmpeg, annexb_path)
        except Exception as exc:
            errors.append(type(exc).__name__ + ': ' + str(exc))
        finally:
            if timer:
                timer.cancel()
            terminate(source)
            terminate(native)
            if writer and writer.is_alive():
                writer.join(timeout=3)
            if metrics_reader and metrics_reader.is_alive():
                metrics_reader.join(timeout=3)
            for process in (source, native):
                if process:
                    for stream in (process.stdin, process.stdout, process.stderr):
                        if stream and not stream.closed:
                            stream.close()
    ready = [row for row in native_events if row.get('event') == 'ready']
    final = [row for row in native_events if row.get('probe') == 'emulator-hardware-rgba-v1'
             and 'output_frames' in row]
    frames = [row for row in packets if row['kind'] == 'frame']
    report['native_events'] = native_events
    report['hardware_verified_ready_and_final'] = bool(ready and final and
            all(row.get('using_hardware') is True for row in ready + final))
    if not report['hardware_verified_ready_and_final']:
        errors.append('required actual hardware readback missing/false in ready or final metrics')
    report['source']['raw_sha256'] = digest.hexdigest()
    report['source']['fed_frames'] = len(feeds)
    report['source']['feed_block_ms'] = distribution([(row['feed_end_ns'] - row['feed_start_ns']) / 1e6 for row in feeds])
    report['source']['feed_start_gap_ms'] = distribution([(b['feed_start_ns'] - a['feed_start_ns']) / 1e6 for a, b in zip(feeds, feeds[1:])])
    report['source']['feed_start_lateness_ms'] = distribution([max(0, row['feed_start_ns'] - row['target_ns']) / 1e6 for row in feeds])
    report['output'] = {'frames': len(frames), 'config_packets': len(packets) - len(frames),
                        'pts_match_fixed_source_sequence': [row['pts_us'] for row in frames] ==
                            [args.pts_start_us + index * PTS_STEP_US for index in range(args.frames)],
                        'arrival_gap_ms': distribution([(b['arrival_ns'] - a['arrival_ns']) / 1e6 for a, b in zip(frames, frames[1:])]),
                        'packets': packets}
    if framed_path.is_file():
        report['framed_bytes'] = framed_path.stat().st_size
        report['framed_sha256'] = hashlib.sha256(framed_path.read_bytes()).hexdigest()
    bitstream = report.get('bitstream', {})
    if not report['output']['pts_match_fixed_source_sequence']:
        errors.append('encoded frame PTS/count does not match the entire fixed source sequence')
    if (bitstream.get('ffmpeg_trace_exit_code') != 0 or
            bitstream.get('ffmpeg_decode_exit_code') != 0 or
            bitstream.get('software_decoded_frame_count') != args.frames or
            not bitstream.get('unique_sps')):
        errors.append('SPS trace or full software-decode frame-count verification failed')
    report['errors'] = errors
    report['status'] = 'PASS' if not errors else 'FAIL'
    return report


def self_test():
    packet = struct.pack('>4sIII', b'h264', 0x80000000, WIDTH, HEIGHT)
    config = b'\x00\x00\x00\x01\x67\x01\x00\x00\x01\x68\x02'
    picture = b'\x00\x00\x00\x01\x65\x03'
    packet += struct.pack('>QI', CONFIG_FLAG, len(config)) + config
    packet += struct.pack('>QI', KEY_FLAG | 1_000_000, len(picture)) + picture
    class Fragmented(io.BytesIO):
        def read(self, amount=-1):
            return super().read(min(amount, 3))
    retained, annexb, rows = io.BytesIO(), io.BytesIO(), []
    assert collect_framed(Fragmented(packet), retained, annexb, rows) == len(packet)
    assert retained.getvalue() == packet and annexb.getvalue() == config + picture
    assert rows[0]['nal_types'] == [7, 8] and rows[1]['keyframe'] and rows[1]['pts_us'] == 1_000_000
    assert len(raw_header(1, 1_000_000)) == 20
    assert struct.unpack('>IIQI', raw_header(1, 1_000_000))[2] == 1_016_666
    assert '--low-latency-mode' not in native_command('/fake/native', 'omit', 240)
    assert native_command('/fake/native', 'true', 240)[-2:] == ['--low-latency-mode', 'true']
    try:
        private_media_path(str(ROOT / 'bad-fixture.bin'))
        raise AssertionError('repository media was allowed')
    except ValueError:
        pass
    trace = parse_trace('''[trace_headers @ 0x1] Sequence Parameter Set
[trace_headers @ 0x1] 8 profile_idc 01100100 = 100
[trace_headers @ 0x1] 16 constraint_set3_flag 0 = 0
[trace_headers @ 0x1] 70 max_num_ref_frames 010 = 1
[trace_headers @ 0x1] 170 max_num_reorder_frames 1 = 0
[trace_headers @ 0x1] 180 max_dec_frame_buffering 010 = 1
[trace_headers @ 0x1] Slice Header
[trace_headers @ 0x1] 3 nal_unit_type 00101 = 5
[trace_headers @ 0x1] 9 slice_type 0001000 = 7
''')
    assert trace['unique_sps'][0]['max_num_reorder_frames'] == 0
    assert trace['slice_type_mod5_counts'] == {'2': 1}
    sanitized = safe_native_event({'event': 'ready', 'using_hardware': True, 'secret': 'do-not-copy',
                                   'low_latency_mode_readback': None, 'encoder_id': 'com.apple.encoder.h264'})
    assert sanitized['using_hardware'] is True and sanitized['low_latency_mode_readback'] is None
    assert 'secret' not in sanitized
    print(json.dumps({'self_test': 'PASS', 'checks': 7,
                      'scope': 'offline framing/PTS/path/trace/metadata checks; no codec or ffmpeg process'}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--native-encoder', default=str(DEFAULT_NATIVE))
    parser.add_argument('--low-latency-mode', choices=('omit', 'false', 'true'), default='omit',
                        help='omit is required for the legacy runtime native binary')
    parser.add_argument('--ffmpeg', default='ffmpeg')
    parser.add_argument('--frames', type=int, default=240)
    parser.add_argument('--pts-start-us', type=int, default=1_000_000)
    parser.add_argument('--label', default='synthetic-testsrc2-540x1200-60')
    parser.add_argument('--framed-output', help='absolute retained binary path under /private/tmp; must not exist')
    parser.add_argument('--report', help='optional .json metadata report; never contains media payload')
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not 180 <= args.frames <= 300:
        parser.error('frames must be bounded to 180...300')
    if not 0 <= args.pts_start_us < (1 << 61) - args.frames * PTS_STEP_US:
        parser.error('PTS sequence must fit protocol bits 0...60')
    if not args.framed_output:
        parser.error('--framed-output is required except for --self-test')
    try:
        framed_path = private_media_path(args.framed_output)
    except ValueError as exc:
        parser.error(str(exc))
    if framed_path.exists():
        parser.error('framed-output already exists; select a new experiment path')
    report_path = Path(args.report).expanduser().resolve() if args.report else None
    if report_path and (report_path.suffix != '.json' or report_path == framed_path):
        parser.error('--report must be a separate .json metadata file')
    report = generate(args, framed_path)
    encoded = json.dumps(report, indent=2, sort_keys=True) + '\n'
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(encoded)
        print(json.dumps({'status': report['status'], 'report': str(report_path),
                          'framed_output': str(framed_path), 'frames': report['output']['frames'],
                          'hardware_verified': report['hardware_verified_ready_and_final']}))
    else:
        print(encoded, end='')
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
