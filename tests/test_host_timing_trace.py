"""Owned clocks, files and pipes only; no listener, device or performance claim."""
import ast
from collections import deque
import io
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import hardware_stream
from hardware_stream import BoundedCaptureTrace, FrameRateBudget, take_raw_frame, raw_writer_trace_arguments
from host_timing_trace import FIELDS, HostTimingTrace, private_trace_attempt


class Sink:
    def __init__(self): self.rows = []
    def emit(self, event, **values): self.rows.append(dict(event=event, **values))


class FixtureClock:
    def __init__(self): self.seconds = 10.; self.budget_calls = 0; self.observation_calls = 0
    def monotonic(self): self.budget_calls += 1; return self.seconds
    def monotonic_ns(self): return round(self.seconds * 1e9)
    def time_ns(self): return 1_700_000_000_000_000_000 + self.monotonic_ns()
    def observed(self): self.observation_calls += 1; return self.monotonic_ns() + self.observation_calls


def actual_raw_loop():
    # Exercise the actual nested body without importing gRPC or invoking worker.
    tree = ast.parse(Path(hardware_stream.__file__).read_text())
    worker = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'worker')
    loop = next(node for node in ast.walk(worker) if isinstance(node, ast.FunctionDef) and node.name == 'video_input')
    return compile(ast.fix_missing_locations(ast.Module(body=[loop], type_ignores=[])), '<owned-raw-loop>', 'exec')


