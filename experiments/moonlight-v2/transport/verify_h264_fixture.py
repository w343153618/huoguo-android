#!/usr/bin/env python3
"""Encode a synthetic H264 fixture, UDP-roundtrip, compare decoded frame digests."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent


def run(*args, **kwargs):
    return subprocess.run([str(value) for value in args], check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, default=Path(tempfile.gettempdir())/'huoguo-udp-native-build/h264_udp_bridge')
    parser.add_argument('--output', type=Path, default=ROOT/'evidence/h264-fixture-roundtrip-20261001.json')
    args = parser.parse_args()
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        raise SystemExit('Local ffmpeg/ffprobe required; no install/download is attempted')
    with tempfile.TemporaryDirectory(prefix='huoguo-h264-udp-') as directory:
        temporary = Path(directory)
        source = temporary/'fixture.h264'
        run(ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
            'testsrc2=size=320x240:rate=60', '-t', '1', '-c:v', 'libx264', '-preset', 'ultrafast',
            '-tune', 'zerolatency', '-g', '30', '-pix_fmt', 'yuv420p', '-b:v', '1M',
            '-f', 'h264', source)
        packets = json.loads(run(ffprobe, '-v', 'error', '-show_packets', '-select_streams', 'v:0',
                                 '-of', 'json', source, capture_output=True, text=True).stdout)['packets']
        source_bytes = source.read_bytes()
        framed = bytearray(b'h264'+struct.pack('>III', 0x80000000, 320, 240))
        for index, packet in enumerate(packets):
            payload = source_bytes[int(packet['pos']):int(packet['pos'])+int(packet['size'])]
            framed.extend(struct.pack('>QI', 1_000_000+index*1_000_000//60, len(payload)))
            framed.extend(payload)

        def digests(path):
            completed = run(ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'h264', '-i', path,
                            '-f', 'framemd5', '-', capture_output=True, text=True)
            return [line.rsplit(',', 1)[1].strip() for line in completed.stdout.splitlines()
                    if line and not line.startswith('#')]

        original_digests = digests(source)
        samples = []
        for loss, reorder in ((0, 0), (2, 2)):
            completed = run(args.binary, '12000000', str(loss), str(reorder), '0', input=bytes(framed),
                            capture_output=True, timeout=20)
            returned = temporary/f'roundtrip-{loss}.h264'
            returned.write_bytes(completed.stdout)
            decoded = digests(returned)
            summaries = [json.loads(line) for line in completed.stderr.decode().splitlines()]
            summary = next(value for value in summaries if value['event'] == 'summary')
            summary.update({'decoded_frames': len(decoded), 'source_decoded_frames': len(original_digests),
                            'frame_digest_exact_match': decoded == original_digests,
                            'source_byte_sha256': hashlib.sha256(source_bytes).hexdigest(),
                            'returned_byte_sha256': hashlib.sha256(completed.stdout).hexdigest()})
            samples.append(summary)
            if loss == 0 and (decoded != original_digests or completed.stdout != source_bytes):
                raise SystemExit('Loss-free H264 byte/frame-digest comparison failed')
        result = {'validation_layer': 'synthetic_encoded_H264_actual_localhost_udp_and_ffmpeg_decode',
                  'not_measured': ['YouTube/Android capture', 'phone decoder/display', 'WAN/NAT', 'AV sync'],
                  'samples': samples}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
        print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
