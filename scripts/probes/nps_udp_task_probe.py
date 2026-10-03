#!/usr/bin/env python3
"""Bounded authenticated NPS UDP-task roundtrip diagnostic, never media acceptance.

Importing opens nothing. The server is only 127.0.0.1:45965. The client is only
macOS, binds/readbacks an en interface and its IPv4 address, and sends only to
146.56.249.175:15556 or :15558. Each invocation reads a separate owner-controlled
0600 regular file containing exactly 32 random raw bytes; no key/nonce is output.
An NPS UDP task may carry these packets over reliable QUIC streams internally.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import struct
import subprocess
import sys
import time

HOST = '146.56.249.175'
PORTS = (15556, 15558)
SERVER_HOST, SERVER_PORT = '127.0.0.1', 45965
IP_BOUND_IF = 25  # Darwin's IPv4 socket option, not an option on Linux.
BODY = struct.Struct('!4sBBHQ16s')
PACKET_BYTES = BODY.size + hashlib.sha256().digest_size
MAX_SEQUENCE = 32
MAX_ACCEPTED = 128
SCOPE = 'UDP_TASK_DIAGNOSTIC_NOT_MEDIA'


class ProbeError(Exception):
    """Only literal nonsecret codes reach output; never format an OS exception."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def bounded(value, minimum, maximum, code):
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ProbeError(code)
    return value


def load_secret(path):
    """Check the opened inode, so a path swap cannot bypass mode/owner checks."""
    flags = (os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_CLOEXEC', 0)
             | getattr(os, 'O_NOFOLLOW', 0))
    if not hasattr(os, 'O_NOFOLLOW'):
        raise ProbeError('secret_nofollow_unavailable')
    try:
        fd = os.open(Path(path), flags)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_uid != os.geteuid() or info.st_nlink != 1):
                raise ProbeError('secret_permissions')
            if info.st_size != 32:
                raise ProbeError('secret_length')
            value = os.read(fd, 33)
            if len(value) != 32:
                raise ProbeError('secret_length')
            return value
        finally:
            os.close(fd)
    except OSError:
        raise ProbeError('secret_unavailable') from None


def encode(secret, kind, sequence, nonce):
    if (type(secret) is not bytes or len(secret) != 32 or kind not in (1, 2)
            or type(sequence) is not int or not 1 <= sequence <= MAX_SEQUENCE
            or type(nonce) is not bytes or len(nonce) != 16 or nonce == bytes(16)):
        raise ProbeError('packet_fields')
    body = BODY.pack(b'HGNP', 1, kind, 0, sequence, nonce)
    return body + hmac.digest(secret, body, 'sha256')


def decode(secret, packet, expected_kind):
    if type(packet) is not bytes or len(packet) != PACKET_BYTES:
        raise ProbeError('packet_length')
    body, signature = packet[:BODY.size], packet[BODY.size:]
    if not hmac.compare_digest(signature, hmac.digest(secret, body, 'sha256')):
        raise ProbeError('packet_authentication')
    magic, version, kind, reserved, sequence, nonce = BODY.unpack(body)
    if (magic != b'HGNP' or version != 1 or kind != expected_kind or reserved != 0
            or not 1 <= sequence <= MAX_SEQUENCE or nonce == bytes(16)):
        raise ProbeError('packet_fields')
    return sequence, nonce


class Responder:
    """Constant upper bound; authenticated old pairs are never evicted/reaccepted."""
    def __init__(self, secret):
        self.secret = secret
        self.seen = set()
        self.counts = dict(requests=0, accepted=0, invalid=0, foreign=0, replay=0, capacity=0)

    def accept(self, packet, peer):
        self.counts['requests'] += 1
        if peer[0] != SERVER_HOST:
            self.counts['foreign'] += 1
            return None
        try:
            sequence, nonce = decode(self.secret, packet, 1)
        except ProbeError:
            self.counts['invalid'] += 1
            return None
        identity = (nonce, sequence)
        if identity in self.seen:
            self.counts['replay'] += 1
            return None
        if len(self.seen) >= MAX_ACCEPTED:
            self.counts['capacity'] += 1
            return None
        self.seen.add(identity)
        self.counts['accepted'] += 1
        return encode(self.secret, 2, sequence, nonce)


