#!/usr/bin/env python3
"""Export bounded numeric SurfaceView buffer identities from a private trace.

The field mappings were checked against the M1 Android 17 trace captured on
2026-10-01 with Perfetto trace_processor v58.2. Snapshot timestamps are
observations of state; they are not latch, present-fence or optical timestamps.
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
    MAX_TRACE_BYTES, ProbeError, distribution, parse_integer, private_trace,
)

MAX_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_SECTION_ROWS = 30000
SURFACE_TRACE_MAX_BYTES = MAX_TRACE_BYTES
HEADERS = {
    ('trace_start_ns', 'trace_end_ns'): 'trace_bounds',
    ('analysis_start_ns', 'analysis_end_ns'): 'analysis_window',
    ('clock_snapshot_id', 'trace_ts_ns', 'monotonic_ns', 'boottime_ns'): 'clock_relations',
    ('snapshot_count', 'snapshot_min_ts_ns', 'snapshot_max_ts_ns',
     'invalid_snapshot_time_count', 'target_layer_row_count',
     'target_layer_id_count', 'target_buffer_layer_id_count',
     'transaction_entry_count', 'transaction_min_ts_ns', 'transaction_max_ts_ns',
     'transaction_entries_before_bounds', 'transaction_entries_in_bounds',
     'target_buffer_transaction_count', 'target_buffer_transactions_before_bounds',
     'import_error_or_loss_count', 'import_error_or_loss_value'): 'coverage',
    ('layer_row_id', 'snapshot_id', 'snapshot_ts_ns', 'layer_id', 'curr_frame',
     'active_width', 'active_height', 'queued_frames', 'refresh_pending',
     'hwc_composition_type', 'snapshot_vsync_id'): 'layer_observations',
    ('transaction_row_id', 'transaction_entry_id', 'entry_ts_ns', 'entry_vsync_id',
     'transaction_id', 'layer_id', 'buffer_frame_number', 'buffer_id',
     'cached_buffer_id', 'buffer_flags', 'state_frame_number', 'post_time_ns',
     'transaction_vsync_id'): 'buffer_transactions',
}

# Names and arbitrary proto arguments are used only to select the fixed target;
# no name, free-text argument, base64 proto or raw trace content is exported.
SQL = """
INCLUDE PERFETTO MODULE android.winscope.surfaceflinger;
CREATE PERFETTO TABLE _huoguo_target_layers AS
SELECT DISTINCT layer_id FROM surfaceflinger_layer
WHERE layer_name LIKE '%app.morphe.android.youtube%'
 AND layer_name LIKE '%SurfaceView%' __LAYER_SELECTION__;
CREATE PERFETTO TABLE _huoguo_analysis_window AS
SELECT MAX(start_ts,
 (SELECT MIN(ts) FROM surfaceflinger_layers_snapshot WHERE has_invalid_elapsed_ts=0),
 (SELECT MIN(ts) FROM surfaceflinger_transactions)) AS analysis_start_ns,
 MIN(end_ts,
 (SELECT MAX(ts) FROM surfaceflinger_layers_snapshot WHERE has_invalid_elapsed_ts=0),
 (SELECT MAX(ts) FROM surfaceflinger_transactions)) AS analysis_end_ns
FROM trace_bounds;
CREATE PERFETTO TABLE _huoguo_transaction_metadata AS
SELECT p.id AS entry_id, ids.int_value AS transaction_id,
 post.int_value AS post_time_ns, vsync.int_value AS transaction_vsync_id
FROM surfaceflinger_transactions p
JOIN args ids ON ids.arg_set_id=p.arg_set_id
 AND ids.flat_key='transactions.transaction_id'
LEFT JOIN args post ON post.arg_set_id=p.arg_set_id
 AND post.key=REPLACE(ids.key,'.transaction_id','.post_time')
LEFT JOIN args vsync ON vsync.arg_set_id=p.arg_set_id
 AND vsync.key=REPLACE(ids.key,'.transaction_id','.vsync_id');

SELECT start_ts AS trace_start_ns,end_ts AS trace_end_ns FROM trace_bounds;
SELECT analysis_start_ns,analysis_end_ns FROM _huoguo_analysis_window;
SELECT b.snapshot_id AS clock_snapshot_id,b.ts AS trace_ts_ns,
 m.clock_value AS monotonic_ns,b.clock_value AS boottime_ns
