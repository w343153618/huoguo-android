"""Offline numeric fixtures; no device, socket, source-video or service operations."""
import copy
import unittest

from scripts.probes.udp_event_coverage import coverage, phone_window_coverage
from scripts.probes.analyze_udp_stalls import analyze


def fixture():
    return {
        'host': {'native_events_evicted': 0},
        'native_events': [
            {'event': 'summary', 'elapsed_us': 1_000_000, 'frames_budget_drops': 99,
             'output_deadline_drops': 1, 'source_frames': 50, 'final': False},
            {'event': 'summary', 'elapsed_us': 2_000_000, 'output_deadline_drops': 2,
             'source_frames': 110, 'final': False},
        ],
        'socket_video': {'counts': {'deadline_dropped_frames': 3},
                         'frame_events': [], 'frame_events_evicted': 0},
        'phone': {
            'first_server_packet_ns': 1_000_000_000, 'receive_end_ns': 3_000_000_000,
            'requested_seconds': 2, 'video_worker_alive': False,
            'native_frame_event_capacity': 8192, 'native_frame_events_evicted': 0,
            'native_frame_event_stats': {'enabled': True, 'generated': 4, 'pending': 0,
                                         'native_events_evicted': 0},
            'native_frame_events': [
                {'event': 'frame_delivered', 'event_sequence': index,
                 'event_phone_us': 1_000_000+index*300_000} for index in range(1, 5)],
            'native_fec': {'frames_expired': 5, 'reference_lost': 0},
            'video_input_queue': {'epoch_events_evicted': 0, 'epoch_event_capacity': 256,
                                  'overflow_events': 2},
            'inbox_epoch_events': [], 'presentation_records_evicted': 0,
            'presentation_frames': [],
        },
    }


class CoverageCheck(unittest.TestCase):
    def test_latest_snapshots_not_summed_and_counters_do_not_become_window_events(self):
        result = coverage(fixture())
        snapshots = result['cumulative_snapshots']
        self.assertEqual(snapshots['host_packetizer']['values']['source_frames'], 110)
        self.assertEqual(snapshots['host_packetizer']['values']['output_deadline_drops'], 2)
        self.assertFalse(snapshots['host_packetizer']['complete_final_snapshot'])
        self.assertEqual(snapshots['phone_native_fec']['values']['frames_expired'], 5)
        self.assertEqual(snapshots['phone_native_fec']['values']['reference_lost'], 0)
        self.assertEqual(snapshots['host_socket_guard']['values']['deadline_dropped_frames'], 3)
        self.assertNotIn('frames_budget_drops', snapshots['host_packetizer']['values'])

    def test_cropped_prefix_is_unknown_while_contiguous_exported_tail_can_be_inspected(self):
        raw = fixture()
        raw['phone']['native_frame_events'] = raw['phone']['native_frame_events'][2:]
        raw['phone']['native_frame_events_evicted'] = 2
        result = coverage(raw)
        native = result['detail_streams']['phone_native']
        self.assertEqual(native['detail_history_status'], 'contiguous_suffix_exported')
        self.assertFalse(native['complete_generated_history'])
        self.assertFalse(phone_window_coverage(result, 1_500_000_000, 1_800_000_000)
                         ['phone_native']['window_detail_complete'])
        self.assertTrue(phone_window_coverage(result, 2_000_000_000, 2_800_000_000)
                        ['phone_native']['window_detail_complete'])
        self.assertEqual(result['cumulative_snapshots']['phone_native_fec']['values']['frames_expired'], 5)

    def test_internal_ring_sequence_gap_remains_incomplete_even_with_matching_total(self):
        raw = fixture()
        del raw['phone']['native_frame_events'][1]
        raw['phone']['native_frame_event_stats']['native_events_evicted'] = 1
        native = coverage(raw)['detail_streams']['phone_native']
        self.assertTrue(native['generated_accounting_matches'])
        self.assertFalse(native['retained_sequences_contiguous'])
        self.assertFalse(native['complete_generated_tail'])

    def test_pending_events_and_unknown_eviction_counters_are_not_zero(self):
        raw = fixture()
        raw['phone']['native_frame_event_stats'].update(generated=5, pending=1)
        native = coverage(raw)['detail_streams']['phone_native']
        self.assertTrue(native['generated_accounting_matches'])
        self.assertFalse(native['complete_generated_tail'])
        raw['phone'].pop('native_frame_events_evicted')
        native = coverage(raw)['detail_streams']['phone_native']
        self.assertIsNone(native['evicted_records'])
        self.assertEqual(native['retention_status'], 'unknown')
        self.assertFalse(native['generated_accounting_matches'])

    def test_empty_enabled_stream_is_distinct_from_missing_or_disabled_diagnostics(self):
        raw = fixture()
        raw['phone']['native_frame_events'] = []
        raw['phone']['native_frame_event_stats']['generated'] = 0
        self.assertTrue(coverage(raw)['detail_streams']['phone_native']['complete_generated_history'])
        missing = copy.deepcopy(raw)
        missing['phone'].pop('native_frame_events')
        self.assertFalse(coverage(missing)['detail_streams']['phone_native']['complete_generated_history'])
        # A missing exported list cannot become a complete zero-event history.
        self.assertEqual(coverage(missing)['detail_streams']['phone_native']['retention_status'], 'missing')
        raw['phone']['native_frame_event_stats']['enabled'] = False
        self.assertFalse(coverage(raw)['detail_streams']['phone_native']['complete_generated_history'])

    def test_host_clock_cannot_define_phone_window_coverage(self):
        raw = fixture()
        expected = phone_window_coverage(coverage(raw), 1_100_000_000, 2_900_000_000)
        for row in raw['phone']['native_frame_events']:
            row['host_capture_us'] = 99_999_999_999
        raw['native_events'].append({'event': 'frame_source', 'host_capture_us': 1})
        self.assertEqual(expected, phone_window_coverage(coverage(raw), 1_100_000_000, 2_900_000_000))
        self.assertFalse(phone_window_coverage(coverage(raw), 900_000_000, 2_000_000_000)
                         ['phone_native']['window_detail_complete'])

    def test_invalid_phone_time_cannot_prove_absence_in_a_stall(self):
        raw = fixture()
        raw['phone']['native_frame_events'][0]['event_phone_us'] = True
        result = coverage(raw)
        self.assertEqual(result['detail_streams']['phone_native']['invalid_timestamp_records'], 1)
        self.assertFalse(phone_window_coverage(result, 1_100_000_000, 2_900_000_000)
                         ['phone_native']['window_detail_complete'])

    def test_stall_readback_marks_early_cropped_gap_without_losing_actual_surface_gap(self):
        raw = fixture()
        raw['phone']['native_frame_events'] = raw['phone']['native_frame_events'][2:]
        raw['phone']['native_frame_events_evicted'] = 2
        result = analyze(raw, {'presentation_ns': [1_500_000_000, 1_700_000_000]})
        self.assertEqual(result['stalls_count'], 1)
        self.assertEqual(result['stalls'][0]['gap_ms'], 200)
        self.assertFalse(result['stalls'][0]['diagnostic_window_coverage']
                         ['phone_native']['window_detail_complete'])


if __name__ == '__main__':
    unittest.main()
