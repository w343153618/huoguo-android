import csv
import importlib.util
import io
import json
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / 'scripts/probes/analyze_sf_readiness_trace.py'
SPEC = importlib.util.spec_from_file_location('sf_readiness_trace', PATH)
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


def fixture(marker_names=()):
    data = {
        'trace_bounds': [{'trace_start_ns': 10, 'trace_end_ns': 30_000_000_010}],
        'coverage': [{'sf_slice_count': 100, 'candidate_marker_count': len(marker_names),
                      'import_error_stat_count': 0, 'data_loss_stat_count': 0,
                      'data_loss_stat_value': 0, 'graphics_parser_info_value': 100}],
        'clock_relations': [{'clock_snapshot_id': 0, 'trace_ts_ns': 10,
                             'monotonic_ns': 94, 'boottime_ns': 10}],
        'private_candidates': [],
    }
    for index, name in enumerate(marker_names):
        data['private_candidates'].append({'slice_id': index + 1, 'ts_ns': 20 + index,
                                           'dur_ns': 100, 'utid': 1, 'pid': 10,
                                           'tid': 10, 'private_marker_name': name})
    chunks = []
    for header, key in PROBE.HEADERS.items():
        out = io.StringIO()
        writer = csv.writer(out, lineterminator='\n')
        writer.writerow(header)
        for row in data[key]:
            writer.writerow('[NULL]' if row[column] is None else row[column] for column in header)
        chunks.append(out.getvalue().strip())
    return ('\n\n'.join(chunks) + '\n').encode('utf-8')


class SfReadinessTraceChecks(unittest.TestCase):
    def test_exact_desired_and_early_formats_export_numeric_fields(self):
        desired = PROBE.parse_marker('not current desiredPresentTime: 500 expectedPresentTime: 400')
        self.assertEqual(desired['reason_id'], 1)
        self.assertEqual(desired['desired_present_ns'], 500)
        self.assertEqual(desired['expected_present_ns'], 400)
        early = PROBE.parse_marker('frameIsEarly vsyncId: -1 expectedPresentTime: 400')
        self.assertEqual(early['reason_id'], 2)
        self.assertEqual(early['vsync_id'], -1)
        self.assertFalse(early['identity_verified'])

    def test_vsync_uid_is_not_silently_assigned_to_a_surface(self):
        row = PROBE.parse_marker('!isVsyncValid expectedPresentTime: 400 uid: 10099')
        self.assertEqual(row['reason_id'], 3)
        self.assertEqual(row['origin_uid'], 10099)
        self.assertEqual(row['scope_id'], 1)
        self.assertIsNone(row['layer_generation_id'])

    def test_arbitrary_layer_names_only_select_scope_and_never_escape(self):
        name = 'SurfaceView[app.morphe.android.youtube/PRIVATE_VIDEO_TITLE](BLAST)#424'
        rows = [PROBE.parse_marker(prefix + name) for prefix in
                ['hasPendingBuffer ', 'fence unsignaled ',
                 'fence unsignaled try allowLatchUnsignaled ']]
        self.assertEqual([row['reason_id'] for row in rows], [4, 5, 6])
        self.assertTrue(all(row['scope_id'] == 2 for row in rows))
        self.assertTrue(all(row['layer_generation_id'] is None and not row['identity_verified'] for row in rows))
        self.assertNotIn('PRIVATE_VIDEO_TITLE', json.dumps(rows))
        self.assertEqual(PROBE.parse_marker('fence unsignaled OtherLayer#424')['scope_id'], 3)

    def test_barrier_values_are_distinct_from_buffer_frame_identity(self):
        row = PROBE.parse_marker('NotReadyBarrier SurfaceView[app.morphe.android.youtube] barrierFrameNumber:20 > 30')
        self.assertEqual(row['reason_id'], 7)
        self.assertEqual(row['barrier_current_frame'], 20)
        self.assertEqual(row['barrier_required_frame'], 30)
        self.assertFalse(row['parent_transaction_identity_present'])

    def test_invalid_numbers_and_unknown_names_do_not_turn_into_zero(self):
        names = ['not current desiredPresentTime: PRIVATE expectedPresentTime: 400',
                 'not current desiredPresentTime: 1.5 expectedPresentTime: 400',
                 'not current desiredPresentTime: 01 expectedPresentTime: 400',
                 'frameIsEarly vsyncId: 2 expectedPresentTime: 400 EXTRA',
                 'Unknown PRIVATE_NAME', 'fence unsignaled try allowLatchUnsignaled ']
        self.assertTrue(all(PROBE.parse_marker(name) is None for name in names))
        raw = fixture(['not current desiredPresentTime: 9223372036854775808 expectedPresentTime: 400'])
        report = PROBE.parse_results(raw)
        self.assertEqual(report['invalid_marker_format_count'], 1)
        self.assertEqual(report['numeric_readiness_records'], [])

    def test_parser_retains_only_numeric_records_and_fixed_metadata(self):
        raw = fixture(['hasPendingBuffer SurfaceView[app.morphe.android.youtube/PRIVATE_TITLE]#424',
                       'not current desiredPresentTime: bad expectedPresentTime: 400'])
        result = PROBE.parse_results(raw)
        self.assertEqual(result['invalid_marker_format_count'], 1)
        text = json.dumps(result)
        for forbidden in ('PRIVATE_TITLE', 'private_marker_name', 'SurfaceView[', 'hasPendingBuffer '):
            self.assertNotIn(forbidden, text)

    def test_whole_report_rejects_unknown_columns_and_non_integer_metadata(self):
        with self.assertRaises(ValueError):
            PROBE.parse_results(fixture() + b'"secret_header"\n"PRIVATE_VALUE"\n')
        with self.assertRaises(ValueError):
            PROBE.parse_results(fixture() + fixture())
        raw = fixture().replace(b'trace_start_ns', b'unknown_ns')
        with self.assertRaises(ValueError):
            PROBE.parse_results(raw)
        raw = fixture().replace(b'10,30000000010', b'bad,30000000010')
        self.assertNotEqual(raw, fixture())
        with self.assertRaises(PROBE.ProbeError):
            PROBE.parse_results(raw)
        raw = fixture().replace(b'10,30000000010', b'[NULL],30000000010')
        with self.assertRaises(ValueError):
            PROBE.parse_results(raw)

    def test_info_parser_counts_are_not_data_loss(self):
        result = PROBE.summarize(PROBE.parse_results(fixture()), 100)
        self.assertEqual(result['coverage'][0]['graphics_parser_info_value'], 100)
        self.assertEqual(result['coverage'][0]['data_loss_stat_count'], 0)
        self.assertFalse(result['readiness_markers_observed'])

    def test_open_scope_is_not_wait_duration_and_has_no_frame_match(self):
        parsed = PROBE.parse_results(fixture(['fence unsignaled']))
        parsed['numeric_readiness_records'][0]['dur_ns'] = -1
        result = PROBE.summarize(parsed, 100)
        summary = next(row for row in result['summary'] if row['scope_id'] == 1 and row['reason_id'] == 5)
        self.assertEqual(summary['open_marker_scope_count'], 1)
        self.assertEqual(summary['marker_scope_duration']['count'], 0)
        self.assertFalse(result['identity_verified'])
        self.assertFalse(result['parent_transaction_identity_present'])

    def test_marker_record_must_be_inside_trace_bounds(self):
        parsed = PROBE.parse_results(fixture(['fence unsignaled']))
        parsed['numeric_readiness_records'][0]['ts_ns'] = 9
        with self.assertRaises(ValueError):
            PROBE.summarize(parsed, 100)


if __name__ == '__main__':
    unittest.main()
