#!/usr/bin/env python3
"""Sanitize bounded capture/VT events and join only the same source screenshot PTS.

All cross-process intervals use explicitly identical host CLOCK_MONOTONIC.
Screenshot PTS is a production estimate, not guest media PTS or phone latency.
"""
import json
import math
from pathlib import Path

MAX_BYTES = 16 * 1024 * 1024
MAX_ROWS = 24002
EVENTS = {'trace_clock', 'trace_summary', 'capture_enqueue', 'raw_submit',
          'raw_drop', 'encoded_egress', 'raw_read', 'vt_frame', 'vt_submit_return'}
NUMBERS = {
    'clock_before_ns', 'clock_after_ns', 'unix_ns', 'wall_clock_precision_ns',
    'capture_seq', 'screenshot_seq', 'native_input_seq', 'source_pts_us', 'grpc_return_ns', 'enqueue_ns',
    'width', 'height', 'raw_bytes', 'pending_count', 'replaced_capture_seq',
    'dequeue_ns', 'pipe_write_begin_ns', 'pipe_write_end_ns', 'skipped_frames',
    'encoded_read_complete_ns', 'socket_write_end_ns', 'au_bytes',
    'header_read_begin_ns', 'header_read_complete_ns', 'raw_read_begin_ns',
    'raw_read_complete_ns', 'slot_wait_begin_ns', 'slot_wait_end_ns',
    'pixel_conversion_begin_ns', 'pixel_conversion_end_ns', 'vt_submit_ns',
    'vt_callback_ns', 'vt_status', 'vt_submit_return_ns', 'vt_submit_status',
    'stdout_lock_wait_begin_ns', 'stdout_lock_acquired_ns',
    'stdout_write_begin_ns', 'stdout_write_end_ns', 'accepted_records',
    'written_records', 'dropped_records', 'record_limit', 'byte_limit',
}
BOOLEANS = {'idle_repeat', 'frame_dropped', 'keyframe', 'byte_capped', 'failed', 'clean_close'}


def sanitize(row):
    if (not isinstance(row, dict) or row.get('schema') != 'capture-vt-trace-v1'
            or row.get('process') not in ('python', 'swift') or row.get('event') not in EVENTS):
        return None
    clean = {name: row[name] for name in ('schema', 'process', 'event')}
    for name in NUMBERS:
        value = row.get(name)
        if type(value) in (int, float) and math.isfinite(value):
            clean[name] = value
    for name in BOOLEANS:
        if type(row.get(name)) is bool:
            clean[name] = row[name]
    if row.get('phase') in ('start', 'end'):
        clean['phase'] = row['phase']
    if row.get('clock_domain') == 'host_clock_gettime_CLOCK_MONOTONIC_ns':
        clean['clock_domain'] = row['clock_domain']
    if row.get('reason') == 'nonincreasing_source_pts':
        clean['reason'] = row['reason']
    return clean


def read_sanitized_trace(paths):
    records, status = [], []
    for path in paths:
        path = Path(path)
        result = {'available': path.is_file(), 'process': 'swift' if path.name.endswith('.native.jsonl') else 'python'}
        if not result['available']:
            status.append(result)
            continue
        if path.stat().st_size > MAX_BYTES:
            result['byte_bound_exceeded'] = True
            status.append(result)
            continue
        malformed = discarded = read = 0
        summary = None
        with path.open() as source:
            for line in source:
                read += 1
                if read > MAX_ROWS:
                    result['record_bound_exceeded'] = True
                    break
                if len(line) > 8192:
                    discarded += 1
                    continue
                try:
                    row = sanitize(json.loads(line))
                except (ValueError, TypeError):
                    malformed += 1
                    continue
                if row is not None:
                    records.append(row)
                    if row['event'] == 'trace_summary':
                        summary = row
                else:
                    discarded += 1
        result.update(lines_read=read, malformed=malformed, discarded=discarded,
                      summary_present=summary is not None,
                      clean_close=summary is not None and summary.get('clean_close') is True,
                      summary=summary)
        status.append(result)
    return {'scope': 'Whitelisted numeric capture/VT metadata only; no source pixels, logs or credentials',
            'clock': 'host_clock_gettime_CLOCK_MONOTONIC_ns', 'status': status, 'records': records}


def distribution(values):
    values = sorted(value for value in values if math.isfinite(value))
    if not values:
        return {'count': 0}
    def p(q):
        x = (len(values)-1)*q
        lo = int(x)
        return round(values[lo]+(values[min(lo+1,len(values)-1)]-values[lo])*(x-lo), 4)
    return dict(count=len(values), p50=p(.5), p95=p(.95), p99=p(.99),
                max=round(values[-1], 4), over_50_ms=sum(x > 50 for x in values),
                over_100_ms=sum(x > 100 for x in values))


