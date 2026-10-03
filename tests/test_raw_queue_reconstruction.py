"""Bounded inert replay fixtures; no phone, listeners, credentials or media."""
import json
from pathlib import Path
import tempfile
import unittest

from host_timing_trace import FIELDS
from scripts.probes import host_timing_analysis as host
from scripts.probes import raw_queue_reconstruction as queue


B = 1_000_000_000


def row(event, **values):
    return dict(schema='capture-vt-trace-v1', process='python', event=event, **values)


def enqueue(seq, ms, depth=1, replaced=0, pts=None):
    return row('capture_enqueue', capture_seq=seq, source_pts_us=seq if pts is None else pts,
        screenshot_seq=seq, grpc_return_ns=B+ms*1_000_000-1,
        enqueue_ns=B+ms*1_000_000, width=2, height=2, raw_bytes=16,
        pending_count=depth, replaced_capture_seq=replaced)


def dequeue(seq, ms, iteration, pts=None, drop=False, wait=None):
    pts = seq if pts is None else pts
    at = B+ms*1_000_000
    loop = dict.fromkeys(FIELDS['raw_loop'], 0)
    loop.update(iteration=iteration, capture_seq=seq, source_pts_us=pts,
        dequeue_ns=at, outcome=3 if drop else 1)
    budget = dict.fromkeys(FIELDS['raw_budget'], 0)
    budget['iteration'] = iteration
    if wait:
        budget.update(requested_wait_ns=(wait[1]-wait[0])*1_000_000,
            wait_begin_ns=B+wait[0]*1_000_000, wait_end_ns=B+wait[1]*1_000_000)
    removal = row('raw_drop', capture_seq=seq, source_pts_us=pts, dequeue_ns=at,
        reason='nonincreasing_source_pts') if drop else row('raw_submit',
        capture_seq=seq, source_pts_us=pts, dequeue_ns=at,
        pipe_write_begin_ns=at+1, pipe_write_end_ns=at+2,
        raw_bytes=16, skipped_frames=0, idle_repeat=False)
    return [removal, row('raw_loop', **loop), row('raw_budget', **budget)]


def complete(events):
    start = row('trace_clock', phase='start', clock_domain=host.CLOCK,
        clock_before_ns=B, clock_after_ns=B+1, unix_ns=B)
    end = row('trace_clock', phase='end', clock_domain=host.CLOCK,
        clock_before_ns=B+1_000_000_000, clock_after_ns=B+1_000_000_001, unix_ns=B+1)
    rows = [start, row('host_timing_contract', **host.CONTRACT), *events,
        row('host_timing_summary', clock_errors=0, emit_errors=0, schema_errors=0,
            emit_attempts=len(events), producer_quiescent=True), end]
    return rows+[row('trace_summary', accepted_records=len(rows), written_records=len(rows),
        dropped_records=0, byte_capped=False, clock_errors=0, record_limit=24000,
        byte_limit=host.MAX_BYTES, failed=False, clean_close=True)]