FROM clock_snapshot b JOIN clock_snapshot m ON b.snapshot_id=m.snapshot_id
WHERE b.clock_id=6 AND m.clock_id=3 ORDER BY b.snapshot_id LIMIT 256;
SELECT
 (SELECT COUNT(*) FROM surfaceflinger_layers_snapshot) AS snapshot_count,
 (SELECT MIN(ts) FROM surfaceflinger_layers_snapshot) AS snapshot_min_ts_ns,
 (SELECT MAX(ts) FROM surfaceflinger_layers_snapshot) AS snapshot_max_ts_ns,
 (SELECT COUNT(*) FROM surfaceflinger_layers_snapshot
  WHERE has_invalid_elapsed_ts!=0) AS invalid_snapshot_time_count,
 (SELECT COUNT(*) FROM surfaceflinger_layer
  WHERE layer_id IN (SELECT layer_id FROM _huoguo_target_layers)) AS target_layer_row_count,
 (SELECT COUNT(*) FROM _huoguo_target_layers) AS target_layer_id_count,
 (SELECT COUNT(DISTINCT layer_id) FROM surfaceflinger_layer
  WHERE layer_id IN (SELECT layer_id FROM _huoguo_target_layers)
  AND EXTRACT_ARG(arg_set_id,'active_buffer.width')>0) AS target_buffer_layer_id_count,
 (SELECT COUNT(*) FROM surfaceflinger_transactions) AS transaction_entry_count,
 (SELECT MIN(ts) FROM surfaceflinger_transactions) AS transaction_min_ts_ns,
 (SELECT MAX(ts) FROM surfaceflinger_transactions) AS transaction_max_ts_ns,
 (SELECT COUNT(*) FROM surfaceflinger_transactions
  WHERE ts<(SELECT start_ts FROM trace_bounds)) AS transaction_entries_before_bounds,
 (SELECT COUNT(*) FROM surfaceflinger_transactions
  WHERE ts BETWEEN (SELECT start_ts FROM trace_bounds)
   AND (SELECT end_ts FROM trace_bounds)) AS transaction_entries_in_bounds,
 (SELECT COUNT(*) FROM android_surfaceflinger_transaction
  WHERE layer_id IN (SELECT layer_id FROM _huoguo_target_layers)
  AND EXTRACT_ARG(arg_set_id,'buffer_data.frame_number') IS NOT NULL)
  AS target_buffer_transaction_count,
 (SELECT COUNT(*) FROM android_surfaceflinger_transaction t
  JOIN surfaceflinger_transactions p ON p.id=t.snapshot_id
  WHERE t.layer_id IN (SELECT layer_id FROM _huoguo_target_layers)
  AND EXTRACT_ARG(t.arg_set_id,'buffer_data.frame_number') IS NOT NULL
  AND p.ts<(SELECT start_ts FROM trace_bounds))
  AS target_buffer_transactions_before_bounds,
 (SELECT COUNT(*) FROM stats WHERE value>0 AND
  (severity IN ('error','data_loss') OR name LIKE '%lost%' OR name LIKE '%overrun%'))
  AS import_error_or_loss_count,
 (SELECT COALESCE(SUM(value),0) FROM stats WHERE value>0 AND
  (severity IN ('error','data_loss') OR name LIKE '%lost%' OR name LIKE '%overrun%'))
  AS import_error_or_loss_value;

SELECT l.id AS layer_row_id,l.snapshot_id,s.ts AS snapshot_ts_ns,l.layer_id,
 EXTRACT_ARG(l.arg_set_id,'curr_frame') AS curr_frame,
 EXTRACT_ARG(l.arg_set_id,'active_buffer.width') AS active_width,
 EXTRACT_ARG(l.arg_set_id,'active_buffer.height') AS active_height,
 EXTRACT_ARG(l.arg_set_id,'queued_frames') AS queued_frames,
 EXTRACT_ARG(l.arg_set_id,'refresh_pending') AS refresh_pending,
 l.hwc_composition_type,EXTRACT_ARG(s.arg_set_id,'vsync_id') AS snapshot_vsync_id
