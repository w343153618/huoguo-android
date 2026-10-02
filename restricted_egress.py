#!/usr/bin/env python3
"""Candidate public-web CONNECT outlet for an isolated Android runtime.

This is a destination boundary, not a complete host sandbox. It listens only
on both 127.0.0.1 and ::1 and permits CONNECT to globally routable unicast addresses on
80/443, plus bodyless absolute-form HTTP GET/HEAD on port 80. Host firewall
rules must separately prevent the guest from bypassing
it. CONNECT payloads are opaque: this does not enforce HTTP semantics inside
an allowed tunnel, authenticate users, or provide a production performance
claim. Do not expose this listener on a physical or Tailnet interface.

Every DNS answer is checked, then the chosen numeric address is used both for
direct connections and SOCKS5 ATYP 1/4 requests. Fake-IP answers fail closed.
The optional SOCKS listener must itself be a loopback numeric address. Keep
the process's file/network permissions restricted independently of this code.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import os
import re
import select
import signal
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
from dataclasses import dataclass


MAX_HEADER_BYTES = 8192
MAX_RESOLVED_ADDRESSES = 32
MAX_PENDING_BYTES = 65536
PUBLIC_PORTS = frozenset((80, 443))
_NAT64 = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))
_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")
_HEADER_NAME = re.compile(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")
_DNS_PROGRAM = """import json,socket,sys
rows=socket.getaddrinfo(sys.argv[1],int(sys.argv[2]),socket.AF_UNSPEC,socket.SOCK_STREAM,socket.IPPROTO_TCP)
if len(rows)>32: raise SystemExit(2)
print(json.dumps([[int(r[0]),r[4][0],r[4][1],r[4][3] if len(r[4])>3 else 0] for r in rows]))
"""
LOG = logging.getLogger("restricted_egress")
_LOG_LOCK = threading.Lock()
_LOG_WINDOW, _LOG_COUNT = 0.0, 0


def _log_connection(status: int, reason: str, seconds: float, counts: tuple[int, int]):
    """Optional access outcomes have fixed size and a shared 60/minute ceiling."""
    if not LOG.isEnabledFor(logging.INFO):
        return
    global _LOG_WINDOW, _LOG_COUNT
    with _LOG_LOCK:
        now = time.monotonic()
        if now - _LOG_WINDOW >= 60:
            _LOG_WINDOW, _LOG_COUNT = now, 0
        if _LOG_COUNT >= 60:
            return
        _LOG_COUNT += 1
    LOG.info("event=connection status=%d reason=%s seconds=%.3f tx=%d rx=%d",
             status, reason, seconds, *counts)


class Rejected(Exception):
    def __init__(self, status: int, reason: str):
        self.status, self.reason = status, reason
        super().__init__(reason)


def parse_authority(authority: str) -> tuple[str, int]:
    """Parse an ASCII CONNECT authority, never a URL or an alternate IP form."""
    if (not authority or len(authority) > 260 or not authority.isascii()
            or any(ord(char) <= 32 or ord(char) == 127 for char in authority)
            or any(char in authority for char in "@/\\?#%")):
        raise Rejected(400, "invalid_authority")
    if authority.startswith("["):
        close = authority.find("]")
        if close < 0 or not authority[close + 1:].startswith(":"):
            raise Rejected(400, "invalid_authority")
        host, port_text = authority[1:close], authority[close + 2:]
        try:
            parsed = ipaddress.ip_address(host)
        except ValueError:
            raise Rejected(400, "invalid_authority") from None
        if parsed.version != 6:
            raise Rejected(400, "invalid_authority")
        host = str(parsed)
    else:
        if authority.count(":") != 1 or "[" in authority or "]" in authority:
            raise Rejected(400, "invalid_authority")
        host, port_text = authority.split(":")
        try:
            parsed = ipaddress.ip_address(host)
            if parsed.version != 4:
                raise ValueError()
            host = str(parsed)
        except ValueError:
            host = (host[:-1] if host.endswith(".") else host).lower()
            labels = host.split(".")
            # Exclude numeric alternatives, search-domain names and empty labels.
            if (not host or len(host) > 253 or len(labels) < 2
                    or all(label.isdecimal() for label in labels)
                    or not all(_LABEL.fullmatch(label) for label in labels)
                    or labels[-1].isdecimal()):
                raise Rejected(400, "invalid_authority") from None
    if not re.fullmatch(r"[0-9]{1,5}", port_text):
        raise Rejected(400, "invalid_authority")
    port = int(port_text)
    if port not in PUBLIC_PORTS:
        raise Rejected(403, "port_not_allowed")
    return host, port


def parse_loopback_socks(endpoint: str) -> tuple[str, int]:
    match = re.fullmatch(r"(?:\[([^\]]+)\]|([^:]+)):([0-9]{1,5})", endpoint)
    if not match:
        raise ValueError("SOCKS endpoint must be a numeric loopback IP and port")
    address = ipaddress.ip_address(match.group(1) or match.group(2))
    port = int(match.group(3))
    if not address.is_loopback or not 1 <= port <= 65535 or "%" in str(address):
        raise ValueError("SOCKS endpoint must be a numeric loopback IP and port")
    return str(address), port


def _public_unicast(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if address.version == 6 and address.ipv4_mapped:
        return _public_unicast(address.ipv4_mapped)
    if address.version == 6 and (address.sixtofour or address.teredo
                                 or any(address in prefix for prefix in _NAT64)):
        return False
    # Some Python versions report multicast as global; never rely on that alone.
    return (address.is_global and not address.is_multicast and not address.is_reserved
            and not address.is_loopback and not address.is_link_local
            and not address.is_unspecified)


def _normalized(address: str) -> str:
    parsed = ipaddress.ip_address(address)
    return str(parsed.ipv4_mapped if parsed.version == 6 and parsed.ipv4_mapped else parsed)


@dataclass(frozen=True)
class EgressPolicy:
    deny_self_ips: frozenset[str] = frozenset()
    upstream_socks: tuple[str, int] | None = None
    max_clients: int = 8
    header_timeout: float = 3.0
    dns_timeout: float = 3.0
    connect_timeout: float = 4.0
    establishment_timeout: float = 12.0
    idle_timeout: float = 30.0
    total_timeout: float = 300.0

    def __post_init__(self):
        object.__setattr__(self, "deny_self_ips",
                           frozenset(_normalized(address) for address in self.deny_self_ips))
        if self.upstream_socks is not None:
            host, port = self.upstream_socks
            address = ipaddress.ip_address(host)
            if not address.is_loopback or not 1 <= port <= 65535 or "%" in host:
                raise ValueError("upstream must be a numeric loopback endpoint")
            object.__setattr__(self, "upstream_socks", (str(address), port))
        if not 1 <= self.max_clients <= 64:
            raise ValueError("max_clients must be between 1 and 64")
        for value in (self.header_timeout, self.dns_timeout, self.connect_timeout,
                      self.establishment_timeout, self.idle_timeout, self.total_timeout):
            if not 0 < value <= 3600:
                raise ValueError("timeouts must be between 0 and 3600 seconds")

    def check_address(self, address: str) -> str:
        try:
            if "%" in address:
                raise ValueError()
            parsed = ipaddress.ip_address(address)
        except ValueError:
            raise Rejected(502, "invalid_dns_answer") from None
        if not _public_unicast(parsed):
            raise Rejected(403, "destination_not_public")
        if _normalized(address) in self.deny_self_ips:
            raise Rejected(403, "self_destination")
        return str(parsed)


def resolve_public(host: str, port: int, policy: EgressPolicy,
                   deadline: float) -> list[str]:
    """Resolve in a bounded child so a blocked system resolver cannot pin a worker."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        remaining = min(policy.dns_timeout, deadline - time.monotonic())
        if remaining <= 0:
            raise Rejected(504, "dns_timeout")
        try:
            result = subprocess.run(
                [sys.executable, "-I", "-c", _DNS_PROGRAM, host, str(port)],
                capture_output=True, timeout=remaining, check=False,
                # Avoid inherited Python hooks; no credentials are read or logged.
                env={"PATH": "/usr/bin:/bin"},
            )
        except subprocess.TimeoutExpired:
            raise Rejected(504, "dns_timeout") from None
        except OSError:
            raise Rejected(502, "dns_failed") from None
        if result.returncode != 0 or len(result.stdout) > 16384:
            raise Rejected(502, "dns_failed")
        try:
            records = json.loads(result.stdout)
        except (ValueError, UnicodeError):
            raise Rejected(502, "invalid_dns_answer") from None
        if not isinstance(records, list) or not 1 <= len(records) <= MAX_RESOLVED_ADDRESSES:
            raise Rejected(502, "invalid_dns_answer")
        checked = []
        for row in records:
            if (not isinstance(row, list) or len(row) != 4
                    or row[0] not in (socket.AF_INET, socket.AF_INET6)
                    or not isinstance(row[1], str) or row[2] != port or row[3] != 0):
                raise Rejected(502, "invalid_dns_answer")
            address = policy.check_address(row[1])
            expected_family = socket.AF_INET6 if ":" in address else socket.AF_INET
            if row[0] != expected_family:
                raise Rejected(502, "invalid_dns_answer")
            checked.append(address)
        return list(dict.fromkeys(checked))
    else:
        return [policy.check_address(host)]


