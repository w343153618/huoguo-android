#!/usr/bin/env python3
"""Export fixed numeric SF readiness reasons from a private gfx trace.

Formatted names are inspected only in private memory. The output never contains
slice names or layer names, and readiness markers are not matched to frames by
nearest timestamp. Global timeline markers have no verified transaction identity.
"""
import argparse
from collections import Counter
import csv
import io
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.probes.guest_perfetto_probe import (  # noqa: E402
    ProbeError, VIDEO_TRACE_BYTES, distribution, parse_integer, private_trace,
)

MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_MARKER_ROWS = 20000
TARGET_PACKAGE = 'app.morphe.android.youtube'
REASONS = {
    1: 'desired_time_not_current',
    2: 'frame_timeline_early',
    3: 'vsync_not_valid',
    4: 'pending_buffer_backpressure',
    5: 'fence_unsignaled',
    6: 'fence_unsignaled_allow_latch_candidate',
    7: 'barrier_not_ready',
}
SCOPES = {1: 'global_or_unattributed', 2: 'target_surfaceview_name', 3: 'other_named_layer'}
NUMBER = r'(-?(?:0|[1-9][0-9]{0,18}))'
DESIRED = re.compile(r'not current desiredPresentTime: ' + NUMBER + r' expectedPresentTime: ' + NUMBER)
EARLY = re.compile(r'frameIsEarly vsyncId: ' + NUMBER + r' expectedPresentTime: ' + NUMBER)
VSYNC = re.compile(r'!isVsyncValid expectedPresentTime: ' + NUMBER + r' uid: ' + NUMBER)
BARRIER = re.compile(r'NotReadyBarrier (.{1,768}) barrierFrameNumber:' + NUMBER + r' > ' + NUMBER)

SQL = """
CREATE PERFETTO TABLE _huoguo_sf_slices AS
SELECT s.id AS slice_id,s.ts AS ts_ns,s.dur AS dur_ns,t.utid,p.pid,t.tid,s.name
FROM slice s JOIN thread_track tt ON s.track_id=tt.id
JOIN thread t ON tt.utid=t.utid JOIN process p ON t.upid=p.upid
WHERE lower(p.name) LIKE '%surfaceflinger%';
CREATE PERFETTO TABLE _huoguo_readiness_candidates AS
SELECT * FROM _huoguo_sf_slices WHERE
 name LIKE 'not current desiredPresentTime:%'
 OR name LIKE 'frameIsEarly vsyncId:%'
 OR name LIKE '!isVsyncValid expectedPresentTime:%'
 OR name='noVsyncValid'
 OR name LIKE 'hasPendingBuffer %'
 OR name='fence unsignaled'
 OR name LIKE 'fence unsignaled %'
 OR name LIKE 'NotReadyBarrier %';

SELECT start_ts AS trace_start_ns,end_ts AS trace_end_ns FROM trace_bounds;
SELECT
 (SELECT COUNT(*) FROM _huoguo_sf_slices) AS sf_slice_count,
 (SELECT COUNT(*) FROM _huoguo_readiness_candidates) AS candidate_marker_count,
 (SELECT COUNT(*) FROM stats WHERE value>0 AND severity='error') AS import_error_stat_count,
 (SELECT COUNT(*) FROM stats WHERE value>0 AND severity='data_loss') AS data_loss_stat_count,
 (SELECT COALESCE(SUM(value),0) FROM stats
  WHERE value>0 AND severity='data_loss') AS data_loss_stat_value,
 (SELECT COALESCE(SUM(value),0) FROM stats
  WHERE name='graphics_frame_event_parser_errors') AS graphics_parser_info_value;
SELECT b.snapshot_id AS clock_snapshot_id,b.ts AS trace_ts_ns,
 m.clock_value AS monotonic_ns,b.clock_value AS boottime_ns
FROM clock_snapshot b JOIN clock_snapshot m ON b.snapshot_id=m.snapshot_id
WHERE b.clock_id=6 AND m.clock_id=3 ORDER BY b.snapshot_id LIMIT 256;
SELECT slice_id,ts_ns,dur_ns,utid,pid,tid,name AS private_marker_name
FROM _huoguo_readiness_candidates
WHERE ts_ns BETWEEN (SELECT start_ts FROM trace_bounds) AND (SELECT end_ts FROM trace_bounds)
ORDER BY ts_ns,slice_id LIMIT 20000;
"""
HEADERS = {
    ('trace_start_ns', 'trace_end_ns'): 'trace_bounds',
    ('sf_slice_count', 'candidate_marker_count', 'import_error_stat_count',
     'data_loss_stat_count', 'data_loss_stat_value', 'graphics_parser_info_value'): 'coverage',
    ('clock_snapshot_id', 'trace_ts_ns', 'monotonic_ns', 'boottime_ns'): 'clock_relations',
    ('slice_id', 'ts_ns', 'dur_ns', 'utid', 'pid', 'tid', 'private_marker_name'): 'private_candidates',
}


