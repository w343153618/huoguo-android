"""Offline acceptance/identity tests, with no device or subprocess calls."""
import json
from pathlib import Path
import tempfile
import unittest

from scripts.probes import summarize_overnight_suite as summary


class OvernightSummaryTest(unittest.TestCase):
    def test_sorted_unique_surface_uses_fixed_window_and_separates_edge_gaps(self):
        origin = 1_000_000_000
        stamps = [origin+int(x*1e9) for x in (4.9, 5.1, 5.2, 29.9, 30.1)]
        surface = {'presentation_ns': list(reversed(stamps))+[stamps[2]],
            'presented_frames': 5, 'seconds': 32, 'display_vsync_ns': 8_333_333,
            'polls': [{'elapsed_s': x/2} for x in range(65)]}
        result = summary.surface_summary(surface, origin, origin+5_000_000_000, origin+30_000_000_000)
        self.assertTrue(result['coverage_complete'])
        self.assertEqual(result['duplicate_timestamps'], 1)
        self.assertFalse(result['input_sorted'])
        self.assertEqual(result['window_presentations'], 3)
        self.assertEqual(result['window_fps'], .12)
        self.assertEqual(result['window_gaps_ms']['count'], 2)
        self.assertEqual(len(result['boundary_clipped_gaps']), 2)
        self.assertAlmostEqual(result['boundary_clipped_gaps'][0]['inside_window_ms'], 100)

    def test_missing_end_or_ring_poll_risk_cannot_certify_surface_rate(self):
        surface = {'presentation_ns': [4_900_000_000, 5_100_000_000, 29_900_000_000],
            'presented_frames': 3, 'seconds': 32, 'display_vsync_ns': 8_333_333,
            'polls': [{'elapsed_s': 0}, {'elapsed_s': 2}]}
        result = summary.surface_summary(surface, 1, 5_000_000_000, 30_000_000_000)
        self.assertFalse(result['coverage_complete'])
        self.assertTrue(result['poll_ring_capacity_risk'])
        self.assertIsNone(result['window_fps'])

    def test_evicted_native_history_never_becomes_complete_receive_fps(self):
        phone = {'diagnostic_events_enabled': True,
            'native_frame_events': [{'event': 'frame_delivered', 'event_phone_us': 6_000_000}],
            'native_frame_events_evicted': 1,
            'native_frame_event_stats': {'native_events_evicted': 0, 'pending': 0, 'generated': 2},
            'native_fec': {'frames_delivered': 2}, 'queued_media_frames': 0,
            'presentation_records_evicted': 0, 'presentation_pending_evicted': 0,
            'surface_unmatched_callbacks': 0, 'codec_callback_count': 0,
            'video_input_queue': {'epoch_events_evicted': 0}}
        result = summary.phone_window(phone, 5_000_000_000, 30_000_000_000)
        receive = result['complete_au_receive']
        self.assertEqual(receive['observed_count'], 1)
        self.assertFalse(receive['complete_history'])
        self.assertIsNone(receive['fps'])

    def test_each_timestamp_field_must_be_complete_for_its_own_rate(self):
        rows = [{'decoder_ready_ns': 6_000_000_000, 'callback_ns': -1},
                {'decoder_ready_ns': 7_000_000_000, 'callback_ns': 7_100_000_000}]
        ready = summary.event_rate(rows, 'decoder_ready_ns', 5_000_000_000, 30_000_000_000, True)
        callback = summary.event_rate(rows, 'callback_ns', 5_000_000_000, 30_000_000_000, True)
        self.assertTrue(ready['complete_history'])
        self.assertFalse(callback['complete_history'])
        self.assertIsNone(callback['fps'])
        self.assertEqual(callback['observed_count'], 1)

    @staticmethod
    def trace(dropped=0):
        clock = [{'event': 'trace_clock', 'process': p, 'clock_domain': summary.CLOCK}
                 for p in ('python', 'swift')]
        captures = [{'event': 'capture_enqueue', 'capture_seq': seq, 'source_pts_us': pts,
                     'grpc_return_ns': time, 'enqueue_ns': time+1000}
                    for seq, pts, time in ((1, 100, 1_000_000_000),
                        (2, 200, 7_000_000_000), (3, 300, 32_000_000_000))]
        native = [{'event': 'raw_submit', 'capture_seq': 2, 'source_pts_us': 200,
                   'idle_repeat': False, 'dequeue_ns': 7_000_002_000},
                  {'event': 'vt_frame', 'source_pts_us': 200,
                   'vt_submit_ns': 7_000_003_000, 'vt_callback_ns': 7_010_003_000}]
        status = {'process': 'python', 'available': True, 'summary_present': True,
            'clean_close': True, 'malformed': 0, 'discarded': 0,
            'summary': {'dropped_records': dropped, 'byte_capped': False}}
        return {'records': clock+captures+native, 'status': [status]}

    def test_host_endpoint_coverage_does_not_rescue_dropped_history(self):
        result = summary.host_window(self.trace(dropped=1))
        self.assertTrue(result['coverage_complete'])
        self.assertFalse(result['complete_capture_history'])
        self.assertIsNone(result['captures_fps'])
        self.assertEqual(result['observed_captures'], 1)

    def test_reused_pts_or_conflicting_sequence_rejects_host_stage_join(self):
        trace = self.trace()
        trace['records'].extend([
            {'event': 'capture_enqueue', 'capture_seq': 4, 'source_pts_us': 200,
             'grpc_return_ns': 7_100_000_000},
            {'event': 'raw_submit', 'capture_seq': 4, 'source_pts_us': 200, 'idle_repeat': False}])
        result = summary.host_window(trace)
        self.assertEqual(result['unique_submitted_joins'], 0)
        self.assertEqual(result['stage_ms']['vt_submit_to_callback_ms']['count'], 0)
        trace = self.trace()
        for row in trace['records']:
            if row['event'] == 'vt_frame':
                row['capture_seq'] = 99
        result = summary.host_window(trace)
        self.assertEqual(result['ambiguous_events']['vt_frame'], 1)
        self.assertEqual(result['stage_ms']['vt_submit_to_callback_ms']['count'], 0)

    def test_private_pointer_and_codec_filename_are_never_read(self):
        with tempfile.TemporaryDirectory() as name:
            parent = Path(name)
            private = parent/'private.json'
            private.write_text('{"secret":"do not read"}')
            folder = parent/'cap-01-60'
            folder.mkdir()
            link = folder/'hint120-buffer80-fifo-submit0-01.json'
            link.symlink_to(private)
            value, status = summary.read_saved(link, folder)
            self.assertIsNone(value)
            self.assertEqual(status, 'unsafe_path')
            row = summary.report_row(folder, {}, {'report': 'codec720-01.json'})
            self.assertEqual(row['status'], 'unsafe_or_non_udp_report_name')

    def test_all_matrix_rows_and_partial_json_are_preserved_without_codec_matrix(self):
        with tempfile.TemporaryDirectory() as name:
            parent = Path(name)
            (parent/'codec720-matrix.json').write_text('{"runs":[{}]}')
            incomplete = parent/'long-01-80'
            incomplete.mkdir()
            (incomplete/'matrix.json').write_text('{')
            complete = parent/'cap-01-60'
            complete.mkdir()
            report = 'hint120-buffer80-fifo-submit0-01.json'
            (complete/'matrix.json').write_text(json.dumps({'runs': [{'report': report}]}))
            result = summary.summarize(parent)
            self.assertEqual(result['counts']['matrices'], 2)
            self.assertEqual(result['counts']['rows'], 1)
            self.assertEqual(result['rows'][0]['status'], 'missing')
            self.assertTrue(any(x['status'] == 'unreadable_or_incomplete' for x in result['matrices']))

    def test_omitted_raw_request_and_upstream_failures_cannot_be_resurrected(self):
        state = {'command_ok': True, 'unknown': False, 'state_known': True,
                 'active_sessions': 1, 'state': 3}
        report = {'experimental_client': True,
            'client_package': 'local.remoteandroid.direct.experiment',
            'probe_package': 'local.remoteandroid.phoneprobe.experiment',
            'instrumentation_target_verified': True, 'surface_submit_report_verified': True,
            'host_failures': [], 'actual_surface_samples': {'source': {
                'seconds': 32, 'presented_frames': 1, 'display_vsync_ns': 16_666_667}},
            'phone': {'test_scope': next(iter(summary.UDP_SCOPES)),
                'transport': summary.UDP_TRANSPORT, 'requested_seconds': 35,
                'first_server_packet_ns': 1_000_000_000, 'receive_end_ns': 36_000_000_000,
                'observation_end_ns': 36_000_000_000, 'running_at_end': True,
                'received_media_frames': 1, 'queued_media_frames': 1, 'codec_callback_count': 1}}
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)/'budget-01-60'
            folder.mkdir()
            filename = 'hint120-buffer80-fifo-submit0-rawsubmit60-01.json'
            (folder/filename).write_text(json.dumps(report))
            matrix = {'conditions': {'requested_seconds': 35,
                'requested_raw_submit_fps': 60, 'video_fps_cap': 120}}
            row = {'report': filename, 'exit_code': 0,
                   'source_state_before': state, 'source_state_after': state}
            result = summary.report_row(folder, matrix, row)
            self.assertTrue(result['validation']['duration']['passed'])
            self.assertTrue(result['validation']['source']['passed'])
            self.assertFalse(result['validation']['raw_submit_budget_verified'])
            self.assertFalse(result['real_video_valid'])
            matrix['conditions'].pop('requested_raw_submit_fps')
            row['valid_real_video_test'] = False
            result = summary.report_row(folder, matrix, row)
            self.assertFalse(result['validation']['prior_validation_not_failed'])
            self.assertFalse(result['real_video_valid'])
            report['requested_raw_submit_fps'] = 'sensitive-value'
            report['phone']['fps_limit'] = {'secret': 'sensitive-value'}
            matrix['conditions']['video_fps_cap'] = 'sensitive-value'
            (folder/filename).write_text(json.dumps(report))
            result = summary.report_row(folder, matrix, row)
            self.assertNotIn('sensitive-value', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