def open_numeric(address: str, port: int, timeout: float) -> socket.socket:
    parsed = ipaddress.ip_address(address)
    family = socket.AF_INET if parsed.version == 4 else socket.AF_INET6
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        # Numeric sockaddr only: no DNS result is handed back to connect().
        endpoint = (str(parsed), port) if family == socket.AF_INET else (str(parsed), port, 0, 0)
        sock.connect(endpoint)
        return sock
    except BaseException:
        sock.close()
        raise


def _recv_exact(sock: socket.socket, count: int, deadline: float) -> bytes:
    result = bytearray()
    while len(result) < count:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("socks_timeout")
        sock.settimeout(remaining)
        block = sock.recv(count - len(result))
        if not block:
            raise OSError("socks_closed")
        result.extend(block)
    return bytes(result)


def connect_checked(addresses: list[str], port: int, policy: EgressPolicy,
                    deadline: float, stopping: threading.Event | None = None) -> socket.socket:
    # resolve_public checks ALL records before this function is called.
    for address in addresses[:8]:
        if stopping is not None and stopping.is_set():
            raise Rejected(503, "shutting_down")
        checked = policy.check_address(address)
        remaining = min(policy.connect_timeout, deadline - time.monotonic())
        if remaining <= 0:
            raise Rejected(504, "connect_timeout")
        connected = None
        try:
            if policy.upstream_socks is None:
                return open_numeric(checked, port, remaining)
            upstream_host, upstream_port = policy.upstream_socks
            handshake_deadline = min(deadline, time.monotonic() + remaining)
            connected = open_numeric(upstream_host, upstream_port, remaining)
            connected.sendall(b"\x05\x01\x00")
            if _recv_exact(connected, 2, handshake_deadline) != b"\x05\x00":
                raise OSError("socks_auth_unavailable")
            parsed = ipaddress.ip_address(checked)
            atyp = b"\x01" if parsed.version == 4 else b"\x04"
            connected.sendall(b"\x05\x01\x00" + atyp + parsed.packed + port.to_bytes(2, "big"))
            response = _recv_exact(connected, 4, handshake_deadline)
            if response[:3] != b"\x05\x00\x00":
                raise OSError("socks_connect_failed")
            if response[3] == 1:
                count = 4
            elif response[3] == 4:
                count = 16
            elif response[3] == 3:
                count = _recv_exact(connected, 1, handshake_deadline)[0]
                if not count:
                    raise OSError("socks_invalid_response")
            else:
                raise OSError("socks_invalid_response")
            _recv_exact(connected, count + 2, handshake_deadline)
            return connected
        except (OSError, TimeoutError):
            if connected is not None:
                connected.close()
    raise Rejected(502, "destination_unreachable")


