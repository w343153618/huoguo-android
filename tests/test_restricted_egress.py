"""Restricted outlet boundaries, using only owned loopback socket fixtures.

Public addresses below are validation inputs, never contacted. Tests substitute
DNS replies and, for direct pinning, map the approved numeric destination onto
an owned echo socket. These checks are not guest/PF or production acceptance.
"""
import contextlib
import ipaddress
import json
import socket
import subprocess
import threading
import time
import unittest
from unittest.mock import Mock, patch

import restricted_egress as egress


def dns_result(*addresses, port=443):
    records = [[socket.AF_INET6 if ":" in address else socket.AF_INET, address, port, 0]
               for address in addresses]
    return subprocess.CompletedProcess([], 0, json.dumps(records).encode(), b"")


def exact(sock, count):
    data = b""
    while len(data) < count:
        block = sock.recv(count - len(data))
        if not block:
            raise EOFError("fixture closed")
        data += block
    return data


def read_header(sock):
    data = b""
    while b"\r\n\r\n" not in data:
        block = sock.recv(4096)
        if not block:
            break
        data += block
    return data


class OwnedListener:
    """One owned listener with bounded socket lifetime and captured fixture errors."""
    def __init__(self, action):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(8)
        self.listener.settimeout(0.1)
        self.address = self.listener.getsockname()
        self.stopped, self.accepted = threading.Event(), threading.Event()
        self.sockets, self.workers, self.errors = [], [], []
        self.action = action
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        while not self.stopped.is_set():
            try:
                conn, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conn.settimeout(2)
            self.sockets.append(conn)
            self.accepted.set()
            worker = threading.Thread(target=self.handle, args=(conn,), daemon=True)
            self.workers.append(worker)
            worker.start()

    def handle(self, conn):
        try:
            self.action(conn)
        except (OSError, EOFError):
            pass
        except BaseException as exc:
            self.errors.append(exc)
        finally:
            conn.close()

    def close(self):
        self.stopped.set()
        self.listener.close()
        for sock in self.sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        self.thread.join(2)
        for worker in self.workers:
            worker.join(2)
        if self.errors:
            raise self.errors[0]


@contextlib.contextmanager
def running_proxy(policy=None):
    proxy = egress.LoopbackConnectProxy(0, policy or egress.EgressPolicy())
    thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    try:
        yield proxy
    finally:
        proxy.shutdown()
        thread.join(2)


def connect_client(proxy, request):
    sock = socket.create_connection(proxy.address, timeout=2)
    sock.sendall(request)
    return sock


