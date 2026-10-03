"""Parser and owned loopback UDP fixtures; never contact a public resolver.

These checks do not start Android or prove live UID/firewall isolation.
The fixed production upstream is patched only inside the owned fixtures.
"""
from contextlib import contextmanager
import ipaddress
import socket
import struct
import threading
import time
import unittest
from unittest.mock import Mock, patch

import restricted_dns as dns


def wire_name(name):
    return b"".join(bytes((len(label),)) + label.encode("ascii")
                    for label in name.split(".")) + b"\0"


def query(name="example.com", kind=1, identifier=1234, flags=0x100, opt=False):
    packet = (struct.pack("!6H", identifier, flags, 1, 0, 0, int(opt))
              + wire_name(name) + struct.pack("!HH", kind, 1))
    if opt:
        packet += b"\0" + struct.pack("!HHIH", 41, 1232, 0, 0)
    return packet


def response(request, *, address="1.1.1.1", kind=1, name=None, flags=0x8180):
    question = dns.parse_query(request)
    question_bytes = request[12:question.question_end]
    if name is not None:
        question_bytes = wire_name(name) + struct.pack("!HH", question.qtype, 1)
    raw = ipaddress.ip_address(address).packed
    record = b"\xc0\x0c" + struct.pack("!HHIH", kind, 1, 30, len(raw)) + raw
    return (struct.pack("!6H", question.identifier, flags, 1, 1, 0, 0)
            + question_bytes + record)


class OwnedResolver:
    def __init__(self, behavior=None):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.settimeout(0.05)
        self.endpoint = self.socket.getsockname()
        self.behavior = behavior or (lambda packet: [response(packet)])
        self.received = []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, name="owned-dns-fixture", daemon=False)

    def run(self):
        while not self.stop.is_set():
            try:
                packet, client = self.socket.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            self.received.append(packet)
            for answer in self.behavior(packet):
                try:
                    self.socket.sendto(answer, client)
                except OSError:
                    break

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *unused):
        self.stop.set()
        self.thread.join(timeout=1)
        self.socket.close()
        assert not self.thread.is_alive()


@contextmanager
def owned_guard(resolver, policy=None, dual=False):
    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listener.bind(("127.0.0.1", 0))
    endpoint, stop = listener.getsockname(), threading.Event()
    listener_ipv6 = None
    if dual:
        listener_ipv6 = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        listener_ipv6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        listener_ipv6.bind(("::1", endpoint[1]))
    with patch.object(dns, "PUBLIC_RESOLVER", resolver.endpoint), \
            patch.object(dns, "bind_physical_interface") as binding:
        guard = dns.DNSGuard(listener, 7, policy or dns.DNSPolicy(timeout=0.15, retries=0),
                             stop, listener_ipv6)
        thread = threading.Thread(target=guard.run, name="owned-dns-guard", daemon=False)
        thread.start()
        try:
            yield guard, endpoint, binding
        finally:
            stop.set()
            thread.join(timeout=2)
            if thread.is_alive():
                listener.close()
                thread.join(timeout=1)
            assert not thread.is_alive()
            assert all(not worker.is_alive() for worker in guard.threads)
            if listener_ipv6 is not None:
                assert listener_ipv6.fileno() == -1