@dataclass(frozen=True)
class Request:
    host: str
    port: int
    initial: bytes
    connect: bool


def read_request(sock: socket.socket, deadline: float) -> Request:
    data = bytearray()
    while True:
        index = data.find(b"\r\n\r\n")
        if index >= 0:
            if index + 4 > MAX_HEADER_BYTES:
                raise Rejected(431, "headers_too_large")
            head, rest = bytes(data[:index]), bytes(data[index + 4:])
            break
        if len(data) > MAX_HEADER_BYTES:
            raise Rejected(431, "headers_too_large")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Rejected(408, "header_timeout")
        sock.settimeout(remaining)
        try:
            part = sock.recv(min(4096, MAX_HEADER_BYTES + 1 - len(data)))
        except (socket.timeout, TimeoutError):
            raise Rejected(408, "header_timeout") from None
        if not part:
            raise Rejected(400, "incomplete_headers")
        data.extend(part)
    lines = head.split(b"\r\n")
    try:
        request = lines[0].decode("ascii")
    except UnicodeError:
        raise Rejected(400, "invalid_request") from None
    components = request.split(" ")
    if len(components) != 3 or components[2] not in ("HTTP/1.0", "HTTP/1.1"):
        raise Rejected(400, "invalid_request")
    method, target = components[:2]
    if method not in ("CONNECT", "GET", "HEAD"):
        raise Rejected(405, "method_not_allowed")
    headers = []
    for line in lines[1:]:
        if b":" not in line:
            raise Rejected(400, "invalid_headers")
        name, value = line.split(b":", 1)
        if (not _HEADER_NAME.fullmatch(name)
                or any(byte < 32 and byte != 9 or byte == 127 for byte in value)):
            raise Rejected(400, "invalid_headers")
        name_lower, value_clean = name.lower(), value.strip(b" \t")
        if name_lower == b"transfer-encoding":
            raise Rejected(400, "connect_body_not_allowed")
        if name_lower == b"content-length" and (method == "CONNECT" or value_clean != b"0"):
            raise Rejected(400, "request_body_not_allowed")
        headers.append((name, name_lower, value_clean))
    if method == "CONNECT":
        host, port = parse_authority(target)
        return Request(host, port, rest, True)
    if rest:
        raise Rejected(400, "request_body_not_allowed")
    if (not target.startswith("http://") or not target.isascii()
            or any(ord(char) <= 32 or ord(char) == 127 for char in target)
            or "\\" in target):
        raise Rejected(400, "absolute_http_uri_required")
    try:
        parts = urlsplit(target)
    except ValueError:
        raise Rejected(400, "invalid_http_uri") from None
    if parts.scheme != "http" or not parts.netloc or "#" in target:
        raise Rejected(400, "invalid_http_uri")
    authority = parts.netloc
    if authority.startswith("["):
        authority = authority + ":80" if authority.endswith("]") else authority
    elif ":" not in authority:
        authority += ":80"
    host, port = parse_authority(authority)
    if port != 80:
        raise Rejected(403, "plain_http_port_not_allowed")
    hosts = [value for _, name, value in headers if name == b"host"]
    if len(hosts) != 1:
        raise Rejected(400, "host_header_required")
    try:
        host_header = hosts[0].decode("ascii")
        if host_header.startswith("["):
            host_header += ":80" if host_header.endswith("]") else ""
        elif ":" not in host_header:
            host_header += ":80"
        host_pair = parse_authority(host_header)
    except (Rejected, UnicodeError):
        raise Rejected(400, "host_header_mismatch") from None
    if host_pair != (host, port):
        raise Rejected(400, "host_header_mismatch")
    blocked = {b"connection", b"keep-alive", b"proxy-authenticate", b"proxy-authorization",
               b"te", b"trailer", b"transfer-encoding", b"upgrade", b"proxy-connection",
               b"content-length", b"host"}
    for _, name, value in headers:
        if name == b"upgrade":
            raise Rejected(400, "upgrade_not_allowed")
        if name in (b"connection", b"proxy-connection"):
            for token in value.lower().split(b","):
                token = token.strip(b" \t")
                if not _HEADER_NAME.fullmatch(token):
                    raise Rejected(400, "invalid_headers")
                if token == b"upgrade":
                    raise Rejected(400, "upgrade_not_allowed")
                blocked.add(token)
    path = parts.path or "/"
    if not path.startswith("/"):
        raise Rejected(400, "invalid_http_uri")
    if parts.query:
        path += "?" + parts.query
    canonical_host = f"[{host}]" if ":" in host else host
    rewritten = [f"{method} {path} HTTP/1.1".encode("ascii"),
                 f"Host: {canonical_host}".encode("ascii"), b"Connection: close"]
    rewritten.extend(name + b": " + value for name, lower_name, value in headers
                     if lower_name not in blocked and not lower_name.startswith(b"proxy-"))
    return Request(host, port, b"\r\n".join(rewritten) + b"\r\n\r\n", False)


