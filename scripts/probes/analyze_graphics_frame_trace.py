#!/usr/bin/env python3
"""Export bounded numeric graphics frame events for the fixed source SurfaceView.

Mappings were checked against the full descriptor embedded in v58.2 and its
graphics_frame_event_parser.cc. Arbitrary names select the target privately;
only fixed numeric columns leave the processor. No device operation is made.
"""
import argparse
from collections import defaultdict
import csv
import io
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.probes.guest_perfetto_probe import (  # noqa: E402
    ProbeError, distribution, parse_integer, private_trace,
)

MAX_ROWS = 30000
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_TRACE_BYTES = 64 * 1024 * 1024
EVENTS = {1: 'dequeue', 2: 'sf_receipt_queue', 4: 'acquire_fence_signaled',
          5: 'latch', 8: 'present_fence_signaled'}
HEADERS = {
    ('trace_start_ns', 'trace_end_ns'): 'trace_bounds',
    ('clock_snapshot_id', 'trace_ts_ns', 'monotonic_ns', 'boottime_ns'): 'clock_relations',
    ('all_graphics_slice_count', 'target_slice_count', 'target_layer_name_count',
     'target_machine_count', 'target_buffer_event_count', 'target_phase_slice_count',
     'invalid_buffer_identity_count', 'unsupported_buffer_event_count',
     'target_min_ts_ns', 'target_max_ts_ns', 'graphics_parser_diagnostic_count',
     'loss_or_overrun_stat_count', 'loss_or_overrun_value',
     'other_import_error_stat_count', 'other_import_error_value'): 'coverage',
    ('slice_id', 'ts_ns', 'dur_ns', 'track_id', 'machine_id', 'buffer_id_low32',
     'frame_number_low32', 'event_type'): 'events',
}

