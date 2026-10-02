#!/usr/bin/env python3
"""Offline pause/resume analysis from bounded numeric graphics and clock data.

Actions use host clock_gettime(CLOCK_MONOTONIC), bracket the command separately
from its subsequent media-state read, and alternate pause/resume. Media states
are 0=unknown, 2=paused and 3=playing. A steady window's index is the index of
its pause/resume pair, rather than an action index. No device access is made.

Examples of the action JSON (all timestamps are integer ns)::

  {"schema":1,"host_clock":"host_clock_gettime_CLOCK_MONOTONIC_ns",
   "measurement_host_before_ns":1,"measurement_host_after_ns":100,
   "actions":[{"action_index":0,"action":"pause","host_before_ns":10,
      "host_after_ns":11,"media_state_before":3,"media_state_after":2,
      "media_state_host_before_ns":12,"media_state_host_after_ns":13},
     {"action_index":1,"action":"resume","host_before_ns":30,
      "host_after_ns":31,"media_state_before":2,"media_state_after":3,
      "media_state_host_before_ns":32,"media_state_host_after_ns":33}],
   "steady_windows":[{"window_index":0,"host_before_ns":50,"host_after_ns":90}]}

The host/guest constant offset and the observed MONOTONIC-minus-BOOTTIME
snapshot range are conditional models. Their interval widths are retained,
without nearest-time matching to media PTS or an exact-clock claim.
"""
import argparse
import bisect
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.probes import analyze_graphics_frame_trace as graphics  # noqa: E402
from scripts.probes.measure_source_frame_fences import HOST_CLOCK, clock_mapping_sample  # noqa: E402

MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_ACTIONS = 8
INT64_MAX = 2**63 - 1
ACTION_KEYS = {'action_index', 'action', 'host_before_ns', 'host_after_ns',
               'media_state_before', 'media_state_after',
               'media_state_host_before_ns', 'media_state_host_after_ns'}
WINDOW_KEYS = {'window_index', 'host_before_ns', 'host_after_ns'}
PHASE_KEYS = ('sf_receipt_queue_ts_ns', 'latch_ts_ns', 'present_fence_ts_ns')
INTERVAL_KEYS = ('queue_to_latch_ns', 'latch_to_present_ns', 'queue_to_present_ns')
IDENTITY_KEYS = ('machine_id', 'track_id', 'buffer_id_low32', 'frame_number_low32')


def integer(value, allow_none=False, signed=False):
    if value is None and allow_none:
        return value
    if type(value) is not int or not (-INT64_MAX if signed else 0) <= value < INT64_MAX:
        raise ValueError('integer_metadata_required')
    return value


def bracket(row, prefix='host'):
    lower = integer(row[prefix + '_before_ns'])
    upper = integer(row[prefix + '_after_ns'])
    if upper < lower:
        raise ValueError('reversed_host_bracket')
    return [lower, upper]


def identity(row):
    return tuple(row[key] for key in IDENTITY_KEYS)


