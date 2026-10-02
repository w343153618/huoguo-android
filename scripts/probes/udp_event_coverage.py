#!/usr/bin/env python3
"""Read event retention and cumulative counters offline; never operate devices.

An empty or cropped detail ring is not a zero-event count. Cumulative snapshots
survive ring eviction, but they cannot locate an event inside a presentation gap.
No host clock is compared with a phone clock here.
"""
import argparse
import json
from pathlib import Path


PHONE_FEC_KEYS = (
    'packets', 'wire_bytes', 'invalid', 'duplicate', 'settled_packets',
    'expired_packets', 'frames_expired', 'frames_delivered', 'recovered_shards',
    'reference_lost', 'keyframe_requests', 'dependency_dropped', 'memory_rejected',
    'clock_mapping_rejected', 'clock_mapping_evictions', 'clock_mapping_expired',
    'logical_body_rejected', 'completed_bodies', 'clock_mappings_active',
    'needs_keyframe', 'max_assembly_latency_us',
)
PHONE_QUEUE_KEYS = (
    'admitted_frames', 'overflow_events', 'overflow_rejected_frames',
    'queue_cleared_frames', 'waiting_idr_admission_drops', 'oversize_admission_drops',
    'closing_drops', 'recovery_epochs', 'recovery_idrs_completed',
    'maximum_depth', 'maximum_bytes', 'pending_frames', 'pending_bytes',
)
PACKETIZER_KEYS = (
    'source_frames', 'config_messages', 'source_idr', 'output_frames',
    'output_packets', 'plaintext_bytes', 'estimated_ipv4_encrypted_wire_bytes',
    'frame_budget_drops', 'output_deadline_drops', 'optional_tail_parity_frames',
    'optional_tail_parity_packets', 'unsupported_frames', 'dependent_source_drops',
    'recovery_requests', 'recovery_exhausted', 'recovery_completions',
    'stdout_blocked_us', 'stdout_max_blocked_us', 'max_au_bytes',
    'max_wire_frame_bytes', 'wire_bitrate',
)
SOCKET_KEYS = (
    'deadline_dropped_frames', 'skipped_chain_frames', 'incomplete_output_frames',
    'completed_idr_frames', 'tail_parity_deadline_frames',
    'tail_parity_deadline_datagrams', 'incomplete_fec_output_frames',
)


def integer(value):
    return value if type(value) is int and value >= 0 else None


def obj(value):
    return value if isinstance(value, dict) else {}


