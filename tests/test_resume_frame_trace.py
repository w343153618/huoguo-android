import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.probes import analyze_graphics_frame_trace as graphics
from scripts.probes import analyze_resume_frame_trace as probe
from scripts.probes.measure_source_frame_fences import HOST_CLOCK, clock_mapping_sample


SECOND = 1_000_000_000
TRACE_BASE = 1_000_000_000_000
MONO_DELTA = 1_000
HOST_OFFSET = 99_000_000_000_000


def host(seconds):
    return TRACE_BASE + MONO_DELTA + HOST_OFFSET + round(seconds * SECOND)


def event(stage, seconds, frame, slice_id, track=5):
    return {'slice_id': slice_id, 'ts_ns': TRACE_BASE + round(seconds * SECOND),
            'dur_ns': 0, 'track_id': track, 'machine_id': 0,
            'buffer_id_low32': frame % 3 + 1, 'frame_number_low32': frame,
            'event_type': stage}


def add_frame(data, seconds, frame, latch=.040, present=.044):
    for stage, delay in [(2, 0), (5, latch), (8, present)]:
        if delay is not None:
            data['events'].append(event(stage, seconds + delay, frame, len(data['events']) + 1))


def source_graphics():
    coverage = {key: 0 for header, name in graphics.HEADERS.items() if name == 'coverage' for key in header}
    coverage.update(target_layer_name_count=1, target_machine_count=1)
    sections = {'trace_bounds': [{'trace_start_ns': TRACE_BASE, 'trace_end_ns': TRACE_BASE + 20 * SECOND}],
                'clock_relations': [{'clock_snapshot_id': index, 'trace_ts_ns': TRACE_BASE + index * 20 * SECOND,
                                     'monotonic_ns': TRACE_BASE + MONO_DELTA + index * 20 * SECOND,
                                     'boottime_ns': TRACE_BASE + index * 20 * SECOND} for index in range(2)],
                'coverage': [coverage], 'events': []}
    add_frame(sections, 3.5, 1)
    add_frame(sections, 6.04, 2, latch=.070, present=.074)
    for index in range(20):
        add_frame(sections, 7.1 + index / 60, index + 3)
    return graphics.summarize(sections, 100)


def clock_sample(seconds, extra_offset=0):
    guest_us = (TRACE_BASE + MONO_DELTA + round(seconds * SECOND)) // 1000
    center = guest_us * 1000 + HOST_OFFSET + extra_offset
    return {'available': True, **clock_mapping_sample(
        {'monotonic_us': guest_us, 'unix_us': 0, 'sample_span_us': 0},
        center - 2_000_000, center + 2_000_000)}


def source_fences():
    return {'schema': 1, 'host_clock': HOST_CLOCK, 'complete_fixed_generation_verified': True,
            'measurement_host_before_ns': host(0), 'measurement_host_after_ns': host(20),
            'guest_clock_queries': [clock_sample(0), clock_sample(20)]}


def action(index, seconds):
    pause = index % 2 == 0
    return {'action_index': index, 'action': 'pause' if pause else 'resume',
            'host_before_ns': host(seconds), 'host_after_ns': host(seconds + .010),
            'media_state_before': 3 if pause else 2, 'media_state_after': 2 if pause else 3,
            'media_state_host_before_ns': host(seconds + .011),
            'media_state_host_after_ns': host(seconds + .021)}


def source_actions():
    return {'schema': 1, 'host_clock': HOST_CLOCK,
            'measurement_host_before_ns': host(0), 'measurement_host_after_ns': host(20),
            'actions': [action(0, 4), action(1, 6)],
            'steady_windows': [{'window_index': 0, 'host_before_ns': host(7), 'host_after_ns': host(10)}]}


