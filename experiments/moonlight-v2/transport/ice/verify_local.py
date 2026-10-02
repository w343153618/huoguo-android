#!/usr/bin/env python3
"""Short-lived same-host UDP test. No STUN, TURN, media, device or cloud changes."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent
TOP_FIELDS = {
    'schema', 'scope', 'passed', 'error_code', 'elapsed_us', 'roundtrips', 'application_datagrams',
    'application_bytes', 'roundtrip_p50_us', 'roundtrip_p95_us', 'roundtrip_max_us',
    'fail_closed_checks_passed', 'tcp_candidate_rejected', 'oversize_rejected', 'stun_used',
    'turn_used', 'signaling_memory_only', 'tcp_media_used', 'peers'}
PEER_FIELDS = {'state', 'host_candidates', 'selected_local_class', 'selected_remote_class',
               'socket_binding_audits', 'requested_ifindex', 'actual_ifindex',
               'sent_datagrams', 'received_datagrams', 'payload_errors'}


def validate(report):
    if set(report) != TOP_FIELDS or report['scope'] != 'same_host_real_udp_host_candidate_only_not_WAN':
        return False
    if any(type(value) not in (bool, int) for key, value in report.items() if key not in ('scope', 'peers')):
        return False
    if not isinstance(report['peers'], list) or len(report['peers']) != 2:
        return False
    for peer in report['peers']:
        if not isinstance(peer, dict) or set(peer) != PEER_FIELDS or any(type(v) is not int for v in peer.values()):
            return False
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, default=Path('/private/tmp/huoguo-ice-build/host-arm64'))
    parser.add_argument('--interfaces', nargs='+', choices=('en7', 'en0'), default=['en7', 'en0'])
    parser.add_argument('--rounds', type=int, choices=(1, 2), default=2)
    parser.add_argument('--output', type=Path, default=ROOT/'evidence/local-host-20261001.json')
    args = parser.parse_args()
    results = []
    begin = datetime.now(timezone.utc).isoformat()
    for interface in args.interfaces:
        for trial in range(args.rounds):
            try:
                process = subprocess.run([str(args.build/'ice_local_probe'), interface],
                                         stdin=subprocess.DEVNULL, capture_output=True, timeout=15)
                report = json.loads(process.stdout)
                if not validate(report): raise ValueError('Non-whitelist probe output')
                report['exit_code'] = process.returncode
            except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
                # Do not echo stderr or unexpected output. No log may contain SDP.
                report = {'passed': False, 'error_code': 100}
            results.append({'interface_class': 7 if interface == 'en7' else 0, 'trial': trial+1, 'probe': report})
    document = {'schema': 1, 'scope': 'actual_same_host_UDP_not_phone_not_WAN_not_NAT_hole_punch',
                'started_utc': begin, 'finished_utc': datetime.now(timezone.utc).isoformat(),
                'upstream_commit': json.loads((ROOT/'upstream.json').read_text())['commit'],
                'patch_sha256': hashlib.sha256((ROOT/'libjuice-physical-ipv4.patch').read_bytes()).hexdigest(),
                'all_passed': all(item['probe']['passed'] for item in results), 'trials': results,
                'boundary': {'media_tested': False, 'aes_gcm_tested_here': False, 'phone_tested': False,
                             'wan_tested': False, 'stun_srflx_tested': False, 'turn_relay_tested': False,
                             'tcp_media_used': False, 'raw_sdp_logged': False, 'persistent_service_created': False}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2)+'\n')
    print(json.dumps({'all_passed': document['all_passed'], 'trials': len(results)}))
    raise SystemExit(0 if document['all_passed'] else 1)


if __name__ == '__main__':
    main()
