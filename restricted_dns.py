#!/usr/bin/env python3
"""Unprivileged, bounded UDP DNS outlet for the isolated Android candidate.

The supervisor binds 127.0.0.1:53 and IPv6-only [::1]:53 first, then passes
both sockets to ``serve``
after dropping privileges. Every upstream request goes to the fixed numeric
223.5.5.5:53, bound to the supervisor-selected Darwin physical interface.
There is no generic forwarding, TCP fallback, hostname resolver or client API
for selecting an upstream. The emulator's separate sandbox must prohibit
bypass, and the web outlet must recheck/pin its final destination addresses.

This deliberately small wire parser accepts one uncompressed IN question for
A, AAAA, CNAME, SVCB or HTTPS, optionally with one empty EDNS0 OPT. It rejects
DNSSEC requests, arbitrary EDNS options, zone transfers, reverse queries and
private namespace queries. Responses may use validated backward compression.
Truncated/oversized replies fail closed; this is not a full recursive resolver.
It records only aggregate counters, never names, packets or client addresses.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import ipaddress
import os
import queue
import re
import secrets
import select
import signal
import socket
import struct
import sys
import threading
import time


LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 53
PUBLIC_RESOLVER = ("223.5.5.5", 53)
IP_BOUND_IF = getattr(socket, "IP_BOUND_IF", 25)
MAX_PACKET = 1232
MAX_RECORDS = 64
ALLOWED_TYPES = frozenset((1, 5, 28, 64, 65))
_LABEL = re.compile(rb"[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?\Z")
_PRIVATE_SUFFIXES = (b"local", b"localhost", b"home", b"lan", b"internal",
                     b"invalid", b"test", b"onion", b"in-addr.arpa", b"ip6.arpa",
                     b"home.arpa")


class InvalidDNS(ValueError):
    """A fixed reason only; never put a queried name into the exception."""


@dataclass(frozen=True)
class DNSPolicy:
    workers: int = 4
    pending: int = 8
    timeout: float = 1.0
    retries: int = 1
    requests_per_second: int = 64

    def __post_init__(self):
        if (not 1 <= self.workers <= 16 or not 1 <= self.pending <= 64
                or not 0 < self.timeout <= 3 or self.retries not in (0, 1)
                or not 1 <= self.requests_per_second <= 256):
            raise ValueError("invalid bounded DNS policy")


@dataclass(frozen=True)
class Question:
    identifier: int
    flags: int
    name: bytes
    qtype: int
    question_end: int


def _name(packet: bytes, offset: int, *, compression: bool = True) -> tuple[bytes, int]:
    labels: list[bytes] = []
    cursor, after, expanded, hops = offset, None, 1, 0
    while True:
        if cursor >= len(packet):
            raise InvalidDNS("name_out_of_bounds")
        size = packet[cursor]
        if size & 0xC0 == 0xC0:
            if not compression or cursor + 1 >= len(packet):
                raise InvalidDNS("invalid_compression")
            target = ((size & 0x3F) << 8) | packet[cursor + 1]
            # Strict backward-only compression also excludes cycles.
            if target < 12 or target >= cursor or hops >= 16:
                raise InvalidDNS("invalid_compression")
            if after is None:
                after = cursor + 2
            cursor, hops = target, hops + 1
            continue
        if size & 0xC0:
            raise InvalidDNS("invalid_label")
        cursor += 1
        if size == 0:
            return b".".join(labels).lower(), after if after is not None else cursor
        if size > 63 or cursor + size > len(packet):
            raise InvalidDNS("invalid_label")
        label = packet[cursor:cursor + size]
        if not _LABEL.fullmatch(label):
            raise InvalidDNS("invalid_label")
        expanded += size + 1
        if expanded > 255:
            raise InvalidDNS("name_too_long")
        labels.append(label)
        cursor += size


def _public_name(name: bytes) -> None:
    if (len(name.split(b".")) < 2 or any(
            name == suffix or name.endswith(b"." + suffix)
            for suffix in _PRIVATE_SUFFIXES)):
        raise InvalidDNS("private_namespace")


def _public_address(raw: bytes) -> None:
    address = ipaddress.ip_address(raw)
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    if (not address.is_global or address.is_multicast or address.is_reserved
            or address.is_loopback or address.is_link_local or address.is_unspecified
            or (address.version == 6 and (address.sixtofour or address.teredo))):
        raise InvalidDNS("nonpublic_answer")


def _question(packet: bytes, *, compression: bool) -> tuple[Question, tuple[int, ...]]:
    if not 12 <= len(packet) <= MAX_PACKET:
        raise InvalidDNS("invalid_length")
    header = struct.unpack_from("!6H", packet)
    if header[2] != 1:
        raise InvalidDNS("question_count")
    name, cursor = _name(packet, 12, compression=compression)
    if cursor + 4 > len(packet):
        raise InvalidDNS("incomplete_question")
    qtype, qclass = struct.unpack_from("!HH", packet, cursor)
    if qclass != 1 or qtype not in ALLOWED_TYPES:
        raise InvalidDNS("question_not_allowed")
    _public_name(name)
    return Question(header[0], header[1], name, qtype, cursor + 4), header


def _opt(packet: bytes, start: int, *, request: bool) -> int:
    # The sole additional request must be an uncompressed root-name OPT.
    if start + 11 > len(packet) or packet[start] != 0:
        raise InvalidDNS("invalid_opt")
    kind, size, ttl, length = struct.unpack_from("!HHIH", packet, start + 1)
    if (kind != 41 or not 512 <= size <= MAX_PACKET or ttl != 0
            or (request and length != 0) or start + 11 + length > len(packet)):
        raise InvalidDNS("invalid_opt")
    cursor, end = start + 11, start + 11 + length
    while cursor < end:
        if cursor + 4 > end:
            raise InvalidDNS("invalid_opt")
        _, option_length = struct.unpack_from("!HH", packet, cursor)
        cursor += 4 + option_length
        if cursor > end:
            raise InvalidDNS("invalid_opt")
    return end


def parse_query(packet: bytes) -> Question:
    question, header = _question(packet, compression=False)
    # Only RD, AD and CD are query flags. No QR/opcode/TC/rcode/reserved flags.
    if header[1] & ~0x0130 or header[3] or header[4] or header[5] not in (0, 1):
        raise InvalidDNS("query_flags_or_counts")
    end = _opt(packet, question.question_end, request=True) if header[5] else question.question_end
    if end != len(packet):
        raise InvalidDNS("trailing_bytes")
    return question


def _record(packet: bytes, start: int) -> tuple[int, int]:
    _, cursor = _name(packet, start)
    if cursor + 10 > len(packet):
        raise InvalidDNS("incomplete_record")
    kind, rclass, _, size = struct.unpack_from("!HHIH", packet, cursor)
    data, end = cursor + 10, cursor + 10 + size
    if end > len(packet):
        raise InvalidDNS("incomplete_record")
    if kind == 41:
        # OPT must be root and handled with its complete options validation.
        return kind, _opt(packet, start, request=False)
    if rclass != 1:
        raise InvalidDNS("record_class")
    if kind in (1, 28):
        if size != (4 if kind == 1 else 16):
            raise InvalidDNS("address_length")
        _public_address(packet[data:end])
    elif kind in (2, 5, 12):
        _, consumed = _name(packet, data)
        if consumed != end:
            raise InvalidDNS("record_name_length")
    elif kind == 6:  # SOA, needed for negative answers.
        _, cursor = _name(packet, data)
        _, cursor = _name(packet, cursor)
        if cursor + 20 != end:
            raise InvalidDNS("soa_length")
    elif kind in (64, 65):
        if size < 3:
            raise InvalidDNS("service_record_length")
        _, cursor = _name(packet, data + 2)
        previous = -1
        while cursor < end:
            if cursor + 4 > end:
                raise InvalidDNS("service_parameter_length")
            key, length = struct.unpack_from("!HH", packet, cursor)
            cursor += 4
            if key <= previous or cursor + length > end:
                raise InvalidDNS("service_parameter_order")
            if key in (4, 6):  # ipv4hint / ipv6hint may not point into the LAN.
                stride = 4 if key == 4 else 16
                if not length or length % stride:
                    raise InvalidDNS("service_hint_length")
                for offset in range(cursor, cursor + length, stride):
                    _public_address(packet[offset:offset + stride])
            cursor, previous = cursor + length, key
        if cursor != end:
            raise InvalidDNS("service_record_length")
    return kind, end


def validate_response(packet: bytes, question: Question, identifier: int) -> bytes:
    response, header = _question(packet, compression=True)
    flags = header[1]
    if (response.identifier != identifier or not flags & 0x8000
            or flags & 0x7A40 or flags & 0xF not in (0, 1, 2, 3, 4, 5)
            or bool(flags & 0x100) != bool(question.flags & 0x100)
            or response.name != question.name or response.qtype != question.qtype
            or sum(header[3:]) > MAX_RECORDS):
        raise InvalidDNS("unmatched_response")
    cursor, opt_count = response.question_end, 0
    for section, count in enumerate(header[3:]):
        for _ in range(count):
            kind, cursor = _record(packet, cursor)
            if kind == 41:
                opt_count += 1
                if section != 2 or opt_count > 1:
                    raise InvalidDNS("unexpected_opt")
    if cursor != len(packet):
        raise InvalidDNS("trailing_bytes")
    return struct.pack("!H", question.identifier) + packet[2:]


def failure_response(packet: bytes, question: Question) -> bytes:
    # Echo only a validated single question, no arbitrary additional content.
    flags = 0x8000 | 0x0080 | (question.flags & 0x0110) | 2  # SERVFAIL
    return (struct.pack("!6H", question.identifier, flags, 1, 0, 0, 0)
            + packet[12:question.question_end])


def bind_physical_interface(sock: socket.socket, interface_index: int) -> None:
    if (sys.platform != "darwin" or not isinstance(interface_index, int)
            or isinstance(interface_index, bool) or interface_index <= 0):
        raise ValueError("a Darwin physical interface index is required")
    if not re.fullmatch(r"en[0-9]+", socket.if_indextoname(interface_index)):
        raise ValueError("interface must be an en physical interface")
    sock.setsockopt(socket.IPPROTO_IP, IP_BOUND_IF, interface_index)


def resolve(packet: bytes, question: Question, interface_index: int,
            policy: DNSPolicy, stop_event: threading.Event) -> bytes:
    for _ in range(policy.retries + 1):
        if stop_event.is_set():
            break
        identifier = secrets.randbelow(65536)
        if identifier == question.identifier:
            identifier ^= 1
        upstream = struct.pack("!H", identifier) + packet[2:]
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            bind_physical_interface(sock, interface_index)
            # Numeric fixed tuple: no DNS or client-controlled target lookup.
            sock.connect(PUBLIC_RESOLVER)
            deadline = time.monotonic() + policy.timeout
            sock.send(upstream)
            while not stop_event.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                sock.settimeout(min(remaining, 0.1))
                try:
                    answer, source = sock.recvfrom(MAX_PACKET + 1)
                except socket.timeout:
                    continue
                if source != PUBLIC_RESOLVER:
                    continue
                try:
                    return validate_response(answer, question, identifier)
                except InvalidDNS:
                    continue
    return failure_response(packet, question)


class DNSGuard:
    def __init__(self, bound_socket: socket.socket, interface_index: int,
                 policy: DNSPolicy | None = None,
                 stop_event: threading.Event | None = None,
                 bound_ipv6_socket: socket.socket | None = None):
        if os.getuid() == 0 or os.geteuid() == 0:
            raise PermissionError("DNS serving must not retain root")
        if (bound_socket.family != socket.AF_INET or bound_socket.type != socket.SOCK_DGRAM
                or bound_socket.getsockname()[0] != LISTEN_HOST):
            raise ValueError("a bound IPv4 loopback UDP socket is required")
        if bound_ipv6_socket is not None:
            if (bound_ipv6_socket.family != socket.AF_INET6
                    or bound_ipv6_socket.type != socket.SOCK_DGRAM
                    or bound_ipv6_socket.getsockname()[0] != "::1"
                    or bound_ipv6_socket.getsockname()[1] != bound_socket.getsockname()[1]
                    or bound_ipv6_socket.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY) != 1):
                raise ValueError("same-port IPv6-only ::1 UDP socket is required")
        # Validate interface before accepting queries, without sending anything.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            bind_physical_interface(probe, interface_index)
        self.socket, self.interface_index = bound_socket, interface_index
        # Single-family construction is only for owned synthetic fixtures.
        # Production serve() and CLI require both loopback families.
        self.listeners = (bound_socket,) if bound_ipv6_socket is None else (bound_socket, bound_ipv6_socket)
        self.policy, self.stop = policy or DNSPolicy(), stop_event or threading.Event()
        self.pending: queue.Queue = queue.Queue(maxsize=self.policy.pending)
        self.threads: list[threading.Thread] = []
        self.lock = threading.Lock()
        self.counts = {name: 0 for name in ("accepted", "rejected", "busy", "replied", "failed")}
        self.active, self.max_active = 0, 0
        self.tokens, self.last_token = float(self.policy.requests_per_second), time.monotonic()

    def _count(self, name: str) -> None:
        with self.lock:
            self.counts[name] += 1

    def snapshot(self) -> dict[str, int]:
        with self.lock:
            return {**self.counts, "active": self.active, "max_active": self.max_active,
                    "pending": self.pending.qsize(), "workers": len(self.threads)}

    def _worker(self) -> None:
        while not self.stop.is_set():
            try:
                packet, question, client, reply_socket = self.pending.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                if self.stop.is_set():
                    continue
                with self.lock:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                try:
                    answer = resolve(packet, question, self.interface_index, self.policy, self.stop)
                except (OSError, ValueError):
                    self._count("failed")
                    answer = failure_response(packet, question)
                finally:
                    with self.lock:
                        self.active -= 1
                if not self.stop.is_set():
                    try:
                        reply_socket.sendto(answer, client)
                        self._count("replied")
                    except OSError:
                        self._count("failed")
            finally:
                self.pending.task_done()

    def run(self) -> None:
        try:
            for listener in self.listeners:
                listener.setblocking(False)
            for number in range(self.policy.workers):
                thread = threading.Thread(target=self._worker, name=f"dns-guard-{number}", daemon=False)
                self.threads.append(thread)
                thread.start()
            while not self.stop.is_set():
                try:
                    ready, _, _ = select.select(self.listeners, (), (), 0.1)
                except OSError:
                    if self.stop.is_set():
                        break
                    raise
                for listener in ready:
                    try:
                        packet, client = listener.recvfrom(MAX_PACKET + 1)
                    except BlockingIOError:
                        continue
                    expected = LISTEN_HOST if listener.family == socket.AF_INET else "::1"
                    if client[0] != expected:
                        self._count("rejected")
                        continue
                    try:
                        question = parse_query(packet)
                    except InvalidDNS:
                        self._count("rejected")
                        continue
                    now = time.monotonic()
                    self.tokens = min(float(self.policy.requests_per_second), self.tokens
                                      + (now - self.last_token) * self.policy.requests_per_second)
                    self.last_token = now
                    if self.tokens < 1:
                        self._count("busy")
                        continue
                    self.tokens -= 1
                    try:
                        self.pending.put_nowait((packet, question, client, listener))
                        self._count("accepted")
                    except queue.Full:
                        self._count("busy")
        finally:
            self.stop.set()
            for thread in self.threads:
                thread.join()
            while True:
                try:
                    self.pending.get_nowait()
                    self.pending.task_done()
                except queue.Empty:
                    break
            for listener in self.listeners:
                listener.close()


def serve(bound_socket: socket.socket, *, interface_index: int,
          bound_ipv6_socket: socket.socket | None = None,
          stop_event: threading.Event | None = None, policy: DNSPolicy | None = None) -> None:
    """Own/close both prebound sockets; caller must already have dropped root."""
    try:
        if bound_ipv6_socket is None:
            raise ValueError("production DNS requires both loopback families")
        guard = DNSGuard(bound_socket, interface_index, policy, stop_event, bound_ipv6_socket)
    except BaseException:
        bound_socket.close()
        if bound_ipv6_socket is not None:
            bound_ipv6_socket.close()
        raise
    guard.run()


def drop_privileges(uid: int, gid: int) -> None:
    if uid <= 0 or gid <= 0 or os.geteuid() != 0:
        raise PermissionError("positive dedicated uid/gid and initial root are required")
    os.setgroups([])
    os.setgid(gid)
    os.setuid(uid)
    if os.getuid() != uid or os.geteuid() != uid or os.getgid() != gid or os.getegid() != gid:
        raise PermissionError("privilege drop did not complete")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bound-fd", type=int, required=True)
    parser.add_argument("--bound-ipv6-fd", type=int, required=True)
    parser.add_argument("--interface-index", type=int, required=True)
    parser.add_argument("--uid", type=int)
    parser.add_argument("--gid", type=int)
    args = parser.parse_args()
    # File descriptor provided by the privileged supervisor, not a public bind.
    if args.bound_fd < 0 or args.bound_ipv6_fd < 0 or args.bound_fd == args.bound_ipv6_fd:
        parser.error("two distinct prebound file descriptors are required")
    with socket.socket(fileno=args.bound_fd) as listener, \
            socket.socket(fileno=args.bound_ipv6_fd) as listener_ipv6:
        if (listener.getsockname() != (LISTEN_HOST, LISTEN_PORT)
                or listener_ipv6.getsockname() != ("::1", LISTEN_PORT, 0, 0)):
            parser.error("descriptors must already be bound to 127.0.0.1:53 and [::1]:53")
        if os.geteuid() == 0:
            if args.uid is None or args.gid is None:
                parser.error("root must specify dedicated --uid and --gid")
            drop_privileges(args.uid, args.gid)
        stop = threading.Event()
        for signum in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, lambda *_: stop.set())
        serve(listener, bound_ipv6_socket=listener_ipv6,
              interface_index=args.interface_index, stop_event=stop)


if __name__ == "__main__":
    main()
