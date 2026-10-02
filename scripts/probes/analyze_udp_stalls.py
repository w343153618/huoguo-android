#!/usr/bin/env python3
"""Associate actual phone Surface gaps with bounded same-clock UDP events.

No device operations. Associations are evidence to inspect, not automatic causal
claims. Host capture clocks and vendor callback targets are never subtracted
from phone presentation timestamps.
"""
import argparse
import bisect
import json
from pathlib import Path

try:
    from scripts.probes.run_phone_transport import distribution
except ModuleNotFoundError:
    from run_phone_transport import distribution


def positive(value):
    return type(value) is int and value > 0


def timeline(values):
    if not isinstance(values, list) or any(not positive(value) for value in values):
        raise ValueError('Timeline requires positive integer monotonic timestamps')
    if any(b <= a for a, b in zip(values, values[1:])):
        raise ValueError('Timeline must strictly increase')
    return values


def supply_gaps(values, start, end, threshold_ns):
    """Include silent spans crossing a presentation-gap boundary."""
    values = sorted(set(value for value in values if positive(value)))
    index = max(0, bisect.bisect_left(values, start)-1)
    result = []
    for left, right in zip(values[index:], values[index+1:]):
        if left >= end:
            break
        if right > start and right-left >= threshold_ns:
            result.append({'start_ns': left, 'end_ns': right,
                           'gap_ms': round((right-left)/1e6, 3),
                           'overlap_ms': round(max(0, min(right, end)-max(left, start))/1e6, 3)})
    return result[:12]