def parse_marker(name):
    """Recognize exact numeric formats and discard all arbitrary name text."""
    if not isinstance(name, str) or len(name) > 1024 or any(ord(c) < 32 for c in name):
        return None
    row = {'reason_id': None, 'scope_id': 1, 'desired_present_ns': None,
           'expected_present_ns': None, 'vsync_id': None, 'origin_uid': None,
           'barrier_current_frame': None, 'barrier_required_frame': None,
           'layer_generation_id': None, 'identity_verified': False,
           'parent_transaction_identity_present': False}
    if match := DESIRED.fullmatch(name):
        row.update(reason_id=1, desired_present_ns=parse_integer(match[1]),
                   expected_present_ns=parse_integer(match[2]))
    elif match := EARLY.fullmatch(name):
        row.update(reason_id=2, vsync_id=parse_integer(match[1]),
                   expected_present_ns=parse_integer(match[2]))
    elif match := VSYNC.fullmatch(name):
        row.update(reason_id=3, expected_present_ns=parse_integer(match[1]),
                   origin_uid=parse_integer(match[2]))
    elif name == 'noVsyncValid':
        row['reason_id'] = 3
    elif match := BARRIER.fullmatch(name):
        row.update(reason_id=7, barrier_current_frame=parse_integer(match[2]),
                   barrier_required_frame=parse_integer(match[3]))
        row['scope_id'] = named_scope(match[1])
    else:
        for prefix, reason in (('fence unsignaled try allowLatchUnsignaled ', 6),
                               ('hasPendingBuffer ', 4), ('fence unsignaled ', 5)):
            if name.startswith(prefix):
                if not name[len(prefix):].strip():
                    return None
                row.update(reason_id=reason, scope_id=named_scope(name[len(prefix):]))
                break
        if name == 'fence unsignaled':
            row['reason_id'] = 5
    return row if row['reason_id'] is not None else None


def named_scope(private_name):
    return 2 if TARGET_PACKAGE in private_name and 'SurfaceView' in private_name else 3


