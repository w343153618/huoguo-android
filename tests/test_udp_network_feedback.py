"""Pure feedback-policy checks. No network, accounts or device access."""
from pathlib import Path
import struct
import sys
import unittest

DIRECTORY = Path(__file__).resolve().parents[1]/'experiments/moonlight-v2/transport/android-udp'
sys.path.insert(0, str(DIRECTORY))
from feedback_controller import NetworkFeedbackController, FEEDBACK, PING, PONG, parse_feedback


def sample(index, *, wire=None, expired=0, reference_lost=0, over80ms=0):
    return FEEDBACK.pack(b'HGUF', 1, index, (index-1)*100_000, index*100_000,
                         index*60, index*60_000 if wire is None else wire,
                         index*6, expired, reference_lost, index, over80ms, 100, 100_000, index*6)


class FeedbackChecks(unittest.TestCase):
    def controller(self):
        return NetworkFeedbackController(4_000_000, 40_000_000, clock=lambda: 0)

    def echo(self, controller, sent_at, rtt_ms):
        query=controller.ping(sent_at)
        self.assertIsNotNone(query)
        _, identifier, stamp=PING.unpack(query)
        self.assertTrue(controller.pong(PONG.pack(b'HGPR',identifier,stamp,9_000_000_000,9_000_000_100),
                                        sent_at+rtt_ms/1000))

    def test_schema_roundtrip_and_exact_bounds(self):
        self.assertEqual(FEEDBACK.size, 112)
        self.assertEqual(parse_feedback(sample(1))['wire_bytes'], 60_000)
        zero=list(FEEDBACK.unpack(sample(1))); zero[2]=0
        for value in (sample(1)+b'x', sample(1)[:-1], FEEDBACK.pack(*zero), sample(1).replace(b'HGUF', b'BAD!')):
            with self.assertRaises((ValueError, struct.error)): parse_feedback(value)

    def test_feedback_absence_never_raises_or_floods_encoder(self):
        c=self.controller()
        c.requested(3_000_000, 0)
        c.acknowledge(3_000_000)
        c.tick(50)
        self.assertEqual(c.target_bps, 3_000_000)
        self.assertTrue(c.report(50)['feedback_stale'])
        self.assertIsNone(c.pending)

    def test_duplicate_disorder_reset_and_clock_regression_ignored(self):
        c=self.controller()
        c.consume(sample(2), host_video_wire_bytes=120_000, now=.2)
        c.consume(sample(1), host_video_wire_bytes=120_000, now=.3)
        c.consume(sample(2), host_video_wire_bytes=120_000, now=.4)
        c.consume(sample(3, wire=1), host_video_wire_bytes=180_000, now=.5)
        c.consume(sample(3), host_video_wire_bytes=180_000, now=.1)
        self.assertEqual(c.counters['old_feedback'], 2)
        self.assertEqual(c.counters['invalid_feedback'], 2)
        self.assertEqual(c.last['sequence'], 2)

    def test_flood_cannot_evaluate_window_or_move_target(self):
        c=self.controller()
        c.consume(sample(1), host_video_wire_bytes=60_000, now=.1)
        for n in range(2, 100):
            c.consume(sample(n), host_video_wire_bytes=n*60_000, now=.1001)
        self.assertEqual(c.counters['feedback_rate_limited'], 98)
        self.assertEqual(c.counters['evaluated_windows'], 0)
        self.assertEqual(c.target_bps, 4_000_000)

    def test_two_bad_windows_then_single_ack_gated_reduction(self):
        c=self.controller()
        actions=[]
        for n in range(1, 31):
            decision=c.consume(sample(n, expired=n), host_video_wire_bytes=n*60_000, now=n*.1)
            if decision:
                actions.append(decision)
                c.requested(decision.requested_bitrate_bps, n*.1)
        self.assertEqual(len(actions), 1) # No matching ACK; second proposal is blocked.
        self.assertEqual(actions[0].requested_bitrate_bps, 3_200_000)
        self.assertEqual(actions[0].command_bytes(), b'\xf0'+struct.pack('>I', 3_200_000))

    def test_stable_delivery_increase_bounded_by_original_and_recovery_cap(self):
        c=self.controller()
        c.requested(3_000_000, 0)
        c.acknowledge(3_000_000)
        actions=[]
        for n in range(1, 132):
            decision=c.consume(sample(n), host_video_wire_bytes=n*60_000, now=n*.1)
            if decision:
                actions.append(decision)
                c.requested(decision.requested_bitrate_bps, n*.1)
                c.acknowledge(decision.requested_bitrate_bps)
        self.assertEqual(len(actions), 2)
        self.assertLessEqual(actions[0].requested_bitrate_bps, 3_150_000)
        c.set_recovery_ceiling(3_100_000)
        self.assertEqual(c.target_bps, 3_100_000)
        with self.assertRaises(ValueError): c.requested(3_200_000, 14)

    def test_static_low_rate_and_nonacknowledged_target_do_not_probe_up(self):
        for ack in (True, False):
            c=self.controller()
            c.requested(3_000_000, 0)
            if ack: c.acknowledge(3_000_000)
            for n in range(1, 120):
                decision=c.consume(sample(n, wire=n*1000), host_video_wire_bytes=n*1000, now=n*.1)
                self.assertIsNone(decision)

    def test_counter_jump_and_short_interval_rejected(self):
        c=self.controller()
        c.consume(sample(1), host_video_wire_bytes=60_000, now=.1)
        self.assertIsNone(c.consume(sample(2, wire=10_000_000), host_video_wire_bytes=120_000, now=.2))
        fields=list(FEEDBACK.unpack(sample(2))); fields[4]=149_999
        with self.assertRaises(ValueError): parse_feedback(FEEDBACK.pack(*fields))
        self.assertEqual(c.last['sequence'], 1)

    def test_ping_echo_same_host_clock_not_phone_epoch(self):
        c=self.controller()
        query=c.ping(10)
        _, identifier, stamp=PING.unpack(query)
        self.assertIsNone(c.ping(10.1))
        self.assertTrue(c.pong(PONG.pack(b'HGPR', identifier, stamp, 9_000_000_000, 9_000_000_250), 10.030))
        self.assertAlmostEqual(c.rtt_ewma_ms, 30)
        self.assertAlmostEqual(c.last_rtt_processing_ms, .25)
        self.assertFalse(c.pong(PONG.pack(b'HGPR', identifier, stamp, 0, 1), 10.031))

    def test_spoofed_wrong_token_expired_and_negative_processing_echo(self):
        c=self.controller()
        _, identifier, stamp=PING.unpack(c.ping(10))
        for payload in (b'HGPR', PONG.pack(b'HGPR', identifier, stamp+1, 0, 1),
                        PONG.pack(b'HGPR', identifier, stamp, 2, 1)):
            self.assertFalse(c.pong(payload, 10.1))
        self.assertFalse(c.pong(PONG.pack(b'HGPR', identifier, stamp, 0, 1), 14))
        self.assertEqual(c.counters['pongs_accepted'], 0)

    def test_stale_ack_does_not_raise_and_timeout_requires_new_acceptance(self):
        c=self.controller()
        c.requested(3_000_000, 0)
        self.assertFalse(c.acknowledge(4_000_000))
        self.assertFalse(c.accepted_current)
        c.tick(2.1)
        self.assertEqual(c.counters['encoder_ack_timeout'], 1)
        self.assertFalse(c.acknowledge(3_000_000))
        self.assertEqual(c.target_bps, 3_000_000)
        c.requested(2_400_000, 3)
        self.assertTrue(c.acknowledge(2_400_000))
        self.assertFalse(c.acknowledge(0))
        self.assertFalse(c.accepted_current)

    def test_small_delivery_ratio_alone_is_not_declared_network_loss(self):
        c=self.controller()
        for n in range(1, 100):
            self.assertIsNone(c.consume(sample(n, wire=n*20_000), host_video_wire_bytes=n*60_000, now=n*.1))
        self.assertEqual(c.target_bps, 4_000_000)

    def test_one_startup_high_then_three_recent_low_does_not_reduce_on_stale_ewma(self):
        c=self.controller()
        self.echo(c,0,80)
        for sent in (1,2,3): self.echo(c,sent,5)
        report=c.report(3.1)
        self.assertEqual(report['rtt_recent_pong_count'],3)
        self.assertTrue(report['rtt_warmup_ready'])
        self.assertAlmostEqual(report['host_clock_rtt_latest_ms'],5)
        self.assertAlmostEqual(report['host_clock_rtt_recent_median_ms'],5)
        self.assertGreater(report['host_clock_rtt_ewma_ms'],report['rtt_pressure_threshold_ms'])
        self.assertFalse(report['rtt_pressure'])
        for n in range(1,15):
            self.assertIsNone(c.consume(sample(n),host_video_wire_bytes=n*60_000,now=3+n*.1))
        self.assertEqual(c.counters['downward_updates'],0)

    def test_two_startup_high_then_latest_low_rejects_even_a_temporarily_high_median(self):
        c=self.controller()
        self.echo(c,0,80); self.echo(c,1,80); self.echo(c,2,5)
        report=c.report(2.1)
        self.assertTrue(report['rtt_warmup_ready'])
        self.assertGreater(report['host_clock_rtt_recent_median_ms'],report['rtt_pressure_threshold_ms'])
        self.assertGreater(report['host_clock_rtt_ewma_ms'],report['rtt_pressure_threshold_ms'])
        self.assertFalse(report['rtt_pressure'])
        for n in range(1,11):
            self.assertIsNone(c.consume(sample(n),host_video_wire_bytes=n*60_000,now=2+n*.1))
        self.assertEqual(c.counters['downward_updates'],0)

    def test_fewer_than_three_recent_echoes_never_qualify_rtt_pressure(self):
        c=self.controller()
        self.echo(c,0,5); self.echo(c,1,200)
        report=c.report(1.3)
        self.assertEqual(report['rtt_recent_pong_count'],2)
        self.assertFalse(report['rtt_warmup_ready'])
        self.assertGreater(report['host_clock_rtt_recent_median_ms'],report['rtt_pressure_threshold_ms'])
        self.assertFalse(report['rtt_pressure'])

    def test_sustained_recent_high_rtt_still_reduces_after_two_pressure_windows(self):
        c=self.controller()
        self.echo(c,0,5)
        for sent in (1,2,3): self.echo(c,sent,60)
        report=c.report(3.1)
        self.assertEqual(report['rtt_recent_pong_count'],3)
        self.assertTrue(report['rtt_pressure'])
        for n in range(1,11):
            self.assertIsNone(c.consume(sample(n),host_video_wire_bytes=n*60_000,now=3+n*.1))
        self.echo(c,4,60)
        decision=c.consume(sample(11),host_video_wire_bytes=660_000,now=4.1)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.requested_bitrate_bps,3_200_000)
        self.assertEqual(c.counters['downward_updates'],1)
        c.requested(decision.requested_bitrate_bps,4.1)
        self.assertTrue(c.acknowledge(3_200_000))
        report=c.report(7.2)
        self.assertEqual(report['rtt_recent_pong_count'],0)
        self.assertFalse(report['rtt_pressure'])

    def test_cold_rtt_warmup_does_not_delay_local_receive_pressure_policy(self):
        c=self.controller(); self.echo(c,0,80)
        decisions=[]
        for n in range(1,13):
            d=c.consume(sample(n),host_video_wire_bytes=n*60_000,local_pressure=True,now=n*.1)
            if d:
                decisions.append(d); c.requested(d.requested_bitrate_bps,n*.1)
        self.assertEqual(len(decisions),1)
        self.assertEqual(decisions[0].requested_bitrate_bps,3_200_000)
        self.assertTrue(any(x['local_socket_pressure'] for x in c.samples))


if __name__ == '__main__': unittest.main()
