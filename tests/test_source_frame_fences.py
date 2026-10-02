import os
import unittest

from scripts.probes.measure_source_frame_fences import (
    FrameCollector, INT64_MAX, clock_mapping_sample, layer_identity, parse_latency,
    select_layer, read_bounded_line, host_clock_ns,
)


class SourceFenceParsingTests(unittest.TestCase):
    def test_column_order_and_sentinels_retained(self):
        data=parse_latency(f'16666666\n10 20 15\n30 {INT64_MAX} 0\n-1 0 {INT64_MAX}\n')
        self.assertEqual(data['display_vsync_ns'],16666666)
        self.assertEqual(data['rows'][0]['desired_present_ns'],10)
        self.assertEqual(data['rows'][0]['actual_present_ns'],20)
        self.assertEqual(data['rows'][0]['frame_ready_ns'],15)
        self.assertFalse(data['rows'][1]['actual_present_valid'])
        self.assertEqual(data['rows'][1]['actual_present_invalid_reason'],'int64_max_pending_or_unsignaled')
        self.assertEqual(data['rows'][1]['frame_ready_ns'],0)
        self.assertEqual(data['rows'][2]['desired_present_invalid_reason'],'negative_or_out_of_range')

    def test_malformed_lines_do_not_enter_numeric_report(self):
        data=parse_latency('16666666\nprivate unrelated text\n1 2\n1 2 3 4\n1 2 not_a_timestamp\n1 2 3\n')
        self.assertEqual(data['malformed_rows'],4)
        self.assertEqual(len(data['rows']),1)

    def test_invalid_period_and_out_of_range_timestamps(self):
        data=parse_latency(f'0\n1 {INT64_MAX+1} 3\n')
        self.assertIsNone(data['display_vsync_ns'])
        self.assertEqual(data['malformed_rows'],1)
        self.assertEqual(data['rows'],[])

    def test_ring_size_bounded(self):
        with self.assertRaisesRegex(ValueError,'latency_ring_size'):
            parse_latency('16666666\n'+'1 2 3\n'*257)

    def test_fixed_exact_layer_generation(self):
        pkg='app.morphe.android.youtube'
        layer=f'123abc SurfaceView[{pkg}/Player](BLAST)#160'
        wrapped=f'RequestedLayerState{{{layer} parentId=14 foo=1}}'
        self.assertEqual(select_layer(wrapped,pkg),layer)
        self.assertIsNone(select_layer(f'abc SurfaceView[{pkg}.other/Player](BLAST)#99',pkg))
        fresh=f'123abc SurfaceView[{pkg}/Player](BLAST)#161'
        self.assertEqual(select_layer(layer+'\n'+fresh,pkg),fresh)
        self.assertNotEqual(layer_identity(layer)['sha256'],layer_identity(fresh)['sha256'])
        self.assertEqual(layer_identity(layer)['generation_suffix'],160)


