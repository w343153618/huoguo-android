"""Closed, bounded offline native event/worker-prefix observations.

No default file/device/network access, input or live observer. Valid JSON does
not establish an App Attempt, JNI provenance or a shared phone clock. The caller
must qualify those independently before requesting cross-prefix association.
"""
from __future__ import annotations

from scripts.probes import transport_progress_analysis as progress


MAX_LONG = (1 << 63) - 1
MAX_PHONE_US = MAX_LONG // 1000 - 1
COLUMNS = ('frame_id', 'reference_id', 'flags', 'first_arrival_us', 'last_arrival_us',
           'fec_quorum_ready_us', 'event_phone_us', 'deadline_phone_us', 'logical_bytes',
           'reason_code', 'event_sequence', 'pts_us')
ZERO_FLAGS = ('all_pipeline_events_covered', 'timestamp_is_physical_packet_arrival',
              'quorum_is_codec_ready', 'host_phone_clock_subtraction_valid',
              'frame_identity_from_JSON_verified')
BASE = {'schema_version', 'available', 'enabled', 'capacity', 'source_ring_capacity',
        'native_ring_capacity', 'all_generated_events_exported', 'omitted_rows_validated',
        *ZERO_FLAGS}
COUNTS = {'source_retained_count', 'exported_count', 'omitted_prefix_following_count',
          'generated_count', 'native_pending_count', 'native_evicted_count',
          'java_evicted_count', 'exported_sequence_contiguous_from_one'}
REASONS = {1: (0,), 2: (1, 2), 3: (3,), 4: (4,), 5: (5, 6)}


def integer(value, low=0, high=MAX_LONG):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('native_integral_bound')
    return value


def validate(value):
    """Validate only the exported remaining prefix, never omitted source rows."""
    if type(value) is not dict:
        raise ValueError('native_schema')
    available = integer(value.get('available'), 0, 1)
    expected = BASE | (COUNTS | {'event_code', *COLUMNS} if available else set())
    if len(value) != len(expected) or set(value) != expected:
        raise ValueError('native_closed_schema')
    for field, wanted in (('schema_version', 1), ('capacity', 64),
                          ('source_ring_capacity', 8192), ('native_ring_capacity', 256)):
        integer(value[field], wanted, wanted)
    for flag in ZERO_FLAGS:
        integer(value[flag], 0, 0)
    all_generated = integer(value['all_generated_events_exported'], 0, 1)
    omitted_validated = integer(value['omitted_rows_validated'], 0, 1)
    enabled = integer(value['enabled'], -1, 1)
    if not available:
        if enabled == 1 or all_generated or omitted_validated:
            raise ValueError('native_unavailable_claim')
        return None
    if enabled != 1:
        raise ValueError('native_enabled_snapshot_required')
    n = integer(value['exported_count'], 0, 64)
    retained = integer(value['source_retained_count'], n, 8192)
    generated = integer(value['generated_count'])
    pending = integer(value['native_pending_count'], 0, 256)
    native_evicted = integer(value['native_evicted_count'])
    java_evicted = integer(value['java_evicted_count'])
    if (n != min(retained, 64) or generated != retained + pending + native_evicted + java_evicted
            or integer(value['omitted_prefix_following_count']) != retained - n
            or omitted_validated != int(n == retained)):
        raise ValueError('native_accounting_or_prefix')
    for field in ('event_code', *COLUMNS):
        if type(value[field]) is not list or len(value[field]) != n:
            raise ValueError('native_column_length')
    rows = []
    frames = {}
    previous_sequence = previous_time = 0
    contiguous = True
    time_fields = ('first_arrival_us', 'last_arrival_us', 'fec_quorum_ready_us',
                   'event_phone_us', 'deadline_phone_us')
    for i in range(n):
        row = {field: integer(value[field][i]) for field in ('event_code', *COLUMNS)}
        kind, reason = row['event_code'], row['reason_code']
        if kind not in REASONS or reason not in REASONS[kind]:
            raise ValueError('native_event_reason')
        for field in time_fields:
            integer(row[field], 0, MAX_PHONE_US)
        first, last, quorum = (row[key] for key in time_fields[:3])
        at, deadline = row['event_phone_us'], row['deadline_phone_us']
        seq = row['event_sequence']
        if (not row['frame_id'] or row['flags'] > 3 or not 1 <= row['logical_bytes'] <= 1048576
                or first == 0 or last < first or at < last or not 1000 <= deadline - first <= 80000
                or quorum != 0 and not first <= quorum <= last
                or kind == 1 and quorum == 0 or kind == 3 and at < deadline
                or not previous_sequence < seq <= generated or at < previous_time):
            raise ValueError('native_metadata_order')
        contiguous &= seq == previous_sequence + 1
        previous_sequence, previous_time = seq, at
        # Same ID within one retained prefix must not contradict the original
        # mapping. This consistency check is not independent frame ownership.
        identity = tuple(row[k] for k in ('reference_id', 'flags', 'first_arrival_us',
                                         'deadline_phone_us', 'logical_bytes'))
        known = frames.setdefault(row['frame_id'], {'identity': identity, 'kinds': set(), 'terminal': False})
        if known['identity'] != identity or kind in known['kinds'] or known['terminal']:
            raise ValueError('native_frame_prefix_contradiction')
        known['kinds'].add(kind)
        known['terminal'] |= kind != 1
        rows.append(row)
    all_observed = (n == retained and pending == native_evicted == java_evicted == 0
                    and contiguous and previous_sequence == generated)
    if (integer(value['exported_sequence_contiguous_from_one'], 0, 1) != int(contiguous)
            or all_generated != int(all_observed)):
        raise ValueError('native_sequence_coverage')
    return rows