def remaining(deadline, clock):
    return max(0.0, deadline - clock())


def serve(secret, max_runtime, *, clock=time.monotonic, socket_factory=socket.socket):
    bounded(max_runtime, .1, 60, 'runtime_bound')
    start = clock()
    deadline = start + max_runtime
    engine = Responder(secret)
    responses, send_errors = 0, 0
    with socket_factory(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        # No address/port arguments and no SO_REUSE*, so no live-service takeover.
        sock.bind((SERVER_HOST, SERVER_PORT))
        while remaining(deadline, clock) > 0:
            sock.settimeout(min(.1, remaining(deadline, clock)))
            try:
                packet, peer = sock.recvfrom(PACKET_BYTES + 1)
            except socket.timeout:
                continue
            response = engine.accept(packet, peer)
            if response is None or remaining(deadline, clock) <= 0:
                continue
            sock.settimeout(min(.1, remaining(deadline, clock)))
            try:
                if sock.sendto(response, peer) == PACKET_BYTES:
                    responses += 1
                else:
                    send_errors += 1
            except OSError:
                send_errors += 1
    return dict(scope=SCOPE, status='SERVER_FINISHED', runtime_ms=round((clock()-start)*1000, 3),
                server_port=SERVER_PORT, packet_bytes=PACKET_BYTES, responses=responses,
                send_errors=send_errors, media_acceptance=0, **engine.counts)


def physical_socket(interface, deadline, *, clock=time.monotonic, platform=sys.platform,
                    socket_factory=socket.socket, interface_index=socket.if_nametoindex,
                    run=subprocess.run):
    if platform != 'darwin':
        raise ProbeError('client_requires_macos')
    if type(interface) is not str or not re.fullmatch(r'en[0-9]+', interface):
        raise ProbeError('physical_interface_required')
    if remaining(deadline, clock) <= 0:
        raise ProbeError('runtime_expired')
    try:
        index = interface_index(interface)
        if type(index) is not int or index <= 0:
            raise ProbeError('physical_interface_missing')
        result = run(['/usr/sbin/ipconfig', 'getifaddr', interface], capture_output=True,
                     text=True, timeout=min(1.0, remaining(deadline, clock)), check=False)
        if result.returncode != 0:
            raise ProbeError('physical_ipv4_missing')
        address = str(ipaddress.IPv4Address(result.stdout.strip()))
        ip = ipaddress.IPv4Address(address)
        if ip.is_unspecified or ip.is_loopback or ip.is_multicast:
            raise ProbeError('physical_ipv4_invalid')
        sock = socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.IPPROTO_IP, IP_BOUND_IF, index)
            if sock.getsockopt(socket.IPPROTO_IP, IP_BOUND_IF) != index:
                raise ProbeError('physical_bind_readback')
            sock.bind((address, 0))
            return sock, index, address
        except BaseException:
            sock.close()
            raise
    except ProbeError:
        raise
    except (OSError, ValueError, subprocess.SubprocessError):
        raise ProbeError('physical_bind_failed') from None