def records(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def counters(value, names):
    value = obj(value)
    return {name: value[name] for name in names if integer(value.get(name)) is not None}


def detail(value, evicted, *, timestamp=None, multiplier=1):
    rows = records(value)
    times = [row[timestamp]*multiplier for row in rows
             if timestamp and type(row.get(timestamp)) is int and row[timestamp] > 0]
    count = integer(evicted)
    return {
        'exported': isinstance(value, list), 'retained_records': len(rows),
        'non_object_records': len(value)-len(rows) if isinstance(value, list) else None,
        'evicted_records': count,
        'retention_status': ('missing' if not isinstance(value, list) else
                             'unknown' if count is None else
                             'cropped_prefix' if count else 'not_cropped'),
        'first_retained_time_ns': min(times) if times else None,
        'last_retained_time_ns': max(times) if times else None,
        'invalid_timestamp_records': len(rows)-len(times) if timestamp else None,
        'timestamps_non_decreasing': all(b >= a for a, b in zip(times, times[1:])) if timestamp else None,
    }


def coverage(report):
    """Keep whole-session counters separate from bounded detail histories."""
    if not isinstance(report, dict):
        raise ValueError('Expected combined UDP report object')
    phone, host = obj(report.get('phone')), obj(report.get('host'))
    native_stats = obj(phone.get('native_frame_event_stats'))
    native = detail(phone.get('native_frame_events'), phone.get('native_frame_events_evicted'),
                    timestamp='event_phone_us', multiplier=1000)
    rows = records(phone.get('native_frame_events'))
    sequences = [integer(row.get('event_sequence')) for row in rows]
    valid_sequence = (all(value is not None and value > 0 for value in sequences)
                      and all(b == a+1 for a, b in zip(sequences, sequences[1:])))
    generated, pending, internal_evicted = (integer(native_stats.get(key)) for key in
                                          ('generated', 'pending', 'native_events_evicted'))
    java_evicted = native['evicted_records']
    accounting = (generated is not None and pending is not None and
                  internal_evicted is not None and java_evicted is not None and
                  generated == len(rows)+pending+internal_evicted+java_evicted)
    tail_exported = (accounting and pending == 0 and valid_sequence and native['non_object_records'] == 0 and
                     (sequences[-1] == generated if sequences else generated == 0))
    full = bool(tail_exported and internal_evicted == java_evicted == 0)
    suffix = bool(tail_exported and not full and rows)
    native.update({
        'enabled': native_stats.get('enabled') if type(native_stats.get('enabled')) is bool else None,
        'capacity_records': integer(phone.get('native_frame_event_capacity')),
        'generated_records': generated, 'native_pending_records': pending,
        'native_ring_evicted_records': internal_evicted,
        'first_retained_sequence': sequences[0] if sequences else None,
        'last_retained_sequence': sequences[-1] if sequences else None,
        'retained_sequences_contiguous': valid_sequence if rows else None,
        'generated_accounting_matches': accounting,
        'detail_history_status': ('disabled' if native_stats.get('enabled') is False else
                                  'all_generated_events_exported' if full else
                                  'contiguous_suffix_exported' if suffix else
                                  'incomplete_or_unknown'),
        'complete_generated_history': full and native_stats.get('enabled') is True,
        'complete_generated_tail': bool((full or suffix) and native_stats.get('enabled') is True),
        'clock_scope': 'phone_monotonic_event_decisions_not_host_capture',
    })
    queue = obj(phone.get('video_input_queue'))
    epoch = detail(phone.get('inbox_epoch_events'), queue.get('epoch_events_evicted'), timestamp='time_ns')
    epoch['capacity_records'] = integer(queue.get('epoch_event_capacity'))
    epoch['complete_generated_history'] = bool(epoch['exported'] and epoch['evicted_records'] == 0
                                               and phone.get('video_worker_alive') is False)
    epoch['complete_generated_tail'] = bool(epoch['exported'] and epoch['evicted_records'] is not None
                                            and phone.get('video_worker_alive') is False)
    socket = obj(report.get('socket_video'))
    host_detail = detail(report.get('native_events'), host.get('native_events_evicted'))
    host_detail['clock_scope'] = 'host_monotonic_not_comparable_to_phone'
    socket_detail = detail(socket.get('frame_events'), socket.get('frame_events_evicted'))
    socket_detail['clock_scope'] = 'host_monotonic_not_comparable_to_phone'
    summaries = [row for row in records(report.get('native_events')) if row.get('event') == 'summary']
    latest = summaries[-1] if summaries else {}
    begin, end = integer(phone.get('first_server_packet_ns')), integer(phone.get('receive_end_ns'))
    bounds_valid = begin is not None and end is not None and 0 < begin < end
    return {
        'schema': 1,
        'scope': 'Offline readback only; no ring expansion or runtime behavior change',
        'phone_observation': {
            'first_packet_ns': begin, 'receive_end_ns': end,
            'receive_seconds': (end-begin)/1e9 if bounds_valid else None,
            'requested_seconds': integer(phone.get('requested_seconds')),
        },
        'detail_streams': {'phone_native': native, 'phone_inbox': epoch,
                           'host_native': host_detail, 'host_socket': socket_detail,
                           'phone_presentation': detail(phone.get('presentation_frames'),
                                                        phone.get('presentation_records_evicted'),
                                                        timestamp='callback_ns')},
        'cumulative_snapshots': {
            'phone_native_fec': {
                'scope': 'Receiver creation through final nativeStats snapshot; not a per-stall window',
                'values': counters(phone.get('native_fec'), PHONE_FEC_KEYS),
            },
            'phone_video_inbox': {
                'scope': 'Inbox creation through snapshot; not a per-stall window',
                'values': counters(queue, PHONE_QUEUE_KEYS),
            },
            'host_socket_guard': {
                'scope': 'SocketVideoGate creation through snapshot; not receiver delivery',
                'values': counters(socket.get('counts'), SOCKET_KEYS),
            },
            'host_packetizer': {
                'scope': 'Packetizer creation through latest retained cumulative summary only',
                'values': counters(latest, PACKETIZER_KEYS),
                'elapsed_us': integer(latest.get('elapsed_us')),
                'final': latest.get('final') if type(latest.get('final')) is bool else None,
                'complete_final_snapshot': latest.get('final') is True,
            },
        },
        'limits': [
            'Missing counters stay missing; retained event counts never substitute for cumulative counters',
            'A non-final packetizer snapshot can omit the last partial interval',
            'Cropped or pending event history cannot prove absence of events in an earlier gap',
            'Even complete same-clock event association does not establish a single cause',
            'Counter zeros do not measure optical tearing, audio/video timing, or WAN packet loss',
        ],
    }


def phone_window_coverage(readback, start_ns, end_ns):
    """Whether exported details support absence checks in this phone-only window."""
    observation = obj(readback.get('phone_observation'))
    begin, finish = observation.get('first_packet_ns'), observation.get('receive_end_ns')
    inside = (type(start_ns) is int and type(end_ns) is int and start_ns <= end_ns and
              type(begin) is int and type(finish) is int and begin <= start_ns <= end_ns <= finish)
    result = {}
    for name in ('phone_native', 'phone_inbox'):
        stream = obj(obj(readback.get('detail_streams')).get(name))
        first = stream.get('first_retained_time_ns')
        clocks_valid = (stream.get('invalid_timestamp_records') == 0 and
                        stream.get('timestamps_non_decreasing') is True)
        complete = bool(inside and clocks_valid and (stream.get('complete_generated_history') is True or
                        (stream.get('complete_generated_tail') is True and type(first) is int
                         and start_ns >= first)))
        result[name] = {
            'window_detail_complete': complete,
            'status': ('complete_generated_event_window' if complete else
                       'outside_receive_observation' if not inside else
                       'cropped_prefix_or_unknown_history'),
            'empty_events_are_not_zero_cumulative_count': True,
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = coverage(json.loads(args.report.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'output': str(args.output),
                      'phone_detail_status': result['detail_streams']['phone_native']['detail_history_status']}))


if __name__ == '__main__':
    main()