FROM surfaceflinger_layer l
JOIN surfaceflinger_layers_snapshot s ON s.id=l.snapshot_id
WHERE l.layer_id IN (SELECT layer_id FROM _huoguo_target_layers)
 AND EXTRACT_ARG(l.arg_set_id,'active_buffer.width')>0
 AND s.has_invalid_elapsed_ts=0
 AND s.ts BETWEEN (SELECT analysis_start_ns FROM _huoguo_analysis_window)
  AND (SELECT analysis_end_ns FROM _huoguo_analysis_window)
ORDER BY s.ts,l.layer_id,l.id LIMIT 30000;

SELECT t.id AS transaction_row_id,t.snapshot_id AS transaction_entry_id,
 p.ts AS entry_ts_ns,p.vsync_id AS entry_vsync_id,t.transaction_id,t.layer_id,
 EXTRACT_ARG(t.arg_set_id,'buffer_data.frame_number') AS buffer_frame_number,
 EXTRACT_ARG(t.arg_set_id,'buffer_data.buffer_id') AS buffer_id,
 EXTRACT_ARG(t.arg_set_id,'buffer_data.cached_buffer_id') AS cached_buffer_id,
 EXTRACT_ARG(t.arg_set_id,'buffer_data.flags') AS buffer_flags,
 EXTRACT_ARG(t.arg_set_id,'frame_number') AS state_frame_number,
 m.post_time_ns,m.transaction_vsync_id
FROM android_surfaceflinger_transaction t
JOIN surfaceflinger_transactions p ON p.id=t.snapshot_id
LEFT JOIN _huoguo_transaction_metadata m
 ON m.entry_id=t.snapshot_id AND m.transaction_id=t.transaction_id
WHERE t.layer_id IN (SELECT layer_id FROM _huoguo_target_layers)
 AND t.transaction_type='LAYER_CHANGED'
 AND EXTRACT_ARG(t.arg_set_id,'buffer_data.frame_number') IS NOT NULL
 AND p.ts BETWEEN (SELECT analysis_start_ns FROM _huoguo_analysis_window)
  AND (SELECT analysis_end_ns FROM _huoguo_analysis_window)
