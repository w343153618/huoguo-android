#!/usr/bin/env python3
"""Bounded numeric host-region analysis, never source-to-phone latency.

Only the three explicit roles from one owned attempt may be combined. Every
clock, identity, terminal/coverage and window boundary is checked separately.
"""
import argparse
from collections import Counter, defaultdict
import json
import io
import os
from pathlib import Path
import stat
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from host_timing_trace import FIELDS
from scripts.probes.capture_trace_analysis import distribution

MAX_BYTES = 16 * 1024 * 1024
MAX_ROWS = 24002
MAX_LINE = 8192
MAX_FIELDS = 64
MAX_INT = (1 << 63)-1
CLOCK = 'host_clock_gettime_CLOCK_MONOTONIC_ns'
ROLES = ('capture', 'native', 'feed')
HEADER = frozenset(('schema', 'process', 'event'))
FLAGS = frozenset(('condition_waited', 'native_ready', 'consumed', 'flush_complete',
    'idle_repeat', 'has_source_pts', 'published', 'cancel_before', 'cancel_after',
    'cancelled', 'partial', 'producer_quiescent'))
LEGACY_FIELDS = {
    'capture_enqueue': frozenset(('capture_seq', 'source_pts_us', 'screenshot_seq', 'grpc_return_ns',
        'enqueue_ns', 'width', 'height', 'raw_bytes', 'pending_count', 'replaced_capture_seq')),
    'raw_submit': frozenset(('capture_seq', 'source_pts_us', 'dequeue_ns', 'pipe_write_begin_ns',
        'pipe_write_end_ns', 'raw_bytes', 'skipped_frames', 'idle_repeat')),
    'raw_drop': frozenset(('capture_seq', 'source_pts_us', 'dequeue_ns', 'reason')),
    'encoded_egress': frozenset(('source_pts_us', 'encoded_read_complete_ns', 'socket_write_end_ns', 'au_bytes')),
    'raw_read': frozenset(('native_input_seq', 'source_pts_us', 'header_read_begin_ns',
        'header_read_complete_ns', 'raw_read_begin_ns', 'raw_read_complete_ns', 'width', 'height', 'raw_bytes')),
    'vt_frame': frozenset(('native_input_seq', 'source_pts_us', 'header_read_begin_ns',
        'header_read_complete_ns', 'raw_read_begin_ns', 'raw_read_complete_ns', 'width', 'height', 'raw_bytes',
        'slot_wait_begin_ns', 'slot_wait_end_ns', 'pixel_conversion_begin_ns', 'pixel_conversion_end_ns',
        'vt_submit_ns', 'vt_callback_ns', 'vt_status', 'frame_dropped',
        'stdout_lock_wait_begin_ns', 'stdout_lock_acquired_ns', 'stdout_write_begin_ns',
        'stdout_write_end_ns', 'au_bytes', 'keyframe')),
    'vt_submit_return': frozenset(('source_pts_us', 'vt_submit_return_ns', 'vt_submit_status')),
    'trace_clock': frozenset(('phase', 'clock_domain', 'clock_before_ns', 'clock_after_ns',
        'unix_ns', 'wall_clock_precision_ns', 'source_pts_scope')),
    'trace_summary': frozenset(('accepted_records', 'written_records', 'dropped_records',
        'byte_capped', 'clock_errors', 'record_limit', 'byte_limit', 'failed', 'clean_close')),
}
LEGACY_REQUIRED = {event: fields for event, fields in LEGACY_FIELDS.items()}
LEGACY_REQUIRED['vt_frame'] = LEGACY_FIELDS['vt_frame'] - frozenset(('stdout_lock_wait_begin_ns',
    'stdout_lock_acquired_ns', 'stdout_write_begin_ns', 'stdout_write_end_ns', 'au_bytes', 'keyframe'))