class DNSWireTests(unittest.TestCase):
    def test_allowed_question_types_and_case(self):
        for kind in dns.ALLOWED_TYPES:
            self.assertEqual(dns.parse_query(query("WWW.Example.com", kind)).name,
                             b"www.example.com")
            self.assertEqual(dns.parse_query(query(kind=kind, opt=True)).qtype, kind)

    def test_private_namespace_and_reverse_queries(self):
        for name in ("localhost", "x.local", "x.localhost", "x.home", "x.lan",
                     "x.home.arpa", "1.0.0.127.in-addr.arpa", "x.ip6.arpa",
                     "x.onion", "x.internal", "x.invalid", "example.com.local"):
            with self.subTest(name=name), self.assertRaises(dns.InvalidDNS):
                dns.parse_query(query(name))
        # Matching suffix must not reject an unrelated public suffix.
        self.assertEqual(dns.parse_query(query("example.locality.com")).name,
                         b"example.locality.com")

    def test_disallowed_types_and_class(self):
        for kind in (12, 15, 16, 33, 43, 48, 251, 252, 255):
            with self.subTest(kind=kind), self.assertRaises(dns.InvalidDNS):
                dns.parse_query(query(kind=kind))
        packet = query()[:-2] + struct.pack("!H", 3)
        with self.assertRaises(dns.InvalidDNS):
            dns.parse_query(packet)

    def test_query_header_and_extra_content(self):
        packet = query()
        for field, value in ((2, 0), (2, 2), (3, 1), (4, 1), (5, 2)):
            changed = bytearray(packet)
            struct.pack_into("!H", changed, 2 * field, value)
            with self.subTest(field=field), self.assertRaises(dns.InvalidDNS):
                dns.parse_query(bytes(changed))
        for flags in (0x8100, 0x0900, 0x0300, 0x0140, 0x0180, 0x0101):
            with self.subTest(flags=flags), self.assertRaises(dns.InvalidDNS):
                dns.parse_query(query(flags=flags))
        for packet in (b"", query() + b"\0", query()[:15], query() + b"x" * 1232):
            with self.assertRaises(dns.InvalidDNS):
                dns.parse_query(packet)

    def test_query_compression_and_bad_labels_rejected(self):
        head = struct.pack("!6H", 1, 0x100, 1, 0, 0, 0)
        for name in (b"\xc0\x0c", b"\xc0\x00", b"\x40", b"\x01\xff\0",
                     b"\x03a/b\0", b"\x03-ab\0", b"\x03ab-\0"):
            with self.subTest(name=name), self.assertRaises(dns.InvalidDNS):
                dns.parse_query(head + name + struct.pack("!HH", 1, 1))
        too_long = b"".join(b"\x3f" + b"a" * 63 for _ in range(4)) + b"\0"
        with self.assertRaises(dns.InvalidDNS):
            dns.parse_query(head + too_long + struct.pack("!HH", 1, 1))

    def test_edns_rejects_options_dnssec_versions_and_oversize(self):
        packet = query(opt=True)
        for field, value in ((-8, 4096), (-8, 511)):
            changed = bytearray(packet)
            struct.pack_into("!H", changed, len(changed) + field, value)
            with self.assertRaises(dns.InvalidDNS):
                dns.parse_query(bytes(changed))
        for ttl in (0x8000, 0x10000, 0x1000000):
            changed = bytearray(packet)
            struct.pack_into("!I", changed, len(changed) - 6, ttl)
            with self.assertRaises(dns.InvalidDNS):
                dns.parse_query(bytes(changed))
        changed = packet[:-2] + b"\0\x04" + b"\0\x0a\0\0"
        with self.assertRaises(dns.InvalidDNS):
            dns.parse_query(changed)

    def test_public_response_restores_client_id(self):
        original = query()
        rewritten = b"\xab\xcd" + original[2:]
        reply = response(rewritten)
        checked = dns.validate_response(reply, dns.parse_query(original), 0xABCD)
        self.assertEqual(checked[:2], original[:2])
        self.assertEqual(checked[2:], reply[2:])

    def test_wrong_id_question_type_flags_and_length_rejected(self):
        packet = query()
        q = dns.parse_query(packet)
        good = response(packet)
        bad = [b"\x99\x99" + good[2:], response(packet, name="different.com"),
               response(packet, flags=0x8380), response(packet, flags=0x0180),
               response(packet, flags=0x8980), response(packet, flags=0x81C0),
               good + b"\0", good + b"x" * dns.MAX_PACKET, good[:-1]]
        different_type = bytearray(good)
        struct.pack_into("!H", different_type, q.question_end - 4, 28)
        bad.append(bytes(different_type))
        for reply in bad:
            with self.subTest(reply=reply[:12]), self.assertRaises(dns.InvalidDNS):
                dns.validate_response(reply, q, q.identifier)

    def test_nonpublic_address_answers_fail_closed(self):
        for address in ("127.0.0.1", "192.168.9.125", "100.65.0.2", "169.254.169.254",
                        "198.18.0.1", "224.0.0.1", "0.0.0.0", "::1", "fe80::1",
                        "fd00::1", "::ffff:192.168.1.1", "2002:0808:0808::1"):
            kind = 28 if ":" in address else 1
            packet = query(kind=kind)
            with self.subTest(address=address), self.assertRaises(dns.InvalidDNS):
                dns.validate_response(response(packet, address=address, kind=kind),
                                      dns.parse_query(packet), 1234)

    def test_negative_answer_and_failure_reply(self):
        packet = query(opt=True)
        q = dns.parse_query(packet)
        answer = struct.pack("!6H", 1234, 0x8183, 1, 0, 0, 0) + packet[12:q.question_end]
        self.assertEqual(dns.validate_response(answer, q, 1234), answer)
        failure = dns.failure_response(packet, q)
        self.assertEqual(struct.unpack_from("!6H", failure), (1234, 0x8182, 1, 0, 0, 0))
        self.assertEqual(failure[12:], packet[12:q.question_end])

    def test_response_compression_loops_and_forward_pointers(self):
        packet = query()
        q = dns.parse_query(packet)
        head = struct.pack("!6H", 1234, 0x8180, 1, 1, 0, 0) + packet[12:q.question_end]
        for pointer in (b"\xc0" + bytes((q.question_end,)), b"\xc0\xff", b"\xc0\x00"):
            reply = head + pointer + struct.pack("!HHIH", 1, 1, 0, 4) + b"\x01" * 4
            with self.assertRaises(dns.InvalidDNS):
                dns.validate_response(reply, q, 1234)

    def test_service_record_private_hints_and_parameter_order(self):
        packet = query(kind=65)
        q = dns.parse_query(packet)
        head = struct.pack("!6H", 1234, 0x8180, 1, 1, 0, 0) + packet[12:q.question_end]
        for raw in (b"\0\x01\0" + struct.pack("!HH", 4, 4) + b"\xc0\xa8\x01\x01",
                    b"\0\x01\0" + struct.pack("!HH", 4, 3) + b"\x01" * 3,
                    b"\0\x01\0" + struct.pack("!HHHH", 3, 0, 2, 0)):
            reply = head + b"\xc0\x0c" + struct.pack("!HHIH", 65, 1, 0, len(raw)) + raw
            with self.assertRaises(dns.InvalidDNS):
                dns.validate_response(reply, q, 1234)