ORDER BY p.ts,t.layer_id,t.id LIMIT 30000;
"""


def build_sql(layer_id=None):
    if layer_id is not None and (isinstance(layer_id, bool) or not isinstance(layer_id, int)
                                 or not 0 < layer_id < (1 << 32)):
        raise ValueError('invalid_selected_layer_id')
    return SQL.replace('__LAYER_SELECTION__', '' if layer_id is None else f'AND layer_id={layer_id}')


def private_buffer_trace(path, max_bytes=SURFACE_TRACE_MAX_BYTES):
    return private_trace(path, max_bytes=max_bytes)


def parse_results(raw):
    """Reject unrecognized columns and values before anything can be saved."""
    if not isinstance(raw, bytes) or len(raw) > MAX_OUTPUT_BYTES:
        raise ValueError('bounded_numeric_output_required')
    sections = {}
    for block in raw.decode('ascii', 'strict').strip().split('\n\n'):
        if not block.strip():
            continue
        reader = csv.reader(io.StringIO(block))
        header = tuple(next(reader))
        name = HEADERS.get(header)
        if name is None or name in sections:
            raise ValueError('unexpected_numeric_section')
        rows = []
        for values in reader:
            if len(values) != len(header) or len(rows) >= MAX_SECTION_ROWS:
                raise ValueError('invalid_numeric_row')
            rows.append({key: parse_integer(value) for key, value in zip(header, values)})
        sections[name] = rows
    if set(sections) != set(HEADERS.values()):
        raise ValueError('missing_numeric_section')
    if any(len(sections[name]) != 1 for name in ('trace_bounds', 'analysis_window', 'coverage')):
        raise ValueError('invalid_numeric_coverage')
    for row in sections['layer_observations']:
        if any(row[key] is None for key in ('layer_row_id', 'snapshot_id',
                                          'snapshot_ts_ns', 'layer_id')):
            raise ValueError('missing_observation_identity')
    for row in sections['buffer_transactions']:
        if any(row[key] is None for key in ('transaction_row_id', 'transaction_entry_id',
                                          'entry_ts_ns', 'layer_id', 'buffer_frame_number')):
            raise ValueError('missing_transaction_identity')
    return sections


def build_runs(rows):
    """Compress consecutive equal frames without interpreting snapshot time as presentation."""
    groups = defaultdict(list)
    for row in rows:
        if row['curr_frame'] is not None and row['curr_frame'] > 0:
            groups[row['layer_id']].append(row)
    runs = []
    for layer_id, observations in sorted(groups.items()):
        observations.sort(key=lambda row: (row['snapshot_ts_ns'], row['layer_row_id']))
        epoch = 0
        previous_frame = None
        local = []
        for row in observations:
            frame, ts = row['curr_frame'], row['snapshot_ts_ns']
            if previous_frame is not None and frame < previous_frame:
                epoch += 1
            if local and local[-1]['curr_frame'] == frame and local[-1]['observed_epoch_id'] == epoch:
                local[-1]['last_seen_ts_ns'] = ts
                local[-1]['observation_count'] += 1
            else:
                local.append({'layer_id': layer_id, 'observed_epoch_id': epoch,
                              'curr_frame': frame, 'first_seen_ts_ns': ts,
                              'last_seen_ts_ns': ts, 'observation_count': 1})
            previous_frame = frame
        for index, run in enumerate(local):
            following = local[index + 1] if index + 1 < len(local) else None
            same_epoch = following is not None and following['observed_epoch_id'] == run['observed_epoch_id']
            run['next_frame_first_seen_ts_ns'] = following['first_seen_ts_ns'] if same_epoch else None
            run['observed_hold_lower_bound_ns'] = run['last_seen_ts_ns'] - run['first_seen_ts_ns']
            run['first_seen_gap_to_next_frame_ns'] = (
                following['first_seen_ts_ns'] - run['first_seen_ts_ns'] if same_epoch else None)
            run['frame_number_step_to_next'] = following['curr_frame'] - run['curr_frame'] if same_epoch else None
            run['left_censored'] = index == 0 or local[index - 1]['observed_epoch_id'] != run['observed_epoch_id']
            run['right_censored'] = not same_epoch
        runs.extend(local)
    return runs


def summarize(sections, trace_bytes, requested_duration_ms=30000,
              max_trace_bytes=SURFACE_TRACE_MAX_BYTES, selected_layer_id=None):
    bounds = sections['trace_bounds'][0]
    coverage = sections['coverage'][0]
    start, end = bounds['trace_start_ns'], bounds['trace_end_ns']
    if start is None or end is None or end < start or requested_duration_ms <= 0:
        raise ValueError('invalid_trace_bounds')
    if coverage['target_buffer_layer_id_count'] > 1 and selected_layer_id is None:
        raise ValueError('multiple_buffer_layers_require_explicit_selection')
    if selected_layer_id is not None and coverage['target_buffer_layer_id_count'] != 1:
        raise ValueError('selected_buffer_layer_not_observed')
    window = sections['analysis_window'][0]
    window_start, window_end = window['analysis_start_ns'], window['analysis_end_ns']
    window_valid = window_start is not None and window_end is not None and window_start <= window_end
    if window_valid and not start <= window_start <= window_end <= end:
        raise ValueError('invalid_intersection_window')
    for name, ts_key in (('layer_observations', 'snapshot_ts_ns'), ('buffer_transactions', 'entry_ts_ns')):
        if any(not window_valid or not window_start <= row[ts_key] <= window_end for row in sections[name]):
            raise ValueError('record_outside_source_intersection')
    runs = build_runs(sections['layer_observations'])
    observed_layer_ids = sorted(set(run['layer_id'] for run in runs))
    if selected_layer_id is not None and any(layer_id != selected_layer_id for layer_id in observed_layer_ids):
        raise ValueError('unexpected_selected_buffer_layer')
    runs_by_identity = defaultdict(list)
    for run in runs:
        runs_by_identity[(run['layer_id'], run['curr_frame'])].append(run)
    transactions = sections['buffer_transactions']
    transactions_by_identity = defaultdict(list)
    for row in transactions:
        transactions_by_identity[(row['layer_id'], row['buffer_frame_number'])].append(row)
    matches, ambiguous, no_transaction, negative = [], 0, 0, 0
    for identity, observations in sorted(runs_by_identity.items()):
        candidates = transactions_by_identity.get(identity, [])
        # A repeated frame number or duplicated transaction is not silently resolved.
        if len(observations) != 1 or len(candidates) > 1:
            ambiguous += 1
            continue
        if not candidates:
            no_transaction += 1
            continue
        run, transaction = observations[0], candidates[0]
        delta = run['first_seen_ts_ns'] - transaction['entry_ts_ns']
        if delta < 0:
            negative += 1
            continue
        matches.append({'layer_id': identity[0], 'frame_number': identity[1],
                        'transaction_row_id': transaction['transaction_row_id'],
                        'transaction_id': transaction['transaction_id'],
                        'buffer_id': transaction['buffer_id'],
                        'entry_ts_ns': transaction['entry_ts_ns'],
                        'post_time_ns': transaction['post_time_ns'],
                        'first_seen_ts_ns': run['first_seen_ts_ns'],
                        'entry_to_first_observation_ns': delta,
                        'post_to_first_observation_raw_clock_difference_ns': (
                            run['first_seen_ts_ns'] - transaction['post_time_ns']
                            if transaction['post_time_ns'] is not None else None),
                        'left_censored': run['left_censored']})
    summaries = []
    for layer_id in sorted(set(run['layer_id'] for run in runs)):
        selected = [run for run in runs if run['layer_id'] == layer_id]
        tx = [row for row in transactions if row['layer_id'] == layer_id]
        stamps = sorted(set(row['entry_ts_ns'] for row in tx))
        summaries.append({'layer_id': layer_id, 'observed_frame_runs': len(selected),
                          'observed_reset_count': max(run['observed_epoch_id'] for run in selected),
                          'repeated_frame_observations': sum(run['observation_count'] - 1 for run in selected),
                          'frame_number_jumps_over_one': sum((run['frame_number_step_to_next'] or 0) > 1 for run in selected),
                          'first_observation_gaps': distribution([run['first_seen_gap_to_next_frame_ns'] for run in selected
                                                                if run['first_seen_gap_to_next_frame_ns'] is not None]),
                          'observed_hold_lower_bounds': distribution([run['observed_hold_lower_bound_ns'] for run in selected]),
                          'buffer_transaction_rows': len(tx),
                          'unique_buffer_ids': len(set(row['buffer_id'] for row in tx if row['buffer_id'] is not None)),
                          'transaction_entry_gaps': distribution([b - a for a, b in zip(stamps, stamps[1:])])})
    duration = end - start
    sources_present = coverage['snapshot_count'] > 0 and coverage['transaction_entry_count'] > 0
    rows_at_limit = {name: len(sections[name]) >= MAX_SECTION_ROWS
                     for name in ('layer_observations', 'buffer_transactions')}
    clock_relations = [row for row in sections['clock_relations']
                       if all(row[key] is not None for key in ('trace_ts_ns', 'monotonic_ns', 'boottime_ns'))]
    clock_offsets = [row['monotonic_ns'] - row['boottime_ns'] for row in clock_relations]
    raw_post_differences = [row['post_to_first_observation_raw_clock_difference_ns'] for row in matches
                            if row['post_to_first_observation_raw_clock_difference_ns'] is not None]
    return {
        'schema': 1, 'scope': 'M1 source app video SurfaceView numeric buffer identity observations',
        'requested_duration_ms': requested_duration_ms,
        'recorded_bounds_duration_ms': round(duration / 1e6, 3),
        'duration_requirement_met_with_500ms_tolerance': duration >= (requested_duration_ms - 500) * 1000000,
        'raw_trace_bytes': trace_bytes, 'configured_max_trace_bytes': max_trace_bytes,
        'trace_near_size_limit': trace_bytes >= max_trace_bytes * .95,
        'source_intersection_duration_ms': round((window_end - window_start) / 1e6, 3) if window_valid else 0,
        'requested_layer_id': selected_layer_id,
        'selected_layer_id': observed_layer_ids[0] if len(observed_layer_ids) == 1 else None,
        'sources_present': sources_present,
        'target_buffer_surfaceview_covered': bool(runs),
        'rows_at_limit': rows_at_limit,
        'post_time_clock_origin_verified_for_guest': False,
        'clock_relation_summary': {
            'snapshot_count': len(clock_relations),
            'monotonic_minus_boottime_min_ns': min(clock_offsets, default=None),
            'monotonic_minus_boottime_max_ns': max(clock_offsets, default=None),
            'trace_timestamps_match_boottime': bool(clock_relations)
                and all(row['trace_ts_ns'] == row['boottime_ns'] for row in clock_relations),
        },
        'transaction_tail_coverage_gap_ns': max(0, (coverage['snapshot_max_ts_ns'] or end)
                                               - (coverage['transaction_max_ts_ns'] or start)),
        'summary_by_layer': summaries,
        'identity_association': {
            'matched_identity_count': len(matches), 'ambiguous_identity_count': ambiguous,
            'observed_identity_without_transaction_in_bounds': no_transaction,
            'negative_entry_to_observation_count': negative,
            'transaction_identity_without_observation_in_bounds': len(set(transactions_by_identity) - set(runs_by_identity)),
            'entry_to_first_observation': distribution([row['entry_to_first_observation_ns'] for row in matches]),
            'post_time_to_first_observation_raw_clock_difference': distribution(
                [value for value in raw_post_differences if value >= 0]),
            'negative_post_time_raw_clock_difference_count': sum(value < 0 for value in raw_post_differences),
            'missing_transaction_post_time_count': sum(row['post_time_ns'] is None for row in transactions),
        },
        'numeric_frame_runs': runs, 'numeric_identity_matches': matches,
        **sections,
        'limitations': [
            'Snapshot timestamps observe layer state and do not directly identify latch, present fences or panel photons',
            'Transaction-entry timestamps are commit records; post_time is exported in its original unverified clock domain',
            'Post-time differences are raw clock differences; AOSP uses an SF transaction-receipt sample, not decoder or producer submission time',
            'Layers and transactions may share one SF commit timestamp; a zero timestamp difference is not zero latch or display latency',
            'Associations require an assumed unchanged layer generation and producer; observed resets and duplicate identities are rejected',
            'Buffer allocation IDs can be reused and are not video-content PTS identifiers',
            'A frame-number jump or an unobserved transaction does not prove a dropped displayed video frame',
            'The first and last frame runs are censored; observations outside trace_bounds and historical transactions are excluded',
            'Layers and transactions can have different time coverage; no positive import-loss stat does not prove complete capture',
            'This schema has no desired_present_time field and no verified mapping from decoder content PTS to buffer identity',
        ],
    }


def analyze(trace, processor, requested_duration_ms=30000,
            max_trace_bytes=SURFACE_TRACE_MAX_BYTES, selected_layer_id=None):
    trace = private_buffer_trace(trace, max_trace_bytes)
    response = subprocess.run([str(processor), 'query', '-f', '-', str(trace)],
                              input=build_sql(selected_layer_id).encode('ascii'), stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=45)
    if response.returncode:
        raise ValueError('bounded_buffer_trace_query_failed')
    return summarize(parse_results(response.stdout), trace.stat().st_size, requested_duration_ms,
                     max_trace_bytes, selected_layer_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trace', type=Path, required=True)
    parser.add_argument('--processor', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--requested-seconds', type=float, default=30)
    parser.add_argument('--max-trace-mib', type=int, choices=(64, 128), default=128,
                        help='Actual file cap used during capture, for the near-cap check')
    parser.add_argument('--layer-id', type=int, help='Required if more than one target buffer layer is present')
    args = parser.parse_args()
    if args.output.exists() or not 0 < args.requested_seconds <= 45:
        parser.error('fresh numeric output and a bounded requested duration are required')
    try:
        report = analyze(args.trace, args.processor, round(args.requested_seconds * 1000),
                         args.max_trace_mib * 1024 * 1024, args.layer_id)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as out:
            json.dump(report, out, indent=2)
            out.write('\n')
    except (ProbeError, ValueError, OSError, UnicodeError, subprocess.TimeoutExpired):
        raise SystemExit('bounded_surface_buffer_analysis_failed') from None
    print(json.dumps({'recorded_bounds_duration_ms': report['recorded_bounds_duration_ms'],
                      'target_buffer_surfaceview_covered': report['target_buffer_surfaceview_covered'],
                      'trace_near_size_limit': report['trace_near_size_limit'],
                      'summary_by_layer': report['summary_by_layer'],
                      'identity_association': report['identity_association']}))


if __name__ == '__main__':
    main()
