"""Opt-in numeric source admission; no UI, media, path scan or device writes.

The trusted collector owns its bounded ADB reads and is called exactly once
with timeout=15. The wrapper rejects an overrun after it returns; it does not
invent a thread timeout that would leave a collector or child behind. Native
CLOCK_MONOTONIC brackets never subtract the collector's Python clock values.
Trace ownership, inode pinning and complete-prefix reads belong to the caller.
"""
import json
import math
import re
import time

from scripts.probes import source_decoder_observation as observation
from scripts.probes.host_timing_analysis import sanitize_host

NATIVE_CLOCK = 'host_clock_gettime_CLOCK_MONOTONIC_ns'
COLLECTOR_CLOCK = 'python_time_monotonic_ns'
SOURCE_SCHEMA = 'owner-source-gate-v1'
MAX_COLLECT_NS = 15_000_000_000
MAX_FRESH_NS = 30_000_000_000
MAX_INT = (1 << 63) - 1
MAX_PREFIX_BYTES = 64 * 1024
MAX_PREFIX_ROWS = 256
MAX_LINE_BYTES = 8192
IDENTITY = frozenset(('pid', 'uid', 'start_ticks'))
COMMAND = frozenset(('command_ok', 'error_code', 'raw_bytes',
    'host_started_monotonic_ns', 'host_finished_monotonic_ns', 'child_reaped'))
PLAYBACK = frozenset(('schema', 'unknown', 'matching_sessions', 'active_sessions',
    'state_known', 'state', 'position_ms', 'updated_elapsed_ms', 'speed',
    'media_fps_known', 'media_fps'))
STRUCTURE = frozenset(('raw_available', 'utf8_valid', 'line_count',
    'historical_queue_only', 'installed_schema_validated', 'codec_identity_known', 'live_format_known'))
FORMAT_NULL = frozenset(('width', 'height', 'mime_enum', 'configured_fps',
    'content_fps', 'itag', 'source_bitrate'))
FORMAT_FALSE = frozenset(('live_format_known', 'configured_fps_known',
    'content_fps_known', 'itag_known', 'source_bitrate_known'))
OBSERVATION_KEYS = frozenset(('schema', 'identity_before', 'identity_after',
    'playback_before', 'playback_after', 'identity_stable',
    'session_owner_matches_before', 'session_owner_matches_after',
    'playing_state_bracket', 'reported_position_changed', 'metrics', 'codec',
    'raw_total_bytes', 'all_children_reaped', 'host_started_monotonic_ns',
    'host_finished_monotonic_ns')) | FORMAT_NULL | FORMAT_FALSE
GATE_KEYS = frozenset(('schema', 'evidence_kind', 'source_clock_domain',
    'collector_clock_domain', 'listener_ready_ns', 'source_started_monotonic_ns',
    'source_finished_monotonic_ns', 'collector_started_python_monotonic_ns',
    'collector_finished_python_monotonic_ns', 'pid', 'uid', 'start_ticks',
    'state_before', 'state_after', 'speed_before', 'speed_after',
    'position_before_ms', 'position_after_ms', 'updated_before_elapsed_ms',
    'updated_after_elapsed_ms', 'reported_position_changed', 'raw_total_bytes',
    'all_children_reaped', 'visual_motion_observed')) | FORMAT_NULL | FORMAT_FALSE
SOURCE_GATE_LABELS = frozenset((
    'source_expected_identity_invalid', 'source_listener_invalid',
    'source_clock_failed', 'source_collect_bracket_invalid', 'source_collect_timeout',
    'source_collector_failed', 'source_observation_schema_invalid',
    'source_read_failed', 'source_raw_bounds_invalid', 'source_children_unreaped',
    'source_identity_mismatch', 'source_playback_not_playing', 'source_owner_mismatch',
    'source_gate_invalid', 'source_launch_future', 'source_launch_stale',
    'source_trace_bounds', 'source_trace_malformed', 'source_trace_clock_invalid',
    'source_trace_predates_observation', 'source_trace_duplicate_contradiction',
    'source_first_capture_missing', 'source_first_capture_ambiguous',
    'source_first_capture_invalid',
    'source_first_capture_future', 'source_first_capture_stale',
    'source_first_capture_order_invalid'))


