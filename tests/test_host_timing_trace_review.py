"""Independent fault/lifecycle review; owned files and clocks, no media or devices."""
import ast
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import hardware_stream
from hardware_stream import BoundedCaptureTrace
from udp_lan_worker import LanMediaWorker
from tests import test_udp_lan_worker as worker_fixture


def actual_raw_trace_cleanup():
    """Execute only the actual nested worker's final trace cleanup block."""
    tree = ast.parse(Path(hardware_stream.__file__).read_text())
    worker = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'worker')
    blocks = [n for n in ast.walk(worker)
              if isinstance(n, ast.Try) and n.finalbody]
    final = next(n.finalbody for n in blocks
                 if any(isinstance(x, ast.If)
                        and ast.unparse(x.test) == 'trace is not None'
                        for x in n.finalbody))
    observed = next(x for x in final
                    if isinstance(x, ast.If) and ast.unparse(x.test) == 'trace is not None')
    harness = ast.parse('def cleanup():\n    try:\n        raise RuntimeError("owned_media_fault")\n    finally:\n        pass\n')
    harness.body[0].body[0].finalbody = [copy.deepcopy(observed)]
    return compile(ast.fix_missing_locations(harness), '<actual-raw-trace-cleanup>', 'exec')


class IndependentTraceReviewChecks(unittest.TestCase):
    def test_writer_thread_start_failure_closes_its_owned_descriptor(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'owned.jsonl'
            with patch('hardware_stream.threading.Thread.start',
                       side_effect=RuntimeError('owned_start_fault')), \
                    patch('hardware_stream.os.close', wraps=os.close) as close:
                with self.assertRaisesRegex(RuntimeError, '^owned_start_fault$'):
                    BoundedCaptureTrace(path)
                self.assertEqual(close.call_count, 1)
                fd = close.call_args.args[0]
                with self.assertRaises(OSError):
                    os.fstat(fd)

    def test_initial_clock_fault_does_not_leave_an_unowned_writer_or_fd(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'owned.jsonl'
            trace = None
            try:
                with patch.object(BoundedCaptureTrace, 'clock_sample',
                                  side_effect=OSError('private clock detail')):
                    trace = BoundedCaptureTrace(path)
                trace.emit('owned_numeric', value=1)
                trace.close()
                self.assertFalse(trace.writer.is_alive())
                rows = [json.loads(line) for line in path.read_text().splitlines()]
                self.assertGreaterEqual(rows[-1]['clock_errors'], 1)
                self.assertNotIn('private clock detail', path.read_text())
            finally:
                if trace is not None:
                    trace.close()

    def test_final_clock_fault_still_finishes_sink_with_numeric_fault_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'owned.jsonl'
            trace = BoundedCaptureTrace(path)
            try:
                trace.emit('owned_numeric', value=1)
                with patch.object(trace, 'clock_sample',
                                  side_effect=OSError('private closing detail')):
                    trace.close()
                self.assertTrue(trace.closed)
                self.assertFalse(trace.writer.is_alive())
                rows = [json.loads(line) for line in path.read_text().splitlines()]
                self.assertEqual(rows[-1]['event'], 'trace_summary')
                self.assertGreaterEqual(rows[-1]['clock_errors'], 1)
                self.assertNotIn('private closing detail', path.read_text())
            finally:
                trace.close()

    def test_actual_raw_cleanup_preserves_primary_media_failure_when_close_raises(self):
        class FailedClose:
            def close(self): raise OSError('private closing detail')
        diagnostic = SimpleNamespace(emit_errors=0, summary=lambda value: None)
        namespace = dict(trace=FailedClose(), raw_diagnostics=diagnostic, threads=[])
        exec(actual_raw_trace_cleanup(), namespace)
        with self.assertRaisesRegex(RuntimeError, '^owned_media_fault$'):
            namespace['cleanup']()
        self.assertGreaterEqual(diagnostic.emit_errors, 1)

    def test_actual_raw_cleanup_default_off_keeps_primary_failure_without_sink_calls(self):
        namespace = dict(trace=None, raw_diagnostics=None, threads=[])
        exec(actual_raw_trace_cleanup(), namespace)
        with self.assertRaisesRegex(RuntimeError, '^owned_media_fault$'):
            namespace['cleanup']()

    def test_failed_socket_constructor_preserves_media_failure_if_trace_close_also_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker_fixture.ConstructorChecks().fixture(root)
            sock = worker_fixture.FakeSocket()
            def failed_bind(address):
                raise OSError('owned_socket_bind_fault')
            sock.bind = failed_bind
            with patch('udp_lan_worker.private_trace_attempt', return_value=root / 'private'), \
                    patch('udp_lan_worker.BoundedCaptureTrace') as sink, \
                    patch('udp_lan_worker.socket.socket', return_value=sock), \
                    patch('udp_lan_worker.socket.if_nametoindex', return_value=7):
                sink.return_value.close.side_effect = RuntimeError('private_trace_close_fault')
                with self.assertRaisesRegex(OSError, '^owned_socket_bind_fault$'):
                    LanMediaWorker(worker_fixture.config(), '192.168.9.149', '192.168.9.128', 'en7',
                                   root, '/fake/packetizer', '/fake/encoder',
                                   SimpleNamespace(), root / 'evidence', lambda: False,
                                   capture_trace_dir=root / 'diagnostics')
                self.assertEqual(sock.closed, 1)
                sink.return_value.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
