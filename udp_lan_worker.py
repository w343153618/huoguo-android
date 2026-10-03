"""Owned M1 LAN UDP worker; secrets stay in memory, never argv or evidence.

The physical peer is learned from an authenticated READY from the HTTPS client's
exact LAN IPv4. This initial implementation has no NAT migration or cloud route.
"""
import base64
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import select
import socket
import struct
import subprocess
import sys
import threading
import time

from hardware_stream import HostHardwareSession, read_exact, read_control

UDP_SOURCE = Path(__file__).resolve().parent / 'experiments/moonlight-v2/transport/android-udp'
if str(UDP_SOURCE) not in sys.path:
    sys.path.insert(0, str(UDP_SOURCE))
from udp_probe_protocol import CLIENT_NONCE, ReplayWindow, open_packet
from udp_session_sender import AuthenticatedSender, SocketPacer, SocketVideoGate
from udp_touch_control import UdpTouchBridge
from audio_datagram import packetize_audio, AAC
from feedback_controller import NetworkFeedbackController
from recovery_controller import RecoveryController


class LanMediaWorker:
    FORMAL_CHECK_SECONDS = 2.0
    NATIVE_GRACE_SECONDS = 2.0
    # Match native HostReader/logical_frame.hpp and media_datagram.hpp. A
    # single complete record is buffered, never a queue of access units.
    FEED_MAX_AU_BYTES = 1024 * 1024
    FEED_MAX_CONFIG_BYTES = 65536
    FEED_READ_CHECK_SECONDS = .1
    FEED_PARTIAL_SECONDS = 2.0
    FAILURE_ROLES = frozenset(('authenticated_udp_ingress', 'owned_LAN_UDP_media',
        'udp_feed', 'udp_video', 'udp_audio', 'udp_control', 'udp_events', 'udp_ping',
        'formal_session_monitor', 'native_shutdown_stdout_drain', 'native_shutdown_stderr_drain'))
    FAILURE_OPERATIONS = frozenset(('udp_socket_send', 'udp_send_wait', 'udp_send_cancel',
        'udp_sender_state', 'udp_auth_seal', 'formal_busy_guard', 'not_instrumented'))

    def __init__(self, config, peer_ip, host_ip, interface, runtime, packetizer,
                 native_encoder, registry, evidence_dir, busy):
        self.config = dict(config)
        self.sid = config['session']
        self.key = base64.b64decode(config['key_b64'], validate=True)
        self.tag = int(config['session_tag_hex'], 16)
        self.peer_ip, self.host_ip = peer_ip, host_ip
        self.runtime, self.packetizer = Path(runtime), Path(packetizer)
        self.native_encoder, self.evidence_dir = Path(native_encoder), Path(evidence_dir)
        self.registry, self.busy = registry, busy
        self.stop_event = threading.Event()
        self.startup_done = threading.Event()
        self.control_lock, self.lifecycle_lock = threading.RLock(), threading.RLock()
        self.threads, self.native_summaries = [], deque(maxlen=4)
        self.hardware = self.native = self.sender = self.gate = self.touch = None
        self.peer = None
        self.started = self.closed = False
        self.last_idr = 0.
        self.failure = ''
        self.failure_role = self.failure_operation = ''
        self.failure_observed_host_ns = 0
        self.failure_events = deque(maxlen=8)
        self.failure_events_evicted = 0
        self.send_udp = None
        self.send_state = dict(dup_created=False, reader_timeout_preserved=False,
                               same_local_endpoint=False, same_peer=False,
                               reader_timeout_ms=100, writer_timeout_ms=0)
        self.native_shutdown = self._native_shutdown_state()
        self.feed_state = self._feed_state()
        self.formal_monitor = dict(interval_ms=2000, checks=0, busy_seen=0,
                                   check_failures=0, max_check_duration_ms=0.0)
        self.counts = dict(authenticated_ready=0, authenticated_alive=0, invalid_packets=0,
                           foreign_peer=0, authenticated_stop=0, touch_revoked=0)
        # Require an independently built guest capability manifest. Stock scrcpy
        # cannot clear all contact bookkeeping after true ACTION_CANCEL.
        capability = json.loads((self.runtime/'hardware/capabilities.json').read_text())
        jar = self.runtime/'hardware/scrcpy-audio-control'
        if (capability.get('touch_cancel_clears_pointer_state') is not True or
                capability.get('guest_jar_sha256') != hashlib.sha256(jar.read_bytes()).hexdigest()):
            raise ValueError('cancel_capable_candidate_guest_required')
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            ifindex = socket.if_nametoindex(interface)
            self.udp.setsockopt(socket.IPPROTO_IP, 25, ifindex)
            if self.udp.getsockopt(socket.IPPROTO_IP, 25) != ifindex:
                raise RuntimeError('physical_UDP_interface_mismatch')
            self.udp.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024*1024)
            self.udp.bind((host_ip, config['peer_port']))
            self.udp.settimeout(.1)
            self._background('authenticated_udp_ingress', self._receive)
        except Exception:
            self.udp.close()
            raise

    def _background(self, name, operation):
        def run():
            try:
                operation()
            except Exception as error:
                if name == 'owned_LAN_UDP_media':
                    self.startup_done.set()
                if not self.stop_event.is_set():
                    self._record_failure(name, error)
                    self.registry.revoke(self.sid)
            finally:
                if name == 'owned_LAN_UDP_media':
                    self.startup_done.set()
        thread = threading.Thread(target=run, name=name, daemon=True)
        self.threads.append(thread)
        thread.start()

    def _record_failure(self, role, error, operation=None, failure_class=None):
        role = role if role in self.FAILURE_ROLES else 'unknown_owned_worker'
        operation = operation or getattr(error, 'huoguo_udp_failure_operation', 'not_instrumented')
        operation = operation if operation in self.FAILURE_OPERATIONS else 'not_instrumented'
        lane = getattr(error, 'huoguo_udp_failure_lane', '')
        lane = lane if lane in AuthenticatedSender.LANES else ''
        item = dict(role=role, operation=operation, lane=lane,
                    failure_class=failure_class or type(error).__name__, observed_host_monotonic_ns=time.monotonic_ns())
        error_number = getattr(error, 'errno', None)
        if type(error_number) is int:
            item['errno'] = error_number
        with self.lifecycle_lock:
            if not self.failure:
                self.failure, self.failure_role, self.failure_operation = item['failure_class'], role, operation
                self.failure_observed_host_ns = item['observed_host_monotonic_ns']
            if len(self.failure_events) == self.failure_events.maxlen:
                self.failure_events_evicted += 1
            self.failure_events.append(item)

    def _create_send_wrapper(self):
        # The timeout-mode reader already has shared-kernel O_NONBLOCK enabled.
        # Only this duplicate Python object's timeout becomes zero; do not change
        # the reader object, bind, interface, buffers or connected UDP endpoint.
        reader_timeout = self.udp.gettimeout()
        if reader_timeout != .1:
            raise RuntimeError('owned_udp_reader_timeout_contract')
        writer = self.udp.dup()
        try:
            writer.settimeout(0)
            self.send_state.update(dup_created=True, reader_timeout_preserved=self.udp.gettimeout()==reader_timeout,
                same_local_endpoint=writer.getsockname()==self.udp.getsockname(),
                same_peer=writer.getpeername()==self.udp.getpeername())
            if writer.gettimeout()!=0 or not all(self.send_state[key] for key in (
                    'reader_timeout_preserved','same_local_endpoint','same_peer')):
                raise RuntimeError('owned_udp_send_wrapper_contract')
        except Exception:
            writer.close()
            raise
        self.send_udp = writer
        return writer

    def _receive(self):
        replay = ReplayWindow()
        while not self.stop_event.is_set():
            try:
                packet, peer = self.udp.recvfrom(1401)
            except socket.timeout:
                continue
            if peer[0] != self.peer_ip or self.peer is not None and peer != self.peer:
                self.counts['foreign_peer'] += 1
                continue
            try:
                payload = open_packet(self.key, self.tag, packet, replay, CLIENT_NONCE)
            except Exception:
                self.counts['invalid_packets'] += 1
                continue
            if self.peer is None:
                if payload != b'READY':
                    continue
                with self.lifecycle_lock:
                    if self.closed or self.stop_event.is_set():
                        return
                    self.peer = peer
                    self.udp.connect(peer)
                    writer = self._create_send_wrapper()
                    pacer = SocketPacer(32_000_000, burst_bytes=2048, wait=self.stop_event.wait)
                    try:
                        self.sender = AuthenticatedSender(writer, self.key, self.tag, pacer=pacer,
                            send_policy='owned_nonblocking_deadline', owns_socket=True,
                            cancelled=self.stop_event.is_set)
                    except Exception:
                        writer.close()
                        self.send_udp = None
                        raise
            if payload == b'READY':
                self.counts['authenticated_ready'] += 1
                self.registry.authenticated_ready(self.sid)
            elif payload == b'ALIVE':
                self.counts['authenticated_alive'] += 1
                self.registry.authenticated_alive(self.sid)
            elif payload == b'STOP':
                self.counts['authenticated_stop'] += 1
                self.registry.revoke(self.sid)
                return
            elif payload == b'KEYFRAME' and self.registry.touch_allowed(self.sid):
                self._request_idr('phone')
            elif payload.startswith(b'HGUT'):
                if self.touch and self.registry.touch_allowed(self.sid):
                    self.touch.submit(payload)
                else:
                    self.counts['touch_revoked'] += 1
            elif payload.startswith(b'HGPR') and hasattr(self, 'network'):
                with self.control_lock:
                    self.network.pong(payload)
            elif payload.startswith(b'HGUF') and hasattr(self, 'network'):
                with self.control_lock:
                    lane = self.sender.snapshot()['video']
                    decision = self.network.consume(payload, host_video_wire_bytes=
                        lane['encrypted_bytes']+28*lane['datagrams'], local_pressure=False)
                    if decision is not None and self.config.get('bitrate_mode') == 'ADAPTIVE_VBR':
                        self._apply_bitrate(min(decision.requested_bitrate_bps, self.recovery.target_bps))

    def start(self):
        with self.lifecycle_lock:
            if self.started or self.closed:
                return
            self.started = True
        self._background('owned_LAN_UDP_media', self._start_media)

    @staticmethod
    def _native_shutdown_state():
        return dict(stdin_eof_closed=False, stdin_error_class='',
                    stdout_eof_observed=False, stderr_eof_observed=False,
                    stdout_drained_records=0, stdout_drained_bytes=0,
                    final_summary_observed=False, stderr_error_class='',
                    graceful_exit=False, terminate_used=False, kill_used=False,
                    process_exit_confirmed=False, returncode=None)

    def _request_idr(self, origin):
        with self.control_lock:
            if self.stop_event.is_set() or not self.hardware:
                return
            now = time.monotonic()
            if now - self.last_idr < .5:
                return
            self._send_control(b'\x11')
            self.last_idr = now

    def _apply_bitrate(self, target):
        if self.stop_event.is_set() or target == self.target_bps:
            return
        self._send_control(b'\xf0'+struct.pack('>I', target))
        self.target_bps = target
        self.network.requested(target)

    def _start_media(self):
        if self.stop_event.is_set():
            return
        if self.busy():
            raise RuntimeError('formal_session_busy_skip_candidate')
        config = self.config
        self.target_bps = config['video_bit_rate']
        self.recovery = RecoveryController(self.target_bps, 32_000_000)
        self.network = NetworkFeedbackController(self.target_bps, 32_000_000)
        # Hardware owns only this candidate worker's process group and channels.
        hardware = HostHardwareSession(self.runtime, 'emulator-5556', 'RemoteAndroid17Compare',
            config['max_size'], self.target_bps, config['fps'], config['bitrate_mode'],
            raw_queue_policy='fifo', native_encoder=self.native_encoder, sps_low_delay=True,
            raw_submit_fps=config['fps'], matched_experimental_client=True)
        with self.lifecycle_lock:
            if self.closed:
                hardware.close()
                return
            self.hardware = hardware
            self.channels = {role: hardware.channel(role) for role in ('video', 'audio', 'control')}
            self.native = subprocess.Popen([str(self.packetizer), '32000000', '500000', '0', '2048'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.gate = SocketVideoGate(self.sender, lambda reason: self._request_idr('socket'), max_events=128)
            if config['touch_enabled']:
                from run_phone_udp import read_touch_geometry
                geometry = read_touch_geometry(str(Path.home()/'Library/Android/sdk/platform-tools/adb'))
                self.touch = UdpTouchBridge(geometry['effective_width'], geometry['effective_height'],
                    self._write_touch, lambda data: self.sender.send(data, 'touch_ack'), cancel_capable=True)
            for name, operation in (('feed', self._feed), ('video', self._video), ('audio', self._audio),
                                    ('control', self._control), ('events', self._events), ('ping', self._ping)):
                self._background('udp_'+name, operation)
            hardware.start()
            self._background('formal_session_monitor', self._monitor_formal)

    def _monitor_formal(self):
        """Low-frequency fail-closed cancellation; never touches formal sessions."""
        while not self.stop_event.wait(self.FORMAL_CHECK_SECONDS):
            before = time.monotonic()
            self.formal_monitor['checks'] += 1
            try:
                busy = self.busy()
            except Exception:
                self.formal_monitor['check_failures'] += 1
                busy = True
            finally:
                elapsed = max(0.0, (time.monotonic() - before) * 1000)
                self.formal_monitor['max_check_duration_ms'] = max(
                    elapsed, self.formal_monitor['max_check_duration_ms'])
            if busy:
                self.formal_monitor['busy_seen'] += 1
                failure_class = ('formal_busy_check_unavailable_cancel_candidate'
                    if self.formal_monitor['check_failures'] else
                    'formal_session_started_cancel_candidate')
                self._record_failure('formal_session_monitor', RuntimeError(), 'formal_busy_guard', failure_class)
                self.registry.revoke(self.sid)
                return

    def _send_control(self, data):
        """Bound local input writes without timing out an otherwise idle reader."""
        if not 0 < len(data) <= 352:
            raise ValueError('local_control_write_bound')
        channel = self.channels['control']
        deadline = time.monotonic() + .5
        pending = memoryview(data)
        while pending:
            remaining = deadline-time.monotonic()
            if remaining <= 0 or not select.select([], [channel], [], min(.1, remaining))[1]:
                if remaining <= 0:
                    raise TimeoutError('local_control_write_deadline')
                continue
            try:
                count = channel.send(pending, socket.MSG_DONTWAIT)
            except BlockingIOError:
                continue
            if count <= 0:
                raise OSError('local_control_closed')
            pending = pending[count:]

    def _write_touch(self, data):
        with self.control_lock:
            # A cancellation can still clear the guest after revocation. Other
            # queued touch events never inject into an expired or stopped session.
            cancel_only = len(data) == 32 and data[:2] == b'\x02\x03'
            if cancel_only:
                self._send_control(data)
            elif self.stop_event.is_set() or not self.registry.dispatch_touch(self.sid, lambda: self._send_control(data)):
                raise PermissionError('owned_touch_lease_revoked')

    @staticmethod
    def _feed_state():
        return dict(codec_records=0, geometry_records=0, config_records=0,
                    media_records=0, published_bytes=0, max_buffered_bytes=0,
                    cancelled_unpublished_records=0, cancelled_partial_records=0,
                    cancelled_unpublished_bytes=0, live_eof_records=0,
                    partial_timeouts=0, invalid_records=0, publish_failures=0,
                    publish_inflight_bytes=0, publish_written_bytes=0,
                    exit_reason='not_started')

    def _feed_discard(self, record, partial):
        self.feed_state['cancelled_unpublished_records'] += bool(record)
        self.feed_state['cancelled_partial_records'] += bool(record) and partial
        self.feed_state['cancelled_unpublished_bytes'] += len(record)
        self.feed_state['exit_reason'] = 'cancelled_partial' if record and partial else 'cancelled_before_publish'

    def _feed_fill(self, record, target, deadline=None):
        """One record deadline starts with its first byte, not each recv.

        Idle record boundaries remain cancelable without a media-idle timeout.
        Only this worker's owned video socket receives the 100ms read timeout.
        """
        while len(record) < target:
            if self.stop_event.is_set():
                self._feed_discard(record, True)
                return False, deadline
            if deadline is not None and time.monotonic() >= deadline:
                self.feed_state['partial_timeouts'] += 1
                self.feed_state['exit_reason'] = 'live_partial_timeout'
                raise TimeoutError('owned_video_partial_record_deadline')
            try:
                request = min(65536, target-len(record))
                part = self.channels['video'].recv(request)
            except socket.timeout:
                continue
            except OSError:
                if self.stop_event.is_set():
                    self._feed_discard(record, True)
                    return False, deadline
                raise
            if not part:
                if self.stop_event.is_set():
                    self._feed_discard(record, True)
                    return False, deadline
                self.feed_state['live_eof_records'] += 1
                self.feed_state['exit_reason'] = 'live_source_eof'
                raise EOFError('owned_video_source_closed')
            if len(part) > request:
                raise ValueError('owned_video_recv_bound')
            if deadline is None:
                deadline = time.monotonic()+self.FEED_PARTIAL_SECONDS
            record.extend(part)
            self.feed_state['max_buffered_bytes'] = max(
                self.feed_state['max_buffered_bytes'], len(record))
            if time.monotonic() >= deadline:
                self.feed_state['partial_timeouts'] += 1
                self.feed_state['exit_reason'] = 'live_partial_timeout'
                raise TimeoutError('owned_video_partial_record_deadline')
        return True, deadline

    def _feed_publish(self, record, kind):
        if self.stop_event.is_set():
            self._feed_discard(record, False)
            return False
        # Once publication begins, the sole writer finishes the whole record
        # even on cancellation. The video reader then drains without sending.
        # A broken/stalled owned pipe is still a failure; native termination
        # remains bounded by _finish_native, never an unlimited finish wait.
        self.feed_state['publish_inflight_bytes'] = len(record)
        self.feed_state['publish_written_bytes'] = 0
        view = memoryview(record)
        try:
            offset = 0
            while offset < len(view):
                count = self.native.stdin.write(view[offset:])
                if type(count) is not int or not 0 < count <= len(view)-offset:
                    raise OSError('owned_packetizer_write_closed')
                offset += count
                self.feed_state['publish_written_bytes'] = offset
            self.native.stdin.flush()
        except Exception:
            self.feed_state['publish_failures'] += 1
            self.feed_state['exit_reason'] = 'publish_failure'
            raise
        finally:
            view.release()
        self.feed_state[kind+'_records'] += 1
        self.feed_state['published_bytes'] += len(record)
        self.feed_state['publish_inflight_bytes'] = 0
        self.feed_state['publish_written_bytes'] = 0
        return True

    def _feed(self):
        self.feed_state = self._feed_state()
        self.feed_state['exit_reason'] = 'running'
        try:
            self.channels['video'].settimeout(self.FEED_READ_CHECK_SECONDS)
            record = bytearray()
            complete, _ = self._feed_fill(record, 4)
            if not complete:
                return
            if record != b'h264':
                raise ValueError('owned_video_codec_marker')
            if not self._feed_publish(record, 'codec'):
                return
            while not self.stop_event.is_set():
                record = bytearray()
                complete, deadline = self._feed_fill(record, 4)
                if not complete:
                    return
                high = struct.unpack('>I', record)[0]
                if high != 0x80000000 and high & 0x80000000:
                    raise ValueError('owned_video_pts_flag')
                complete, deadline = self._feed_fill(record, 12, deadline)
                if not complete:
                    return
                if high == 0x80000000:
                    width, height = struct.unpack_from('>II', record, 4)
                    if not 0 < width <= 8192 or not 0 < height <= 8192:
                        raise ValueError('owned_video_geometry')
                    kind = 'geometry'
                else:
                    flagged_pts, size = struct.unpack('>QI', record)
                    kind = 'config' if flagged_pts & (1 << 62) else 'media'
                    bound = self.FEED_MAX_CONFIG_BYTES if kind == 'config' else self.FEED_MAX_AU_BYTES
                    if not 0 < size <= bound:
                        raise ValueError('owned_video_au_bound')
                    complete, _ = self._feed_fill(record, 12+size, deadline)
                    if not complete:
                        return
                if not self._feed_publish(record, kind):
                    return
            self.feed_state['exit_reason'] = 'cancelled_at_record_boundary'
        except ValueError:
            if self.feed_state['exit_reason'] != 'publish_failure':
                self.feed_state['invalid_records'] += 1
                self.feed_state['exit_reason'] = 'invalid_record'
            raise
        except Exception:
            if self.feed_state['exit_reason'] == 'running':
                self.feed_state['exit_reason'] = 'source_failure'
            raise
        finally:
            # Only this writer closes stdin, after its last full publication.
            # stop() must not take BufferedWriter's lock from another thread.
            self._close_native_input()

    def _close_native_input(self):
        if self.native is None or self.native.stdin is None:
            return
        try:
            if not self.native.stdin.closed:
                self.native.stdin.close()
            self.native_shutdown['stdin_eof_closed'] = True
        except Exception as error:
            self.native_shutdown['stdin_error_class'] = type(error).__name__

    def _video(self):
        while True:
            try:
                size = struct.unpack('>H', read_exact(self.native.stdout, 2))[0]
                if not 56 < size <= 1080:
                    raise ValueError('native_shard_bound')
                payload = read_exact(self.native.stdout, size)
            except EOFError:
                self.native_shutdown['stdout_eof_observed'] = True
                if not self.stop_event.is_set():
                    raise EOFError('owned_packetizer_output_closed') from None
                return
            if self.stop_event.is_set():
                # Keep the owned pipe moving so EOF can yield the real final
                # counters; never send queued media after revocation.
                self.native_shutdown['stdout_drained_records'] += 1
                self.native_shutdown['stdout_drained_bytes'] += size + 2
            else:
                self.gate.send(payload)

    def _drain_native_stdout(self):
        """Fallback after a missing/failed framed reader; no forwarding."""
        while True:
            data = self.native.stdout.read(65536)
            if not data:
                self.native_shutdown['stdout_eof_observed'] = True
                return
            self.native_shutdown['stdout_drained_bytes'] += len(data)

    def _audio(self):
        stream = self.channels['audio']
        codec = struct.unpack('>I', read_exact(stream, 4))[0]
        if codec != AAC:
            raise ValueError('candidate_requires_AAC')
        frame_id = 0
        config_record = None
        repeated_at = 0.
        while not self.stop_event.is_set():
            pts, size = struct.unpack('>QI', read_exact(stream, 12))
            if not 0 < size <= 1024*1024:
                raise ValueError('audio_record_bound')
            body = read_exact(stream, size)
            if not self.config['audio_enabled']:
                continue
            frame_id += 1
            packets = packetize_audio(pts, body, frame_id, codec=codec)
            if pts & (1 << 62):
                config_record = packets
            elif config_record is not None and time.monotonic()-repeated_at >= 1:
                for packet in config_record:
                    self.sender.send(packet, 'audio')
                repeated_at = time.monotonic()
            for packet in packets:
                self.sender.send(packet, 'audio')

    def _control(self):
        while not self.stop_event.is_set():
            reply = read_control(self.channels['control'])
            if reply[0] == 240:
                value = struct.unpack_from('>I', reply, 1)[0]
                with self.control_lock:
                    self.recovery.acknowledge(value)
                    self.network.acknowledge(value)

    def _events(self):
        try:
            while True:
                line = self.native.stderr.readline(8193)
                if not line:
                    self.native_shutdown['stderr_eof_observed'] = True
                    return
                if len(line) > 8192:
                    raise ValueError('native_summary_bound')
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError('native_event_object_required')
                if value.get('event') == 'summary':
                    if value.get('clock_domain') != 'host_clock_gettime_CLOCK_UPTIME_RAW_us':
                        raise ValueError('candidate_native_clock_contract')
                    self.native_summaries.append(value)
                    if value.get('final') is True:
                        self.native_shutdown['final_summary_observed'] = True
                # Draining a final event cannot request input/IDR/bitrate from
                # the guest whose control channel was already closed.
                if self.stop_event.is_set():
                    continue
                with self.control_lock:
                    if self.stop_event.is_set():
                        continue
                    decision = self.recovery.consume(value) if self.config.get('bitrate_mode') == 'ADAPTIVE_VBR' else None
                    if decision:
                        self.network.set_recovery_ceiling(decision.requested_bitrate_bps)
                        self._apply_bitrate(min(decision.requested_bitrate_bps, self.network.target_bps))
                        if decision.request_idr:
                            self._request_idr('native')
                    elif value.get('event') == 'request_idr':
                        self._request_idr('native')
        except Exception as error:
            self.native_shutdown['stderr_error_class'] = type(error).__name__
            raise

    def _ping(self):
        while not self.stop_event.wait(.1):
            with self.control_lock:
                query = self.network.ping()
            if query:
                self.sender.send(query, 'network_feedback')

    def _finish_native(self):
        if self.native is None:
            return
        # Existing readers keep draining after stop. If startup failed before
        # they were created, install only owned no-forwarding pipe readers.
        video_reader = any(thread.name == 'udp_video' and thread.is_alive()
                           and thread is not threading.current_thread()
                           for thread in self.threads)
        if not video_reader and not self.native_shutdown['stdout_eof_observed']:
            self._background('native_shutdown_stdout_drain', self._drain_native_stdout)
        event_reader = any(thread.name == 'udp_events' and thread.is_alive()
                           and thread is not threading.current_thread()
                           for thread in self.threads)
        if not event_reader and not self.native_shutdown['stderr_eof_observed']:
            self._background('native_shutdown_stderr_drain', self._events)

        feed_threads = [thread for thread in self.threads if thread.name == 'udp_feed']
        for thread in feed_threads:
            if thread is not threading.current_thread():
                thread.join(timeout=1.5)
        if not feed_threads:
            self._close_native_input()
        # If the sole stdin writer is still blocked, never take its buffered I/O
        # lock here. Draining should free it; bounded TERM/KILL is the fallback.
        try:
            code = self.native.wait(timeout=self.NATIVE_GRACE_SECONDS)
            self.native_shutdown['graceful_exit'] = True
        except subprocess.TimeoutExpired:
            self.native_shutdown['terminate_used'] = True
            self.native.terminate()
            try:
                code = self.native.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.native_shutdown['kill_used'] = True
                self.native.kill()
                code = self.native.wait(timeout=2)
        self.native_shutdown['process_exit_confirmed'] = True
        self.native_shutdown['returncode'] = code

    def stop(self):
        with self.lifecycle_lock:
            if self.closed:
                return
            self.closed = True
            self.stop_event.set()
        errors = []
        def attempt(stage, operation):
            try:
                operation()
                return True
            except Exception as error:
                errors.append({'stage': stage, 'error_class': type(error).__name__})
                return False
        if self.sender:
            # Stop fresh seals/sends and close only the owned writer duplicate.
            # The reader remains owned by this worker's existing UDP cleanup.
            attempt('sender_close', self.sender.close)
        elif self.send_udp is not None:
            attempt('orphan_send_wrapper_close', self.send_udp.close)
        if self.touch:
            attempt('touch_close', self.touch.close)
        if self.hardware:
            attempt('hardware_close', self.hardware.close)
        if self.touch and self.touch.thread is not threading.current_thread():
            attempt('touch_join', lambda: self.touch.thread.join(timeout=1))
        if self.touch and self.touch.thread.is_alive():
            errors.append({'stage': 'touch_quiescence', 'error_class': 'Unconfirmed'})
        attempt('udp_close', self.udp.close)
        # A canceled constructor may still own an initializing subprocess.
        # Hold the registry reservation until it observes closed and tears down.
        if self.started and not self.startup_done.wait(25):
            errors.append({'stage': 'startup_quiescence', 'error_class': 'Unconfirmed'})
        attempt('native_finish', self._finish_native)
        deadline = time.monotonic() + 2
        thread_state = []
        for thread in list(self.threads):
            if thread is not threading.current_thread():
                attempt('thread_join', lambda thread=thread:
                    thread.join(timeout=max(0.0, deadline-time.monotonic())))
                if thread.is_alive():
                    errors.append({'stage': 'thread_quiescence', 'error_class': 'Unconfirmed'})
            thread_state.append({'name': thread.name, 'alive': thread.is_alive(),
                                 'stop_invoker_excluded': thread is threading.current_thread()})
        if self.native is not None:
            if not self.native_shutdown['process_exit_confirmed']:
                errors.append({'stage': 'native_exit', 'error_class': 'Unconfirmed'})
            # Only close pipe handles after their owned readers/writer exited;
            # close() itself may acquire a held BufferedIO lock otherwise.
            if not any(item['alive'] and not item['stop_invoker_excluded'] for item in thread_state):
                for pipe in (self.native.stdin, self.native.stdout, self.native.stderr):
                    if pipe is not None:
                        attempt('native_pipe_close', pipe.close)
        final_status = ('not_started' if self.native is None else
            'confirmed' if self.native_shutdown['final_summary_observed'] else
            'missing_after_forced_exit' if self.native_shutdown['terminate_used'] else
            'missing_after_graceful_exit' if self.native_shutdown['process_exit_confirmed'] else
            'missing_process_exit_unconfirmed')
        network_scope = self.config.get('network_scope', 'lan')
        report = {'scope': 'authenticated_App_'+network_scope+'_UDP_candidate_not_public_or_optical_acceptance',
                  'network_scope': network_scope,
                  'source': 'M1_emulator_5556', 'path': ('physical_LAN_AESGCM_UDP' if network_scope=='lan' else 'registered_Tailnet_inner_AESGCM_UDP_outer_path_unverified'),
                  'counts': dict(self.counts), 'failure_class': self.failure,
                  'failure_role': self.failure_role, 'failure_operation': self.failure_operation,
                  'failure_observed_host_monotonic_ns': self.failure_observed_host_ns,
                  'failure_events': list(self.failure_events), 'failure_events_evicted': self.failure_events_evicted,
                  'failure_primary_first_observed_not_proven_root_cause': True,
                  'owned_send_wrapper': dict(self.send_state),
                  'native_summaries': list(self.native_summaries),
                  'native_shutdown': dict(self.native_shutdown), 'native_final_status': final_status,
                  'host_video_feed': dict(self.feed_state),
                  'thread_cleanup': thread_state, 'cleanup_errors': errors,
                  'cleanup_confirmed': not errors, 'formal_session_monitor': dict(self.formal_monitor),
                  'native_clock': 'CLOCK_UPTIME_RAW', 'tcp_media_used': False,
                  'requested': {key: self.config[key] for key in ('max_size','fps','video_bit_rate','buffer_ms','seconds')},
                  'socket_pacer_wait_enabled': True, 'assembly_lifetime_ms': 80}
        if self.sender:
            report['udp_lanes'] = self.sender.snapshot()
            report['udp_send_policy'] = self.sender.policy_snapshot()
        if self.gate:
            report['socket_guard'] = dict(self.gate.counts)
        if self.touch:
            report['native_touch'] = self.touch.stats()
        self.config.clear()
        self.key = None
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        destination = self.evidence_dir / ('host-session-'+str(time.time_ns())+'.json')
        fd = os.open(destination, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump(report, output, indent=2)
            output.write('\n')
        if errors:
            raise TimeoutError('owned_cleanup_unconfirmed')