LEGACY_REQUIRED['trace_clock'] = frozenset(('phase', 'clock_domain', 'clock_before_ns', 'clock_after_ns', 'unix_ns'))
LEGACY_REQUIRED['trace_summary'] = LEGACY_FIELDS['trace_summary'] - frozenset(('failed', 'clock_errors'))
LEGACY_FLAGS = frozenset(('idle_repeat', 'frame_dropped', 'keyframe', 'byte_capped', 'failed', 'clean_close'))
CONTRACT = {
    'clock_domain': CLOCK,
    'budget_clock_domain': 'python_time_monotonic_seconds_scaled_ns',
    'scope': 'host_regions_only_not_phone_or_network_latency',
    'raw_outcomes': '1submitted_2emptyflush_3ptsreject_4cancel_5unset_6failure',
    'feed_read_outcomes': '1complete_2cancel_3failure',
    'feed_kinds': '1codec_2geometry_3config_4media',
    'counters_scope': 'best_effort_cumulative_snapshots_not_atomic_capture_cohort',
}
ROLE_EVENTS = {
    'capture': frozenset(('trace_clock', 'trace_summary', 'host_timing_contract', 'host_timing_summary',
        'capture_enqueue', 'raw_submit', 'raw_drop', 'encoded_egress', 'raw_loop', 'raw_budget', 'raw_write')),
    'native': frozenset(('trace_clock', 'trace_summary', 'raw_read', 'vt_frame', 'vt_submit_return')),
    'feed': frozenset(('trace_clock', 'trace_summary', 'host_timing_contract', 'host_timing_summary',
        'feed_read', 'feed_publish', 'feed_cancel')),
}


def integer(value, signed=False):
    return type(value) is int and (-MAX_INT if signed else 0) <= value <= MAX_INT


def sanitize_host(row, role):
    """Reject schema drift; never pass through arbitrary strings/fields."""
    if (role not in ROLES or type(row) is not dict or len(row) > MAX_FIELDS
            or row.get('schema') != 'capture-vt-trace-v1'
            or row.get('process') != ('swift' if role == 'native' else 'python')
            or type(row.get('event')) is not str
            or row.get('event') not in ROLE_EVENTS[role]):
        return None
    event = row['event']
    if event == 'host_timing_contract':
        return {**{key: row[key] for key in HEADER}, **CONTRACT} if (
            set(row) == HEADER | CONTRACT.keys() and all(row.get(k) == v for k,v in CONTRACT.items())) else None
    fields = FIELDS[event] if event in FIELDS else LEGACY_FIELDS[event]
    required = FIELDS[event] if event in FIELDS else LEGACY_REQUIRED[event]
    if not required <= row.keys() or not row.keys() <= fields | HEADER:
        return None
    clean = {key: row[key] for key in HEADER}
    for key in fields & row.keys():
        value = row[key]
        if event in FIELDS:
            if key in FLAGS:
                if type(value) not in (int, bool) or value not in (0, 1): return None
                clean[key] = bool(value)
            elif integer(value): clean[key] = value
            else: return None
        elif key in LEGACY_FLAGS:
            if type(value) is not bool: return None
            clean[key] = value
        elif key == 'phase':
            if value not in ('start', 'end'): return None
            clean[key] = value
        elif key == 'clock_domain':
            if value != CLOCK: return None
            clean[key] = CLOCK
        elif key == 'source_pts_scope':
            if value != 'emulator_estimated_screenshot_generation_unix_us_not_guest_media_pts': return None
        elif key == 'reason':
            if value != 'nonincreasing_source_pts': return None
            clean[key] = value
        elif integer(value, signed=key in ('vt_status', 'vt_submit_status')): clean[key] = value
        else: return None
    if event in ('raw_loop','raw_budget','raw_write') and clean['iteration']==0: return None
    if event=='raw_loop' and clean['outcome'] not in range(1,7): return None
    if event=='feed_read' and (clean['read_seq']==0 or clean['outcome'] not in (1,2,3)): return None
    if event=='feed_publish' and (clean['publish_seq']==0 or clean['kind'] not in (1,2,3,4)
                                 or clean['written_bytes']>clean['record_bytes']): return None
    if event in ('raw_read','vt_frame') and clean['native_input_seq']==0: return None
    if event=='capture_enqueue' and clean['capture_seq']==0: return None
    return clean


