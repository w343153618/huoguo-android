"""Owned LAN worker failure/crypto/control checks, entirely offline.

Sockets, subprocesses, geometry queries and native senders are fake. Files are
isolated temporary capability/report fixtures. No live runtime, credentials,
phone, listener, emulator or timing/performance acceptance is accessed.
"""
import base64
from collections import deque
import hashlib
import io
import json
from pathlib import Path
import socket
import stat
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import udp_lan_worker
from udp_lan_worker import LanMediaWorker
from udp_probe_protocol import CLIENT_NONCE, seal


KEY = bytes(range(32))  # Public fake fixture, never a deployment credential.
SESSION = '0123456789abcdef0123456789abcdef'
TAG = 123456789


def config(**changes):
    return {'session': SESSION, 'key_b64': base64.b64encode(KEY).decode('ascii'),
            'session_tag_hex': f'{TAG:016x}', 'peer_port': 15963,
            'max_size': 1920, 'video_bit_rate': 12_000_000, 'fps': 60,
            'bitrate_mode': 'VBR', 'buffer_ms': 80, 'seconds': 30,
            'audio_enabled': True, 'touch_enabled': False, **changes}


class FakeSocket:
    def __init__(self, messages=(), stop_event=None, wrong_interface=False):
        self.options = {}; self.binds = []; self.timeouts = []; self.connects = []
        self.closed = 0; self.messages = deque(messages); self.stop_event = stop_event
        self.wrong_interface = wrong_interface
    def setsockopt(self, level, option, value): self.options[level, option] = value
    def getsockopt(self, level, option):
        return self.options[level, option] + (1 if self.wrong_interface else 0)
    def bind(self, value): self.binds.append(value)
    def settimeout(self, value): self.timeouts.append(value)
    def connect(self, peer): self.connects.append(peer)
    def close(self): self.closed += 1
    def recvfrom(self, size):
        if self.messages:
            return self.messages.popleft()
        self.stop_event.set()
        raise socket.timeout()


def bare_worker():
    worker = LanMediaWorker.__new__(LanMediaWorker)
    worker.config = config()
    worker.sid, worker.key, worker.tag = SESSION, KEY, TAG
    worker.peer_ip, worker.host_ip = '192.168.9.149', '192.168.9.128'
    worker.stop_event, worker.startup_done = threading.Event(), threading.Event()
    worker.control_lock, worker.lifecycle_lock = threading.RLock(), threading.RLock()
    worker.registry = Mock()
    worker.registry.touch_allowed.return_value = True
    worker.hardware = worker.native = worker.sender = worker.gate = worker.touch = None
    worker.runtime, worker.packetizer = Path('/fake/candidate'), Path('/fake/packetizer')
    worker.native_encoder = Path('/fake/native-encoder')
    worker.threads, worker.native_summaries = [], deque(maxlen=4)
    worker.peer = None
    worker.started = worker.closed = False
    worker.failure = ''
    worker.native_shutdown = worker._native_shutdown_state()
    worker.formal_monitor = dict(interval_ms=2000, checks=0, busy_seen=0,
                                check_failures=0, max_check_duration_ms=0.0)
    worker.last_idr = 0.0
    worker.counts = dict(authenticated_ready=0, authenticated_alive=0, invalid_packets=0,
                         foreign_peer=0, authenticated_stop=0, touch_revoked=0)
    worker.busy = Mock(return_value=False)
    worker.udp = FakeSocket(stop_event=worker.stop_event)
    return worker


def fake_native():
    native = Mock()
    native.stdin = io.BytesIO()
    native.stdout = io.BytesIO()
    native.stderr = io.BytesIO()
    native.wait.return_value = 0
    return native


