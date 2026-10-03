import copy
import unittest

from scripts.probes import owner_source_stats_gate as m
from tests.test_source_stats_observation import values
from tests.test_source_decoder_observation import FakeReader

IDENTITY = {'pid': 5212, 'uid': 10235, 'start_ticks': 453401}


def observation():
    reader = FakeReader(values())
    return m.source.collect('inert', 'emulator-5556', 'aqz-KE-bpKQ', reader_factory=lambda *_: reader)


class FreshStatsGateTests(unittest.TestCase):
    def gate(self, value=None, clocks=(100_000_000_000, 103_000_000_000), **kwargs):
        value = observation() if value is None else value
        clock = iter(clocks)
        return m.collect_fresh('inert', 'emulator-5556', IDENTITY, 'aqz-KE-bpKQ',
                               required_state='paused', collector=lambda *a, **k: value,
                               native_clock=lambda: next(clock), **kwargs)

    def test_independent_clocks_and_descriptor_not_rendered_fps(self):
        gate = self.gate()
        self.assertEqual(gate['native_clock_domain'], m.CLOCK)
        self.assertEqual(gate['native_finished_ns'], 103_000_000_000)
        self.assertEqual(gate['video_format']['descriptor_fps'], 60)
        self.assertTrue(gate['process_session_focus_bracket_verified'])
        self.assertFalse(gate['continuous_content_coverage_verified'])
        self.assertIsNone(gate['decoded_or_presented_fps'])
        self.assertIsNone(gate['visual_motion_observed'])
        self.assertFalse(gate['collection_or_overlay_overhead_measured'])

    def test_callback_failure_is_a_closed_error(self):
        with self.assertRaisesRegex(m.Rejected, '^collector_failed$'):
            m.collect_fresh('inert', 'emulator-5556', IDENTITY, 'aqz-KE-bpKQ', required_state='paused',
                           collector=lambda *a, **k: (_ for _ in ()).throw(RuntimeError('private')))

    def test_unqualified_or_unreaped_or_temp_cleanup_unknown_rejected(self):
        for flag in ('qualified', 'identity_stable', 'all_local_children_reaped',
                     'remote_uia_completion_receipt', 'device_UI_temp_removal_confirmed'):
            value = observation(); value[flag] = False
            with self.assertRaises(m.Rejected): self.gate(value)

    def test_bracket_cannot_disagree_with_claimed_qualified(self):
        for field in ('identity_before', 'identity_after'):
            value = observation(); value[field]['start_ticks'] += 1
            with self.assertRaises(m.Rejected): self.gate(value)
        for field in ('focus_before', 'focus_after'):
            value = observation(); value[field]['foreground'] = False
            with self.assertRaises(m.Rejected): self.gate(value)
        value = observation(); value['playback_after']['state'] = 3
        with self.assertRaises(m.Rejected): self.gate(value)

    def test_different_descriptor_or_arbitrary_additional_text_rejected(self):
        edits = (
            lambda v: v['stats'].update(video_id='aaaaaaaaaaa'),
            lambda v: v['stats']['video_format'].update(private='opaque'),
            lambda v: v['stats']['video_format'].update(descriptor_fps=float('nan')),
            lambda v: v['stats']['video_format'].update(codec='foreign'),
            lambda v: v['stats']['audio_format'].update(itag=True),
            lambda v: v['stats'].update(position_ui_seconds='private'),
            lambda v: v['stats'].update(observed_content_fps=60),
        )
        for edit in edits:
            value = observation(); edit(value)
            with self.assertRaises(m.Rejected): self.gate(value)

    def test_native_and_python_elapsed_each_have_own_budget(self):
        for clocks in ((100_000_000_000, 99_000_000_000), (100_000_000_000, 116_000_000_000)):
            with self.assertRaises(m.Rejected): self.gate(clocks=clocks)
        value = observation(); value['host_finished_python_monotonic_ns'] += 16_000_000_000
        with self.assertRaises(m.Rejected): self.gate(value)

    def test_listener_must_precede_collection_and_playing_requires_it(self):
        with self.assertRaises(m.Rejected): self.gate(listener_ready_ns=101_000_000_000)
        with self.assertRaisesRegex(m.Rejected, 'listener_required'):
            m.collect_fresh('inert', 'emulator-5556', IDENTITY, 'aqz-KE-bpKQ', required_state='playing',
                           collector=lambda *a, **k: self.fail('must not collect'))

    def test_freshness_uses_only_native_clock(self):
        gate = self.gate()
        self.assertEqual(m.require_fresh(gate, gate['native_finished_ns']+30_000_000_000), 30_000_000_000)
        for now in (gate['native_finished_ns']-1, gate['native_finished_ns']+30_000_000_001):
            with self.assertRaises(m.Rejected): m.require_fresh(gate, now)
        gate['native_clock_domain'] = 'python_time_monotonic_ns'
        with self.assertRaises(m.Rejected): m.require_fresh(gate, 103_000_000_000)

    def test_same_endpoint_is_not_continuous_or_motion_proof(self):
        before = self.gate(); after = copy.deepcopy(before)
        after.update(native_started_ns=145_000_000_000, native_finished_ns=148_000_000_000,
                     position_after_ms=408000)
        result = m.endpoint_match(before, after, window_started_ns=105_000_000_000,
                                  window_finished_ns=135_000_000_000)
        self.assertTrue(result['endpoint_content_matches'])
        self.assertEqual(result['reported_position_progress_ms'], 31000)
        self.assertFalse(result['continuous_content_coverage_verified'])
        self.assertIsNone(result['decoded_or_presented_fps'])
        after['video_id'] = 'aaaaaaaaaaa'
        self.assertFalse(m.endpoint_match(before, after, window_started_ns=105_000_000_000,
                                          window_finished_ns=135_000_000_000)['endpoint_content_matches'])

    def test_post_endpoint_before_or_too_far_after_window_rejected(self):
        before = self.gate(); after = copy.deepcopy(before)
        for start in (134_999_999_999, 165_000_000_001):
            after['native_started_ns'] = start
            with self.assertRaises(m.Rejected):
                m.endpoint_match(before, after, window_started_ns=105_000_000_000,
                                 window_finished_ns=135_000_000_000)

    def test_post_timestamp_contradiction_and_overclaim_rejected(self):
        before = self.gate(); after = copy.deepcopy(before)
        after.update(native_started_ns=145_000_000_000, native_finished_ns=148_000_000_000)
        for label, value in (('native_finished_ns', 144_000_000_000),
                             ('continuous_content_coverage_verified', True),
                             ('decoded_or_presented_fps', 60)):
            changed = copy.deepcopy(after); changed[label] = value
            with self.assertRaises(m.Rejected):
                m.endpoint_match(before, changed, window_started_ns=105_000_000_000,
                                 window_finished_ns=135_000_000_000)

    def test_invalid_expected_identity_fails_before_collector(self):
        for value in ({**IDENTITY, 'pid': True}, {**IDENTITY, 'private': 'text'}, {**IDENTITY, 'uid': -1}):
            with self.assertRaises(m.Rejected):
                m.collect_fresh('inert', 'emulator-5556', value, 'aqz-KE-bpKQ', required_state='paused',
                               collector=lambda *a, **k: self.fail('must not collect'))


if __name__ == '__main__':
    unittest.main()
