#!/usr/bin/env python3
"""Measure a real gateway /ping through an NPS tunnel over a physical Mac NIC.

This is a connection/setup and small-request probe, not a video FPS benchmark.
The test gateway uses a local self-signed certificate, so certificate validation
is disabled only for this read-only probe. No account credentials are sent.
"""

import argparse
import json
import socket
import ssl
import statistics
import time
from datetime import datetime, timezone


def percentile(values, p):
    ordered = sorted(values)
    if not ordered:
        return None
    rank = (len(ordered) - 1) * p
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (rank - low), 2)


def sample(host, port, interface, timeout):
    started = time.perf_counter()
    raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        raw.setsockopt(socket.IPPROTO_IP, 25, socket.if_nametoindex(interface))
        raw.settimeout(timeout)
        raw.connect((host, port))
        connected = time.perf_counter()
        source = raw.getsockname()[0]
        with ssl._create_unverified_context().wrap_socket(raw, server_hostname=host) as stream:
            handshook = time.perf_counter()
            stream.sendall(('GET /ping HTTP/1.1\r\nHost: ' + host +
                            '\r\nConnection: close\r\n\r\n').encode())
            first = stream.recv(4096)
            first_byte = time.perf_counter()
            if not first.startswith(b'HTTP/1.1 200 '):
                raise ValueError('Unexpected HTTP status: ' +
                                 first.split(b'\r\n', 1)[0].decode(errors='replace'))
            while stream.recv(4096):
                pass
        finished = time.perf_counter()
        return {'source_ip': source,
                'connect_ms': round((connected - started) * 1000, 2),
                'tls_ms': round((handshook - connected) * 1000, 2),
                'first_byte_ms': round((first_byte - started) * 1000, 2),
                'total_ms': round((finished - started) * 1000, 2)}
    finally:
        raw.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('transport', choices=('tcp', 'kcp', 'quic'))
    parser.add_argument('--host', default='146.56.249.175')
    parser.add_argument('--port', type=int, default=15557)
    parser.add_argument('--interface', default='en9')
    parser.add_argument('--samples', type=int, default=40)
    parser.add_argument('--warmup', type=int, default=3)
    parser.add_argument('--timeout', type=float, default=8)
    parser.add_argument('--output')
    args = parser.parse_args()
    measurements = []
    failures = []
    for index in range(args.warmup + args.samples):
        try:
            result = sample(args.host, args.port, args.interface, args.timeout)
            if index >= args.warmup:
                measurements.append(result)
        except (OSError, ValueError, ssl.SSLError) as exc:
            if index >= args.warmup:
                failures.append(str(exc))
        time.sleep(0.05)
    timings = [entry['total_ms'] for entry in measurements]
    first_bytes = [entry['first_byte_ms'] for entry in measurements]
    report = {
        'transport': args.transport,
        'timestamp_utc': datetime.now(timezone.utc).isoformat(),
        'target': f'{args.host}:{args.port}',
        'physical_interface': args.interface,
        'physical_source_ips': sorted(set(entry['source_ip'] for entry in measurements)),
        'samples_requested': args.samples,
        'successes': len(measurements),
        'failures': failures,
        'total_ms': {'median': percentile(timings, .5), 'p95': percentile(timings, .95),
                     'max': max(timings) if timings else None},
        'first_byte_ms': {'median': percentile(first_bytes, .5),
                          'p95': percentile(first_bytes, .95)},
        'raw': measurements,
    }
    if args.output:
        with open(args.output, 'w') as file:
            json.dump(report, file, ensure_ascii=False, indent=2)
            file.write('\n')
    print(json.dumps({key: value for key, value in report.items() if key != 'raw'},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
