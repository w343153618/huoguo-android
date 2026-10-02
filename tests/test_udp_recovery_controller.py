"""Unit checks for experimental local encoder recovery, no device access."""
import pathlib
import struct
import sys
import unittest

DIRECTORY = pathlib.Path(__file__).resolve().parents[1] / 'experiments/moonlight-v2/transport/android-udp'
sys.path.insert(0, str(DIRECTORY))
from recovery_controller import RecoveryController


def oversized(size=200_000, wire=8_000_000):
    return {'event': 'encoder_budget_feedback', 'reason': 'idr_exceeds_assembly_budget',
            'action': 'smaller_idr_or_lower_encoder_bitrate_required',
            'wire_bitrate': wire, 'full_wire_bytes': size, 'budget_us': 80_000}


def request(attempt=1):
    return {'event': 'request_idr', 'reason': 'local_dependency_or_admission',
            'attempt': attempt, 'cooldown_us': 500_000}


class RecoveryControllerCheck(unittest.TestCase):
    def test_lower_target_precedes_idr_using_actual_host_command(self):
        c = RecoveryController(8_000_000, 8_000_000)
        decision = c.consume(oversized(), 0)
        self.assertEqual(decision.capacity_bytes, 80_000)
        self.assertEqual(decision.requested_bitrate_bps, 4_000_000)
        self.assertEqual(decision.command_bytes(), b'\xf0' + struct.pack('>I', 4_000_000) + b'\x11')
        self.assertEqual(c.report()['assembly_budget_ms'], 80)

    def test_duplicate_request_is_coalesced_without_suppressing_retry(self):
        c = RecoveryController(8_000_000, 8_000_000)
        self.assertIsNotNone(c.consume(oversized(), 10))
        self.assertIsNone(c.consume(request(), 10.001))
        self.assertIsNone(c.consume(oversized(), 10.49))
        self.assertEqual(c.consume(request(2), 10.5).command_bytes(), b'\xf0'+struct.pack('>I', 2_000_000)+b'\x11')

    def test_failed_idr_just_after_request_is_retained_for_next_retry(self):
        c = RecoveryController(8_000_000, 8_000_000)
        self.assertEqual(c.consume(request(), 0).command_bytes(), b'\x11')
        self.assertIsNone(c.consume(oversized(), .02))
        self.assertTrue(c.report()['pending_budget_feedback'])
        decision = c.consume(request(2), .5)
        self.assertEqual(decision.reason, 'pending_encoder_budget_feedback')
        self.assertEqual(decision.requested_bitrate_bps, 4_000_000)
        self.assertFalse(c.report()['pending_budget_feedback'])

    def test_minimum_and_six_requests_are_bounded(self):
        c = RecoveryController(8_000_000, 8_000_000)
        values = [c.consume(oversized(), t).requested_bitrate_bps for t in range(6)]
        self.assertEqual(values, [4_000_000, 2_000_000, 1_000_000, 1_000_000, 1_000_000, 1_000_000])
        self.assertIsNone(c.consume(oversized(), 6))
        self.assertTrue(c.exhausted)

    def test_wire_size_ratio_keeps_margin_without_more_than_half_drop(self):
        c = RecoveryController(8_000_000, 12_000_000)
        decision = c.consume(oversized(150_000, 12_000_000), 0)
        self.assertEqual(decision.capacity_bytes, 120_000)
        self.assertEqual(decision.requested_bitrate_bps, 5_400_000)

    def test_quantizing_does_not_reduce_more_than_half(self):
        c = RecoveryController(5_300_000, 8_000_000)
        decision = c.consume(oversized(1_000_000), 0)
        self.assertEqual(decision.requested_bitrate_bps, 2_700_000)
        self.assertGreaterEqual(decision.requested_bitrate_bps*2, 5_300_000)

    def test_recovery_resets_requests_but_preserves_lowered_target_and_cooldown(self):
        c = RecoveryController(8_000_000, 8_000_000)
        c.consume(oversized(), 0)
        self.assertIsNone(c.consume({'event': 'recovery_complete', 'frame': 2, 'attempts': 1}, .1))
        self.assertEqual(c.requests, 0)
        self.assertEqual(c.target_bps, 4_000_000)
        self.assertIsNone(c.consume(request(), .2))
        self.assertEqual(c.consume(oversized(), .5).requested_bitrate_bps, 2_000_000)

    def test_invalid_events_cannot_update_bitrate(self):
        mutations = [('budget_us', 120_000), ('budget_us', True), ('wire_bitrate', 40_000_000),
                     ('full_wire_bytes', 0), ('full_wire_bytes', float('nan')),
                     ('full_wire_bytes', 2_000_001), ('reason', 'other')]
        c = RecoveryController(8_000_000, 8_000_000)
        for key, value in mutations:
            event = oversized(); event[key] = value
            self.assertIsNone(c.consume(event, 0))
        self.assertEqual(c.target_bps, 8_000_000)
        self.assertEqual(c.invalid_events, len(mutations))
        self.assertIsNone(c.consume({'event': 'request_idr', 'reason': 'local_dependency_or_admission',
                                     'attempt': True, 'cooldown_us': 500_000}, 0))

    def test_clock_regression_and_nan_fail_closed(self):
        c = RecoveryController(8_000_000, 8_000_000)
        c.consume(request(), 10)
        self.assertIsNone(c.consume(oversized(), 9))
        self.assertIsNone(c.consume(oversized(), float('nan')))
        self.assertEqual(c.target_bps, 8_000_000)

    def test_actual_encoder_ack_does_not_raise_desired_target(self):
        c = RecoveryController(8_000_000, 8_000_000)
        c.consume(oversized(), 0)
        self.assertTrue(c.acknowledge(4_000_000))
        self.assertTrue(c.acknowledge(8_000_000))
        self.assertEqual(c.target_bps, 4_000_000)
        self.assertEqual(c.accepted_bitrate_bps, 8_000_000)
        self.assertFalse(c.acknowledge(0))
        with self.assertRaises(ValueError):
            c.acknowledge(True)

    def test_floor_minimum_does_not_trigger_negative_or_zero_target(self):
        c = RecoveryController(1_000_000, 8_000_000)
        self.assertEqual(c.consume(oversized(), 0).command_bytes(), b'\x11')

    def test_invalid_constructor_arguments(self):
        for target, wire in [(True, 8_000_000), (999_999, 8_000_000), (8_000_000, 0)]:
            with self.assertRaises(ValueError):
                RecoveryController(target, wire)
        for options in ({'cooldown_s': .05}, {'cooldown_s': float('nan')}, {'max_requests': 7}):
            with self.assertRaises(ValueError):
                RecoveryController(8_000_000, 8_000_000, **options)

    def test_fast_recovery_retains_failed_idr_budget_and_requires_matching_native_interval(self):
        c = RecoveryController(8_000_000, 8_000_000, cooldown_s=.1)
        event = request(); event['cooldown_us'] = 100_000
        self.assertEqual(c.consume(event, 0).command_bytes(), b'\x11')
        self.assertIsNone(c.consume(oversized(), .02))
        self.assertIsNone(c.consume(event, .099))
        self.assertEqual(c.consume(event, .1).requested_bitrate_bps, 4_000_000)
        self.assertIsNone(c.consume(request(), .3))
        self.assertEqual(c.invalid_events, 1)
        self.assertEqual(c.report()['recovery_cooldown_ms'], 100)
        self.assertEqual(c.report()['maximum_requests_per_loss_epoch'], 6)

    def test_fast_recovery_cannot_exceed_six_requests_without_complete_idr(self):
        c = RecoveryController(8_000_000, 8_000_000, cooldown_s=.1)
        for tick in range(6):
            self.assertIsNotNone(c.consume(oversized(), tick * .11))
        self.assertIsNone(c.consume(oversized(), 1))
        self.assertTrue(c.exhausted)


if __name__ == '__main__':
    unittest.main()
