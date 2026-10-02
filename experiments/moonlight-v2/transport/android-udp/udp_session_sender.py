"""One authenticated sequence space for concurrent experimental UDP lanes.

The caller owns a physically bound, connected socket and a fresh per-test key.
No pairing, keys on disk, network routes, or production services are created.
"""
import threading
from collections import deque
import struct
import time

from udp_probe_protocol import seal


class PacingDeadline(TimeoutError):
    """A video shard would miss its host-clock complete-frame deadline."""


class SocketPacer:
    """Serialize video at the actual socket, with priority-lane byte accounting.

    Waiting never holds the authentication/send lock. Audio, touch ACK and ping
    send immediately and add debt to the subsequent video schedule. This is a
    bounded priority policy, not a guarantee that every sub-millisecond window
    stays under the rate. IPv4/UDP plus HGUE/GCM overhead is included by caller.
    """
    def __init__(self, bitrate_bps, *, burst_bytes=0, clock=time.monotonic, wait=None):
        if type(bitrate_bps) is not int or not 500_000 <= bitrate_bps <= 40_000_000:
            raise ValueError('socket pacing budget bound')
        if type(burst_bytes) is not int or not 0 <= burst_bytes <= 4096:
            raise ValueError('socket catch-up credit byte bound')
        self.bitrate_bps = bitrate_bps
        self.burst_bytes = burst_bytes
        self.clock = clock
        self.wait = wait or time.sleep
        self.next_at = 0.
        self.lock = threading.Lock()
        self.counts = dict(video_reservations=0, video_waits=0, video_wait_us=0, planned_video_wait_us=0,
                           max_video_wait_us=0, max_wait_overshoot_us=0, deadline_rejections=0,
                           priority_wire_bytes=0, reserved_video_wire_bytes=0)

    def video(self, wire_bytes, deadline_us=None):
        cost = wire_bytes*8/self.bitrate_bps
        with self.lock:
            now = self.clock()
            virtual = max(self.next_at, now-self.burst_bytes*8/self.bitrate_bps)
            at = max(now, virtual)
            if deadline_us is not None and (at+cost)*1_000_000 > deadline_us:
                self.counts['deadline_rejections'] += 1
                raise PacingDeadline('socket full-shard serialization exceeds frame deadline')
            self.next_at = virtual+cost
            self.counts['video_reservations'] += 1
            self.counts['reserved_video_wire_bytes'] += wire_bytes
        remaining = at-self.clock()
        if remaining > 0:
            # No authentication or pacer lock is held across the sleep.
            wait_start = self.clock()
            self.wait(remaining)
            with self.lock:
                wait_us = round(max(0, self.clock()-wait_start)*1_000_000)
                self.counts['video_waits'] += 1
                self.counts['video_wait_us'] += wait_us
                self.counts['planned_video_wait_us'] += round(remaining*1_000_000)
                self.counts['max_video_wait_us'] = max(self.counts['max_video_wait_us'], wait_us)
                self.counts['max_wait_overshoot_us'] = max(self.counts['max_wait_overshoot_us'],
                    round(max(0, wait_us-remaining*1_000_000)))
        if deadline_us is not None and (self.clock()+cost)*1_000_000 > deadline_us:
            with self.lock:
                self.counts['deadline_rejections'] += 1
            raise PacingDeadline('socket scheduling overslept frame deadline')

    def sent(self, wire_bytes):
        # Account at actual send time. A late packet must spend the bounded
        # credit itself; it cannot grant a fresh bucket to its successors.
        if self.burst_bytes:
            with self.lock:
                self.next_at = max(self.next_at, self.clock()+
                                   (wire_bytes-self.burst_bytes)*8/self.bitrate_bps)

    def rejected_deadline(self):
        with self.lock:
            self.counts['deadline_rejections'] += 1

    def priority(self, wire_bytes):
        with self.lock:
            self.next_at = max(self.clock(), self.next_at) + wire_bytes*8/self.bitrate_bps
            self.counts['priority_wire_bytes'] += wire_bytes

    def snapshot(self):
        with self.lock:
            return dict(self.counts, bitrate_bps=self.bitrate_bps, burst_bytes=self.burst_bytes,
                        scope='actual_socket_scheduling_estimated_IPv4_wire_bytes',
                        priority_policy='audio_touch_ping_immediate_with_video_debt',
                        reservation_includes_injected_test_loss=True)


