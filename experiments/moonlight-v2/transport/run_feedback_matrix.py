#!/usr/bin/env python3
"""Paired seeded local UDP feedback comparisons, not actual H264/phone/WAN."""
import argparse
import datetime
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, default=Path(tempfile.gettempdir())/'huoguo-udp-native-build/udp_media_probe')
    parser.add_argument('--seconds', type=int, default=3, choices=range(1, 31))
    parser.add_argument('--rounds', type=int, default=2, choices=range(1, 11))
    parser.add_argument('--output', type=Path, default=ROOT/'evidence/feedback-loopback-20261001.json')
    args = parser.parse_args()
    result = {'validation_layer': 'actual_loopback_udp_synthetic_reference_chain_and_impairment',
              'feedback': {'attempt_limit': 3, 'retry_interval_ms': 20, 'initial_delay_ms': 5,
                           'injected_loss_percent': 20, 'media_retransmissions': 0},
              'not_measured': ['H264 decode', 'phone/WAN', 'true encoder IDR size/cost',
                               'audio/video sync', 'congestion controller'],
              'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'samples': []}
    for round_number in range(args.rounds):
        seed = 20261001+round_number
        for bitrate in (4_000_000, 8_000_000, 12_000_000, 24_000_000, 40_000_000):
            for fps in (60, 120):
                for feedback in (0, 1):
                    completed = subprocess.run([str(args.binary), '--loopback', str(args.seconds), str(bitrate),
                                                str(fps), '2', '2', str(feedback), '20', str(seed)],
                                               capture_output=True, text=True, check=True, timeout=args.seconds+20)
                    sample = json.loads(completed.stdout)
                    sample['round'] = round_number+1
                    result['samples'].append(sample)
                    print(f'round={round_number+1} {bitrate/1_000_000:g}M {fps}fps feedback={feedback}: '
                          f'{sample["delivered_frames"]}/{sample["generated_frames"]}, '
                          f'maxgap={sample["max_consecutive_unavailable_frames"]}, '
                          f'recovery={sample["recovery_max_ms"]:.1f}ms', flush=True)
                    # Save incrementally, so interruptions do not lose earlier evidence.
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print('Saved paired feedback results: '+str(args.output))


if __name__ == '__main__':
    main()
