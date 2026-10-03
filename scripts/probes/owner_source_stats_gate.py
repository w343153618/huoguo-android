"""Fresh trusted collector call; descriptor endpoints are not continuous video proof.

This is independent of owner-source-gate-v1's format-unknown contract. It does
not load external observation JSON, acquire a lease, resume a player or start
media. Those operations belong to the protected experiment coordinator.
"""
import re
import time
import math

try:
    from scripts.probes import source_stats_observation as source
except ModuleNotFoundError:
    import source_stats_observation as source

SCHEMA = 'owner-source-stats-gate-v1'
CLOCK = 'host_clock_gettime_CLOCK_MONOTONIC_ns'
MAX_COLLECT_NS = 15_000_000_000
MAX_AGE_NS = 30_000_000_000


class Rejected(ValueError):
    pass


def clock_ns():
    return time.clock_gettime_ns(time.CLOCK_MONOTONIC)


def _require(condition, reason):
    if not condition:
        raise Rejected(reason)


def _stamp(value):
    _require(type(value) is int and value > 0, 'clock_invalid')
    return value


def _identity(value):
    _require(type(value) is dict and set(value) == {'pid', 'uid', 'start_ticks'}, 'identity_invalid')
    for key, upper in (('pid', 2147483647), ('uid', 2147483647), ('start_ticks', 9223372036854775807)):
        _require(type(value[key]) is int and (0 if key == 'uid' else 1) <= value[key] <= upper,
                 'identity_invalid')
    return dict(value)


def _descriptor(value, video):
    keys = {'itag', 'codec', 'width', 'height', 'descriptor_fps'} if video else {'itag', 'codec'}
    _require(type(value) is dict and set(value) == keys, 'Stats_descriptor_invalid')
    _require(type(value['itag']) is int and 0 <= value['itag'] <= 999999,
             'Stats_descriptor_invalid')
    codec = value['codec']
    pattern = source.stats.CODEC if video else r'(?:opus|aac|mp4a(?:\.[0-9.]{1,48})?)'
    _require(type(codec) is str and re.fullmatch(pattern, codec) is not None,
             'Stats_descriptor_invalid')
    if video:
        for name in ('width', 'height'):
            _require(type(value[name]) is int and 16 <= value[name] <= 8192, 'Stats_descriptor_invalid')
        fps = value['descriptor_fps']
        _require(type(fps) in (int, float) and math.isfinite(fps) and 1 <= fps <= 240,
                 'Stats_descriptor_invalid')
    return dict(value)