class FenceCollectorTests(unittest.TestCase):
    def test_excludes_historical_baseline_resolves_ready_and_keeps_invalid(self):
        collector=FrameCollector()
        collector.accept(parse_latency('16666666\n10 20 15\n'),0)
        collector.accept(parse_latency(f'16666666\n10 20 15\n30 40 {INT64_MAX}\n50 {INT64_MAX} 0\n'),1)
        collector.accept(parse_latency('16666666\n30 40 35\n'),2)
        result=collector.report()
        self.assertEqual(result['baseline_actual_max_ns'],20)
        self.assertEqual(result['summary']['actual_unique_present_times'],1)
        self.assertEqual(result['frame_records'][0]['frame_ready_ns'],35)
        self.assertTrue(result['frame_records'][0]['ready_invalid_seen'])
        self.assertEqual(result['frame_records'][0]['first_poll_index'],1)
        self.assertEqual(result['frame_records'][0]['last_poll_index'],2)
        self.assertEqual(result['invalid_actual_rows'][0]['actual_present_ns'],INT64_MAX)
        self.assertEqual(result['summary']['ready_to_actual_present_ms']['p50_ms'],.000005)

    def test_duplicate_ring_not_double_counted_and_metadata_limits_recorded(self):
        collector=FrameCollector(max_frames=1,max_invalid=1)
        collector.accept(parse_latency('16666666\n1 2 1\n'),0)
        later=parse_latency(f'16666666\n3 4 3\n5 6 5\n7 {INT64_MAX} 0\n8 {INT64_MAX} 0\n')
        collector.accept(later,1);collector.accept(later,2)
        result=collector.report()
        self.assertEqual(len(result['frame_records']),1)
        self.assertEqual(len(result['invalid_actual_rows']),1)
        self.assertGreater(result['dropped_metadata']['frames'],0)
        self.assertGreater(result['dropped_metadata']['invalid_actual_rows'],0)

    def test_invalid_initial_ring_not_mistaken_for_measurement_start(self):
        collector=FrameCollector()
        first=collector.accept(parse_latency(f'16666666\n1 {INT64_MAX} 0\n'),0)
        self.assertTrue(first['baseline_only'])
        self.assertIsNone(collector.baseline_max)
        collector.accept(parse_latency('16666666\n1 2 1\n'),1)
        self.assertEqual(collector.baseline_max,2)
        self.assertEqual(collector.report()['frame_records'],[])

    def test_conflicting_same_actual_timestamp_not_used_as_one_frame_latency(self):
        collector=FrameCollector()
        collector.accept(parse_latency('16666666\n1 2 1\n'),0)
        collector.accept(parse_latency('16666666\n3 4 3\n'),1)
        collector.accept(parse_latency('16666666\n5 4 3\n'),2)
        result=collector.report()
        self.assertEqual(result['summary']['actual_unique_present_times'],1)
        self.assertEqual(result['summary']['ready_identity_conflict_count'],1)
        self.assertEqual(result['summary']['ready_to_actual_comparable_count'],0)


class GuestClockBoundTests(unittest.TestCase):
    def test_cross_machine_offset_bounds_retain_full_roundtrip(self):
        result=clock_mapping_sample({'monotonic_us':1000,'unix_us':2000000,'sample_span_us':2},10000000,10010000)
        self.assertEqual(result['guest_to_host_monotonic_offset_bounds_ns'],[8998000,9012000])
        self.assertEqual(result['guest_realtime_precision_ns'],1000000)
        self.assertFalse(result['offset_is_exact'])
        self.assertFalse(result['guest_boottime_available'])

    def test_clock_untrusted_or_reversed_samples_rejected(self):
        for sample in ({'monotonic_us':True,'unix_us':1,'sample_span_us':1},
                       {'monotonic_us':1,'unix_us':1,'sample_span_us':5001},
                       {'monotonic_us':-1,'unix_us':1,'sample_span_us':1}):
            with self.assertRaises(ValueError):clock_mapping_sample(sample,10,20)
        with self.assertRaisesRegex(ValueError,'host_clock_order'):
            clock_mapping_sample({'monotonic_us':1,'unix_us':1,'sample_span_us':1},20,10)

    def test_partial_clock_line_cannot_block_past_deadline(self):
        read_fd,write_fd=os.pipe()
        try:
            os.write(write_fd,b'{"monotonic_us":')
            with self.assertRaises(TimeoutError):
                read_bounded_line(read_fd,host_clock_ns()+2_000_000)
        finally:
            os.close(read_fd);os.close(write_fd)

    def test_only_one_bounded_clock_reply_accepted(self):
        read_fd,write_fd=os.pipe()
        try:
            os.write(write_fd,b'{}\n{}\n')
            with self.assertRaisesRegex(ValueError,'unexpected_multiple_clock_replies'):
                read_bounded_line(read_fd,host_clock_ns()+2_000_000)
        finally:
            os.close(read_fd);os.close(write_fd)


if __name__=='__main__':
    unittest.main()