def actions_schema(data):
    keys = {'schema', 'host_clock', 'measurement_host_before_ns',
            'measurement_host_after_ns', 'actions', 'steady_windows'}
    if not isinstance(data, dict) or set(data) != keys or \
            type(data['schema']) is not int or data['schema'] != 1 or data['host_clock'] != HOST_CLOCK:
        raise ValueError('invalid_action_schema_or_host_clock')
    measurement = bracket(data, 'measurement_host')
    actions, windows = data['actions'], data['steady_windows']
    if not isinstance(actions, list) or not 2 <= len(actions) <= MAX_ACTIONS or len(actions) % 2:
        raise ValueError('bounded_alternating_action_pairs_required')
    previous = measurement[0]
    for index, row in enumerate(actions):
        if not isinstance(row, dict) or set(row) != ACTION_KEYS or \
                type(row['action_index']) is not int or row['action_index'] != index or \
                row['action'] != ('pause' if index % 2 == 0 else 'resume'):
            raise ValueError('invalid_alternating_action')
        before, after = bracket(row)
        state_before, state_after = bracket(row, 'media_state_host')
        if any(type(row[key]) is not int or row[key] not in (0, 2, 3)
               for key in ('media_state_before', 'media_state_after')):
            raise ValueError('invalid_media_state_enum')
        if before < previous or state_before < after or state_after > measurement[1]:
            raise ValueError('action_observation_order_or_coverage')
        previous = state_after
    if not isinstance(windows, list) or len(windows) > len(actions) // 2:
        raise ValueError('bounded_steady_windows_required')
    seen = set()
    for row in windows:
        if not isinstance(row, dict) or set(row) != WINDOW_KEYS:
            raise ValueError('invalid_steady_window_schema')
        index = integer(row['window_index'])
        if index in seen or index >= len(actions) // 2:
            raise ValueError('invalid_steady_window_index')
        seen.add(index)
        before, after = bracket(row)
        resume = actions[2 * index + 1]
        end = actions[2 * index + 2]['host_before_ns'] if 2 * index + 2 < len(actions) else measurement[1]
        if before < resume['media_state_host_after_ns'] or after <= before or after > end:
            raise ValueError('steady_window_outside_confirmed_play_interval')
    return actions, windows, measurement


def checked_graphics(data):
    """Rebuild associations from the numeric rows, never trust exported joins."""
    if not isinstance(data, dict) or type(data.get('schema')) is not int or data['schema'] != 1:
        raise ValueError('invalid_graphics_schema')
    sections = {}
    for header, name in graphics.HEADERS.items():
        rows = data.get(name)
        if not isinstance(rows, list) or len(rows) > graphics.MAX_ROWS:
            raise ValueError('bounded_graphics_sections_required')
        for row in rows:
            if not isinstance(row, dict) or set(row) != set(header):
                raise ValueError('invalid_graphics_numeric_row')
            for key in header:
                integer(row[key], allow_none=name != 'events', signed=key == 'dur_ns')
            if name == 'events' and (row['event_type'] not in graphics.EVENTS or
                    row['buffer_id_low32'] >= 2**32 or row['frame_number_low32'] >= 2**32):
                raise ValueError('invalid_graphics_identity_or_event')
        sections[name] = rows
    if len(sections['trace_bounds']) != 1 or len(sections['coverage']) != 1:
        raise ValueError('invalid_graphics_singleton_section')
    trace_bytes = integer(data.get('raw_trace_bytes'))
    maximum = integer(data.get('configured_max_trace_bytes'))
    if maximum <= 0:
        raise ValueError('invalid_trace_byte_limit')
    return graphics.summarize(sections, trace_bytes, max_trace_bytes=maximum)