class ResumeFrameTraceTests(unittest.TestCase):
    def analyze(self, data=None, fences=None, actions=None):
        return probe.summarize(data or source_graphics(), fences or source_fences(), actions or source_actions())

    def test_confirmed_pause_and_first_compare_same_identity_trace_intervals(self):
        report = self.analyze()
        self.assertEqual(report['accepted_cycle_count'], 1)
        self.assertFalse(report['clock_model']['offset_is_exact'])
        self.assertTrue(report['clock_model']['constant_guest_host_offset_assumed_not_proven'])
        self.assertEqual(report['clock_model']['combined_range_width_ns'], 4_002_000)
        cycle = report['cycles'][0]
        self.assertEqual(cycle['pause']['tail_without_new_queue_lower_bound_ns_given_model'], 1_979_000_000)
        self.assertEqual(cycle['first']['first_queue']['frame_number_low32'], 2)
        self.assertEqual(cycle['first']['first_queue']['queue_to_latch_ns'], 70_000_000)
        self.assertEqual(cycle['first']['first_queue']['resume_command_to_phase_model_interval_ns']['sf_receipt_queue_ts_ns'],
                         [27_999_000, 42_001_000])
        compare = cycle['comparison']['trace_clock_intervals']['queue_to_latch_ns']
        self.assertEqual(compare['steady_frame_count'], 20)
        self.assertEqual(compare['first_minus_steady_median_ns'], 30_000_000)
        self.assertEqual(compare['first_empirical_percentile'], 100)
        overlap = report['steady_windows'][0]['later_queue_markers_strictly_between_same_identity_q_and_l']
        self.assertEqual(overlap['p50_count'], 2)
        self.assertEqual(overlap['max_count'], 2)
        self.assertFalse(overlap['exact_producer_buffer_depth_identified'])
        self.assertTrue(cycle['first']['first_sf_receipt_may_be_stale_or_prequeued'])
        self.assertFalse(cycle['first']['media_pts_association_performed'])

    def test_three_pause_resume_pairs_use_their_own_steady_windows(self):
        data, fences, actions = source_graphics(), source_fences(), source_actions()
        data['trace_bounds'][0]['trace_end_ns'] += 15 * SECOND
        for key in ('trace_ts_ns', 'monotonic_ns', 'boottime_ns'):
            data['clock_relations'][1][key] += 15 * SECOND
        fences['measurement_host_after_ns'] = host(35)
        fences['guest_clock_queries'][1] = clock_sample(35)
        actions['measurement_host_after_ns'] = host(35)
        for cycle, pause_time in [(1, 11), (2, 18)]:
            actions['actions'].extend([action(2 * cycle, pause_time), action(2 * cycle + 1, pause_time + 2)])
            actions['steady_windows'].append({'window_index': cycle, 'host_before_ns': host(pause_time + 3),
                                              'host_after_ns': host(pause_time + 5)})
            first_frame = 2 + cycle * 21
            add_frame(data, pause_time + 2.04, first_frame, latch=.06 + cycle * .01, present=.065 + cycle * .01)
            for index in range(20):
                add_frame(data, pause_time + 3.1 + index / 60, first_frame + index + 1,
                          latch=.025 + cycle * .01, present=.03 + cycle * .01)
        report = self.analyze(data, fences, actions)
        self.assertEqual(report['accepted_cycle_count'], 3)
        self.assertEqual([row['window_index'] for row in report['steady_windows']], [0, 1, 2])
        comparisons = [row['comparison']['trace_clock_intervals']['queue_to_latch_ns'] for row in report['cycles']]
        self.assertEqual([row['steady_median_ns'] for row in comparisons], [40_000_000, 35_000_000, 45_000_000])

    def test_queue_drain_reduces_silence_lower_bound_and_busy_tail_rejects(self):
        data = source_graphics()
        # Frame numbers preserve one producer epoch after the insertion.
        data['events'].append(event(2, 4.2, 1, 999, track=9))
        report = self.analyze(data)
        pause = report['cycles'][0]['pause']
        self.assertEqual(pause['possible_queue_marker_count_given_model'], 1)
        self.assertEqual(pause['tail_without_new_queue_lower_bound_ns_given_model'], 1_797_999_000)
        self.assertTrue(pause['tail_without_new_queue_requirement_met_given_model'])
        data['events'][-1]['ts_ns'] = TRACE_BASE + round(5.4 * SECOND)
        report = self.analyze(data)
        self.assertEqual(report['accepted_cycle_count'], 0)
        self.assertIn('pause_queue_silence_requirement_not_met', report['cycles'][0]['reject_codes'])

    def test_queue_boundary_ambiguity_rejects_first_instead_of_skipping_to_next(self):
        data = source_graphics()
        first = next(row for row in data['events'] if row['event_type'] == 2 and row['frame_number_low32'] == 2)
        first['ts_ns'] = TRACE_BASE + round(6.011 * SECOND)
        report = self.analyze(data)
        result = report['cycles'][0]['first']
        self.assertEqual(result['queue_markers_ambiguous_at_resume_boundary'], 1)
        self.assertIsNone(result['first_queue'])
        self.assertEqual(result['reject_codes'], ['resume_boundary_queue_ambiguous'])
        diagnostic = result['diagnostic_earliest_certain_post_command_queue']
        self.assertFalse(diagnostic['true_first_proven'])
        self.assertEqual(diagnostic['same_identity_chain']['frame_number_low32'], 3)
        self.assertEqual(diagnostic['same_identity_chain']['queue_to_latch_ns'], 40_000_000)
        self.assertEqual(diagnostic['status'], 'accepted_same_identity_diagnostic_given_model')
        self.assertEqual(report['accepted_cycle_count'], 0)

    def test_boundary_diagnostic_missing_phase_still_rejected_as_identity_chain(self):
        data = source_graphics()
        first = next(row for row in data['events'] if row['event_type'] == 2 and row['frame_number_low32'] == 2)
        first['ts_ns'] = TRACE_BASE + round(6.011 * SECOND)
        data['events'] = [row for row in data['events'] if not (row['event_type'] == 8 and row['frame_number_low32'] == 3)]
        result = self.analyze(data)['cycles'][0]['first']
        diagnostic = result['diagnostic_earliest_certain_post_command_queue']
        self.assertFalse(diagnostic['true_first_proven'])
        self.assertEqual(diagnostic['same_identity_chain']['frame_number_low32'], 3)
        self.assertIsNone(diagnostic['same_identity_chain']['present_fence_ts_ns'])
        self.assertEqual(diagnostic['status'], 'rejected')
        self.assertEqual(diagnostic['reject_codes'], ['first_queue_missing_latch_or_present_phase'])

    def test_first_missing_phase_is_censored_and_not_replaced_by_complete_later_frame(self):
        data = source_graphics()
        data['events'] = [row for row in data['events'] if not (row['event_type'] == 8 and row['frame_number_low32'] == 2)]
        report = self.analyze(data)
        result = report['cycles'][0]['first']
        self.assertEqual(result['first_queue']['frame_number_low32'], 2)
        self.assertIsNone(result['first_queue']['present_fence_ts_ns'])
        self.assertEqual(result['first_queue']['missing_phase_count'], 1)
        self.assertEqual(result['reject_codes'], ['first_queue_missing_latch_or_present_phase'])
        self.assertEqual(report['cycles'][0]['comparison']['status'], 'rejected')

    def test_first_duplicate_or_cross_track_collision_rejected(self):
        for track in (5, 6):
            with self.subTest(track=track):
                data = source_graphics()
                data['events'].append(event(5, 6.08, 2, 999, track=track))
                result = self.analyze(data)['cycles'][0]['first']
                self.assertEqual(result['first_queue']['frame_number_low32'], 2)
                self.assertEqual(result['reject_codes'], ['first_queue_identity_rejected'])

    def test_first_queues_at_equal_timestamp_do_not_choose_by_slice_id(self):
        data = source_graphics()
        # Identical frame numbers avoid introducing a reset; buffer+track differ.
        extra = event(2, 6.04, 2, 999, track=8)
        extra['buffer_id_low32'] = 12
        data['events'].append(extra)
        result = self.analyze(data)['cycles'][0]['first']
        self.assertEqual(result['reject_codes'], ['first_queue_timestamp_tie'])

    def test_frame_reset_rejects_whole_causal_window(self):
        data = source_graphics()
        data['events'].append(event(2, 12, 1, 999))
        report = self.analyze(data)
        self.assertEqual(report['status'], 'rejected_evidence')
        self.assertEqual(report['cycles'], [])
        self.assertIn('observed_queue_generation_reset', report['reject_codes'])

    def test_constant_offset_inconsistent_samples_not_averaged(self):
        fences = source_fences()
        fences['guest_clock_queries'][1] = clock_sample(20, extra_offset=SECOND)
        report = self.analyze(fences=fences)
        self.assertEqual(report['reject_codes'], ['no_common_constant_offset_model'])
        self.assertIsNone(report['clock_model'])

    def test_exchange_clock_epoch_reset_rejected(self):
        fences = source_fences()
        sample = clock_sample(0, extra_offset=20 * SECOND)
        fences['guest_clock_queries'][1] = sample
        fences['guest_clock_queries'][0] = clock_sample(1)
        report = self.analyze(fences=fences)
        self.assertEqual(report['reject_codes'], ['guest_monotonic_epoch_reset'])

    def test_snapshot_epoch_reset_rejected(self):
        data = source_graphics()
        data['clock_relations'][1]['monotonic_ns'] = data['clock_relations'][0]['monotonic_ns'] - 1
        report = self.analyze(data)
        self.assertEqual(report['reject_codes'], ['snapshot_monotonic_epoch_reset'])

    def test_clock_mapping_bounds_recomputed_not_trusted(self):
        fences = source_fences()
        fences['guest_clock_queries'][0]['guest_to_host_monotonic_offset_bounds_ns'][0] += 1
        with self.assertRaisesRegex(ValueError, 'clock_bounds_do_not_match_numeric_exchange'):
            self.analyze(fences=fences)

    def test_snapshot_bounds_do_not_extrapolate_to_action_or_steady_window(self):
        data = source_graphics()
        for key in ('trace_ts_ns', 'monotonic_ns', 'boottime_ns'):
            data['clock_relations'][0][key] += 5 * SECOND
        report = self.analyze(data)
        self.assertEqual(report['status'], 'rejected_evidence')
        self.assertIn('action_outside_supported_clock_coverage', report['reject_codes'])
        data = source_graphics()
        for key in ('trace_ts_ns', 'monotonic_ns', 'boottime_ns'):
            data['clock_relations'][1][key] -= 11 * SECOND
        report = self.analyze(data)
        self.assertIn('steady_window_outside_supported_clock_coverage', report['reject_codes'])

    def test_unknown_media_state_does_not_prove_pause_or_fresh_frame(self):
        actions = source_actions()
        actions['actions'][0]['media_state_after'] = 0
        report = self.analyze(actions=actions)
        self.assertEqual(report['cycles'][0]['reject_codes'], ['pause_resume_media_state_not_confirmed'])
        self.assertIsNone(report['cycles'][0]['pause'])

    def test_unverified_generation_or_positive_trace_loss_rejected(self):
        fences = source_fences()
        fences['complete_fixed_generation_verified'] = False
        self.assertIn('fence_fixed_generation_not_verified', self.analyze(fences=fences)['reject_codes'])
        data = source_graphics()
        data['coverage'][0]['loss_or_overrun_value'] = 1
        self.assertIn('positive_trace_loss_or_import_error', self.analyze(data)['reject_codes'])
        data = source_graphics()
        data['coverage'][0]['graphics_parser_diagnostic_count'] = 100
        self.assertEqual(self.analyze(data)['accepted_cycle_count'], 1)

    def test_action_schema_rejects_text_bool_and_reversed_or_overlapping_brackets(self):
        for field, value in [('media_state_before', True), ('host_before_ns', 'PRIVATE_TITLE'),
                             ('host_after_ns', host(3)), ('media_state_host_before_ns', host(4))]:
            with self.subTest(field=field, value=value):
                actions = source_actions()
                actions['actions'][0][field] = value
                with self.assertRaises(ValueError):
                    self.analyze(actions=actions)
        actions = source_actions()
        actions['actions'][0]['private_text'] = 'PRIVATE_TITLE'
        with self.assertRaises(ValueError):
            self.analyze(actions=actions)

    def test_numeric_metadata_schema_does_not_accept_bool_or_nonobject_sections(self):
        for name in ('graphics', 'fences'):
            value = source_graphics() if name == 'graphics' else source_fences()
            value['schema'] = True
            with self.assertRaises(ValueError):
                self.analyze(data=value) if name == 'graphics' else self.analyze(fences=value)
        fences = source_fences()
        fences['guest_clock_queries'] = ['PRIVATE_UNEXPECTED_DATA']
        with self.assertRaisesRegex(ValueError, 'bounded_numeric_clock_queries_required'):
            self.analyze(fences=fences)

    def test_steady_missing_phases_not_imputed_or_joined_to_nearby_frames(self):
        data = source_graphics()
        data['events'] = [row for row in data['events'] if not (row['event_type'] == 5 and row['frame_number_low32'] >= 3)]
        report = self.analyze(data)
        self.assertEqual(report['steady_windows'][0]['missing_phase_identity_count'], 20)
        self.assertEqual(report['cycles'][0]['comparison']['reject_codes'], ['insufficient_complete_steady_identities'])

    def test_steady_windows_must_stay_within_their_confirmed_play_interval(self):
        actions = source_actions()
        actions['steady_windows'][0]['host_before_ns'] = host(5)
        with self.assertRaisesRegex(ValueError, 'steady_window_outside_confirmed_play_interval'):
            self.analyze(actions=actions)

    def test_cli_fresh_output_only_hashes_inputs_and_sanitizes_other_input_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = {'graphics': source_graphics(), 'fences': source_fences(), 'actions': source_actions()}
            inputs['graphics']['scope'] = 'PRIVATE_TITLE_MUST_NOT_LEAVE'
            inputs['fences']['package'] = 'PRIVATE_PACKAGE_MUST_NOT_LEAVE'
            for name, value in inputs.items():
                (root / (name + '.json')).write_text(json.dumps(value))
            command = [sys.executable, str(Path(probe.__file__)), '--graphics', str(root / 'graphics.json'),
                       '--fences', str(root / 'fences.json'), '--actions', str(root / 'actions.json'),
                       '--output', str(root / 'result.json')]
            run = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            result = (root / 'result.json').read_text()
            self.assertNotIn('PRIVATE_', result)
            self.assertEqual(set(json.loads(result)['input_sha256']), set(inputs))
            second = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(second.returncode, 0)
            self.assertEqual(result, (root / 'result.json').read_text())


if __name__ == '__main__':
    unittest.main()
