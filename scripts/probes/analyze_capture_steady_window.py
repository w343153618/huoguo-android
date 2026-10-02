#!/usr/bin/env python3
"""Offline, metadata-only capture/VT analysis; never accesses a device or service.

The steady window is [5,30) seconds from the first host gRPC return. Source
screenshot PTS joins phone records; host/phone clocks are never subtracted.
"""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path


CLOCK = 'host_clock_gettime_CLOCK_MONOTONIC_ns'
PHASES = {
    'grpc_validation_enqueue_ms': ('grpc_return_ns', 'enqueue_ns'),
    'raw_queue_ms': ('enqueue_ns', 'dequeue_ns'),
    'dequeue_to_pipe_begin_ms': ('dequeue_ns', 'pipe_write_begin_ns'),
    'raw_pipe_write_ms': ('pipe_write_begin_ns', 'pipe_write_end_ns'),
    'native_payload_read_ms': ('raw_read_begin_ns', 'raw_read_complete_ns'),
    'native_read_to_slot_begin_ms': ('raw_read_complete_ns', 'slot_wait_begin_ns'),
    'native_slot_wait_ms': ('slot_wait_begin_ns', 'slot_wait_end_ns'),
    'pixel_conversion_ms': ('pixel_conversion_begin_ns', 'pixel_conversion_end_ns'),
    'vt_submit_call_ms': ('vt_submit_ns', 'vt_submit_return_ns'),
    'vt_submit_to_callback_ms': ('vt_submit_ns', 'vt_callback_ns'),
    'callback_annexb_prepare_ms': ('vt_callback_ns', 'stdout_lock_wait_begin_ns'),
    'stdout_lock_wait_ms': ('stdout_lock_wait_begin_ns', 'stdout_lock_acquired_ns'),
    'stdout_write_ms': ('stdout_write_begin_ns', 'stdout_write_end_ns'),
    'native_stdout_to_python_read_ms': ('stdout_write_end_ns', 'encoded_read_complete_ns'),
    'python_encoded_socket_write_ms': ('encoded_read_complete_ns', 'socket_write_end_ns'),
    'grpc_return_to_vt_callback_ms': ('grpc_return_ns', 'vt_callback_ns'),
    'grpc_return_to_encoded_socket_end_ms': ('grpc_return_ns', 'socket_write_end_ns'),
}


def distribution(values):
    values = sorted(value for value in values if math.isfinite(value))
    if not values:
        return {'count': 0}
    def percentile(q):
        position = (len(values) - 1) * q
        lower = int(position)
        return round(values[lower] + (values[min(lower + 1, len(values) - 1)] - values[lower]) *
                     (position - lower), 4)
    return {'count': len(values), 'p50_ms': percentile(.5), 'p95_ms': percentile(.95),
            'p99_ms': percentile(.99), 'max_ms': round(values[-1], 4),
            'over_16_667_ms': sum(value > 1000 / 60 for value in values),
            'over_50_ms': sum(value > 50 for value in values),
            'over_80_ms': sum(value > 80 for value in values),
            'over_100_ms': sum(value > 100 for value in values)}


def read_json(path):
    return json.loads(path.read_text())