def read_stream(path, role):
    status = dict(role=role, available=False, read_error=False, regular_file=False,
        byte_bound_exceeded=False, record_bound_exceeded=False, line_bound_exceeded=False,
        changed_during_read=False, truncated_final_line=False, lines_read=0, accepted_rows=0, malformed_rows=0,
        schema_rejected_rows=0, clock_contract_rejected_rows=0)
    records = []
    if path is None: return dict(status=status, records=records)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            status['available'] = True
            status['regular_file'] = stat.S_ISREG(info.st_mode)
            if not status['regular_file']: return dict(status=status, records=records)
            if info.st_size > MAX_BYTES:
                status['byte_bound_exceeded'] = True
                return dict(status=status, records=records)
            with os.fdopen(fd, 'rb', closefd=False) as source: data = source.read(MAX_BYTES+1)
            after = os.fstat(fd)
            status['changed_during_read'] = (info.st_size,info.st_mtime_ns,info.st_ctime_ns) != (
                after.st_size,after.st_mtime_ns,after.st_ctime_ns) or len(data) != after.st_size
        finally: os.close(fd)
    except OSError:
        status['read_error'] = True
        return dict(status=status, records=records)
    if len(data) > MAX_BYTES:
        status['byte_bound_exceeded'] = True
        return dict(status=status, records=records)
    status['truncated_final_line'] = bool(data and not data.endswith(b'\n'))
    bounded = io.BytesIO(data)
    while True:
        line = bounded.readline(MAX_LINE+1)
        if not line: break
        if status['lines_read'] >= MAX_ROWS:
            status['record_bound_exceeded'] = True
            break
        status['lines_read'] += 1
        if len(line.rstrip(b'\n')) > MAX_LINE or len(line)==MAX_LINE+1 and not line.endswith(b'\n'):
            status['line_bound_exceeded'] = True
            status['schema_rejected_rows'] += 1
            while line and not line.endswith(b'\n'): line = bounded.readline(MAX_LINE+1)
            continue
        try: raw = json.loads(line)
        except (ValueError, TypeError, RecursionError):
            status['malformed_rows'] += 1
            continue
        clean = sanitize_host(raw, role)
        if clean is None:
            status['schema_rejected_rows'] += 1
            if type(raw) is dict and raw.get('event') in ('trace_clock', 'host_timing_contract'):
                status['clock_contract_rejected_rows'] += 1
        else: records.append(clean)
    status['accepted_rows'] = len(records)
    return dict(status=status, records=records)


def coverage(stream, role):
    rows, result = stream['records'], dict(stream['status'])
    clocks = [row for row in rows if row['event']=='trace_clock']
    starts = [row for row in clocks if row['phase']=='start']
    ends = [row for row in clocks if row['phase']=='end']
    valid_brackets = all(0 < row['clock_before_ns'] <= row['clock_after_ns'] and row['unix_ns'] > 0 for row in clocks)
    bracketed = len(starts)==len(ends)==1 and valid_brackets and (
        starts[0]['clock_before_ns'] <= ends[0]['clock_before_ns'])
    declared = bracketed and not result['clock_contract_rejected_rows']
    contracts = [row for row in rows if row['event']=='host_timing_contract']
    if role != 'native': declared = declared and len(contracts)==1
    summaries = [row for row in rows if row['event']=='trace_summary']
    observations = [row for row in rows if row['event']=='host_timing_summary']
    summary = summaries[0] if len(summaries)==1 else {}
    observation = observations[0] if len(observations)==1 else {}
    consistent = bool(summary) and (0 <= summary['written_records'] <= summary['accepted_records'] <=
        summary['record_limit'] <= 24000 and 4096 <= summary['byte_limit'] <= MAX_BYTES and
        summary['written_records'] == result['lines_read']-1)
    clock_errors = summary.get('clock_errors', 0)+observation.get('clock_errors', 0)
    sink_complete = (declared and bracketed and consistent and summaries[-1:] == [rows[-1]] and
        summary.get('clean_close') is True and summary.get('failed', False) is False and
        summary.get('byte_capped') is False and summary.get('dropped_records') == 0 and
        summary.get('accepted_records') == summary.get('written_records') and clock_errors == 0 and
        all(not result[key] for key in ('read_error','byte_bound_exceeded','record_bound_exceeded',
            'line_bound_exceeded','truncated_final_line','changed_during_read','malformed_rows','schema_rejected_rows')))
    producer_known = role != 'native' and bool(observation)
    result.update(clock_contract_accepted=declared, clock_start_end_bracketed=bracketed,
        clock_start_before_ns=starts[0]['clock_before_ns'] if len(starts)==1 else 0,
        clock_end_after_ns=ends[0]['clock_after_ns'] if len(ends)==1 else 0,
        sink_summary_count=len(summaries), observation_summary_count=len(observations),
        sink_counts_consistent=consistent, sink_coverage_complete=bool(sink_complete),
        producer_quiescence_known=producer_known,
        producer_quiescent=bool(observation.get('producer_quiescent', False)),
        producer_coverage_complete=bool(sink_complete and producer_known and observation.get('producer_quiescent')
            and not observation.get('emit_errors') and not observation.get('schema_errors')),
        clock_errors=clock_errors, emit_errors=observation.get('emit_errors', 0),
        schema_errors=observation.get('schema_errors', 0), dropped_records=summary.get('dropped_records', 0),
        byte_capped=bool(summary.get('byte_capped', False)))
    return result


