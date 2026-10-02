import json
from pathlib import Path
import tempfile
import unittest

from scripts.probes.analyze_source_fence_gaps import clock_model, same_guest_pair, summarize_fences, summarize_perfetto


def row(actual, desired=None, ready=None, conflict=False):
    return {'actual_present_ns':actual,'desired_present_ns':desired if desired is not None else actual-10_000_000,
            'frame_ready_ns':ready if ready is not None else actual-40_000_000,
            'frame_ready_valid':True,'desired_present_valid':True,
            'desired_conflict':conflict,'ready_conflict':False}


class FenceGapAnalysisTests(unittest.TestCase):
    def test_missing_and_inconsistent_clock_bounds_reject(self):
        with self.assertRaises(ValueError):
            clock_model({'guest_clock_queries':[]})
        with self.assertRaises(ValueError):
            clock_model({'guest_clock_queries':[{'available':True,'guest_to_host_monotonic_offset_bounds_ns':[1,2]},
                                                {'available':True,'guest_to_host_monotonic_offset_bounds_ns':[3,4]}]})

    def test_deadline_miss_is_distinct_from_intentional_desired_wait(self):
        a=row(100_000_000,90_000_000,50_000_000)
        intentional=row(300_000_000,300_000_000,60_000_000)
        missed=row(300_000_000,110_000_000,60_000_000)
        self.assertEqual(same_guest_pair(a,intentional)['right_actual_minus_desired_ms'],0)
        self.assertEqual(same_guest_pair(a,missed)['right_actual_minus_desired_ms'],190)
        self.assertEqual(same_guest_pair(a,missed)['right_desired_minus_ready_ms'],50)

    def test_conflict_preserved_but_disallows_latency_interpretation(self):
        result=same_guest_pair(row(100),row(200,conflict=True))
        self.assertTrue(result['right_identity_conflict'])
        self.assertFalse(result['triplet_latency_interpretation_allowed'])

    def test_pending_ready_sentinel_cannot_become_latency(self):
        pending=row(200,ready=2**63-1)
        pending['frame_ready_valid']=False
        result=same_guest_pair(row(100),pending)
        self.assertFalse(result['per_frame_identity_comparable'])
        self.assertIsNone(result['ready_gap_ms'])
        self.assertIsNone(result['right_actual_minus_ready_ms'])

    def test_constant_clock_bound_marks_window_edge_uncertainty(self):
        offset=100_000_000_000
        data={'guest_clock_queries':[{'available':True,'guest_monotonic_us':0,
                                    'guest_to_host_monotonic_offset_bounds_ns':[offset-1_000_000,offset+1_000_000]}],
              'frame_records':[row(5_000_000_000),row(6_000_000_000),row(29_999_500_000,conflict=True)],
              'polls':[{'display_vsync_ns':16_666_666}], 'measurement_host_before_ns':offset,
              'measurement_seconds':45, 'status':'complete', 'complete_fixed_generation_verified':True,
              'layer_identity':{'sha256':'0'*64}}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'source-fences.json';path.write_text(json.dumps(data))
            result,_=summarize_fences(path)
        window=result['fixed_window']
        self.assertEqual(window['selected_actual_frames_model'],3)
        self.assertEqual(window['frames_definitely_inside_given_constant_model'],1)
        self.assertEqual(window['frames_possibly_inside_given_constant_model'],3)
        self.assertEqual(window['ready_to_actual_comparable_ms']['count'],2)

    def test_perfetto_snapshot_bridge_and_entry_exit_boundaries(self):
        # Trace and guest MONOTONIC differ by 50 ns. A slice can enter in the
        # display gap and return after it; counting calls is not frame identity.
        lo,hi=1_000_000_000,1_200_000_000
        codec=[{'component_id':1,'instance_id':549,'stage_id':3,'ts_ns':lo-40,'dur_ns':30},
               {'component_id':1,'instance_id':549,'stage_id':3,'ts_ns':hi-60,'dur_ns':30}]
        data={'analysis':{'clock_snapshots':[{'clock_id':3,'clock_ts_ns':0,'clock_value_ns':50},
                                           {'clock_id':6,'clock_ts_ns':0,'clock_value_ns':0}],
                          'codec_stages':codec,'thread_states':[]}}
        fences={'largest_actual_gaps':[{'actual_gap_ms':200,'left_actual_guest_monotonic_ns':lo,
                                       'right_actual_guest_monotonic_ns':hi}]}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'perfetto-analysis.json';path.write_text(json.dumps(data))
            result=summarize_perfetto(path,fences,{})
        stage=result['sf_gaps_over_100_ms_codec_time_association'][0]['codec_call_events_during_sf_gap'][0]['stages'][2]
        self.assertEqual(stage['entry_count_model'],2)
        self.assertEqual(stage['exit_count_model'],1)
        self.assertEqual(result['guest_monotonic_minus_trace_ts_ns_range'],[50,50])
        self.assertFalse(result['render_to_sf_media_pts_identity_available'])


if __name__=='__main__':
    unittest.main()