class DNSOutletTests(unittest.TestCase):
    def test_fixed_numeric_upstream_and_physical_binding(self):
        self.assertEqual(dns.PUBLIC_RESOLVER, ("223.5.5.5", 53))
        sock = Mock()
        with patch.object(dns.sys, "platform", "darwin"), \
                patch.object(dns.socket, "if_indextoname", return_value="en7"):
            dns.bind_physical_interface(sock, 7)
        sock.setsockopt.assert_called_once_with(socket.IPPROTO_IP, 25, 7)
        for interface in ("lo0", "utun0", "bridge0"):
            with patch.object(dns.sys, "platform", "darwin"), \
                    patch.object(dns.socket, "if_indextoname", return_value=interface), \
                    self.assertRaises(ValueError):
                dns.bind_physical_interface(Mock(), 7)
        for index in (0, -1, True):
            with self.assertRaises(ValueError):
                dns.bind_physical_interface(Mock(), index)
        with patch.object(dns.sys, "platform", "linux"), self.assertRaises(ValueError):
            dns.bind_physical_interface(Mock(), 7)

    def test_root_serving_and_nonloopback_listeners_refused(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
            listener.bind(("127.0.0.1", 0))
            with patch.object(dns.os, "geteuid", return_value=0), self.assertRaises(PermissionError):
                dns.DNSGuard(listener, 7)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
            listener.bind(("0.0.0.0", 0))
            with self.assertRaises(ValueError):
                dns.DNSGuard(listener, 7)

    def test_privilege_drop_clears_groups_before_uid(self):
        calls = []
        effective = iter((0, 700))
        with patch.object(dns.os, "geteuid", side_effect=lambda: next(effective)), \
                patch.object(dns.os, "getuid", return_value=700), \
                patch.object(dns.os, "getgid", return_value=701), \
                patch.object(dns.os, "getegid", return_value=701), \
                patch.object(dns.os, "setgroups", side_effect=lambda v: calls.append(("groups", v))), \
                patch.object(dns.os, "setgid", side_effect=lambda v: calls.append(("gid", v))), \
                patch.object(dns.os, "setuid", side_effect=lambda v: calls.append(("uid", v))):
            dns.drop_privileges(700, 701)
        self.assertEqual(calls, [("groups", []), ("gid", 701), ("uid", 700)])
        for uid, gid in ((0, 701), (700, 0), (-1, 701)):
            with self.assertRaises(PermissionError):
                dns.drop_privileges(uid, gid)

    def test_owned_upstream_randomized_id_and_bad_replies_then_valid(self):
        def replies(packet):
            good = response(packet)
            wrong_id = struct.pack("!H", struct.unpack_from("!H", good)[0] ^ 0x8000)
            return [wrong_id + good[2:], response(packet, name="other.com"),
                    good + b"x" * 1232, good]

        # Real UDP delivery can precede the worker's post-send accounting.
        # Hold that point deliberately; final counters require joined workers.
        count_entered, release_count = threading.Event(), threading.Event()
        count_timeout = threading.Event()
        original_count = dns.DNSGuard._count

        def held_reply_count(guard, name):
            if name == "replied":
                count_entered.set()
                if not release_count.wait(timeout=2):
                    count_timeout.set()
            original_count(guard, name)

        with OwnedResolver(replies) as resolver, \
                patch.object(dns.DNSGuard, "_count", held_reply_count):
            with owned_guard(resolver) as (guard, endpoint, binding):
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                        client.settimeout(1)
                        client.sendto(query(identifier=54321), endpoint)
                        result, _ = client.recvfrom(2048)
                    self.assertTrue(count_entered.wait(timeout=1))
                    self.assertEqual(result[:2], struct.pack("!H", 54321))
                    self.assertNotEqual(resolver.received[0][:2], struct.pack("!H", 54321))
                    self.assertTrue(all(call.args[1] == 7 for call in binding.call_args_list))
                    self.assertEqual(guard.snapshot()["replied"], 0)
                finally:
                    release_count.set()
            self.assertFalse(count_timeout.is_set())
            self.assertEqual(guard.snapshot()["replied"], 1)
            self.assertEqual(guard.snapshot()["active"], 0)

    @unittest.skipUnless(socket.has_ipv6, "owned IPv6 loopback required")
    def test_both_families_share_the_same_guard_and_close(self):
        with OwnedResolver() as resolver:
            with owned_guard(resolver, dual=True) as (guard, endpoint, _):
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as ipv4, \
                        socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as ipv6:
                    ipv4.settimeout(1)
                    ipv6.settimeout(1)
                    ipv4.sendto(query(identifier=100), endpoint)
                    ipv6.sendto(query(identifier=200), ("::1", endpoint[1]))
                    self.assertEqual(ipv4.recvfrom(2048)[0][:2], struct.pack("!H", 100))
                    self.assertEqual(ipv6.recvfrom(2048)[0][:2], struct.pack("!H", 200))
                self.assertEqual(len(resolver.received), 2)
                self.assertEqual(guard.snapshot()["accepted"], 2)
            self.assertTrue(all(listener.fileno() == -1 for listener in guard.listeners))

    def test_production_serve_missing_ipv6_fails_and_closes(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        listener.bind(("127.0.0.1", 0))
        with self.assertRaises(ValueError):
            dns.serve(listener, interface_index=7)
        self.assertEqual(listener.fileno(), -1)

    @unittest.skipUnless(socket.has_ipv6, "owned IPv6 loopback required")
    def test_wrong_ipv6_bind_port_or_v6only_refused(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
            listener.bind(("127.0.0.1", 0))
            for address, port, v6only in (("::", listener.getsockname()[1], 1),
                                          ("::1", 0, 1),
                                          ("::1", listener.getsockname()[1], 0)):
                with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as v6:
                    v6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, v6only)
                    v6.bind((address, port))
                    with self.assertRaises(ValueError):
                        dns.DNSGuard(listener, 7, bound_ipv6_socket=v6)
            with self.assertRaises(ValueError):
                dns.DNSGuard(listener, 7, bound_ipv6_socket=listener)

    def test_query_rate_limit_shared_across_slots(self):
        policy = dns.DNSPolicy(requests_per_second=1, timeout=0.1, retries=0)
        with OwnedResolver() as resolver, owned_guard(resolver, policy) as (guard, endpoint, _):
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                client.settimeout(1)
                client.sendto(query(identifier=1), endpoint)
                client.sendto(query(identifier=2), endpoint)
                self.assertEqual(client.recvfrom(2048)[0][:2], b"\0\x01")
                deadline = time.monotonic() + 0.2
                while guard.snapshot()["busy"] == 0 and time.monotonic() < deadline:
                    time.sleep(0.005)
            self.assertEqual(len(resolver.received), 1)
            self.assertEqual(guard.snapshot()["busy"], 1)

    def test_wrong_source_is_not_accepted(self):
        packet = query()
        rewritten = b"\x11\x11" + packet[2:]
        fake = Mock()
        fake.__enter__ = Mock(return_value=fake)
        fake.__exit__ = Mock(return_value=False)
        fake.recvfrom.side_effect = [(response(rewritten), ("127.0.0.1", 55555)),
                                    (response(rewritten), dns.PUBLIC_RESOLVER)]
        with patch.object(dns.socket, "socket", return_value=fake), \
                patch.object(dns, "bind_physical_interface"), \
                patch.object(dns.secrets, "randbelow", return_value=0x1111):
            reply = dns.resolve(packet, dns.parse_query(packet), 7, dns.DNSPolicy(), threading.Event())
        self.assertEqual(reply[:2], packet[:2])
        self.assertEqual(fake.recvfrom.call_count, 2)
        fake.connect.assert_called_once_with(("223.5.5.5", 53))

    def test_timeout_retries_bounded_and_worker_recovers(self):
        with OwnedResolver(lambda _: []) as resolver, \
                owned_guard(resolver, dns.DNSPolicy(timeout=0.06, retries=1)) as (guard, endpoint, _):
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                client.settimeout(1)
                started = time.monotonic()
                client.sendto(query(), endpoint)
                result, _ = client.recvfrom(2048)
                self.assertLess(time.monotonic() - started, 0.6)
                self.assertEqual(struct.unpack_from("!H", result, 2)[0] & 0xF, 2)
            self.assertEqual(len(resolver.received), 2)
            self.assertEqual(guard.snapshot()["active"], 0)

    def test_invalid_client_requests_never_reach_upstream(self):
        with OwnedResolver() as resolver, owned_guard(resolver) as (guard, endpoint, _):
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                client.settimeout(1)
                for packet in (b"x", query("x.local"), query(kind=255), b"x" * 1233):
                    client.sendto(packet, endpoint)
                client.sendto(query(), endpoint)
                client.recvfrom(2048)
            self.assertEqual(guard.snapshot()["rejected"], 4)
            self.assertEqual(len(resolver.received), 1)

    def test_concurrency_queue_limits_and_stop_cleanup(self):
        policy = dns.DNSPolicy(workers=2, pending=3, timeout=0.3, retries=1)
        with OwnedResolver(lambda _: []) as resolver:
            with owned_guard(resolver, policy) as (guard, endpoint, _):
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                    for identifier in range(30):
                        client.sendto(query(identifier=identifier), endpoint)
                    deadline = time.monotonic() + 1
                    while guard.snapshot()["busy"] == 0 and time.monotonic() < deadline:
                        time.sleep(0.005)
                    counts = guard.snapshot()
                    self.assertGreater(counts["busy"], 0)
                    self.assertLessEqual(counts["max_active"], 2)
                    self.assertLessEqual(counts["pending"], 3)
            self.assertEqual(guard.snapshot()["active"], 0)
            self.assertEqual(guard.snapshot()["pending"], 0)
            self.assertEqual(guard.socket.fileno(), -1)

    def test_policy_limits(self):
        for kwargs in ({"workers": 0}, {"workers": 17}, {"pending": 65},
                       {"timeout": 0}, {"timeout": 4}, {"retries": 2},
                       {"requests_per_second": 257}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                dns.DNSPolicy(**kwargs)


if __name__ == "__main__":
    unittest.main()
