#!/usr/bin/env python3
"""Offline comparison of real UDP evidence; never substitutes callbacks for display FPS."""
import argparse
import json
from pathlib import Path

try:
    from scripts.probes.run_phone_transport import distribution
except ModuleNotFoundError:
    from run_phone_transport import distribution


def surface_window(report, first_packet_ns, start_s=5, end_s=30):
    """Phone Surface and packet timestamps share the phone monotonic time domain."""
    values = report.get('presentation_ns')
    if not isinstance(values, list) or type(first_packet_ns) is not int or first_packet_ns <= 0:
        return {'available': False}
    if any(type(value) is not int or value <= 0 for value in values):
        raise ValueError('Invalid independent Surface timestamp')
    if any(right <= left for left, right in zip(values, values[1:])):
        raise ValueError('Independent Surface timestamps must increase')
    start = first_packet_ns + int(start_s * 1_000_000_000)
    end = first_packet_ns + int(end_s * 1_000_000_000)
    if end <= start:
        raise ValueError('Invalid comparison window')
    included = [value for value in values if start <= value < end]
    gaps = [(right-left)/1e6 for left, right in zip(included, included[1:])]
    clipped = [(min(right, end)-max(left, start))/1e6 for left, right in zip(values, values[1:])
               if left < end and right > start]
    if not included and not clipped:
        return {'available': False, 'reason': 'No timestamp coverage of selected window'}
    return {'available': True, 'window_s': [start_s, end_s], 'new_presents': len(included),
            'full_window_fps': round(len(included)/(end_s-start_s), 3),
            'present_gap_ms': distribution(gaps), 'gaps_over_100ms': sum(value > 100 for value in gaps),
            'boundary_clipped_gap_ms': distribution(clipped),
            'boundary_clipped_gaps_over_100ms': sum(value > 100 for value in clipped),
            'timestamps_cover_entire_window': values[0] <= start and values[-1] >= end,
            'boundary_limitation': 'present_gap_ms excludes crossings; boundary_clipped_gap_ms includes the silent portion inside the window. Neither is optical panel or touch latency'}


def summarize(path):
    report = json.loads(path.read_text())
    phone = report.get('phone')
    if not isinstance(phone, dict):
        return {'report': path.name, 'valid_phone_report': False}
    first = phone.get('first_server_packet_ns')
    frames = phone.get('presentation_frames', [])
    future, steady_future, shifts = [], [], []
    previous_offset = None
    for frame in frames:
        pts, scheduled, received = (frame.get(key) for key in ('pts_us', 'scheduled_ns', 'received_ns'))
        if not all(type(value) is int and value > 0 for value in (pts, scheduled, received)):
            continue
        lead = (scheduled-received)/1e6
        future.append(lead)
        if type(first) is int and first+5_000_000_000 <= received < first+30_000_000_000:
            steady_future.append(lead)
        offset = scheduled-pts*1000
        if previous_offset is not None and offset-previous_offset > 10_000_000:
            shifts.append(round((offset-previous_offset)/1e6, 3))
        previous_offset = offset
    surfaces = report.get('actual_surface_samples', {})
    companion = path.with_name(path.stem+'-phone-surface.json')
    phone_surface = json.loads(companion.read_text()) if companion.is_file() else None
    native_summaries = [event for event in report.get('native_events', []) if event.get('event') == 'summary']
    result = {'report': path.name, 'report_path': str(path), 'valid_phone_report': True,
              'async_video': report.get('async_video_enabled', False),
              'decoder_reanchor_enabled': report.get('decoder_reanchor_enabled', True),
              'requested_video_bps': report.get('requested_video_bps'),
              'wire_burst_budget_bps': report.get('wire_burst_budget_bps'),
              'recovery_cooldown_ms': report.get('recovery_cooldown_ms', 500),
              'encoder_burst_bytes': report.get('encoder_burst_bytes'),
              'encoder_burst_seconds': report.get('encoder_burst_seconds'),
              'hardware_encoder_readback': report.get('hardware_encoder_readback'),
              'last_native_summary': native_summaries[-1] if native_summaries else None,
              'phone_display_vsync_ns': phone_surface.get('display_vsync_ns') if phone_surface else None,
              'phone_display_mode_start': phone.get('display_mode_start'),
              'phone_display_mode_end': phone.get('display_mode_end'),
              'phone_display_mode_start_matches_requested': phone.get('display_mode_start_matches_requested'),
              'phone_display_mode_end_matches_requested': phone.get('display_mode_end_matches_requested'),
              'accepted_encoder_bps': report.get('encoder_recovery', {}).get('encoder_accepted_bitrate_bps'),
              'test_loss': report.get('test_loss_injection'),
              'source_surface': surfaces.get('source'), 'phone_surface': surfaces.get('phone'),
              'phone_steady_5_to_30_s': surface_window(phone_surface, first) if phone_surface else {'available': False},
              'input_to_decoder_ready_ms': distribution(phone.get('input_queue_to_decoder_ready_ms', [])),
              'scheduled_target_from_receive_ms': distribution(future),
              'steady_scheduled_target_from_receive_ms': distribution(steady_future),
              'requested_schedule_mapping_jumps_over_10ms': shifts,
              'video_worker': {key: phone.get(key) for key in
                               ('video_input_queue', 'video_worker_alive', 'video_worker_join_timed_out',
                                'video_worker_failure_class', 'video_worker_expired_frames',
                                'video_worker_stale_epoch_drops', 'video_codec_timeout_recovery_events',
                                'video_worker_wait_mean_ms', 'video_worker_wait_max_ms',
                                'receive_loop_max_ms', 'receive_processing_max_ms', 'receive_socket_wait_max_ms',
                                'receive_loop_over_80ms')},
              'audio': {key: phone.get('udp_audio', {}).get(key) for key in
                        ('worker_queue_drops', 'worker_late_drops', 'pcm_late_drops', 'assembly_expired', 'pcm_written_bytes', 'failure_class')},
              'fec': {key: phone.get('native_fec', {}).get(key) for key in
                      ('recovered_shards', 'frames_expired', 'reference_lost', 'needs_keyframe')},
              'codec_input_timeouts': phone.get('decoder_input_timeouts'),
              'host_failures': report.get('host_failures'),
              'phone_failure': phone.get('failure_class'),
              'limitations': ['Different live encoded segments; not identical-byte A/B',
                              'Clock target is a scheduling instruction, not measured physical latency',
                              'scheduled minus source PTS includes renderer target=now fallback; jumps do not independently measure PlaybackClock offset',
                              'LAN/OnePlus results do not validate WAN/V50 or acoustic synchronization']}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', nargs='+', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = {'scope': 'Offline summary of real phone UDP iterations',
              'runs': [summarize(path) for path in args.reports]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'output': str(args.output), 'runs': len(result['runs'])}))


if __name__ == '__main__':
    main()