class ConstructorChecks(unittest.TestCase):
    def fixture(self, root, capability=True, mismatch=False):
        directory = Path(root) / 'hardware'
        directory.mkdir()
        jar = b'fake guest fixture, no Android bytecode'
        (directory / 'scrcpy-audio-control').write_bytes(jar)
        manifest = {'touch_cancel_clears_pointer_state': capability,
                    'guest_jar_sha256': '0' * 64 if mismatch else hashlib.sha256(jar).hexdigest()}
        (directory / 'capabilities.json').write_text(json.dumps(manifest))

    def test_capability_and_matching_guest_required_before_any_socket_or_background(self):
        for capability, mismatch in ((False, False), (True, True)):
            with self.subTest(capability=capability, mismatch=mismatch), tempfile.TemporaryDirectory() as root:
                self.fixture(root, capability, mismatch)
                with patch('udp_lan_worker.socket.socket') as create_socket, \
                     patch.object(LanMediaWorker, '_background') as background:
                    with self.assertRaises(ValueError):
                        LanMediaWorker(config(), '192.168.9.149', '192.168.9.128', 'en7',
                                       root, '/fake/packetizer', '/fake/encoder', Mock(), root, Mock())
                    create_socket.assert_not_called()
                    background.assert_not_called()

    def test_exact_physical_interface_readback_and_owned_bind(self):
        with tempfile.TemporaryDirectory() as root:
            self.fixture(root)
            udp = FakeSocket()
            with patch('udp_lan_worker.socket.socket', return_value=udp), \
                 patch('udp_lan_worker.socket.if_nametoindex', return_value=7), \
                 patch.object(LanMediaWorker, '_background') as background:
                worker = LanMediaWorker(config(), '192.168.9.149', '192.168.9.128', 'en7',
                                        root, '/fake/packetizer', '/fake/encoder', Mock(), root, Mock())
            self.assertEqual(udp.options[socket.IPPROTO_IP, 25], 7)
            self.assertEqual(udp.binds, [('192.168.9.128', 15963)])
            self.assertEqual(udp.timeouts, [.1])
            background.assert_called_once()
            self.assertEqual(background.call_args.args[0], 'authenticated_udp_ingress')
            worker.stop()
            self.assertEqual(udp.closed, 1)

    def test_interface_readback_failure_closes_only_owned_udp_socket(self):
        with tempfile.TemporaryDirectory() as root:
            self.fixture(root)
            udp = FakeSocket(wrong_interface=True)
            with patch('udp_lan_worker.socket.socket', return_value=udp), \
                 patch('udp_lan_worker.socket.if_nametoindex', return_value=7), \
                 patch.object(LanMediaWorker, '_background') as background:
                with self.assertRaises(RuntimeError):
                    LanMediaWorker(config(), '192.168.9.149', '192.168.9.128', 'en7',
                                   root, '/fake/packetizer', '/fake/encoder', Mock(), root, Mock())
            self.assertEqual(udp.closed, 1)
            self.assertEqual(udp.binds, [])
            background.assert_not_called()