class AuthenticatedSender:
    LANES = frozenset(('video', 'audio', 'touch_ack', 'network_feedback'))

    def __init__(self, sock, key, session, video_drop_every=0, *, pacer=None, clock_ns=time.monotonic_ns):
        if type(video_drop_every) is not int or video_drop_every != 0 and video_drop_every < 20:
            raise ValueError('Fault injection must be disabled or at most 5 percent')
        self.socket, self.key, self.session = sock, key, session
        self.video_drop_every = video_drop_every
        self.video_attempts = 0
        self.sequence = 1
        self.lock = threading.Lock()
        self.pacer = pacer
        self.clock_ns = clock_ns
        self.last_timing = {}
        self.counts = {lane: {'datagrams': 0, 'encrypted_bytes': 0, 'send_errors': 0, 'test_dropped_datagrams': 0,
                             'deadline_rejections': 0, 'max_auth_lock_wait_ns': 0, 'max_seal_ns': 0,
                             'max_send_syscall_ns': 0, 'send_completed_after_deadline_count': 0,
                             'max_send_deadline_overshoot_ns': 0}
                       for lane in self.LANES}

    def _check_deadline_locked(self, wire_bytes, deadline_us, now_ns):
        """Called only under sender lock; use sender's host monotonic clock.

        A reservation cannot protect later authentication-lock or seal delays.
        Recheck remaining serialization headroom immediately before each phase.
        Taking the pacer lock here follows the existing sender -> pacer order;
        pacing waits never hold that lock while waiting for the sender.
        """
        if deadline_us is None:
            return
        deadline_ns = deadline_us*1000
        cost_ns = ((wire_bytes*8*1_000_000_000+self.pacer.bitrate_bps-1)//self.pacer.bitrate_bps
                   if self.pacer else 0)
        if now_ns >= deadline_ns or cost_ns > deadline_ns-now_ns:
            self.counts['video']['deadline_rejections'] += 1
            if self.pacer:
                self.pacer.rejected_deadline()
            raise PacingDeadline('authentication/send scheduling exceeds frame deadline')

    def send(self, payload, lane, *, deadline_us=None):
        if lane not in self.LANES or not 0 < len(payload) <= 1080:
            raise ValueError('Unknown UDP lane or payload bound')
        if deadline_us is not None and (type(deadline_us) is not int or deadline_us <= 0 or lane != 'video'):
            raise ValueError('Deadline only applies to host-clock video')
        if self.pacer and lane == 'video':
            self.pacer.video(len(payload)+68, deadline_us)
        waiting = self.clock_ns()
        with self.lock:
            acquired = self.clock_ns()
            self.counts[lane]['max_auth_lock_wait_ns'] = max(self.counts[lane]['max_auth_lock_wait_ns'],
                                                          max(0, acquired-waiting))
            if self.key is None or self.sequence > 0xffffffffffffffff:
                raise ValueError('UDP session closed or sequence exhausted')
            self._check_deadline_locked(len(payload)+68, deadline_us, acquired)
            # Consume the nonce even if send fails. A retry seals a new packet,
            # never different plaintext under the same key/nonce.
            sequence = self.sequence
            self.sequence += 1
            sealing = self.clock_ns()
            try:
                packet = seal(self.key, self.session, sequence, payload)
            finally:
                self.counts[lane]['max_seal_ns'] = max(self.counts[lane]['max_seal_ns'],
                                                     max(0, self.clock_ns()-sealing))
            injected_drop = False
            if lane == 'video':
                self.video_attempts += 1
                injected_drop = bool(self.video_drop_every and self.video_attempts % self.video_drop_every == 0)
            start = self.clock_ns()
            # Outside the OSError handler: PacingDeadline is a TimeoutError,
            # and must not be mislabeled as a socket syscall error.
            self._check_deadline_locked(len(payload)+68, deadline_us, start)
            if injected_drop:
                self.counts[lane]['test_dropped_datagrams'] += 1
                if self.pacer:
                    self.pacer.sent(len(payload)+68)
                return 0  # Explicit probe-only loss, not a successful send.
            try:
                size = self.socket.send(packet)
                end = self.clock_ns()
                if size != len(packet):
                    raise OSError('Partial UDP send')
            except OSError:
                self.counts[lane]['send_errors'] += 1
                raise
            finally:
                self.counts[lane]['max_send_syscall_ns'] = max(self.counts[lane]['max_send_syscall_ns'],
                                                             max(0, self.clock_ns()-start))
            if deadline_us is not None and end > deadline_us*1000:
                # UDP has already been posted. Do not pretend it can be undone
                # or that the syscall was interrupted at the frame deadline.
                self.counts[lane]['send_completed_after_deadline_count'] += 1
                self.counts[lane]['max_send_deadline_overshoot_ns'] = max(
                    self.counts[lane]['max_send_deadline_overshoot_ns'],end-deadline_us*1000)
            self.counts[lane]['datagrams'] += 1
            self.counts[lane]['encrypted_bytes'] += size
            self.last_timing[lane] = (start, end)
            if self.pacer:
                if lane == 'video':
                    self.pacer.sent(size+28)
                else:
                    self.pacer.priority(size+28)
            return size

    def timing(self, lane):
        with self.lock:
            return self.last_timing.get(lane)

    def snapshot(self):
        with self.lock:
            return {lane: dict(values) for lane, values in self.counts.items()}

    def close(self):
        with self.lock:
            self.key = None


