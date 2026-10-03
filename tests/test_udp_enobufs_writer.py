"""Explicit ENOBUFS candidate fixtures; no real congestion, phone or WAN claim."""
import errno
from pathlib import Path
import struct
import sys
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'experiments/moonlight-v2/transport/android-udp'))
from udp_session_sender import AuthenticatedSender, PacingDeadline, SocketPacer, SocketVideoGate
from udp_probe_protocol import HEADER, ReplayWindow, SERVER_NONCE, open_packet

KEY = bytes(range(32))  # Public synthetic fixture only.


class Clock:
    def __init__(self):
        self.now = 10_000_000_000
        self.backoffs = []
        self.writable_waits = []

    def ns(self):
        return self.now

    def backoff(self, seconds):
        self.backoffs.append(round(seconds * 1e9))
        self.now += round(seconds * 1e9)

    def writable(self, sock, seconds):
        self.writable_waits.append(round(seconds * 1e9))
        self.now += round(seconds * 1e9)
        return False


class Writer:
    def __init__(self, failures=()):
        self.failures = list(failures)
        self.attempts = []
        self.sent = []
        self.closed = 0

    def gettimeout(self):
        return 0

    def send(self, packet):
        self.attempts.append(packet)
        if self.failures:
            error = self.failures.pop(0)
            if error is not None:
                raise error
        self.sent.append(packet)
        return len(packet)

    def close(self):
        self.closed += 1


def no_buffers():
    # Use platform errno, not a Linux-specific constant. Actual Darwin errno55
    # belongs to the independently recorded phone failure, not this fixture.
    return OSError(errno.ENOBUFS, 'synthetic interface output pressure')


def would_block():
    return BlockingIOError(errno.EWOULDBLOCK, 'synthetic readiness pressure')


def candidate(writer, clock, **kwargs):
    return AuthenticatedSender(writer, KEY, 17, send_policy='owned_nonblocking_deadline',
        clock_ns=clock.ns, wait_writable=clock.writable,
        enobufs_retry_enabled=True, backpressure_wait=clock.backoff, **kwargs)


def sequences(packets):
    return [HEADER.unpack(packet[:HEADER.size])[3] for packet in packets]


def shard(frame, capture, *, key=True, index=0):
    value = bytearray(66)
    value[:9] = b'HGUD\x01' + bytes((3 if key else 2, index, 1, 2))
    struct.pack_into('>HHH', value, 10, 0, 1, 10)
    struct.pack_into('>QQ', value, 16, frame, capture)
    struct.pack_into('>IIIIQ', value, 32, 10, 10, 80_000, 0x53494d31, frame - 1 if not key else 0)
    value[56:] = b'x' * 10
    return bytes(value)