class IngressChecks(unittest.TestCase):
    def run_ingress(self, messages, *, touch_allowed=True):
        worker = bare_worker()
        worker.registry.touch_allowed.return_value = touch_allowed
        worker.touch = Mock()
        worker._request_idr = Mock()
        worker.udp = FakeSocket(messages, worker.stop_event)
        with patch('udp_lan_worker.AuthenticatedSender', return_value=Mock()) as sender:
            worker._receive()
        return worker, sender

    def packet(self, sequence, payload, key=KEY, tag=TAG):
        return seal(key, tag, sequence, payload, CLIENT_NONCE)

    def test_foreign_source_cannot_pin_peer_or_poison_replay_before_real_ready(self):
        real = ('192.168.9.149', 34567)
        foreign = ('192.168.9.148', 45678)
        worker, sender = self.run_ingress([
            (self.packet(99999, b'READY'), foreign),
            (self.packet(1, b'READY'), real),
            (self.packet(2, b'ALIVE'), real),
            (self.packet(3, b'STOP'), real),
        ])
        self.assertEqual(worker.peer, real)
        self.assertEqual(worker.udp.connects, [real])
        worker.registry.authenticated_ready.assert_called_once_with(SESSION)
        worker.registry.authenticated_alive.assert_called_once_with(SESSION)
        worker.registry.revoke.assert_called_once_with(SESSION)
        self.assertEqual(worker.counts['foreign_peer'], 1)
        sender.assert_called_once()

    def test_forged_high_sequence_does_not_poison_authenticated_low_sequence(self):
        peer = ('192.168.9.149', 34567)
        corrupted = bytearray(self.packet(99999, b'READY')); corrupted[-1] ^= 1
        worker, _ = self.run_ingress([
            (bytes(corrupted), peer), (self.packet(1, b'READY'), peer),
            (self.packet(2, b'STOP'), peer),
        ])
        self.assertEqual(worker.counts['invalid_packets'], 1)
        worker.registry.authenticated_ready.assert_called_once_with(SESSION)

    def test_authenticated_nonready_cannot_pin_and_second_port_cannot_take_over(self):
        original = ('192.168.9.149', 34567)
        other_port = ('192.168.9.149', 34568)
        worker, _ = self.run_ingress([
            (self.packet(1, b'ALIVE'), other_port),
            (self.packet(2, b'READY'), original),
            (self.packet(99999, b'STOP'), other_port),
            (self.packet(3, b'ALIVE'), original),
            (self.packet(4, b'STOP'), original),
        ])
        self.assertEqual(worker.udp.connects, [original])
        self.assertEqual(worker.counts['foreign_peer'], 1)
        worker.registry.authenticated_alive.assert_called_once_with(SESSION)
        worker.registry.revoke.assert_called_once_with(SESSION)

    def test_duplicate_authenticated_packet_rejected_and_does_not_extend_lease(self):
        peer = ('192.168.9.149', 34567)
        alive = self.packet(2, b'ALIVE')
        worker, _ = self.run_ingress([(self.packet(1, b'READY'), peer),
            (alive, peer), (alive, peer), (self.packet(3, b'STOP'), peer)])
        worker.registry.authenticated_alive.assert_called_once_with(SESSION)
        self.assertEqual(worker.counts['invalid_packets'], 1)

    def test_wrong_session_wrong_key_and_oversized_datagrams_do_not_allocate_sender(self):
        peer = ('192.168.9.149', 34567)
        worker, sender = self.run_ingress([
            (self.packet(1, b'READY', tag=TAG + 1), peer),
            (self.packet(2, b'READY', key=b'x' * 32), peer),
            (b'x' * 1401, peer),
        ])
        self.assertIsNone(worker.peer)
        self.assertEqual(worker.counts['invalid_packets'], 3)
        sender.assert_not_called()
        worker.registry.authenticated_ready.assert_not_called()

    def test_revoked_touch_payload_never_reaches_bridge(self):
        peer = ('192.168.9.149', 34567)
        worker, _ = self.run_ingress([(self.packet(1, b'READY'), peer),
            (self.packet(2, b'HGUT fake authenticated snapshot'), peer),
            (self.packet(3, b'STOP'), peer)], touch_allowed=False)
        worker.touch.submit.assert_not_called()
        self.assertEqual(worker.counts['touch_revoked'], 1)


class ControlChecks(unittest.TestCase):
    def test_control_sender_handles_partial_and_would_block_without_changing_reader_timeout(self):
        worker = bare_worker()
        channel = Mock()
        channel.send.side_effect = [BlockingIOError(), 2, 3]
        worker.channels = {'control': channel}
        with patch('udp_lan_worker.select.select', return_value=([], [channel], [])), \
             patch('udp_lan_worker.time.monotonic', side_effect=[0.0, .01, .02, .03]):
            worker._send_control(b'12345')
        self.assertEqual(channel.send.call_count, 3)
        self.assertEqual(bytes(channel.send.call_args_list[0].args[0]), b'12345')
        self.assertEqual(bytes(channel.send.call_args_list[-1].args[0]), b'345')
        channel.settimeout.assert_not_called()
        for call in channel.send.call_args_list:
            self.assertEqual(call.args[1], socket.MSG_DONTWAIT)

    def test_control_write_has_bound_and_does_not_block_on_nonwritable_channel(self):
        worker = bare_worker()
        worker.channels = {'control': Mock()}
        with patch('udp_lan_worker.time.monotonic', side_effect=[0.0, .501]), \
             patch('udp_lan_worker.select.select') as select:
            with self.assertRaises(TimeoutError): worker._send_control(b'x')
            select.assert_not_called()
        worker.channels['control'].send.assert_not_called()
        for body in (b'', b'x' * 353):
            with self.subTest(size=len(body)), self.assertRaises(ValueError):
                worker._send_control(body)

    def test_revoked_or_stopped_normal_touch_raises_without_applied_ack_path(self):
        worker = bare_worker()
        worker._send_control = Mock()
        normal = b'\x02\x00' + b'x' * 30
        worker.registry.dispatch_touch.return_value = False
        with self.assertRaises(PermissionError): worker._write_touch(normal)
        worker._send_control.assert_not_called()
        worker.stop_event.set()
        worker.registry.dispatch_touch.reset_mock()
        with self.assertRaises(PermissionError): worker._write_touch(normal)
        worker.registry.dispatch_touch.assert_not_called()

    def test_only_exact_trusted_cancel_frame_bypasses_revoked_touch_lease(self):
        worker = bare_worker()
        worker.stop_event.set()
        worker._send_control = Mock()
        cancel = b'\x02\x03' + b'x' * 30
        worker._write_touch(cancel)
        worker._send_control.assert_called_once_with(cancel)
        worker.registry.dispatch_touch.assert_not_called()
        for invalid in (cancel + b'x', cancel[:-1], b'\x02\x01' + b'x' * 30):
            with self.subTest(size=len(invalid)), self.assertRaises(PermissionError):
                worker._write_touch(invalid)

    def test_active_touch_uses_registry_linearized_dispatch_for_actual_write(self):
        worker = bare_worker()
        worker._send_control = Mock()
        worker.registry.dispatch_touch.side_effect = lambda sid, write: (write() or True)
        body = b'\x02\x00' + b'x' * 30
        worker._write_touch(body)
        self.assertEqual(worker.registry.dispatch_touch.call_args.args[0], SESSION)
        worker._send_control.assert_called_once_with(body)


