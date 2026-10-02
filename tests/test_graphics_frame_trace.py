import csv
import importlib.util
import io
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / 'scripts/probes/analyze_graphics_frame_trace.py'
SPEC = importlib.util.spec_from_file_location('graphics_frame_trace', PATH)
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


def sections():
    coverage = {key: 0 for header, name in PROBE.HEADERS.items()
                if name == 'coverage' for key in header}
    coverage.update(target_layer_name_count=1, target_machine_count=1)
    return {'trace_bounds': [{'trace_start_ns': 10, 'trace_end_ns': 1000}],
            'clock_relations': [{'clock_snapshot_id': 1, 'trace_ts_ns': 10,
                                 'monotonic_ns': 17, 'boottime_ns': 10},
                                {'clock_snapshot_id': 2, 'trace_ts_ns': 1000,
                                 'monotonic_ns': 1009, 'boottime_ns': 1000}],
            'coverage': [coverage], 'events': []}


def event(stage, ts, frame=1, row_id=1, track=5, buffer_id=9):
    return {'slice_id': row_id, 'ts_ns': ts, 'dur_ns': 0,
            'track_id': track, 'machine_id': 0, 'buffer_id_low32': buffer_id,
            'frame_number_low32': frame, 'event_type': stage}


def encoded(values):
    blocks = []
    for header, name in PROBE.HEADERS.items():
        out = io.StringIO()
        writer = csv.writer(out, lineterminator='\n')
        writer.writerow(header)
        for row in values[name]:
            writer.writerow('[NULL]' if row[key] is None else row[key] for key in header)
        blocks.append(out.getvalue().strip())
    return ('\n\n'.join(blocks)+'\n').encode('ascii')


class GraphicsFrameTraceChecks(unittest.TestCase):
    def test_numeric_export_rejects_text_unknown_fields_and_missing_identity(self):
        value = sections()
        value['events'] = [event(2, 20)]
        self.assertEqual(PROBE.parse_results(encoded(value))['events'][0]['event_type'], 2)
        for invalid in ('PRIVATE_VIDEO_TITLE', None, 2**63):
            value['events'][0]['ts_ns'] = invalid
            with self.assertRaises((ValueError, PROBE.ProbeError)):
                PROBE.parse_results(encoded(value))
        with self.assertRaises(ValueError):
            PROBE.parse_results(encoded(sections())+b'"private_name"\n"PRIVATE"\n')
        with self.assertRaises(ValueError):
            PROBE.parse_results(encoded(sections())+encoded(sections()))
        value['events'] = [event(2, 20, buffer_id=2**32)]
        with self.assertRaises(ValueError):
            PROBE.parse_results(encoded(value))

    def test_matching_requires_buffer_frame_and_track_not_nearest_time(self):
        value = sections()
        value['events'] = [event(2, 20, 1, 1), event(2, 21, 2, 2),
                           event(5, 40, 1, 3), event(8, 60, 1, 4)]
        report = PROBE.summarize(value, 100)
        chains = report['numeric_identity_chains']
        self.assertEqual(chains[0]['queue_to_latch_ns'], 20)
        self.assertEqual(chains[0]['latch_to_present_ns'], 20)
        self.assertEqual(chains[0]['acquire_fence_ts_ns'], None)
        self.assertTrue(chains[1]['queue_without_present_observed'])
        self.assertEqual(report['event_counts']['4'], 0)
        self.assertEqual(report['identity_association']['complete_queue_latch_present_count'], 1)

    def test_duplicate_stages_and_low32_collisions_remain_ambiguous(self):
        value = sections()
        value['events'] = [event(2, 20, 1, 1), event(2, 21, 1, 2),
                           event(5, 40, 1, 3), event(8, 60, 1, 4)]
        report = PROBE.summarize(value, 100)
        self.assertEqual(report['identity_association']['duplicate_stage_identity_count'], 1)
        self.assertFalse(report['numeric_identity_chains'])
        value['events'] = [event(2, 20, 1, 1), event(5, 40, 1, 2, track=6),
                           event(8, 60, 1, 3, track=6)]
        report = PROBE.summarize(value, 100)
        self.assertEqual(report['identity_association']['cross_track_low32_collision_identity_count'], 2)
        self.assertFalse(report['numeric_identity_chains'])

    def test_observed_reset_and_negative_event_order_prevent_causal_intervals(self):
        value = sections()
        value['events'] = [event(2, 20, 2, 1), event(5, 40, 2, 2),
                           event(8, 60, 2, 3), event(2, 70, 1, 4)]
        report = PROBE.summarize(value, 100)
        self.assertEqual(report['identity_association']['observed_queue_frame_reset_count'], 1)
        self.assertFalse(report['numeric_identity_chains'])
        value['events'] = [event(2, 50, 1, 1), event(5, 40, 1, 2)]
        report = PROBE.summarize(value, 100)
        self.assertEqual(report['identity_association']['invalid_event_order_count'], 1)
        self.assertEqual(report['identity_association']['queue_to_latch']['count'], 0)

    def test_clock_conversion_gap_window_and_censored_edges_are_explicit(self):
        value = sections()
        value['events'] = [event(8, 50, 1, 1), event(2, 70, 2, 2),
                           event(5, 90, 2, 3), event(8, 150, 3, 4),
                           event(2, 160, 4, 5)]
        report = PROBE.summarize(value, 100)
        gap = report['largest_present_gap']
        self.assertEqual(gap['gap_ns'], 100)
        self.assertEqual(gap['sf_receipt_queue']['event_count'], 1)
        self.assertEqual(gap['latch']['event_count'], 1)
        self.assertEqual(gap['sf_receipt_queue']['max_subwindow_without_marker_ns'], 80)
        self.assertEqual(gap['start_monotonic_observed_offset_range_ns'], [57, 59])
        self.assertEqual(gap['end_present_identities'][0]['identity_chain']['frame_number_low32'], 3)
        self.assertEqual(gap['intervening_frame_observations'][0]['identity_chain']['latch_ts_ns'], 90)
        self.assertIsNone(gap['intervening_frame_observations'][0]['identity_chain']['present_fence_ts_ns'])
        self.assertEqual(report['censored_edges']['last_present_to_trace_end_ns'], 850)
        self.assertEqual(report['censored_edges']['queue_after_last_present_without_present_count'], 1)
        self.assertEqual(report['event_gaps']['8']['count'], 1)
        self.assertTrue(report['clock_relation_summary']['trace_timestamps_match_boottime'])

    def test_diagnostics_loss_and_coverage_do_not_imply_completeness(self):
        value = sections()
        value['coverage'][0]['graphics_parser_diagnostic_count'] = 20
        report = PROBE.summarize(value, 63*1024*1024)
        self.assertEqual(report['coverage'][0]['loss_or_overrun_value'], 0)
        self.assertEqual(report['coverage'][0]['graphics_parser_diagnostic_count'], 20)
        self.assertTrue(report['trace_near_size_limit'])
        self.assertFalse(report['duration_requirement_met_with_500ms_tolerance'])
        self.assertFalse(report['target_surfaceview_observed'])
        value['events'] = [event(2, 1001)]
        with self.assertRaises(ValueError):
            PROBE.summarize(value, 100)
        value['events'] = []
        value['coverage'][0]['target_layer_name_count'] = 2
        with self.assertRaises(ValueError):
            PROBE.summarize(value, 100)


if __name__ == '__main__':
    unittest.main()
