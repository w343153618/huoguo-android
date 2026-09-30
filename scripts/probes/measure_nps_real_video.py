#!/usr/bin/env python3
"""Receive a real Android/VideoToolbox H.264 stream through the NPS test port.

This measures the M1 gateway -> NPS -> M1 receiver transport leg. It does not
include a phone decoder or the phone's mobile network. Set the existing M1 test
account password in HUOGUO_TEST_PASSWORD; it is never written to the report.
"""

import argparse
import base64
import json
import os
import socket
import ssl
import statistics
import struct
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

CONFIG_FLAG = 1 << 62
PTS_MASK = (1 << 61) - 1


def read_exact(sock, length):
    chunks = bytearray()
    while len(chunks) < length:
        data = sock.recv(length - len(chunks))
        if not data:
            raise EOFError('stream ended')
        chunks.extend(data)
    return bytes(chunks)


def headers(sock):
    result = bytearray()
    while not result.endswith(b'\r\n\r\n'):
        if len(result) > 8192:
            raise ValueError('oversized HTTP headers')
        result.extend(read_exact(sock, 1))
    first, *lines = result.decode('iso-8859-1').split('\r\n')
    status = int(first.split(' ', 2)[1])
    parsed = {}
    for line in lines:
        if ':' in line:
            key, value = line.split(':', 1)
            parsed[key.lower()] = value.strip()
    return status, parsed


def connect(host, port, interface, context):
    raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        raw.setsockopt(socket.IPPROTO_IP, 25, socket.if_nametoindex(interface))
        raw.settimeout(20)
        raw.connect((host, port))
        source_ip = raw.getsockname()[0]
        stream = context.wrap_socket(raw, server_hostname=host)
        stream.settimeout(20)
        return stream, source_ip
    except Exception:
        raw.close()
        raise