def analyze(report, surface, threshold_ms=60):
    phone = report.get('phone')
    if not isinstance(phone, dict):
        raise ValueError('Missing real phone report')
    presents = timeline(surface.get('presentation_ns', []))
    begin, finish = phone.get('first_server_packet_ns'), phone.get('receive_end_ns')
    if not positive(begin) or not positive(finish) or finish <= begin:
        raise ValueError('Missing valid phone observation bounds')
    if type(threshold_ms) not in (int, float) or not 20 <= threshold_ms <= 1000:
        raise ValueError('Stall threshold outside 20..1000 ms')
    threshold_ns = int(threshold_ms*1e6)
    presents = [value for value in presents if begin <= value <= finish]
    inputs = [row for row in phone.get('media_input_observations', []) if isinstance(row, dict)]
    received = [row.get('received_ns') for row in inputs]
    queued = [row.get('input_queued_ns') for row in inputs]
    native = [row for row in phone.get('native_frame_events', []) if isinstance(row, dict)]
    epochs = [row for row in phone.get('inbox_epoch_events', []) if isinstance(row, dict)]
    decoded = [row for row in phone.get('presentation_frames', []) if isinstance(row, dict)]
    host_sources = {row['frame']: row for row in report.get('native_events', [])
                    if isinstance(row, dict) and row.get('event') == 'frame_source' and positive(row.get('frame'))}
    host_outputs = {row['frame']: row for row in report.get('native_events', [])
                    if isinstance(row, dict) and row.get('event') == 'frame_output' and positive(row.get('frame'))}
    spans = []
    completed = []
    for row in native:
        first, last, decided = (row.get(key) for key in ('first_arrival_us', 'last_arrival_us', 'event_phone_us'))
        if row.get('event') == 'frame_delivered':
            completed.append(row)
            if positive(first) and positive(last) and first <= last:
                spans.append((last-first)/1000)
    stalls = []
    for left, right in zip(presents, presents[1:]):
        if right-left < threshold_ns:
            continue
        # Events slightly preceding the first repeated display can initiate
        # the following starvation. Retain that explicit search margin.
        window_start = left-80_000_000
        near_native = [row for row in native if positive(row.get('event_phone_us'))
                       and window_start <= row['event_phone_us']*1000 <= right]
        near_epochs = [row for row in epochs if positive(row.get('time_ns'))
                       and window_start <= row['time_ns'] <= right]
        host_context = []
        for frame_id in sorted(set(row.get('frame_id') for row in near_native if positive(row.get('frame_id')))):
            source, previous, output = host_sources.get(frame_id, {}), host_sources.get(frame_id-1, {}), host_outputs.get(frame_id, {})
            current_capture, previous_capture = source.get('host_capture_us'), previous.get('host_capture_us')
            source_gap = ((current_capture-previous_capture)/1000
                          if positive(current_capture) and positive(previous_capture) and current_capture >= previous_capture else None)
            first_write, last_write = output.get('first_write_host_us'), output.get('last_write_host_us')
            pts, previous_pts = source.get('source_pts_us'), previous.get('source_pts_us')
            host_context.append({'frame_id': frame_id, 'packetizer_intake_gap_from_previous_ms': source_gap,
                                 'source_pts_gap_from_previous_ms': (pts-previous_pts)/1000
                                     if positive(pts) and positive(previous_pts) and pts >= previous_pts else None,
                                 'source_au_bytes': source.get('au_bytes'), 'keyframe': source.get('keyframe'),
                                 'packetizer_output_complete': output.get('complete'),
                                 'packetizer_media_data_complete': output.get('media_data_complete'),
                                 'packetizer_failure_stage': output.get('failure_stage'),
                                 'packetizer_expected_shards': output.get('expected_shards'),
                                 'packetizer_emitted_shards': output.get('emitted_shards'),
                                 'packetizer_write_span_ms': (last_write-first_write)/1000
                                     if positive(first_write) and positive(last_write) and last_write >= first_write else None})
        codec_rows = []
        for row in decoded:
            ready, queued_at = row.get('decoder_ready_ns'), row.get('input_queued_ns')
            if positive(ready) and positive(queued_at) and queued_at <= ready and window_start <= ready <= right:
                codec_rows.append({'pts_us': row.get('pts_us'), 'input_queued_ns': queued_at,
                                   'decoder_ready_ns': ready, 'queue_to_ready_ms': round((ready-queued_at)/1e6, 3)})
        receiver_gaps = supply_gaps(received, left, right, threshold_ns)
        input_gaps = supply_gaps(queued, left, right, threshold_ns)
        associations = []
        if receiver_gaps:
            associations.append('complete_frame_supply_gap')
        if input_gaps:
            associations.append('codec_input_supply_gap')
        if any(row.get('event') == 'frame_expired' for row in near_native):
            associations.append('receiver_frame_expiry_near_gap')
        if any(row.get('event') == 'chain_lost' for row in near_epochs):
            associations.append('inbox_chain_loss_near_gap')
        stalls.append({'phone_start_ns': left, 'phone_end_ns': right,
                       'seconds_from_first_packet': round((left-begin)/1e9, 6),
                       'gap_ms': round((right-left)/1e6, 3),
                       'associated_observations': associations,
                       'accepted_complete_frame_supply_gaps': receiver_gaps,
                       'codec_input_supply_gaps': input_gaps,
                       'native_events_with_80ms_preceding_margin': near_native[:48],
                       'inbox_events_with_80ms_preceding_margin': near_epochs[:48],
                       'host_context_joined_by_frame_id_not_clock': host_context[:48],
                       'codec_ready_in_window': codec_rows[:48]})
    result = {
        'scope': 'Offline association of independent phone Surface timestamps and same-phone diagnostic events',
        'source': report.get('source'), 'route': report.get('route'),
        'requested_fps': phone.get('fps_limit'), 'threshold_ms': threshold_ms,
        'surface_presents_in_receive_window': len(presents),
        'surface_gaps_ms': distribution([(b-a)/1e6 for a, b in zip(presents, presents[1:])]),
        'stalls_count': len(stalls), 'stalls': sorted(stalls, key=lambda row: row['gap_ms'], reverse=True),
        'native_frame_event_count': len(native), 'inbox_epoch_event_count': len(epochs),
        'host_source_events_count': len(host_sources),
        'native_events_evicted': phone.get('native_frame_events_evicted'),
        'inbox_events_evicted': phone.get('video_input_queue', {}).get('epoch_events_evicted'),
        'delivered_frame_packet_span_ms': distribution(spans),
        'diagnostic_events_available': bool(native or epochs),
        'limits': [
            'Temporal associations are not proof of a single cause',
            'Accepted input observations omit reconstructed frames rejected before codec admission',
            'SurfaceFlinger timestamps are not optical panel or human touch latency',
            'Host capture_us is packetizer intake after encoded AU read, not guest capture; never compared with phone clocks',
            'Decoder-ready intervals include codec queues and thread scheduling, not pure hardware execution',
            'Evicted/missing events and surface sampling bounds can leave gaps unexplained',
        ],
    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--surface', type=Path)
    parser.add_argument('--threshold-ms', type=float, default=60)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    surface = args.surface or args.report.with_name(args.report.stem+'-phone-surface.json')
    output = analyze(json.loads(args.report.read_text()), json.loads(surface.read_text()), args.threshold_ms)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'output': str(args.output), 'stalls': output['stalls_count'],
                      'diagnostic_events_available': output['diagnostic_events_available']}))


if __name__ == '__main__':
    main()