# Buffer IDs are absent from args. The verified parser formats the track as
# "Buffer: %u %.*s". Require its entire private suffix and canonical uint token
# to agree, rather than accepting CAST's permissive prefix conversion.
SQL = """
CREATE PERFETTO TABLE _huoguo_graphics_target AS
SELECT f.*, t.name AS private_track_name, t.machine_id,
 CASE f.name WHEN 'Dequeue' THEN 1 WHEN 'Queue' THEN 2
 WHEN 'AcquireFenceSignaled' THEN 4 WHEN 'Latch' THEN 5
 WHEN 'PresentFenceSignaled' THEN 8 ELSE 0 END AS event_type
FROM frame_slice f JOIN track t ON t.id=f.track_id
WHERE f.layer_name LIKE '%app.morphe.android.youtube%'
 AND f.layer_name LIKE '%SurfaceView%';
CREATE PERFETTO TABLE _huoguo_graphics_buffer AS
WITH tokens AS (
 SELECT *, SUBSTR(private_track_name,9,
  INSTR(SUBSTR(private_track_name,9),' ')-1) AS buffer_token
 FROM _huoguo_graphics_target WHERE private_track_name LIKE 'Buffer: %'
), numbers AS (
 SELECT *, CAST(buffer_token AS INT) AS buffer_id_low32 FROM tokens
)
SELECT *, CASE WHEN buffer_token=CAST(buffer_id_low32 AS TEXT)
 AND buffer_id_low32 BETWEEN 0 AND 4294967295
 AND private_track_name='Buffer: '||buffer_token||' '||layer_name
 AND frame_number BETWEEN 0 AND 4294967295 THEN 1 ELSE 0 END AS identity_valid
FROM numbers;
SELECT start_ts AS trace_start_ns,end_ts AS trace_end_ns FROM trace_bounds;
SELECT b.snapshot_id AS clock_snapshot_id,b.ts AS trace_ts_ns,
 m.clock_value AS monotonic_ns,b.clock_value AS boottime_ns
FROM clock_snapshot b JOIN clock_snapshot m
 ON b.snapshot_id=m.snapshot_id AND b.machine_id IS m.machine_id
WHERE b.clock_id=6 AND m.clock_id=3 ORDER BY b.snapshot_id LIMIT 256;
SELECT
 (SELECT COUNT(*) FROM frame_slice) AS all_graphics_slice_count,
 (SELECT COUNT(*) FROM _huoguo_graphics_target) AS target_slice_count,
 (SELECT COUNT(DISTINCT layer_name) FROM _huoguo_graphics_target) AS target_layer_name_count,
 (SELECT COUNT(DISTINCT COALESCE(machine_id,0)) FROM _huoguo_graphics_target) AS target_machine_count,
 (SELECT COUNT(*) FROM _huoguo_graphics_buffer) AS target_buffer_event_count,
 (SELECT COUNT(*) FROM _huoguo_graphics_target WHERE private_track_name NOT LIKE 'Buffer: %') AS target_phase_slice_count,
 (SELECT COUNT(*) FROM _huoguo_graphics_buffer WHERE identity_valid=0) AS invalid_buffer_identity_count,
 (SELECT COUNT(*) FROM _huoguo_graphics_buffer WHERE event_type=0) AS unsupported_buffer_event_count,
 (SELECT MIN(ts) FROM _huoguo_graphics_buffer) AS target_min_ts_ns,
 (SELECT MAX(ts) FROM _huoguo_graphics_buffer) AS target_max_ts_ns,
 (SELECT COALESCE(SUM(value),0) FROM stats WHERE name='graphics_frame_event_parser_errors') AS graphics_parser_diagnostic_count,
 (SELECT COUNT(*) FROM stats WHERE value>0 AND
  (severity='data_loss' OR name LIKE '%lost%' OR name LIKE '%overrun%')) AS loss_or_overrun_stat_count,
 (SELECT COALESCE(SUM(value),0) FROM stats WHERE value>0 AND
  (severity='data_loss' OR name LIKE '%lost%' OR name LIKE '%overrun%')) AS loss_or_overrun_value,
 (SELECT COUNT(*) FROM stats WHERE value>0 AND severity='error'
  AND name!='graphics_frame_event_parser_errors'
  AND name NOT LIKE '%lost%' AND name NOT LIKE '%overrun%') AS other_import_error_stat_count,
 (SELECT COALESCE(SUM(value),0) FROM stats WHERE value>0 AND severity='error'
  AND name!='graphics_frame_event_parser_errors'
  AND name NOT LIKE '%lost%' AND name NOT LIKE '%overrun%') AS other_import_error_value;
SELECT id AS slice_id,ts AS ts_ns,dur AS dur_ns,track_id,
 COALESCE(machine_id,0) AS machine_id,buffer_id_low32,
 frame_number AS frame_number_low32,event_type
FROM _huoguo_graphics_buffer
WHERE identity_valid=1 AND event_type IN (1,2,4,5,8)
ORDER BY ts,id LIMIT 30000;
"""


