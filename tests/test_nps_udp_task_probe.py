"""Owned inert protocol/socket fixtures; no cloud, device or live listener."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

PATH = Path(__file__).resolve().parents[1]/'scripts/probes/nps_udp_task_probe.py'
SPEC = importlib.util.spec_from_file_location('nps_udp_task_probe', PATH)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)
KEY = bytes(range(32))
NONCE = bytes(range(1, 17))


class Clock:
    def __init__(self): self.now = 10.0
    def __call__(self): return self.now
    def advance(self, seconds): self.now += seconds


class InertSocket:
    def __init__(self, clock):
        self.clock, self.timeout, self.index = clock, .1, 7
        self.bound, self.destination, self.closed = None, None, False
        self.events, self.sent, self.requests, self.options = [], [], [], []
        self.on_send = None
    def __enter__(self): return self
    def __exit__(self, *_): self.close()
    def close(self): self.closed = True
    def bind(self, address): self.bound = address
    def settimeout(self, timeout): self.timeout = timeout
    def setsockopt(self, level, option, value):
        self.options.append((level, option, value)); self.index = value
    def getsockopt(self, level, option): return self.index
    def getsockname(self): return self.bound
    def connect(self, peer): self.destination = peer
    def send(self, packet):
        self.requests.append(packet)
        if self.on_send is not None: self.on_send(packet)
        return len(packet)
    def sendto(self, packet, peer): self.sent.append((packet, peer)); return len(packet)
    def recvfrom(self, maximum):
        if self.events:
            packet, peer, delay = self.events.pop(0)
            if delay > self.timeout:
                self.events.insert(0, (packet, peer, delay-self.timeout))
                self.clock.advance(self.timeout)
                raise socket.timeout()
            self.clock.advance(delay)
            return packet[:maximum], peer
        self.clock.advance(self.timeout)
        raise socket.timeout()


class ProtocolTests(unittest.TestCase):
    def test_roundtrip_fixed_size_and_separate_direction(self):
        request = probe.encode(KEY, 1, 1, NONCE)
        engine = probe.Responder(KEY)
        response = engine.accept(request, ('127.0.0.1', 35000))
        self.assertEqual(len(request), 64)
        self.assertEqual(len(response), len(request))
        self.assertEqual(probe.decode(KEY, response, 2), (1, NONCE))
        self.assertNotEqual(response, request)
        with self.assertRaises(probe.ProbeError): probe.decode(KEY, request, 2)

    def test_tamper_wrong_key_short_oversized_and_unknown_fields(self):
        request = probe.encode(KEY, 1, 1, NONCE)
        for bad in (request[:-1], request+b'x', request[:8]+b'x'+request[9:],
                    probe.encode(bytes([9])*32, 1, 1, NONCE),
                    probe.encode(KEY, 2, 1, NONCE)):
            self.assertIsNone(probe.Responder(KEY).accept(bad, ('127.0.0.1', 1)))
        # Authentication passes, but a signed reserved-field violation still fails.
        body = probe.BODY.pack(b'HGNP', 1, 1, 1, 1, NONCE)
        signed = body+probe.hmac.digest(KEY, body, 'sha256')
        self.assertIsNone(probe.Responder(KEY).accept(signed, ('127.0.0.1', 1)))

    def test_auth_failure_cannot_consume_replay_identity(self):
        engine = probe.Responder(KEY)
        request = probe.encode(KEY, 1, 1, NONCE)
        self.assertIsNone(engine.accept(request[:-1]+bytes([request[-1]^1]), ('127.0.0.1', 1)))
        self.assertIsNotNone(engine.accept(request, ('127.0.0.1', 1)))
        self.assertIsNone(engine.accept(request, ('127.0.0.1', 2)))
        self.assertEqual(engine.counts['invalid'], 1)
        self.assertEqual(engine.counts['replay'], 1)

    def test_foreign_peer_cannot_consume_replay_identity(self):
        engine = probe.Responder(KEY)
        request = probe.encode(KEY, 1, 1, NONCE)
        self.assertIsNone(engine.accept(request, ('192.168.9.128', 1)))
        self.assertIsNotNone(engine.accept(request, ('127.0.0.1', 1)))

    def test_replay_capacity_never_evicts_and_reaccepts_old_request(self):
        engine = probe.Responder(KEY)
        first = probe.encode(KEY, 1, 1, (1).to_bytes(16, 'big'))
        for n in range(1, probe.MAX_ACCEPTED+1):
            packet = probe.encode(KEY, 1, 1, n.to_bytes(16, 'big'))
            self.assertIsNotNone(engine.accept(packet, ('127.0.0.1', 1)))
        self.assertIsNone(engine.accept(probe.encode(KEY, 1, 1, (129).to_bytes(16, 'big')), ('127.0.0.1', 1)))
        self.assertIsNone(engine.accept(first, ('127.0.0.1', 1)))
        self.assertEqual(len(engine.seen), probe.MAX_ACCEPTED)
        self.assertEqual(engine.counts['capacity'], 1)
        self.assertEqual(engine.counts['replay'], 1)

    def test_sequence_and_zero_nonce_bounds(self):
        for seq in (0, 33, -1, True):
            with self.assertRaises(probe.ProbeError): probe.encode(KEY, 1, seq, NONCE)
        with self.assertRaises(probe.ProbeError): probe.encode(KEY, 1, 1, bytes(16))


class FileAndBindingTests(unittest.TestCase):
    def test_secret_inode_permissions_length_symlink_and_owner(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'key'
            path.write_bytes(KEY); path.chmod(0o600)
            self.assertEqual(probe.load_secret(path), KEY)
            path.chmod(0o644)
            with self.assertRaises(probe.ProbeError): probe.load_secret(path)
            path.chmod(0o600); path.write_bytes(KEY+b'x')
            with self.assertRaises(probe.ProbeError): probe.load_secret(path)
            path.write_bytes(KEY)
            link = Path(temp)/'link'; link.symlink_to(path)
            with self.assertRaises(probe.ProbeError): probe.load_secret(link)
            with mock.patch.object(probe.os, 'geteuid', return_value=os.geteuid()+1):
                with self.assertRaises(probe.ProbeError): probe.load_secret(path)
            hardlink = Path(temp)/'hardlink'; os.link(path, hardlink)
            with self.assertRaises(probe.ProbeError): probe.load_secret(path)

    def test_fifo_is_rejected_without_waiting_for_writer(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'fifo'; os.mkfifo(path, 0o600)
            with self.assertRaises(probe.ProbeError): probe.load_secret(path)

    def test_binding_reads_back_and_binds_interface_ipv4(self):
        clock = Clock(); sock = InertSocket(clock)
        run = mock.Mock(return_value=SimpleNamespace(returncode=0, stdout='192.168.9.128\n'))
        found, index, address = probe.physical_socket('en7', clock()+2, clock=clock,
            platform='darwin', socket_factory=lambda *_: sock,
            interface_index=lambda _: 7, run=run)
        self.assertIs(found, sock)
        self.assertEqual((index, address), (7, '192.168.9.128'))
        self.assertEqual(sock.bound, ('192.168.9.128', 0))
        self.assertEqual(sock.options, [(socket.IPPROTO_IP, 25, 7)])
        self.assertEqual(run.call_args.args[0], ['/usr/sbin/ipconfig', 'getifaddr', 'en7'])
        self.assertLessEqual(run.call_args.kwargs['timeout'], 1)

    def test_binding_fail_closed_platform_interface_ip_and_readback(self):
        clock = Clock()
        for interface, platform in [('utun4', 'darwin'), ('lo0', 'darwin'), ('en7', 'linux'), ('en7;bad', 'darwin')]:
            with self.assertRaises(probe.ProbeError):
                probe.physical_socket(interface, clock()+1, clock=clock, platform=platform)
        sock = InertSocket(clock)
        sock.getsockopt = lambda *_: 99
        with self.assertRaises(probe.ProbeError):
            probe.physical_socket('en7', clock()+1, clock=clock, platform='darwin',
                socket_factory=lambda *_: sock, interface_index=lambda _: 7,
                run=lambda *_, **__: SimpleNamespace(returncode=0, stdout='192.168.9.128'))
        self.assertTrue(sock.closed)
        for text in ('127.0.0.1', '0.0.0.0', '224.0.0.1', 'not-an-ip'):
            with self.assertRaises(probe.ProbeError):
                probe.physical_socket('en7', clock()+1, clock=clock, platform='darwin',
                    interface_index=lambda _: 7,
                    run=lambda *_, **__: SimpleNamespace(returncode=0, stdout=text))


class OwnedLoopTests(unittest.TestCase):
    def test_server_fixed_bind_expiry_no_amplification_or_invalid_reply(self):
        clock = Clock(); sock = InertSocket(clock)
        request = probe.encode(KEY, 1, 1, NONCE)
        sock.events = [(b'wrong', ('127.0.0.1', 1), .01),
                       (request, ('127.0.0.1', 1), .01),
                       (request, ('127.0.0.1', 1), .01)]
        report = probe.serve(KEY, .2, clock=clock, socket_factory=lambda *_: sock)
        self.assertEqual(sock.bound, ('127.0.0.1', 45965))
        self.assertEqual(len(sock.sent), 1)
        self.assertEqual(len(sock.sent[0][0]), len(request))
        self.assertEqual((report['responses'], report['invalid'], report['replay']), (1, 1, 1))
        self.assertAlmostEqual(report['runtime_ms'], 200, places=5)
        self.assertTrue(sock.closed)
        self.assertEqual(report['media_acceptance'], 0)

    def make_client(self, on_send):
        clock = Clock(); sock = InertSocket(clock); sock.bound = ('192.168.9.128', 0)
        sock.on_send = lambda packet: on_send(sock, packet)
        factory = lambda *_, **__: (sock, 7, '192.168.9.128')
        return clock, sock, factory

    def test_client_rejects_wrong_direction_peer_auth_nonce_then_correct(self):
        def sent(sock, request):
            seq, nonce = probe.decode(KEY, request, 1)
            response = probe.encode(KEY, 2, seq, nonce)
            sock.events.extend([(response, ('1.1.1.1', 15556), .001),
                                (b'x'*64, (probe.HOST, 15556), .001),
                                (request, (probe.HOST, 15556), .001),
                                (probe.encode(KEY, 2, seq, b'z'*16), (probe.HOST, 15556), .001),
                                (response, (probe.HOST, 15556), .001)])
        clock, sock, factory = self.make_client(sent)
        report = probe.client(KEY, 15556, 'en7', 2, .1, 1, clock=clock,
                             nonce_factory=lambda _: NONCE, physical_factory=factory)
        self.assertEqual(sock.destination, (probe.HOST, 15556))
        self.assertEqual((report['status'], report['responses']), ('PASS', 2))
        self.assertEqual((report['foreign'], report['invalid'], report['unmatched']), (2, 4, 2))
        self.assertEqual(report['rtt_ms'], [5.0, 5.0])
        self.assertTrue(sock.closed)

    def test_client_total_runtime_bounds_silent_and_invalid_flood(self):
        for flood in (False, True):
            def sent(sock, request):
                if flood: sock.events.extend([(b'bad', (probe.HOST, 15558), .01)]*50)
            clock, sock, factory = self.make_client(sent)
            report = probe.client(KEY, 15558, 'en7', 32, .1, .25, clock=clock,
                nonce_factory=lambda _: NONCE, physical_factory=factory)
            self.assertEqual(report['status'], 'TIMEOUT')
            self.assertLessEqual(report['attempted'], 3)
            self.assertLessEqual(report['runtime_ms'], 250.001)
            self.assertTrue(sock.closed)

    def test_client_fresh_nonce_each_packet_and_no_old_response_acceptance(self):
        nonces = iter([bytes([n])*16 for n in range(1, 4)])
        old = []
        def sent(sock, request):
            seq, nonce = probe.decode(KEY, request, 1)
            if old: sock.events.append((old[-1], (probe.HOST, 15556), .001))
            response = probe.encode(KEY, 2, seq, nonce); old.append(response)
            sock.events.append((response, (probe.HOST, 15556), .001))
        clock, sock, factory = self.make_client(sent)
        report = probe.client(KEY, 15556, 'en7', 3, .1, 1, clock=clock,
            nonce_factory=lambda _: next(nonces), physical_factory=factory)
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(report['unmatched'], 2)
        self.assertEqual(len({probe.decode(KEY, p, 1)[1] for p in sock.requests}), 3)

    def test_client_rechecks_route_after_connect(self):
        clock, sock, factory = self.make_client(lambda *_: None)
        sock.getsockopt = lambda *_: 4
        with self.assertRaises(probe.ProbeError):
            probe.client(KEY, 15556, 'en7', 1, .1, 1, clock=clock,
                         nonce_factory=lambda _: NONCE, physical_factory=factory)
        self.assertTrue(sock.closed)
        self.assertEqual(sock.requests, [])

    def test_bounds_reject_before_socket_open(self):
        bad = [(1, 1, .1, 1), (15556, 0, .1, 1), (15556, 33, .1, 1),
               (15556, 1, float('nan'), 1), (15556, 1, 3, 1),
               (15556, 1, .1, 61), (15556, 1, .1, float('inf'))]
        factory = mock.Mock(side_effect=AssertionError('must not open'))
        for port, count, timeout, runtime in bad:
            with self.assertRaises(probe.ProbeError):
                probe.client(KEY, port, 'en7', count, timeout, runtime, physical_factory=factory)
        factory.assert_not_called()

    def test_cli_failure_json_cannot_leak_arguments_key_or_os_error(self):
        for argv, error in [(['client', '--secret-file', 'PRIVATE_PATH', '--port', '15556', '--interface', 'en7'],
                             OSError('SECRET_DATA')),
                            (['server', '--secret-file', 'PRIVATE_PATH', '--port', '15556'], None),
                            (['server', '--secret-file', 'PRIVATE_PATH', '--unexpected', 'SECRET_DATA'], None)]:
            output = io.StringIO()
            with mock.patch.object(probe, 'load_secret', side_effect=error or AssertionError('should not read')):
                with contextlib.redirect_stdout(output): self.assertEqual(probe.main(argv), 2)
            report = json.loads(output.getvalue())
            self.assertEqual(report['status'], 'ERROR')
            for hidden in ('PRIVATE_PATH', 'SECRET_DATA', NONCE.hex(), KEY.hex()):
                self.assertNotIn(hidden, output.getvalue())


if __name__ == '__main__': unittest.main()
