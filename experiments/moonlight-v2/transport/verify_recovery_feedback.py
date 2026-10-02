#!/usr/bin/env python3
"""Control-policy integration only: constructed NAL framing, not decoded H264."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, default=Path(tempfile.gettempdir())/'huoguo-udp-native-build/h264_udp_bridge')
    parser.add_argument('--output', type=Path, default=ROOT/'evidence/bounded-recovery-control-20261001.json')
    args = parser.parse_args()
    unit = subprocess.run([str(args.binary.with_name('recovery_feedback_probe'))],
                          capture_output=True, text=True, timeout=5, check=True)
    evidence = {'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'validation_layer': 'deterministic_feedback_policy_and_constructed_NAL_control_framing_localhost_UDP',
                'media_is_decodable_H264': False,
                'not_measured': ['real capture', 'actual H264 decode', 'phone', 'VideoToolbox', 'WAN', 'AV sync'],
                'policy_test': json.loads(unit.stdout), 'samples': []}
    for label, oversized, heal in (('ordinary_reference_outage_exhausts_finite_retries', False, False),
                                   ('oversized_response_IDR_blocks_repeat_requests', True, False),
                                   ('smaller_received_IDR_completes_recovery', True, True)):
        process = subprocess.Popen([str(args.binary), '12000000', '0', '0', '1'],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output = bytearray()
        event_bytes = bytearray()
        readers = [threading.Thread(target=lambda: output.extend(process.stdout.read())),
                   threading.Thread(target=lambda: event_bytes.extend(process.stderr.read()))]
        for reader in readers:
            reader.start()
        try:
            process.stdin.write(b'h264'+struct.pack('>III', 0x80000000, 540, 1200))
            origin = time.monotonic()
            for frame in range(120):
                time.sleep(max(0, origin+frame/60-time.monotonic()))
                is_idr = frame == 0 or (oversized and frame == 24) or (heal and frame == 90)
                # NAL header inspection and reference-chain bookkeeping only.
                # The synthetic body is deliberately not a coded H264 slice.
                size = 160000 if frame == 6 or (oversized and frame == 24) else (2000 if is_idr else 1200)
                payload = b'\x00\x00\x00\x01'+bytes([0x65 if is_idr else 0x41])+b'\x55'*(size-5)
                process.stdin.write(struct.pack('>QI', 1000000+frame*1000000//60, len(payload)))
                process.stdin.write(payload)
                process.stdin.flush()
        finally:
            process.stdin.close()
        process.wait(timeout=10)
        for reader in readers:
            reader.join(timeout=5)
            if reader.is_alive():
                raise SystemExit('Control fixture reader did not finish')
        if process.returncode:
            raise SystemExit('Bridge failed: '+str(process.returncode))
        lines = event_bytes.decode().splitlines()
        if any(len(line) > 8192 for line in lines):
            raise SystemExit('Bridge JSON exceeds real Host event reader bound')
        events = [json.loads(line) for line in lines]
        summary = next(event for event in events if event.get('event') == 'summary')
        requests = [event for event in events if event.get('event') == 'request_idr']
        adaptation = [event for event in events if event.get('event') == 'encoder_budget_feedback']
        summary.update({'fixture': label, 'feedback_events': len(requests),
                        'adaptation_events': len(adaptation), 'largest_event_json_bytes': max(map(len, lines)),
                        'returned_byte_sha256': hashlib.sha256(output).hexdigest()})
        if not oversized:
            assert summary['feedback_request_rounds'] == 3
            assert len(requests) == 3
            assert summary['feedback_udp_packets_sent'] == 9
            assert summary['feedback_exhausted_episodes'] == 1
            assert not summary['requires_encoder_or_wire_budget_change']
        else:
            assert summary['insufficient_idr_budget_count'] == 1
            assert len(requests) == 1 and summary['feedback_request_rounds'] == 1, {
                'requests': requests, 'rounds': summary['feedback_request_rounds'],
                'receiver_reference_outages': summary['idr_requests'],
                'recovery_state': summary['recovery_feedback_state']}
            assert len(adaptation) == 1
            assert summary['feedback_budget_blocked_episodes'] == 1
            assert summary['requires_encoder_or_wire_budget_change'] == (not heal)
            assert summary['feedback_recovered_episodes'] == int(heal)
            assert adaptation[0]['detail']['auto_apply'] is False
            assert adaptation[0]['detail']['estimated_next_idr_payload_budget_bytes'] < 160000
        evidence['samples'].append(summary)
        print(f'{label}: requests={len(requests)}, rounds={summary["feedback_request_rounds"]}, '
              f'adaptation={len(adaptation)}, recovered={summary["feedback_recovered_episodes"]}', flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+'\n')
    print('Saved control-policy evidence: '+str(args.output))


if __name__ == '__main__':
    main()