class EnobufsPolicyChecks(unittest.TestCase):
    def test_default_off_keeps_original_fatal_error_and_no_retry(self):
        clock = Clock()
        original = no_buffers()
        writer = Writer([original])
        sender = AuthenticatedSender(writer, KEY, 17, send_policy='owned_nonblocking_deadline',
            clock_ns=clock.ns, wait_writable=clock.writable, backpressure_wait=clock.backoff)
        with self.assertRaises(OSError) as raised:
            sender.send(b'video', 'video', deadline_us=clock.ns() // 1000 + 80_000)
        self.assertIs(raised.exception, original)
        self.assertEqual(original.huoguo_udp_failure_operation, 'udp_socket_send')
        self.assertEqual(original.huoguo_udp_failure_lane, 'video')
        self.assertEqual(len(writer.attempts), 1)
        self.assertEqual(clock.backoffs + clock.writable_waits, [])
        value = sender.snapshot()['video']
        self.assertEqual((value['send_errors'], value['enobufs_calls'], value['enobufs_fatal_calls']), (1, 1, 1))
        self.assertEqual(value['enobufs_retry_syscalls'], 0)
        self.assertFalse(sender.policy_snapshot()['enobufs_retry_enabled'])

    def test_opt_in_requires_owned_policy_exact_bool_and_callable_backoff(self):
        for policy in ('legacy_socket_timeout', 'not_a_policy'):
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                AuthenticatedSender(Writer(), KEY, 17, send_policy=policy, enobufs_retry_enabled=True)
        for flag in (1, 0, None, 'true'):
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                AuthenticatedSender(Writer(), KEY, 17, send_policy='owned_nonblocking_deadline', enobufs_retry_enabled=flag)
        with self.assertRaises(ValueError):
            AuthenticatedSender(Writer(), KEY, 17, send_policy='owned_nonblocking_deadline',
                enobufs_retry_enabled=True, backpressure_wait=1)
        value = candidate(Writer(), Clock()).policy_snapshot()
        self.assertEqual(value['max_enobufs_calls'], 8)
        self.assertEqual(value['enobufs_backoff_slices_ns'], [1_000_000, 2_000_000, 4_000_000, 5_000_000])
        self.assertEqual(value['priority_budget_ns'], 10_000_000)
        self.assertFalse(value['send_mutex_held_across_wait'])

    def test_success_reseals_fresh_nonce_counts_real_recovery_and_never_selects(self):
        clock = Clock()
        writer = Writer([no_buffers(), None])
        sender = candidate(writer, clock)
        sender.wait_writable = lambda *args: self.fail('ENOBUFS is timed pressure, not select readiness')
        self.assertGreater(sender.send(b'video', 'video', deadline_us=clock.ns() // 1000 + 80_000), 0)
        self.assertEqual(sequences(writer.attempts), [1, 2])
        self.assertEqual(open_packet(KEY, 17, writer.sent[0], ReplayWindow(), SERVER_NONCE), b'video')
        self.assertNotEqual(writer.attempts[0], writer.attempts[1])
        self.assertEqual(clock.backoffs, [1_000_000])
        value = sender.snapshot()['video']
        self.assertEqual((value['datagrams'], value['send_errors'], value['enobufs_calls'],
                          value['enobufs_retry_syscalls'], value['enobufs_recovered_datagrams']), (1, 1, 1, 1, 1))
        self.assertEqual((value['would_block_calls'], value['would_block_retries'], value['retry_wait_ns']), (0, 0, 0))
        self.assertEqual(value['enobufs_backoff_wait_ns'], 1_000_000)

    def test_seven_errors_then_success_at_eighth_syscall(self):
        clock = Clock()
        writer = Writer([no_buffers() for _ in range(7)] + [None])
        sender = candidate(writer, clock)
        sender.send(b'video', 'video', deadline_us=clock.ns() // 1000 + 80_000)
        self.assertEqual(clock.backoffs, [1_000_000, 2_000_000, 4_000_000] + [5_000_000] * 4)
        self.assertEqual(sequences(writer.attempts), list(range(1, 9)))
        value = sender.snapshot()['video']
        self.assertEqual((value['enobufs_calls'], value['enobufs_retry_syscalls'], value['enobufs_recovered_datagrams']), (7, 7, 1))
        self.assertEqual(value['enobufs_backoff_wait_ns'], 27_000_000)
        self.assertEqual(value['max_enobufs_backoff_wait_ns'], 5_000_000)

    def test_eighth_enobufs_drops_inside_existing_reference_guard(self):
        clock = Clock()
        writer = Writer([no_buffers() for _ in range(8)])
        sender = candidate(writer, clock)
        recovery = []
        gate = SocketVideoGate(sender, recovery.append, clock_us=lambda: clock.ns() // 1000)
        self.assertEqual(gate.send(shard(1, clock.ns() // 1000)), 0)
        self.assertEqual(recovery, ['socket_frame_deadline'])
        self.assertTrue(gate.needs_idr)
        self.assertFalse(gate.current['media_data_complete'])
        self.assertEqual(len(writer.attempts), 8)
        self.assertEqual(len(clock.backoffs), 7, 'no wait follows the final failed syscall')
        value = sender.snapshot()['video']
        self.assertEqual((value['enobufs_calls'], value['enobufs_retry_syscalls'], value['enobufs_retry_bound_drops']), (8, 7, 1))
        self.assertEqual((value['enobufs_deadline_drops'], value['enobufs_fatal_calls'], value['datagrams']), (0, 0, 0))
        self.assertEqual(value['retry_bound_dropped_datagrams'], 1)
        self.assertGreater(gate.send(shard(2, clock.ns() // 1000)), 0)
        self.assertTrue(gate.current['media_data_complete'])
        self.assertFalse(gate.needs_idr)

    def test_original_video_deadline_clips_wait_and_does_not_renew(self):
        clock = Clock()
        writer = Writer([no_buffers() for _ in range(8)])
        sender = candidate(writer, clock)
        original = clock.ns() // 1000 + 2_500
        with self.assertRaises(PacingDeadline):
            sender.send(b'video', 'video', deadline_us=original)
        self.assertEqual(clock.backoffs, [1_000_000, 1_500_000])
        self.assertEqual(clock.ns(), original * 1000)
        self.assertEqual(len(writer.attempts), 2)
        value = sender.snapshot()['video']
        self.assertEqual((value['enobufs_deadline_drops'], value['enobufs_retry_bound_drops'], value['deadline_rejections']), (1, 0, 1))

    def test_priority_lanes_share_original_ten_ms_budget_and_drop_not_sent(self):
        for lane in ('audio', 'touch_ack', 'network_feedback'):
            with self.subTest(lane=lane):
                clock = Clock()
                writer = Writer([no_buffers() for _ in range(8)])
                sender = candidate(writer, clock)
                self.assertEqual(sender.send(b'priority', lane), 0)
                self.assertEqual(clock.backoffs, [1_000_000, 2_000_000, 4_000_000, 3_000_000])
                self.assertEqual(clock.ns(), 10_010_000_000)
                self.assertEqual(len(writer.attempts), 4)
                value = sender.snapshot()[lane]
                self.assertEqual((value['enobufs_calls'], value['enobufs_deadline_drops'], value['priority_budget_dropped_datagrams']), (4, 1, 1))
                self.assertEqual((value['datagrams'], value['encrypted_bytes'], value['enobufs_recovered_datagrams']), (0, 0, 0))

    def test_mixed_readiness_and_output_pressure_keep_metrics_and_waits_separate(self):
        for errors in ([no_buffers(), would_block(), None], [would_block(), no_buffers(), None]):
            with self.subTest(first=errors[0].errno):
                clock = Clock()
                writer = Writer(errors)
                sender = candidate(writer, clock)
                sender.send(b'video', 'video', deadline_us=clock.ns() // 1000 + 80_000)
                self.assertEqual(sequences(writer.attempts), [1, 2, 3])
                self.assertEqual(clock.backoffs, [1_000_000])
                self.assertEqual(clock.writable_waits, [5_000_000])
                value = sender.snapshot()['video']
                self.assertEqual((value['would_block_calls'], value['would_block_retries'], value['retry_wait_ns']), (1, 1, 5_000_000))
                self.assertEqual((value['enobufs_calls'], value['enobufs_retry_syscalls'], value['enobufs_backoff_wait_ns']), (1, 1, 1_000_000))
                self.assertEqual((value['datagrams'], value['enobufs_recovered_datagrams']), (1, 1))

    def test_backoff_does_not_hold_auth_or_pacer_locks_and_priority_can_progress(self):
        clock = Clock()
        writer = Writer([no_buffers(), None, None])
        pacer = SocketPacer(4_000_000, clock=lambda: clock.ns() / 1e9, wait=lambda seconds: clock.backoff(seconds))
        sender = candidate(writer, clock, pacer=pacer)
        def backoff(seconds):
            self.assertTrue(sender.lock.acquire(False), 'ENOBUFS wait must release auth/send lock')
            sender.lock.release()
            self.assertTrue(pacer.lock.acquire(False), 'ENOBUFS wait must release pacer lock')
            pacer.lock.release()
            sender.send(b'priority', 'audio')
            clock.backoff(seconds)
        sender.backpressure_wait = backoff
        sender.send(b'video', 'video', deadline_us=clock.ns() // 1000 + 80_000)
        self.assertEqual(sequences(writer.attempts), [1, 2, 3])
        replay = ReplayWindow()
        self.assertEqual([open_packet(KEY, 17, packet, replay, SERVER_NONCE) for packet in writer.sent], [b'priority', b'video'])
        self.assertGreater(pacer.snapshot()['priority_wire_bytes'], 0)

    def test_cancel_during_backoff_stops_without_extra_nonce_or_send_and_closes_once(self):
        clock = Clock()
        writer = Writer([no_buffers(), None])
        cancelled = threading.Event()
        sender = candidate(writer, clock, owns_socket=True, cancelled=cancelled.is_set)
        def backoff(seconds):
            cancelled.set()
            clock.backoff(seconds)
        sender.backpressure_wait = backoff
        with self.assertRaises(OSError) as raised:
            sender.send(b'video', 'video', deadline_us=clock.ns() // 1000 + 80_000)
        self.assertEqual(raised.exception.errno, errno.ECANCELED)
        self.assertEqual(raised.exception.huoguo_udp_failure_operation, 'udp_send_cancel')
        self.assertEqual((len(writer.attempts), sender.sequence), (1, 2))
        self.assertEqual(sender.snapshot()['video']['enobufs_recovered_datagrams'], 0)
        sender.close()
        sender.close()
        self.assertEqual(writer.closed, 1)
        self.assertTrue(sender.policy_snapshot()['owned_socket_close_confirmed'])

    def test_fatal_errors_after_pressure_keep_exact_exception_and_errno(self):
        errors = [OSError(code, 'synthetic hard error') for code in
                  (errno.ENETUNREACH, errno.EHOSTUNREACH, errno.EBADF, errno.EACCES, errno.EMSGSIZE, errno.EPERM)]
        errors += [TimeoutError('synthetic timeout'), BlockingIOError(errno.EPERM, 'not a readiness retry')]
        for original in errors:
            with self.subTest(errno=original.errno, kind=type(original).__name__):
                clock = Clock()
                writer = Writer([no_buffers(), original])
                sender = candidate(writer, clock)
                with self.assertRaises(type(original)) as raised:
                    sender.send(b'video', 'video', deadline_us=clock.ns() // 1000 + 80_000)
                self.assertIs(raised.exception, original)
                self.assertEqual(original.huoguo_udp_failure_operation, 'udp_socket_send')
                value = sender.snapshot()['video']
                self.assertEqual((value['send_errors'], value['enobufs_calls'], value['enobufs_retry_syscalls']), (2, 1, 1))
                self.assertEqual((value['datagrams'], value['enobufs_recovered_datagrams']), (0, 0))

    def test_backoff_errors_remain_fixed_wait_failure_and_are_not_swallowed(self):
        for original in (OSError(errno.EBADF, 'synthetic wait error'), ValueError('synthetic callback failure')):
            with self.subTest(kind=type(original).__name__):
                clock = Clock()
                writer = Writer([no_buffers()])
                sender = candidate(writer, clock)
                def failed_wait(seconds):
                    raise original
                sender.backpressure_wait = failed_wait
                with self.assertRaises(type(original)) as raised:
                    sender.send(b'video', 'video', deadline_us=clock.ns() // 1000 + 80_000)
                self.assertIs(raised.exception, original)
                self.assertEqual(original.huoguo_udp_failure_operation, 'udp_send_wait')
                self.assertEqual(sender.snapshot()['video']['enobufs_backoff_waits'], 1)
                self.assertEqual(len(writer.attempts), 1)

    def test_no_progress_backoff_is_finite_even_if_socket_select_is_writable(self):
        clock = Clock()
        writer = Writer([no_buffers() for _ in range(20)])
        sender = candidate(writer, clock)
        sender.backpressure_wait = lambda seconds: None
        sender.wait_writable = lambda *args: self.fail('never select an ENOBUFS retry')
        with self.assertRaises(PacingDeadline):
            sender.send(b'video', 'video', deadline_us=clock.ns() // 1000 + 80_000)
        self.assertEqual(len(writer.attempts), 8)
        self.assertEqual(sender.snapshot()['video']['enobufs_backoff_waits'], 7)

    def test_wait_overshoot_is_measured_then_original_deadline_prevents_retry(self):
        clock = Clock()
        writer = Writer([no_buffers(), None])
        sender = candidate(writer, clock)
        def slow_wait(seconds):
            clock.backoffs.append(round(seconds * 1e9))
            clock.now += 90_000_000
        sender.backpressure_wait = slow_wait
        with self.assertRaises(PacingDeadline):
            sender.send(b'video', 'video', deadline_us=clock.ns() // 1000 + 80_000)
        self.assertEqual(clock.backoffs, [1_000_000])
        self.assertEqual(len(writer.attempts), 1)
        value = sender.snapshot()['video']
        self.assertEqual(value['enobufs_backoff_wait_ns'], 90_000_000)
        self.assertEqual(value['max_total_send_call_ns'], 90_000_000)
        self.assertEqual(value['enobufs_deadline_drops'], 1)

    def test_shared_transient_cap_bounds_mixed_errors_without_enobufs_limit_reset(self):
        clock = Clock()
        writer = Writer([would_block() for _ in range(63)] + [no_buffers(), None])
        sender = candidate(writer, clock)
        sender.wait_writable = lambda sock, seconds: True
        sender.backpressure_wait = lambda seconds: None
        with self.assertRaises(PacingDeadline):
            sender.send(b'video', 'video', deadline_us=clock.ns() // 1000 + 80_000)
        self.assertEqual(len(writer.attempts), 64)
        value = sender.snapshot()['video']
        self.assertEqual((value['enobufs_calls'], value['enobufs_transient_bound_drops'], value['enobufs_retry_bound_drops']), (1, 1, 0))
        self.assertEqual(value['would_block_retries'], 63)

    def test_session_close_and_sequence_exhaustion_after_wait_preserve_state_failure(self):
        for operation in ('close', 'exhaust'):
            with self.subTest(operation=operation):
                clock = Clock()
                writer = Writer([no_buffers(), None])
                sender = candidate(writer, clock)
                def invalidate(seconds):
                    if operation == 'close':
                        sender.close()
                    else:
                        with sender.lock:
                            sender.sequence = 0x10000000000000000
                    clock.backoff(seconds)
                sender.backpressure_wait = invalidate
                with self.assertRaises(ValueError) as raised:
                    sender.send(b'video', 'video', deadline_us=clock.ns() // 1000 + 80_000)
                self.assertEqual(raised.exception.huoguo_udp_failure_operation, 'udp_sender_state')
                self.assertEqual(len(writer.attempts), 1)

    def test_data_complete_tail_parity_pressure_keeps_valid_reference(self):
        clock = Clock()
        writer = Writer([None] + [no_buffers() for _ in range(8)])
        sender = candidate(writer, clock)
        recovery = []
        gate = SocketVideoGate(sender, recovery.append, clock_us=lambda: clock.ns() // 1000)
        capture = clock.ns() // 1000
        self.assertGreater(gate.send(shard(1, capture, index=0)), 0)
        self.assertEqual(gate.send(shard(1, capture, index=1)), 0)
        self.assertEqual(gate.send(shard(1, capture, index=2)), 0)
        self.assertTrue(gate.current['media_data_complete'])
        self.assertFalse(gate.needs_idr)
        self.assertEqual(gate.last_reference, 1)
        self.assertEqual(recovery, [])
        self.assertEqual(gate.counts['tail_parity_deadline_frames'], 1)
        self.assertGreater(gate.send(shard(2, clock.ns() // 1000, key=False)), 0)
        self.assertFalse(gate.needs_idr)


if __name__ == '__main__':
    unittest.main()
