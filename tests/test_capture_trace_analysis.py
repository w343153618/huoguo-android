import json
from pathlib import Path
import tempfile
import unittest
from scripts.probes.capture_trace_analysis import sanitize, read_sanitized_trace, analyze_trace


class TraceAnalysisCheck(unittest.TestCase):
    def row(self, event, **fields):
        return {'schema':'capture-vt-trace-v1','process':'python','event':event, **fields}

    def test_whitelist(self):
        row = sanitize(self.row('capture_enqueue', source_pts_us=9, grpc_return_ns=12,
                                password='private', clock_domain='arbitrary', au_bytes=True))
        self.assertEqual(row['source_pts_us'],9)
        self.assertNotIn('password',row)
        self.assertNotIn('clock_domain',row)
        self.assertNotIn('au_bytes',row)
        self.assertIsNone(sanitize({'schema':'other','event':'capture_enqueue'}))

    def test_same_pts_join_and_clock_intervals(self):
        records = [self.row('capture_enqueue',source_pts_us=9,capture_seq=1,grpc_return_ns=1000,enqueue_ns=2000),
                   self.row('raw_submit',source_pts_us=9,capture_seq=1,dequeue_ns=3000,pipe_write_begin_ns=4000,pipe_write_end_ns=8000),
                   self.row('vt_frame',source_pts_us=9,vt_submit_ns=8000,vt_callback_ns=13000),
                   self.row('encoded_egress',source_pts_us=9,socket_write_end_ns=14000),
                   self.row('vt_frame',source_pts_us=10,vt_submit_ns=9999999,vt_callback_ns=20000000)]
        result = analyze_trace({'records':records})
        self.assertEqual(result['stage_ms']['vt_submit_to_callback_ms']['count'],1)
        self.assertEqual(result['stage_ms']['grpc_return_to_encoded_socket_end_ms']['p50'],.013)
        self.assertEqual(result['partial_joined_frames'],1)

    def test_negative_intervals_not_clamped(self):
        result = analyze_trace({'records':[self.row('capture_enqueue',source_pts_us=9,capture_seq=1,
                                                      grpc_return_ns=1000,enqueue_ns=500),
                                           self.row('raw_submit',source_pts_us=9,capture_seq=1)]})
        self.assertEqual(result['invalid_clock_intervals'],1)
        self.assertEqual(result['stage_ms']['enqueue_after_grpc_ms']['count'],0)

    def test_sanitize_file_no_raw_strings(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'capture.jsonl'
            path.write_text(json.dumps(self.row('raw_drop',source_pts_us=9,password='never'))+'\ninvalid\n')
            result=read_sanitized_trace([path,Path(directory)/'missing.native.jsonl'])
            self.assertNotIn('never',json.dumps(result))
            self.assertEqual(result['status'][0]['malformed'],1)
            self.assertFalse(result['status'][1]['available'])

    def test_repeated_capture_pts_selects_submitted_sequence(self):
        result=analyze_trace({'records':[
            self.row('capture_enqueue',source_pts_us=9,capture_seq=1,grpc_return_ns=1000,enqueue_ns=1500),
            self.row('capture_enqueue',source_pts_us=9,capture_seq=2,grpc_return_ns=5000,enqueue_ns=5500),
            self.row('raw_submit',source_pts_us=9,capture_seq=2,dequeue_ns=6500)]})
        self.assertEqual(result['stage_ms']['raw_queue_ms']['p50'],.001)
        self.assertEqual(result['slowest_local_frames'][0]['capture_seq'],2)

    def test_cross_process_clock_required(self):
        rows=[self.row('capture_enqueue',source_pts_us=9,capture_seq=1,grpc_return_ns=1000),
              self.row('raw_submit',source_pts_us=9,capture_seq=1),
              self.row('vt_frame',source_pts_us=9,vt_submit_ns=2000,vt_callback_ns=5000)]
        result=analyze_trace({'records':rows})
        self.assertFalse(result['common_host_clock_verified'])
        self.assertEqual(result['stage_ms']['grpc_return_to_vt_callback_ms']['count'],0)
        rows.extend([self.row('trace_clock',clock_domain='host_clock_gettime_CLOCK_MONOTONIC_ns'),
                     dict(self.row('trace_clock',clock_domain='host_clock_gettime_CLOCK_MONOTONIC_ns'),process='swift')])
        result=analyze_trace({'records':rows})
        self.assertTrue(result['common_host_clock_verified'])
        self.assertEqual(result['stage_ms']['grpc_return_to_vt_callback_ms']['p50'],.004)

    def test_screenshot_sequence_distinguishes_stream_skip_from_local_sequence(self):
        rows = [self.row('capture_enqueue', source_pts_us=10, capture_seq=1,
                         screenshot_seq=0xfffffffe, grpc_return_ns=1000),
                self.row('capture_enqueue', source_pts_us=20, capture_seq=2,
                         screenshot_seq=1, grpc_return_ns=2000)]
        result = analyze_trace({'records': rows})
        gap = result['largest_grpc_gaps'][0]
        self.assertEqual(gap['right_capture_seq']-gap['left_capture_seq'], 1)
        self.assertEqual(gap['screenshot_sequence_delta'], 3)
        rows[1].pop('screenshot_seq')
        self.assertIsNone(analyze_trace({'records': rows})['largest_grpc_gaps'][0]['screenshot_sequence_delta'])


if __name__ == '__main__':
    unittest.main()