def collect_fresh(adb, serial, expected_identity, expected_video_id, *, required_state,
                  listener_ready_ns=None, collector=source.collect, native_clock=clock_ns):
    """Call the in-process collector, verify its bracket, retain distinct clocks."""
    expected = _identity(expected_identity)
    _require(isinstance(expected_video_id, str) and source.stats.VIDEO_ID.fullmatch(expected_video_id)
             is not None, 'video_id_invalid')
    _require(required_state in ('paused', 'playing'), 'state_invalid')
    _require(type(serial) is str and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', serial) is not None,
             'selector_invalid')
    _require(required_state != 'playing' or listener_ready_ns is not None, 'listener_required')
    begin = _stamp(native_clock())
    if listener_ready_ns is not None:
        _require(begin >= _stamp(listener_ready_ns), 'listener_not_ready')
    try:
        value = collector(adb, serial, expected_video_id, required_state=required_state, timeout=15)
    except Exception:
        raise Rejected('collector_failed') from None
    end = _stamp(native_clock())
    _require(0 <= end - begin <= MAX_COLLECT_NS, 'collection_budget')
    _require(type(value) is dict and value.get('schema') == source.SCHEMA
             and value.get('qualified') is True and value.get('required_state') == required_state,
             'collector_not_qualified')
    for flag in ('identity_stable', 'session_owner_matches_bracket', 'state_matches_bracket',
                 'remote_uia_completion_receipt', 'all_local_children_reaped',
                 'device_UI_temp_removal_confirmed'):
        _require(value.get(flag) is True, 'collector_incomplete')
    for label in ('identity_before', 'identity_after'):
        ident = value.get(label)
        _require(type(ident) is dict and ident.get('known') is True
                 and all(type(ident.get(k)) is int and ident[k] == v for k, v in expected.items()),
                 'source_identity_changed')
        _require(ident.get('command_ok') is True and ident.get('child_reaped') is True,
                 'collector_incomplete')
    for label in ('focus_before', 'focus_after'):
        focus = value.get(label)
        _require(type(focus) is dict and focus.get('foreground') is True
                 and focus.get('command_ok') is True and focus.get('child_reaped') is True,
                 'collector_incomplete')
    desired = 2 if required_state == 'paused' else 3
    for label in ('playback_before', 'playback_after'):
        state = value.get(label)
        _require(type(state) is dict and state.get('unknown') is False and state.get('state') == desired
                 and state.get('command_ok') is True and state.get('child_reaped') is True
                 and (desired != 3 or state.get('speed') == 1), 'collector_incomplete')
        _require(state.get('position_ms') is None or type(state['position_ms']) is int
                 and 0 <= state['position_ms'] <= 9223372036854775807, 'collector_incomplete')
    stats = value.get('stats')
    _require(type(stats) is dict and stats.get('format_known') is True
             and stats.get('video_id') == expected_video_id
             and stats.get('observed_content_fps') is None
             and stats.get('decoded_or_presented_fps_verified') is False,
             'Stats_descriptor_invalid')
    video_format = _descriptor(stats.get('video_format'), True)
    audio_format = _descriptor(stats.get('audio_format'), False)
    position = stats.get('position_ui_seconds')
    _require(position is None or type(position) is int and 0 <= position < 360000,
             'Stats_descriptor_invalid')
    dropped = stats.get('dropped_frames_cumulative')
    _require(dropped is None or type(dropped) is dict and set(dropped) == {'dropped', 'total'}
             and all(type(dropped[k]) is int for k in dropped)
             and 0 <= dropped['dropped'] <= dropped['total'] < 10**15, 'Stats_descriptor_invalid')
    start_python = _stamp(value.get('host_started_python_monotonic_ns'))
    end_python = _stamp(value.get('host_finished_python_monotonic_ns'))
    _require(0 <= end_python - start_python <= MAX_COLLECT_NS, 'collector_clock_invalid')
    return {'schema': SCHEMA, 'evidence_kind': 'fresh_player_Stats_UI_descriptor',
            'native_clock_domain': CLOCK, 'native_started_ns': begin, 'native_finished_ns': end,
            'collector_clock_domain': 'python_time_monotonic_ns',
            'collector_started_ns': start_python, 'collector_finished_ns': end_python,
            'listener_ready_ns': listener_ready_ns, 'identity': expected,
            'required_state': required_state, 'video_id': expected_video_id,
            'video_format': video_format, 'audio_format': audio_format,
            'position_ui_seconds': position,
            'position_before_ms': value['playback_before']['position_ms'],
            'position_after_ms': value['playback_after']['position_ms'],
            'dropped_frames_cumulative': dict(dropped) if dropped is not None else None,
            'process_session_focus_bracket_verified': True,
            'visual_motion_observed': None, 'decoded_or_presented_fps': None,
            'continuous_content_coverage_verified': False,
            'collection_or_overlay_overhead_measured': False}


def _validate_gate(gate):
    _require(type(gate) is dict and gate.get('schema') == SCHEMA
             and gate.get('native_clock_domain') == CLOCK, 'gate_clock_invalid')
    begin, end = _stamp(gate.get('native_started_ns')), _stamp(gate.get('native_finished_ns'))
    _require(0 <= end - begin <= MAX_COLLECT_NS, 'gate_clock_invalid')
    _identity(gate.get('identity'))
    _require(type(gate.get('video_id')) is str and source.stats.VIDEO_ID.fullmatch(gate['video_id'])
             is not None, 'Stats_descriptor_invalid')
    _descriptor(gate.get('video_format'), True)
    _descriptor(gate.get('audio_format'), False)
    _require(gate.get('process_session_focus_bracket_verified') is True
             and gate.get('continuous_content_coverage_verified') is False
             and gate.get('visual_motion_observed') is None
             and gate.get('decoded_or_presented_fps') is None, 'gate_evidence_invalid')


def require_fresh(gate, now_native_ns):
    _validate_gate(gate)
    age = _stamp(now_native_ns) - _stamp(gate.get('native_finished_ns'))
    _require(0 <= age <= MAX_AGE_NS, 'gate_stale')
    return age


def endpoint_match(before, after, *, window_started_ns, window_finished_ns):
    """Endpoints surround a window; a video changed and changed back is unknown."""
    start, end = _stamp(window_started_ns), _stamp(window_finished_ns)
    _require(end >= start, 'window_invalid')
    require_fresh(before, start)
    _validate_gate(after)
    _require(end <= _stamp(after.get('native_started_ns')) <= end + MAX_AGE_NS,
             'post_observation_not_adjacent')
    matches = all(before[k] == after[k] for k in ('identity', 'video_id', 'video_format', 'audio_format'))
    progress = (after['position_after_ms'] - before['position_before_ms']
                if type(after.get('position_after_ms')) is int
                and type(before.get('position_before_ms')) is int else None)
    return {'endpoint_content_matches': matches, 'reported_position_progress_ms': progress,
            'continuous_content_coverage_verified': False, 'visual_motion_observed': None,
            'decoded_or_presented_fps': None}