def parse_results(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_OUTPUT_BYTES:
        raise ValueError('bounded_readiness_output_required')
    sections, invalid, candidates_seen = {}, 0, 0
    for block in raw.decode('utf-8', 'strict').strip().split('\n\n'):
        if not block.strip():
            continue
        reader = csv.reader(io.StringIO(block))
        header = tuple(next(reader))
        name = HEADERS.get(header)
        if name is None or name in sections:
            raise ValueError('unexpected_readiness_section')
        rows = []
        for values in reader:
            if len(values) != len(header):
                raise ValueError('invalid_readiness_row')
            if name == 'private_candidates':
                candidates_seen += 1
                if candidates_seen > MAX_MARKER_ROWS:
                    raise ValueError('bounded_marker_rows_required')
                row = {key: parse_integer(value) for key, value in zip(header[:-1], values[:-1])}
                if any(row[key] is None for key in ('slice_id', 'ts_ns', 'utid', 'pid', 'tid')):
                    raise ValueError('missing_readiness_identity')
                try:
                    marker = parse_marker(values[-1])
                except ProbeError:
                    marker = None
                if marker is None:
                    invalid += 1
                    continue
                row.update(marker)
            else:
                row = {key: parse_integer(value) for key, value in zip(header, values)}
                if any(value is None for value in row.values()):
                    raise ValueError('missing_readiness_metadata')
                if len(rows) >= 256:
                    raise ValueError('bounded_readiness_metadata_required')
            rows.append(row)
        sections[name] = rows
    if set(sections) != set(HEADERS.values()):
        raise ValueError('missing_readiness_section')
    if any(len(sections[key]) != 1 for key in ('trace_bounds', 'coverage')):
        raise ValueError('invalid_readiness_coverage')
    # Do not retain even the private-name column title in the result artifact.
    sections['numeric_readiness_records'] = sections.pop('private_candidates')
    sections['invalid_marker_format_count'] = invalid
    sections['candidate_rows_seen'] = candidates_seen
    return sections


def summarize(sections, trace_bytes, requested_duration_ms=30000):
    start, end = (sections['trace_bounds'][0][key] for key in ('trace_start_ns', 'trace_end_ns'))
    if start is None or end is None or end < start:
        raise ValueError('invalid_readiness_bounds')
    records = sections['numeric_readiness_records']
    if any(not start <= row['ts_ns'] <= end for row in records):
        raise ValueError('readiness_record_outside_bounds')
    counts = Counter((row['scope_id'], row['reason_id']) for row in records)
    summaries = []
    for scope in SCOPES:
        for reason in REASONS:
            selected = [row for row in records if row['scope_id'] == scope and row['reason_id'] == reason]
            durations = [row['dur_ns'] for row in selected if row['dur_ns'] is not None and row['dur_ns'] >= 0]
            summaries.append({'scope_id': scope, 'reason_id': reason,
                              'count': counts[(scope, reason)],
                              'marker_scope_duration': distribution(durations),
                              'open_marker_scope_count': sum(row['dur_ns'] == -1 for row in selected)})
    offsets = [row['monotonic_ns'] - row['boottime_ns'] for row in sections['clock_relations']
               if row['monotonic_ns'] is not None and row['boottime_ns'] is not None]
    desired_leads = [row['desired_present_ns'] - row['expected_present_ns'] for row in records
                     if row['reason_id'] == 1 and row['desired_present_ns'] >= row['expected_present_ns']]
    return {
        'schema': 1,
        'scope': 'SF readiness markers; global timeline reasons and named-layer reasons are separate',
        'identity_verified': False, 'parent_transaction_identity_present': False,
        'layer_generation_identity_verified': False,
        'recorded_bounds_duration_ms': round((end - start) / 1e6, 3),
        'requested_duration_ms': requested_duration_ms,
        'duration_requirement_met_with_500ms_tolerance': end - start >= (requested_duration_ms - 500) * 1000000,
        'raw_trace_bytes': trace_bytes, 'configured_max_trace_bytes': VIDEO_TRACE_BYTES,
        'trace_near_size_limit': trace_bytes >= VIDEO_TRACE_BYTES * .95,
        'rows_at_limit': sections['candidate_rows_seen'] >= MAX_MARKER_ROWS,
        'readiness_markers_observed': bool(records),
        'summary': summaries,
        'global_desired_minus_expected': distribution(desired_leads),
        'clock_relation_summary': {'snapshot_count': len(offsets),
                                   'monotonic_minus_boottime_min_ns': min(offsets, default=None),
                                   'monotonic_minus_boottime_max_ns': max(offsets, default=None)},
        'reason_legend': {str(k): v for k, v in REASONS.items()},
        'scope_legend': {str(k): v for k, v in SCOPES.items()},
        **sections,
        'limitations': [
            'Timeline marker formats carry no transaction, layer or frame identity; they remain global reasons',
            'A target SurfaceView name selects a layer scope but does not verify layer generation or buffer identity',
            'No nearest-time mapping to decoder PTS, buffer transactions, frame events or desired/actual/ready triplets is performed',
            'Marker scope duration measures one readiness check, not the full time spent waiting for a frame',
            'Desired-time rejection uses non-auto timestamp logic in reference AOSP; isAutoTimestamp is not directly exported by this schema',
            'The reference pending-buffer branch requires auto timestamp; its count is not direct evidence about explicitly timed video buffers',
            'A fence marker may describe an unsignaled-latch candidate rather than an ultimately blocked transaction',
            'No markers can mean missing trace coverage or no rejected checks; it does not prove readiness was uninterrupted',
            'Graphics parser info counts are distinct from error severity and data_loss stats',
        ],
    }


def analyze(trace, processor, requested_duration_ms=30000):
    trace = private_trace(trace, max_bytes=VIDEO_TRACE_BYTES)
    response = subprocess.run([str(processor), 'query', '-f', '-', str(trace)],
                              input=SQL.encode('ascii'), stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=45)
    if response.returncode:
        raise ValueError('readiness_trace_query_failed')
    return summarize(parse_results(response.stdout), trace.stat().st_size, requested_duration_ms)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trace', type=Path, required=True)
    parser.add_argument('--processor', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--requested-seconds', type=float, default=30)
    args = parser.parse_args()
    if args.output.exists() or not 0 < args.requested_seconds <= 45:
        parser.error('fresh numeric output and a bounded requested duration are required')
    try:
        report = analyze(args.trace, args.processor, round(args.requested_seconds * 1000))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as out:
            json.dump(report, out, indent=2)
            out.write('\n')
    except (ProbeError, ValueError, OSError, UnicodeError, subprocess.TimeoutExpired):
        raise SystemExit('bounded_sf_readiness_analysis_failed') from None
    print(json.dumps({'readiness_markers_observed': report['readiness_markers_observed'],
                      'recorded_bounds_duration_ms': report['recorded_bounds_duration_ms'],
                      'coverage': report['coverage'],
                      'summary': [row for row in report['summary'] if row['count']]}))


if __name__ == '__main__':
    main()
