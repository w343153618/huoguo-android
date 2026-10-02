import unittest
import copy
from scripts.probes.analyze_udp_stalls import analyze, supply_gaps


class StallAnalysisCheck(unittest.TestCase):
    def fixture(self):
        return {'source': 'synthetic metadata for parser tests', 'phone': {
            'first_server_packet_ns': 1_000_000_000, 'receive_end_ns': 2_000_000_000,
            'fps_limit': 60, 'media_input_observations': [
                {'received_ns': 1_010_000_000, 'input_queued_ns': 1_011_000_000},
                {'received_ns': 1_150_000_000, 'input_queued_ns': 1_151_000_000}],
            'native_frame_events': [{'event': 'frame_expired', 'event_phone_us': 1_120_000,
                                    'host_capture_us': 999_999_999_999, 'frame_id': 4}],
            'inbox_epoch_events': [{'event': 'chain_lost', 'time_ns': 1_130_000_000, 'epoch': 1}],
            'presentation_frames': [{'input_queued_ns': 1_151_000_000, 'decoder_ready_ns': 1_155_000_000,
                                     'scheduled_ns': 9_999_999_999, 'pts_us': 10}]}}, {
            'presentation_ns': [990_000_000, 1_020_000_000, 1_160_000_000, 1_176_666_667, 2_010_000_000]}

    def test_actual_surface_and_same_clock_associations(self):
        report, surface = self.fixture()
        value = analyze(report, surface)
        self.assertEqual(value['stalls_count'], 1)
        self.assertEqual(value['stalls'][0]['gap_ms'], 140)
        self.assertIn('receiver_frame_expiry_near_gap', value['stalls'][0]['associated_observations'])
        self.assertIn('inbox_chain_loss_near_gap', value['stalls'][0]['associated_observations'])
        self.assertEqual(value['stalls'][0]['codec_ready_in_window'][0]['queue_to_ready_ms'], 4)

    def test_host_clock_and_vendor_target_cannot_create_stall(self):
        report, surface = self.fixture()
        expected = copy.deepcopy(analyze(report, surface))
        report['phone']['native_frame_events'][0]['host_capture_us'] = 1
        report['phone']['presentation_frames'][0]['scheduled_ns'] = 1
        actual = analyze(report, surface)
        self.assertEqual(expected['surface_gaps_ms'], actual['surface_gaps_ms'])
        self.assertEqual(expected['stalls'][0]['associated_observations'], actual['stalls'][0]['associated_observations'])
        self.assertEqual(expected['stalls'][0]['codec_ready_in_window'], actual['stalls'][0]['codec_ready_in_window'])

    def test_gap_crossing_window_is_preserved(self):
        self.assertEqual(supply_gaps([100_000_000, 500_000_000], 200_000_000,
                                     300_000_000, 300_000_000)[0]['overlap_ms'], 100)

    def test_packetizer_intake_gap_is_separate_from_source_pts(self):
        report,surface=self.fixture()
        report['native_events']=[
            {'event':'frame_source','frame':3,'host_capture_us':10_000_000,'source_pts_us':1_000_000},
            {'event':'frame_source','frame':4,'host_capture_us':10_080_000,'source_pts_us':1_016_667},
            {'event':'frame_output','frame':4,'expected_shards':120,'emitted_shards':117,
             'complete':False,'media_data_complete':False,'failure_stage':'reserve_deadline'}]
        context=analyze(report,surface)['stalls'][0]['host_context_joined_by_frame_id_not_clock'][0]
        self.assertEqual(context['packetizer_intake_gap_from_previous_ms'],80)
        self.assertEqual(context['source_pts_gap_from_previous_ms'],16.667)
        self.assertNotIn('source_capture_gap_from_previous_ms',context)
        self.assertEqual(context['packetizer_emitted_shards'],117)
        self.assertFalse(context['packetizer_media_data_complete'])

    def test_inbox_eviction_is_read_from_actual_nested_counter(self):
        report,surface=self.fixture()
        report['phone']['video_input_queue']={'epoch_events_evicted':7}
        self.assertEqual(analyze(report,surface)['inbox_events_evicted'],7)

    def test_missing_events_explicit(self):
        report, surface = self.fixture()
        report['phone'].pop('native_frame_events')
        report['phone'].pop('inbox_epoch_events')
        value = analyze(report, surface)
        self.assertFalse(value['diagnostic_events_available'])
        self.assertEqual(value['stalls_count'], 1)

    def test_invalid_surface_timestamp_rejected(self):
        report, surface = self.fixture()
        for values in ([1, 1], [True], [2, 1], [-1]):
            with self.assertRaises(ValueError):
                analyze(report, {'presentation_ns': values})


if __name__ == '__main__':
    unittest.main()
