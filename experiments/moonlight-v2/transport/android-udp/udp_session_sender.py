"""One authenticated sequence space for concurrent experimental UDP lanes.

The caller owns a physically bound, connected socket and a fresh per-test key.
No pairing, keys on disk, network routes, or production services are created.
"""
import threading
import errno
import select
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
    def __init__(self, bitrate_bps, *, burst_bytes=0, clock=time.monotonic, wait=None,
                 wait_enabled=True):
        if type(bitrate_bps) is not int or not 500_000 <= bitrate_bps <= 40_000_000:
            raise ValueError('socket pacing budget bound')
        if type(burst_bytes) is not int or not 0 <= burst_bytes <= 4096:
            raise ValueError('socket catch-up credit byte bound')
        if type(wait_enabled) is not bool:
            raise ValueError('socket wait selection must be boolean')
        self.bitrate_bps = bitrate_bps
        self.burst_bytes = burst_bytes
        self.clock = clock
        self.wait = wait or time.sleep
        self.wait_enabled = wait_enabled
        self.next_at = 0.
        self.lock = threading.Lock()
        self.counts = dict(video_reservations=0, video_waits=0, video_wait_us=0, planned_video_wait_us=0,
                           max_video_wait_us=0, max_wait_overshoot_us=0, deadline_rejections=0,
                           priority_wire_bytes=0, reserved_video_wire_bytes=0,
                           skipped_video_waits=0, skipped_planned_video_wait_us=0,
                           max_skipped_planned_video_wait_us=0)

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
        if remaining > 0 and not self.wait_enabled:
            # Single-variable experiment: skip this Python timed wait only.
            # Reservations, serialization deadline checks and priority debt
            # remain identical. This mode relies on the unchanged, bounded
            # native pacer upstream; it does not enforce socket send spacing.
            with self.lock:
                skipped_us = round(remaining*1_000_000)
                self.counts['skipped_video_waits'] += 1
                self.counts['skipped_planned_video_wait_us'] += skipped_us
                self.counts['max_skipped_planned_video_wait_us'] = max(
                    self.counts['max_skipped_planned_video_wait_us'], skipped_us)
        elif remaining > 0:
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
                        wait_enabled=self.wait_enabled,
                        wait_policy=('socket_timed_wait' if self.wait_enabled else
                                     'skip_socket_timed_wait_keep_reservations_and_deadlines'),
                        socket_send_spacing_enforced=self.wait_enabled,
                        skipped_wait_scope='sum_of_skipped_plans_not_measured_latency_saved',
                        scope='actual_socket_scheduling_estimated_IPv4_wire_bytes',
                        priority_policy='audio_touch_ping_immediate_with_video_debt',
                        reservation_includes_injected_test_loss=True)


