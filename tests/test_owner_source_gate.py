"""Offline admission/clock contracts, never phone FPS, playback or live ADB."""
import copy
import json
import unittest
from unittest import mock

from scripts.probes import owner_source_gate as gate

PRIVATE = 'SYNTHETIC_PRIVATE_DO_NOT_EXPORT'
NATIVE = 100_000_000_000
PYTHON = 900_000_000_000
EXPECTED = {'pid': 4001, 'uid': 10235, 'start_ticks': 450001}


def identity():
    p, u, start = (EXPECTED[key] for key in ('pid', 'uid', 'start_ticks'))
    package = gate.observation.playback.TARGET_PACKAGE.encode()
    status = f'Name:\t{PRIVATE}\nTgid:\t{p}\nPid:\t{p}\nUid:\t{u}\t{u}\t{u}\t{u}\n'.encode()
    suffix = b'S ' + b' '.join([b'0'] * 18) + b' ' + str(start).encode()
    return (str(p).encode() + b'\n' + package + b'\0' + gate.observation.STATUS_MARKER
        + status + gate.observation.STAT_MARKER + str(p).encode() + b' (fixture) ' + suffix + b'\n')


def playback(position=0):
    return (f'  fixture\n    ownerPid={EXPECTED["pid"]}, ownerUid={EXPECTED["uid"]}, userId=0\n'
        f'    package={gate.observation.playback.TARGET_PACKAGE}\n    active=true\n'
        f'    state=PlaybackState {{state=3, position={position}, buffered position=0, '
        f'speed=1.0, updated=3000, actions=1}}\n    metadata: {PRIVATE}\n').encode()


class Reader:
    def __init__(self, position=0):
        self.started_ns, self.total_bytes, self.unreaped = PYTHON, 0, False
        self.values = [identity(), playback(), b'', b'', playback(position), identity()]
        self.buffers, self.calls = [], []

    def read(self, args, byte_limit=gate.observation.MAX_DUMP_BYTES):
        index = len(self.calls)
        self.calls.append((args, byte_limit))
        raw = bytearray(self.values[index])
        self.buffers.append(raw)
        self.total_bytes += len(raw)
        return raw, dict(gate.observation.command_info(), command_ok=True,
            raw_bytes=len(raw), host_started_monotonic_ns=PYTHON + index * 100 + 1,
            host_finished_monotonic_ns=PYTHON + index * 100 + 2)


def observation(position=0):
    reader = Reader(position)
    with mock.patch.object(gate.observation.time, 'monotonic_ns', return_value=PYTHON + 1000):
        value = gate.observation.collect('/never-execute', 'fixture', reader_factory=lambda *_: reader)
    assert all(not raw for raw in reader.buffers)
    return value


def clock_row(before=NATIVE + 1_000_000_000, phase='start'):
    return dict(schema='capture-vt-trace-v1', process='python', event='trace_clock',
        phase=phase, clock_domain=gate.NATIVE_CLOCK, clock_before_ns=before,
        clock_after_ns=before + 1, unix_ns=1_800_000_000_000_000_000,
        source_pts_scope='emulator_estimated_screenshot_generation_unix_us_not_guest_media_pts')


def capture(seq=1, at=NATIVE + 2_000_000_000):
    return dict(schema='capture-vt-trace-v1', process='python', event='capture_enqueue',
        capture_seq=seq, source_pts_us=1_800_000_000_000_000 + seq,
        screenshot_seq=19, grpc_return_ns=at, enqueue_ns=at + 1,
        width=720, height=1280, raw_bytes=720 * 1280 * 4,
        pending_count=1, replaced_capture_seq=0)