def parse_results(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_OUTPUT_BYTES:
        raise ValueError('bounded_numeric_output_required')
    result = {}
    for block in raw.decode('ascii', 'strict').strip().split('\n\n'):
        reader = csv.reader(io.StringIO(block))
        header = tuple(next(reader))
        name = HEADERS.get(header)
        if name is None or name in result:
            raise ValueError('unexpected_numeric_section')
        rows = []
        for values in reader:
            if len(values) != len(header) or len(rows) >= MAX_ROWS:
                raise ValueError('invalid_numeric_row')
            rows.append({key: parse_integer(value) for key, value in zip(header, values)})
        result[name] = rows
    if set(result) != set(HEADERS.values()):
        raise ValueError('missing_numeric_section')
    if len(result['trace_bounds']) != 1 or len(result['coverage']) != 1:
        raise ValueError('invalid_numeric_coverage')
    for row in result['events']:
        if any(row[key] is None for key in row):
            raise ValueError('missing_event_identity_or_timestamp')
        if row['event_type'] not in EVENTS or not 0 <= row['buffer_id_low32'] < 2**32 \
                or not 0 <= row['frame_number_low32'] < 2**32:
            raise ValueError('invalid_event_identity')
    return result


def continuity(rows, start, end):
    times = sorted(row['ts_ns'] for row in rows if start < row['ts_ns'] <= end)
    frames = [row['frame_number_low32'] for row in rows if start < row['ts_ns'] <= end]
    gaps = [b-a for a, b in zip(times, times[1:])]
    clipped = [start, *times, end]
    return {'event_count': len(times), 'first_ts_ns': min(times, default=None),
            'last_ts_ns': max(times, default=None),
            'min_frame_number_low32': min(frames, default=None),
            'max_frame_number_low32': max(frames, default=None),
            'internal_event_gaps': distribution(gaps),
            'max_subwindow_without_marker_ns': max((b-a for a, b in zip(clipped, clipped[1:])), default=0)}


def summarize(sections, trace_bytes, requested_duration_ms=30000,
              max_trace_bytes=MAX_TRACE_BYTES):
    bounds, coverage = sections['trace_bounds'][0], sections['coverage'][0]
    start, end = bounds['trace_start_ns'], bounds['trace_end_ns']
    if start is None or end is None or end <= start:
        raise ValueError('invalid_trace_bounds')
    if coverage['target_layer_name_count'] > 1 or coverage['target_machine_count'] > 1:
        raise ValueError('multiple_target_layers_or_machines_require_new_selection')
    rows = sorted(sections['events'], key=lambda r: (r['ts_ns'], r['slice_id']))
    if any(not start <= row['ts_ns'] <= end or row['dur_ns'] != 0 for row in rows):
        raise ValueError('event_outside_bounds_or_noninstant')
    if len({row['slice_id'] for row in rows}) != len(rows):
        raise ValueError('duplicate_slice_id')
    stages = {event: [row for row in rows if row['event_type'] == event] for event in EVENTS}
    queued = stages[2]
    resets = sum(b['frame_number_low32'] < a['frame_number_low32']
                 for a, b in zip(queued, queued[1:]))
    groups, narrow_id_tracks = defaultdict(lambda: defaultdict(list)), defaultdict(set)
    for row in rows:
        key = (row['machine_id'], row['track_id'], row['buffer_id_low32'], row['frame_number_low32'])
        groups[key][row['event_type']].append(row)
        narrow_id_tracks[(row['machine_id'], row['buffer_id_low32'], row['frame_number_low32'])].add(row['track_id'])
    chains, identities = [], []
    ambiguous = invalid_order = 0
    for key, events in sorted(groups.items()):
        machine, track, buffer_id, frame = key
        collision = len(narrow_id_tracks[(machine, buffer_id, frame)]) > 1
        duplicate = any(len(events.get(stage, [])) > 1 for stage in (2, 4, 5, 8))
        identity = {'machine_id': machine, 'track_id': track,
                    'buffer_id_low32': buffer_id, 'frame_number_low32': frame,
                    'cross_track_low32_collision': collision,
                    'duplicate_stage': duplicate, 'observed_generation_reset': bool(resets),
                    'event_counts': {str(stage): len(events.get(stage, [])) for stage in EVENTS}}
        identities.append(identity)
        # Reset epochs cannot disambiguate delayed fence records. Reject the
        # whole observed reset window rather than assigning by nearest time.
        if collision or duplicate or resets:
            ambiguous += 1
            continue
        def stamp(stage):
            return events[stage][0]['ts_ns'] if events.get(stage) else None
        q, a, l, p = stamp(2), stamp(4), stamp(5), stamp(8)
        if (q is not None and l is not None and l < q) or \
                (l is not None and p is not None and p < l) or \
                (q is not None and p is not None and p < q):
            invalid_order += 1
            continue
        chains.append({'machine_id': machine, 'track_id': track,
                       'buffer_id_low32': buffer_id, 'frame_number_low32': frame,
                       'dequeue_ts_ns': stamp(1) if len(events.get(1, [])) == 1 else None,
                       'sf_receipt_queue_ts_ns': q, 'acquire_fence_ts_ns': a,
                       'latch_ts_ns': l, 'present_fence_ts_ns': p,
                       'queue_to_latch_ns': l-q if q is not None and l is not None else None,
                       'latch_to_present_ns': p-l if l is not None and p is not None else None,
                       'queue_to_present_ns': p-q if q is not None and p is not None else None,
                       'queue_without_latch_observed': q is not None and l is None,
                       'queue_without_present_observed': q is not None and p is None,
                       'latch_without_present_observed': l is not None and p is None})
    clocks = [row for row in sections['clock_relations']
              if all(row[key] is not None for key in row)]
    offsets = [row['monotonic_ns']-row['boottime_ns'] for row in clocks]
    presentations = stages[8]
    present_times = sorted(row['ts_ns'] for row in presentations)
    max_pair = max(zip(present_times, present_times[1:]), key=lambda pair: pair[1]-pair[0], default=None)
    largest_gap = None
    if max_pair:
        gap_start, gap_end = max_pair
        chain_by_key = {(item['machine_id'], item['track_id'], item['buffer_id_low32'],
                         item['frame_number_low32']): item for item in chains}
        diagnostic_by_key = {(item['machine_id'], item['track_id'], item['buffer_id_low32'],
                              item['frame_number_low32']): item for item in identities}
        def endpoint(ts):
            result = []
            for row in presentations:
                if row['ts_ns'] != ts:
                    continue
                key = (row['machine_id'], row['track_id'], row['buffer_id_low32'], row['frame_number_low32'])
                result.append({'present_event': row, 'identity_chain': chain_by_key.get(key),
                               'identity_diagnostic': diagnostic_by_key[key]})
            return result
        start_identities, end_identities = endpoint(gap_start), endpoint(gap_end)
        between = []
        frame_interval_verified = len(start_identities) == len(end_identities) == 1 and not resets
        if frame_interval_verified:
            first, last = start_identities[0]['present_event'], end_identities[0]['present_event']
            frame_interval_verified = first['machine_id'] == last['machine_id'] and first['frame_number_low32'] < last['frame_number_low32']
            if frame_interval_verified:
                for key, diagnostic in diagnostic_by_key.items():
                    if key[0] == first['machine_id'] and first['frame_number_low32'] < key[3] < last['frame_number_low32']:
                        between.append({'identity_diagnostic': diagnostic, 'identity_chain': chain_by_key.get(key)})
                between.sort(key=lambda item: (item['identity_diagnostic']['frame_number_low32'], item['identity_diagnostic']['track_id']))
        largest_gap = {'start_present_ts_ns': gap_start, 'end_present_ts_ns': gap_end,
                       'gap_ns': gap_end-gap_start,
                       'start_present_identities': start_identities,
                       'end_present_identities': end_identities,
                       'intervening_frame_number_interval_assumes_unchanged_generation': frame_interval_verified,
                       'intervening_frame_observations': between,
                       'sf_receipt_queue': continuity(stages[2], gap_start, gap_end),
                       'latch': continuity(stages[5], gap_start, gap_end),
                       'acquire_fence': continuity(stages[4], gap_start, gap_end),
                       'start_monotonic_observed_offset_range_ns':
                           [gap_start+min(offsets), gap_start+max(offsets)] if offsets else None,
                       'end_monotonic_observed_offset_range_ns':
                           [gap_end+min(offsets), gap_end+max(offsets)] if offsets else None}
    last_present = max(present_times, default=None)
    return {
        'schema': 1,
        'scope': 'M1 source SurfaceView SF-receipt queue, batch-latch and SF present-event records',
        'present_fence_branch_verified_for_guest': False,
        'latch_timestamp_is_per_buffer_completion': False,
        'requested_duration_ms': requested_duration_ms,
        'recorded_bounds_duration_ms': round((end-start)/1e6, 3),
        'duration_requirement_met_with_500ms_tolerance': end-start >= (requested_duration_ms-500)*1_000_000,
        'trace_bounds_alone_verifies_capture_duration': False,
        'clock_snapshot_span_ms': round((max(row['trace_ts_ns'] for row in clocks)-min(row['trace_ts_ns'] for row in clocks))/1e6, 3) if clocks else None,
        'bounds_before_first_clock_snapshot_ns': min((row['trace_ts_ns'] for row in clocks), default=start)-start,
        'raw_trace_bytes': trace_bytes, 'configured_max_trace_bytes': max_trace_bytes,
        'trace_near_size_limit': trace_bytes >= .95*max_trace_bytes,
        'target_surfaceview_observed': bool(rows),
        'event_rows_at_limit': len(rows) >= MAX_ROWS,
        'source_event_span_ms': round((max(row['ts_ns'] for row in rows)-min(row['ts_ns'] for row in rows))/1e6, 3) if rows else 0,
        'event_counts': {str(stage): len(stages[stage]) for stage in EVENTS},
        'event_type_legend': EVENTS,
        'event_gaps': {str(stage): distribution([b['ts_ns']-a['ts_ns'] for a, b in zip(stages[stage], stages[stage][1:])]) for stage in EVENTS},
        'clock_relation_summary': {
            'snapshot_count': len(clocks), 'clock_id_3_is_monotonic': True,
            'clock_id_6_is_boottime': True,
            'trace_timestamps_match_boottime': bool(clocks) and all(row['trace_ts_ns']==row['boottime_ns'] for row in clocks),
            'monotonic_minus_boottime_min_ns': min(offsets, default=None),
            'monotonic_minus_boottime_max_ns': max(offsets, default=None),
            'conversion': 'MONOTONIC = imported BOOTTIME + snapshot(MONOTONIC-BOOTTIME); observed range is not an exact offset between snapshots',
        },
        'identity_association': {
            'observed_queue_frame_reset_count': resets,
            'identity_count': len(groups), 'unambiguous_identity_count': len(chains),
            'ambiguous_identity_count': ambiguous, 'invalid_event_order_count': invalid_order,
            'cross_track_low32_collision_identity_count': sum(item['cross_track_low32_collision'] for item in identities),
            'duplicate_stage_identity_count': sum(item['duplicate_stage'] for item in identities),
            'complete_queue_latch_present_count': sum(all(item[k] is not None for k in ('sf_receipt_queue_ts_ns','latch_ts_ns','present_fence_ts_ns')) for item in chains),
            'queue_without_latch_observed_count': sum(item['queue_without_latch_observed'] for item in chains),
            'queue_without_present_observed_count': sum(item['queue_without_present_observed'] for item in chains),
            'latch_without_present_observed_count': sum(item['latch_without_present_observed'] for item in chains),
            'queue_to_latch': distribution([item['queue_to_latch_ns'] for item in chains if item['queue_to_latch_ns'] is not None]),
            'latch_to_present': distribution([item['latch_to_present_ns'] for item in chains if item['latch_to_present_ns'] is not None]),
            'queue_to_present': distribution([item['queue_to_present_ns'] for item in chains if item['queue_to_present_ns'] is not None]),
        },
        'censored_edges': {
            'trace_start_to_first_present_ns': present_times[0]-start if present_times else None,
            'last_present_to_trace_end_ns': end-present_times[-1] if present_times else None,
            'first_and_last_display_holds_are_censored': True,
            'queue_after_last_present_without_present_count': sum(item['sf_receipt_queue_ts_ns'] is not None and item['present_fence_ts_ns'] is None and last_present is not None and item['sf_receipt_queue_ts_ns']>=last_present for item in chains),
            'missing_stage_is_not_classified_as_packet_loss_or_drop': True,
        },
        'largest_present_gap': largest_gap,
        'numeric_identity_chains': chains, 'numeric_identity_diagnostics': identities,
        **sections,
        'limitations': [
            'The Queue timestamp in this AOSP setBuffer path samples SF transaction receipt, not producer queueBuffer or codec release',
            'Only setBuffer paths with positive dequeueTime register and trace a layer; absent events do not prove inactivity',
            'Dequeue frame numbers may be backfilled by the parser from the following Queue and are not native Dequeue packet identities',
            'Buffer IDs and frame numbers are uint32 and may be truncated from wider native identities; observed uniqueness does not prove no latent collision',
            'Associations require one unchanged layer and producer generation in this trace; observed resets, cross-track collisions and duplicate stages are rejected',
            'No closest-timestamp association is used and no transaction64-to-graphics32 association is made',
            'AcquireFence absent or invalid can omit its event; zero Acquire events never establishes immediate buffer readiness',
            'Parser-derived queue/acquire/latch/present timing args are not used because per-buffer state can be stale',
            'Latch records a SurfaceFlinger batch latchTime, not per-buffer GPU readiness or operation-completion time; allowLatchUnsignaled may bypass ready-fence checks',
            'Queue-to-latch can include desired-present-time, acquire readiness, backpressure and scheduling; this source has no desired-present-time field to separate them',
            'The PresentFenceSignaled enum name does not prove a real hardware-fence path: AOSP also emits PRESENT_FENCE using an HWC present-time estimate when no valid fence exists; guest branch is unverified',
            'Present fences may be emitted only on a later buffer event; missing tail events remain censored or unobserved',
            'Retroactive dequeue and present timestamps can expand trace_bounds; bounds duration alone does not verify a complete recording interval after the capture command',
            'graphics_frame_event_parser_errors can include valid present events; it is reported separately from loss or overrun',
            'No positive loss statistic does not guarantee complete instrumentation or capture',
            'Present-fence timestamps are compositor evidence, not physical panel photons, remote phone display or audio/video alignment',
            'Queue-to-present is an SF-receipt-to-present interval, not codec-to-display or host-to-phone end-to-end latency',
            'Largest-gap marker continuity describes observations and does not alone identify the responsible stage',
        ],
    }


def analyze(trace, processor, requested_duration_ms=30000, max_trace_bytes=MAX_TRACE_BYTES):
    trace = private_trace(trace, max_bytes=max_trace_bytes)
    result = subprocess.run([str(processor), 'query', '-f', '-', str(trace)],
                            input=SQL.encode('ascii'), stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=45)
    if result.returncode:
        raise ValueError('bounded_graphics_trace_query_failed')
    return summarize(parse_results(result.stdout), trace.stat().st_size,
                     requested_duration_ms, max_trace_bytes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trace', type=Path, required=True)
    parser.add_argument('--processor', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--requested-seconds', type=float, default=30)
    parser.add_argument('--max-trace-mib', type=int, choices=(64,128), default=64)
    args = parser.parse_args()
    if args.output.exists() or not 0 < args.requested_seconds <= 45:
        parser.error('fresh numeric output and bounded duration required')
    try:
        report = analyze(args.trace, args.processor, round(args.requested_seconds*1000),
                         args.max_trace_mib*1024*1024)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as out:
            json.dump(report, out, indent=2, allow_nan=False)
            out.write('\n')
    except (ProbeError, ValueError, OSError, UnicodeError, subprocess.TimeoutExpired):
        raise SystemExit('bounded_graphics_frame_analysis_failed') from None
    print(json.dumps({key: report[key] for key in (
        'recorded_bounds_duration_ms','event_counts','identity_association',
        'largest_present_gap','clock_relation_summary','censored_edges')}))


if __name__ == '__main__':
    main()