class Regions:
    def __init__(self, clock_ok, bounds, window):
        self.clock_ok, self.bounds, self.window = clock_ok, bounds, window
        self.values = defaultdict(list)
        self.skips = Counter()

    def endpoints(self, row, begin, end, role):
        if not self.clock_ok[role]: self.skips['clock_not_accepted'] += 1; return None
        a, b = row.get(begin), row.get(end)
        if not integer(a) or not integer(b): self.skips['missing_endpoint'] += 1; return None
        if a==0 or b==0: self.skips['zero_endpoint'] += 1; return None
        if b<a: self.skips['reversed_interval'] += 1; return None
        if not self.bounds[role][0] <= a <= b <= self.bounds[role][1]:
            self.skips['outside_trace_clock_bracket'] += 1; return None
        if self.window is not None and not (self.window[0] <= a <= b < self.window[1]):
            self.skips['window_boundary_excluded'] += 1; return None
        return a, b

    def add(self, name, row, begin, end, role):
        endpoints = self.endpoints(row, begin, end, role)
        if endpoints: self.values[name].append((endpoints[1]-endpoints[0])/1e6)
        return endpoints

    def overlap(self, name, a, b):
        if a is not None and b is not None:
            self.values[name].append(max(0, min(a[1],b[1])-max(a[0],b[0]))/1e6)


STAGES = (
    'raw_loop_ms','raw_condition_region_ms','raw_condition_wait_ms','raw_control_write_ms',
    'raw_budget_check_ms','raw_budget_wait_ms','raw_budget_wait_overshoot_ms','raw_budget_consume_ms',
    'raw_header_rgba_flush_attempt_ms','raw_empty_flush_ms','raw_inter_loop_gap_ms','raw_after_write_to_loop_ms',
    'capture_enqueue_to_raw_dequeue_ms','native_header_read_ms','native_payload_read_ms',
    'native_slot_wait_ms','native_conversion_ms','native_vt_call_ms','native_submit_to_callback_ms',
    'native_stdout_lock_wait_ms','native_stdout_write_ms','encoded_socket_write_ms',
    'feed_fill_segment_ms','feed_media_write_region_attempt_ms','feed_media_flush_attempt_ms','feed_media_publish_attempt_ms',
    'feed_nonmedia_publish_ms','raw_pipe_native_payload_overlap_ms','native_stdout_feed_publish_overlap_ms')

MIXED_GAPS = (
    'raw_mixed_loop_begin_to_condition_begin_ms',
    'raw_mixed_budget_wait_end_to_dequeue_ms',
    'raw_mixed_consume_end_to_write_begin_ms',
    'raw_mixed_write_end_to_loop_end_ms',
    'raw_mixed_prior_end_to_next_begin_ms')
MIXED_GAP_EXCLUSIONS = (
    'clock_not_accepted','missing_endpoint','zero_endpoint','reversed_interval',
    'window_boundary_excluded','outside_trace_clock_bracket','unmatched_prior_loop',
    'no_budget_wait','budget_not_consumed','raw_identity_conflict','no_prior_endpoint',
    'raw_triplet_not_unique_or_complete')


class MixedRawGaps:
    """Fixed, independent observation buckets; never alter legacy exclusions.

    Names identify endpoints, not a specific lock/CPU cost. No observations
    means unknown, including legacy schemas without raw diagnostic triplets.
    """
    def __init__(self, clock_ok, bounds, window, incomplete):
        self.regions = {name:Regions(clock_ok,bounds,window) for name in MIXED_GAPS}
        for region in self.regions.values():
            region.skips['raw_triplet_not_unique_or_complete'] = incomplete

    def add(self, name, row, begin, end):
        self.regions[name].add(name,row,begin,end,'capture')

    def exclude(self, name, reason):
        self.regions[name].skips[reason] += 1

    def distributions(self):
        result = {}
        for name, region in self.regions.items():
            observed = distribution(region.values[name])
            result[name] = dict(observed, status='observed_subset' if observed['count'] else 'unknown')
        return result

    def exclusions(self):
        return {name:{reason:region.skips[reason] for reason in MIXED_GAP_EXCLUSIONS}
                for name,region in self.regions.items()}