def analyze_trace(trace):
    rows = trace.get('records', [])
    frames, captures, summaries, capture_by_key = {}, [], [], {}
    verified_clocks = {row.get('process') for row in rows if row.get('event') == 'trace_clock'
                       and row.get('clock_domain') == 'host_clock_gettime_CLOCK_MONOTONIC_ns'}
    common_clock_verified = {'python','swift'} <= verified_clocks
    duplicate_events = 0
    for row in rows:
        if row.get('event') == 'trace_summary':
            summaries.append(row)
        pts = row.get('source_pts_us')
        if type(pts) is not int:
            continue
        event = row['event']
        if event == 'capture_enqueue':
            captures.append(row)
            capture_by_key.setdefault((row.get('capture_seq'),pts),[]).append(row)
            continue
        frame = frames.setdefault(pts, {})
        if event in frame:
            duplicate_events += 1
        else:
            frame[event] = row
    metrics = {name: [] for name in ('enqueue_after_grpc_ms', 'raw_queue_ms', 'raw_pipe_ms',
        'native_payload_read_ms', 'native_slot_wait_ms', 'pixel_conversion_ms',
        'vt_submit_to_callback_ms', 'vt_submit_call_ms', 'stdout_lock_wait_ms',
        'callback_to_stdout_begin_ms', 'stdout_write_ms',
        'grpc_return_to_vt_callback_ms', 'grpc_return_to_encoded_socket_end_ms')}
    joined = []
    fields = {
        'enqueue_after_grpc_ms': ('grpc_return_ns', 'enqueue_ns'),
        'raw_queue_ms': ('enqueue_ns', 'dequeue_ns'),
        'raw_pipe_ms': ('pipe_write_begin_ns', 'pipe_write_end_ns'),
        'native_payload_read_ms': ('raw_read_begin_ns', 'raw_read_complete_ns'),
        'native_slot_wait_ms': ('slot_wait_begin_ns', 'slot_wait_end_ns'),
        'pixel_conversion_ms': ('pixel_conversion_begin_ns', 'pixel_conversion_end_ns'),
        'vt_submit_to_callback_ms': ('vt_submit_ns', 'vt_callback_ns'),
        'vt_submit_call_ms': ('vt_submit_ns', 'vt_submit_return_ns'),
        'stdout_lock_wait_ms': ('stdout_lock_wait_begin_ns', 'stdout_lock_acquired_ns'),
        'callback_to_stdout_begin_ms': ('vt_callback_ns', 'stdout_write_begin_ns'),
        'stdout_write_ms': ('stdout_write_begin_ns', 'stdout_write_end_ns'),
        'grpc_return_to_vt_callback_ms': ('grpc_return_ns', 'vt_callback_ns'),
        'grpc_return_to_encoded_socket_end_ms': ('grpc_return_ns', 'socket_write_end_ns')}
    invalid_intervals = ambiguous_capture_matches = 0
    for pts, events in frames.items():
        submit = events.get('raw_submit', {})
        matches = capture_by_key.get((submit.get('capture_seq'),pts),[])
        if len(matches) != 1:
            ambiguous_capture_matches += bool(submit)
            continue
        capture = matches[0]
        if submit.get('idle_repeat'):
            continue
        events['capture_enqueue'] = capture
        merged = {}
        for name in ('capture_enqueue', 'raw_submit', 'raw_read', 'vt_frame', 'vt_submit_return', 'encoded_egress'):
            merged.update(events.get(name, {}))
        result = {'source_pts_us': pts, 'capture_seq': capture.get('capture_seq'),
                  'screenshot_seq': capture.get('screenshot_seq'),
                  'grpc_return_ns': capture.get('grpc_return_ns'),
                  'joined_events': sorted(events)}
        for metric, (start, stop) in fields.items():
            if metric in ('grpc_return_to_vt_callback_ms',) and not common_clock_verified:
                continue
            a, b = merged.get(start), merged.get(stop)
            if type(a) is int and type(b) is int:
                if b < a:
                    invalid_intervals += 1
                    continue
                value = (b-a)/1e6
                metrics[metric].append(value)
                result[metric] = round(value, 4)
        joined.append(result)
    captures.sort(key=lambda row: row['grpc_return_ns'])
    gaps = []
    for left, right in zip(captures, captures[1:]):
        gap = (right['grpc_return_ns']-left['grpc_return_ns'])/1e6
        pts_gap = (right['source_pts_us']-left['source_pts_us'])/1000
        seq_left, seq_right = left.get('screenshot_seq'), right.get('screenshot_seq')
        seq_delta = ((seq_right-seq_left) & 0xffffffff) if (type(seq_left) is int and
            type(seq_right) is int and 0 <= seq_left <= 0xffffffff and 0 <= seq_right <= 0xffffffff) else None
        gaps.append({'left_capture_seq': left['capture_seq'], 'right_capture_seq': right['capture_seq'],
                     'screenshot_sequence_delta': seq_delta,
                     'right_source_pts_us': right['source_pts_us'], 'grpc_return_gap_ms': round(gap, 4),
                     'screenshot_pts_gap_ms': round(pts_gap, 4)})
    complete = sum(all(name in row['joined_events'] for name in
                      ('capture_enqueue','raw_submit','raw_read','vt_frame','encoded_egress')) for row in joined)
    return {'scope': 'Same screenshot PTS joined through local capture/VT; not source-to-phone latency',
            'capture_records': len(captures), 'complete_joined_frames': complete,
            'common_host_clock_verified': common_clock_verified,
            'ambiguous_capture_matches': ambiguous_capture_matches,
            'partial_joined_frames': len(joined)-complete, 'duplicate_same_pts_events': duplicate_events,
            'invalid_clock_intervals': invalid_intervals, 'trace_summaries': summaries,
            'stage_ms': {name: distribution(values) for name, values in metrics.items()},
            'grpc_return_gaps_ms': distribution([row['grpc_return_gap_ms'] for row in gaps]),
            'screenshot_pts_gaps_ms': distribution([row['screenshot_pts_gap_ms'] for row in gaps]),
            'largest_grpc_gaps': sorted(gaps,key=lambda row:row['grpc_return_gap_ms'],reverse=True)[:16],
            'slowest_local_frames': sorted(joined,key=lambda row:row.get('grpc_return_to_encoded_socket_end_ms',0),reverse=True)[:16],
            'limitations': ['Payload reads can wait on producer; read time is not pure copy CPU time',
                           'Raw pipe write overlaps native read and cannot be added as a serial stage',
                           'Screenshot PTS is not guest video decoder PTS',
                           'Tracing has overhead; compare a trace-disabled control',
                           'No optical, touch or acoustic A/V measurement']}