class RawQueueReconstructionChecks(unittest.TestCase):
    def analyze(self, events, mutate=None, window=(B+1, B+900_000_000)):
        rows = complete(events)
        if mutate:
            mutate(rows)
        with tempfile.TemporaryDirectory() as name:
            path = Path(name)/'capture.jsonl'
            path.write_text(''.join(json.dumps(x)+'\n' for x in rows))
            return queue.reconstruct(host.read_stream(path, 'capture'), window)

    def normal(self):
        return [enqueue(1, 10), enqueue(2, 40, depth=2),
            *dequeue(1, 42, 1, wait=(12, 41)), *dequeue(2, 73, 2)]

    def assert_refused(self, result, reason):
        self.assertFalse(result['reconstructed'])
        self.assertGreater(result['rejections'].get(reason, 0), 0)
        self.assertNotIn('distributions_ms', result)

    def test_fifo2_reconstruction_local_newest_keeps_historical_next_decision(self):
        result = self.analyze(self.normal())
        self.assertTrue(result['reconstructed'])
        self.assertEqual(result['pending_at_dequeue'], {'1': 1, '2': 1})
        self.assertEqual(result['window_decisions'], 2)
        self.assertEqual(result['decision_local_newest_differs'], 1)
        d = result['distributions_ms']
        self.assertEqual(d['observed_age_ms']['p50'], 32.5)
        self.assertEqual(d['different_newest_age_ms']['p50'], 2.)
        self.assertEqual(d['different_newest_age_difference_ms']['p50'], 30.)
        self.assertEqual(d['newest_age_at_same_dequeue_ms']['p50'], 17.5)
        self.assertEqual(d['age_budget_wait_overlap_ms']['max'], 29.)
        self.assertEqual(result['decision_local_age_difference_ms']['mean'], 15.)
        self.assertEqual(result['final_pending_depth'], 0)
        self.assertFalse(result['alternative_is_dynamic_replay'])
        self.assertFalse(result['alternative_is_performance_prediction'])
        self.assertFalse(result['whole_pipeline_coverage'])

    def test_trace_emission_order_does_not_define_condition_lock_order(self):
        events = self.normal()
        result = self.analyze([*events[2:], *events[:2]])
        self.assertTrue(result['reconstructed'])
        self.assertEqual(result['window_decisions'], 2)

    def test_capacity_replacement_and_nonincreasing_pts_drop_are_recorded(self):
        events = [enqueue(1, 10), enqueue(2, 20, depth=2),
            enqueue(3, 30, depth=2, replaced=1, pts=1),
            *dequeue(2, 40, 1), *dequeue(3, 50, 2, pts=1, drop=True)]
        result = self.analyze(events)
        self.assertTrue(result['reconstructed'])
        self.assertEqual(result['total_replaced_enqueues'], 1)
        self.assertEqual(result['window_dropped_before_encode'], 1)

    def test_window_excludes_boundary_crossing_but_full_trace_reconstructs_queue(self):
        result = self.analyze(self.normal(), window=(B+30_000_000, B+80_000_000))
        self.assertTrue(result['reconstructed'])
        self.assertEqual(result['window_decisions'], 1)
        self.assertEqual(result['window_dequeues_excluded'], 1)
        self.assertEqual(result['decision_local_newest_differs'], 0)

    def test_final_pending_depth_is_reported_not_inferred_submitted(self):
        result = self.analyze([enqueue(1, 10)])
        self.assertTrue(result['reconstructed'])
        self.assertEqual(result['final_pending_depth'], 1)
        self.assertEqual(result['window_decisions'], 0)

    def test_duplicate_or_missing_capture_sequence_refused(self):
        for seq in (1, 3):
            with self.subTest(seq=seq):
                events = self.normal()
                events[1]['capture_seq'] = seq
                self.assert_refused(self.analyze(events), 'capture_sequence_missing_or_duplicate')

    def test_wrong_fifo_selection_or_source_pts_refused(self):
        for key, value in (('capture_seq', 2), ('source_pts_us', 99)):
            with self.subTest(key=key):
                events = self.normal()
                events[2][key] = value
                events[3][key] = value
                self.assert_refused(self.analyze(events), 'fifo_selection_identity_conflict')

    def test_missing_duplicate_dequeue_or_loop_refused(self):
        for events in (self.normal()[1:], self.normal()+dequeue(1, 42, 1),
                       [x for x in self.normal() if x['event'] != 'raw_loop']):
            with self.subTest(events=events):
                self.assertFalse(self.analyze(events)['reconstructed'])

    def test_equal_timestamps_do_not_guess_lock_order(self):
        events = [enqueue(1, 10), *dequeue(1, 10, 1)]
        self.assert_refused(self.analyze(events), 'ambiguous_queue_operation_timestamp')

    def test_queue_depth_or_replacement_conflict_refused(self):
        for key, value, reason in (('pending_count', 1, 'pending_depth_conflict'),
                ('replaced_capture_seq', 1, 'replacement_identity_conflict')):
            events = self.normal()
            events[1][key] = value
            self.assert_refused(self.analyze(events), reason)

    def test_invalid_rgba_shape_refused(self):
        events = self.normal()
        events[0]['raw_bytes'] = 15
        self.assert_refused(self.analyze(events), 'enqueue_identity_or_shape_invalid')

    def test_nonfifo_skip_and_failed_raw_loop_refused(self):
        for key, value in (('skipped', 1), ('outcome', 6)):
            events = self.normal()
            events[3][key] = value
            self.assert_refused(self.analyze(events), 'unsupported_or_failed_raw_loop')

    def test_future_wait_end_cannot_be_overlap(self):
        events = self.normal()
        events[4]['wait_end_ns'] = B+43_000_000
        self.assert_refused(self.analyze(events), 'budget_wait_endpoints_invalid')

    def test_raw_drop_cannot_use_successful_submit_outcome(self):
        events = [enqueue(1, 10), *dequeue(1, 40, 1, drop=True)]
        events[2]['outcome'] = 1
        self.assert_refused(self.analyze(events), 'dequeue_outcome_conflict')

    def test_duplicate_budget_or_loop_and_missing_budget_refused(self):
        for events in (self.normal()+[self.normal()[4]],
                       self.normal()+[self.normal()[3]],
                       [x for x in self.normal() if x['event'] != 'raw_budget']):
            with self.subTest(events=events):
                self.assertFalse(self.analyze(events)['reconstructed'])

    def test_pipe_timestamp_shape_and_idle_identity_refused(self):
        for key, value, reason in (
                ('pipe_write_end_ns', B, 'unsupported_or_invalid_submit'),
                ('idle_repeat', True, 'unsupported_or_invalid_submit'),
                ('raw_bytes', 20, 'dequeue_shape_conflict')):
            events = self.normal()
            events[2][key] = value
            self.assert_refused(self.analyze(events), reason)

    def test_incomplete_coverage_unknown_not_zero_queue_delay(self):
        def mutate(rows):
            rows[-1]['dropped_records'] = 1
        self.assert_refused(self.analyze(self.normal(), mutate), 'capture_coverage_not_complete')

    def test_foreign_schema_or_clock_refused_by_existing_bounded_reader(self):
        def mutate(rows):
            rows[0]['clock_domain'] = 'CLOCK_UPTIME_RAW'
        self.assert_refused(self.analyze(self.normal(), mutate), 'capture_coverage_not_complete')
        def foreign(rows):
            rows[2]['password'] = 'not-a-real-password'
        result = self.analyze(self.normal(), foreign)
        self.assert_refused(result, 'capture_coverage_not_complete')
        self.assertNotIn('password', json.dumps(result))

    def test_invalid_window_refused(self):
        for window in ((B, B), (True, B), (B+2, B+1), (B, float(B+100)), (0, B)):
            with self.subTest(window=window), self.assertRaises(ValueError):
                self.analyze(self.normal(), window=window)

    def test_window_outside_declared_clock_bracket_refused(self):
        self.assert_refused(self.analyze(self.normal(), window=(B-1, B+80_000_000)),
            'window_outside_clock_bracket')


if __name__ == '__main__':
    unittest.main()
