#!/usr/bin/env python3
"""Offline 540x1200 motion/noise CPU-H264 pacing checks; no host/phone capture."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parent


def run(*args, **kwargs):
    return subprocess.run([str(value) for value in args], check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, default=Path(tempfile.gettempdir())/'huoguo-udp-native-build/h264_udp_bridge')
    parser.add_argument('--output', type=Path, default=ROOT/'evidence/complex-h264-budget-20261001.json')
    parser.add_argument('--noise-seed', type=int, default=20261001)
    args = parser.parse_args()
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        raise SystemExit('Existing local ffmpeg/ffprobe required')
    evidence = {'validation_layer': 'offline_CPU_encoded_synthetic_540x1200_VBR_real_loopback_UDP',
                'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'not_measured': ['real Android source', 'phone', 'VideoToolbox', 'WAN', 'AV sync'],
                'source_parameters': {'codec': 'libx264', 'profile': 'baseline', 'level': '3.2', 'refs': 1,
                                      'bframes': 0, 'fps': 60, 'dimensions': [540, 1200],
                                      'average_bitrate_target': 4000000, 'maxrate': 8000000, 'VBV_buffer': 2000000,
                                      'IDR_interval_frames': 120, 'ipratio': 4,
                                      'noise_seed': args.noise_seed}, 'samples': []}
    with tempfile.TemporaryDirectory(prefix='huoguo-complex-h264-', dir='/private/tmp') as directory:
        directory = Path(directory)
        for noise in (False, True):
            source = directory/f'source-{noise}.h264'
            arguments = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
                         'testsrc2=size=540x1200:rate=60']
            if noise:
                arguments += ['-vf', f'noise=alls=10:allf=t+u:all_seed={args.noise_seed}']
            arguments += ['-t', '4', '-c:v', 'libx264', '-preset', 'ultrafast', '-tune', 'zerolatency',
                          '-profile:v', 'baseline', '-level', '3.2', '-refs', '1', '-bf', '0', '-b:v', '4M',
                          '-maxrate', '8M', '-bufsize', '2M', '-g', '120', '-keyint_min', '120',
                          '-sc_threshold', '0', '-x264-params', 'ipratio=4', '-pix_fmt', 'yuv420p', '-f', 'h264', source]
            run(*arguments)
            packets = json.loads(run(ffprobe, '-v', 'error', '-select_streams', 'v', '-show_packets', '-of',
                                     'json', source, capture_output=True, text=True).stdout)['packets']
            source_bytes = source.read_bytes()
            headers = run(ffmpeg, '-hide_banner', '-loglevel', 'info', '-i', source, '-c:v', 'copy',
                          '-bsf:v', 'trace_headers', '-f', 'null', '-', capture_output=True, text=True).stderr
            sps = {key: sorted(set(map(int, re.findall(r'\b'+key+r'\s+[^\r\n=]*=\s*(\d+)', headers))))
                   for key in ('profile_idc', 'level_idc', 'max_num_ref_frames', 'max_num_reorder_frames',
                               'max_dec_frame_buffering', 'bitstream_restriction_flag')}
            for bitrate in (8_000_000, 12_000_000, 24_000_000):
                returned_path = directory/f'returned-{noise}-{bitrate}.h264'
                stderr_path = directory/f'events-{noise}-{bitrate}.txt'
                with returned_path.open('wb') as returned, stderr_path.open('wb') as native_events:
                    bridge = subprocess.Popen([str(args.binary), str(bitrate), '0', '0', '0'],
                                              stdin=subprocess.PIPE, stdout=returned, stderr=native_events)
                    errors = []

                    def feed():
                        try:
                            bridge.stdin.write(b'h264'+struct.pack('>III', 0x80000000, 540, 1200))
                            origin = time.monotonic()
                            for index, packet in enumerate(packets):
                                time.sleep(max(0, origin+index/60-time.monotonic()))
                                payload = source_bytes[int(packet['pos']):int(packet['pos'])+int(packet['size'])]
                                bridge.stdin.write(struct.pack('>QI', 1_000_000+index*1_000_000//60, len(payload)))
                                bridge.stdin.write(payload); bridge.stdin.flush()
                        except Exception as error:
                            errors.append(type(error).__name__)
                        finally:
                            bridge.stdin.close()

                    writer = threading.Thread(target=feed)
                    writer.start(); writer.join(timeout=20)
                    if writer.is_alive():
                        bridge.kill()
                        raise SystemExit('Offline writer blocked beyond bounded duration')
                    bridge.wait(timeout=10)
                event_lines = stderr_path.read_text().splitlines()
                if any(len(line) > 8192 for line in event_lines):
                    raise SystemExit('Summary exceeds real Host driver JSON event bound')
                summary = next(json.loads(line) for line in event_lines if json.loads(line).get('event') == 'summary')
                decoded = subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-i', returned_path,
                                          '-f', 'framemd5', '-'], capture_output=True, text=True, timeout=20)
                hashes = [line.rsplit(',', 1)[-1].strip() for line in decoded.stdout.splitlines()
                          if line and not line.startswith('#')]
                summary.update({'source_kind': 'motion_with_noise' if noise else 'motion', 'wire_budget_bps': bitrate,
                                'source_frames_expected': len(packets), 'source_h264_mbps': len(source_bytes)*8/4/1e6,
                                'source_IDR_payload_bytes': [int(value['size']) for value in packets if 'K' in value['flags']],
                                'source_sps': sps, 'source_byte_sha256': hashlib.sha256(source_bytes).hexdigest(),
                                'returned_byte_sha256': hashlib.sha256(returned_path.read_bytes()).hexdigest(),
                                'byte_exact': returned_path.read_bytes() == source_bytes,
                                'ffmpeg_decode_exit': decoded.returncode, 'ffmpeg_decoded_frames': len(hashes),
                                'ffmpeg_diagnostic_lines': len(decoded.stderr.splitlines()), 'feeder_errors': errors,
                                'feedback': 'off: prerecorded encoder cannot respond to a real IDR request'})
                evidence['samples'].append(summary)
                print(f'noise={noise} {bitrate//1_000_000}M: {len(hashes)}/{len(packets)} decoded, '
                      f'frame-budget-drops={summary["insufficient_frame_budget_count"]}, '
                      f'IDR-budget-drops={summary["insufficient_idr_budget_count"]}, '
                      f'scheduler-drops={summary["scheduler_expired_frame_count"]}, '
                      f'queue-max={summary["pacer_queue_before_frame_us_distribution"].get("max")}us', flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+'\n')
    print('Saved offline complex H264 budget comparison: '+str(args.output))


if __name__ == '__main__':
    main()