def clock_model(graphics_data, fences):
    if not isinstance(fences, dict) or type(fences.get('schema')) is not int or \
            fences['schema'] != 1 or fences.get('host_clock') != HOST_CLOCK:
        raise ValueError('invalid_fence_schema_or_host_clock')
    snapshot_rows = graphics_data['clock_relations']
    queries = fences.get('guest_clock_queries')
    if not isinstance(queries, list) or len(queries) > 256 or any(not isinstance(row, dict) for row in queries):
        raise ValueError('bounded_numeric_clock_queries_required')
    samples = [row for row in queries if row.get('available') is True]
    if len(samples) < 2 or len(snapshot_rows) < 2:
        return None, ['insufficient_clock_samples']
    query_bounds = []
    for sample in samples:
        if sample.get('host_clock') != HOST_CLOCK:
            raise ValueError('mixed_fence_query_host_clock')
        bounds = sample.get('guest_to_host_monotonic_offset_bounds_ns')
        if not isinstance(bounds, list) or len(bounds) != 2:
            raise ValueError('invalid_constant_offset_bounds')
        lower, upper = (integer(value, signed=True) for value in bounds)
        if upper < lower:
            raise ValueError('reversed_constant_offset_bounds')
        before, after = bracket(sample, 'host_query')
        guest_us = integer(sample.get('guest_monotonic_us'))
        span_us = integer(sample.get('guest_sample_span_us'))
        derived = clock_mapping_sample({'monotonic_us': guest_us, 'unix_us': 0,
                                        'sample_span_us': span_us}, before, after)
        if derived['guest_to_host_monotonic_offset_bounds_ns'] != bounds:
            raise ValueError('clock_bounds_do_not_match_numeric_exchange')
        query_bounds.append([lower, upper])
    ordered = sorted(samples, key=lambda row: row['host_query_before_ns'])
    if any(b['guest_monotonic_us'] < a['guest_monotonic_us'] for a, b in zip(ordered, ordered[1:])):
        return None, ['guest_monotonic_epoch_reset']
    lo, hi = max(row[0] for row in query_bounds), min(row[1] for row in query_bounds)
    if lo > hi:
        return None, ['no_common_constant_offset_model']
    if any(any(row[key] is None for key in row) or row['trace_ts_ns'] != row['boottime_ns']
           for row in snapshot_rows):
        return None, ['invalid_snapshot_trace_boottime_mapping']
    ordered_snapshots = sorted(snapshot_rows, key=lambda row: row['trace_ts_ns'])
    if any(b['monotonic_ns'] < a['monotonic_ns'] for a, b in zip(ordered_snapshots, ordered_snapshots[1:])):
        return None, ['snapshot_monotonic_epoch_reset']
    deltas = [row['monotonic_ns'] - row['boottime_ns'] for row in snapshot_rows]
    dlo, dhi = min(deltas), max(deltas)
    first, last = min(row['trace_ts_ns'] for row in snapshot_rows), max(row['trace_ts_ns'] for row in snapshot_rows)
    snapshot_coverage = [first + dhi + hi, last + dlo + lo]
    exchange_coverage = [min(row['host_query_before_ns'] for row in samples),
                         max(row['host_query_after_ns'] for row in samples)]
    fence_coverage = bracket(fences, 'measurement_host')
    return {'sample_count': len(samples), 'constant_guest_to_host_offset_bounds_ns': [lo, hi],
            'constant_offset_width_ns': hi - lo,
            'snapshot_count': len(snapshot_rows),
            'snapshot_monotonic_minus_boottime_observed_range_ns': [dlo, dhi],
            'combined_trace_to_host_observed_model_range_ns': [dlo + lo, dhi + hi],
            'combined_range_width_ns': dhi + hi - dlo - lo,
            'snapshot_supported_host_interval_ns': snapshot_coverage,
            'exchange_supported_host_interval_ns': exchange_coverage,
            'fence_measurement_host_interval_ns': fence_coverage,
            'model_supported_host_interval_ns': [max(snapshot_coverage[0], exchange_coverage[0], fence_coverage[0]),
                                                  min(snapshot_coverage[1], exchange_coverage[1], fence_coverage[1])],
            'offset_is_exact': False,
            'constant_guest_host_offset_assumed_not_proven': True,
            'snapshot_range_between_samples_is_assumed_not_proven': True,
            'unmodelled_clock_drift_or_suspend_bound_available': False,
            'host_roundtrip_bounds_preserved': True}, []


def host_range(ts, model):
    if ts is None:
        return None
    lower, upper = model['combined_trace_to_host_observed_model_range_ns']
    return [ts + lower, ts + upper]


def contained(bounds, window):
    return bounds is not None and window[0] <= bounds[0] and bounds[1] <= window[1]


def overlap(bounds, window):
    return bounds[0] <= window[1] and bounds[1] >= window[0]


def phase_summary(chains):
    return {key: graphics.distribution([row[key] for row in chains]) for key in INTERVAL_KEYS}


