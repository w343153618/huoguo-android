import csv
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / 'scripts/probes/guest_perfetto_probe.py'
SPEC = importlib.util.spec_from_file_location('guest_perfetto_probe', PATH)
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


def result_csv(codec_name):
    blocks = []
    for header, name in PROBE.HEADERS.items():
        out = io.StringIO()
        writer = csv.writer(out, lineterminator='\n')
        writer.writerow(header)
        if name == 'codec_stages':
            writer.writerow([1, 1000000, 1000, 3, 4, 5, codec_name])
        blocks.append(out.getvalue().strip())
    return ('\n\n'.join(blocks) + '\n').encode()


class GuestPerfettoProbeChecks(unittest.TestCase):
    def test_android_indented_atrace_categories_are_detected(self):
        raw = b'       gfx - Graphics\n    video - Video\n      view - View System\n secret - Private\n'
        self.assertEqual(PROBE.parse_categories(raw), ['gfx', 'view', 'video'])
        self.assertEqual(PROBE.parse_categories(b'unknown - Unknown\n'), [])

    def test_configuration_is_bounded_and_excludes_unneeded_data(self):
        config = PROBE.make_config(35, {'sources': list(PROBE.ALLOWED_SOURCES),
                                       'events': list(PROBE.ALLOWED_EVENTS),
                                       'categories': ['video', 'gfx', 'view', 'private_category']}).decode()
        self.assertIn('duration_ms: 35000', config)
        self.assertIn('max_file_size_bytes: 67108864', config)
        self.assertIn('atrace_categories: "video"', config)
        for absent in ('android.log', 'screenrecord', 'atrace_apps: "*"', 'private_category'):
            self.assertNotIn(absent, config)
        for seconds in (0, 29.99, 45.01, 100):
            with self.assertRaises(PROBE.ProbeError):
                PROBE.make_config(seconds, {'sources': [], 'events': [], 'categories': []})

    def test_cleanup_checks_both_process_and_owned_output_path(self):
        path = '/data/misc/perfetto-traces/huoguo-guest-123.pftrace'
        self.assertTrue(PROBE.owned_perfetto_cmdline(b'/system/bin/perfetto\0-o\0' + path.encode() + b'\0', path))
        self.assertFalse(PROBE.owned_perfetto_cmdline(b'/system/bin/another\0-o\0' + path.encode() + b'\0', path))
        self.assertFalse(PROBE.owned_perfetto_cmdline(b'/system/bin/perfetto\0-o\0other-trace\0', path))

    def test_surface_buffer_trace_is_opt_in_and_requires_both_sources(self):
        available = {'sources': list(PROBE.ALLOWED_SOURCES), 'events': list(PROBE.ALLOWED_EVENTS),
                     'categories': ['video']}
        normal = PROBE.make_config(30, available).decode()
        self.assertNotIn('surfaceflinger_layers_config', normal)
        active = PROBE.make_config(30, available, True).decode()
        self.assertIn('max_file_size_bytes: 134217728', active)
        self.assertIn('max_file_size_bytes: 67108864', normal)
        self.assertIn('surfaceflinger_layers_config { mode: MODE_ACTIVE', active)
        self.assertIn('surfaceflinger_transactions_config { mode: MODE_ACTIVE', active)
        self.assertIn('TRACE_FLAG_BUFFERS', active)
        for bad in ['android.surfaceflinger.layers', 'android.surfaceflinger.transactions']:
            limited = dict(available, sources=[x for x in available['sources'] if x != bad])
            with self.assertRaises(PROBE.ProbeError):
                PROBE.make_config(30, limited, True)

    def test_graphics_frame_trace_requires_registered_source_and_is_opt_in(self):
        available = {'sources': list(PROBE.ALLOWED_SOURCES), 'events': list(PROBE.ALLOWED_EVENTS),
                     'categories': ['video']}
        normal = PROBE.make_config(30, available).decode()
        frames = PROBE.make_config(30, available, include_frames=True).decode()
        self.assertNotIn('name: "android.surfaceflinger.frame"', normal)
        self.assertIn('name: "android.surfaceflinger.frame"', frames)
        self.assertIn('max_file_size_bytes: 67108864', frames)
        self.assertNotIn('surfaceflinger_layers_config', frames)
        limited = dict(available, sources=[x for x in available['sources'] if x != 'android.surfaceflinger.frame'])
        with self.assertRaises(PROBE.ProbeError):
            PROBE.make_config(30, limited, include_frames=True)

    def test_partial_failure_retains_owned_guest_trace(self):
        path = '/data/misc/perfetto-traces/huoguo-guest-123.pftrace'
        with patch.object(PROBE, 'shell', side_effect=[b'42', b'perfetto\0-o\0'+path.encode()+b'\0', b'', b'']) as calls:
            result = PROBE.cleanup_owned('adb', 'emulator-5556', '/data/local/tmp/unique.pid', path,
                                         remove_trace=False)
        self.assertTrue(result['owned_guest_trace_retained'])
        self.assertIn('kill -TERM 42', calls.call_args_list[2].args[2])
        self.assertNotIn(path, calls.call_args_list[-1].args[2])

    def test_private_diagnostics_only_emit_fixed_flags(self):
        result = PROBE.classify_diagnostics(b'PRIVATE_LOG_VALUE Failed to write: No space left on device')
        self.assertTrue(result['storage_failure'])
        self.assertTrue(result['write_failure'])
        self.assertNotIn('PRIVATE_LOG', json.dumps(result))

    def test_raw_trace_must_be_in_private_tmp_and_owner_only(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            path = Path(directory) / 'trace.pftrace'
            path.write_bytes(b'fixture')
            path.chmod(0o600)
            self.assertEqual(PROBE.private_trace(path), path.resolve())
            path.chmod(0o644)
            with self.assertRaises(PROBE.ProbeError):
                PROBE.private_trace(path)
        with self.assertRaises(PROBE.ProbeError):
            PROBE.private_trace(PATH)

    def test_codec_name_only_exports_known_component_and_integer_pts(self):
        rows = PROBE.parse_results(result_csv(
            'CCodecBufferChannel::onWorkDone(c2.goldfish.h264.decoder@ts=123456)'))
        self.assertEqual(rows['codec_stages'][0]['content_pts_us'], 123456)
        self.assertEqual(rows['codec_stages'][0]['stage_id'], 2)
        self.assertNotIn('codec_name', rows['codec_stages'][0])
        for text in ('SECRET_PRIVATE_VIDEO_TITLE',
                     'CCodecBufferChannel::onWorkDone(c2.goldfish.h264.decoder@ts=SECRET)',
                     'CCodecBufferChannel::queue(c2.secret.decoder@ts=42)',
                     'CCodecBufferChannel::onWorkDone'):
            parsed = PROBE.parse_results(result_csv(text))
            self.assertEqual(parsed['codec_stages'], [])
            self.assertNotIn('SECRET', json.dumps(parsed))

    def test_large_surface_trace_requires_explicit_larger_private_read_bound(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            path = Path(directory) / 'large.pftrace'
            with path.open('wb') as out:
                out.truncate(PROBE.VIDEO_TRACE_BYTES + 1)
            path.chmod(0o600)
            with self.assertRaises(PROBE.ProbeError):
                PROBE.private_trace(path)
            self.assertEqual(PROBE.private_trace(path, PROBE.MAX_TRACE_BYTES), path.resolve())
            with self.assertRaises(PROBE.ProbeError):
                PROBE.private_trace(path, PROBE.MAX_TRACE_BYTES * 2)

    def test_actual_codec_instance_is_part_of_pts_pair_key(self):
        name = 'CCodecBufferChannel::onWorkDone(c2.goldfish.h264.decoder#320@ts=123456)'
        parsed = PROBE.parse_codec_name(name)
        self.assertEqual(parsed['instance_id'], 320)
        self.assertEqual(parsed['content_pts_us'], 123456)
        outer = PROBE.parse_codec_name('CCodecBufferChannel::onWorkDone-c2.goldfish.h264.decoder#320')
        self.assertEqual(outer['stage_id'], 6)
        rows = [dict(component_id=1, stage_id=1, instance_id=1, content_pts_us=5, ts_ns=10),
                dict(component_id=1, stage_id=2, instance_id=2, content_pts_us=5, ts_ns=20)]
        self.assertTrue(all(row['queue_to_work_done']['count'] == 0 for row in PROBE.codec_summary(rows)))

    def test_codec_pts_queue_and_done_in_different_pids_are_not_paired(self):
        rows = [dict(pid=100, component_id=1, instance_id=9, stage_id=1,
                     content_pts_us=5, ts_ns=0),
                dict(pid=200, component_id=1, instance_id=9, stage_id=2,
                     content_pts_us=5, ts_ns=10_000_000)]
        results = {row['pid']: row for row in PROBE.codec_summary(rows)}
        self.assertEqual(set(results), {100, 200})
        self.assertTrue(all(row['queue_to_work_done']['count'] == 0 for row in results.values()))
        self.assertEqual(results[100]['queue_without_done'], 1)
        self.assertEqual(results[100]['done_without_queue'], 0)
        self.assertEqual(results[200]['queue_without_done'], 0)
        self.assertEqual(results[200]['done_without_queue'], 1)

    def test_each_pid_can_pair_the_same_codec_instance_and_pts_independently(self):
        rows = [dict(pid=pid, component_id=1, instance_id=9, stage_id=stage,
                     content_pts_us=5, ts_ns=ts)
                for pid, stage, ts in ((100, 1, 0), (200, 1, 1_000_000),
                                       (100, 2, 2_000_000), (200, 2, 9_000_000))]
        results = {row['pid']: row for row in PROBE.codec_summary(rows)}
        self.assertEqual(set(results), {100, 200})
        for pid, delay_ms in ((100, 2), (200, 8)):
            self.assertEqual(results[pid]['queue_to_work_done']['count'], 1)
            self.assertEqual(results[pid]['queue_to_work_done']['max_ms'], delay_ms)
            self.assertEqual(results[pid]['ambiguous_duplicate_pts'], 0)

    def test_numeric_parser_rejects_strings_nonfinite_and_unbounded_values(self):
        for value in ('secret', 'nan', 'inf', '1.5', str(2**63)):
            with self.assertRaises(PROBE.ProbeError):
                PROBE.parse_integer(value)
        self.assertIsNone(PROBE.parse_integer('[NULL]'))
        self.assertEqual(PROBE.parse_integer('-1'), -1)

    def test_pts_pairing_ignores_ambiguous_and_negative_durations(self):
        def row(stage, pts, ts):
            return {'component_id': 1, 'stage_id': stage, 'content_pts_us': pts, 'ts_ns': ts}
        result = PROBE.codec_summary([row(1, 1, 0), row(2, 1, 10_000_000),
                                     row(1, 2, 1), row(1, 2, 2), row(2, 2, 3),
                                     row(1, 3, 50), row(2, 3, 20)])[0]
        self.assertEqual(result['queue_to_work_done']['count'], 1)
        self.assertEqual(result['queue_to_work_done']['max_ms'], 10)
        self.assertEqual(result['ambiguous_duplicate_pts'], 1)

    def test_scope_duration_completion_and_trace_boundary_are_distinct(self):
        rows = [dict(component_id=1, instance_id=1, stage_id=1, content_pts_us=1, ts_ns=0, dur_ns=1),
                dict(component_id=1, instance_id=1, stage_id=2, content_pts_us=2, ts_ns=5, dur_ns=1),
                dict(component_id=1, instance_id=1, stage_id=3, content_pts_us=None, ts_ns=10_000_000, dur_ns=2_000_000),
                dict(component_id=1, instance_id=1, stage_id=3, content_pts_us=None, ts_ns=15_000_000, dur_ns=-1)]
        result = PROBE.codec_summary(rows)[0]
        self.assertEqual(result['queue_without_done'], 1)
        self.assertEqual(result['done_without_queue'], 1)
        self.assertEqual(result['stage_counts']['3'], 2)
        self.assertEqual(result['stage_duration_ms']['3']['count'], 1)
        self.assertEqual(result['stage_duration_ms']['3']['max_ms'], 2)
        self.assertEqual(result['stage_open_slice_counts']['3'], 1)
        self.assertEqual(result['stage_completion_gaps']['3']['count'], 0)


if __name__ == '__main__':
    unittest.main()