def file_evidence(path):
    return {'file': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def analyze_one(trace_path, window_start=5, window_end=30):
    base = trace_path.name.removesuffix('-capture-trace.json')
    run_path = trace_path.with_name(base + '.json')
    surface_path = trace_path.with_name(base + '-phone-surface.json')
    stalls_path = trace_path.with_name(base + '-stalls.json')
    trace, run, surface, stalls = [read_json(path) for path in
                                  (trace_path, run_path, surface_path, stalls_path)]
    records = trace['records']
    captures = sorted((row for row in records if row['event'] == 'capture_enqueue'),
                      key=lambda row: row['grpc_return_ns'])
    origin = captures[0]['grpc_return_ns']
    lower, upper = origin + int(window_start * 1e9), origin + int(window_end * 1e9)
    selected = [row for row in captures if lower <= row['grpc_return_ns'] < upper]
    clocks = {row['process'] for row in records if row['event'] == 'trace_clock' and row.get('clock_domain') == CLOCK}
    if not {'python', 'swift'} <= clocks:
        raise ValueError('Both explicit same-host clock domains are required')
    events = defaultdict(lambda: defaultdict(list))
    capture_by_key = defaultdict(list)
    for row in records:
        pts = row.get('source_pts_us')
        if type(pts) is not int:
            continue
        if row['event'] == 'capture_enqueue':
            capture_by_key[(row.get('capture_seq'), pts)].append(row)
        else:
            events[pts][row['event']].append(row)
    phase_values, joined, rejected = defaultdict(list), {}, []
    for capture in selected:
        pts, seq = capture['source_pts_us'], capture['capture_seq']
        group = events[pts]
        submissions = [row for row in group['raw_submit'] if row.get('capture_seq') == seq and not row.get('idle_repeat')]
        if len(capture_by_key[(seq, pts)]) != 1 or len(submissions) != 1:
            rejected.append({'capture_seq': seq, 'source_pts_us': pts, 'reason': 'missing_or_ambiguous_submission'})
            continue
        merged = dict(capture)
        for name in ('raw_submit', 'raw_read', 'vt_frame', 'vt_submit_return', 'encoded_egress'):
            items = submissions if name == 'raw_submit' else group[name]
            if len(items) == 1:
                merged.update(items[0])
        result = {'capture_seq': seq, 'source_pts_us': pts,
                  'seconds_from_first_grpc_return': round((capture['grpc_return_ns'] - origin) / 1e9, 6),
                  'phase_ms': {}, 'negative_or_missing_phases': [],
                  'au_bytes': merged.get('au_bytes'), 'keyframe': merged.get('keyframe')}
        for name, (start, stop) in PHASES.items():
            left, right = merged.get(start), merged.get(stop)
            if type(left) is not int or type(right) is not int or right < left:
                result['negative_or_missing_phases'].append(name)
                continue
            value = (right - left) / 1e6
            result['phase_ms'][name] = round(value, 4)
            phase_values[name].append(value)
        joined[(seq, pts)] = result

    phone = run['phone']
    phone_inputs = defaultdict(list)
    for row in phone.get('media_input_observations', []):
        phone_inputs[row['pts_us']].append(row)
    phone_native = defaultdict(list)
    for row in phone.get('native_frame_events', []):
        if row.get('pts_available') and row.get('pts_us'):
            phone_native[row['pts_us']].append(row)
    gaps = []
    for left, right in zip(captures, captures[1:]):
        if not (lower <= left['grpc_return_ns'] and right['grpc_return_ns'] < upper):
            continue
        grpc_gap = (right['grpc_return_ns'] - left['grpc_return_ns']) / 1e6
        pts_gap = (right['source_pts_us'] - left['source_pts_us']) / 1000
        row = {'left_capture_seq': left['capture_seq'], 'right_capture_seq': right['capture_seq'],
               'left_source_pts_us': left['source_pts_us'], 'right_source_pts_us': right['source_pts_us'],
               'right_seconds_from_first_grpc_return': round((right['grpc_return_ns'] - origin) / 1e9, 6),
               'grpc_return_gap_ms': round(grpc_gap, 4), 'screenshot_pts_gap_ms': round(pts_gap, 4),
               'delivery_gap_minus_screenshot_gap_ms': round(grpc_gap - pts_gap, 4)}
        if grpc_gap > 50:
            row['left_frame'] = joined.get((left['capture_seq'], left['source_pts_us']))
            row['right_frame'] = joined.get((right['capture_seq'], right['source_pts_us']))
            a, b = phone_inputs[left['source_pts_us']], phone_inputs[right['source_pts_us']]
            if len(a) == len(b) == 1:
                row['matched_phone_received_gap_ms'] = round((b[0]['received_ns'] - a[0]['received_ns']) / 1e6, 4)
                row['matched_phone_input_gap_ms'] = round((b[0]['input_queued_ns'] - a[0]['input_queued_ns']) / 1e6, 4)
            deliveries = [item for item in phone_native[right['source_pts_us']] if item['event'] == 'frame_delivered']
            if len(deliveries) == 1:
                item = deliveries[0]
                row['right_phone_frame_id'] = item['frame_id']
                row['right_phone_first_to_complete_ms'] = round((item['event_phone_us'] - item['first_arrival_us']) / 1000, 4)
            gaps.append(row)

    # This is an association window on the phone defined by receives for the
    # host-selected PTS range; no clock subtraction or one-way delay assertion.
    received = [item['received_ns'] for capture in selected for item in phone_inputs[capture['source_pts_us']]]
    phone_start, phone_end = min(received), max(received)
    presentations = [value for value in surface.get('presentation_ns', []) if phone_start <= value < phone_end]
    panel_gaps = [(right - left) / 1e6 for left, right in zip(presentations, presentations[1:])]
    associated_stalls = []
    for stall in stalls['stalls']:
        if not (phone_start <= stall['phone_start_ns'] and stall['phone_end_ns'] < phone_end):
            continue
        pts_set = {row['pts_us'] for row in stall.get('codec_ready_in_window', [])}
        near = [value for value in joined.values() if value['source_pts_us'] in pts_set]
        associated_stalls.append({
            'seconds_from_phone_first_packet': stall['seconds_from_first_packet'],
            'phone_gap_ms': stall['gap_ms'], 'associated_observations': stall['associated_observations'],
            'matched_capture_frames': near,
            'host_packetizer_context': stall.get('host_context_joined_by_frame_id_not_clock', []),
            'accepted_complete_frame_supply_gaps': stall.get('accepted_complete_frame_supply_gaps', []),
            'codec_input_supply_gaps': stall.get('codec_input_supply_gaps', []),
        })
    return {
        'sample': base,
        'input_files': [file_evidence(path) for path in (trace_path, run_path, surface_path, stalls_path)],
        'requested_display_hz': run.get('requested_phone_display_hz'),
        'actual_phone_display_hz_start': phone.get('display_mode_start', {}).get('refresh_hz'),
        'actual_phone_display_hz_end': phone.get('display_mode_end', {}).get('refresh_hz'),
        'surface_content_hint_fps': run.get('requested_surface_content_hint_fps'),
        'host_window': {'origin': 'first_grpc_return_ns', 'start_s': window_start, 'end_s': window_end,
                        'duration_s': window_end - window_start, 'captures': len(selected),
                        'captured_fps_over_fixed_window': len(selected) / (window_end - window_start),
                        'joined_submitted_frames': len(joined), 'rejected_joins': rejected},
        'trace_file_status': trace['status'],
        'last_capture_seconds': round((captures[-1]['grpc_return_ns'] - origin) / 1e9, 6),
        'stage_ms': {name: distribution(phase_values[name]) for name in PHASES},
        'pending_raw_queue': {'captures_with_pending_two': sum(row['pending_count'] == 2 for row in selected),
                              'replaced_raw_capture_events': sum(row['replaced_capture_seq'] != 0 for row in selected)},
        'capture_gaps_over_50_ms': sorted(gaps, key=lambda row: row['grpc_return_gap_ms'], reverse=True),
        'slowest_grpc_to_encoded_socket_frames': sorted(joined.values(), key=lambda row:
             row['phase_ms'].get('grpc_return_to_encoded_socket_end_ms', 0), reverse=True)[:12],
        'phone_pts_association_window': {
            'definition': 'phone complete receives matching host-selected screenshot PTS, not a shared host-phone clock',
            'received_observations': len(received), 'phone_receive_span_s': round((phone_end - phone_start) / 1e9, 6),
            'surface_presentations': len(presentations),
            'surface_fps_over_associated_receive_span': round(len(presentations) / ((phone_end - phone_start) / 1e9), 4),
            'surface_gap_ms': distribution(panel_gaps), 'stalls': associated_stalls},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    paths = sorted(args.input_dir.glob('*-capture-trace.json'))
    if not paths:
        parser.error('no sanitized traces')
    report = {
        'scope': 'Offline host [5,30) second capture/VT metadata and same-PTS phone association',
        'date': '2026-10-01', 'samples': [analyze_one(path) for path in paths],
        'limitations': [
            'No new device, VM, service or live streaming operation is performed by this analysis',
            'Native missing final summary is an incomplete prefix, not proof of zero diagnostic drops',
            'Raw pipe write and native read overlap; do not sum them as sequential copy cost',
            'VT submit-to-callback includes scheduling; it is not pure hardware engine time',
            'No guest SurfaceFlinger to host-clock mapping is established here',
            'Phone association uses screenshot PTS; no host-phone timestamp subtraction',
            'Phone SurfaceFlinger is not an optical panel, touch or acoustic timing measurement',
            'The four measured phone modes are 90 Hz despite 120 Hz requests; no fixed-120 claim',
            'Different real video timelines and four samples cannot establish causal improvement',
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as destination:
        json.dump(report, destination, indent=2)
        destination.write('\n')
    print(json.dumps({'output': str(args.output), 'samples': len(report['samples'])}))


if __name__ == '__main__':
    main()
