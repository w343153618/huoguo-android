#!/usr/bin/env python3
"""Bounded Apple LTR experiment using synthetic local RGBA and offline FFmpeg.

No device, capture, service, account or network access. Temporary source media
is synthetic and removed on exit. Explicitly retained compressed fixtures must
stay under /private/tmp. ACK means the full prefix was actually decoded
offline, not received or decoded by a phone. Experimental encoder is required.
"""
import argparse
import hashlib
import json
from pathlib import Path
import queue
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import time

CONFIG = 1 << 62
KEYFRAME = 1 << 61
PTS_MASK = (1 << 61) - 1
BASE_PTS = 1_000_000


def framed_stream(packets, geometry, fps, frames, selected_indices=None, cycles=1):
    """FileProbe framing; cycles repeat the same synthetic encoded sequence.

    Every sequence begins at its existing IDR. Config appears only at the start;
    PTS advance monotonically, preserving dropped-frame gaps within each cycle.
    Repetition is decoder/load input, not a longer independent capture.
    """
    selected = None if selected_indices is None else set(selected_indices)
    output = bytearray(b'h264' + struct.pack('>III', 0x80000000, *geometry))
    records = 0
    for cycle in range(cycles):
        media_index = 0
        for packet in packets:
            if packet['config']:
                if cycle:
                    continue
            elif selected is not None and media_index not in selected:
                media_index += 1
                continue
            flags = packet['pts_us'] + cycle * frames * 1_000_000 // fps
            flags |= CONFIG if packet['config'] else (KEYFRAME if packet['keyframe'] else 0)
            output.extend(struct.pack('>QI', flags, len(packet['payload'])))
            output.extend(packet['payload'])
            records += 1
            if not packet['config']:
                media_index += 1
    if records > 4096 or len(output) > 32 * 1024 * 1024:
        raise ValueError('Retained fixture exceeds FileProbe record/byte bounds')
    return bytes(output)


def exact(stream, count):
    data = bytearray()
    while len(data) < count:
        chunk = stream.read(count - len(data))
        if not chunk:
            raise EOFError('Short encoder record')
        data.extend(chunk)
    return bytes(data)


def nals(payload):
    markers = list(re.finditer(b'\x00\x00\x00?\x01', payload))
    if not markers or markers[0].start() != 0:
        raise ValueError('Non-AnnexB payload')
    result = []
    for i, match in enumerate(markers):
        end = markers[i + 1].start() if i + 1 < len(markers) else len(payload)
        body = payload[match.end():end]
        if not body:
            raise ValueError('Empty NAL')
        result.append(body)
    return result


def decode_hashes(ffmpeg, bitstream, width, height):
    result = subprocess.run([ffmpeg, '-v', 'error', '-err_detect', 'explode',
                             '-f', 'h264', '-i', 'pipe:0', '-fps_mode', 'passthrough',
                             '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'],
                            input=bitstream, capture_output=True, timeout=15)
    frame_bytes = width * height * 3
    if result.returncode or len(result.stdout) % frame_bytes:
        raise ValueError('Offline decode failed: ' + result.stderr.decode(errors='replace')[:500])
    if result.stderr.strip():
        raise ValueError('Offline decoder reported errors: ' + result.stderr.decode(errors='replace')[:500])
    return [hashlib.sha256(result.stdout[i:i + frame_bytes]).hexdigest()
            for i in range(0, len(result.stdout), frame_bytes)]


def recovery_hash_match(full, recovered, prefix_count, recovery_index):
    expected = full[:prefix_count] + full[recovery_index:]
    return {'decoded_frames': len(recovered), 'expected_frames': len(expected),
            'all_frames_match_complete_decode': recovered == expected,
            'prefix_frames_match': recovered[:prefix_count] == full[:prefix_count],
            'recovery_frames_match': recovered[prefix_count:] == full[recovery_index:],
            'complete_rgb_sha256': hashlib.sha256(''.join(full).encode()).hexdigest(),
            'recovered_rgb_sha256': hashlib.sha256(''.join(recovered).encode()).hexdigest()}