class OwnershipChecks(unittest.TestCase):
    def test_start_does_not_launch_twice_or_after_close(self):
        worker = bare_worker()
        worker._background = Mock()
        worker.start(); worker.start()
        worker._background.assert_called_once()
        worker.closed = True
        worker.start()
        self.assertEqual(worker._background.call_count, 1)

    def test_formal_busy_recheck_prevents_hardware_creation(self):
        worker = bare_worker()
        worker.busy.return_value = True
        with patch('udp_lan_worker.HostHardwareSession') as hardware:
            with self.assertRaises(RuntimeError): worker._start_media()
            hardware.assert_not_called()

    def test_closed_during_local_hardware_initialization_closes_only_late_owned_hardware(self):
        worker = bare_worker()
        hardware = Mock()
        def constructor(*args, **kwargs):
            worker.closed = True
            return hardware
        with patch('udp_lan_worker.HostHardwareSession', side_effect=constructor), \
             patch('udp_lan_worker.subprocess.Popen') as popen:
            worker._start_media()
        hardware.close.assert_called_once()
        hardware.channel.assert_not_called()
        hardware.start.assert_not_called()
        popen.assert_not_called()

    def test_failed_channel_handoff_keeps_hardware_registered_for_owned_cleanup(self):
        worker = bare_worker()
        hardware = Mock()
        hardware.channel.side_effect = RuntimeError('fake worker terminated before channel handoff')
        with patch('udp_lan_worker.HostHardwareSession', return_value=hardware):
            with self.assertRaises(RuntimeError): worker._start_media()
        self.assertIs(worker.hardware, hardware)
        with tempfile.TemporaryDirectory() as root:
            worker.evidence_dir = Path(root)
            worker.stop()
        hardware.close.assert_called_once()

    def test_pipeline_command_contains_no_session_secret_and_mode_matches_descriptor(self):
        worker = bare_worker()
        worker.config['bitrate_mode'] = 'CBR'
        worker.sender = Mock()
        worker._background = Mock()
        hardware, native = Mock(), Mock()
        with patch('udp_lan_worker.HostHardwareSession', return_value=hardware) as hardware_constructor, \
             patch('udp_lan_worker.subprocess.Popen', return_value=native) as popen:
            worker._start_media()
        self.assertEqual(hardware_constructor.call_args.args[6], 'CBR')
        self.assertEqual(hardware_constructor.call_args.kwargs['raw_submit_fps'], 60)
        argv = popen.call_args.args[0]
        self.assertEqual(argv, ['/fake/packetizer', '32000000', '500000', '0', '2048'])
        self.assertNotIn(worker.config['key_b64'], repr(argv))
        self.assertNotIn(worker.config['session_tag_hex'], repr(argv))
        hardware.start.assert_called_once()

    def test_stop_closes_owned_handles_once_and_report_omits_session_secret(self):
        worker = bare_worker()
        worker.hardware, worker.sender = Mock(), Mock()
        worker.sender.snapshot.return_value = {'video': {'datagrams': 3}}
        worker.native = fake_native()
        worker.native.poll.return_value = None
        worker.native.wait.return_value = 0
        with tempfile.TemporaryDirectory() as root:
            worker.evidence_dir = Path(root)
            worker.stop(); worker.stop()
            reports = list(Path(root).glob('host-session-*.json'))
            self.assertEqual(len(reports), 1)
            self.assertEqual(stat.S_IMODE(reports[0].stat().st_mode), 0o600)
            report = json.loads(reports[0].read_text())
            raw = reports[0].read_text()
            self.assertFalse(report['tcp_media_used'])
            self.assertEqual(report['path'], 'physical_LAN_AESGCM_UDP')
            self.assertNotIn(base64.b64encode(KEY).decode('ascii'), raw)
            self.assertNotIn(SESSION, raw)
            for field in ('key_b64', 'session_tag_hex', 'peer_ip', 'account'):
                self.assertNotIn(field, raw)
        worker.hardware.close.assert_called_once()
        worker.sender.close.assert_called_once()
        worker.native.terminate.assert_not_called()
        self.assertEqual(worker.udp.closed, 1)
        self.assertIsNone(worker.key)
        self.assertEqual(worker.config, {})

    def test_startup_cleanup_must_be_confirmed_before_allowing_success(self):
        worker = bare_worker()
        worker.started = True
        worker.startup_done = Mock()
        worker.startup_done.wait.return_value = False
        with tempfile.TemporaryDirectory() as root:
            worker.evidence_dir = Path(root)
            with self.assertRaises(TimeoutError): worker.stop()
            report = json.loads(next(Path(root).glob('host-session-*.json')).read_text())
            self.assertFalse(report['cleanup_confirmed'])
            self.assertTrue(any(error['stage'] == 'startup_quiescence' for error in report['cleanup_errors']))
        worker.startup_done.wait.assert_called_once_with(25)
        self.assertTrue(worker.stop_event.is_set())

    def test_packetizer_termination_timeout_kills_only_the_owned_process_handle(self):
        worker = bare_worker()
        worker.native = fake_native()
        worker.native.poll.return_value = None
        worker.native.wait.side_effect = [subprocess.TimeoutExpired('fake-owned-packetizer', 2),
                                        subprocess.TimeoutExpired('fake-owned-packetizer', 1), 0]
        with tempfile.TemporaryDirectory() as root:
            worker.evidence_dir = Path(root)
            with patch('udp_lan_worker.subprocess.run') as run, \
                 patch('udp_lan_worker.subprocess.Popen') as popen:
                worker.stop()
            run.assert_not_called()  # In particular no pkill/killall/ADB teardown.
            popen.assert_not_called()
        worker.native.terminate.assert_called_once()
        worker.native.kill.assert_called_once()
        self.assertEqual(worker.native.wait.call_count, 3)

    def test_unconfirmed_touch_thread_prevents_claiming_clean_shutdown(self):
        worker = bare_worker()
        worker.touch = Mock()
        worker.touch.stats.return_value = {'writer_alive': True}
        worker.touch.thread.is_alive.return_value = True
        worker.hardware = Mock()
        with tempfile.TemporaryDirectory() as root:
            worker.evidence_dir = Path(root)
            with self.assertRaises(TimeoutError): worker.stop()
            report = json.loads(next(Path(root).glob('host-session-*.json')).read_text())
            self.assertFalse(report['cleanup_confirmed'])
            self.assertTrue(any(error['stage'] == 'touch_quiescence' for error in report['cleanup_errors']))
        worker.touch.close.assert_called_once()
        worker.hardware.close.assert_called_once()

    def test_background_startup_failure_marks_done_before_revoking(self):
        worker = bare_worker()
        observations = []
        worker.registry.revoke.side_effect = lambda sid: observations.append(worker.startup_done.is_set())
        def fail(): raise OSError('fake detail, not stored')
        worker._background('owned_LAN_UDP_media', fail)
        worker.threads[0].join(2)
        self.assertFalse(worker.threads[0].is_alive())
        self.assertEqual(observations, [True])
        self.assertEqual(worker.failure, 'OSError')
        self.assertTrue(worker.startup_done.is_set())


