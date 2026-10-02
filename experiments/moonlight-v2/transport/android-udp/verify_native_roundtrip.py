#!/usr/bin/env python3
"""CPU H264 -> paced stdio HGUD -> native phone core -> ffmpeg frame digests.

This is NOT a phone, Android source, authenticated WAN, UDP socket, or AV-sync test.
"""
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
    return subprocess.run([str(x) for x in args], check=True, **kwargs)


def nals(data):
    starts = list(re.finditer(b'\x00\x00\x00?\x01', data))
    return [(data[m.end()] & 31, data[m.start():starts[i+1].start() if i+1 < len(starts) else len(data)])
            for i, m in enumerate(starts)]


def digests(ffmpeg, path):
    result = subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'h264', '-i', str(path),
                             '-f', 'framemd5', '-'], capture_output=True, text=True, timeout=20)
    return result, [x.rsplit(',', 1)[-1].strip() for x in result.stdout.splitlines() if x and not x.startswith('#')]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, default=Path('/private/tmp/huoguo-android-udp-build/host'))
    parser.add_argument('--output', type=Path, default=ROOT/'evidence/native-roundtrip-20261001.json')
    parser.add_argument('--frame-events', action='store_true', help='Verify opt-in host frame metadata without changing media bytes')
    parser.add_argument('--pacing-burst-bytes', type=int, choices=(0, 2048, 4096), default=0)
    args = parser.parse_args()
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        raise SystemExit('Existing ffmpeg/ffprobe required, no installs attempted')
    evidence = {'validation_layer': 'offline_CPU_H264_real_bitstream_stdio_pacing_native_receiver_ffmpeg_decode',
                'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'pacing_catchup_credit_bytes': args.pacing_burst_bytes,
                'not_measured': ['phone JNI execution', 'Android/YouTube source', 'UDP sockets', 'WAN/NAT',
                                 'AESGCM', 'VideoToolbox', 'audio/video timing'], 'samples': []}
    with tempfile.TemporaryDirectory(prefix='huoguo-udp-jni-', dir='/private/tmp') as folder:
        folder = Path(folder)
        source = folder/'source.h264'
        run(ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=540x1200:rate=60',
            '-t', '2', '-c:v', 'libx264', '-preset', 'ultrafast', '-tune', 'zerolatency', '-profile:v', 'baseline',
            '-refs', '1', '-bf', '0', '-b:v', '4M', '-maxrate', '8M', '-bufsize', '2M', '-g', '30',
            '-sc_threshold', '0', '-pix_fmt', 'yuv420p', '-f', 'h264', source)
        data = source.read_bytes()
        packet_info = json.loads(run(ffprobe, '-v', 'error', '-show_packets', '-select_streams', 'v', '-of',
                                    'json', source, capture_output=True, text=True).stdout)['packets']
        config = b''.join(payload for kind, payload in nals(data[:int(packet_info[0]['size'])]) if kind in (7, 8))
        framed_path, events_path = folder/'packets.bin', folder/'sender-events.jsonl'
        with framed_path.open('wb') as framed, events_path.open('wb') as events:
            command = [str(args.build/'h264_udp_packetizer'), '40000000']
            if args.frame_events or args.pacing_burst_bytes:
                command += ['500000', '1' if args.frame_events else '0']
            if args.pacing_burst_bytes:
                command.append(str(args.pacing_burst_bytes))
            producer = subprocess.Popen(command, stdin=subprocess.PIPE,
                                        stdout=framed, stderr=events)
            errors = []

            def feed():
                try:
                    producer.stdin.write(b'h264'+struct.pack('>III', 0x80000000, 540, 1200))
                    producer.stdin.write(struct.pack('>QI', 1 << 62, len(config))+config)
                    origin = time.monotonic()
                    for index, packet in enumerate(packet_info):
                        time.sleep(max(0, origin+index/60-time.monotonic()))
                        payload = data[int(packet['pos']):int(packet['pos'])+int(packet['size'])]
                        producer.stdin.write(struct.pack('>QI', 1_000_000+index*1_000_000//60, len(payload))+payload)
                        producer.stdin.flush()
                except Exception as error:
                    errors.append(type(error).__name__)
                finally:
                    producer.stdin.close()

            feeder = threading.Thread(target=feed)
            feeder.start(); feeder.join(timeout=15)
            if feeder.is_alive():
                producer.kill(); raise SystemExit('Synthetic feeder blocked')
            producer.wait(timeout=10)
        events = [json.loads(x) for x in events_path.read_text().splitlines()]
        summaries = [x for x in events if x['event'] == 'summary']
        evidence['sender'] = summaries[-1]
        evidence['feeder_errors'] = errors
        evidence['source_frames'] = len(packet_info)
        evidence['source_sha256'] = hashlib.sha256(data).hexdigest()
        evidence['source_configuration_bytes'] = len(config)
        sources = [value for value in events if value.get('event') == 'frame_source']
        outputs = [value for value in events if value.get('event') == 'frame_output']
        evidence['host_frame_diagnostics'] = {'enabled': args.frame_events, 'source_events': len(sources), 'output_events': len(outputs)}
        if args.frame_events:
            if len(sources) != len(packet_info) or len(outputs) != len(packet_info):
                raise ValueError('Host diagnostic frame coverage')
            if [value['frame'] for value in sources] != list(range(1, len(packet_info)+1)):
                raise ValueError('Host diagnostic frame sequence')
            if not all(value['complete'] and value['host_capture_us'] <= value['first_write_host_us'] <= value['last_write_host_us'] for value in outputs):
                raise ValueError('Host diagnostic timing/complete contract')
        trace = run(ffmpeg, '-hide_banner', '-loglevel', 'info', '-i', source, '-c:v', 'copy',
                    '-bsf:v', 'trace_headers', '-f', 'null', '-', capture_output=True, text=True).stderr
        evidence['source_sps'] = {name: sorted(set(map(int, re.findall(r'\b'+name+r'\s+[^\r\n=]*=\s*(\d+)', trace))))
                                  for name in ('profile_idc', 'max_num_ref_frames', 'max_num_reorder_frames', 'max_dec_frame_buffering')}
        _, original = digests(ffmpeg, source)
        for label, switches in [('zero_loss', []), ('erase_two_data_shards_each_block_reorder3', ['--drop-two-data', '--reorder', '3']),
                                ('random2pct_reorder3_seed7', ['--loss', '0.02', '--reorder', '3', '--seed', '7'])]:
            completed = run(args.build/'phone_receiver_probe', *switches, input=framed_path.read_bytes(), capture_output=True, timeout=20)
            logical_bytes, position, returned, bodies = completed.stdout, 0, bytearray(), 0
            while position < len(logical_bytes):
                size = struct.unpack_from('>I', logical_bytes, position)[0]; position += 4
                body = logical_bytes[position:position+size]; position += size
                width, height, pts, config_size = struct.unpack_from('>IIQI', body)
                if (width, height) != (540, 1200) or size != len(body) or config_size > 65536:
                    raise SystemExit('Logical body roundtrip metadata mismatch')
                if not pts & (1 << 61) and config_size:
                    raise SystemExit('Non-IDR carried configuration')
                returned.extend(body[20:]); bodies += 1
            path = folder/(label+'.h264'); path.write_bytes(returned)
            decoded, hashes = digests(ffmpeg, path)
            sample = json.loads(completed.stderr)
            sample.update({'label': label, 'logical_bodies': bodies, 'decoded_frames': len(hashes),
                           'ffmpeg_decode_exit': decoded.returncode, 'ffmpeg_diagnostic_lines': len(decoded.stderr.splitlines()),
                           'exact_frame_digest_match': hashes == original,
                           'returned_sha256': hashlib.sha256(returned).hexdigest()})
            evidence['samples'].append(sample)
            if label != 'random2pct_reorder3_seed7' and hashes != original:
                raise SystemExit('Required loss-free/deterministic-FEC exact decode failed: '+json.dumps(evidence))
        if producer.returncode or errors or evidence['sender']['output_frames'] != len(packet_info):
            raise SystemExit('Synthetic source rejected by packetizer: '+json.dumps(evidence))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(evidence, ensure_ascii=False))


if __name__ == '__main__':
    main()