class AuthenticatedSender:
    LANES = frozenset(('video', 'audio', 'touch_ack', 'network_feedback'))
    SEND_POLICIES = frozenset(('legacy_socket_timeout', 'owned_nonblocking_deadline'))
    WRITABLE_WAIT_NS = 5_000_000
    PRIORITY_BUDGET_NS = 10_000_000
    MAX_WOULD_BLOCK_ATTEMPTS = 64
    MAX_ENOBUFS_CALLS = 8
    ENOBUFS_BACKOFF_NS = (1_000_000, 2_000_000, 4_000_000, 5_000_000)

    def __init__(self, sock, key, session, video_drop_every=0, *, pacer=None, clock_ns=time.monotonic_ns,
                 send_policy='legacy_socket_timeout', owns_socket=False, cancelled=None, wait_writable=None,
                 enobufs_retry_enabled=False, backpressure_wait=None):
        if type(video_drop_every) is not int or video_drop_every != 0 and video_drop_every < 20:
            raise ValueError('Fault injection must be disabled or at most 5 percent')
        if send_policy not in self.SEND_POLICIES or type(owns_socket) is not bool:
            raise ValueError('UDP send policy/ownership contract')
        if send_policy == 'owned_nonblocking_deadline' and sock.gettimeout() != 0:
            raise ValueError('Owned candidate writer must have Python nonblocking timeout')
        if type(enobufs_retry_enabled) is not bool or enobufs_retry_enabled and send_policy != 'owned_nonblocking_deadline':
            raise ValueError('ENOBUFS retry requires explicit owned nonblocking opt-in')
        if backpressure_wait is not None and not callable(backpressure_wait):
            raise ValueError('ENOBUFS backpressure wait must be callable')
        self.send_policy, self.owns_socket = send_policy, owns_socket
        self.enobufs_retry_enabled = enobufs_retry_enabled
        # Unlike EAGAIN readiness, ENOBUFS may persist while select is writable.
        # A caller can inject stop_event.wait for prompt cancellation; the default
        # sleep is bounded by one <=5ms slice, never the authentication lock.
        self.backpressure_wait = time.sleep if backpressure_wait is None else backpressure_wait
        self.cancelled = cancelled or (lambda: False)
        self.wait_writable = wait_writable or self._wait_writable
        self.socket_close_attempted = self.socket_close_confirmed = False
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
                             'max_send_deadline_overshoot_ns': 0,
                             'would_block_calls': 0, 'would_block_retries': 0, 'retry_wait_ns': 0,
                             'max_retry_wait_ns': 0, 'max_total_send_call_ns': 0,
                             'would_block_deadline_drops': 0, 'priority_budget_dropped_datagrams': 0,
                             'retry_bound_dropped_datagrams': 0,
                             'enobufs_calls': 0, 'enobufs_retry_syscalls': 0,
                             'enobufs_recovered_datagrams': 0, 'enobufs_fatal_calls': 0,
                             'enobufs_deadline_drops': 0, 'enobufs_retry_bound_drops': 0,
                             'enobufs_transient_bound_drops': 0, 'enobufs_backoff_waits': 0,
                             'enobufs_backoff_wait_ns': 0, 'max_enobufs_backoff_wait_ns': 0}

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
        if self.send_policy == 'owned_nonblocking_deadline' and lane == 'video' and deadline_us is None:
            raise ValueError('Owned nonblocking video requires the original frame deadline')
        if self.pacer and lane == 'video':
            self.pacer.video(len(payload)+68, deadline_us)
        if self.send_policy == 'owned_nonblocking_deadline':
            return self._send_nonblocking(payload, lane, deadline_us)
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

    @staticmethod
    def _wait_writable(sock, seconds):
        return bool(select.select([], [sock], [], seconds)[1])

    @staticmethod
    def _failure_context(error, lane, operation):
        # Only fixed operation/lane enums, never exception text, endpoint or key.
        error.huoguo_udp_failure_lane = lane
        error.huoguo_udp_failure_operation = operation
        return error

    def _drop_nonblocking_locked(self, lane, would_block, retry_bound=False, *,
                                 enobufs=False, enobufs_bound=False):
        counts = self.counts[lane]
        if would_block:
            counts['would_block_deadline_drops'] += 1
        if retry_bound:
            counts['retry_bound_dropped_datagrams'] += 1
        if enobufs:
            if enobufs_bound:
                counts['enobufs_retry_bound_drops'] += 1
            elif retry_bound:
                counts['enobufs_transient_bound_drops'] += 1
            else:
                counts['enobufs_deadline_drops'] += 1
        if lane == 'video':
            counts['deadline_rejections'] += 1
            if self.pacer:
                self.pacer.rejected_deadline()
            raise PacingDeadline('owned nonblocking video deadline/retry bound')
        counts['priority_budget_dropped_datagrams'] += 1
        return 0  # Explicit late priority drop, never a successful datagram.

    def _send_nonblocking(self, payload, lane, deadline_us):
        began = self.clock_ns()
        bound_ns = deadline_us*1000 if lane == 'video' else began+self.PRIORITY_BUDGET_NS
        attempts = 0
        would_block_calls = enobufs_calls = 0
        retry_reason = None
        try:
            while True:
                waiting = self.clock_ns()
                with self.lock:
                    acquired = self.clock_ns()
                    counts = self.counts[lane]
                    counts['max_auth_lock_wait_ns'] = max(counts['max_auth_lock_wait_ns'], max(0, acquired-waiting))
                    if self.cancelled():
                        raise self._failure_context(OSError(errno.ECANCELED, 'owned UDP canceled'), lane, 'udp_send_cancel')
                    if self.key is None or self.sequence > 0xffffffffffffffff:
                        raise self._failure_context(ValueError('UDP session closed or sequence exhausted'), lane, 'udp_sender_state')
                    if lane == 'video':
                        try:
                            self._check_deadline_locked(len(payload)+68, deadline_us, acquired)
                        except PacingDeadline:
                            if would_block_calls:
                                counts['would_block_deadline_drops'] += 1
                            if enobufs_calls:
                                counts['enobufs_deadline_drops'] += 1
                            raise
                    elif acquired >= bound_ns:
                        return self._drop_nonblocking_locked(lane, bool(would_block_calls), enobufs=bool(enobufs_calls))
                    if attempts >= self.MAX_WOULD_BLOCK_ATTEMPTS:
                        return self._drop_nonblocking_locked(lane, bool(would_block_calls), True, enobufs=bool(enobufs_calls))
                    if retry_reason == 'would_block':
                        counts['would_block_retries'] += 1
                    sequence = self.sequence
                    self.sequence += 1  # A failed attempt never reuses this nonce.
                    sealing = self.clock_ns()
                    try:
                        packet = seal(self.key, self.session, sequence, payload)
                    except Exception as error:
                        raise self._failure_context(error, lane, 'udp_auth_seal')
                    finally:
                        counts['max_seal_ns'] = max(counts['max_seal_ns'], max(0, self.clock_ns()-sealing))
                    start = self.clock_ns()
                    if lane == 'video':
                        try:
                            self._check_deadline_locked(len(payload)+68, deadline_us, start)
                        except PacingDeadline:
                            if would_block_calls:
                                counts['would_block_deadline_drops'] += 1
                            if enobufs_calls:
                                counts['enobufs_deadline_drops'] += 1
                            raise
                        self.video_attempts += 1
                        if self.video_drop_every and self.video_attempts % self.video_drop_every == 0:
                            counts['test_dropped_datagrams'] += 1
                            if self.pacer:
                                self.pacer.sent(len(payload)+68)
                            return 0
                    elif start >= bound_ns:
                        return self._drop_nonblocking_locked(lane, bool(would_block_calls), enobufs=bool(enobufs_calls))
                    try:
                        if retry_reason == 'enobufs':
                            counts['enobufs_retry_syscalls'] += 1
                        size = self.socket.send(packet)
                        end = self.clock_ns()
                        if size != len(packet):
                            raise OSError('Partial UDP send')
                    except OSError as error:
                        counts['send_errors'] += 1
                        if isinstance(error, BlockingIOError) and error.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                            counts['would_block_calls'] += 1
                            would_block_calls += 1
                            attempts += 1
                            retry_reason = 'would_block'
                        elif error.errno == errno.ENOBUFS:
                            counts['enobufs_calls'] += 1
                            if not self.enobufs_retry_enabled:
                                counts['enobufs_fatal_calls'] += 1
                                raise self._failure_context(error, lane, 'udp_socket_send')
                            enobufs_calls += 1
                            attempts += 1
                            retry_reason = 'enobufs'
                            if enobufs_calls >= self.MAX_ENOBUFS_CALLS:
                                return self._drop_nonblocking_locked(lane, bool(would_block_calls), True,
                                    enobufs=True, enobufs_bound=True)
                        else:
                            raise self._failure_context(error, lane, 'udp_socket_send')
                    else:
                        if deadline_us is not None and end > deadline_us*1000:
                            counts['send_completed_after_deadline_count'] += 1
                            counts['max_send_deadline_overshoot_ns'] = max(counts['max_send_deadline_overshoot_ns'], end-deadline_us*1000)
                        counts['datagrams'] += 1
                        counts['encrypted_bytes'] += size
                        if enobufs_calls:
                            counts['enobufs_recovered_datagrams'] += 1
                        self.last_timing[lane] = (start, end)
                        if self.pacer:
                            self.pacer.sent(size+28) if lane == 'video' else self.pacer.priority(size+28)
                        return size
                    finally:
                        counts['max_send_syscall_ns'] = max(counts['max_send_syscall_ns'], max(0, self.clock_ns()-start))
                # No authentication or pacer mutex is held during either wait.
                before_wait = self.clock_ns()
                remaining = bound_ns-before_wait
                if remaining > 0 and not self.cancelled():
                    try:
                        if retry_reason == 'enobufs':
                            pause = self.ENOBUFS_BACKOFF_NS[min(enobufs_calls-1, len(self.ENOBUFS_BACKOFF_NS)-1)]
                            self.backpressure_wait(min(pause, remaining)/1e9)
                        else:
                            self.wait_writable(self.socket, min(self.WRITABLE_WAIT_NS, remaining)/1e9)
                    except (OSError, ValueError) as error:
                        # Closing this owned socket while select examines it can
                        # raise ValueError for fd=-1. Keep its class and hard
                        # failure semantics; only attach the fixed operation.
                        raise self._failure_context(error, lane, 'udp_send_wait')
                    finally:
                        elapsed = max(0, self.clock_ns()-before_wait)
                        with self.lock:
                            if retry_reason == 'enobufs':
                                self.counts[lane]['enobufs_backoff_waits'] += 1
                                self.counts[lane]['enobufs_backoff_wait_ns'] += elapsed
                                self.counts[lane]['max_enobufs_backoff_wait_ns'] = max(self.counts[lane]['max_enobufs_backoff_wait_ns'], elapsed)
                            else:
                                self.counts[lane]['retry_wait_ns'] += elapsed
                                self.counts[lane]['max_retry_wait_ns'] = max(self.counts[lane]['max_retry_wait_ns'], elapsed)
        finally:
            with self.lock:
                self.counts[lane]['max_total_send_call_ns'] = max(self.counts[lane]['max_total_send_call_ns'], max(0, self.clock_ns()-began))

    def policy_snapshot(self):
        with self.lock:
            return dict(send_policy=self.send_policy, nonblocking_writer=self.send_policy=='owned_nonblocking_deadline',
                        owns_send_socket=self.owns_socket, writable_wait_slice_ns=self.WRITABLE_WAIT_NS,
                        priority_budget_ns=self.PRIORITY_BUDGET_NS, max_would_block_attempts=self.MAX_WOULD_BLOCK_ATTEMPTS,
                        max_transient_failure_calls=self.MAX_WOULD_BLOCK_ATTEMPTS,
                        enobufs_retry_enabled=self.enobufs_retry_enabled, max_enobufs_calls=self.MAX_ENOBUFS_CALLS,
                        enobufs_retry_counter_scope='syscalls_directly_following_an_ENOBUFS_timed_wait',
                        enobufs_backoff_slices_ns=list(self.ENOBUFS_BACKOFF_NS),
                        enobufs_wait_policy='cancel_injectable_timed_backoff_not_select_readiness',
                        enobufs_video_budget='original_frame_deadline_never_renewed',
                        enobufs_priority_budget='original_10ms_send_call_budget_never_renewed',
                        priority_would_block_expiry_policy='drop_count_not_sent',
                        video_would_block_expiry_policy='PacingDeadline_existing_reference_guard',
                        retry_seals_fresh_nonce=True, send_mutex_held_across_wait=False,
                        owned_socket_close_attempted=self.socket_close_attempted,
                        owned_socket_close_confirmed=self.socket_close_confirmed)

    def timing(self, lane):
        with self.lock:
            return self.last_timing.get(lane)

    def snapshot(self):
        with self.lock:
            return {lane: dict(values) for lane, values in self.counts.items()}

    def close(self):
        with self.lock:
            self.key = None
            if self.owns_socket and not self.socket_close_attempted:
                self.socket_close_attempted = True
                self.socket.close()
                self.socket_close_confirmed = True


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
