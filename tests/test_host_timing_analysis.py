"""Actual Python numeric formatter + exact Swift-contract fixtures, offline only."""
import copy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hardware_stream import BoundedCaptureTrace
from host_timing_trace import FIELDS, HostTimingTrace
from scripts.probes import host_timing_analysis as analysis


B = 1_000_000_000
PTS = 1_700_000_000_000_100


def clock_row(phase, process='python'):
    at = B if phase=='start' else B+1_000_000_000
    return dict(schema='capture-vt-trace-v1',process=process,event='trace_clock',phase=phase,
        clock_domain=analysis.CLOCK,clock_before_ns=at,clock_after_ns=at+1,
        unix_ns=1_700_000_000_000_000_000+at,
        source_pts_scope='emulator_estimated_screenshot_generation_unix_us_not_guest_media_pts')


def native_row(event, **fields):
    return dict(schema='capture-vt-trace-v1',process='swift',event=event,**fields)


def native_events(offset=0, pts=PTS, seq=1):
    at = lambda ms:B+offset+int(ms*1e6)
    raw = dict(native_input_seq=seq,source_pts_us=pts,header_read_begin_ns=at(18.2),
        header_read_complete_ns=at(19),raw_read_begin_ns=at(19),raw_read_complete_ns=at(27),
        width=2,height=2,raw_bytes=16)
    return [native_row('raw_read',**raw),native_row('vt_frame',**raw,
        slot_wait_begin_ns=at(27),slot_wait_end_ns=at(30),pixel_conversion_begin_ns=at(30),
        pixel_conversion_end_ns=at(33),vt_submit_ns=at(33),vt_callback_ns=at(36),
        vt_status=0,frame_dropped=False,stdout_lock_wait_begin_ns=at(36),stdout_lock_acquired_ns=at(37),
        stdout_write_begin_ns=at(37),stdout_write_end_ns=at(48),au_bytes=20,keyframe=False),
        native_row('vt_submit_return',source_pts_us=pts,vt_submit_return_ns=at(40),vt_submit_status=0)]