def client(secret, port, interface, count, timeout, max_runtime, *, clock=time.monotonic,
           nonce_factory=secrets.token_bytes, physical_factory=physical_socket):
    if type(port) is not int or port not in PORTS:
        raise ProbeError('destination_not_allowed')
    if type(count) is not int or not 1 <= count <= MAX_SEQUENCE:
        raise ProbeError('count_bound')
    bounded(timeout, .05, 2, 'timeout_bound')
    bounded(max_runtime, .1, 60, 'runtime_bound')
    start = clock()
    deadline = start + max_runtime
    sock, index, address = physical_factory(interface, deadline, clock=clock)
    attempted, accepted, invalid, foreign, unmatched, errors = 0, 0, 0, 0, 0, 0
    rtts = []
    with sock:
        if remaining(deadline, clock) <= 0:
            raise ProbeError('runtime_expired')
        sock.settimeout(min(timeout, remaining(deadline, clock)))
        sock.connect((HOST, port))
        # Verify after connect too; never retry with an unbound or TUN socket.
        if sock.getsockopt(socket.IPPROTO_IP, IP_BOUND_IF) != index or sock.getsockname()[0] != address:
            raise ProbeError('physical_route_readback')
        for sequence in range(1, count+1):
            if remaining(deadline, clock) <= 0:
                break
            nonce = nonce_factory(16)
            request = encode(secret, 1, sequence, nonce)
            attempted += 1
            sent = clock()
            request_deadline = min(deadline, sent + timeout)
            sock.settimeout(remaining(request_deadline, clock))
            try:
                if sock.send(request) != PACKET_BYTES:
                    errors += 1
                    continue
                while remaining(request_deadline, clock) > 0:
                    sock.settimeout(remaining(request_deadline, clock))
                    response, peer = sock.recvfrom(PACKET_BYTES + 1)
                    if peer != (HOST, port):
                        foreign += 1
                        continue
                    try:
                        returned_sequence, returned_nonce = decode(secret, response, 2)
                    except ProbeError:
                        invalid += 1
                        continue
                    if returned_sequence != sequence or not hmac.compare_digest(returned_nonce, nonce):
                        unmatched += 1
                        continue
                    if clock() > request_deadline:
                        break
                    accepted += 1
                    rtts.append(round((clock()-sent)*1000, 3))
                    break
            except socket.timeout:
                continue
            except OSError:
                errors += 1
    status = 'PASS' if accepted == count else 'PARTIAL' if accepted else 'TIMEOUT'
    return dict(scope=SCOPE, status=status, destination_port=port, interface_index=index,
                physical_bind_verified=1, packet_bytes=PACKET_BYTES, requested=count,
                attempted=attempted, responses=accepted, invalid=invalid, foreign=foreign,
                unmatched=unmatched, socket_errors=errors, rtt_ms=rtts,
                runtime_ms=round((clock()-start)*1000, 3), media_acceptance=0)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ProbeError('invalid_arguments')


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument('mode', choices=('server', 'client'))
    parser.add_argument('--secret-file', required=True)
    parser.add_argument('--max-runtime', type=float, default=30)
    parser.add_argument('--port', type=int)
    parser.add_argument('--interface')
    parser.add_argument('--count', type=int, default=4)
    parser.add_argument('--timeout', type=float, default=1)
    try:
        args = parser.parse_args(argv)
        bounded(args.max_runtime, .1, 60, 'runtime_bound')
        if args.mode == 'server' and (args.port is not None or args.interface is not None):
            raise ProbeError('server_endpoint_fixed')
        if args.mode == 'client' and (args.port not in PORTS or args.interface is None):
            raise ProbeError('client_endpoint_required')
        secret = load_secret(args.secret_file)
        report = (serve(secret, args.max_runtime) if args.mode == 'server' else
                  client(secret, args.port, args.interface, args.count, args.timeout, args.max_runtime))
        code = 0 if report['status'] in ('SERVER_FINISHED', 'PASS') else 2
    except ProbeError as error:
        report, code = dict(scope=SCOPE, status='ERROR', reason=error.code, media_acceptance=0), 2
    except KeyboardInterrupt:
        report, code = dict(scope=SCOPE, status='CANCELLED', media_acceptance=0), 130
    except Exception:
        report, code = dict(scope=SCOPE, status='ERROR', reason='operation_failed', media_acceptance=0), 2
    print(json.dumps(report, sort_keys=True, separators=(',', ':')))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