class SourceGateError(Exception):
    def __init__(self, label):
        self.label = label if type(label) is str and label in SOURCE_GATE_LABELS else 'source_gate_invalid'
        super().__init__(self.label)


def native_clock_ns():
    return time.clock_gettime_ns(time.CLOCK_MONOTONIC)


def _require(ok, label):
    if not ok:
        raise SourceGateError(label)


def _integer(value, low=0, high=MAX_INT):
    return type(value) is int and low <= value <= high


def _identity(value):
    return (type(value) is dict and set(value) == IDENTITY
        and _integer(value['pid'], 1, (1 << 31) - 1)
        and _integer(value['uid'], 0, (1 << 31) - 1)
        and _integer(value['start_ticks'], 1))


def _unknown_format(value, label):
    _require(all(value.get(key) is None for key in FORMAT_NULL)
        and all(value.get(key) is False for key in FORMAT_FALSE), label)


def _stamp(clock):
    try:
        value = clock()
    except Exception:
        raise SourceGateError('source_clock_failed') from None
    _require(_integer(value, 1), 'source_clock_failed')
    return value


def _validate_observation(value, expected):
    label = 'source_observation_schema_invalid'
    _require(type(value) is dict and set(value) == OBSERVATION_KEYS, label)
    _require(type(value['schema']) is int and value['schema'] == 1, label)
    _unknown_format(value, label)
    _require(value['all_children_reaped'] is True, 'source_children_unreaped')
    _require(value['identity_stable'] is True, 'source_identity_mismatch')
    _require(value['session_owner_matches_before'] is True
        and value['session_owner_matches_after'] is True, 'source_owner_mismatch')
    _require(value['playing_state_bracket'] is True, 'source_playback_not_playing')
    started, finished = value['host_started_monotonic_ns'], value['host_finished_monotonic_ns']
    _require(_integer(started, 1) and _integer(finished, started), label)
    _require(finished - started <= MAX_COLLECT_NS, 'source_collect_timeout')
    commands = (value['identity_before'], value['playback_before'], value['metrics'],
        value['codec'], value['playback_after'], value['identity_after'])
    total, previous = 0, started
    for index, command in enumerate(commands):
        extra = (IDENTITY | {'known'}) if index in (0, 5) else PLAYBACK if index in (1, 4) else STRUCTURE
        _require(type(command) is dict and set(command) == COMMAND | extra, label)
        _require(command['child_reaped'] is True, 'source_children_unreaped')
        _require(command['command_ok'] is True and type(command['error_code']) is int
            and command['error_code'] == 0, 'source_read_failed')
        begin, end = command['host_started_monotonic_ns'], command['host_finished_monotonic_ns']
        _require(_integer(begin, previous, finished) and _integer(end, begin, finished), label)
        previous = end
        bound = observation.IDENTITY_BYTES if index in (0, 5) else observation.MAX_DUMP_BYTES
        _require(_integer(command['raw_bytes'], 0, bound - 1), 'source_raw_bounds_invalid')
        total += command['raw_bytes']
        if index in (0, 5):
            identity = {key: command[key] for key in IDENTITY}
            _require(command['known'] is True and _identity(identity) and identity == expected,
                'source_identity_mismatch')
        elif index in (1, 4):
            _require(type(command['schema']) is int and command['schema'] == 1
                and command['unknown'] is False and command['state_known'] is True
                and _integer(command['matching_sessions'], 1, (1 << 31) - 1)
                and type(command['active_sessions']) is int and command['active_sessions'] == 1,
                'source_playback_not_playing')
            _require(type(command['state']) is int and command['state'] == 3
                and type(command['speed']) in (int, float)
                and command['speed'] == 1, 'source_playback_not_playing')
            _require(_integer(command['position_ms'], -1)
                and _integer(command['updated_elapsed_ms'])
                and command['media_fps_known'] is False and command['media_fps'] is None, label)
        else:
            _require(type(command['utf8_valid']) is bool
                and command['raw_available'] is (command['raw_bytes'] > 0)
                and command['historical_queue_only'] is (index == 2)
                and command['installed_schema_validated'] is False
                and command['codec_identity_known'] is False
                and command['live_format_known'] is False, label)
            _require((command['utf8_valid'] and _integer(command['line_count'], 0, bound))
                or (not command['utf8_valid'] and command['line_count'] is None), label)
    _require(_integer(value['raw_total_bytes'], 0, observation.MAX_TOTAL_BYTES - 1)
        and value['raw_total_bytes'] == total, 'source_raw_bounds_invalid')
    positions = (value['playback_before']['position_ms'], value['playback_after']['position_ms'])
    changed = positions[0] != positions[1] if min(positions) >= 0 else None
    _require(value['reported_position_changed'] is changed, label)


