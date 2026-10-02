import importlib.util
import pathlib
import queue
import struct
import sys
import threading
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import udp_touch_control as touch


def decode_frame(frame):
    return struct.unpack('>BBQiiHHHII', frame)


class TouchStateTest(unittest.TestCase):
    def setUp(self):
        self.state = touch.TouchState(720, 1280, cancel_capable=True)

    def event(self, action, sequence, points=(), changed=0, rotation=0, now=10):
        # Clocks have unrelated offsets, intentionally.
        return self.state.consume(touch.encode(action, sequence, 1_000_000 + int((now - 10) * 1e6),
                                               points, rotation, changed), now)

    def test_multifinger_preserves_tracking_pressure_and_touchscreen_buttons(self):
        ack, frames = self.event(touch.DOWN, 1, [(8, 32768, 16384, 30000)], changed=8)
        self.assertEqual(touch.parse_ack(ack), (1, touch.APPLIED))
        self.assertEqual(decode_frame(frames[0]), (2, 0, 8, 360, 320, 720, 1280, 30000, 0, 0))
        _, frames = self.event(touch.DOWN, 2, [(8, 32768, 16384, 30000), (9, 65535, 65535, 65535)], changed=9)
        self.assertEqual(decode_frame(frames[0]), (2, 0, 9, 719, 1279, 720, 1280, 65535, 0, 0))
        _, frames = self.event(touch.MOVE, 3, [(8, 0, 0, 123), (9, 0, 65535, 444)])
        self.assertEqual([decode_frame(frame)[1:3] for frame in frames], [(2, 8), (2, 9)])
        _, frames = self.event(touch.UP, 4, [(9, 0, 65535, 444)], changed=8)
        self.assertEqual(decode_frame(frames[0])[1:3], (1, 8))
        self.assertEqual(decode_frame(frames[0])[7:], (0, 0, 0))
        self.assertEqual(self.state.stats()['active_pointers'], 1)

    def test_missing_down_is_repaired_by_complete_move_then_late_down_is_acked_without_reinjection(self):
        down = touch.encode(touch.DOWN, 1, 1_000_000, [(71, 100, 200, 123)], changed=71)
        _, frames = self.event(touch.MOVE, 2, [(71, 200, 300, 123)])
        self.assertEqual(decode_frame(frames[0])[1:3], (0, 71))
        ack, frames = self.state.consume(down, 10.01)
        self.assertEqual(touch.parse_ack(ack), (1, touch.SUPERSEDED))
        self.assertEqual(frames, [])

    def test_lost_up_empty_hold_releases_and_stale_move_cannot_resurrect(self):
        self.event(touch.DOWN, 1, [(81, 100, 100, 999)], changed=81)
        old_move = touch.encode(touch.MOVE, 2, 1_000_000, [(81, 100, 100, 999)])
        _, frames = self.event(touch.HOLD, 4, [])
        self.assertEqual(decode_frame(frames[0])[1:3], (3, 81))
        self.assertEqual(self.state.consume(old_move, 10.01), (None, []))
        self.assertEqual(self.state.stats()['active_pointers'], 0)

    def test_reused_motion_event_id_requires_new_tracking_token_and_releases_old_first(self):
        self.event(touch.DOWN, 1, [(100, 100, 100, 99)], changed=100)
        _, frames = self.event(touch.DOWN, 3, [(101, 200, 200, 99)], changed=101)
        self.assertEqual([decode_frame(frame)[1:3] for frame in frames], [(3, 100), (0, 101)])

    def test_duplicate_critical_reack_does_not_extend_lease(self):
        down = touch.encode(touch.DOWN, 1, 1_000_000, [(1, 100, 200, 99)], changed=1)
        self.state.consume(down, 10)
        ack, frames = self.state.consume(down, 10.6)
        self.assertEqual(touch.parse_ack(ack), (1, touch.SUPERSEDED))
        self.assertEqual(frames, [])
        self.assertEqual(len(self.state.tick(10.76)), 1)
        self.assertEqual(self.state.tick(11), [])
        self.assertEqual(self.state.stats()['lease_releases'], 1)

    def test_stationary_hold_preserves_finger_then_disconnect_releases(self):
        points = [(1, 100, 200, 99)]
        self.event(touch.DOWN, 1, points, changed=1)
        self.event(touch.HOLD, 2, points, now=10.6)
        self.assertEqual(self.state.tick(10.9), [])
        self.assertEqual(len(self.state.tick(11.36)), 1)

    def test_cancel_releases_all_and_retry_does_not_reinject(self):
        self.event(touch.DOWN, 1, [(1, 100, 200, 99), (2, 300, 400, 99)], changed=2)
        ack, frames = self.event(touch.CANCEL, 2)
        self.assertEqual(touch.parse_ack(ack), (2, touch.APPLIED))
        self.assertEqual(len(frames), 1)
        self.assertEqual(decode_frame(frames[0])[1], 3)
        self.assertEqual(self.state.stats()['cancelled_pointers'], 2)
        self.assertEqual(self.state.stats()['injected_up'], 0)
        ack, frames = self.event(touch.CANCEL, 2)
        self.assertEqual(touch.parse_ack(ack), (2, touch.SUPERSEDED))
        self.assertEqual(frames, [])

    def test_excess_arrival_delay_discards_old_move_but_up_can_still_release(self):
        self.event(touch.DOWN, 1, [(1, 100, 200, 99)], changed=1)
        late = touch.encode(touch.MOVE, 2, 1_000_010, [(1, 65535, 65535, 99)])
        self.assertEqual(self.state.consume(late, 10.2), (None, []))
        self.assertEqual(self.state.active[1][:2], (1, 3))
        up = touch.encode(touch.UP, 3, 1_000_020, [], changed=1)
        _, frames = self.state.consume(up, 10.3)
        self.assertEqual(decode_frame(frames[0])[1:3], (1, 1))

    def test_all_rotations_and_edges_clamp_inside_guest(self):
        expected = [(0, 0), (0, 1279), (719, 1279), (719, 0)]
        for rotation in range(4):
            state = touch.TouchState(720, 1280)
            _, frames = state.consume(touch.encode(touch.DOWN, 1, 1, [(1, 0, 0, 99)], rotation, 1), 10)
            self.assertEqual(decode_frame(frames[0])[3:5], expected[rotation])

    def test_payload_bound_and_semantics_reject_malformed_input_before_state_mutation(self):
        good = touch.encode(touch.DOWN, 1, 1, [(1, 10, 20, 99)], changed=1)
        bad = [good[:-1], good + b'\0', b'\0' * len(good),
               touch.HEADER.pack(touch.MAGIC, 1, touch.CANCEL, 1, 0, 1, 1, 0) + touch.POINT.pack(1, 0, 0, 0),
               touch.HEADER.pack(touch.MAGIC, 1, touch.DOWN, 2, 0, 1, 1, 1) + 2*touch.POINT.pack(1, 0, 0, 0),
               touch.HEADER.pack(touch.MAGIC, 1, touch.UP, 1, 0, 1, 1, 1) + touch.POINT.pack(1, 0, 0, 0)]
        for payload in bad:
            with self.assertRaises(ValueError):
                self.state.consume(payload, 10)
        self.assertEqual(self.state.sequence, 0)
        self.assertEqual(self.state.stats()['active_pointers'], 0)
        ten = [(token, 1, 2, 3) for token in range(1, 11)]
        self.assertEqual(len(touch.encode(touch.DOWN, 1, 1, ten, changed=10)), 128)

    def test_every_corner_and_center_all_rotations_ten_contacts_are_touchscreen(self):
        coords = [(0, 0), (65535, 0), (65535, 65535), (0, 65535),
                  (32768, 32768), (0, 32768), (32768, 0),
                  (65535, 32768), (32768, 65535), (12345, 54321)]
        for width, height in ((720, 1280), (1080, 1920), (1, 1), (65535, 65535)):
            for rotation in range(4):
                with self.subTest(width=width, height=height, rotation=rotation):
                    state = touch.TouchState(width, height, cancel_capable=True)
                    points = [(index + 1, x, y, index * 7000) for index, (x, y) in enumerate(coords)]
                    _, frames = state.consume(touch.encode(touch.DOWN, 1, 1, points, rotation, 10), 10)
                    self.assertEqual(len(frames), 10)
                    for frame, point in zip(frames, points):
                        decoded = decode_frame(frame)
                        token, x, y, pressure = point
                        if rotation == 1:
                            x, y = y, 65535 - x
                        elif rotation == 2:
                            x, y = 65535 - x, 65535 - y
                        elif rotation == 3:
                            x, y = 65535 - y, x
                        expected = (min(width - 1, x * width // 65535),
                                    min(height - 1, y * height // 65535))
                        self.assertEqual(decoded[1:5], (0, token, *expected))
                        self.assertEqual(decoded[5:], (width, height, pressure, 0, 0))
                    _, cancel = state.consume(touch.encode(touch.CANCEL, 2, 2), 10)
                    self.assertEqual(len(cancel), 1)
                    self.assertEqual(decode_frame(cancel[0])[1], 3)
                    self.assertEqual(state.stats()['active_pointers'], 0)
                    self.assertEqual(state.stats()['cancelled_pointers'], 10)

    def test_eleven_contacts_and_invalid_tokens_reject_without_mutation(self):
        eleven = [(token, 0, 0, 0) for token in range(1, 12)]
        cases = [touch.HEADER.pack(touch.MAGIC, 1, touch.DOWN, 11, 0, 1, 1, 11)
                 + b''.join(touch.POINT.pack(*point) for point in eleven),
                 touch.HEADER.pack(touch.MAGIC, 1, touch.DOWN, 1, 0, 1, 1, 0)
                 + touch.POINT.pack(0, 0, 0, 0)]
        for packet in cases:
            with self.assertRaises(ValueError):
                self.state.consume(packet, 10)
        self.assertEqual(self.state.sequence, 0)
        self.assertEqual(self.state.maximum_token, 0)
        self.assertEqual(self.state.active, {})

    def test_lost_cancel_is_repaired_before_new_down_not_as_tap_completing_up(self):
        self.event(touch.DOWN, 1, [(10, 100, 200, 99), (11, 300, 400, 99)], changed=11)
        lost_cancel = touch.encode(touch.CANCEL, 2, 1_000_001)
        _, frames = self.state.consume(touch.encode(touch.DOWN, 3, 1_000_002,
                                                   [(12, 500, 600, 99)], changed=12), 10)
        self.assertEqual([decode_frame(frame)[1:3] for frame in frames], [(3, 10), (0, 12)])
        self.assertEqual(self.state.stats()['injected_up'], 0)
        ack, frames = self.state.consume(lost_cancel, 10)
        self.assertEqual(touch.parse_ack(ack), (2, touch.SUPERSEDED))
        self.assertEqual(frames, [])
        self.assertEqual(list(self.state.active), [12])

    def test_reusing_lifted_or_cancelled_tracking_token_fails_even_with_fresh_sequence(self):
        self.event(touch.DOWN, 1, [(1, 100, 200, 99), (2, 300, 400, 99)], changed=2)
        self.event(touch.UP, 2, [(1, 100, 200, 99)], changed=2)
        with self.assertRaisesRegex(ValueError, 'retired touch tracking token'):
            self.event(touch.DOWN, 3, [(1, 100, 200, 99), (2, 500, 600, 99)], changed=2)
        self.assertEqual(self.state.sequence, 2)
        self.assertEqual(list(self.state.active), [1])
        self.event(touch.CANCEL, 3)
        with self.assertRaisesRegex(ValueError, 'retired touch tracking token'):
            self.event(touch.MOVE, 4, [(1, 100, 200, 99)])
        self.assertEqual(self.state.active, {})
        _, frames = self.event(touch.MOVE, 4, [(3, 100, 200, 99)])
        self.assertEqual(decode_frame(frames[0])[1:3], (0, 3))
        self.assertEqual(self.state.stats()['rejected_tracking'], 2)

    def test_expired_stationary_contact_cannot_be_resurrected_by_later_hold(self):
        self.event(touch.DOWN, 1, [(1, 100, 200, 99)], changed=1)
        frames = self.state.tick(10.8)
        self.assertEqual([decode_frame(frame)[1] for frame in frames], [3])
        with self.assertRaisesRegex(ValueError, 'retired touch tracking token'):
            self.event(touch.HOLD, 2, [(1, 100, 200, 99)], now=10.9)
        self.assertEqual(self.state.stats()['active_pointers'], 0)
        self.assertEqual(self.state.stats()['injected_up'], 0)

    def test_close_abandons_all_ten_contacts_with_one_cancel(self):
        self.event(touch.DOWN, 1, [(i, i, i, 99) for i in range(1, 11)], changed=10)
        frames = self.state.release_all()
        self.assertEqual(len(frames), 1)
        self.assertEqual(decode_frame(frames[0])[1], 3)
        self.assertEqual(self.state.release_all(), [])
        self.assertEqual(self.state.stats()['injected_up'], 0)

    def test_stock_guest_cannot_be_advertised_as_cancel_capable(self):
        legacy = touch.TouchState(720, 1280)
        legacy.consume(touch.encode(touch.DOWN, 1, 1, [(1, 0, 0, 9)], changed=1), 10)
        with self.assertRaisesRegex(ValueError, 'touch_cancel_clears_pointers_v1'):
            legacy.consume(touch.encode(touch.CANCEL, 2, 2), 10)
        self.assertEqual(legacy.sequence, 1)
        self.assertEqual(list(legacy.active), [1])
        frames = legacy.release_all()
        self.assertEqual([decode_frame(frame)[1] for frame in frames], [1])
        self.assertEqual(legacy.stats()['legacy_releases'], 1)
        self.assertEqual(legacy.stats()['injected_cancel'], 0)

    def test_sender_time_backwards_does_not_mutate_applied_sequence(self):
        self.event(touch.DOWN, 1, [(1, 100, 100, 99)], changed=1)
        with self.assertRaises(ValueError):
            self.state.consume(touch.encode(touch.MOVE, 2, 999_999, [(1, 200, 200, 99)]), 10)
        self.assertEqual(self.state.sequence, 1)


class TouchBridgeTest(unittest.TestCase):
    def test_writer_receives_android_packets_before_ack_and_close_releases(self):
        writes, acks = queue.Queue(), queue.Queue()
        order = []
        def writer(data):
            order.append('write');writes.put(data)
        def ack(data):
            order.append('ack');acks.put(data)
        bridge = touch.UdpTouchBridge(720, 1280, writer, ack, cancel_capable=True)
        try:
            self.assertTrue(bridge.submit(touch.encode(touch.DOWN, 1, 1_000_000, [(7, 100, 200, 99)], changed=7)))
            self.assertEqual(decode_frame(writes.get(timeout=1))[1:3], (0, 7))
            self.assertEqual(touch.parse_ack(acks.get(timeout=1)), (1, touch.APPLIED))
            self.assertEqual(order[:2], ['write', 'ack'])
        finally:
            bridge.close()
        self.assertEqual(decode_frame(writes.get(timeout=1))[1:3], (3, 7))
        self.assertFalse(bridge.submit(touch.encode(touch.HOLD, 2, 1_000_001)))
        self.assertFalse(bridge.stats()['writer_alive'])
        self.assertEqual(bridge.stats()['active_pointers'], 0)

    def test_input_writer_backpressure_never_blocks_submit_and_latest_moves_coalesce(self):
        started, release = threading.Event(), threading.Event()
        def writer(data):
            started.set();release.wait(timeout=1)
        bridge = touch.UdpTouchBridge(720, 1280, writer, lambda data: None, cancel_capable=True)
        try:
            bridge.submit(touch.encode(touch.DOWN, 1, 1, [(1, 1, 2, 3)], changed=1))
            self.assertTrue(started.wait(timeout=1))
            for seq in range(2, 102):
                self.assertTrue(bridge.submit(touch.encode(touch.MOVE, seq, seq, [(1, seq, seq, 3)])))
            self.assertGreaterEqual(bridge.stats()['move_coalesced'], 99)
            self.assertEqual(bridge.queue.qsize(), 0)
        finally:
            release.set();bridge.close()

    def test_critical_retry_queue_is_bounded_and_cancel_retries_have_no_early_ack(self):
        started, release = threading.Event(), threading.Event()
        acks = []
        def writer(data):
            started.set()
            release.wait(timeout=1)
        bridge = touch.UdpTouchBridge(720, 1280, writer, acks.append, cancel_capable=True)
        try:
            self.assertTrue(bridge.submit(touch.encode(touch.DOWN, 1, 1, [(1, 1, 2, 3)], changed=1)))
            self.assertTrue(started.wait(timeout=1))
            self.assertEqual(acks, [])
            for seq in range(2, 66):
                self.assertTrue(bridge.submit(touch.encode(touch.CANCEL, seq, seq)))
            self.assertEqual(bridge.queue.qsize(), 64)
            self.assertFalse(bridge.submit(touch.encode(touch.CANCEL, 66, 66)))
            self.assertEqual(bridge.stats()['queue_drops'], 1)
            self.assertEqual(acks, [])
        finally:
            release.set()
            bridge.close()
        self.assertEqual(bridge.stats()['active_pointers'], 0)
        self.assertEqual(bridge.stats()['injected_cancel'], 1)
        self.assertEqual(bridge.stats()['injected_up'], 0)


if __name__ == '__main__':
    unittest.main()