class OwnedTraceFixture:
    """Python rows actually pass through current HostTimingTrace/BoundedCaptureTrace."""
    def __init__(self, directory):
        self.paths = dict(capture=Path(directory)/'capture.jsonl',
            native=Path(directory)/'capture.jsonl.native.jsonl',feed=Path(directory)/'feed.jsonl')
        self.make_python('capture')
        self.make_python('feed')
        rows = [clock_row('start','swift'),*native_events(),*native_events(60_000_000,PTS+33333,2),
                clock_row('end','swift')]
        rows.append(native_row('trace_summary',accepted_records=len(rows),written_records=len(rows),
            dropped_records=0,byte_capped=False,record_limit=24000,byte_limit=16*1024*1024,
            failed=False,clean_close=True))
        self.write('native',rows)

    def make_python(self, role):
        clocks = iter(({k:v for k,v in clock_row(phase).items() if k not in ('schema','process','event','phase')}
                       for phase in ('start','end')))
        with patch.object(BoundedCaptureTrace,'clock_sample',side_effect=lambda:next(clocks)):
            sink = BoundedCaptureTrace(self.paths[role])
            stamps = iter((B+10_000_000,B+30_000_000,B+70_000_000,B+90_000_000))
            diagnostic = HostTimingTrace(sink,lambda:next(stamps))
            if role=='capture':
                for n,offset in enumerate((0,60_000_000)):
                    at=lambda ms:B+offset+int(ms*1e6)
                    pts=PTS+n*33333
                    sink.emit('capture_enqueue',capture_seq=n+1,source_pts_us=pts,screenshot_seq=n+9,
                        grpc_return_ns=at(2),enqueue_ns=at(3),width=2,height=2,raw_bytes=16,
                        pending_count=1,replaced_capture_seq=0)
                    loop,budget,write=diagnostic.begin_raw()
                    loop.update(condition_begin_ns=at(11),condition_end_ns=at(12),queued_before=1,
                        queued_after=1,native_ready=True,control_count=1,control_begin_ns=at(12),
                        control_end_ns=at(13),dequeue_ns=at(17.5),capture_seq=n+1,source_pts_us=pts,outcome=1)
                    budget.update(check_begin_ns=at(13),check_end_ns=at(14),tokens_before_milli=2000,
                        tokens_after_delay_milli=1000,tokens_after_consume_milli=0,
                        updated_python_monotonic_ns=900_000_000_000_000_000,
                        requested_wait_ns=2_000_000,wait_begin_ns=at(14),wait_end_ns=at(17),
                        consume_begin_ns=at(17),consume_end_ns=at(18),consumed=True)
                    write.update(capture_seq=n+1,source_pts_us=pts,raw_bytes=16,write_begin_ns=at(18),
                        write_end_ns=at(28),flush_complete=True,idle_repeat=False)
                    diagnostic.end_raw((loop,budget,write),dict(raw_frames=1+3*n,frames_submitted=n+1,
                        pending_frames_replaced=2*n,idle_repeats=0))
                    sink.emit('encoded_egress',source_pts_us=pts,encoded_read_complete_ns=at(42),
                        socket_write_end_ns=at(47),au_bytes=20)
            else:
                for n,offset in enumerate((0,60_000_000)):
                    at=lambda ms:B+offset+int(ms*1e6)
                    for segment in range(3):
                        row=dict.fromkeys(FIELDS['feed_read'],0)
                        row.update(read_seq=n*3+segment+1,begin_ns=at(40+segment),end_ns=at(41+segment),
                            record_bytes_before=(0,4,12)[segment],record_bytes_after=(4,12,32)[segment],
                            target_bytes=(4,12,32)[segment],recv_calls=1,outcome=1,media_records=n,
                            published_bytes=n*32)
                        diagnostic.emit('feed_read',**row)
                    row=dict.fromkeys(FIELDS['feed_publish'],0)
                    row.update(publish_seq=n+1,begin_ns=at(45),end_ns=at(55),kind=4,has_source_pts=True,
                        source_pts_us=PTS+n*33333,record_bytes=32,write_calls=2,write_end_ns=at(53),
                        flush_begin_ns=at(53),flush_end_ns=at(54),written_bytes=32,
                        flush_complete=True,published=True,media_records=n+1,published_bytes=(n+1)*32)
                    diagnostic.emit('feed_publish',**row)
            diagnostic.summary(True);sink.close()

    def read(self,role): return [json.loads(line) for line in self.paths[role].read_text().splitlines()]
    def write(self,role,rows): self.paths[role].write_text(''.join(json.dumps(row,separators=(',',':'))+'\n' for row in rows))
    def mutate(self,role,callback):
        rows=self.read(role);callback(rows)
        # Repair counts for intentional synthetic record changes unless the test
        # specifically mutates counts afterward. This preserves the formatter's
        # semantics but does not label the mutation an actual native execution.
        summary=next((row for row in rows if row['event']=='trace_summary'),None)
        if summary:
            summary['accepted_records']=summary['written_records']=len(rows)-1
        self.write(role,rows)
    def analyze(self,window=None): return analysis.analyze_files(**self.paths,window=window)