class SocketVideoGate:
    """Optional same-host deadline and dependency guard around actual sends.

    Native packetizer emits shards in block/shard order. Missing original data
    invalidates the later P chain. Missing only final parity, after EVERY block's
    original data was attempted, does not invalidate an intact media reference.
    Metadata is bounded, contains no media bytes and uses host monotonic only.
    The caller serializes this one video lane; audio/touch use sender directly.
    """
    def __init__(self, sender, recovery, *, guard=True, max_events=10_000, clock_us=None):
        self.sender, self.recovery, self.guard = sender, recovery, guard
        self.clock_us = clock_us or (lambda: time.monotonic_ns()//1000)
        self.events = deque(maxlen=max_events)
        self.evicted = 0
        self.current = None
        self.needs_idr = guard
        self.last_reference = 0
        self.last_position = -1
        self.current_keyframe = False
        self.current_reference = 0
        self.current_flags = 0
        self.current_blocks = 0
        self.data_attempted = {}
        self.counts = dict(deadline_dropped_frames=0, skipped_chain_frames=0,
                           incomplete_output_frames=0, completed_idr_frames=0,
                           tail_parity_deadline_frames=0, tail_parity_deadline_datagrams=0,
                           incomplete_fec_output_frames=0)

    def _invalidate(self, reason):
        if self.current['drop_reason'] is not None:
            return
        self.current['drop_reason'] = reason
        self.needs_idr = True
        if reason == 'socket_frame_deadline':
            self.counts['deadline_dropped_frames'] += 1
        elif reason == 'waiting_complete_idr_or_reference':
            self.counts['skipped_chain_frames'] += 1
        else:
            self.counts['incomplete_output_frames'] += 1
        self.recovery(reason)

    def _finish_previous(self):
        if self.current is None:
            return
        if self.guard and not self.current['media_data_complete'] and self.current['drop_reason'] is None:
            self._invalidate('incomplete_native_frame')
        elif self.current['media_data_complete'] and not self.current['all_records_processed']:
            self.counts['incomplete_fec_output_frames'] += 1
            if self.current['drop_reason'] is None:
                self.current['drop_reason'] = 'native_tail_parity_not_emitted_after_data_complete'
        if len(self.events) == self.events.maxlen:
            self.evicted += 1
        self.events.append(dict(self.current))

    def _record_data_attempt(self, block, data, shard):
        if shard >= data:
            return
        count, mask = self.data_attempted.get(block, (data, 0))
        if count != data:
            raise ValueError('native block data count changed')
        self.data_attempted[block] = (count, mask | (1 << shard))
        all_data = (len(self.data_attempted) == self.current_blocks and
                    all(mask == (1 << count)-1 for count, mask in self.data_attempted.values()))
        if all_data and not self.current['media_data_complete']:
            self.current['media_data_complete'] = True
            if self.guard:
                self.needs_idr = False
                if self.current_flags & 2:
                    self.last_reference = self.current['frame_id']
                if self.current_keyframe:
                    self.counts['completed_idr_frames'] += 1

    def send(self, payload):
        if len(payload) <= 56 or payload[:5] != b'HGUD\x01':
            raise ValueError('native HGUD header')
        flags, shard, data, parity = payload[5:9]
        block, blocks, shard_bytes = struct.unpack_from('>HHH', payload, 10)
        frame, capture = struct.unpack_from('>QQ', payload, 16)
        lifetime = struct.unpack_from('>I', payload, 40)[0]
        reference = struct.unpack_from('>Q', payload, 48)[0]
        if (flags > 3 or not frame or not 1 <= data <= 10 or parity != 2 or shard >= data+parity or
            not 1 <= blocks <= 103 or block >= blocks or shard_bytes != len(payload)-56 or
            not 1_000 <= lifetime <= 80_000 or not capture):
            raise ValueError('native HGUD bound')
        at = self.clock_us()
        if self.current is None or frame != self.current['frame_id']:
            if self.current is not None and frame <= self.current['frame_id']:
                raise ValueError('native frame order')
            self._finish_previous()
            self.current = dict(frame_id=frame, capture_host_us=capture,
                                first_read_host_us=at, last_read_host_us=at,
                                first_socket_host_us=None, last_socket_host_us=None,
                                sent_datagrams=0, estimated_IPv4_wire_bytes=0,
                                complete=False, all_records_processed=False,
                                media_data_complete=False, tail_parity_deadline_datagrams=0,
                                drop_reason=None)
            self.current_keyframe, self.current_reference = bool(flags & 1), reference
            self.current_flags, self.current_blocks = flags, blocks
            self.data_attempted = {}
            self.last_position = -1
            if self.guard and (block or shard or
                               (not self.current_keyframe and (self.needs_idr or reference != self.last_reference))):
                self._invalidate('waiting_complete_idr_or_reference')
        elif (capture != self.current['capture_host_us'] or flags != self.current_flags or
              blocks != self.current_blocks or reference != self.current_reference):
            raise ValueError('native frame header changed')
        self.current['last_read_host_us'] = at
        position = block*12+shard
        if (position != self.last_position+1 and self.guard and
            not (self.current['media_data_complete'] and block == blocks-1 and
                 shard >= data and position > self.last_position)):
            self._invalidate('incomplete_native_frame')
        self.last_position = position
        if self.guard and self.current['drop_reason'] is not None:
            if (self.current['media_data_complete'] and block == blocks-1 and shard >= data and
                self.current['drop_reason'] == 'tail_parity_deadline_after_data_complete'):
                self.current['tail_parity_deadline_datagrams'] += 1
                self.counts['tail_parity_deadline_datagrams'] += 1
            return 0
        try:
            size = self.sender.send(payload, 'video', deadline_us=capture+lifetime if self.guard else None)
        except PacingDeadline:
            if self.current['media_data_complete'] and block == blocks-1 and shard >= data:
                self.current['drop_reason'] = 'tail_parity_deadline_after_data_complete'
                self.current['tail_parity_deadline_datagrams'] += 1
                self.counts['tail_parity_deadline_frames'] += 1
                self.counts['tail_parity_deadline_datagrams'] += 1
            else:
                self._invalidate('socket_frame_deadline')
            return 0
        # A successful sender invocation includes intentional pre-socket fault
        # injection: simulated loss retains ordinary network/FEC semantics.
        self._record_data_attempt(block, data, shard)
        if size:
            start, end = self.sender.timing('video')
            if self.current['first_socket_host_us'] is None:
                self.current['first_socket_host_us'] = start//1000
            self.current['last_socket_host_us'] = end//1000
            self.current['sent_datagrams'] += 1
            self.current['estimated_IPv4_wire_bytes'] += size+28
        if block == blocks-1 and shard == data+parity-1:
            self.current['complete'] = True
            self.current['all_records_processed'] = True
        return size

    def snapshot(self):
        values = list(self.events)
        if self.current is not None:
            values.append(dict(self.current))
        return {'scope': 'same_host_monotonic_native_header_to_actual_socket_not_network_latency',
                'guard_enabled': self.guard, 'needs_complete_idr': self.needs_idr,
                'counts': dict(self.counts), 'frame_events': values[-self.events.maxlen:],
                'frame_events_evicted': self.evicted+max(0, len(values)-self.events.maxlen),
                'complete_meaning': 'legacy alias of all_records_processed; all shard records passed to sender, including explicit injected loss',
                'media_data_complete_meaning': 'all original data of every block passed to sender; not receiver delivery or successful FEC protection',
                'completed_idr_frames_meaning': 'IDR original data complete; parity completion is tracked separately'}