class OwnerSourceGateTests(unittest.TestCase):
    def collect(self, value=None, stamps=None, expected=None, listener=None, serial='fixture'):
        collector = mock.Mock(return_value=observation() if value is None else value)
        clock = mock.Mock(side_effect=stamps or [NATIVE, NATIVE + 1_000_000_000])
        result = gate.collect_playing_unknown('/'+PRIVATE, serial,
            NATIVE - 1 if listener is None else listener,
            EXPECTED if expected is None else expected, collector=collector, clock_ns=clock)
        return result, collector, clock

    def rejection(self, label, call):
        with self.assertRaises(gate.SourceGateError) as error:
            call()
        self.assertEqual(str(error.exception), label)
        self.assertEqual(error.exception.args, (label,))
        self.assertIn(error.exception.label, gate.SOURCE_GATE_LABELS)
        self.assertNotIn(PRIVATE, repr(error.exception))

    def reject_observation(self, change, label):
        value = observation()
        change(value)
        self.rejection(label, lambda: self.collect(value))

    def qualify(self, rows=None, result=None, now=None, domain=None):
        return gate.qualify_first_capture(self.collect()[0] if result is None else result,
            [clock_row(), capture()] if rows is None else rows,
            NATIVE + 3_000_000_000 if now is None else now,
            clock_domain=gate.NATIVE_CLOCK if domain is None else domain)

    def test_unchanged_playing_position_is_allowed_but_motion_and_format_stay_unknown(self):
        result, _, _ = self.collect()
        self.assertFalse(result['reported_position_changed'])
        self.assertIsNone(result['visual_motion_observed'])
        self.assertEqual(set(result), gate.GATE_KEYS)
        for key in gate.FORMAT_NULL:self.assertIsNone(result[key])
        for key in gate.FORMAT_FALSE:self.assertIs(result[key], False)
        self.assertNotIn(PRIVATE, json.dumps(result))

    def test_changed_position_does_not_invent_visual_motion(self):
        result, _, _ = self.collect(observation(500))
        self.assertTrue(result['reported_position_changed'])
        self.assertIsNone(result['visual_motion_observed'])

    def test_unknown_position_has_unknown_change(self):
        value = observation()
        for name in ('playback_before', 'playback_after'):value[name]['position_ms'] = -1
        value['reported_position_changed'] = None
        self.assertIsNone(self.collect(value)[0]['reported_position_changed'])

    def test_collector_is_called_once_with_fifteen_second_shared_deadline(self):
        _, collector, clock = self.collect()
        collector.assert_called_once_with('/'+PRIVATE, 'fixture', timeout=15)
        self.assertEqual(clock.call_count, 2)

    def test_native_and_python_clocks_are_separate_even_with_large_epoch_difference(self):
        result, _, _ = self.collect()
        self.assertEqual(result['source_clock_domain'], gate.NATIVE_CLOCK)
        self.assertEqual(result['collector_clock_domain'], gate.COLLECTOR_CLOCK)
        self.assertEqual(result['source_started_monotonic_ns'], NATIVE)
        self.assertEqual(result['collector_started_python_monotonic_ns'], PYTHON)
        gate.validate_launch_fresh(result, NATIVE + 2_000_000_000)

    def test_expected_identity_closed_keys_and_bounded_ints(self):
        for value in ({}, dict(EXPECTED, extra=1), dict(EXPECTED, pid=True),
            dict(EXPECTED, uid=-1), dict(EXPECTED, pid=1 << 31), dict(EXPECTED, start_ticks=1 << 63)):
            with self.subTest(value=value):
                self.rejection('source_expected_identity_invalid', lambda: self.collect(expected=value))

    def test_listener_and_serial_fail_before_collector(self):
        collector = mock.Mock()
        for listener in (True, 0, -1, 1 << 63):
            self.rejection('source_listener_invalid', lambda: gate.collect_playing_unknown('unused',
                'fixture', listener, EXPECTED, collector=collector))
        for serial in ('x'*129, 'device/other', True):
            self.rejection('source_observation_schema_invalid', lambda: gate.collect_playing_unknown(
                'unused', serial, NATIVE, EXPECTED, collector=collector))
        collector.assert_not_called()

    def test_source_bracket_must_follow_this_listener(self):
        collector = mock.Mock()
        self.rejection('source_collect_bracket_invalid', lambda: gate.collect_playing_unknown('unused',
            'fixture', NATIVE + 1, EXPECTED, collector=collector, clock_ns=lambda: NATIVE))
        collector.assert_not_called()

    def test_native_clock_error_and_noninteger_are_fixed_labels(self):
        for value in (True, 0, -1, 1 << 63, float('nan')):
            self.rejection('source_clock_failed', lambda: self.collect(stamps=[value]))
        self.rejection('source_clock_failed', lambda: gate.collect_playing_unknown('unused', 'fixture',
            NATIVE, EXPECTED, clock_ns=mock.Mock(side_effect=OSError(PRIVATE))))

    def test_finish_clock_failure_and_reversal(self):
        self.rejection('source_clock_failed', lambda: self.collect(stamps=[NATIVE, OSError(PRIVATE)]))
        self.rejection('source_collect_bracket_invalid', lambda: self.collect(stamps=[NATIVE, NATIVE - 1]))

    def test_native_collection_timeout_includes_exact_boundary(self):
        self.collect(stamps=[NATIVE, NATIVE + gate.MAX_COLLECT_NS])
        self.rejection('source_collect_timeout', lambda: self.collect(stamps=[NATIVE, NATIVE + gate.MAX_COLLECT_NS + 1]))

    def test_python_collector_timeout_is_checked_in_its_own_domain(self):
        self.reject_observation(lambda value: value.update(host_finished_monotonic_ns=PYTHON + gate.MAX_COLLECT_NS + 1), 'source_collect_timeout')

    def test_collector_exception_has_no_text_and_no_retry(self):
        collector = mock.Mock(side_effect=RuntimeError(PRIVATE))
        self.rejection('source_collector_failed', lambda: gate.collect_playing_unknown('unused', 'fixture',
            NATIVE, EXPECTED, collector=collector, clock_ns=lambda: NATIVE))
        collector.assert_called_once()

    def test_top_level_schema_is_closed(self):
        for change in (lambda v:v.update(extra=PRIVATE), lambda v:v.pop('codec'), lambda v:v.update(schema=True)):
            self.reject_observation(change, 'source_observation_schema_invalid')

    def test_nested_schema_is_closed(self):
        for name in ('identity_before', 'playback_after', 'codec'):
            self.reject_observation(lambda value:value[name].update(secret=PRIVATE), 'source_observation_schema_invalid')

    def test_identity_restart_and_incorrect_expected_process_are_rejected(self):
        self.reject_observation(lambda v:v['identity_after'].update(start_ticks=450002), 'source_identity_mismatch')
        self.reject_observation(lambda v:v['identity_before'].update(pid=4002), 'source_identity_mismatch')
        self.reject_observation(lambda v:v.update(identity_stable=False), 'source_identity_mismatch')

    def test_owner_mismatch_flags_do_not_pass_as_truthy_int(self):
        for name in ('session_owner_matches_before', 'session_owner_matches_after'):
            for bad in (False, 1):
                self.reject_observation(lambda v:v.update({name:bad}), 'source_owner_mismatch')

    def test_paused_unknown_or_duplicate_active_state_is_rejected(self):
        for name in ('playback_before', 'playback_after'):
            for fields in ({'state':2}, {'unknown':True}, {'active_sessions':2}, {'state_known':1}):
                self.reject_observation(lambda v:v[name].update(fields), 'source_playback_not_playing')
        self.reject_observation(lambda v:v.update(playing_state_bracket=False), 'source_playback_not_playing')

    def test_speed_nan_infinite_boolean_and_wrong_speed_rejected(self):
        for value in (float('nan'), float('inf'), True, 0, 2, 10**1000):
            self.reject_observation(lambda v:v['playback_after'].update(speed=value), 'source_playback_not_playing')

    def test_any_read_timeout_or_failed_command_blocks_source_admission(self):
        for name in ('identity_before', 'playback_before', 'metrics', 'codec', 'playback_after', 'identity_after'):
            self.reject_observation(lambda v:v[name].update(command_ok=False, error_code=2), 'source_read_failed')

    def test_unreaped_children_are_rejected_at_both_levels(self):
        self.reject_observation(lambda v:v.update(all_children_reaped=False), 'source_children_unreaped')
        self.reject_observation(lambda v:v['identity_after'].update(child_reaped=False), 'source_children_unreaped')

    def test_raw_bounds_actual_sum_and_bool_values_rejected(self):
        for name, bound in (('identity_before', gate.observation.IDENTITY_BYTES), ('codec', gate.observation.MAX_DUMP_BYTES)):
            self.reject_observation(lambda v:v[name].update(raw_bytes=bound), 'source_raw_bounds_invalid')
        self.reject_observation(lambda v:v.update(raw_total_bytes=True), 'source_raw_bounds_invalid')
        self.reject_observation(lambda v:v.update(raw_total_bytes=v['raw_total_bytes']+1), 'source_raw_bounds_invalid')

    def test_command_python_brackets_cannot_reverse_overlap_or_escape(self):
        for fields in ({'host_started_monotonic_ns':PYTHON-1},
            {'host_finished_monotonic_ns':PYTHON}, {'host_finished_monotonic_ns':PYTHON+1001}):
            self.reject_observation(lambda v:v['playback_before'].update(fields), 'source_observation_schema_invalid')

    def test_unknown_format_and_position_claims_cannot_be_forged(self):
        for change in (lambda v:v.update(content_fps=60), lambda v:v.update(live_format_known=True),
            lambda v:v['playback_after'].update(media_fps_known=True), lambda v:v.update(reported_position_changed=True)):
            self.reject_observation(change, 'source_observation_schema_invalid')

    def test_launch_thirty_second_boundary_and_future_guard(self):
        value = self.collect()[0]
        gate.validate_launch_fresh(value, NATIVE + gate.MAX_FRESH_NS)
        self.rejection('source_launch_stale', lambda:gate.validate_launch_fresh(value, NATIVE + gate.MAX_FRESH_NS + 1))
        for stamp in (NATIVE, True, -1, 1 << 63):
            self.rejection('source_launch_future', lambda:gate.validate_launch_fresh(value, stamp))

    def test_gate_is_revalidated_and_returns_an_independent_copy(self):
        value = self.collect()[0]
        self.assertIsNot(gate.validate_launch_fresh(value, NATIVE+2_000_000_000), value)
        for fields in ({'extra':PRIVATE}, {'visual_motion_observed':True}, {'content_fps':60},
            {'speed_before':float('nan')}, {'speed_after':10**1000}):
            forged = dict(value, **fields)
            self.rejection('source_gate_invalid', lambda:gate.validate_launch_fresh(forged, NATIVE+2_000_000_000))

    def test_error_constructor_never_exports_an_unknown_label(self):
        self.assertEqual(str(gate.SourceGateError(PRIVATE)), 'source_gate_invalid')
        self.assertNotIn(PRIVATE, repr(gate.SourceGateError(PRIVATE)))

    def test_first_capture_accepts_typed_rows_and_complete_jsonl_only(self):
        rows = [clock_row(), capture()]
        result = self.qualify(rows)
        encoded = b''.join(json.dumps(row).encode()+b'\n' for row in rows)
        self.assertEqual(result, self.qualify(encoded))
        self.assertNotIn('rows', result)
        self.assertEqual(result['source_to_first_enqueue_ns'], 2_000_000_001)

    def test_missing_capture_is_the_only_retryable_label(self):
        for prefix in (b'', [], [clock_row()]):
            self.rejection('source_first_capture_missing', lambda:self.qualify(prefix))

    def test_malformed_utf8_json_partial_lines_duplicate_keys_and_nan_rejected(self):
        for prefix in (b'{}', b'\xff\n', b'{invalid}\n', b'{"event":1,"event":2}\n', b'{"value":NaN}\n'):
            self.rejection('source_trace_malformed', lambda:self.qualify(prefix))

    def test_prefix_bounds_cover_bytes_lines_rows_and_nested_or_large_typed_values(self):
        self.rejection('source_trace_bounds', lambda:self.qualify(b'x'*(gate.MAX_PREFIX_BYTES+1)))
        self.rejection('source_trace_bounds', lambda:self.qualify([clock_row()]*257))
        self.rejection('source_trace_bounds', lambda:self.qualify(b'x'*(gate.MAX_LINE_BYTES+1)+b'\n'))
        self.rejection('source_trace_bounds', lambda:self.qualify([dict(clock_row(), extra='x'*193)]))
        self.rejection('source_trace_malformed', lambda:self.qualify([dict(clock_row(), extra=[PRIVATE])]))

    def test_explicit_and_recorded_clock_domains_both_must_match(self):
        self.rejection('source_trace_clock_invalid', lambda:self.qualify(domain='host_clock_gettime_CLOCK_UPTIME_RAW_us'))
        for row in (dict(clock_row(), clock_domain='python_time_monotonic_ns'), dict(clock_row(), phase='end')):
            self.rejection('source_trace_clock_invalid', lambda:self.qualify([row, capture()]))

    def test_nonempty_prefix_cannot_start_without_clock_header(self):
        self.rejection('source_trace_clock_invalid', lambda:self.qualify([capture()]))
        self.rejection('source_trace_clock_invalid', lambda:self.qualify([dict(clock_row(), event='raw_submit')]))

    def test_clock_header_future_reversal_and_previous_attempt_rejected(self):
        self.rejection('source_trace_clock_invalid', lambda:self.qualify([clock_row(NATIVE+4_000_000_000),capture()]))
        self.rejection('source_trace_clock_invalid', lambda:self.qualify([dict(clock_row(),clock_after_ns=NATIVE),capture()]))
        self.rejection('source_trace_predates_observation', lambda:self.qualify([clock_row(NATIVE),capture()]))

    def test_identical_header_duplicate_allowed_but_conflicting_duplicate_rejected(self):
        self.qualify([clock_row(), clock_row(), capture()])
        self.rejection('source_trace_duplicate_contradiction', lambda:self.qualify([clock_row(),clock_row(NATIVE+1_000_000_010),capture()]))

    def test_identical_capture_duplicate_allowed_but_conflicting_duplicate_rejected(self):
        self.qualify([clock_row(),capture(),capture()])
        self.rejection('source_trace_duplicate_contradiction', lambda:self.qualify([clock_row(),capture(),dict(capture(),source_pts_us=9)]))

    def test_lost_initial_capture_is_ambiguous_not_a_later_frame_success(self):
        self.rejection('source_first_capture_ambiguous', lambda:self.qualify([clock_row(),capture(2)]))
        self.rejection('source_first_capture_order_invalid', lambda:self.qualify([clock_row(),capture(),capture(3),capture(2)]))

    def test_capture_future_reversal_zero_and_preheader_stamps_rejected(self):
        self.rejection('source_first_capture_future', lambda:self.qualify([clock_row(),capture(at=NATIVE+4_000_000_000)]))
        self.rejection('source_first_capture_order_invalid', lambda:self.qualify([clock_row(),dict(capture(),enqueue_ns=NATIVE)]))
        self.rejection('source_first_capture_order_invalid', lambda:self.qualify([clock_row(),dict(capture(),grpc_return_ns=0)]))
        self.rejection('source_trace_predates_observation', lambda:self.qualify([clock_row(),capture(at=NATIVE+1_000_000_000)]))

    def test_first_capture_thirty_second_boundary_and_observation_deadline(self):
        at = NATIVE + gate.MAX_FRESH_NS
        self.qualify([clock_row(),dict(capture(at=at),enqueue_ns=at)], now=at)
        self.rejection('source_first_capture_stale', lambda:self.qualify(now=at+1))

    def test_first_age_begins_at_earliest_source_stamp_not_collection_finish_or_unix_pts(self):
        result = self.collect(stamps=[NATIVE,NATIVE+15_000_000_000])[0]
        row = dict(capture(at=NATIVE+29_000_000_000), source_pts_us=gate.MAX_INT)
        value = self.qualify([clock_row(NATIVE+15_000_000_000), row], result=result, now=NATIVE+30_000_000_000)
        self.assertEqual(value['source_to_first_grpc_ns'],29_000_000_000)
        self.assertEqual(value['source_pts_us'],gate.MAX_INT)

    def test_complete_capture_geometry_and_queue_identity_required(self):
        for fields in ({'source_pts_us':0}, {'width':0}, {'width':719}, {'height':0},
            {'raw_bytes':1}, {'pending_count':0}, {'replaced_capture_seq':1}):
            self.rejection('source_first_capture_invalid', lambda:self.qualify([clock_row(),dict(capture(),**fields)]))

    def test_capture_schema_bool_float_extra_text_and_wrong_role_rejected(self):
        for fields in ({'grpc_return_ns':True}, {'enqueue_ns':1.0}, {'secret':PRIVATE}, {'process':'swift'}):
            self.rejection('source_trace_malformed', lambda:self.qualify([clock_row(),dict(capture(),**fields)]))

    def test_capture_after_end_header_cannot_extend_the_attempt(self):
        self.rejection('source_trace_clock_invalid', lambda:self.qualify([clock_row(),clock_row(NATIVE+2_000_000_000,phase='end'),capture()]))

    def test_end_header_covers_preceding_capture_and_accepts_exact_boundary(self):
        self.rejection('source_trace_clock_invalid', lambda:self.qualify([clock_row(),capture(),clock_row(NATIVE+1_500_000_000,phase='end')]))
        self.qualify([clock_row(),capture(),clock_row(capture()['enqueue_ns'],phase='end')])

    def test_increasing_capture_sequence_cannot_hide_reversed_native_timestamps(self):
        self.rejection('source_first_capture_order_invalid', lambda:self.qualify([clock_row(),capture(),capture(2,NATIVE+1_500_000_000)]))


if __name__ == '__main__':
    unittest.main()