def collect_playing_unknown(adb, serial, listener_ready_ns, expected_identity, *,
                            collector=observation.collect, clock_ns=native_clock_ns):
    """One trusted <=15s collector call; numeric playing is not decoded motion."""
    _require(_identity(expected_identity), 'source_expected_identity_invalid')
    expected = dict(expected_identity)
    _require(_integer(listener_ready_ns, 1), 'source_listener_invalid')
    _require(type(serial) is str and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', serial) is not None,
        'source_observation_schema_invalid')
    begin = _stamp(clock_ns)
    _require(begin >= listener_ready_ns, 'source_collect_bracket_invalid')
    try:
        value = collector(adb, serial, timeout=15)
    except Exception:
        raise SourceGateError('source_collector_failed') from None
    end = _stamp(clock_ns)
    _require(end >= begin, 'source_collect_bracket_invalid')
    _require(end - begin <= MAX_COLLECT_NS, 'source_collect_timeout')
    _validate_observation(value, expected)
    before, after = value['playback_before'], value['playback_after']
    gate = dict(schema=SOURCE_SCHEMA, evidence_kind='numeric_playing_format_unknown',
        source_clock_domain=NATIVE_CLOCK, collector_clock_domain=COLLECTOR_CLOCK,
        listener_ready_ns=listener_ready_ns, source_started_monotonic_ns=begin,
        source_finished_monotonic_ns=end,
        collector_started_python_monotonic_ns=value['host_started_monotonic_ns'],
        collector_finished_python_monotonic_ns=value['host_finished_monotonic_ns'],
        **expected, state_before=before['state'], state_after=after['state'],
        speed_before=before['speed'], speed_after=after['speed'],
        position_before_ms=before['position_ms'], position_after_ms=after['position_ms'],
        updated_before_elapsed_ms=before['updated_elapsed_ms'],
        updated_after_elapsed_ms=after['updated_elapsed_ms'],
        reported_position_changed=value['reported_position_changed'],
        raw_total_bytes=value['raw_total_bytes'], all_children_reaped=True,
        visual_motion_observed=None)
    gate.update({key: None for key in FORMAT_NULL})
    gate.update({key: False for key in FORMAT_FALSE})
    return _validate_gate(gate)


