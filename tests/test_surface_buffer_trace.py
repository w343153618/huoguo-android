import csv
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

PATH = Path(__file__).resolve().parents[1] / 'scripts/probes/analyze_surface_buffer_trace.py'
SPEC = importlib.util.spec_from_file_location('surface_buffer_trace', PATH)
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


def sections():
    coverage = {key: 0 for header, name in PROBE.HEADERS.items()
                if name == 'coverage' for key in header}
    coverage.update(snapshot_count=4, snapshot_min_ts_ns=10,
                    snapshot_max_ts_ns=90, target_buffer_layer_id_count=1,
                    transaction_entry_count=3, transaction_min_ts_ns=1,
                    transaction_max_ts_ns=80)
    return {'trace_bounds': [{'trace_start_ns': 10, 'trace_end_ns': 100}],
            'analysis_window': [{'analysis_start_ns': 10, 'analysis_end_ns': 80}],
            'clock_relations': [{'clock_snapshot_id': 1, 'trace_ts_ns': 10,
                                 'monotonic_ns': 11, 'boottime_ns': 10}],
            'coverage': [coverage], 'layer_observations': [], 'buffer_transactions': []}


def observation(frame, ts, row_id=1, layer_id=391):
    return dict(layer_row_id=row_id, snapshot_id=row_id, snapshot_ts_ns=ts,
                layer_id=layer_id, curr_frame=frame, active_width=1920,
                active_height=1080, queued_frames=None, refresh_pending=None,
                hwc_composition_type=2, snapshot_vsync_id=None)


def transaction(frame, ts, row_id=1, layer_id=391, buffer_id=500):
    return dict(transaction_row_id=row_id, transaction_entry_id=row_id,
                entry_ts_ns=ts, entry_vsync_id=1, transaction_id=1000 + row_id,
                layer_id=layer_id, buffer_frame_number=frame, buffer_id=buffer_id,
                cached_buffer_id=buffer_id, buffer_flags=7, state_frame_number=None,
                post_time_ns=ts - 10, transaction_vsync_id=-1)


def encoded(values):
    blocks = []
    for header, name in PROBE.HEADERS.items():
        out = io.StringIO()
        writer = csv.writer(out, lineterminator='\n')
        writer.writerow(header)
        for row in values[name]:
            writer.writerow('[NULL]' if row[key] is None else row[key] for key in header)
        blocks.append(out.getvalue().strip())
    return ('\n\n'.join(blocks) + '\n').encode('ascii')