def _reply(sock: socket.socket, status: int, reason: str):
    labels = {400: "Bad Request", 403: "Forbidden", 405: "Method Not Allowed",
              408: "Request Timeout", 431: "Request Header Fields Too Large",
              502: "Bad Gateway", 503: "Service Unavailable", 504: "Gateway Timeout"}
    body = (reason + "\n").encode("ascii")
    response = (f"HTTP/1.1 {status} {labels.get(status, 'Error')}\r\n"
                f"Content-Length: {len(body)}\r\nConnection: close\r\n"
                "Content-Type: text/plain\r\n\r\n").encode("ascii") + body
    try:
        sock.settimeout(0.2)
        sock.sendall(response)
    except OSError:
        pass


def tunnel(client: socket.socket, upstream: socket.socket, initial: bytes,
           idle_timeout: float, deadline: float, stopping: threading.Event,
           allow_client_payload: bool = True) -> tuple[int, int]:
    """Bounded nonblocking duplex pump, including TCP half-close handling."""
    client.setblocking(False)
    upstream.setblocking(False)
    peers = {client: upstream, upstream: client}
    outgoing = {client: bytearray(), upstream: bytearray(initial)}
    reads_open = {client: allow_client_payload, upstream: True}
    writes_closed = {client: False, upstream: False}
    received = {client: len(initial), upstream: 0}
    last_activity = time.monotonic()
    while not stopping.is_set():
        now = time.monotonic()
        remaining = min(deadline - now, idle_timeout - (now - last_activity), 0.2)
        if remaining <= 0:
            break
        for source, destination in peers.items():
            if not reads_open[source] and not outgoing[destination] and not writes_closed[destination]:
                try:
                    destination.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                writes_closed[destination] = True
        if not any(reads_open.values()) and not any(outgoing.values()):
            break
        readable = [sock for sock in peers if reads_open[sock]
                    and len(outgoing[peers[sock]]) < MAX_PENDING_BYTES]
        writable = [sock for sock in peers if outgoing[sock]]
        try:
            ready_read, ready_write, _ = select.select(readable, writable, [], remaining)
            for sock in ready_write:
                try:
                    count = sock.send(outgoing[sock])
                except BlockingIOError:
                    continue
                if count:
                    del outgoing[sock][:count]
                    last_activity = time.monotonic()
            for sock in ready_read:
                destination = peers[sock]
                try:
                    block = sock.recv(min(16384, MAX_PENDING_BYTES - len(outgoing[destination])))
                except BlockingIOError:
                    continue
                if block:
                    outgoing[destination].extend(block)
                    received[sock] += len(block)
                    last_activity = time.monotonic()
                else:
                    reads_open[sock] = False
        except (OSError, ValueError):
            break
    return received[client], received[upstream]