class TimingSchemaChecks(unittest.TestCase):
    def test_private_attempt_is_exclusive_owner_only_without_touching_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            sentinel = root / 'preserve'
            sentinel.write_text('owned fixture')
            first, second = private_trace_attempt(root), private_trace_attempt(root)
            self.assertNotEqual(first, second)
            self.assertEqual(stat.S_IMODE(first.stat().st_mode), 0o700)
            self.assertEqual(sentinel.read_text(), 'owned fixture')
            self.assertEqual(list(first.iterdir()), [])

    def test_path_requires_existing_owner_private_no_symlink_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            root.chmod(0o755)
            with self.assertRaisesRegex(ValueError, 'private_owner'):
                private_trace_attempt(root)
            root.chmod(0o700)
            link = root / 'link'; link.symlink_to(root, target_is_directory=True)
            for path in (link, link / 'child', root / 'missing'):
                with self.subTest(path=path), self.assertRaises((ValueError, FileNotFoundError)):
                    private_trace_attempt(path)
            with patch('host_timing_trace.os.geteuid', return_value=os.geteuid()+1):
                with self.assertRaisesRegex(ValueError, 'private_owner'):
                    private_trace_attempt(root)

    def test_explicit_optin_only_and_never_app_configuration(self):
        self.assertEqual(raw_writer_trace_arguments(False, None, None), [])
        self.assertEqual(raw_writer_trace_arguments(True, 'trace', 'encoder'), ['--raw-writer-diagnostics'])
        for enabled, trace, native in ((1, 'trace', 'encoder'), (True, None, 'encoder'), (True, 'trace', None)):
            with self.assertRaises(ValueError): raw_writer_trace_arguments(enabled, trace, native)

    def test_whitelist_rejects_payload_strings_extra_fields_nonfinite_and_missing_fields(self):
        sink = Sink(); diagnostic = HostTimingTrace(sink, lambda: 123)
        good = dict.fromkeys(FIELDS['feed_cancel'], 0)
        diagnostic.emit('feed_cancel', **good)
        diagnostic.emit('feed_cancel', **{**good, 'payload': 7})
        diagnostic.emit('feed_cancel', **{**good, 'record_bytes': 'secret'})
        diagnostic.emit('feed_cancel', **{**good, 'record_bytes': float('nan')})
        diagnostic.emit('unknown', value=123)
        diagnostic.emit('feed_cancel', at_ns=123)
        self.assertEqual(diagnostic.schema_errors, 5)
        self.assertEqual(len(sink.rows), 2)
        self.assertTrue(all(len(fields) <= 32 for fields in FIELDS.values()))

    def test_failed_clock_and_sink_count_errors_without_raising(self):
        sink = Mock(); sink.emit.side_effect = OSError('detail never exported')
        diagnostic = HostTimingTrace(sink, lambda: (_ for _ in ()).throw(OSError('clock detail')))
        self.assertEqual(diagnostic.stamp(), 0)
        diagnostic.summary(False)
        self.assertEqual(diagnostic.clock_errors, 1)
        self.assertEqual(diagnostic.emit_errors, 2)
        self.assertEqual(diagnostic.schema_errors, 0)

    def test_fixed_numeric_records_use_existing_bounded_async_sink(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'trace.jsonl'
            sink = BoundedCaptureTrace(path, max_records=6, max_bytes=4096, pending_limit=2)
            diagnostic = HostTimingTrace(sink, lambda: 123)
            for _ in range(40): diagnostic.emit('feed_cancel', **dict.fromkeys(FIELDS['feed_cancel'], 0))
            diagnostic.summary(True); sink.close(); sink.close()
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(rows[-1]['event'], 'trace_summary')
            self.assertGreater(rows[-1]['dropped_records'], 0)
            self.assertLessEqual(path.stat().st_size, 4096)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertFalse(sink.writer.is_alive())


class ActualRawWriterChecks(unittest.TestCase):
    def run_loop(self, *, enabled, frames=2, flushes=2, queue_policy='fifo',
                 repeat_pts=False, controls=(), ready=True, on_condition=None,
                 budget_cancel=False, write_failure=False, flush_failure=False, sink_failure=False,
                 continuous=False):
        clock = FixtureClock(); sink = Sink()
        if sink_failure: sink.emit = Mock(side_effect=OSError('sink failure'))
        diagnostic = HostTimingTrace(sink, clock.observed) if enabled else None
        queue = deque(maxlen=2)
        counters = dict(raw_frames=0, frames_submitted=0, pending_frames_replaced=0, idle_repeats=0)
        def enqueue(pts, seq):
            queue.append((2, 2, pts, b'X'*16, clock.monotonic_ns(), seq)); counters['raw_frames'] += 1
        for n in range(frames): enqueue(100 if repeat_pts else 100+n, n+1)
        class Stop:
            value = False
            def is_set(self): return self.value
            def wait(self, delay):
                clock.seconds += delay + .005
                if budget_cancel: self.value = True
        stop = Stop()
        class Condition:
            calls = 0
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def wait(self, timeout):
                self.calls += 1; clock.seconds += timeout
                if on_condition: on_condition(self.calls, enqueue, stop, clock)
                elif not queue: stop.value = True
        condition = Condition()
        class Writer:
            writes = 0; flush_count = 0; events = []
            def write(self, value):
                self.writes += 1
                if write_failure and self.writes == 2: raise OSError('owned raw write failure')
                self.events.append(('write', bytes(value)))
                clock.seconds += .002
                return len(value)
            def flush(self):
                self.flush_count += 1; self.events.append(('flush', None)); clock.seconds += .001
                if flush_failure: raise OSError('owned raw flush failure')
                if continuous and self.flush_count < flushes:
                    enqueue(101+self.flush_count, 2+self.flush_count)
                if self.flush_count >= flushes: stop.value = True
        writer = Writer()
        # Supplying the actual FrameRateBudget with an explicit fake monotonic
        # clock verifies diagnostics does not refresh/consume tokens again.
        def budget(fps): return FrameRateBudget(fps, clock.monotonic)
        namespace = dict(FrameRateBudget=budget, raw_submit_budget={'effective_raw_submit_fps': 30},
            stop=stop, condition=condition, latest_frames=queue, controls=deque(controls, maxlen=8),
            native_ready=SimpleNamespace(is_set=lambda: ready), native=SimpleNamespace(stdin=writer),
            take_raw_frame=take_raw_frame, args=SimpleNamespace(raw_queue_policy=queue_policy),
            counters=counters, trace=None, raw_diagnostics=diagnostic, capture_trace_clock_ns=clock.observed,
            time=clock, timing=lambda *args: None, struct=struct)
        # New explicit diagnostic stamps exist even when the older trace is off
        # in this extracted fixture. Runtime requires both, tested separately.
        exec(actual_raw_loop(), namespace)
        error = None
        try: namespace['video_input']()
        except OSError as caught: error = str(caught)
        return SimpleNamespace(clock=clock, sink=sink, diagnostic=diagnostic, counters=counters,
            writer=writer, condition=condition, controls=namespace['controls'], error=error)

    def rows(self, result, event): return [row for row in result.sink.rows if row['event'] == event]

    def test_off_has_no_observation_calls_and_on_preserves_exact_bytes_flushes_and_budget_calls(self):
        off, on = self.run_loop(enabled=False), self.run_loop(enabled=True)
        self.assertEqual(off.clock.observation_calls, 0)
        self.assertEqual(off.sink.rows, [])
        self.assertEqual(off.writer.events, on.writer.events)
        self.assertEqual(off.counters, on.counters)
        self.assertEqual(off.clock.budget_calls, on.clock.budget_calls)
        self.assertEqual(off.clock.seconds, on.clock.seconds)
        self.assertEqual([r['outcome'] for r in self.rows(on, 'raw_loop')], [1, 1])
        self.assertEqual(self.rows(on, 'raw_loop')[1]['prior_end_ns'], self.rows(on, 'raw_loop')[0]['end_ns'])
        self.assertEqual(self.rows(on, 'raw_loop')[1]['prior_write_end_ns'], self.rows(on, 'raw_write')[0]['write_end_ns'])

    def test_actual_budget_wait_records_overshoot_without_new_budget_clock_calls(self):
        off = self.run_loop(enabled=False, flushes=3, continuous=True)
        on = self.run_loop(enabled=True, flushes=3, continuous=True)
        self.assertEqual(off.clock.budget_calls, on.clock.budget_calls)
        self.assertEqual(off.writer.events, on.writer.events)
        row = self.rows(on, 'raw_budget')[-1]
        self.assertGreater(row['requested_wait_ns'], 0)
        self.assertGreater(row['wait_end_ns']-row['wait_begin_ns'], row['requested_wait_ns'])
        self.assertTrue(row['consumed'])

    def test_condition_wait_spurious_and_controls_before_ready_remain_queued(self):
        def wake(call, enqueue, stop, clock):
            if call == 2: enqueue(123, 1)
        result = self.run_loop(enabled=True, frames=0, flushes=2, controls=((9,1,4000000),),
                               ready=False, on_condition=wake)
        loops = self.rows(result, 'raw_loop')
        self.assertEqual([row['outcome'] for row in loops], [2, 1])
        self.assertTrue(all(row['condition_waited'] for row in loops))
        self.assertTrue(all(row['condition_wait_end_ns'] >= row['condition_wait_begin_ns'] for row in loops))
        self.assertEqual(list(result.controls), [(9,1,4000000)])
        self.assertEqual(loops[0]['control_count'], 0)
        self.assertGreater(self.rows(result, 'raw_write')[0]['idle_flush_end_ns'], 0)

    def test_control_write_count_duration_do_not_export_values_or_flush_independently(self):
        result = self.run_loop(enabled=True, controls=((9,1,4000000),(10,2,0)))
        row = self.rows(result, 'raw_loop')[0]
        self.assertEqual(row['control_count'], 2)
        self.assertGreater(row['control_end_ns'], row['control_begin_ns'])
        self.assertEqual([event[0] for event in result.writer.events[:5]], ['write','write','write','write','flush'])
        self.assertNotIn(4000000, [value for row in result.sink.rows for value in row.values()])
        self.assertTrue(all(not {'sequence', 'kind', 'value'} & row.keys() for row in result.sink.rows))

    def test_cancel_after_condition_or_budget_records_terminal_without_raw_submission(self):
        result = self.run_loop(enabled=True, frames=0)
        self.assertEqual(self.rows(result, 'raw_loop')[0]['outcome'], 4)
        self.assertEqual(result.writer.events, [])
        result = self.run_loop(enabled=True, flushes=3, continuous=True, budget_cancel=True)
        self.assertEqual(self.rows(result, 'raw_loop')[-1]['outcome'], 4)
        self.assertEqual(result.counters['frames_submitted'], 2)
        self.assertFalse(self.rows(result, 'raw_budget')[-1]['consumed'])

    def test_nonincreasing_pts_is_rejected_before_consume(self):
        result = self.run_loop(enabled=True, repeat_pts=True)
        self.assertEqual([r['outcome'] for r in self.rows(result, 'raw_loop')], [1,3,4])
        self.assertFalse(self.rows(result, 'raw_budget')[1]['consumed'])
        self.assertEqual(result.counters['frames_submitted'], 1)

    def test_latest_skip_and_idle_repeat_are_separate_from_fresh_capture(self):
        result = self.run_loop(enabled=True, queue_policy='latest', flushes=1)
        self.assertEqual(self.rows(result, 'raw_loop')[0]['skipped'], 1)
        self.assertEqual(result.counters['pending_frames_replaced'], 1)
        def idle(call, enqueue, stop, clock): clock.seconds += 1.1
        result = self.run_loop(enabled=True, frames=1, flushes=2, on_condition=idle)
        write = self.rows(result, 'raw_write')[1]
        self.assertTrue(write['idle_repeat'])
        self.assertEqual(write['capture_seq'], 0)
        self.assertEqual(result.counters['raw_frames'], 1)
        self.assertEqual(result.counters['idle_repeats'], 1)

    def test_writer_failure_and_sink_failure_do_not_increment_submission_or_replace_error(self):
        for option in ('write_failure', 'flush_failure'):
            for sink_failure in (False, True):
                with self.subTest(option=option, sink_failure=sink_failure):
                    result = self.run_loop(enabled=True, sink_failure=sink_failure, **{option: True})
                    self.assertIn('owned raw', result.error)
                    self.assertEqual(result.counters['frames_submitted'], 0)
                    if not sink_failure:
                        self.assertEqual(self.rows(result, 'raw_loop')[0]['outcome'], 6)
                        self.assertFalse(self.rows(result, 'raw_write')[0]['flush_complete'])
                    else: self.assertGreater(result.diagnostic.emit_errors, 0)


if __name__ == '__main__': unittest.main()