def _validate_gate(gate):
    label = 'source_gate_invalid'
    _require(type(gate) is dict and set(gate) == GATE_KEYS, label)
    _require(gate['schema'] == SOURCE_SCHEMA and gate['evidence_kind'] == 'numeric_playing_format_unknown'
        and gate['source_clock_domain'] == NATIVE_CLOCK
        and gate['collector_clock_domain'] == COLLECTOR_CLOCK, label)
    _require(_identity({key: gate[key] for key in IDENTITY}), label)
    listener, begin, end = (gate[key] for key in ('listener_ready_ns',
        'source_started_monotonic_ns', 'source_finished_monotonic_ns'))
    _require(_integer(listener, 1) and _integer(begin, listener) and _integer(end, begin)
        and end - begin <= MAX_COLLECT_NS, label)
    py_begin, py_end = gate['collector_started_python_monotonic_ns'], gate['collector_finished_python_monotonic_ns']
    _require(_integer(py_begin, 1) and _integer(py_end, py_begin)
        and py_end - py_begin <= MAX_COLLECT_NS, label)
    for suffix in ('before', 'after'):
        _require(type(gate['state_' + suffix]) is int and gate['state_' + suffix] == 3
            and type(gate['speed_' + suffix]) in (int, float)
            and gate['speed_' + suffix] == 1
            and _integer(gate['position_' + suffix + '_ms'], -1)
            and _integer(gate['updated_' + suffix + '_elapsed_ms']), label)
    positions = (gate['position_before_ms'], gate['position_after_ms'])
    changed = positions[0] != positions[1] if min(positions) >= 0 else None
    _require(gate['reported_position_changed'] is changed and gate['all_children_reaped'] is True
        and gate['visual_motion_observed'] is None
        and _integer(gate['raw_total_bytes'], 0, observation.MAX_TOTAL_BYTES - 1), label)
    _unknown_format(gate, label)
    return dict(gate)


def validate_launch_fresh(gate, launch_ns):
    clean = _validate_gate(gate)
    _require(_integer(launch_ns, 1), 'source_launch_future')
    _require(launch_ns >= clean['source_finished_monotonic_ns'], 'source_launch_future')
    _require(launch_ns - clean['source_started_monotonic_ns'] <= MAX_FRESH_NS, 'source_launch_stale')
    return clean


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SourceGateError('source_trace_malformed')
        result[key] = value
    return result


def _flat_row(row):
    _require(type(row) is dict and len(row) <= 64, 'source_trace_malformed')
    for key, value in row.items():
        _require(type(key) is str and len(key) <= 64, 'source_trace_malformed')
        _require(type(value) in (int, float, bool, str), 'source_trace_malformed')
        if type(value) is str:
            _require(len(value) <= 192, 'source_trace_bounds')
        elif type(value) is int:
            _require(-MAX_INT <= value <= MAX_INT, 'source_trace_malformed')
        elif type(value) is float:
            _require(math.isfinite(value) and abs(value) <= MAX_INT, 'source_trace_malformed')


def _prefix_rows(prefix):
    if type(prefix) is bytes:
        _require(len(prefix) <= MAX_PREFIX_BYTES, 'source_trace_bounds')
        _require(not prefix or prefix.endswith(b'\n'), 'source_trace_malformed')
        lines = prefix.splitlines()
        _require(len(lines) <= MAX_PREFIX_ROWS, 'source_trace_bounds')
        rows = []
        for line in lines:
            _require(0 < len(line) <= MAX_LINE_BYTES, 'source_trace_bounds')
            try:
                rows.append(json.loads(line.decode('utf-8'), object_pairs_hook=_object,
                    parse_constant=lambda _: (_ for _ in ()).throw(SourceGateError('source_trace_malformed'))))
            except (ValueError, UnicodeError, RecursionError):
                raise SourceGateError('source_trace_malformed') from None
        return rows
    _require(type(prefix) in (list, tuple) and len(prefix) <= MAX_PREFIX_ROWS, 'source_trace_bounds')
    rows = []
    total = 0
    for row in prefix:
        _flat_row(row)
        try:
            encoded = json.dumps(row, allow_nan=False, separators=(',', ':')).encode()
        except (ValueError, TypeError, RecursionError):
            raise SourceGateError('source_trace_malformed') from None
        _require(len(encoded) <= MAX_LINE_BYTES, 'source_trace_bounds')
        total += len(encoded) + 1
        _require(total <= MAX_PREFIX_BYTES, 'source_trace_bounds')
        rows.append(row)
    return rows


