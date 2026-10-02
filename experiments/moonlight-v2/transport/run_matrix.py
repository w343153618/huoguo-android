#!/usr/bin/env python3
"""Sequential real loopback sockets; synthetic content/loss, never WAN claims."""
import argparse
import datetime
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
CASES = [
    (4_000_000, 60, 0, 0),
    (4_000_000, 60, 2, 2),
    (4_000_000, 60, 5, 2),
    (8_000_000, 60, 2, 2),
    (12_000_000, 120, 0, 0),
    (12_000_000, 120, 2, 2),
    (24_000_000, 120, 2, 2),
    (40_000_000, 120, 2, 2),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, default=Path(tempfile.gettempdir())/'huoguo-udp-native-build/udp_media_probe')
    parser.add_argument('--seconds', type=int, default=5, choices=range(1, 31))
    parser.add_argument('--output', type=Path, default=ROOT/'evidence/loopback-20261001.json')
    args = parser.parse_args()
    result = {'validation_layer': 'actual_loopback_udp_kernel_socket_synthetic_media_and_injected_loss',
              'not_measured': ['Android video capture', 'H264 decode/display', 'audio/video sync',
                               'NAT traversal', 'WAN', 'V50 power/decoder'],
              'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'samples': []}
    for bitrate, fps, loss, reorder in CASES:
        completed = subprocess.run([str(args.binary), '--loopback', str(args.seconds), str(bitrate),
                                    str(fps), str(loss), str(reorder)],
                                   capture_output=True, text=True, check=True, timeout=args.seconds+20)
        sample = json.loads(completed.stdout)
        result['samples'].append(sample)
        print(f'{bitrate/1_000_000:g} Mbps {fps} FPS loss={loss}%: '
              f'delivered={sample["delivered_frames"]}/{sample["generated_frames"]}, '
              f'p95={sample["delay_p95_ms"]:.2f} ms, IDR={sample["idr_requests"]}', flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print('Saved bounded loopback results: '+str(args.output))


if __name__ == '__main__':
    main()