def steady_summary(window, chains, queues, model, min_frames):
    bounds = bracket(window)
    eligible = [row for row in chains if contained(host_range(row['sf_receipt_queue_ts_ns'], model), bounds)]
    complete = [row for row in eligible if all(row[key] is not None for key in PHASE_KEYS)]
    interior = [row for row in complete if all(contained(host_range(row[key], model), bounds) for key in PHASE_KEYS)]
    selected_queues = [row for row in queues if contained(host_range(row['ts_ns'], model), bounds)]
    queue_times = sorted(row['ts_ns'] for row in queues)
    # Count strict temporal overlap within the same trace domain. This is not
    # a frame/PTS join or an estimate of producer buffer capacity.
    overlap_counts = sorted(bisect.bisect_left(queue_times, row['latch_ts_ns']) -
                            bisect.bisect_right(queue_times, row['sf_receipt_queue_ts_ns'])
                            for row in interior)
    result = {'window_index': window['window_index'], 'host_interval_ns': bounds,
              'eligible_identity_count': len(eligible),
              'missing_phase_identity_count': len(eligible) - len(complete),
              'phase_crosses_window_edge_count': len(complete) - len(interior),
              'complete_interior_identity_count': len(interior),
              'comparison_minimum_frame_count': min_frames,
              'enough_frames_for_comparison': len(interior) >= min_frames,
              'same_identity_trace_intervals': phase_summary(interior),
              'later_queue_markers_strictly_between_same_identity_q_and_l': {
                  'frame_count': len(overlap_counts),
                  'positive_count_frames': sum(value > 0 for value in overlap_counts),
                  'p50_count': overlap_counts[(len(overlap_counts) - 1) // 2] if overlap_counts else None,
                  'p95_count': overlap_counts[int((len(overlap_counts) - 1) * .95)] if overlap_counts else None,
                  'max_count': max(overlap_counts, default=None),
                  'exact_producer_buffer_depth_identified': False,
                  'queue_to_latch_interval_is_serial_processing_cost': False},
              'queue_marker_count': len(selected_queues),
              'queue_marker_interval': graphics.distribution([b['ts_ns'] - a['ts_ns']
                                                             for a, b in zip(selected_queues, selected_queues[1:])])}
    return result, interior


def pause_summary(pause, resume, queues, model, min_silence_ns):
    start = max(pause['host_after_ns'], pause['media_state_host_after_ns'])
    end = resume['host_before_ns']
    window = [start, end]
    possible = [host_range(row['ts_ns'], model) for row in queues if overlap(host_range(row['ts_ns'], model), window)]
    definite = [bounds for bounds in possible if contained(bounds, window)]
    quiet_after = max([start, *(min(end, bounds[1]) for bounds in possible)])
    tail = max(0, end - quiet_after)
    return {'confirmed_pause_host_interval_ns': window, 'confirmed_pause_duration_ns': end - start,
            'possible_queue_marker_count_given_model': len(possible),
            'definite_queue_marker_count_given_model': len(definite),
            'latest_possible_queue_host_upper_ns': max((bounds[1] for bounds in possible), default=None),
            'tail_without_new_queue_lower_bound_ns_given_model': tail,
            'minimum_required_silence_ns': min_silence_ns,
            'tail_without_new_queue_requirement_met_given_model': tail >= min_silence_ns,
            'marker_silence_does_not_prove_decoder_inactivity': True,
            'instrumentation_completeness_proven': False}


def first_summary(resume, next_pause, queues, chain_by_identity, model, coverage, _diagnostic=False):
    action_bounds = bracket(resume)
    cutoff = next_pause['host_before_ns'] if next_pause else coverage[1]
    ambiguous = [row for row in queues if overlap(host_range(row['ts_ns'], model), action_bounds)]
    result = {'resume_action_index': resume['action_index'],
              'resume_command_host_interval_ns': action_bounds,
              'resume_media_state_read_host_interval_ns': bracket(resume, 'media_state_host'),
              'new_queue_search_end_host_ns': cutoff,
              'queue_markers_ambiguous_at_resume_boundary': len(ambiguous),
              'first_queue': None, 'status': 'rejected', 'reject_codes': [],
              'diagnostic_earliest_certain_post_command_queue': None,
              'first_sf_receipt_may_be_stale_or_prequeued': True,
              'new_media_content_identity_proven': False,
              'producer_queue_or_codec_release_observed': False,
              'player_implementation_known': False,
              'media_pts_association_performed': False}
    if ambiguous and not _diagnostic:
        result['reject_codes'].append('resume_boundary_queue_ambiguous')
        candidate = first_summary(resume, next_pause, queues, chain_by_identity,
                                  model, coverage, _diagnostic=True)
        result['diagnostic_earliest_certain_post_command_queue'] = {
            'true_first_proven': False,
            'earlier_boundary_queue_count': len(ambiguous),
            'boundary_ambiguity_retained': True,
            'status': ('accepted_same_identity_diagnostic_given_model'
                       if candidate['status'] == 'accepted_same_identity_q_l_p_given_model' else 'rejected'),
            'reject_codes': candidate['reject_codes'],
            'same_identity_chain': candidate['first_queue'],
            'candidate_may_be_stale_or_prequeued': True,
            'causal_first_frame_comparison_permitted': False}
        return result
    new = [row for row in queues if host_range(row['ts_ns'], model)[0] > action_bounds[1]
           and host_range(row['ts_ns'], model)[0] < cutoff]
    if not new:
        result['reject_codes'].append('no_certain_new_queue_in_resume_window')
        return result
    first = new[0]
    q_bounds = host_range(first['ts_ns'], model)
    selected = {key: first[key] for key in IDENTITY_KEYS}
    selected['sf_receipt_queue_ts_ns'] = first['ts_ns']
    selected['sf_receipt_queue_host_model_range_ns'] = q_bounds
    result['first_queue'] = selected
    if sum(row['ts_ns'] == first['ts_ns'] for row in new) > 1:
        result['reject_codes'].append('first_queue_timestamp_tie')
        return result
    if q_bounds[1] >= cutoff or not contained(q_bounds, coverage):
        result['reject_codes'].append('first_queue_boundary_or_coverage_ambiguous')
        return result
    chain = chain_by_identity.get(identity(first))
    if chain is None:
        result['reject_codes'].append('first_queue_identity_rejected')
        return result
    selected.update({key: chain[key] for key in ('dequeue_ts_ns', 'acquire_fence_ts_ns', *PHASE_KEYS, *INTERVAL_KEYS)})
    selected['missing_phase_count'] = sum(chain[key] is None for key in PHASE_KEYS)
    selected['phase_host_model_ranges_ns'] = {key: host_range(chain[key], model) for key in PHASE_KEYS}
    selected['resume_command_to_phase_model_interval_ns'] = {
        key: [bounds[0] - action_bounds[1], bounds[1] - action_bounds[0]] if bounds else None
        for key, bounds in selected['phase_host_model_ranges_ns'].items()}
    d_bounds = host_range(chain['dequeue_ts_ns'], model)
    selected['dequeue_definitely_before_resume_command_given_model'] = d_bounds is not None and d_bounds[1] < action_bounds[0]
    selected['dequeue_identity_may_be_parser_backfilled'] = True
    if selected['missing_phase_count']:
        result['reject_codes'].append('first_queue_missing_latch_or_present_phase')
    elif any(not contained(bounds, coverage) for bounds in selected['phase_host_model_ranges_ns'].values()):
        result['reject_codes'].append('first_queue_phase_outside_snapshot_coverage')
    elif any(bounds[1] >= cutoff for bounds in selected['phase_host_model_ranges_ns'].values()):
        result['reject_codes'].append('first_queue_phase_crosses_next_pause_boundary')
    else:
        result['status'] = 'accepted_same_identity_q_l_p_given_model'
    return result


def comparison(first, steady_rows, min_frames):
    if first['status'] != 'accepted_same_identity_q_l_p_given_model':
        return {'status': 'rejected', 'reject_codes': ['first_queue_not_accepted']}
    selected = first['first_queue']
    rows = [row for row in steady_rows if identity(row) != identity(selected)]
    if len(rows) < min_frames:
        return {'status': 'rejected', 'reject_codes': ['insufficient_complete_steady_identities'],
                'steady_frame_count': len(rows)}
    phases = {}
    for key in INTERVAL_KEYS:
        values = sorted(row[key] for row in rows)
        middle = values[(len(values) - 1) // 2]
        phases[key] = {'first_ns': selected[key], 'steady_median_ns': middle,
                       'first_minus_steady_median_ns': selected[key] - middle,
                       'steady_count_le_first': sum(value <= selected[key] for value in values),
                       'steady_frame_count': len(values),
                       'first_empirical_percentile': round(100 * sum(value <= selected[key] for value in values) / len(values), 3)}
    return {'status': 'accepted_same_identity_interval_comparison', 'reject_codes': [],
            'steady_frame_count': len(rows), 'trace_clock_intervals': phases,
            'steady_empirical_percentile_is_not_causal_proof': True}


def summarize(graphics_input, fences, action_input, min_silence_ns=1_000_000_000, steady_min_frames=10):
    if type(min_silence_ns) is not int or min_silence_ns < 1_000_000_000 or \
            type(steady_min_frames) is not int or not 1 <= steady_min_frames <= 500:
        raise ValueError('invalid_analysis_threshold')
    actions, windows, measurement = actions_schema(action_input)
    data = checked_graphics(graphics_input)
    model, rejects = clock_model(data, fences)
    coverage = data['coverage'][0]
    if fences.get('complete_fixed_generation_verified') is not True:
        rejects.append('fence_fixed_generation_not_verified')
    if data['identity_association']['observed_queue_frame_reset_count']:
        rejects.append('observed_queue_generation_reset')
    if coverage['target_layer_name_count'] != 1 or coverage['target_machine_count'] != 1:
        rejects.append('single_graphics_target_not_verified')
    if coverage['invalid_buffer_identity_count'] or coverage['unsupported_buffer_event_count']:
        rejects.append('unsupported_or_invalid_graphics_identity')
    if coverage['loss_or_overrun_value'] or coverage['other_import_error_value']:
        rejects.append('positive_trace_loss_or_import_error')
    if data['event_rows_at_limit'] or data['trace_near_size_limit']:
        rejects.append('trace_or_export_near_limit')
    report = {'schema': 1, 'probe': 'pause_resume_sf_receipt_first_queue_offline',
              'status': 'rejected_evidence' if rejects else 'analyzed_given_clock_model',
              'reject_codes': rejects, 'clock_model': model,
              'action_pair_count': len(actions) // 2, 'cycles': [], 'steady_windows': [],
              'graphics_identity_counts': data['identity_association'],
              'graphics_parser_diagnostic_count': coverage['graphics_parser_diagnostic_count'],
              'limitations': [
                  'clock_offset_is_conditional_constant_model_with_unbounded_unmodelled_drift',
                  'snapshot_observed_offset_range_is_not_exact_between_samples',
                  'queue_is_sf_transaction_receipt_not_producer_queue_or_codec_release',
                  'latch_is_batch_timestamp_not_per_buffer_operation_completion',
                  'guest_present_fence_branch_unverified_and_not_physical_panel_photons',
                  'silent_queue_markers_do_not_prove_decoder_inactivity_or_complete_trace',
                  'first_new_sf_receipt_may_contain_stale_or_prequeued_media',
                  'dequeue_identity_may_be_parser_backfilled',
                  'missing_present_tail_may_be_censored_or_unobserved',
                  'no_media_pts_nearest_timestamp_or_codec_identity_join',
                  'player_implementation_unknown_no_force_release_conclusion',
                  'no_phone_network_display_or_audio_video_timing_claim']}
    if rejects:
        return report
    supported = model['model_supported_host_interval_ns']
    supported = [max(supported[0], measurement[0]), min(supported[1], measurement[1])]
    report['analysis_supported_host_interval_ns'] = supported
    for row in actions:
        if not contained(bracket(row), supported) or not contained(bracket(row, 'media_state_host'), supported):
            rejects.append('action_outside_supported_clock_coverage')
            break
    if any(not contained(bracket(row), supported) for row in windows):
        rejects.append('steady_window_outside_supported_clock_coverage')
    if rejects:
        report['status'] = 'rejected_evidence'
        return report
    queues = sorted((row for row in data['events'] if row['event_type'] == 2), key=lambda row: (row['ts_ns'], row['slice_id']))
    chain_by_identity = {identity(row): row for row in data['numeric_identity_chains']}
    steady_rows = {}
    for window in sorted(windows, key=lambda row: row['window_index']):
        summary, selected = steady_summary(window, data['numeric_identity_chains'], queues, model, steady_min_frames)
        report['steady_windows'].append(summary)
        steady_rows[window['window_index']] = selected
    for index in range(len(actions) // 2):
        pause, resume = actions[2 * index:2 * index + 2]
        cycle = {'cycle_index': index, 'pause_action_index': pause['action_index'],
                 'resume_action_index': resume['action_index'], 'status': 'rejected',
                 'reject_codes': [], 'pause': None, 'first': None, 'comparison': None}
        if (pause['media_state_before'], pause['media_state_after'],
                resume['media_state_before'], resume['media_state_after']) != (3, 2, 2, 3):
            cycle['reject_codes'].append('pause_resume_media_state_not_confirmed')
        else:
            cycle['pause'] = pause_summary(pause, resume, queues, model, min_silence_ns)
            if not cycle['pause']['tail_without_new_queue_requirement_met_given_model']:
                cycle['reject_codes'].append('pause_queue_silence_requirement_not_met')
            next_pause = actions[2 * index + 2] if 2 * index + 2 < len(actions) else None
            cycle['first'] = first_summary(resume, next_pause, queues, chain_by_identity, model, supported)
            cycle['reject_codes'].extend(cycle['first']['reject_codes'])
            if cycle['reject_codes']:
                cycle['comparison'] = {'status': 'rejected', 'reject_codes': ['pause_or_first_not_accepted']}
            else:
                cycle['comparison'] = comparison(cycle['first'], steady_rows.get(index, []), steady_min_frames)
                cycle['reject_codes'].extend(cycle['comparison']['reject_codes'])
            if not cycle['reject_codes']:
                cycle['status'] = 'accepted_given_clock_model'
        report['cycles'].append(cycle)
    report['accepted_cycle_count'] = sum(row['status'] == 'accepted_given_clock_model' for row in report['cycles'])
    return report


def read_json(path):
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError('bounded_metadata_input_required')
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--graphics', type=Path, required=True)
    parser.add_argument('--fences', type=Path, required=True)
    parser.add_argument('--actions', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--steady-min-frames', type=int, default=10)
    args = parser.parse_args()
    if args.output.exists() or not 1 <= args.steady_min_frames <= 500:
        parser.error('fresh output and steady minimum1..500 required')
    try:
        report = summarize(read_json(args.graphics), read_json(args.fences), read_json(args.actions),
                           steady_min_frames=args.steady_min_frames)
        report['input_sha256'] = {name: hashlib.sha256(path.read_bytes()).hexdigest()
                                  for name, path in [('graphics', args.graphics), ('fences', args.fences), ('actions', args.actions)]}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as output:
            json.dump(report, output, indent=2, allow_nan=False)
            output.write('\n')
    except (OSError, UnicodeError, ValueError, KeyError, TypeError):
        raise SystemExit('bounded_resume_frame_analysis_failed') from None
    print(json.dumps({key: report.get(key) for key in ('status', 'reject_codes', 'action_pair_count', 'accepted_cycle_count')}))
    return 0 if report['status'] == 'analyzed_given_clock_model' else 2


if __name__ == '__main__':
    raise SystemExit(main())