class NativeEofChecks(unittest.TestCase):
    def test_feed_owns_stdin_eof_even_when_source_ends_with_error(self):
        worker = bare_worker()
        worker.native = fake_native()
        worker.channels = {'video': Mock()}
        worker.channels['video'].recv.return_value = b''
        with self.assertRaises(EOFError): worker._feed()
        self.assertTrue(worker.native.stdin.closed)
        self.assertTrue(worker.native_shutdown['stdin_eof_closed'])

    def test_revoked_video_reader_keeps_draining_without_udp_forwarding(self):
        worker = bare_worker()
        worker.stop_event.set()
        worker.native = fake_native()
        payload = b'x' * 1000
        worker.native.stdout = io.BytesIO((len(payload).to_bytes(2, 'big') + payload) * 3)
        worker.gate = Mock()
        worker._video()
        worker.gate.send.assert_not_called()
        self.assertEqual(worker.native_shutdown['stdout_drained_records'], 3)
        self.assertEqual(worker.native_shutdown['stdout_drained_bytes'], 3006)
        self.assertTrue(worker.native_shutdown['stdout_eof_observed'])

    def test_unexpected_runtime_output_eof_still_triggers_owned_failure_path(self):
        worker = bare_worker()
        worker.native = fake_native()
        with self.assertRaises(EOFError):
            worker._video()
        self.assertTrue(worker.native_shutdown['stdout_eof_observed'])

    def test_final_summary_is_real_clock_checked_input_and_never_drives_closed_control(self):
        worker = bare_worker()
        worker.stop_event.set()
        worker.config['bitrate_mode'] = 'ADAPTIVE_VBR'
        worker.recovery, worker.network = Mock(), Mock()
        worker._request_idr = Mock()
        worker.native = fake_native()
        events = [{'event': 'request_idr'}, {'event': 'summary', 'final': False,
                  'clock_domain': 'host_clock_gettime_CLOCK_UPTIME_RAW_us'},
                  {'event': 'summary', 'final': True,
                  'clock_domain': 'host_clock_gettime_CLOCK_UPTIME_RAW_us', 'source_frames': 3}]
        worker.native.stderr = io.BytesIO(b''.join(json.dumps(event).encode() + b'\n' for event in events))
        worker._events()
        self.assertTrue(worker.native_shutdown['stderr_eof_observed'])
        self.assertTrue(worker.native_shutdown['final_summary_observed'])
        self.assertEqual(worker.native_summaries[-1]['source_frames'], 3)
        worker.recovery.consume.assert_not_called()
        worker._request_idr.assert_not_called()

    def test_wrong_clock_or_string_final_never_counts_as_valid_final(self):
        for clock, final in (('wrong_clock', True), ('host_clock_gettime_CLOCK_UPTIME_RAW_us', 'true')):
            with self.subTest(clock=clock, final=final):
                worker = bare_worker()
                worker.stop_event.set()
                worker.native = fake_native()
                worker.native.stderr = io.BytesIO(json.dumps({'event': 'summary',
                    'clock_domain': clock, 'final': final}).encode() + b'\n')
                if clock == 'wrong_clock':
                    with self.assertRaises(ValueError): worker._events()
                else:
                    worker._events()
                self.assertFalse(worker.native_shutdown['final_summary_observed'])

    def test_missing_native_final_is_explicit_in_successful_resource_cleanup_report(self):
        worker = bare_worker()
        worker.native = fake_native()
        worker.native.stderr = io.BytesIO(json.dumps({'event': 'summary', 'final': False,
            'clock_domain': 'host_clock_gettime_CLOCK_UPTIME_RAW_us'}).encode() + b'\n')
        with tempfile.TemporaryDirectory() as root:
            worker.evidence_dir = Path(root)
            worker.stop()
            report = json.loads(next(Path(root).glob('host-session-*.json')).read_text())
        self.assertTrue(report['cleanup_confirmed'])
        self.assertEqual(report['native_final_status'], 'missing_after_graceful_exit')
        self.assertFalse(report['native_shutdown']['final_summary_observed'])
        self.assertFalse(report['native_summaries'][-1]['final'])

    def test_failing_events_thread_invoking_stop_is_not_mistaken_for_an_active_pipe_reader(self):
        worker = bare_worker()
        worker.native = fake_native()
        invoking_thread = Mock()
        invoking_thread.name = 'udp_events'
        invoking_thread.is_alive.return_value = True
        worker.threads = [invoking_thread]
        worker._background = Mock()
        with patch('udp_lan_worker.threading.current_thread', return_value=invoking_thread):
            worker._finish_native()
        names = [call.args[0] for call in worker._background.call_args_list]
        self.assertIn('native_shutdown_stderr_drain', names)
        self.assertIn('native_shutdown_stdout_drain', names)

    def test_owned_child_exceeding_pipe_capacity_gets_eof_and_final_without_terminate(self):
        # Real owned subprocess/stdio regression, no native binary, sockets or
        # credentials. Output exceeds pipe capacity, recreating the drain bug.
        script = (
            "import sys,json,struct\n"
            "record=struct.pack('>H',1000)+b'x'*1000\n"
            "for i in range(256): sys.stdout.buffer.write(record)\n"
            "sys.stdout.buffer.flush()\n"
            "sys.stdin.buffer.read()\n"
            "print(json.dumps({'event':'summary','clock_domain':"
            "'host_clock_gettime_CLOCK_UPTIME_RAW_us','final':True,"
            "'fixture_records':256}),file=sys.stderr,flush=True)\n"
        )
        worker = bare_worker()
        worker.stop_event.set()
        worker.native = subprocess.Popen([sys.executable, '-c', script],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        worker.gate = Mock()
        worker.gate.counts = {}
        worker._background('udp_video', worker._video)
        worker._background('udp_events', worker._events)
        try:
            with tempfile.TemporaryDirectory() as root:
                worker.evidence_dir = Path(root)
                worker.stop()
                report = json.loads(next(Path(root).glob('host-session-*.json')).read_text())
            self.assertEqual(report['native_final_status'], 'confirmed')
            self.assertTrue(report['native_shutdown']['stdin_eof_closed'])
            self.assertTrue(report['native_shutdown']['stdout_eof_observed'])
            self.assertTrue(report['native_shutdown']['stderr_eof_observed'])
            self.assertEqual(report['native_shutdown']['stdout_drained_records'], 256)
            self.assertTrue(report['native_shutdown']['process_exit_confirmed'])
            self.assertFalse(report['native_shutdown']['terminate_used'])
            self.assertFalse(report['native_shutdown']['kill_used'])
            self.assertEqual(report['native_summaries'][-1]['fixture_records'], 256)
            self.assertTrue(report['cleanup_confirmed'])
            self.assertFalse(any(item['alive'] for item in report['thread_cleanup']))
            worker.gate.send.assert_not_called()
        finally:
            if worker.native.poll() is None:
                worker.native.kill(); worker.native.wait(timeout=2)
            for stream in (worker.native.stdin, worker.native.stdout, worker.native.stderr):
                stream.close()


class FormalMonitorChecks(unittest.TestCase):
    def test_monitor_uses_low_frequency_wait_and_cancels_only_candidate_on_new_formal_session(self):
        worker = bare_worker()
        worker.stop_event = Mock()
        worker.stop_event.wait.side_effect = [False, False]
        worker.busy.side_effect = [False, True]
        with patch('udp_lan_worker.time.monotonic', side_effect=[0, .010, 2, 2.030]):
            worker._monitor_formal()
        self.assertEqual(worker.stop_event.wait.call_count, 2)
        self.assertTrue(all(call.args == (2.0,) for call in worker.stop_event.wait.call_args_list))
        self.assertEqual(worker.formal_monitor['checks'], 2)
        self.assertEqual(worker.formal_monitor['busy_seen'], 1)
        self.assertAlmostEqual(worker.formal_monitor['max_check_duration_ms'], 30)
        worker.registry.revoke.assert_called_once_with(SESSION)
        self.assertEqual(worker.failure, 'formal_session_started_cancel_candidate')

    def test_monitor_check_failure_fails_closed_without_exception_detail(self):
        worker = bare_worker()
        worker.stop_event = Mock()
        worker.stop_event.wait.return_value = False
        worker.busy.side_effect = subprocess.TimeoutExpired('fake secret-command-detail', 2)
        worker._monitor_formal()
        worker.registry.revoke.assert_called_once_with(SESSION)
        self.assertEqual(worker.formal_monitor['check_failures'], 1)
        self.assertEqual(worker.failure, 'formal_busy_check_unavailable_cancel_candidate')
        self.assertNotIn('secret-command-detail', json.dumps(worker.formal_monitor))

    def test_stopped_monitor_never_queries_formal_service(self):
        worker = bare_worker()
        worker.stop_event.set()
        worker._monitor_formal()
        worker.busy.assert_not_called()


if __name__ == '__main__':
    unittest.main()