def percentile(values, quantile):
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * quantile
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (rank - low), 2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('transport', choices=('tcp', 'quic', 'kcp'))
    parser.add_argument('--host', default='146.56.249.175')
    parser.add_argument('--port', type=int, default=15557)
    parser.add_argument('--interface', default='en9')
    parser.add_argument('--duration', type=int, default=45)
    parser.add_argument('--fps', type=int, default=60)
    parser.add_argument('--bitrate', type=int, default=4000000)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    password = os.environ.get('HUOGUO_TEST_PASSWORD')
    if not password:
        raise SystemExit('HUOGUO_TEST_PASSWORD is required')
    if not 10 <= args.duration <= 120:
        parser.error('duration must be 10-120 seconds')
    ca = Path.home() / '.config/sunshine/credentials/cacert.pem'
    context = ssl.create_default_context(cafile=ca)
    context.check_hostname = False  # The exact private CA is pinned; IP varies.
    auth = 'Basic ' + base64.b64encode(('huoguo:' + password).encode()).decode()
    sockets = []
    sources = []
    stop = threading.Event()
    errors = []
    frames = []
    audio_packets = 0
    audio_age_ms = []
    try:
        stream, source_ip = connect(args.host, args.port, args.interface, context)
        sockets.append(stream)
        sources.append(source_ip)
        body = json.dumps({'max_size': 1200, 'max_fps': args.fps,
                           'video_bit_rate': args.bitrate,
                           'bitrate_mode': 'ADAPTIVE_VBR'}).encode()
        request = (f'POST /session HTTP/1.1\r\nHost: {args.host}\r\n'
                   f'Authorization: {auth}\r\nContent-Type: application/json\r\n'
                   f'Content-Length: {len(body)}\r\nConnection: close\r\n\r\n').encode() + body
        stream.sendall(request)
        status, fields = headers(stream)
        response = read_exact(stream, int(fields.get('content-length', '0')))
        if status != 200:
            raise RuntimeError(f'gateway session rejected: HTTP {status}')
        metadata = json.loads(response)
        sid = metadata['session']
        stream.close()
        sockets.remove(stream)

        channels = {}
        for role in ('video', 'audio', 'control'):
            channel, source_ip = connect(args.host, args.port, args.interface, context)
            sockets.append(channel)
            sources.append(source_ip)
            channel.sendall((f'CONNECT /stream/{sid}/{role} HTTP/1.1\r\n'
                             f'Host: {args.host}\r\nAuthorization: {auth}\r\n\r\n').encode())
            status, _ = headers(channel)
            if status != 200:
                raise RuntimeError(f'{role} channel rejected: HTTP {status}')
            channels[role] = channel

        def drain_audio():
            nonlocal audio_packets
            try:
                if read_exact(channels['audio'], 4) != b'\x00aac':
                    raise ValueError('AAC header mismatch')
                while not stop.is_set():
                    pts, length = struct.unpack('>QI', read_exact(channels['audio'], 12))
                    if length > 1024 * 1024:
                        raise ValueError('oversized audio packet')
                    read_exact(channels['audio'], length)
                    audio_packets += 1
                    if not pts & CONFIG_FLAG:
                        audio_age_ms.append(round((time.time_ns() // 1000 -
                                                   (pts & PTS_MASK)) / 1000, 2))
            except (EOFError, OSError):
                pass
            except Exception as exc:
                errors.append('audio:' + type(exc).__name__)

        def drain_control():
            try:
                while not stop.is_set():
                    if not channels['control'].recv(4096):
                        return
            except OSError:
                pass

        threading.Thread(target=drain_audio, daemon=True).start()
        threading.Thread(target=drain_control, daemon=True).start()
        video = channels['video']
        video.settimeout(2)
        if read_exact(video, 4) != b'h264':
            raise ValueError('H.264 header mismatch')
        started = time.monotonic()
        deadline = started + args.duration
        while time.monotonic() < deadline:
            try:
                hi = struct.unpack('>I', read_exact(video, 4))[0]
                if hi & 0x80000000:
                    read_exact(video, 8)
                    continue
                low, length = struct.unpack('>II', read_exact(video, 8))
                if length > 8 * 1024 * 1024:
                    raise ValueError('oversized video packet')
                read_exact(video, length)
                pts = (hi << 32) | low
                if not pts & CONFIG_FLAG:
                    frames.append((time.monotonic() - started, pts & PTS_MASK,
                                   length, round((time.time_ns() // 1000 -
                                                  (pts & PTS_MASK)) / 1000, 2)))
            except socket.timeout:
                continue
        # Exclude the first three seconds of encoder startup from steady-state FPS.
        steady = [f for f in frames if f[0] >= 3]
        arrivals = [round((b[0] - a[0]) * 1000, 2) for a, b in zip(steady, steady[1:])]
        pts_gaps = [round((b[1] - a[1]) / 1000, 2) for a, b in zip(steady, steady[1:])]
        worst_index = max(range(len(arrivals)), key=arrivals.__getitem__) if arrivals else None
        second_counts = [sum(i <= frame[0] < i + 1 for frame in steady)
                         for i in range(3, args.duration)]
        video_ages = [frame[3] for frame in steady]
        report = {
            'transport': args.transport,
            'timestamp_utc': datetime.now(timezone.utc).isoformat(),
            'target': f'{args.host}:{args.port}',
            'physical_interface': args.interface,
            'physical_source_ips': sorted(set(sources)),
            'duration_seconds': args.duration,
            'requested_fps': args.fps,
            'requested_bitrate_bps': args.bitrate,
            'gateway_video_backend': metadata.get('video_backend'),
            'frames_all': len(frames),
            'steady_mean_fps': round(len(steady) / max(1, args.duration - 3), 2),
            'steady_second_fps_p10': percentile(second_counts, .1),
            'steady_second_fps_min': min(second_counts) if second_counts else None,
            'arrival_gap_ms': {'p50': percentile(arrivals, .5),
                               'p95': percentile(arrivals, .95),
                               'p99': percentile(arrivals, .99),
                               'max': max(arrivals) if arrivals else None,
                               'worst_at_second': round(steady[worst_index][0], 2)
                               if worst_index is not None else None},
            'source_pts_gap_ms': {'p50': percentile(pts_gaps, .5),
                                  'p95': percentile(pts_gaps, .95),
                                  'max': max(pts_gaps) if pts_gaps else None,
                                  'at_worst_arrival': pts_gaps[worst_index]
                                  if worst_index is not None else None},
            'video_source_to_receive_ms': {'p50': percentile(video_ages, .5),
                                           'p95': percentile(video_ages, .95)},
            'audio_source_to_receive_ms': {'p50': percentile(audio_age_ms, .5),
                                           'p95': percentile(audio_age_ms, .95)},
            'video_minus_audio_receive_age_ms': round(
                percentile(video_ages, .5) - percentile(audio_age_ms, .5), 2)
                if video_ages and audio_age_ms else None,
            'received_video_mbps': round(sum(f[2] for f in steady) * 8 /
                                         max(1, args.duration - 3) / 1e6, 3),
            'audio_packets': audio_packets,
            'errors': errors,
            'second_counts': second_counts,
        }
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({k: v for k, v in report.items() if k != 'second_counts'},
                         ensure_ascii=False))
    finally:
        stop.set()
        for stream in sockets:
            try:
                stream.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            stream.close()


if __name__ == '__main__':
    main()