def unique_index(rows, event, key):
    groups = defaultdict(list)
    for row in rows:
        if row['event']==event: groups[key(row)].append(row)
    return {k: values[0] for k,values in groups.items() if len(values)==1}, sum(len(v) for v in groups.values() if len(v)>1)


def anchors(rows, fields, clock_ok, bounds, window):
    usable = [row for row in rows if clock_ok and integer(row.get('end_ns')) and row['end_ns'] > 0
        and bounds[0] <= row['end_ns'] <= bounds[1]
        and (window is None or window[0] <= row['end_ns'] < window[1])]
    usable.sort(key=lambda row: row['end_ns'])
    if len(usable)<2: return dict(available=False, snapshots=len(usable), same_frame_cohort=False)
    first, last = usable[0], usable[-1]
    elapsed = last['end_ns']-first['end_ns']
    regressions = sum(any(right[key]<left[key] for key in fields) for left,right in zip(usable,usable[1:]))
    valid = elapsed>0 and regressions==0
    return dict(available=valid, snapshots=len(usable), first_ns=first['end_ns'], last_ns=last['end_ns'],
        elapsed_ns=elapsed, first={key:first[key] for key in fields}, last={key:last[key] for key in fields},
        delta={key:last[key]-first[key] for key in fields} if valid else {},
        rates_per_second={key:round((last[key]-first[key])*1e9/elapsed,4) for key in fields} if valid else {},
        counter_regressions=regressions, same_frame_cohort=False, interval_not_full_session=True)