class HostAnalysisChecks(unittest.TestCase):
    def fixture(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        return OwnedTraceFixture(Path(temp.name))

    def test_actual_python_formatter_and_native_contract_join_regions_without_e2e_or_addition(self):
        fixture=self.fixture();result=fixture.analyze()
        self.assertEqual(result['joins']['complete_observed_frame_chains'],2)
        self.assertEqual(result['joins']['raw_iterations_complete'],2)
        for role in analysis.ROLES: self.assertTrue(result['streams'][role]['sink_coverage_complete'])
        self.assertTrue(result['streams']['capture']['producer_coverage_complete'])
        self.assertFalse(result['streams']['native']['producer_quiescence_known'])
        self.assertFalse(result['whole_pipeline_coverage_accepted'])
        self.assertTrue(result['regions_are_not_additive'])
        stage=result['stages_ms']
        self.assertEqual(stage['raw_header_rgba_flush_attempt_ms']['p50'],10)
        self.assertEqual(stage['native_payload_read_ms']['p50'],8)
        self.assertEqual(stage['raw_pipe_native_payload_overlap_ms']['p50'],8)
        self.assertEqual(stage['native_stdout_feed_publish_overlap_ms']['p50'],3)
        self.assertEqual(stage['raw_budget_wait_overshoot_ms']['p50'],1)
        self.assertEqual(stage['native_vt_call_ms']['p50'],7)
        self.assertEqual(stage['native_submit_to_callback_ms']['p50'],3)
        self.assertEqual(stage['feed_fill_segment_ms']['count'],6)
        self.assertEqual(stage['feed_media_publish_attempt_ms']['count'],2)
        self.assertEqual(result['counter_anchors']['raw']['delta']['frames_submitted'],1)
        self.assertFalse(result['counter_anchors']['raw']['same_frame_cohort'])
        self.assertNotIn('source_to_phone',json.dumps(result))

    def test_zeros_are_unobserved_but_equal_nonzero_endpoints_are_real_zero_duration(self):
        fixture=self.fixture()
        def change(rows):
            writes=[row for row in rows if row['event']=='raw_write']
            writes[0]['write_begin_ns']=0
            writes[1]['write_end_ns']=writes[1]['write_begin_ns']
        fixture.mutate('capture',change)
        result=fixture.analyze()
        self.assertEqual(result['stages_ms']['raw_header_rgba_flush_attempt_ms']['count'],1)
        self.assertEqual(result['stages_ms']['raw_header_rgba_flush_attempt_ms']['max'],0)
        self.assertGreater(result['excluded_intervals']['zero_endpoint'],0)

    def test_truncated_last_line_and_missing_summary_do_not_mean_clean_coverage(self):
        fixture=self.fixture()
        original=fixture.paths['capture'].read_bytes()
        fixture.paths['capture'].write_bytes(original[:-9])
        result=fixture.analyze()
        self.assertTrue(result['streams']['capture']['truncated_final_line'])
        self.assertFalse(result['streams']['capture']['sink_coverage_complete'])
        self.assertEqual(result['streams']['capture']['sink_summary_count'],0)
        self.assertGreater(result['streams']['capture']['malformed_rows'],0)

    def test_clock_errors_reject_all_intervals_in_that_role_and_block_cross_role_join(self):
        fixture=self.fixture()
        fixture.mutate('capture',lambda rows:next(row for row in rows if row['event']=='host_timing_summary').update(clock_errors=1))
        result=fixture.analyze()
        self.assertEqual(result['stages_ms']['raw_loop_ms']['count'],0)
        self.assertEqual(result['joins']['complete_observed_frame_chains'],0)
        self.assertFalse(result['streams']['capture']['producer_coverage_complete'])
        self.assertGreater(result['excluded_intervals']['clock_not_accepted'],0)

    def test_clean_sink_does_not_override_nonquiescent_python_producer(self):
        fixture=self.fixture()
        fixture.mutate('feed',lambda rows:next(row for row in rows if row['event']=='host_timing_summary').update(producer_quiescent=False))
        result=fixture.analyze()
        self.assertTrue(result['streams']['feed']['sink_coverage_complete'])
        self.assertFalse(result['streams']['feed']['producer_coverage_complete'])
        self.assertEqual(result['joins']['complete_observed_frame_chains'],2)

    def test_invalid_clock_domain_missing_end_duplicate_end_and_reversed_bracket(self):
        for case in ('domain','missing','duplicate','reversed'):
            with self.subTest(case=case):
                fixture=self.fixture()
                def change(rows):
                    end=next(row for row in rows if row['event']=='trace_clock' and row['phase']=='end')
                    if case=='domain': end['clock_domain']='host_clock_gettime_CLOCK_UPTIME_RAW_us'
                    if case=='missing': rows.remove(end)
                    if case=='duplicate': rows.insert(-1,copy.deepcopy(end))
                    if case=='reversed': end['clock_after_ns']=end['clock_before_ns']-1
                fixture.mutate('native',change)
                result=fixture.analyze()
                self.assertFalse(result['streams']['native']['clock_contract_accepted'])
                self.assertEqual(result['stages_ms']['native_slot_wait_ms']['count'],0)
                self.assertEqual(result['joins']['complete_observed_frame_chains'],0)

    def test_drop_caps_count_mismatch_and_observation_errors_stay_partial(self):
        for key,value in (('dropped_records',1),('byte_capped',True),('failed',True),('written_records',1)):
            with self.subTest(key=key):
                fixture=self.fixture()
                rows=fixture.read('native');next(row for row in rows if row['event']=='trace_summary')[key]=value
                fixture.write('native',rows)
                result=fixture.analyze()
                self.assertFalse(result['streams']['native']['sink_coverage_complete'])
                self.assertEqual(result['stages_ms']['native_slot_wait_ms']['count'],2)
        for key in ('emit_errors','schema_errors'):
            fixture=self.fixture()
            fixture.mutate('capture',lambda rows:next(row for row in rows if row['event']=='host_timing_summary').update({key:1}))
            self.assertFalse(fixture.analyze()['streams']['capture']['producer_coverage_complete'])

    def test_duplicate_or_missing_raw_triplet_has_no_first_wins_or_fake_zero_sample(self):
        for event in ('raw_loop','raw_budget','raw_write'):
            for duplicate in (False,True):
                with self.subTest(event=event,duplicate=duplicate):
                    fixture=self.fixture()
                    def change(rows):
                        target=next(row for row in rows if row['event']==event)
                        if duplicate: rows.insert(-1,copy.deepcopy(target))
                        else: rows.remove(target)
                    fixture.mutate('capture',change)
                    result=fixture.analyze()
                    self.assertEqual(result['joins']['raw_iterations_complete'],1)
                    self.assertEqual(result['joins']['raw_iterations_partial'],1)
                    self.assertEqual(result['stages_ms']['raw_loop_ms']['count'],1)
                    self.assertEqual(result['joins']['complete_observed_frame_chains'],1)

    def test_duplicate_pts_does_not_select_first_native_or_feed_record(self):
        for role,event in (('native','vt_frame'),('feed','feed_publish')):
            fixture=self.fixture()
            def change(rows):
                row=copy.deepcopy(next(row for row in rows if row['event']==event))
                if role=='feed': row['publish_seq']=3
                rows.insert(-1,row)
            fixture.mutate(role,change)
            self.assertEqual(fixture.analyze()['joins']['complete_observed_frame_chains'],1)

    def test_repeated_capture_pts_uses_exact_capture_sequence(self):
        fixture=self.fixture()
        def change(rows):
            row=copy.deepcopy(next(row for row in rows if row['event']=='capture_enqueue'))
            row['capture_seq']=91;row['enqueue_ns']=B+9_000_000;rows.insert(-1,row)
        fixture.mutate('capture',change)
        result=fixture.analyze()
        self.assertEqual(result['joins']['complete_observed_frame_chains'],2)
        self.assertEqual(result['stages_ms']['capture_enqueue_to_raw_dequeue_ms']['p50'],14.5)

    def test_idle_config_and_failed_flush_are_not_successful_media_frame_joins(self):
        fixture=self.fixture()
        def raw_change(rows):
            loop=next(row for row in rows if row['event']=='raw_loop')
            write=next(row for row in rows if row['event']=='raw_write')
            loop['capture_seq']=write['capture_seq']=0;write['idle_repeat']=True
        fixture.mutate('capture',raw_change)
        def feed_change(rows):
            row=copy.deepcopy(next(row for row in rows if row['event']=='feed_publish'))
            row.update(publish_seq=3,kind=3,has_source_pts=True,source_pts_us=0)
            rows.insert(-1,row)
            media=[row for row in rows if row['event']=='feed_publish' and row['kind']==4]
            media[1].update(flush_complete=False,published=False,publish_failures=1)
        fixture.mutate('feed',feed_change)
        result=fixture.analyze()
        self.assertEqual(result['joins']['complete_observed_frame_chains'],0)
        self.assertEqual(result['joins']['idle_repeats_excluded'],1)
        self.assertEqual(result['joins']['config_publications_separate'],1)
        self.assertEqual(result['stages_ms']['feed_nonmedia_publish_ms']['count'],1)

    def test_native_success_status_without_output_and_identity_sizes_do_not_prove_output(self):
        for case in ('no_output','size','native_seq'):
            fixture=self.fixture()
            def change(rows):
                row=next(row for row in rows if row['event']=='vt_frame')
                if case=='no_output':
                    for key in ('stdout_lock_wait_begin_ns','stdout_lock_acquired_ns','stdout_write_begin_ns',
                                'stdout_write_end_ns','au_bytes','keyframe'): row.pop(key)
                if case=='size': row['au_bytes']=999
                if case=='native_seq': row['native_input_seq']=999
            fixture.mutate('native',change)
            result=fixture.analyze()
            self.assertEqual(result['joins']['complete_observed_frame_chains'],1)
            self.assertEqual(result['stages_ms']['native_slot_wait_ms']['count'],2)

    def test_window_boundary_and_counter_snapshot_window_are_not_same_frame_cohort(self):
        fixture=self.fixture();result=fixture.analyze((B+20_000_000,B+90_000_000))
        self.assertEqual(result['stages_ms']['raw_loop_ms']['count'],0)
        self.assertGreater(result['excluded_intervals']['window_boundary_excluded'],0)
        self.assertEqual(result['counter_anchors']['raw']['snapshots'],1)
        self.assertFalse(result['counter_anchors']['raw']['available'])
        self.assertFalse(result['counter_anchors']['raw']['same_frame_cohort'])
        result=fixture.analyze((B+1_000_000,B+100_000_000))
        self.assertEqual(result['counter_anchors']['raw']['delta']['raw_frames'],3)
        self.assertEqual(result['counter_anchors']['raw']['delta']['frames_submitted'],1)
        self.assertEqual(result['joins']['complete_observed_frame_chains'],1)

    def test_outside_clock_bracket_and_interior_counter_regression_are_rejected(self):
        fixture=self.fixture()
        def change(rows):
            write=next(row for row in rows if row['event']=='raw_write');write['write_end_ns']=B+2_000_000_000
            second=[row for row in rows if row['event']=='raw_loop'][1]
            second['raw_frames']=0
        fixture.mutate('capture',change)
        result=fixture.analyze()
        self.assertGreater(result['excluded_intervals']['outside_trace_clock_bracket'],0)
        self.assertFalse(result['counter_anchors']['raw']['available'])
        self.assertEqual(result['counter_anchors']['raw']['counter_regressions'],1)

    def test_schema_guard_rejects_secret_extra_missing_bool_timestamp_float_id_and_nonfinite(self):
        original=next(row for row in self.fixture().read('capture') if row['event']=='raw_loop')
        bad=[]
        for key,value in (('password','never-export-this'),('begin_ns',True),('iteration',1.0),
                          ('raw_frames',float('nan')),('raw_frames',float('inf')),('event',['raw_loop'])):
            row=copy.deepcopy(original);row[key]=value;bad.append(row)
        row=copy.deepcopy(original);row.pop('queued_before');bad.append(row)
        for row in bad:
            with self.subTest(row=list(row)):
                self.assertIsNone(analysis.sanitize_host(row,'capture'))
        original['condition_waited']=0
        self.assertIsNotNone(analysis.sanitize_host(original,'capture'))
        original['begin_ns']=0
        self.assertIsNotNone(analysis.sanitize_host(original,'capture'))

    def test_read_bounds_growth_nonregular_and_sanitized_output_never_include_paths_or_secret_text(self):
        fixture=self.fixture();path=fixture.paths['capture']
        with path.open('a') as output:
            output.write(json.dumps(dict(schema='wrong',event='unknown',password='never-export-this'))+'\n')
        result=fixture.analyze();encoded=json.dumps(result)
        self.assertNotIn('never-export-this',encoded);self.assertNotIn(str(path),encoded)
        self.assertGreater(result['streams']['capture']['schema_rejected_rows'],0)
        with patch.object(analysis,'MAX_BYTES',64):
            self.assertTrue(analysis.read_stream(path,'capture')['status']['byte_bound_exceeded'])
        with patch.object(analysis,'MAX_ROWS',2):
            self.assertTrue(analysis.read_stream(path,'capture')['status']['record_bound_exceeded'])
        with path.open('a') as output: output.write('X'*(analysis.MAX_LINE+1)+'\n')
        self.assertTrue(analysis.read_stream(path,'capture')['status']['line_bound_exceeded'])
        initial=os.stat(path)
        changed=SimpleNamespace(**{key:getattr(initial,key) for key in ('st_mode','st_size','st_mtime_ns','st_ctime_ns')})
        changed.st_size+=1
        with patch.object(analysis.os,'fstat',side_effect=(initial,changed)):
            self.assertTrue(analysis.read_stream(path,'capture')['status']['changed_during_read'])
        self.assertFalse(analysis.read_stream(path.parent,'capture')['status']['regular_file'])

    def test_attempt_layout_and_window_errors_are_fixed_contract(self):
        fixture=self.fixture()
        with self.assertRaises(ValueError): analysis.analyze_files(capture=fixture.paths['capture'],native=Path('/different/capture.jsonl.native.jsonl'))
        with self.assertRaises(ValueError): analysis.analyze_files(capture=fixture.paths['capture'].parent/'auth.json')
        for window in ((0,1),(3,2),(None,2),(1,1.0)):
            with self.subTest(window=window),self.assertRaises(ValueError): fixture.analyze(window)

    def test_five_mixed_gaps_use_named_endpoints_and_retain_observed_zero(self):
        result=self.fixture().analyze()
        gaps=result['mixed_gaps_ms']
        self.assertEqual(set(gaps),set(analysis.MIXED_GAPS))
        for name,count,p50 in zip(analysis.MIXED_GAPS,(2,2,2,2,1),(1,.5,0,2,40)):
            self.assertEqual(gaps[name]['count'],count)
            self.assertEqual(gaps[name]['p50'],p50)
            self.assertEqual(gaps[name]['status'],'observed_subset')
        self.assertEqual(gaps[analysis.MIXED_GAPS[4]]['p50'],result['stages_ms']['raw_inter_loop_gap_ms']['p50'])
        self.assertEqual(result['mixed_gap_exclusions'][analysis.MIXED_GAPS[4]]['no_prior_endpoint'],1)
        self.assertTrue(result['regions_are_not_additive'])
        self.assertFalse(result['whole_pipeline_coverage_accepted'])

    def test_mixed_wait_gap_has_no_check_end_fallback_and_zero_wait_stamp_is_unknown(self):
        fixture=self.fixture()
        def change(rows):
            budgets=[row for row in rows if row['event']=='raw_budget']
            budgets[0]['requested_wait_ns']=0  # Valid check_end must not substitute.
            budgets[1]['wait_end_ns']=0
        fixture.mutate('capture',change)
        result=fixture.analyze();name=analysis.MIXED_GAPS[1]
        self.assertEqual(result['mixed_gaps_ms'][name],{'count':0,'status':'unknown'})
        self.assertEqual(result['mixed_gap_exclusions'][name]['no_budget_wait'],1)
        self.assertEqual(result['mixed_gap_exclusions'][name]['zero_endpoint'],1)
        self.assertEqual(result['mixed_gaps_ms'][analysis.MIXED_GAPS[0]]['count'],2)

    def test_mixed_gaps_keep_clock_and_half_open_boundary_rules(self):
        fixture=self.fixture();name=analysis.MIXED_GAPS[1]
        result=fixture.analyze((B+17_000_000,B+17_500_000))
        self.assertEqual(result['mixed_gaps_ms'][name]['status'],'unknown')
        self.assertEqual(result['mixed_gap_exclusions'][name]['window_boundary_excluded'],2)
        result=fixture.analyze((B+17_000_000,B+18_000_000))
        self.assertEqual(result['mixed_gaps_ms'][name]['count'],1)
        self.assertEqual(result['mixed_gaps_ms'][name]['max'],.5)
        self.assertEqual(result['mixed_gaps_ms'][analysis.MIXED_GAPS[2]]['status'],'unknown')
        fixture.mutate('capture',lambda rows:next(row for row in rows if row['event']=='host_timing_summary').update(clock_errors=1))
        result=fixture.analyze()
        self.assertTrue(all(gap=={'count':0,'status':'unknown'} for gap in result['mixed_gaps_ms'].values()))
        self.assertGreater(result['mixed_gap_exclusions'][name]['clock_not_accepted'],0)

    def test_mixed_gaps_exclude_reversal_identity_conflict_and_unmatched_prior(self):
        fixture=self.fixture()
        def change(rows):
            loops=[row for row in rows if row['event']=='raw_loop']
            writes=[row for row in rows if row['event']=='raw_write']
            loops[0]['condition_begin_ns']=loops[0]['begin_ns']-1
            writes[0]['capture_seq']+=90
            loops[1]['prior_end_ns']+=1
        fixture.mutate('capture',change);result=fixture.analyze()
        self.assertEqual(result['mixed_gap_exclusions'][analysis.MIXED_GAPS[0]]['reversed_interval'],1)
        for name in analysis.MIXED_GAPS[2:4]:
            self.assertEqual(result['mixed_gap_exclusions'][name]['raw_identity_conflict'],1)
            self.assertEqual(result['mixed_gaps_ms'][name]['count'],1)
        name=analysis.MIXED_GAPS[4]
        self.assertEqual(result['mixed_gaps_ms'][name]['status'],'unknown')
        self.assertEqual(result['mixed_gap_exclusions'][name]['unmatched_prior_loop'],1)

    def test_mixed_gaps_require_unique_triplets_and_actual_previous_loop(self):
        fixture=self.fixture()
        fixture.mutate('capture',lambda rows:rows.insert(-1,copy.deepcopy(next(row for row in rows if row['event']=='raw_loop'))))
        result=fixture.analyze()
        for name in analysis.MIXED_GAPS[:4]:
            self.assertEqual(result['mixed_gaps_ms'][name]['count'],1)
        self.assertEqual(result['mixed_gaps_ms'][analysis.MIXED_GAPS[4]]['status'],'unknown')
        for row in result['mixed_gap_exclusions'].values():
            self.assertEqual(row['raw_triplet_not_unique_or_complete'],1)

    def test_legacy_missing_diagnostic_rows_are_fixed_unknown_and_output_is_closed(self):
        fixture=self.fixture()
        fixture.mutate('capture',lambda rows:rows.__setitem__(slice(None),[
            row for row in rows if row['event'] not in ('raw_loop','raw_budget','raw_write')]))
        result=fixture.analyze()
        for name in analysis.MIXED_GAPS:
            self.assertEqual(result['mixed_gaps_ms'][name],{'count':0,'status':'unknown'})
            self.assertEqual(set(result['mixed_gap_exclusions'][name]),set(analysis.MIXED_GAP_EXCLUSIONS))
        self.assertNotIn(str(fixture.paths['capture']),json.dumps(result))

    def test_mixed_buckets_do_not_change_legacy_stage_exclusion_join_or_anchor_results(self):
        fixture=self.fixture();baseline=fixture.analyze()
        # Disable only the extra observation hooks, leaving the original pass.
        with patch.object(analysis.MixedRawGaps,'add'),patch.object(analysis.MixedRawGaps,'exclude'):
            unchanged=fixture.analyze()
        for key in ('stages_ms','excluded_intervals','joins','counter_anchors','streams'):
            self.assertEqual(baseline[key],unchanged[key])


if __name__=='__main__': unittest.main()
