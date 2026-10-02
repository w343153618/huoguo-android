#!/usr/bin/env python3
"""Repeatable offline real-UDP summary; reads saved metadata, never a device.

Phone windows use first_server_packet_ns + [5,30), or [5,110) for
120-second trials. Host windows have their own first-gRPC-return origin.
The two origins are not synchronized and are never subtracted.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.probes.analyze_capture_steady_window import CLOCK, PHASES, distribution
from scripts.probes.run_surface_hint_matrix import (playback_validation, real_source_validation,
                                                    raw_submit_experiment_verified)

DEFAULT_DIR = ROOT / 'docs/evidence/overnight-20261002'
REPORT_NAME = re.compile(r'hint(?:0|30|60|90|120)-buffer(?:30|60|80|100)-(?:fifo|latest)-submit(?:0|8|16)(?:-rawsubmit(?:30|60|120))?-\d{2}\.json')
LABEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9-]{0,100}')
UDP_SCOPES = {'isolated_real_phone_authenticated_udp_media_and_touch_components',
              'isolated_real_phone_authenticated_udp_video_component'}
UDP_TRANSPORT = 'AES256_GCM_authenticated_UDP_native_10_plus_2_FEC'
INBOX_EVENTS = {'chain_lost', 'stale_fail_ignored', 'idr_admitted', 'recovered'}
INBOX_REASONS = {'queue_frame_overflow', 'queue_byte_overflow', 'oversized_au',
                 'codec_input_failure', 'codec_input_timeout', 'old_epoch',
                 'complete_config_idr', 'initial_idr_committed', 'epoch_idr_committed'}
PHONE_COUNTERS = ('received_media_frames', 'queued_media_frames', 'codec_callback_count',
    'late_discarded_count', 'decoder_input_timeouts', 'video_codec_timeout_recovery_events',
    'video_worker_expired_frames', 'video_worker_stale_epoch_drops',
    'video_input_reservations_cancelled', 'receive_processing_max_ms', 'receive_loop_max_ms',
    'receive_socket_wait_max_ms', 'receive_loop_over_80ms', 'video_worker_wait_max_ms',
    'video_worker_wait_mean_ms', 'surface_unmatched_callbacks', 'presentation_records_evicted',
    'presentation_pending_evicted', 'native_frame_events_evicted',
    'surface_submit_applications', 'surface_submit_wait_count', 'surface_submit_wait_total_ms',
    'surface_submit_max_output_hold_ms', 'surface_submit_max_park_ms', 'surface_submit_budget_fallbacks')
INBOX_COUNTERS = ('capacity_frames', 'capacity_bytes', 'maximum_depth', 'maximum_bytes',
    'admitted_frames', 'overflow_events', 'overflow_rejected_frames', 'queue_cleared_frames',
    'waiting_idr_admission_drops', 'oversize_admission_drops', 'closing_drops', 'recovery_epochs',
    'recovery_idrs_completed', 'pending_frames', 'pending_bytes', 'epoch_events_evicted')
FEC_COUNTERS = ('packets', 'wire_bytes', 'invalid', 'duplicate', 'settled_packets',
    'expired_packets', 'frames_expired', 'frames_delivered', 'recovered_shards',
    'reference_lost', 'keyframe_requests', 'dependency_dropped', 'memory_rejected',
    'clock_mapping_rejected', 'clock_mappings_active', 'clock_mapping_evictions',
    'clock_mapping_expired', 'logical_body_rejected', 'completed_bodies',
    'needs_keyframe', 'max_assembly_latency_us')
AUDIO_COUNTERS = ('decoder_configurations', 'accepted_fragments', 'malformed', 'duplicates',
    'assembly_expired', 'assembly_evicted', 'complete_records', 'reorder_drops',
    'pending_records', 'pending_complete_records', 'worker_queue_drops', 'worker_late_drops',
    'media_before_configuration', 'unsupported_configurations', 'decoder_input_records',
    'pcm_written_bytes', 'pcm_late_drops', 'timestamp_observations', 'valid_audio_timestamps',
    'last_playback_head_frames')
HARDWARE_FIELDS = ('using_hardware', 'hardware_property_readback', 'hardware_property_read_status',
    'required_hardware', 'width', 'height', 'expected_frame_rate_read_status',
    'expected_frame_rate_readback', 'bitrate_target_requested_bps', 'bitrate_target_set_status',
    'bitrate_target_current_bps', 'bitrate_target_read_status', 'bitrate_target_readback',
    'prioritize_speed_requested', 'prioritize_speed_supported_properties_status',
    'prioritize_speed_supported', 'prioritize_speed_set_status', 'prioritize_speed_read_status',
    'prioritize_speed_readback', 'pixel_pool_buffer_verified', 'pixel_pool_attributes_verified',
    'pixel_pool_width_readback', 'pixel_pool_height_readback', 'burst_bytes_requested',
    'burst_seconds_requested', 'vbr_rate_limits_set_attempted', 'vbr_rate_limits_read_status',
    'vbr_rate_limit_status')
PACKETIZER_COUNTERS = ('elapsed_us', 'wire_bitrate', 'source_frames', 'config_messages',
    'source_idr', 'output_frames', 'output_packets', 'plaintext_bytes',
    'estimated_ipv4_encrypted_wire_bytes', 'frame_budget_drops', 'output_deadline_drops',
    'unsupported_frames', 'dependent_source_drops', 'recovery_requests', 'recovery_exhausted',
    'recovery_completions', 'stdout_blocked_us', 'stdout_max_blocked_us', 'max_au_bytes')


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def positive_ns(value):
    return type(value) is int and 0 < value < 2**63 - 1


def zero_counter(source, key):
    return type(source.get(key)) is int and source[key] == 0


def count_matches(source, key, count):
    return type(source.get(key)) is int and source[key] == count


def fields(source, names):
    source = source if isinstance(source, dict) else {}
    return {key: source[key] for key in names if key in source and
            (source[key] is None or type(source[key]) is bool or number(source[key]))}


def records(source, key):
    value = source.get(key, []) if isinstance(source, dict) else []
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def read_saved(path, directory):
    """Do not follow evidence pointers or symlinks into private runtime files."""
    if path.resolve().parent != directory.resolve():
        return None, 'unsafe_path'
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
        if not isinstance(value, dict):
            return None, 'invalid_object'
        return value, 'available'
    except FileNotFoundError:
        return None, 'missing'
    except (OSError, ValueError):
        return None, 'unreadable_or_incomplete'


def gap_window(stamps, lower, upper, origin):
    selected = [value for value in stamps if lower <= value < upper]
    gaps = [(b-a)/1e6 for a, b in zip(selected, selected[1:])]
    clipped = []
    for left, right in zip(stamps, stamps[1:]):
        if left < upper and right >= lower and (left < lower or right >= upper):
            clipped.append({'left_s': round((left-origin)/1e9, 6),
                            'right_s': round((right-origin)/1e9, 6),
                            'gap_ms': round((right-left)/1e6, 6),
                            'inside_window_ms': round(max(0, min(right, upper)-max(left, lower))/1e6, 6),
                            'crosses_start': left < lower, 'crosses_end': right >= upper})
    return selected, distribution(gaps), clipped


def surface_summary(surface, origin=None, lower=None, upper=None):
    if not isinstance(surface, dict):
        return {'status': 'missing', 'coverage_complete': False}
    raw = surface.get('presentation_ns', [])
    raw = raw if isinstance(raw, list) else []
    valid_stamps = [x for x in raw if positive_ns(x)]
    stamps = sorted(set(valid_stamps))
    seconds = surface.get('seconds')
    period = surface.get('display_vsync_ns')
    polls = records(surface, 'polls')
    times = [row['elapsed_s'] for row in polls if number(row.get('elapsed_s'))]
    poll_deltas = [b-a for a, b in zip(times, times[1:])]
    monotonic_polls = all(delta >= 0 for delta in poll_deltas) and len(times) == len(polls)
    # 127 timestamps at at most one present per reported display period. This
    # is a conservative polling-risk check, not a guarantee of no ring loss.
    ring_risk = not positive_ns(period) or not monotonic_polls or len(times) < 2
    if not ring_risk:
        ring_risk = max(poll_deltas, default=0)*1e9 >= 127*period
    full_gaps = [(b-a)/1e6 for a, b in zip(stamps, stamps[1:])]
    result = {'status': 'available', 'seconds': seconds if number(seconds) else None,
        'display_vsync_ns': period if positive_ns(period) else None,
        'unique_presentations': len(stamps), 'input_timestamps': len(raw),
        'invalid_timestamps': len(raw)-len(valid_stamps),
        'duplicate_timestamps': len(valid_stamps)-len(stamps),
        'input_sorted': valid_stamps == sorted(valid_stamps),
        'reported_count_matches_unique': count_matches(surface, 'presented_frames', len(stamps)),
        'fps_over_sampler_seconds': round(len(stamps)/seconds, 6) if number(seconds) and seconds > 0 else None,
        'cadence_fps_over_present_span': round((len(stamps)-1)*1e9/(stamps[-1]-stamps[0]), 6) if len(stamps)>1 else None,
        'gaps_ms': distribution(full_gaps), 'poll_count': len(polls),
        'max_poll_gap_s': round(max(poll_deltas, default=0), 6),
        'poll_ring_capacity_risk': ring_risk, 'coverage_complete': False}
    if origin is not None and stamps:
        result['first_s_from_packet'] = round((stamps[0]-origin)/1e9, 6)
        result['last_s_from_packet'] = round((stamps[-1]-origin)/1e9, 6)
    if lower is not None and upper is not None:
        selected, gaps, clipped = gap_window(stamps, lower, upper, origin)
        result.update(window_presentations=len(selected), window_gaps_ms=gaps,
                      boundary_clipped_gaps=clipped,
                      left_covered=bool(stamps and stamps[0] <= lower),
                      right_covered=bool(stamps and stamps[-1] >= upper))
        result['coverage_complete'] = (result['left_covered'] and result['right_covered']
            and not ring_risk and result['invalid_timestamps'] == 0
            and result['reported_count_matches_unique']
            and surface.get('measurement_available') is not False
            and surface.get('timed_out') is not True)
        result['window_fps_observed'] = round(len(selected)/((upper-lower)/1e9), 6)
        result['window_fps'] = result['window_fps_observed'] if result['coverage_complete'] else None
    return result


def event_rate(rows, field, lower, upper, complete):
    times = sorted(row[field] for row in rows if positive_ns(row.get(field)))
    complete = complete and len(times) == len(rows)
    selected, gaps, clipped = gap_window(times, lower, upper, lower-5_000_000_000)
    return {'observed_count': len(selected), 'complete_history': complete,
        'fps': round(len(selected)/((upper-lower)/1e9), 6) if complete else None,
        'observed_count_per_second': round(len(selected)/((upper-lower)/1e9), 6),
        'gaps_ms': gaps, 'boundary_clipped_gaps': clipped}


def latency(rows, left, right, selector, lower, upper):
    values, invalid = [], 0
    for row in rows:
        if not positive_ns(row.get(selector)) or not lower <= row[selector] < upper:
            continue
        a, b = row.get(left), row.get(right)
        if positive_ns(a) and positive_ns(b) and b >= a:
            values.append((b-a)/1e6)
        else:
            invalid += 1
    return dict(distribution(values), invalid_or_missing=invalid)


def phone_window(phone, lower, upper):
    native = records(phone, 'native_frame_events')
    delivered = [dict(row, time_ns=row['event_phone_us']*1000) for row in native
                 if row.get('event') == 'frame_delivered' and positive_ns(row.get('event_phone_us'))]
    stats = phone.get('native_frame_event_stats', {})
    stats = stats if isinstance(stats, dict) else {}
    fec = phone.get('native_fec', {})
    fec = fec if isinstance(fec, dict) else {}
    native_complete = (phone.get('diagnostic_events_enabled') is True
        and zero_counter(phone, 'native_frame_events_evicted')
        and zero_counter(stats, 'native_events_evicted') and zero_counter(stats, 'pending')
        and count_matches(stats, 'generated', len(native))
        and count_matches(fec, 'frames_delivered', len(delivered)))
    inputs = records(phone, 'media_input_observations')
    inputs_complete = (count_matches(phone, 'queued_media_frames', len(inputs))
                       and all(positive_ns(row.get('input_queued_ns')) for row in inputs))
    ready = records(phone, 'presentation_frames')
    ready_complete = (zero_counter(phone, 'presentation_records_evicted')
        and zero_counter(phone, 'presentation_pending_evicted')
        and zero_counter(phone, 'surface_unmatched_callbacks')
        and count_matches(phone, 'codec_callback_count', len(ready)))
    inbox = phone.get('video_input_queue', {})
    inbox = inbox if isinstance(inbox, dict) else {}
    events = records(phone, 'inbox_epoch_events')
    selected_events = [row for row in events if positive_ns(row.get('time_ns'))
                       and lower <= row['time_ns'] < upper]
    known_events = [row for row in selected_events if type(row.get('event')) is str
                    and row['event'] in INBOX_EVENTS and type(row.get('reason')) is str
                    and row['reason'] in INBOX_REASONS]
    counts = Counter((row['event'], row['reason']) for row in known_events)
    inbox_counts = [{'event': event, 'reason': reason, 'count': count}
                    for (event, reason), count in sorted(counts.items(), key=lambda v: str(v[0]))
                    if event in INBOX_EVENTS and reason in INBOX_REASONS]
    expired = [row for row in native if row.get('event') == 'frame_expired'
               and positive_ns(row.get('event_phone_us')) and lower <= row['event_phone_us']*1000 < upper]
    lead = {}
    for name, left, right in (
        ('receive_to_input_ms', 'received_ns', 'input_queued_ns'),
        ('input_to_ready_ms', 'input_queued_ns', 'decoder_ready_ns'),
        ('ready_to_callback_receipt_ms', 'decoder_ready_ns', 'callback_ns'),
        ('schedule_minus_receive_ms', 'received_ns', 'scheduled_ns'),
        ('schedule_minus_ready_ms', 'decoder_ready_ns', 'scheduled_ns'),
        ('release_to_callback_receipt_ms', 'released_ns', 'callback_ns')):
        lead[name] = latency(inputs if name == 'receive_to_input_ms' else ready,
                             left, right, 'received_ns', lower, upper)
    # Schedule-minus-ready can legitimately be negative; preserve signed values
    # separately rather than hide late outputs behind the positive distributions.
    for name, left in (('schedule_minus_receive_signed_ms', 'received_ns'),
                       ('schedule_minus_ready_signed_ms', 'decoder_ready_ns'),
                       ('target_minus_release_signed_ms', 'released_ns')):
        lead[name] = distribution([(row['scheduled_ns']-row[left])/1e6 for row in ready
            if positive_ns(row.get('received_ns')) and lower <= row['received_ns'] < upper
            and positive_ns(row.get('scheduled_ns')) and positive_ns(row.get(left))])
    interval_samples = []
    previous = phone.get('start_ns')
    for sample in records(phone, 'samples'):
        current = sample.get('t_ns')
        if positive_ns(previous) and positive_ns(current) and previous < current:
            if lower <= previous and current <= upper:
                interval_samples.append(dict(fields(sample, ('media_receive_fps',
                    'codec_callback_fps', 'udp_payload_mbps', 'late_discarded_fps')),
                    start_s=round((previous-(lower-5_000_000_000))/1e9, 6),
                    end_s=round((current-(lower-5_000_000_000))/1e9, 6)))
        previous = current
    au_bytes = [row['access_unit_bytes'] for row in inputs
                if positive_ns(row.get('received_ns')) and lower <= row['received_ns'] < upper
                and number(row.get('access_unit_bytes')) and row['access_unit_bytes'] >= 0]
    return {'complete_au_receive': event_rate(delivered, 'time_ns', lower, upper, native_complete),
        'successful_input': event_rate(inputs, 'input_queued_ns', lower, upper, inputs_complete),
        'retained_decoder_ready': event_rate(ready, 'decoder_ready_ns', lower, upper, ready_complete),
        'codec_callback_receipt': event_rate(ready, 'callback_ns', lower, upper, ready_complete),
        'retained_ready_history_complete': ready_complete,
        'retained_ready_excludes_discarded_outputs': True,
        'latency_by_receive_window': lead,
        'inbox_event_history_complete': phone.get('diagnostic_events_enabled') is True and zero_counter(inbox, 'epoch_events_evicted')
                                       and len(known_events) == len(selected_events),
        'unknown_inbox_event_records': len(selected_events)-len(known_events),
        'inbox_events': inbox_counts, 'observed_fec_expiries': len(expired),
        'fec_event_history_complete': native_complete,
        'native_history': dict(fields(stats, ('enabled', 'pending', 'native_events_evicted', 'generated')),
                               retained_events=len(native), retained_deliveries=len(delivered)),
        'phone_complete_interval_samples': interval_samples,
        'phone_sample_scope': 'Complete sample brackets within phone window; UDP payload includes non-video protocol bodies, not encoded video bitrate',
        'accepted_input_au_bytes': {'count': len(au_bytes), 'sum': sum(au_bytes),
            'mean': round(sum(au_bytes)/len(au_bytes), 3) if au_bytes else None,
            'bytes_per_fixed_window_second': round(sum(au_bytes)/((upper-lower)/1e9), 3)},
        'late_discards_exact_window_unavailable': True, 'audio_window_counters_unavailable': True}


def host_window(trace, start=5, end=30):
    if not isinstance(trace, dict):
        return {'status': 'missing'}
    rows = records(trace, 'records')
    captures = sorted((row for row in rows if row.get('event') == 'capture_enqueue'
                       and positive_ns(row.get('grpc_return_ns'))), key=lambda r: r['grpc_return_ns'])
    clocks = {row.get('process') for row in rows if row.get('event') == 'trace_clock'
              and row.get('clock_domain') == CLOCK}
    common_clock = {'python', 'swift'} <= clocks
    statuses = []
    for row in records(trace, 'status'):
        if row.get('process') not in ('python', 'swift'):
            continue
        clean = dict(process=row['process'], **fields(row, ('available', 'lines_read', 'malformed',
            'discarded', 'summary_present', 'clean_close', 'byte_bound_exceeded', 'record_bound_exceeded')))
        clean['summary'] = fields(row.get('summary'), ('accepted_records', 'written_records',
            'dropped_records', 'record_limit', 'byte_limit', 'clean_close', 'byte_capped'))
        statuses.append(clean)
    result = {'status': 'available', 'window': {'origin': 'first_grpc_return_ns', 'start_s': start,
        'end_s': end, 'duration_s': end-start}, 'common_host_clock_verified': common_clock,
        'trace_status': statuses, 'native_final_trace_summary_present': any(
            row['process'] == 'swift' and row.get('summary_present') is True for row in statuses)}
    python_status = [row for row in statuses if row['process'] == 'python']
    python_history = len(python_status) == 1
    if python_history:
        value = python_status[0]
        python_history = (value.get('available') is True and value.get('summary_present') is True
            and value.get('clean_close') is True and zero_counter(value, 'malformed')
            and zero_counter(value, 'discarded') and value.get('byte_bound_exceeded') is not True
            and value.get('record_bound_exceeded') is not True
            and zero_counter(value['summary'], 'dropped_records')
            and value['summary'].get('byte_capped') is False and 'python' in clocks)
    if not captures:
        return dict(result, coverage_complete=False)
    origin = captures[0]['grpc_return_ns']
    lower, upper = origin+int(start*1e9), origin+int(end*1e9)
    selected = [row for row in captures if lower <= row['grpc_return_ns'] < upper]
    cover = captures[-1]['grpc_return_ns'] >= upper
    _, gaps, clipped = gap_window([row['grpc_return_ns'] for row in captures], lower, upper, origin)
    by_key, by_pts = defaultdict(list), defaultdict(lambda: defaultdict(list))
    capture_pts = defaultdict(list)
    for row in rows:
        if type(row.get('source_pts_us')) is not int:
            continue
        if row.get('event') == 'capture_enqueue':
            by_key[(row.get('capture_seq'), row['source_pts_us'])].append(row)
            capture_pts[row['source_pts_us']].append(row)
        else:
            by_pts[row['source_pts_us']][row.get('event')].append(row)
    values, missing, ambiguous, submitted = defaultdict(list), Counter(), Counter(), 0
    for capture in selected:
        seq, pts = capture.get('capture_seq'), capture.get('source_pts_us')
        group = by_pts[pts]
        submits = [r for r in group['raw_submit'] if r.get('capture_seq') == seq and r.get('idle_repeat') is False]
        if (not positive_ns(seq) or not positive_ns(pts) or len(by_key[(seq, pts)]) != 1
            or len(capture_pts[pts]) != 1 or len(submits) != 1
            or len([r for r in group['raw_submit'] if r.get('idle_repeat') is False]) != 1):
            ambiguous['capture_or_submission'] += 1
            continue
        submitted += 1
        merged = dict(capture)
        native_sequences = {r['native_input_seq'] for name in ('raw_read', 'vt_frame', 'vt_submit_return')
                            for r in group[name] if type(r.get('native_input_seq')) is int}
        for event in ('raw_submit', 'raw_read', 'vt_frame', 'vt_submit_return', 'encoded_egress'):
            items = submits if event == 'raw_submit' else group[event]
            conflicts = any('capture_seq' in r and r['capture_seq'] != seq for r in items)
            native_conflicts = event in ('raw_read', 'vt_frame', 'vt_submit_return') and len(native_sequences) > 1
            if len(items) == 1 and not conflicts and not native_conflicts:
                merged.update(items[0])
            elif len(items) > 1 or conflicts or native_conflicts:
                ambiguous[event] += 1
        for phase, (a, b) in PHASES.items():
            left, right = merged.get(a), merged.get(b)
            if not common_clock or not positive_ns(left) or not positive_ns(right) or right < left:
                missing[phase] += 1
            else:
                values[phase].append((right-left)/1e6)
    capture_identity_complete = all(positive_ns(row.get('capture_seq')) and positive_ns(row.get('source_pts_us')) for row in captures)
    capture_identity_complete = capture_identity_complete and all(len(items) == 1 for items in by_key.values())
    capture_rate_complete = cover and python_history and capture_identity_complete
    result.update(coverage_complete=cover, complete_capture_history=python_history,
        capture_identity_complete=capture_identity_complete, observed_captures=len(selected),
        captures_fps=round(len(selected)/(end-start), 6) if capture_rate_complete else None,
        observed_captures_per_second=round(len(selected)/(end-start), 6),
        last_capture_s=round((captures[-1]['grpc_return_ns']-origin)/1e9, 6),
        grpc_gap_ms=gaps, boundary_clipped_gaps=clipped, unique_submitted_joins=submitted,
        ambiguous_events=dict(ambiguous), missing_or_invalid_phase_counts=dict(missing),
        stage_ms={name: distribution(values[name]) for name in PHASES},
        pending_two=sum(row.get('pending_count') == 2 for row in selected),
        replaced_capture_events=sum(type(row.get('replaced_capture_seq')) is int
                                    and row['replaced_capture_seq'] > 0 for row in selected))
    egress = [row for row in rows if row.get('event') == 'encoded_egress'
              and positive_ns(row.get('socket_write_end_ns'))
              and lower <= row['socket_write_end_ns'] < upper
              and number(row.get('au_bytes')) and row['au_bytes'] >= 0]
    result['encoded_egress_window'] = {'au_count': len(egress),
        'au_bytes': sum(row['au_bytes'] for row in egress),
        'observed_encoded_bps': round(sum(row['au_bytes'] for row in egress)*8/(end-start), 3),
        'complete_python_trace_history_verified': python_history,
        'native_final_trace_summary_present': result['native_final_trace_summary_present']}
    return result


def source_state(state):
    return fields(state, ('command_ok', 'unknown', 'state_known', 'active_sessions', 'state'))


def report_row(directory, matrix, row):
    label = directory.name
    path_string = row.get('report')
    filename = Path(path_string).name if isinstance(path_string, str) else ''
    output = {'run_label': label, 'report_path': None, 'valid': False,
              'status': 'missing_or_invalid_report', 'display_fps': None, 'receive_fps': None}
    if not REPORT_NAME.fullmatch(filename):
        return dict(output, status='unsafe_or_non_udp_report_name')
    path = directory / filename
    output['report_path'] = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else label+'/'+filename
    report, status = read_saved(path, directory)
    if status != 'available':
        return dict(output, status=status)
    phone = report.get('phone')
    if not isinstance(phone, dict) or phone.get('test_scope') not in UDP_SCOPES or phone.get('transport') != UDP_TRANSPORT:
        return dict(output, status='not_real_udp_report')
    conditions = matrix.get('conditions')
    conditions = conditions if isinstance(conditions, dict) else {}
    requested = conditions.get('requested_seconds')
    if type(requested) is not int or requested < 31:
        return dict(output, status='invalid_requested_duration')
    start, end = 5, 110 if requested >= 120 else 30
    first = phone.get('first_server_packet_ns')
    duration = playback_validation(report, requested)
    source = real_source_validation(row.get('source_state_before'), row.get('source_state_after'), report, requested)
    child_ok = type(row.get('exit_code')) is int and row['exit_code'] == 0
    experimental = report.get('experimental_client') is True
    client_ok = (not experimental or (report.get('client_package') == 'local.remoteandroid.direct.experiment'
        and report.get('probe_package') == 'local.remoteandroid.phoneprobe.experiment'
        and report.get('instrumentation_target_verified') is True
        and report.get('surface_submit_report_verified') is True))
    prior_flags_ok = (row.get('valid_playback_test') is not False
        and row.get('valid_real_video_test') is not False
        and row.get('matched_experimental_client_verified') is not False
        and report.get('valid_real_video_test') is not False)
    raw_requested = report.get('requested_raw_submit_fps')
    matrix_raw = conditions.get('requested_raw_submit_fps')
    row_raw = row.get('requested_raw_submit_fps')
    expected_raw = row_raw if row_raw is not None else matrix_raw
    raw_valid = True
    if raw_requested is not None or expected_raw is not None:
        pair = {'client_package': 'local.remoteandroid.direct.experiment',
                'probe_package': 'local.remoteandroid.phoneprobe.experiment'}
        raw_valid = (type(raw_requested) is int
            and (expected_raw is None or (type(expected_raw) is int and raw_requested == expected_raw))
            and row.get('raw_submit_budget_verified') is True
            and raw_submit_experiment_verified(report, pair,
                conditions.get('video_fps_cap'), raw_requested) is True)
    real_valid = child_ok and duration['passed'] and source['passed'] and client_ok and prior_flags_ok and raw_valid
    output.update(status='analyzed', real_video_valid=real_valid,
        window={'origin': 'phone_first_server_packet_ns', 'start_s': start, 'end_s': end,
                'duration_s': end-start},
        validation={'child_exit_ok': child_ok, 'matched_client': client_ok, 'prior_validation_not_failed': prior_flags_ok,
            'raw_submit_budget_verified': raw_valid,
            'duration': duration, 'source': source,
            'source_state_before': source_state(row.get('source_state_before')),
            'source_state_after': source_state(row.get('source_state_after')),
            'source_fingerprints_unchanged': matrix.get('source_unchanged') is True},
        buffer_ms=phone.get('buffer_ms') if type(phone.get('buffer_ms')) is int else None,
        target_bitrate_bps=report.get('requested_video_bps') if number(report.get('requested_video_bps')) else None,
        requested_encoder_fps=fields(matrix.get('conditions'), ('video_fps_cap',)).get('video_fps_cap'),
        requested_raw_submit_fps=fields(report, ('requested_raw_submit_fps',)).get('requested_raw_submit_fps'),
        actual_phone_fps_limit=fields(phone, ('fps_limit',)).get('fps_limit'),
        wire_pacing_ceiling_bps=fields(report, ('wire_burst_budget_bps',)).get('wire_burst_budget_bps'),
        actual_display_hz_start=fields(phone.get('display_mode_start'), ('refresh_hz',)).get('refresh_hz'),
        actual_display_hz_end=fields(phone.get('display_mode_end'), ('refresh_hz',)).get('refresh_hz'),
        phone_hardware_decoder=phone.get('hardware') is True,
        decoder_family=('qti_avc_low_latency' if phone.get('decoder') == 'c2.qti.avc.decoder.low_latency' else 'other_or_unverified'),
        phone_whole_run=fields(phone, PHONE_COUNTERS))
    output['surface_submit_status'] = (phone['surface_submit_status'] if phone.get('surface_submit_status') in (
        'disabled_existing_release_path', 'applied_bounded_wait', 'enabled_no_wait_observed') else 'unverified')
    output['requested_surface_submit_lead_ms'] = fields(report, ('requested_surface_submit_lead_ms',)).get('requested_surface_submit_lead_ms')
    output['test_loss_injection'] = fields(report.get('test_loss_injection'), ('enabled',
        'video_drop_every', 'drop_video_every', 'video_datagrams_observed', 'video_datagrams_dropped'))
    geometry = phone.get('source_geometry')
    match = re.fullmatch(r'(\d{2,4})x(\d{2,4})', geometry) if isinstance(geometry, str) else None
    output['geometry'] = {'width': int(match[1]), 'height': int(match[2])} if match else None
    hardware = []
    for entry in records(report, 'hardware_encoder_readback'):
        clean = fields(entry, HARDWARE_FIELDS)
        if entry.get('event') in ('ready', 'bitrate', 'summary'):
            clean['event'] = entry['event']
        if entry.get('pixel_pool_mode') in ('manual', 'session'):
            clean['pixel_pool_mode'] = entry['pixel_pool_mode']
        if entry.get('prioritize_speed_status') in ('not_requested', 'confirmed', 'unsupported', 'set_failed', 'read_failed', 'readback_mismatch',
                                                  'api_unavailable', 'supported_properties_query_failed', 'readback_not_boolean'):
            clean['prioritize_speed_status'] = entry['prioritize_speed_status']
        for key in ('vbr_rate_limits_requested', 'vbr_rate_limits_readback'):
            value = entry.get(key)
            if isinstance(value, list) and len(value) <= 8 and all(number(x) for x in value):
                clean[key] = value
        clean['encoder_family'] = ('apple_ave_avc' if entry.get('encoder_id') == 'com.apple.videotoolbox.videoencoder.ave.avc' else 'other_or_unverified')
        hardware.append(clean)
    output['encoder_hardware_readback'] = hardware
    encoder_finals = [entry for entry in records(report, 'hardware_encoder_readback')
                      if entry.get('event') == 'summary']
    output['native_encoder_final_statistics_present'] = len(encoder_finals) == 1
    output['native_encoder_final_statistics'] = fields(encoder_finals[0], ('input_frames', 'output_frames',
        'dropped_or_failed', 'pending_at_end', 'output_bitrate_bps', 'output_fps')) if len(encoder_finals) == 1 else None
    binaries = matrix.get('binaries')
    binaries = binaries if isinstance(binaries, dict) else {}
    output['binary_sha256'] = {key: value for key, value in binaries.items()
        if key in ('encoder', 'packetizer', 'probe') and isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value)}
    audio = phone.get('udp_audio', {})
    output['audio'] = dict(whole_run=fields(audio, AUDIO_COUNTERS),
        tested=phone.get('audio_tested') is True,
        failure_present=isinstance(audio, dict) and audio.get('failure_class') not in (None, ''),
        steady_window_counters_available=False, optical_or_acoustic_av_sync_measured=False)
    output['inbox'] = dict(whole_run=fields(phone.get('video_input_queue'), INBOX_COUNTERS))
    output['native'] = dict(whole_run_fec=fields(phone.get('native_fec'), FEC_COUNTERS),
        host_events_evicted=fields(report.get('host'), ('native_events_evicted',)).get('native_events_evicted'))
    finals = [x for x in records(report, 'native_events') if x.get('event') == 'summary' and x.get('final') is True]
    output['native']['packetizer_final_summary_present'] = len(finals) == 1
    output['native']['packetizer_final_summary'] = fields(finals[0], PACKETIZER_COUNTERS) if len(finals) == 1 else None
    surfaces = report.get('actual_surface_samples', {})
    surfaces = surfaces if isinstance(surfaces, dict) else {}
    # Source SF has no established guest-to-phone clock mapping. Preserve only
    # its full independent observation, not a fabricated phone-window crop.
    output['source_full_surface'] = fields(surfaces.get('source'), ('seconds', 'presented_frames', 'fps', 'cadence_fps', 'display_vsync_ns', 'measurement_available', 'timed_out'))
    source_full = surfaces.get('source', {})
    if isinstance(source_full, dict):
        output['source_full_surface']['gaps_ms'] = fields(source_full.get('gaps_ms'), ('p50', 'p95', 'p99', 'max', 'over_50', 'over_100'))
    surface, surface_status = read_saved(path.with_name(path.stem+'-phone-surface.json'), directory)
    output['phone_surface_file_status'] = surface_status
    package_matches = isinstance(surface, dict) and surface.get('package') == report.get('client_package') and surface.get('package') in (
        'local.remoteandroid.direct', 'local.remoteandroid.direct.experiment')
    serial_expected = matrix.get('phone', {}).get('requested_serial') if isinstance(matrix.get('phone'), dict) else None
    serial_matches = isinstance(surface, dict) and isinstance(serial_expected, str) and surface.get('serial') == serial_expected
    output['validation']['phone_surface_package_matches'] = package_matches
    output['validation']['phone_surface_device_matches'] = serial_matches
    if positive_ns(first):
        lower, upper = first+int(start*1e9), first+int(end*1e9)
        output['full_surface'] = surface_summary(surface, first, lower, upper)
        steady = phone_window(phone, lower, upper)
        output['stable_window'] = steady
        coverage = output['full_surface']['coverage_complete']
        receive_covers = positive_ns(phone.get('receive_end_ns')) and phone['receive_end_ns'] >= upper
        output['validation']['receive_window_covered'] = receive_covers
        output['valid'] = real_valid and coverage and receive_covers and package_matches and serial_matches
        output['display_fps'] = output['full_surface'].get('window_fps') if output['valid'] else None
        output['receive_fps'] = steady['complete_au_receive']['fps'] if real_valid and receive_covers else None
        gaps = output['full_surface'].get('window_gaps_ms', {})
        output.update(gap_p99_ms=gaps.get('p99_ms'), gap_max_ms=gaps.get('max_ms'),
                      gaps_over_100=gaps.get('over_100_ms'))
        ir = steady['latency_by_receive_window']['input_to_ready_ms']
        output.update(input_ready_p50_ms=ir.get('p50_ms'), input_ready_p99_ms=ir.get('p99_ms'))
    else:
        output['validation']['receive_window_covered'] = False
        output['full_surface'] = surface_summary(surface)
    trace, trace_status = read_saved(path.with_name(path.stem+'-capture-trace.json'), directory)
    output['host'] = host_window(trace, start, end)
    output['host']['file_status'] = trace_status
    worker = report.get('host_worker_timing_samples')
    worker_samples = []
    for sample in records(worker, 'samples'):
        clean = fields(sample, ('unix_ms', 'interval_ms', 'fps_cap', 'raw_fps',
            'submitted_fps', 'replaced_fps', 'idle_repeats'))
        if sample.get('event') in ('pipeline_sample', 'phase_sample'):
            clean['event'] = sample['event']
        timings = sample.get('timings')
        if isinstance(timings, dict):
            clean['timings'] = {phase: fields(timings.get(phase), ('count', 'p50_ms', 'p95_ms', 'max_ms'))
                for phase in ('capture_age', 'capture_gap', 'source_pts_gap', 'raw_queue', 'raw_pipe', 'encoded_egress')}
        worker_samples.append(clean)
    output['host']['whole_worker_samples'] = worker_samples
    output['host']['whole_worker_samples_evicted'] = fields(worker, ('samples_evicted',)).get('samples_evicted')
    return output


def summarize(directory):
    directory = directory.resolve()
    rows, matrices = [], []
    for matrix_path in sorted(directory.glob('*/matrix.json')):
        folder = matrix_path.parent
        if not LABEL.fullmatch(folder.name) or folder.resolve().parent != directory:
            continue
        matrix, status = read_saved(matrix_path, folder)
        if status != 'available':
            matrices.append({'run_label': folder.name, 'status': status})
            continue
        entries = records(matrix, 'runs')
        matrices.append({'run_label': folder.name, 'status': status, 'runs': len(entries),
            'matrix_failed': matrix.get('matrix_failed') is True,
            'source_fingerprints_unchanged': matrix.get('source_unchanged') is True})
        for row in entries:
            rows.append(report_row(folder, matrix, row))
    return {'schema': 'overnight-real-video-summary-v1',
        'scope': 'Offline saved real phone UDP metadata; not optical, acoustic, WAN or V50 acceptance',
        'rows': rows, 'matrices': matrices,
        'counts': {'matrices': len(matrices), 'rows': len(rows),
                   'valid_rows': sum(row['valid'] for row in rows),
                   'real_video_valid_rows': sum(row.get('real_video_valid') is True for row in rows)},
        'limitations': [
            'Phone windows use its first server packet; host uses its first gRPC return, with no cross-device alignment.',
            'SurfaceFlinger actual-present records are not optical panel timings or unique content hashes.',
            'SF timestamp endpoint coverage and conservative polling-risk checks cannot exclude every ring loss or layer recreation.',
            'Internal gaps require both presents within the fixed window; clipped boundary gaps are reported separately.',
            'Complete-AU receive counts require un-evicted native events; missing history yields lower-bound observed counts only.',
            'Retained decoder-ready records omit discarded outputs; callback receipts never certify physical display.',
            'Input-to-ready includes codec queueing, scheduling and output observation; it is not pure hardware execution.',
            'Schedule-minus-receive/ready are requested clock targets, not measured end-to-end latency.',
            'Video target bitrate, accepted AU bytes, UDP payload Mbps and encrypted/FEC wire pacing ceiling are different quantities.',
            'VT VBR readback includes short-window byte caps; a target bitrate does not guarantee the encoded bitrate.',
            'Audio and several inbox/FEC counters are whole-run totals, not steady-window or acoustic A/V evidence.',
            'Host joins require exact capture sequence and screenshot PTS; ambiguous events are excluded, never paired by nearest time.',
            'Missing Swift final trace summary prevents claiming zero diagnostic drops, even when a full window has observed records.',
            'Source acceptance uses two PLAYING snapshots plus long-window SF progress, not continuous playback or verified media FPS.',
            'Resolution trials also change bitrate; chronological, CPU and source variation prevent single-cause attribution.',
            'Codec-file fixtures are excluded; this summary reads no private runtime files and performs no device operations.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, default=DEFAULT_DIR)
    parser.add_argument('--output', type=Path, default=DEFAULT_DIR/'real-video-summary.json')
    args = parser.parse_args()
    report = summarize(args.input_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replacement allows reruns while live matrices are still accruing.
    temporary = args.output.with_name(args.output.name+'.tmp')
    temporary.write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
    temporary.replace(args.output)
    print(json.dumps(report['counts']))


if __name__ == '__main__':
    main()
