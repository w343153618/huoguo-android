import json
import pathlib
import stat
import tempfile
import threading
import unittest

from hardware_stream import BoundedCaptureTrace, capture_trace_clock_ns


class CaptureTraceTest(unittest.TestCase):
    def records(self, path):
        return [json.loads(line) for line in path.read_text().splitlines()]

    def test_private_exclusive_file_and_explicit_host_clock(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / 'capture.jsonl'
            before = capture_trace_clock_ns()
            trace = BoundedCaptureTrace(path)
            trace.emit('capture_enqueue', capture_seq=7, source_pts_us=1700000000000000,
                       grpc_return_ns=capture_trace_clock_ns(), enqueue_ns=capture_trace_clock_ns())
            trace.close()
            after = capture_trace_clock_ns()
            rows = self.records(path)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            clocks = [row for row in rows if row['event'] == 'trace_clock']
            self.assertEqual([row['phase'] for row in clocks], ['start', 'end'])
            for row in clocks:
                self.assertLessEqual(before, row['clock_before_ns'])
                self.assertLessEqual(row['clock_before_ns'], row['clock_after_ns'])
                self.assertLessEqual(row['clock_after_ns'], after)
                self.assertEqual(row['clock_domain'], 'host_clock_gettime_CLOCK_MONOTONIC_ns')
                self.assertIn('not_guest_media_pts', row['source_pts_scope'])
            frame = next(row for row in rows if row['event'] == 'capture_enqueue')
            self.assertEqual(frame['capture_seq'], 7)
            self.assertEqual(frame['source_pts_us'], 1700000000000000)
            self.assertEqual(rows[-1]['event'], 'trace_summary')
            self.assertTrue(rows[-1]['clean_close'])
            with self.assertRaises(FileExistsError):
                BoundedCaptureTrace(path)

    def test_existing_symlink_target_is_never_followed(self):
        with tempfile.TemporaryDirectory() as folder:
            target = pathlib.Path(folder) / 'original'
            target.write_text('preserve')
            link = pathlib.Path(folder) / 'trace'
            link.symlink_to(target)
            with self.assertRaises(OSError):
                BoundedCaptureTrace(link)
            self.assertEqual(target.read_text(), 'preserve')

    def test_record_and_byte_caps_are_visible_without_unbounded_output(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / 'record-cap.jsonl'
            trace = BoundedCaptureTrace(path, max_records=1)
            for number in range(20):
                trace.emit('raw_submit', capture_seq=number, source_pts_us=number)
            trace.close()
            rows = self.records(path)
            self.assertEqual(rows[-1]['accepted_records'], 1)
            self.assertGreaterEqual(rows[-1]['dropped_records'], 20)
            self.assertEqual(len(rows), 2)

            path = pathlib.Path(folder) / 'byte-cap.jsonl'
            trace = BoundedCaptureTrace(path, max_records=100, max_bytes=4096)
            for number in range(80):
                trace.emit('raw_submit', capture_seq=number, source_pts_us=number,
                           description='test_bound_' * 10)
            trace.close()
            self.assertLessEqual(path.stat().st_size, 4096)
            self.assertTrue(self.records(path)[-1]['byte_capped'])

    def test_blocked_trace_writer_drops_metadata_without_blocking_media(self):
        class PausedTrace(BoundedCaptureTrace):
            def __init__(self, path):
                self.entered, self.release = threading.Event(), threading.Event()
                super().__init__(path, pending_limit=1)

            def _line(self, record):
                self.entered.set()
                self.release.wait(timeout=2)
                super()._line(record)

        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / 'slow-writer.jsonl'
            trace = PausedTrace(path)
            self.assertTrue(trace.entered.wait(timeout=1))
            trace.emit('raw_submit', capture_seq=1)
            # The metadata queue is now full and the writer is still stopped.
            for number in range(100):
                trace.emit('raw_submit', capture_seq=number + 2)
            self.assertEqual(trace.pending.qsize(), 1)
            self.assertEqual(trace.dropped, 100)
            trace.release.set()
            trace.close()
            self.assertGreaterEqual(self.records(path)[-1]['dropped_records'], 100)

    def test_trace_rejects_pixel_buffers_and_oversized_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / 'metadata-only.jsonl'
            trace = BoundedCaptureTrace(path)
            trace.emit('bad', pixels=b'private_raw_pixels')
            trace.emit('bad', text='x' * 193)
            trace.emit('bad', **{str(number): number for number in range(33)})
            trace.close()
            self.assertNotIn('private_raw_pixels', path.read_text())
            self.assertFalse(any(row['event'] == 'bad' for row in self.records(path)))
            self.assertEqual(self.records(path)[-1]['dropped_records'], 3)

    def test_unbounded_queue_configuration_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / 'capture.jsonl'
            for kwargs in ({'pending_limit': 0}, {'max_records': 24001}, {'max_bytes': 4095}):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    BoundedCaptureTrace(path, **kwargs)
            self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