class Encoder:
    def __init__(self, executable, low_latency, ltr, fps, bitrate):
        self.events, self.packets = [], []
        self.lock = threading.Lock()
        self.inbox = queue.Queue()
        self.failure = None
        self.process = subprocess.Popen([str(executable), '--service', 'true',
            '--low-latency-mode', str(low_latency).lower(), '--enable-ltr', str(ltr).lower(),
            '--ltr-allow-hardware-wrapper', str(low_latency and ltr).lower(),
            '--fps', str(fps), '--bitrate', str(bitrate)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.readers = [threading.Thread(target=self.read_media, daemon=True),
                        threading.Thread(target=self.read_events, daemon=True)]
        for thread in self.readers:
            thread.start()

    def read_events(self):
        for line in self.process.stderr:
            try:
                event = json.loads(line)
                with self.lock:
                    self.events.append(event)
            except (ValueError, UnicodeError):
                self.failure = 'invalid_encoder_json'

    def read_media(self):
        try:
            if exact(self.process.stdout, 4) != b'h264':
                raise ValueError('Wrong codec')
            marker, width, height = struct.unpack('>III', exact(self.process.stdout, 12))
            if marker != 0x80000000:
                raise ValueError('Wrong geometry marker')
            self.geometry = (width, height)
            while True:
                flags, size = struct.unpack('>QI', exact(self.process.stdout, 12))
                if not 0 < size <= 8 * 1024 * 1024:
                    raise ValueError('Invalid packet size')
                payload = exact(self.process.stdout, size)
                packet = {'pts_us': flags & PTS_MASK, 'config': bool(flags & CONFIG),
                          'keyframe': bool(flags & KEYFRAME), 'payload': payload}
                nals(payload)
                self.packets.append(packet)
                if not packet['config']:
                    self.inbox.put(packet)
        except EOFError:
            self.inbox.put(None)
        except Exception as error:
            self.failure = str(error)
            self.inbox.put(None)

    def frame(self, pixels, width, height, pts):
        self.process.stdin.write(struct.pack('>IIQI', width, height, pts, len(pixels)) + pixels)
        self.process.stdin.flush()

    def command(self, kind, value=0, sequence=1):
        payload = struct.pack('>IQ', kind, value) if kind in (3, 4) else struct.pack('>II', kind, value)
        self.process.stdin.write(struct.pack('>IIQI', 0, 0, sequence, len(payload)) + payload)
        self.process.stdin.flush()

    def wait_frame(self, pts):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            packet = self.inbox.get(timeout=max(.01, deadline - time.monotonic()))
            if packet is None:
                raise ValueError('Encoder stopped before requested output')
            if packet['pts_us'] == pts:
                return packet
        raise TimeoutError('No requested encoded frame')

    def close(self):
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=3)
        for thread in self.readers:
            thread.join(timeout=2)
        self.process.stdout.close()
        self.process.stderr.close()

    def stream(self, selected_indices=None):
        selected = None if selected_indices is None else set(selected_indices)
        output, media_index = bytearray(), 0
        for packet in self.packets:
            if packet['config'] or selected is None or media_index in selected:
                output.extend(packet['payload'])
            if not packet['config']:
                media_index += 1
        return bytes(output)


def reference_trace(ffmpeg, bitstream, recovery_index):
    result = subprocess.run([ffmpeg, '-v', 'verbose', '-f', 'h264', '-i', 'pipe:0',
                             '-c:v', 'copy', '-bsf:v', 'trace_headers', '-f', 'null', '-'],
                            input=bitstream, capture_output=True, timeout=10)
    fields = ('nal_unit_type', 'nal_ref_idc', 'slice_type', 'frame_num',
              'max_num_ref_frames', 'num_ref_idx_l0_active_minus1',
              'ref_pic_list_modification_flag_l0', 'modification_of_pic_nums_idc',
              'long_term_pic_num', 'long_term_frame_idx',
              'long_term_reference_flag', 'adaptive_ref_pic_marking_mode_flag',
              'memory_management_control_operation')
    lines, global_fields, packet_index = [], [], -1
    for line in result.stderr.decode(errors='replace').splitlines():
        if '[trace_headers' not in line:
            continue
        if 'Packet:' in line:
            packet_index += 1
        if any(re.search(r'\b' + f + r'\b', line) for f in fields):
            parsed = line.split('] ', 1)[-1].strip()
            if packet_index == recovery_index:
                lines.append(parsed)
            elif packet_index == -1:
                global_fields.append(parsed)
    return {'trace_returncode': result.returncode, 'reference_header_fields': lines[:80],
            'trace_packet_count': packet_index + 1, 'global_reference_header_fields': global_fields[:20],
            'trace_recovery_packet_index': recovery_index}


def run_case(args, pixels_path, case, low_latency, ltr, ack, refresh):
    encoder = Encoder(args.encoder, low_latency, ltr, args.fps, args.bitrate)
    report = {'case': case, 'low_latency_mode': low_latency, 'enable_ltr': ltr,
              'ack_requested': ack, 'refresh_kind': refresh, 'phone_ack': False,
              'ack_scope': 'complete received prefix actually decoded with offline FFmpeg',
              'frames_requested': args.frames, 'loss_interval': [args.prefix, args.recover]}
    size = args.width * args.height * 4
    try:
        with pixels_path.open('rb') as source:
            for index in range(args.frames):
                if index == args.prefix and ack:
                    # An emitted token alone is insufficient: actually decode the
                    # received prefix before the caller sends that token back.
                    decoded = decode_hashes(args.ffmpeg, encoder.stream(), args.width, args.height)
                    if len(decoded) != index:
                        raise ValueError('Cannot ACK: full prefix did not decode completely')
                    with encoder.lock:
                        candidates = [e for e in encoder.events if e.get('event') == 'ltr_output'
                                      and isinstance(e.get('ack_token'), int)]
                    if not candidates:
                        raise ValueError('No emitted LTR token after decoded prefix')
                    token = candidates[-1]['ack_token']
                    encoder.command(3, token, index)
                    report.update(ack_token=token, ack_token_pts_us=candidates[-1]['pts_us'],
                                  offline_decoded_prefix_frames=len(decoded))
                if index == args.recover:
                    encoder.command(4 if refresh == 'ltr' else 2, sequence=index)
                rgba = exact(source, size)
                pts = BASE_PTS + index * 1_000_000 // args.fps
                encoder.frame(rgba, args.width, args.height, pts)
                if low_latency:
                    encoder.wait_frame(pts)
                else:
                    time.sleep(1 / args.fps)
        encoder.close()
        if encoder.process.returncode or encoder.failure:
            raise ValueError(encoder.failure or 'encoder_nonzero_exit')
        media = [p for p in encoder.packets if not p['config']]
        if len(media) != args.frames:
            raise ValueError('Requested/output frame mismatch')
        full = decode_hashes(args.ffmpeg, encoder.stream(), args.width, args.height)
        selected = list(range(args.prefix)) + list(range(args.recover, args.frames))
        recovered = decode_hashes(args.ffmpeg, encoder.stream(selected), args.width, args.height)
        report.update(recovery_hash_match(full, recovered, args.prefix, args.recover))
        recovery = media[args.recover]
        config = b''.join(p['payload'] for p in encoder.packets if p['config'])
        sps = next((n for n in nals(config) if n[0] & 31 == 7), b'')
        report.update(success=True, complete_decode_frames=len(full),
                      complete_bitstream_sha256=hashlib.sha256(encoder.stream()).hexdigest(),
                      recovery_au_bytes=len(recovery['payload']), recovery_keyframe=recovery['keyframe'],
                      recovery_nal_types=[n[0] & 31 for n in nals(recovery['payload'])],
                      sps_profile_idc=sps[1] if len(sps) > 1 else None,
                      sps_constraints_byte=sps[2] if len(sps) > 2 else None,
                      all_au_bytes=[len(p['payload']) for p in media],
                      **reference_trace(args.ffmpeg, encoder.stream(), args.recover))
        if args.retain_fixtures:
            args.retain_fixtures.mkdir(parents=True, exist_ok=True, mode=0o700)
            retained = {}
            for label, indices in (('complete', None), ('recovery', selected)):
                for cycles in sorted(set((1, args.fixture_cycles))):
                    data = framed_stream(encoder.packets, encoder.geometry, args.fps,
                                         args.frames, indices, cycles)
                    path = args.retain_fixtures / f'{case}-{label}-{cycles}cycle.h264framed'
                    path.write_bytes(data)
                    path.chmod(0o600)
                    retained[f'{label}_{cycles}cycle'] = {
                        'path': str(path), 'bytes': len(data),
                        'sha256': hashlib.sha256(data).hexdigest(),
                        'cycles': cycles, 'independent_source_frames': args.frames,
                        'media_records': (args.frames if indices is None else len(indices)) * cycles,
                        'scope': 'repeated synthetic encoded sequence; no phone/UDP measurement'}
            report['retained_fixtures'] = retained
    except Exception as error:
        report.update(success=False, failure_class=type(error).__name__, failure=str(error)[:700])
    finally:
        encoder.close()
        report['encoder_exit_code'] = encoder.process.returncode
        report['encoder_events'] = encoder.events
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--encoder', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ffmpeg', default=shutil.which('ffmpeg'))
    parser.add_argument('--width', type=int, default=540)
    parser.add_argument('--height', type=int, default=960)
    parser.add_argument('--fps', type=int, default=60)
    parser.add_argument('--bitrate', type=int, default=4_000_000)
    parser.add_argument('--frames', type=int, default=72)
    parser.add_argument('--prefix', type=int, default=24)
    parser.add_argument('--recover', type=int, default=40)
    parser.add_argument('--retain-fixtures', type=Path)
    parser.add_argument('--fixture-cycles', type=int, default=1)
    args = parser.parse_args()
    if not args.ffmpeg or not args.encoder.is_file():
        parser.error('Experimental encoder and FFmpeg required')
    if not (2 <= args.width <= 720 and 2 <= args.height <= 1280 and
            args.width % 2 == 0 and args.height % 2 == 0 and
            2 <= args.prefix < args.recover < args.frames <= 120 and 1 <= args.fps <= 120):
        parser.error('Bounded even geometry <=720x1280; 2<=prefix<recover<frames<=120')
    if not 1 <= args.fixture_cycles <= 25 or args.frames * args.fixture_cycles / args.fps > 30:
        parser.error('Fixture cycles must fit <=30 seconds and <=25 repeats')
    if args.retain_fixtures:
        args.retain_fixtures = args.retain_fixtures.resolve()
        if not args.retain_fixtures.is_relative_to(Path('/private/tmp')):
            parser.error('Compressed synthetic fixtures may only be retained under /private/tmp')
    with tempfile.TemporaryDirectory(prefix='huoguo-ltr-', dir='/private/tmp') as tmp:
        pixels = Path(tmp) / 'synthetic.rgba'
        subprocess.run([args.ffmpeg, '-v', 'error', '-f', 'lavfi', '-i',
            f'testsrc2=size={args.width}x{args.height}:rate={args.fps}',
            '-frames:v', str(args.frames), '-pix_fmt', 'rgba', '-f', 'rawvideo', str(pixels)],
            check=True, timeout=15)
        cases = [('baseline-default-idr', False, False, False, 'idr'),
                 ('baseline-support', False, True, False, 'ltr'),
                 ('low-latency-no-ack', True, True, False, 'ltr'),
                 ('low-latency-ack-ltr', True, True, True, 'ltr'),
                 ('low-latency-ack-idr', True, True, True, 'idr')]
        report = {'scope': 'M1 Apple hardware API/bitstream/offline decode; synthetic testsrc2, no phone/WAN/video source measurement',
                  'geometry': [args.width, args.height], 'fps': args.fps, 'bitrate': args.bitrate,
                  'temporary_media_deleted_on_exit': True, 'actual_phone_ack_measured': False,
                  'compressed_fixtures_retained_explicitly': bool(args.retain_fixtures),
                  'cases': [run_case(args, pixels, *case) for case in cases]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'saved': str(args.output), 'cases': [{k: c.get(k) for k in
        ('case', 'success', 'failure', 'recovery_au_bytes', 'recovery_keyframe',
         'all_frames_match_complete_decode')} for c in report['cases']]}))


if __name__ == '__main__':
    main()