class LoopbackConnectProxy:
    def __init__(self, listen_port: int, policy: EgressPolicy):
        # An administrator stages immutable code, then launches the outlet as
        # its dedicated non-root identity. Never bind a root-owned parser here.
        if os.geteuid() == 0 or os.getuid() == 0:
            raise PermissionError("refusing_root_process")
        if not 0 <= listen_port <= 65535:
            raise ValueError("invalid listen port")
        self.policy, self.stopping = policy, threading.Event()
        self._slots = threading.BoundedSemaphore(policy.max_clients)
        self._lock = threading.Lock()
        self._active: dict[socket.socket, socket.socket | None] = {}
        self._threads: set[threading.Thread] = set()
        bound = []
        try:
            ipv4 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            bound.append(ipv4)
            ipv4.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            ipv4.bind(("127.0.0.1", listen_port))
            actual_port = ipv4.getsockname()[1]
            ipv6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            bound.append(ipv6)
            ipv6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            ipv6.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            ipv6.bind(("::1", actual_port, 0, 0))
            # Own both families before either listener accepts a request. Any
            # bind failure closes both sockets rather than exposing half a guard.
            for listener in bound:
                listener.listen(policy.max_clients)
                listener.setblocking(False)
        except BaseException:
            for listener in bound:
                listener.close()
            raise
        self.listeners = tuple(bound)
        self.listener = ipv4  # Existing owned-fixture API denotes the IPv4 socket.
        self.address = self.listener.getsockname()
        self.ipv6_address = ipv6.getsockname()

    @property
    def active_clients(self) -> int:
        with self._lock:
            return len(self._active)

    def serve_forever(self):
        while not self.stopping.is_set():
            try:
                ready, _, _ = select.select(self.listeners, [], [], 0.2)
            except (OSError, ValueError):
                break
            for listener in ready:
                try:
                    client, _ = listener.accept()
                except BlockingIOError:
                    continue
                except OSError:
                    self.stopping.set()
                    break
                if self.stopping.is_set():
                    client.close()
                    break
                if not self._slots.acquire(blocking=False):
                    _reply(client, 503, "connection_limit")
                    client.close()
                    continue
                worker = threading.Thread(target=self._handle, args=(client,), daemon=True)
                with self._lock:
                    self._active[client] = None
                    self._threads.add(worker)
                try:
                    worker.start()
                except RuntimeError:
                    with self._lock:
                        self._active.pop(client, None)
                        self._threads.discard(worker)
                    self._slots.release()
                    _reply(client, 503, "worker_unavailable")
                    client.close()

    def _handle(self, client: socket.socket):
        start, upstream, status, reason = time.monotonic(), None, 502, "connection_failed"
        counts = (0, 0)
        try:
            deadline = start + self.policy.total_timeout
            request = read_request(
                client, min(deadline, start + self.policy.header_timeout))
            establish_deadline = min(deadline, start + self.policy.establishment_timeout)
            addresses = resolve_public(request.host, request.port, self.policy, establish_deadline)
            if self.stopping.is_set():
                return
            upstream = connect_checked(addresses, request.port, self.policy, establish_deadline,
                                       self.stopping)
            with self._lock:
                if self.stopping.is_set():
                    return
                self._active[client] = upstream
            client.settimeout(min(self.policy.header_timeout, max(0.01, deadline - time.monotonic())))
            if request.connect:
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            status, reason = 200, "tunnel_finished" if request.connect else "http_forward_finished"
            counts = tunnel(client, upstream, request.initial, self.policy.idle_timeout,
                            deadline, self.stopping, allow_client_payload=request.connect)
        except Rejected as exc:
            status, reason = exc.status, exc.reason
            _reply(client, status, reason)
        except (OSError, ValueError):
            _reply(client, status, reason)
        finally:
            for sock in (client, upstream):
                if sock is not None:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    sock.close()
            with self._lock:
                self._active.pop(client, None)
                self._threads.discard(threading.current_thread())
            self._slots.release()
            # Never log hostnames, request headers, tunneled data or credentials.
            _log_connection(status, reason, time.monotonic() - start, counts)

    def shutdown(self):
        self.stopping.set()
        for listener in self.listeners:
            listener.close()
        with self._lock:
            connections = [(client, upstream) for client, upstream in self._active.items()]
            workers = list(self._threads)
        for pair in connections:
            for sock in pair:
                if sock is not None:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    sock.close()
        # The DNS child and connect handshakes are individually bounded.
        join_deadline = time.monotonic() + max(self.policy.dns_timeout, self.policy.connect_timeout) + 1
        for worker in workers:
            worker.join(max(0, join_deadline - time.monotonic()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen-port", required=True, type=int)
    parser.add_argument("--upstream-socks", type=parse_loopback_socks)
    parser.add_argument("--deny-self-ip", action="append", default=[])
    parser.add_argument("--max-clients", type=int, default=8)
    parser.add_argument("--idle-timeout", type=float, default=30)
    parser.add_argument("--total-timeout", type=float, default=300)
    parser.add_argument("--log-events", action="store_true",
                        help="log bounded connection outcomes without addresses or payloads")
    args = parser.parse_args(argv)
    if not 1 <= args.listen_port <= 65535:
        parser.error("listen-port must be between 1 and 65535")
    try:
        policy = EgressPolicy(deny_self_ips=frozenset(args.deny_self_ip),
                              upstream_socks=args.upstream_socks, max_clients=args.max_clients,
                              idle_timeout=args.idle_timeout, total_timeout=args.total_timeout)
        proxy = LoopbackConnectProxy(args.listen_port, policy)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    logging.basicConfig(level=logging.INFO if args.log_events else logging.WARNING,
                        format="%(message)s")
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: proxy.stopping.set())
    try:
        proxy.serve_forever()
    finally:
        proxy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