def analyze_streams(streams, window=None):
    if window is not None and not (len(window)==2 and integer(window[0]) and integer(window[1])
                                  and 0 < window[0] < window[1]):
        raise ValueError('invalid_monotonic_window')
    states = {role:coverage(streams[role],role) for role in ROLES}
    clock_ok = {role:states[role]['clock_contract_accepted'] and states[role]['clock_errors']==0 for role in ROLES}
    bounds = {role:(states[role]['clock_start_before_ns'],states[role]['clock_end_after_ns']) for role in ROLES}
    regions = Regions(clock_ok, bounds, window)
    raw, native, feed = (streams[role]['records'] for role in ROLES)
    loops, loop_dup = unique_index(raw,'raw_loop',lambda row:row['iteration'])
    budgets, budget_dup = unique_index(raw,'raw_budget',lambda row:row['iteration'])
    writes, write_dup = unique_index(raw,'raw_write',lambda row:row['iteration'])
    captures, capture_dup = unique_index(raw,'capture_enqueue',lambda row:(row['capture_seq'],row['source_pts_us']))
    encoded, encoded_dup = unique_index(raw,'encoded_egress',lambda row:row['source_pts_us'])
    native_read, nr_dup = unique_index(native,'raw_read',lambda row:row['source_pts_us'])
    vt, vt_dup = unique_index(native,'vt_frame',lambda row:row['source_pts_us'])
    returns, return_dup = unique_index(native,'vt_submit_return',lambda row:row['source_pts_us'])
    publications, pub_dup = unique_index(feed,'feed_publish',lambda row:row['publish_seq'])
    reads, read_dup = unique_index(feed,'feed_read',lambda row:row['read_seq'])
    fresh = defaultdict(list)
    raw_endpoints = {}
    complete = set(loops)&set(budgets)&set(writes)
    raw_iterations = {row['iteration'] for row in raw if row['event'] in ('raw_loop','raw_budget','raw_write')}
    mixed = MixedRawGaps(clock_ok,bounds,window,len(raw_iterations)-len(complete))
    identity_conflicts = idle = failed_raw = 0
    for iteration in sorted(complete):
        loop, budget, write = loops[iteration], budgets[iteration], writes[iteration]
        mixed.add(MIXED_GAPS[0],loop,'begin_ns','condition_begin_ns')
        if budget['requested_wait_ns']>0:
            mixed.add(MIXED_GAPS[1],dict(wait_end_ns=budget.get('wait_end_ns'),
                dequeue_ns=loop.get('dequeue_ns')),'wait_end_ns','dequeue_ns')
        else:
            mixed.exclude(MIXED_GAPS[1],'no_budget_wait')
        same_raw_identity = (loop['capture_seq'],loop['source_pts_us']) == (
            write['capture_seq'],write['source_pts_us'])
        if not same_raw_identity:
            mixed.exclude(MIXED_GAPS[2],'raw_identity_conflict')
            mixed.exclude(MIXED_GAPS[3],'raw_identity_conflict')
        else:
            if budget['consumed']:
                mixed.add(MIXED_GAPS[2],dict(consume_end_ns=budget.get('consume_end_ns'),
                    write_begin_ns=write.get('write_begin_ns')),'consume_end_ns','write_begin_ns')
            else:
                mixed.exclude(MIXED_GAPS[2],'budget_not_consumed')
            mixed.add(MIXED_GAPS[3],dict(write_end_ns=write.get('write_end_ns'),
                end_ns=loop.get('end_ns')),'write_end_ns','end_ns')
        regions.add('raw_loop_ms',loop,'begin_ns','end_ns','capture')
        regions.add('raw_condition_region_ms',loop,'condition_begin_ns','condition_end_ns','capture')
        if loop['condition_waited']:
            regions.add('raw_condition_wait_ms',loop,'condition_wait_begin_ns','condition_wait_end_ns','capture')
        if loop['control_count']>0: regions.add('raw_control_write_ms',loop,'control_begin_ns','control_end_ns','capture')
        regions.add('raw_budget_check_ms',budget,'check_begin_ns','check_end_ns','capture')
        if budget['requested_wait_ns']>0:
            wait = regions.add('raw_budget_wait_ms',budget,'wait_begin_ns','wait_end_ns','capture')
            if wait and loop['outcome']!=4:
                regions.values['raw_budget_wait_overshoot_ms'].append(
                    (wait[1]-wait[0]-budget['requested_wait_ns'])/1e6)
        if budget['consumed']: regions.add('raw_budget_consume_ms',budget,'consume_begin_ns','consume_end_ns','capture')
        if loop['outcome']==2: regions.add('raw_empty_flush_ms',write,'idle_flush_begin_ns','idle_flush_end_ns','capture')
        if write['write_begin_ns'] or write['write_end_ns'] or write['flush_complete'] or loop['outcome']==1:
            raw_endpoints[iteration] = regions.add('raw_header_rgba_flush_attempt_ms',write,'write_begin_ns','write_end_ns','capture')
        prior = loops.get(iteration-1)
        if prior and loop['prior_end_ns']==prior['end_ns']:
            regions.add('raw_inter_loop_gap_ms',loop,'prior_end_ns','begin_ns','capture')
            mixed.add(MIXED_GAPS[4],loop,'prior_end_ns','begin_ns')
        elif loop['prior_end_ns']: regions.skips['unmatched_prior_loop'] += 1
        if not prior or loop['prior_end_ns']!=prior['end_ns']:
            mixed.exclude(MIXED_GAPS[4],
                'unmatched_prior_loop' if loop['prior_end_ns'] else 'no_prior_endpoint')
        prior_write = writes.get(iteration-1)
        if prior_write and prior_write['write_end_ns'] and loop['prior_write_end_ns']==prior_write['write_end_ns']:
            regions.add('raw_after_write_to_loop_ms',loop,'prior_write_end_ns','begin_ns','capture')
        if (loop['capture_seq'],loop['source_pts_us']) != (write['capture_seq'],write['source_pts_us']):
            identity_conflicts += 1; continue
        if write['idle_repeat'] or loop['capture_seq']==0:
            idle += bool(write['idle_repeat']); continue
        if loop['outcome']!=1 or not write['flush_complete'] or not budget['consumed']:
            failed_raw += 1; continue
        fresh[loop['source_pts_us']].append((loop,write))
        capture = captures.get((loop['capture_seq'],loop['source_pts_us']))
        if capture:
            regions.add('capture_enqueue_to_raw_dequeue_ms',dict(enqueue_ns=capture['enqueue_ns'],
                dequeue_ns=loop['dequeue_ns']),'enqueue_ns','dequeue_ns','capture')
    for pts,row in native_read.items():
        regions.add('native_header_read_ms',row,'header_read_begin_ns','header_read_complete_ns','native')
        regions.add('native_payload_read_ms',row,'raw_read_begin_ns','raw_read_complete_ns','native')
    for pts,row in vt.items():
        regions.add('native_slot_wait_ms',row,'slot_wait_begin_ns','slot_wait_end_ns','native')
        regions.add('native_conversion_ms',row,'pixel_conversion_begin_ns','pixel_conversion_end_ns','native')
        regions.add('native_submit_to_callback_ms',row,'vt_submit_ns','vt_callback_ns','native')
        returned = returns.get(pts)
        if returned:
            regions.add('native_vt_call_ms',dict(vt_submit_ns=row['vt_submit_ns'],
                vt_submit_return_ns=returned['vt_submit_return_ns']),'vt_submit_ns','vt_submit_return_ns','native')
        regions.add('native_stdout_lock_wait_ms',row,'stdout_lock_wait_begin_ns','stdout_lock_acquired_ns','native')
        regions.add('native_stdout_write_ms',row,'stdout_write_begin_ns','stdout_write_end_ns','native')
    for row in encoded.values():
        regions.add('encoded_socket_write_ms',row,'encoded_read_complete_ns','socket_write_end_ns','capture')
    for row in reads.values():
        regions.add('feed_fill_segment_ms',row,'begin_ns','end_ns','feed')
    media = defaultdict(list)
    config_records = 0
    for row in publications.values():
        if row['kind']==4:
            regions.add('feed_media_write_region_attempt_ms',row,'begin_ns','write_end_ns','feed')
            if row['flush_begin_ns']:
                regions.add('feed_media_flush_attempt_ms',row,'flush_begin_ns','flush_end_ns','feed')
            regions.add('feed_media_publish_attempt_ms',row,'begin_ns','end_ns','feed')
            if row['published'] and row['flush_complete'] and row['has_source_pts'] and row['source_pts_us']>0:
                media[row['source_pts_us']].append(row)
        else:
            config_records += row['kind']==3
            regions.add('feed_nonmedia_publish_ms',row,'begin_ns','end_ns','feed')
    joined = []
    join_conflicts = Counter()
    for pts, raw_matches in fresh.items():
        if len(raw_matches)!=1: join_conflicts['duplicate_fresh_pts'] += 1; continue
        loop,write = raw_matches[0]
        capture = captures.get((loop['capture_seq'],pts))
        nr,frame,egress,returned = native_read.get(pts),vt.get(pts),encoded.get(pts),returns.get(pts)
        pm = media.get(pts,[])
        if not capture: join_conflicts['missing_capture'] += 1; continue
        if not nr or not frame or not egress or not returned or len(pm)!=1:
            join_conflicts['missing_or_duplicate_downstream'] += 1; continue
        publication = pm[0]
        if (nr['native_input_seq'] != frame['native_input_seq'] or
                not (write['raw_bytes']==capture['raw_bytes']==nr['raw_bytes']==frame['raw_bytes']) or
                not (frame.get('au_bytes',0)>0 and frame['au_bytes']==egress['au_bytes'] and
                     publication['record_bytes']==frame['au_bytes']+12) or
                frame['vt_status']!=0 or frame['frame_dropped'] or returned['vt_submit_status']!=0):
            join_conflicts['identity_size_or_status_conflict'] += 1; continue
        if not all(clock_ok.values()): join_conflicts['clock_not_accepted'] += 1; continue
        pipe = raw_endpoints.get(loop['iteration'])
        payload = regions.endpoints(nr,'raw_read_begin_ns','raw_read_complete_ns','native')
        stdout = regions.endpoints(frame,'stdout_write_begin_ns','stdout_write_end_ns','native')
        publish = regions.endpoints(publication,'begin_ns','end_ns','feed')
        if any(value is None for value in (pipe,payload,stdout,publish)):
            join_conflicts['unobserved_or_outside_window'] += 1; continue
        regions.overlap('raw_pipe_native_payload_overlap_ms',pipe,payload)
        regions.overlap('native_stdout_feed_publish_overlap_ms',stdout,publish)
        joined.append(dict(iteration=loop['iteration'],capture_seq=loop['capture_seq'],source_pts_us=pts,
            native_input_seq=nr['native_input_seq'],publish_seq=publication['publish_seq']))
    skip_names = ('clock_not_accepted','missing_endpoint','zero_endpoint','reversed_interval',
        'window_boundary_excluded','outside_trace_clock_bracket','unmatched_prior_loop')
    join_names = ('duplicate_fresh_pts','missing_capture','missing_or_duplicate_downstream',
        'identity_size_or_status_conflict','clock_not_accepted','unobserved_or_outside_window')
    return dict(schema='host-region-analysis-v1', scope='observed_host_regions_not_phone_or_network_latency',
        clock_domain=CLOCK, window=dict(explicit=window is not None, start_ns=window[0] if window else 0,
            end_ns=window[1] if window else 0, half_open=True), streams=states,
        stages_ms={name:distribution(regions.values[name]) for name in STAGES},
        mixed_gaps_ms=mixed.distributions(), mixed_gap_exclusions=mixed.exclusions(),
        excluded_intervals={name:regions.skips[name] for name in skip_names},
        joins=dict(complete_observed_frame_chains=len(joined), raw_iterations_complete=len(complete),
            raw_iterations_partial=len(raw_iterations)-len(complete),
            raw_identity_conflicts=identity_conflicts, idle_repeats_excluded=idle,
            non_success_raw_excluded=failed_raw, config_publications_separate=config_records,
            duplicate_records=loop_dup+budget_dup+write_dup+capture_dup+encoded_dup+nr_dup+vt_dup+return_dup+pub_dup+read_dup,
            exclusions={name:join_conflicts[name] for name in join_names}, bounded_examples=joined[:8]),
        counter_anchors=dict(raw=anchors(list(loops.values()),('raw_frames','frames_submitted',
            'pending_frames_replaced','idle_repeats'),clock_ok['capture'],bounds['capture'],window),
            feed=anchors(list(publications.values()),('codec_records','geometry_records','config_records',
                'media_records','published_bytes','publish_failures'),clock_ok['feed'],bounds['feed'],window)),
        whole_pipeline_coverage_accepted=False, regions_are_not_additive=True,
        limits=dict(input_bytes_per_role=MAX_BYTES,input_rows_per_role=MAX_ROWS,
            input_line_bytes=MAX_LINE,input_fields_per_row=MAX_FIELDS,joined_examples=8),
        limitations=['Intervals are observed subsets; missing/drop/cap/zero values never mean zero cost',
            'Condition region contains condition wait; raw write and native payload read overlap',
            'Native stdout and feed publication overlap; do not sum them as serialized CPU costs',
            'Inter-loop gap can include previous diagnostic emit and thread scheduling',
            'Mixed raw gaps include bookkeeping, observer work, lock acquisition and scheduling; no single cause is attributed',
            'Mixed gaps are observed subsets, not additive serialized CPU cost; no-wait dequeue uses no substituted endpoint',
            'Counter anchors are non-atomic snapshots, not the same-frame cohort or full-session FPS',
            'Source screenshot PTS is identity only; no wall/UPTIME/phone/budget epoch subtraction',
            'File roles/clock declarations cannot independently prove the same host or frozen attempt identity',
            'Native trace alone does not establish pending-at-end producer quiescence',
            'No end-to-end, optical, acoustic, touch, phone presentation or WAN latency is calculated'])