def qualify_first_capture(gate, prefix, observed_ns, *, clock_domain):
    """Qualify only this complete bounded prefix, never files or historical data.

    Only source_first_capture_missing is retryable before the caller's deadline.
    Returned metadata is numeric/fixed-label and is not phone FPS or latency.
    """
    clean = _validate_gate(gate)
    _require(clock_domain == NATIVE_CLOCK, 'source_trace_clock_invalid')
    _require(_integer(observed_ns, clean['source_finished_monotonic_ns']), 'source_first_capture_future')
    _require(observed_ns - clean['source_started_monotonic_ns'] <= MAX_FRESH_NS, 'source_first_capture_stale')
    rows = _prefix_rows(prefix)
    _require(not rows or type(rows[0]) is dict and rows[0].get('event') == 'trace_clock'
        and rows[0].get('phase') == 'start', 'source_trace_clock_invalid')
    clocks, captures, previous_seq, last_enqueue = {}, {}, 0, 0
    first = None
    for index, row in enumerate(rows):
        _flat_row(row)
        if type(row) is dict and row.get('event') in ('trace_clock', 'host_timing_contract'):
            _require(row.get('clock_domain') == NATIVE_CLOCK, 'source_trace_clock_invalid')
        record = sanitize_host(row, 'capture')
        _require(record is not None, 'source_trace_malformed')
        event = record['event']
        if event == 'trace_clock':
            phase = record['phase']
            _require(_integer(record['clock_before_ns'], 1)
                and record['clock_before_ns'] <= record['clock_after_ns'] <= observed_ns,
                'source_trace_clock_invalid')
            if phase in clocks:
                _require(record == clocks[phase], 'source_trace_duplicate_contradiction')
            else:
                _require(phase != 'end' or 'start' in clocks, 'source_trace_clock_invalid')
                if phase == 'start':
                    _require(index == 0, 'source_trace_clock_invalid')
                    _require(record['clock_before_ns'] >= clean['source_finished_monotonic_ns'],
                        'source_trace_predates_observation')
                else:
                    _require(record['clock_before_ns'] >= max(clocks['start']['clock_after_ns'], last_enqueue),
                        'source_trace_clock_invalid')
                clocks[phase] = record
        elif event == 'capture_enqueue':
            _require('start' in clocks and 'end' not in clocks, 'source_trace_clock_invalid')
            _require(record['source_pts_us'] > 0 and record['width'] >= 2 and record['height'] >= 2
                and record['width'] % 2 == 0 and record['height'] % 2 == 0
                and record['raw_bytes'] == record['width'] * record['height'] * 4
                and record['pending_count'] >= 1
                and record['replaced_capture_seq'] < record['capture_seq'],
                'source_first_capture_invalid')
            seq = record['capture_seq']
            if seq in captures:
                _require(record == captures[seq], 'source_trace_duplicate_contradiction')
                continue
            _require(seq > previous_seq, 'source_first_capture_order_invalid')
            previous_seq = seq
            captures[seq] = record
            grpc, enqueue = record['grpc_return_ns'], record['enqueue_ns']
            _require(_integer(grpc, 1) and _integer(enqueue, grpc), 'source_first_capture_order_invalid')
            _require(grpc >= clocks['start']['clock_after_ns'], 'source_trace_predates_observation')
            _require(grpc >= last_enqueue, 'source_first_capture_order_invalid')
            _require(enqueue <= observed_ns, 'source_first_capture_future')
            _require(enqueue - clean['source_started_monotonic_ns'] <= MAX_FRESH_NS,
                'source_first_capture_stale')
            last_enqueue = enqueue
            if first is None:
                _require(seq == 1, 'source_first_capture_ambiguous')
                first = record
    _require(first is not None, 'source_first_capture_missing')
    return dict(schema=1, evidence_kind='numeric_first_capture_clock_qualified',
        clock_domain=NATIVE_CLOCK, source_stamp_ns=clean['source_started_monotonic_ns'],
        capture_seq=first['capture_seq'], source_pts_us=first['source_pts_us'],
        grpc_return_ns=first['grpc_return_ns'], enqueue_ns=first['enqueue_ns'],
        observed_ns=observed_ns, source_to_first_grpc_ns=first['grpc_return_ns']-clean['source_started_monotonic_ns'],
        source_to_first_enqueue_ns=first['enqueue_ns']-clean['source_started_monotonic_ns'],
        prefix_rows=len(rows))