def analyze(native_events, transport, worker_rows, *, same_attempt_phone_clock_verified=False):
    """Observed retained event counts inside a worker plateau, never causation.

    The native microsecond clock is floor(phone nanoTime /1000). A row is
    interior only if its entire possible [us*1000, us*1000+999] interval is
    strictly inside the plateau. Boundary overlap is reported separately.
    Neither this interval nor RX sample t_ns is physical packet arrival time.
    """
    if type(same_attempt_phone_clock_verified) is not bool:
        raise ValueError('native_clock_provenance_flag')
    rows = validate(native_events)
    rx = progress.analyze(transport, worker_rows,
                          same_attempt_phone_clock_verified=same_attempt_phone_clock_verified)
    result = {'schema': 'phone-native-frame-progress-analysis-v1',
              'same_attempt_phone_clock_verified_by_caller': same_attempt_phone_clock_verified,
              'retained_native_rows': None if rows is None else len(rows),
              'native_observation_available': rows is not None,
              'whole_session_or_plateau_native_coverage_verified': False,
              'physical_packet_loss_or_FEC_root_cause_proven': False,
              'codec_ready_or_presentation_verified': False,
              'native_zero_counts_mean_no_retained_event_only': True,
              'microsecond_quantization_max_ns': 999,
              'RX_progress': rx, 'plateaus': []}
    if not same_attempt_phone_clock_verified:
        result['association_status'] = 'provenance_unverified'
        return result
    if rows is None:
        result['association_status'] = 'native_unavailable_not_zero_events'
        return result
    if rx['correlation_status'] == 'legacy_RX_unavailable':
        result['association_status'] = 'RX_unavailable_native_prefix_unassociated'
        return result
    result['association_status'] = 'retained_prefix_only'
    for plateau in rx['plateaus']:
        start, end = plateau['worker_start_ns'], plateau['worker_end_ns']
        interior = [r for r in rows if start < r['event_phone_us'] * 1000
                    and r['event_phone_us'] * 1000 + 999 < end]
        ambiguous = sum(1 for r in rows if r['event_phone_us'] * 1000 <= end
                        and r['event_phone_us'] * 1000 + 999 >= start
                        and not (start < r['event_phone_us'] * 1000
                                 and r['event_phone_us'] * 1000 + 999 < end))
        counts = [sum(r['event_code'] == kind for r in interior) for kind in range(1, 6)]
        result['plateaus'].append({'worker_start_ns': start, 'worker_end_ns': end,
                                   'interior_retained_event_count': len(interior),
                                   'boundary_ambiguous_retained_event_count': ambiguous,
                                   'interior_event_counts_code1_to5': counts,
                                   'interior_distinct_frame_ids': len({r['frame_id'] for r in interior}),
                                   'native_whole_plateau_coverage_verified': False,
                                   'observed_counts_are_not_fault_or_loss_rates': True})
    return result