def analyze_files(capture=None, native=None, feed=None, window=None):
    paths = dict(capture=capture,native=native,feed=feed)
    selected = [Path(path).absolute() for path in paths.values() if path is not None]
    expected = dict(capture='capture.jsonl',native='capture.jsonl.native.jsonl',feed='feed.jsonl')
    if (any(path is not None and Path(path).name!=expected[role] for role,path in paths.items())
            or len({str(path.parent) for path in selected})>1):
        raise ValueError('one_owned_attempt_layout_required')
    return analyze_streams({role:read_stream(paths[role],role) for role in ROLES},window)


def main():
    parser = argparse.ArgumentParser(description='Bounded numeric host-only timing analysis')
    parser.add_argument('--attempt-dir', type=Path)
    parser.add_argument('--capture', type=Path)
    parser.add_argument('--native', type=Path)
    parser.add_argument('--feed', type=Path)
    parser.add_argument('--window-start-ns', type=int)
    parser.add_argument('--window-end-ns', type=int)
    args = parser.parse_args()
    if args.attempt_dir is not None:
        if any(path is not None for path in (args.capture,args.native,args.feed)):
            print(json.dumps(dict(schema='host-region-analysis-v1',valid=False,error_code=1)))
            return 2
        args.capture=args.attempt_dir/'capture.jsonl'
        args.native=args.attempt_dir/'capture.jsonl.native.jsonl'
        args.feed=args.attempt_dir/'feed.jsonl'
    window = None if args.window_start_ns is args.window_end_ns is None else (args.window_start_ns,args.window_end_ns)
    try: result = analyze_files(args.capture,args.native,args.feed,window)
    except ValueError:
        print(json.dumps(dict(schema='host-region-analysis-v1',valid=False,error_code=1)))
        return 2
    print(json.dumps(result,separators=(',',':'),sort_keys=True))
    return 0


if __name__=='__main__': raise SystemExit(main())