def socks_fixture(records, response_payload=None):
    def serve(conn):
        if exact(conn, 3) != b"\x05\x01\x00":
            raise AssertionError("only no-auth SOCKS5 expected")
        conn.sendall(b"\x05\x00")
        version, command, reserved, atyp = exact(conn, 4)
        if (version, command, reserved) != (5, 1, 0) or atyp not in (1, 4):
            raise AssertionError("upstream must receive a numeric ATYP 1/4")
        packed = exact(conn, 4 if atyp == 1 else 16)
        address = str(ipaddress.ip_address(packed))
        port = int.from_bytes(exact(conn, 2), "big")
        records.append((atyp, address, port))
        conn.sendall(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00")
        if response_payload is not None:
            request = read_header(conn)
            records.append(request)
            conn.sendall(response_payload)
            return
        while True:
            block = conn.recv(4096)
            if not block:
                return
            conn.sendall(block)
    return OwnedListener(serve)


class PublicDestinationPolicyTest(unittest.TestCase):
    def test_all_private_special_and_multicast_ranges_are_rejected(self):
        policy = egress.EgressPolicy()
        forbidden = ("127.0.0.1", "10.0.0.1", "172.16.2.3", "192.168.9.125",
                     "100.64.0.3", "169.254.169.254", "0.0.0.0", "198.18.0.2",
                     "192.0.2.4", "198.51.100.3", "203.0.113.2", "224.1.1.1",
                     "255.255.255.255", "::", "::1", "fc00::1", "fd00::1",
                     "fe80::1", "ff02::1", "2001:db8::1", "::ffff:127.0.0.1",
                     "::ffff:192.168.1.1", "2002:c0a8:0101::1", "2002:0808:0808::1",
                     "64:ff9b::c0a8:0101", "64:ff9b:1::c0a8:0101")
        for address in forbidden:
            with self.subTest(address=address), self.assertRaises(egress.Rejected) as raised:
                policy.check_address(address)
            self.assertEqual(raised.exception.status, 403)

    def test_public_unicast_and_mapped_public_are_allowed(self):
        policy = egress.EgressPolicy()
        for address in ("1.1.1.1", "8.8.8.8", "2001:4860:4860::8888", "::ffff:8.8.8.8"):
            self.assertEqual(policy.check_address(address), str(ipaddress.ip_address(address)))

    def test_self_list_applies_to_dns_and_mapped_address(self):
        policy = egress.EgressPolicy(deny_self_ips=frozenset(("8.8.8.8",)))
        for address in ("8.8.8.8", "::ffff:8.8.8.8"):
            with self.assertRaises(egress.Rejected) as raised:
                policy.check_address(address)
            self.assertEqual(raised.exception.reason, "self_destination")

    def test_strict_authorities_and_ports(self):
        invalid = ("http://example.com:443", "user@example.com:443", "example.com:443/path",
                   "example.com:443?x", "example.com:443#x", "example.com:443\r\nX: y",
                   "example.com:443\t", "example.com:+443", "example.com:999999",
                   "[fe80::1%en0]:443", "2001:4860::1:443", "[8.8.8.8]:443",
                   "127.1:443", "0177.0.0.1:443", "2130706433:443", "0x7f000001:443",
                   "example..com:443", "example.com..:443", "-bad.example:443",
                   "localhost:443", "例子.com:443", "example.com:４４３", "example.com\\:443")
        for authority in invalid:
            with self.subTest(authority=authority), self.assertRaises(egress.Rejected):
                egress.parse_authority(authority)
        for port in (22, 53, 15556, 15963, 65535, 0):
            with self.assertRaises(egress.Rejected) as raised:
                egress.parse_authority(f"example.com:{port}")
            self.assertEqual(raised.exception.status, 403)
        self.assertEqual(egress.parse_authority("Example.COM.:443"), ("example.com", 443))
        self.assertEqual(egress.parse_authority("[2001:4860::1]:80"), ("2001:4860::1", 80))

    def test_socks_endpoint_never_accepts_a_domain_or_remote_ip(self):
        self.assertEqual(egress.parse_loopback_socks("127.0.0.1:7890"), ("127.0.0.1", 7890))
        self.assertEqual(egress.parse_loopback_socks("[::1]:7890"), ("::1", 7890))
        for endpoint in ("localhost:7890", "192.168.9.125:7890", "8.8.8.8:7890",
                         "127.0.0.1:0", "user@127.0.0.1:7890", "[::1%lo0]:7890"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                egress.parse_loopback_socks(endpoint)


class ResolverPinningTest(unittest.TestCase):
    def test_mixed_dns_records_reject_the_entire_resolution_before_dial(self):
        for forbidden in ("192.168.9.125", "::1", "198.18.0.8", "::ffff:10.0.0.1"):
            with self.subTest(forbidden=forbidden), patch.object(
                    egress.subprocess, "run", return_value=dns_result("8.8.8.8", forbidden)), \
                    patch.object(egress, "open_numeric") as dial:
                with self.assertRaises(egress.Rejected) as raised:
                    egress.resolve_public("video.example", 443, egress.EgressPolicy(), time.monotonic() + 1)
                self.assertEqual(raised.exception.status, 403)
                dial.assert_not_called()

    def test_no_dns_for_literals(self):
        with patch.object(egress.subprocess, "run") as resolver:
            self.assertEqual(egress.resolve_public("1.1.1.1", 443, egress.EgressPolicy(),
                                                  time.monotonic() + 1), ["1.1.1.1"])
            resolver.assert_not_called()

    def test_dns_child_isolated_and_bounded(self):
        with patch.object(egress.subprocess, "run", return_value=dns_result("8.8.8.8")) as run:
            addresses = egress.resolve_public("video.example", 443, egress.EgressPolicy(dns_timeout=0.5),
                                              time.monotonic() + 1)
        self.assertEqual(addresses, ["8.8.8.8"])
        self.assertEqual(run.call_args.args[0][1], "-I")
        self.assertLessEqual(run.call_args.kwargs["timeout"], 0.5)
        self.assertEqual(run.call_args.kwargs["env"], {"PATH": "/usr/bin:/bin"})
        with patch.object(egress.subprocess, "run", side_effect=subprocess.TimeoutExpired([], 1)):
            with self.assertRaises(egress.Rejected) as raised:
                egress.resolve_public("video.example", 443, egress.EgressPolicy(), time.monotonic() + 1)
        self.assertEqual(raised.exception.reason, "dns_timeout")

    def test_malformed_and_scoped_dns_answers_fail_closed(self):
        rows = ([[socket.AF_INET, "8.8.8.8", 80, 0]],
                [[socket.AF_INET6, "8.8.8.8", 443, 0]],
                [[socket.AF_INET6, "2001:4860::1", 443, 1]],
                [[socket.AF_INET6, "fe80::1%en0", 443, 0]], [], {}, [1],
                [[socket.AF_INET, "8.8.8.8", 443, 0]] * 33)
        for row in rows:
            with self.subTest(row=row), patch.object(egress.subprocess, "run", return_value=
                    subprocess.CompletedProcess([], 0, json.dumps(row).encode(), b"")):
                with self.assertRaises(egress.Rejected):
                    egress.resolve_public("video.example", 443, egress.EgressPolicy(), time.monotonic() + 1)

    def test_direct_connection_uses_only_the_checked_numeric_address(self):
        echo = OwnedListener(lambda sock: sock.sendall(exact(sock, 5)))
        original_open = egress.open_numeric
        called = []
        def mapped_numeric(address, port, timeout):
            called.append((address, port))
            return original_open(echo.address[0], echo.address[1], timeout)
        try:
            with patch.object(egress.subprocess, "run", side_effect=[dns_result("8.8.8.8"),
                              dns_result("192.168.9.125")]) as resolver, \
                    patch.object(egress, "open_numeric", side_effect=mapped_numeric), running_proxy() as proxy:
                with connect_client(proxy, b"CONNECT video.example:443 HTTP/1.1\r\n\r\nhello") as client:
                    received = read_header(client)
                    self.assertIn(b"200 Connection Established", received)
                    payload = received.split(b"\r\n\r\n", 1)[1]
                    if len(payload) < 5:
                        payload += exact(client, 5 - len(payload))
                    self.assertEqual(payload, b"hello")
                self.assertEqual(resolver.call_count, 1)
                self.assertEqual(called, [("8.8.8.8", 443)])
        finally:
            echo.close()

    def test_socks_upstream_receives_numeric_ipv4_and_ipv6_not_domain(self):
        records = []
        upstream = socks_fixture(records)
        try:
            policy = egress.EgressPolicy(upstream_socks=upstream.address)
            with patch.object(egress.subprocess, "run", side_effect=[dns_result("8.8.8.8"),
                              dns_result("2001:4860:4860::8888")]) as resolver, running_proxy(policy) as proxy:
                for _ in range(2):
                    with connect_client(proxy, b"CONNECT video.example:443 HTTP/1.1\r\n\r\n") as client:
                        self.assertIn(b"200 Connection Established", read_header(client))
                        client.sendall(b"owned-fixture")
                        self.assertEqual(exact(client, 13), b"owned-fixture")
                self.assertEqual(resolver.call_count, 2)
            self.assertEqual(records, [(1, "8.8.8.8", 443), (4, "2001:4860:4860::8888", 443)])
        finally:
            upstream.close()


class HTTPAndResourceBoundaryTest(unittest.TestCase):
    def test_both_loopback_families_enforce_the_same_guard(self):
        with running_proxy() as proxy, patch.object(egress, "open_numeric") as dial:
            self.assertEqual(proxy.address[1], proxy.ipv6_address[1])
            self.assertEqual(proxy.listeners[1].getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY), 1)
            for address in (proxy.address, proxy.ipv6_address[:2]):
                with socket.create_connection(address, timeout=2) as client:
                    client.sendall(b"CONNECT 127.0.0.1:443 HTTP/1.1\r\n\r\n")
                    self.assertIn(b"403 Forbidden", read_header(client))
            dial.assert_not_called()
        self.assertTrue(all(listener.fileno() == -1 for listener in proxy.listeners))

    def test_either_family_conflict_fails_closed_without_a_partial_listener(self):
        for occupied_family in (socket.AF_INET, socket.AF_INET6):
            with self.subTest(family=occupied_family):
                occupied = socket.socket(occupied_family, socket.SOCK_STREAM)
                if occupied_family == socket.AF_INET6:
                    occupied.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                occupied.bind(("127.0.0.1" if occupied_family == socket.AF_INET else "::1", 0))
                occupied.listen(1)
                port = occupied.getsockname()[1]
                other_family = socket.AF_INET6 if occupied_family == socket.AF_INET else socket.AF_INET
                try:
                    with self.assertRaises(OSError):
                        egress.LoopbackConnectProxy(port, egress.EgressPolicy())
                    with socket.socket(other_family, socket.SOCK_STREAM) as remaining:
                        if other_family == socket.AF_INET6:
                            remaining.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                        remaining.bind(("::1" if other_family == socket.AF_INET6 else "127.0.0.1", port))
                finally:
                    occupied.close()

    def test_ipv4_and_ipv6_share_one_concurrency_limit(self):
        with running_proxy(egress.EgressPolicy(max_clients=1, header_timeout=0.3)) as proxy:
            with socket.create_connection(proxy.ipv6_address[:2], timeout=2) as ipv6:
                ipv6.sendall(b"CONNECT ")
                deadline = time.monotonic() + 1
                while not proxy.active_clients and time.monotonic() < deadline:
                    threading.Event().wait(0.01)
                with connect_client(proxy, b"CONNECT 127.0.0.1:443 HTTP/1.1\r\n\r\n") as ipv4:
                    self.assertIn(b"503 Service Unavailable", read_header(ipv4))
                self.assertIn(b"408 Request Timeout", read_header(ipv6))
            self.wait_no_clients(proxy)

    def test_root_is_rejected_before_any_listener_is_created(self):
        for real_uid, effective_uid in ((0, 0), (501, 0), (0, 601)):
            with self.subTest(real_uid=real_uid, effective_uid=effective_uid), \
                    patch.object(egress.os, "getuid", return_value=real_uid), \
                    patch.object(egress.os, "geteuid", return_value=effective_uid), \
                    patch.object(egress.socket, "socket") as create_socket:
                with self.assertRaisesRegex(PermissionError, "^refusing_root_process$"):
                    egress.LoopbackConnectProxy(18089, egress.EgressPolicy())
                create_socket.assert_not_called()

    def test_root_cli_has_a_fixed_reason_and_never_binds(self):
        with patch.object(egress.os, "geteuid", return_value=0), \
                patch.object(egress.socket, "socket") as create_socket, \
                patch.object(egress.sys, "stderr") as stderr:
            with self.assertRaises(SystemExit) as raised:
                egress.main(["--listen-port", "18089"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("refusing_root_process", "".join(call.args[0] for call in stderr.write.call_args_list))
        create_socket.assert_not_called()

    def request_result(self, request, policy=None):
        with running_proxy(policy) as proxy, connect_client(proxy, request) as client:
            return read_header(client)

    def test_private_targets_and_forbidden_port_return_explicit_403(self):
        for request in (b"CONNECT 127.0.0.1:443 HTTP/1.1\r\n\r\n",
                        b"CONNECT 192.168.9.125:80 HTTP/1.1\r\n\r\n",
                        b"CONNECT 8.8.8.8:22 HTTP/1.1\r\n\r\n",
                        b"GET http://127.0.0.1/ HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n",
                        b"HEAD http://[::1]/ HTTP/1.1\r\nHost: [::1]\r\n\r\n"):
            with self.subTest(request=request), patch.object(egress, "open_numeric") as dial:
                self.assertIn(b"403 Forbidden", self.request_result(request))
                dial.assert_not_called()

    def test_absolute_http_requires_matching_single_host_and_no_body_upgrade(self):
        requests = (
            b"POST http://8.8.8.8/ HTTP/1.1\r\nHost: 8.8.8.8\r\n\r\n",
            b"GET /path HTTP/1.1\r\nHost: 8.8.8.8\r\n\r\n",
            b"GET https://8.8.8.8/ HTTP/1.1\r\nHost: 8.8.8.8\r\n\r\n",
            b"GET http://user@8.8.8.8/ HTTP/1.1\r\nHost: 8.8.8.8\r\n\r\n",
            b"GET http://8.8.8.8/# HTTP/1.1\r\nHost: 8.8.8.8\r\n\r\n",
            b"GET http://8.8.8.8/ HTTP/1.1\r\nHost: 1.1.1.1\r\n\r\n",
            b"GET http://8.8.8.8/ HTTP/1.1\r\nHost: 8.8.8.8\r\nHost: 8.8.8.8\r\n\r\n",
            b"GET http://8.8.8.8/ HTTP/1.1\r\n\r\n",
            b"GET http://8.8.8.8/ HTTP/1.1\r\nHost: 8.8.8.8\r\nContent-Length: 1\r\n\r\nx",
            b"GET http://8.8.8.8/ HTTP/1.1\r\nHost: 8.8.8.8\r\nTransfer-Encoding: chunked\r\n\r\n",
            b"GET http://8.8.8.8/ HTTP/1.1\r\nHost: 8.8.8.8\r\nUpgrade: websocket\r\n\r\n",
            b"GET http://8.8.8.8/ HTTP/1.1\r\nHost: 8.8.8.8\r\nConnection: upgrade\r\n\r\n",
            b"GET http://8.8.8.8/ HTTP/1.1\r\nHost: 8.8.8.8\r\n\r\nsecond-request",
            b"CONNECT 8.8.8.8:443 HTTP/1.1\r\nContent-Length: 0\r\n\r\n",
            b"CONNECT 8.8.8.8:443 HTTP/1.1\r\nX-A: y\nInjected: z\r\n\r\n",
        )
        for request in requests:
            with self.subTest(request=request), patch.object(egress, "open_numeric") as dial:
                self.assertNotIn(b"200", self.request_result(request).split(b"\r\n", 1)[0])
                dial.assert_not_called()

    def test_get_rewrites_origin_target_and_removes_hop_and_proxy_headers(self):
        records = []
        upstream = socks_fixture(records, b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        try:
            policy = egress.EgressPolicy(upstream_socks=upstream.address)
            with patch.object(egress.subprocess, "run", return_value=dns_result("8.8.8.8", port=80)), \
                    running_proxy(policy) as proxy:
                request = (b"GET http://Video.Example:80/watch?v=1 HTTP/1.1\r\nHost: video.example\r\n"
                           b"Connection: keep-alive, X-Hop\r\nX-Hop: secret\r\nTE: trailers\r\n"
                           b"Proxy-Authorization: Bearer DO-NOT-LOG\r\nContent-Length: 0\r\n"
                           b"User-Agent: fixture\r\n\r\n")
                with connect_client(proxy, request) as client:
                    received = b""
                    while True:
                        chunk = client.recv(4096)
                        if not chunk:
                            break
                        received += chunk
                self.assertTrue(received.endswith(b"ok"))
            self.assertEqual(records[0], (1, "8.8.8.8", 80))
            forwarded = records[1]
            self.assertTrue(forwarded.startswith(b"GET /watch?v=1 HTTP/1.1\r\n"))
            self.assertIn(b"Host: video.example\r\nConnection: close\r\n", forwarded)
            self.assertIn(b"User-Agent: fixture", forwarded)
            for forbidden in (b"Proxy-Authorization", b"DO-NOT-LOG", b"X-Hop", b"TE:", b"Content-Length"):
                self.assertNotIn(forbidden, forwarded)
        finally:
            upstream.close()

    def test_redirect_target_is_revalidated_and_dns_rebinding_cannot_reuse_approval(self):
        records = []
        upstream = socks_fixture(records, b"HTTP/1.1 302 Found\r\nLocation: http://video.example/next\r\n"
                                 b"Content-Length: 0\r\n\r\n")
        try:
            policy = egress.EgressPolicy(upstream_socks=upstream.address)
            with patch.object(egress.subprocess, "run", side_effect=[dns_result("8.8.8.8", port=80),
                              dns_result("192.168.9.125", port=80)]) as resolver, running_proxy(policy) as proxy:
                for target, expected in ((b"/first", b"302 Found"), (b"/next", b"403 Forbidden")):
                    request = b"GET http://video.example" + target + b" HTTP/1.1\r\nHost: video.example\r\n\r\n"
                    with connect_client(proxy, request) as client:
                        self.assertIn(expected, read_header(client))
                self.assertEqual(resolver.call_count, 2)
            self.assertEqual([item for item in records if isinstance(item, tuple)], [(1, "8.8.8.8", 80)])
        finally:
            upstream.close()

    def test_headers_are_bounded_and_slot_recovers_after_rejection(self):
        policy = egress.EgressPolicy(max_clients=1)
        with running_proxy(policy) as proxy:
            oversized = b"CONNECT 8.8.8.8:443 HTTP/1.1\r\nX: " + b"a" * egress.MAX_HEADER_BYTES + b"\r\n\r\n"
            with connect_client(proxy, oversized) as client:
                self.assertIn(b"431 Request Header Fields Too Large", read_header(client))
            self.wait_no_clients(proxy)
            with connect_client(proxy, b"CONNECT 127.0.0.1:443 HTTP/1.1\r\n\r\n") as client:
                self.assertIn(b"403 Forbidden", read_header(client))
            self.wait_no_clients(proxy)

    def wait_no_clients(self, proxy):
        deadline = time.monotonic() + 2
        while proxy.active_clients and time.monotonic() < deadline:
            threading.Event().wait(0.01)
        self.assertEqual(proxy.active_clients, 0)

    def test_slow_header_concurrency_limit_and_timeout_cleanup(self):
        policy = egress.EgressPolicy(max_clients=1, header_timeout=0.2)
        with running_proxy(policy) as proxy:
            first = connect_client(proxy, b"CONNECT ")
            try:
                deadline = time.monotonic() + 1
                while not proxy.active_clients and time.monotonic() < deadline:
                    threading.Event().wait(0.01)
                with connect_client(proxy, b"CONNECT 127.0.0.1:443 HTTP/1.1\r\n\r\n") as second:
                    self.assertIn(b"503 Service Unavailable", read_header(second))
                self.assertIn(b"408 Request Timeout", read_header(first))
            finally:
                first.close()
            self.wait_no_clients(proxy)

    def test_idle_timeout_and_shutdown_release_tunnel_slots(self):
        records = []
        upstream = socks_fixture(records)
        try:
            policy = egress.EgressPolicy(upstream_socks=upstream.address, idle_timeout=0.15)
            with running_proxy(policy) as proxy:
                with connect_client(proxy, b"CONNECT 8.8.8.8:443 HTTP/1.1\r\n\r\n") as client:
                    self.assertIn(b"200 Connection Established", read_header(client))
                    self.assertEqual(client.recv(1), b"")
                self.wait_no_clients(proxy)
            policy = egress.EgressPolicy(upstream_socks=upstream.address)
            with running_proxy(policy) as proxy:
                with connect_client(proxy, b"CONNECT 8.8.8.8:443 HTTP/1.1\r\n\r\n") as client:
                    self.assertIn(b"200 Connection Established", read_header(client))
                    proxy.shutdown()
                    self.assertEqual(client.recv(1), b"")
                    self.assertEqual(proxy.active_clients, 0)
        finally:
            upstream.close()

    def test_total_timeout_is_independent_of_continuous_activity(self):
        records = []
        upstream = socks_fixture(records)
        try:
            policy = egress.EgressPolicy(upstream_socks=upstream.address, idle_timeout=1, total_timeout=0.2)
            with running_proxy(policy) as proxy:
                with connect_client(proxy, b"CONNECT 8.8.8.8:443 HTTP/1.1\r\n\r\n") as client:
                    self.assertIn(b"200 Connection Established", read_header(client))
                    started = time.monotonic()
                    ended = False
                    while time.monotonic() - started < 1:
                        try:
                            client.sendall(b"ping")
                            if client.recv(4) != b"ping":
                                ended = True
                                break
                        except OSError:
                            ended = True
                            break
                        threading.Event().wait(0.01)
                    self.assertTrue(ended)
                    self.assertLess(time.monotonic() - started, 1)
                self.wait_no_clients(proxy)
        finally:
            upstream.close()

    def test_negative_outcome_logs_never_contain_auth_or_target(self):
        with self.assertLogs(egress.LOG, level="INFO") as logs:
            self.request_result(b"CONNECT 127.0.0.1:443 HTTP/1.1\r\n"
                                b"Proxy-Authorization: Bearer DO-NOT-LOG\r\n\r\n")
        joined = " ".join(logs.output)
        self.assertIn("destination_not_public", joined)
        for forbidden in ("127.0.0.1", "DO-NOT-LOG", "Proxy-Authorization"):
            self.assertNotIn(forbidden, joined)

    def test_optional_connection_logs_have_a_rate_ceiling(self):
        with patch.object(egress, "_LOG_WINDOW", time.monotonic()), \
                patch.object(egress, "_LOG_COUNT", 0), self.assertLogs(egress.LOG, level="INFO") as logs:
            for _ in range(100):
                egress._log_connection(403, "destination_not_public", 0.01, (0, 0))
        self.assertEqual(len(logs.output), 60)

    def test_shutdown_cancels_new_address_attempts(self):
        stopped = threading.Event()
        def refused(*args):
            stopped.set()
            raise OSError("owned-fixture failure")
        with patch.object(egress, "open_numeric", side_effect=refused) as dial:
            with self.assertRaises(egress.Rejected) as raised:
                egress.connect_checked(["8.8.8.8", "1.1.1.1"], 443, egress.EgressPolicy(),
                                        time.monotonic() + 1, stopped)
        self.assertEqual(raised.exception.reason, "shutting_down")
        self.assertEqual(dial.call_count, 1)

    def test_connect_half_close_preserves_last_upstream_bytes(self):
        records = []
        def half_close_server(conn):
            self.assertEqual(exact(conn, 3), b"\x05\x01\x00")
            conn.sendall(b"\x05\x00")
            self.assertEqual(exact(conn, 10), b"\x05\x01\x00\x01\x08\x08\x08\x08\x01\xbb")
            conn.sendall(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00")
            body = b""
            while True:
                block = conn.recv(1024)
                if not block:
                    break
                body += block
            records.append(body)
            conn.sendall(body + b"-final")
        upstream = OwnedListener(half_close_server)
        try:
            with running_proxy(egress.EgressPolicy(upstream_socks=upstream.address)) as proxy:
                with connect_client(proxy, b"CONNECT 8.8.8.8:443 HTTP/1.1\r\n\r\n") as client:
                    self.assertIn(b"200 Connection Established", read_header(client))
                    client.sendall(b"hello")
                    client.shutdown(socket.SHUT_WR)
                    self.assertEqual(exact(client, 11), b"hello-final")
                    self.assertEqual(client.recv(1), b"")
                self.wait_no_clients(proxy)
            self.assertEqual(records, [b"hello"])
        finally:
            upstream.close()


if __name__ == "__main__":
    unittest.main()