class SurfaceBufferTraceChecks(unittest.TestCase):
    def test_export_parser_rejects_arbitrary_text_and_columns(self):
        value = sections()
        value['layer_observations'] = [observation(1, 20)]
        result = PROBE.parse_results(encoded(value))
        self.assertEqual(result['layer_observations'][0]['curr_frame'], 1)
        value['layer_observations'][0]['curr_frame'] = 'PRIVATE_VIDEO_TITLE'
        with self.assertRaises(PROBE.ProbeError):
            PROBE.parse_results(encoded(value))
        with self.assertRaises(ValueError):
            PROBE.parse_results(encoded(sections()) + b'"secret_column"\n"PRIVATE_TITLE"\n')
        with self.assertRaises(ValueError):
            PROBE.parse_results(encoded(sections()) + encoded(sections()))

    def test_missing_time_identity_and_integer_overflow_are_rejected(self):
        value = sections()
        value['buffer_transactions'] = [transaction(1, 20)]
        for invalid in (None, 'not_a_timestamp', str(2 ** 63), '20.5'):
            value['buffer_transactions'][0]['entry_ts_ns'] = invalid
            with self.assertRaises((PROBE.ProbeError, ValueError)):
                PROBE.parse_results(encoded(value))

    def test_pairing_uses_layer_and_frame_not_nearest_timestamp_or_buffer_id(self):
        value = sections()
        value['layer_observations'] = [observation(1, 25, 1), observation(2, 40, 2)]
        value['buffer_transactions'] = [transaction(1, 20, 1), transaction(2, 24, 2),
                                         transaction(1, 24, 3, layer_id=999)]
        report = PROBE.summarize(value, 100)
        matches = report['numeric_identity_matches']
        self.assertEqual([row['transaction_row_id'] for row in matches], [1, 2])
        self.assertEqual([row['buffer_id'] for row in matches], [500, 500])
        self.assertEqual(report['identity_association']['transaction_identity_without_observation_in_bounds'], 1)

    def test_duplicate_transactions_and_frame_resets_remain_ambiguous(self):
        value = sections()
        value['layer_observations'] = [observation(1, 20, 1), observation(2, 30, 2),
                                       observation(1, 40, 3)]
        value['buffer_transactions'] = [transaction(1, 15, 1), transaction(2, 25, 2),
                                         transaction(2, 26, 3)]
        report = PROBE.summarize(value, 100)
        self.assertEqual(report['identity_association']['matched_identity_count'], 0)
        self.assertEqual(report['identity_association']['ambiguous_identity_count'], 2)
        self.assertEqual(report['summary_by_layer'][0]['observed_reset_count'], 1)

    def test_observation_runs_have_censored_edges_and_no_invented_terminal_hold(self):
        rows = [observation(1, 20, 1), observation(1, 30, 2), observation(2, 60, 3)]
        runs = PROBE.build_runs(rows)
        self.assertTrue(runs[0]['left_censored'])
        self.assertEqual(runs[0]['observed_hold_lower_bound_ns'], 10)
        self.assertEqual(runs[0]['first_seen_gap_to_next_frame_ns'], 40)
        self.assertTrue(runs[-1]['right_censored'])
        self.assertIsNone(runs[-1]['first_seen_gap_to_next_frame_ns'])

    def test_intersection_rejects_historical_and_uncovered_tail_records(self):
        for ts in (9, 81):
            value = sections()
            value['layer_observations'] = [observation(1, ts)]
            with self.assertRaises(ValueError):
                PROBE.summarize(value, 100)
            value = sections()
            value['buffer_transactions'] = [transaction(1, ts)]
            with self.assertRaises(ValueError):
                PROBE.summarize(value, 100)

    def test_size_and_duration_warnings_are_independent_of_zero_loss_stats(self):
        value = sections()
        value['trace_bounds'][0]['trace_end_ns'] = 25_960_000_010
        report = PROBE.summarize(value, 63 * 1024 * 1024, max_trace_bytes=64 * 1024 * 1024)
        self.assertFalse(report['duration_requirement_met_with_500ms_tolerance'])
        self.assertTrue(report['trace_near_size_limit'])
        self.assertEqual(report['coverage'][0]['import_error_or_loss_count'], 0)
        self.assertFalse(report['target_buffer_surfaceview_covered'])

    def test_multiple_buffer_layers_require_selection(self):
        value = sections()
        value['coverage'][0]['target_buffer_layer_id_count'] = 2
        with self.assertRaises(ValueError):
            PROBE.summarize(value, 100)
        with self.assertRaises(ValueError):
            PROBE.build_sql('391 OR 1=1')
        with self.assertRaises(ValueError):
            PROBE.build_sql(True)
        value = sections()
        value['layer_observations'] = [observation(1, 20, layer_id=999)]
        with self.assertRaises(ValueError):
            PROBE.summarize(value, 100, selected_layer_id=391)

    def test_private_trace_requires_owner_only_permissions(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
            trace = Path(folder) / 'fixture.pftrace'
            trace.write_bytes(b'numeric fixture')
            trace.chmod(0o600)
            self.assertEqual(PROBE.private_buffer_trace(trace), trace.resolve())
            trace.chmod(0o644)
            with self.assertRaises(PROBE.ProbeError):
                PROBE.private_buffer_trace(trace)
        with self.assertRaises(PROBE.ProbeError):
            PROBE.private_buffer_trace(PATH)

    def test_numeric_report_contains_no_arbitrary_name_or_raw_proto(self):
        value = sections()
        value['layer_observations'] = [observation(1, 25)]
        value['buffer_transactions'] = [transaction(1, 20)]
        report = PROBE.summarize(PROBE.parse_results(encoded(value)), 100)
        for forbidden in ('PRIVATE_TITLE', 'base64_proto', 'layer_name', 'codec_name'):
            self.assertNotIn(forbidden, json.dumps(report))

    def test_raw_post_difference_does_not_assert_clock_or_presentation_verification(self):
        value = sections()
        value['layer_observations'] = [observation(1, 25)]
        value['buffer_transactions'] = [transaction(1, 20)]
        report = PROBE.summarize(value, 100)
        self.assertEqual(report['numeric_identity_matches'][0]['post_to_first_observation_raw_clock_difference_ns'], 15)
        self.assertFalse(report['post_time_clock_origin_verified_for_guest'])
        self.assertTrue(report['clock_relation_summary']['trace_timestamps_match_boottime'])
        self.assertEqual(report['clock_relation_summary']['monotonic_minus_boottime_min_ns'], 1)


if __name__ == '__main__':
    unittest.main()
