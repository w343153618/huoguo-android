"""Pure SF ring-continuity fixtures; no ADB, screen, device or invented frames."""
import json
import unittest

from scripts.probes.measure_surface_cadence import (
    INT64_MAX, RingCoverage, parse_ring, enough_tail_budget, next_poll_budget_ms)


def ring(stamps, capacity=8):
    return parse_ring('16666666\n'+''.join('1 '+str(value)+' 1\n' for value in stamps)
                      +'0 0 0\n'*max(0,capacity-len(stamps)))


class SurfaceCadenceCoverageChecks(unittest.TestCase):
    def test_baseline_excludes_history_and_overlap_tracks_only_observed_unique_endpoints(self):
        coverage=RingCoverage()
        first=coverage.observe(ring([10,20]),elapsed_ms=10,list_ms=3,latency_ms=5)
        self.assertEqual(first['continuity_status_code'],1)
        self.assertEqual(first['new_presentations'],0)
        row=coverage.observe(ring([10,20,30,40]),elapsed_ms=510,list_ms=4,latency_ms=6)
        self.assertEqual(row['ring_overlap_presentations'],2)
        self.assertEqual(row['previous_actual_last_ns'],20)
        self.assertEqual(row['current_actual_first_ns'],10)
        self.assertEqual(row['new_presentations'],2)
        self.assertEqual(coverage.seen,{30,40})
        self.assertEqual(coverage.summary()['continuity_complete_observed'],1)
        self.assertEqual(coverage.summary()['observed_slots_max'],8)

    def test_low_fps_static_ring_is_continuous_not_sampling_failure(self):
        coverage=RingCoverage()
        coverage.observe(ring([100]),elapsed_ms=10)
        idle=coverage.observe(ring([100]),elapsed_ms=2010)
        self.assertEqual(idle['idle_ring'],1)
        self.assertEqual(idle['new_presentations'],0)
        self.assertEqual(idle['continuity_status_code'],2)
        next_row=coverage.observe(ring([100,200]),elapsed_ms=3010)
        self.assertEqual(next_row['new_presentations'],1)
        self.assertEqual(coverage.summary()['sampling_invalid_observed'],0)
        self.assertEqual(coverage.summary()['continuity_complete_observed'],1)

    def test_disjoint_nonfull_sparse_ring_is_unverified_not_auto_invalid_or_lost_frames(self):
        coverage=RingCoverage()
        coverage.observe(ring([100],capacity=128),elapsed_ms=10)
        row=coverage.observe(ring([200],capacity=128),elapsed_ms=510)
        self.assertEqual(row['continuity_status_code'],4)
        self.assertEqual(row['ring_overlap_presentations'],0)
        self.assertEqual(row['previous_actual_last_ns'],100)
        self.assertEqual(row['current_actual_first_ns'],200)
        self.assertEqual(coverage.seen,{200})
        summary=coverage.summary()
        self.assertEqual(summary['continuity_unverified'],1)
        self.assertEqual(summary['sampling_invalid_observed'],0)
        self.assertEqual(summary['full_ring_no_overlap_risk'],0)
        self.assertNotIn('lost_frames',json.dumps(summary))

    def test_disjoint_full_ring_marks_possible_overwritten_history_without_guessing_missing_count(self):
        coverage=RingCoverage()
        coverage.observe(ring([10,20,30,40],capacity=4),elapsed_ms=10)
        row=coverage.observe(ring([50,60,70,80],capacity=4),elapsed_ms=510)
        self.assertEqual(row['continuity_status_code'],3)
        summary=coverage.summary()
        self.assertEqual(summary['full_ring_no_overlap_risk'],1)
        self.assertEqual(summary['continuity_complete_observed'],0)
        self.assertEqual(summary['sampling_invalid_observed'],0)
        self.assertEqual(coverage.seen,{50,60,70,80})

    def test_layer_missing_and_selected_layer_changed_do_not_join_another_surface(self):
        for state,code,key in ((0,6,'layer_missing_polls'),(2,7,'layer_changed_polls')):
            with self.subTest(state=state):
                coverage=RingCoverage()
                coverage.observe(ring([10,20]),elapsed_ms=10)
                row=coverage.observe(None,elapsed_ms=510,layer_state=state)
                self.assertEqual(row['continuity_status_code'],code)
                self.assertEqual(coverage.seen,set())
                coverage.observe(ring([10,20,30]),elapsed_ms=1010)
                self.assertEqual(coverage.seen,{30})
                summary=coverage.summary()
                self.assertEqual(summary[key],1)
                self.assertEqual(summary['same_selected_layer_all_polls'],0)
                self.assertEqual(summary['sampling_invalid_observed'],1)

    def test_blocked_call_with_overlap_preserves_observed_history_but_timeout_is_explicit_failure(self):
        coverage=RingCoverage()
        coverage.observe(ring([10,20]),elapsed_ms=10)
        row=coverage.observe(ring([10,20,30]),elapsed_ms=1410,list_ms=1100,latency_ms=30)
        self.assertEqual(row['blocked_adb_call'],1)
        self.assertEqual(row['continuity_status_code'],2)
        self.assertEqual(coverage.summary()['continuity_complete_observed'],1)
        self.assertEqual(coverage.summary()['blocked_adb_calls'],1)
        timeout=coverage.observe(None,elapsed_ms=3410,list_ms=2000,adb_error=1)
        self.assertEqual(timeout['continuity_status_code'],8)
        self.assertEqual(timeout['adb_error_code'],1)
        self.assertEqual(coverage.summary()['sampling_invalid_observed'],1)
        self.assertEqual(coverage.seen,{30})

    def test_empty_ring_does_not_create_zero_fps_acceptance_and_next_poll_reuses_baseline_only_if_valid(self):
        coverage=RingCoverage()
        row=coverage.observe(ring([]),elapsed_ms=10)
        self.assertEqual(row['continuity_status_code'],5)
        self.assertEqual(coverage.summary()['baseline_observed'],0)
        coverage.observe(ring([10,20]),elapsed_ms=510)
        self.assertEqual(coverage.seen,set())
        self.assertEqual(coverage.summary()['continuity_complete_observed'],0)
        self.assertEqual(coverage.summary()['empty_ring_polls'],1)

    def test_timestamp_regression_and_malformed_rows_are_invalid_without_publishing_bad_history(self):
        coverage=RingCoverage()
        coverage.observe(ring([100,200]),elapsed_ms=10)
        row=coverage.observe(ring([10,20]),elapsed_ms=510)
        self.assertEqual(row['continuity_status_code'],10)
        bad=parse_ring('16666666\nprivate text\n1 300 1\n')
        row=coverage.observe(bad,elapsed_ms=1010)
        self.assertEqual(row['continuity_status_code'],9)
        self.assertEqual(row['malformed_rows'],1)
        self.assertEqual(coverage.seen,set())
        self.assertEqual(coverage.summary()['sampling_invalid_observed'],1)

    def test_parse_bounds_pending_and_zero_slots_do_not_invent_actual_presentations(self):
        parsed=parse_ring(f'16666666\n1 {INT64_MAX} 0\n0 0 0\n1 10 1\n1 10 1\n')
        self.assertEqual(parsed['slots'],4)
        self.assertEqual(parsed['pending_slots'],1)
        self.assertEqual(parsed['stamps'],{10})
        self.assertEqual(parse_ring('private header\n1 10 1\n')['header_valid'],0)
        self.assertEqual(parse_ring(f'16666666\n1 {INT64_MAX+1} 1\n')['malformed_rows'],1)
        for raw in ('1\n'+'0 0 0\n'*257,'x'*65537,None):
            with self.subTest(raw_type=type(raw).__name__),self.assertRaises(ValueError):
                parse_ring(raw)

    def test_numeric_poll_boundaries_fail_closed_and_export_only_fixed_fields(self):
        for kwargs in ({'elapsed_ms':True},{'elapsed_ms':float('nan')},
                {'elapsed_ms':float('inf')},{'elapsed_ms':-1},
                {'elapsed_ms':1,'latency_ms':-1},{'elapsed_ms':1,'list_ms':float('nan')},
                {'elapsed_ms':1,'layer_state':True},{'elapsed_ms':1,'adb_error':3}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):
                RingCoverage().observe(ring([10]),**kwargs)
        coverage=RingCoverage();coverage.observe(ring([10]),elapsed_ms=10)
        with self.assertRaises(ValueError):coverage.observe(ring([20]),elapsed_ms=9)
        self.assertTrue(all(type(value) in (int,float) for value in coverage.rows[0].values()))
        self.assertTrue(all(type(value) in (int,float) for value in coverage.summary().values()))

    def test_b3_boundary_timeout_fixture_distinguishes_unknown_tail_from_earlier_real_failure(self):
        # Closed numeric regression input from the B3 timing boundary. The
        # frozen old sampler started a fresh call with only 59ms left and
        # recorded its timeout as invalid. The new scheduler PREVENTS that
        # call, but cannot reclassify old evidence or invent tail samples.
        deadline_ms=30000
        last_valid_ms=29441
        next_call_ms=last_valid_ms+500
        timeout_finished_ms=30006
        remaining_ms=deadline_ms-next_call_ms
        self.assertEqual(remaining_ms,59)
        self.assertEqual(deadline_ms-last_valid_ms,559)
        self.assertEqual(timeout_finished_ms-next_call_ms,65)
        coverage=RingCoverage()
        coverage.observe(ring([10],capacity=128),elapsed_ms=100)
        for index in range(1,45):
            coverage.observe(ring(range(10,11+index),capacity=128),
                elapsed_ms=100+(last_valid_ms-100)*index/44,
                list_ms=40,latency_ms=60)
        self.assertEqual(coverage.summary()['overlap_comparisons'],44)
        self.assertEqual(coverage.summary()['continuity_complete_observed'],1)
        observed_before_tail=set(coverage.seen)
        self.assertFalse(enough_tail_budget(remaining_ms,[100,100,100,100]))
        self.assertEqual(next_poll_budget_ms([100,100,100,100]),200)
        # Skipping creates no new poll or presentation: the continuous
        # prefix survives, while the final 559ms remain explicitly unknown.
        self.assertEqual(len(coverage.rows),45)
        self.assertEqual(coverage.seen,observed_before_tail)
        # Replaying the old observed timeout must remain invalid even after
        # the scheduler fix. It is not retroactively a skipped observation.
        row=coverage.observe(None,elapsed_ms=timeout_finished_ms,
                             list_ms=timeout_finished_ms-next_call_ms,adb_error=1)
        self.assertEqual(row['continuity_status_code'],8)
        self.assertEqual(coverage.summary()['sampling_invalid_observed'],1)
        self.assertEqual(coverage.seen,observed_before_tail)
        # A real timeout launched much earlier has ample deadline budget and
        # must stay invalid after any tail-start prevention is introduced.
        earlier=RingCoverage();earlier.observe(ring([10]),elapsed_ms=100)
        earlier_started_ms=10000
        self.assertGreater(deadline_ms-earlier_started_ms,remaining_ms)
        self.assertTrue(enough_tail_budget(deadline_ms-earlier_started_ms,[100,100,100,100]))
        early_row=earlier.observe(None,elapsed_ms=18000,list_ms=8000,adb_error=1)
        self.assertEqual(early_row['continuity_status_code'],8)
        self.assertEqual(earlier.summary()['sampling_invalid_observed'],1)

    def test_tail_budget_is_bounded_conservative_and_uses_only_four_recent_calls(self):
        self.assertEqual(next_poll_budget_ms([]),200)
        self.assertEqual(next_poll_budget_ms([1,50,70]),200)
        self.assertEqual(next_poll_budget_ms([101.1,110]),220)
        self.assertEqual(next_poll_budget_ms([250]),500)
        self.assertEqual(next_poll_budget_ms([8000]),500)
        self.assertEqual(next_poll_budget_ms([8000,1,2,3,4]),200)
        self.assertFalse(enough_tail_budget(199.999,[]))
        self.assertTrue(enough_tail_budget(200,[]))
        self.assertFalse(enough_tail_budget(499.999,[8000]))
        self.assertTrue(enough_tail_budget(500,[8000]))

    def test_tail_budget_rejects_non_numeric_invalid_time_without_touching_coverage(self):
        for value in (True,'200',None,-1,float('nan'),float('inf')):
            with self.subTest(value=value),self.assertRaises(ValueError):
                enough_tail_budget(value,[])
        for value in (True,'200',None,-1,float('nan'),float('inf')):
            with self.subTest(value=value),self.assertRaises(ValueError):
                next_poll_budget_ms([100,value])


if __name__=='__main__':
    unittest.main()
