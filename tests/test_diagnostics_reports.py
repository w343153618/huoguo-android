"""Offline boundaries for report storage and the fixed diagnostic scene lease."""
import base64
import copy
import hashlib
import io
import json
import os
import pathlib
import stat
import sys
import tempfile
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gateway
from diagnostics_reports import DiagnosticSource, DiagnosticsError, MAX_BYTES, ReportStore, SOURCE_COMPONENT, SOURCE_PACKAGE, decode_json, read_json, validate_report


def example_report():
    return {
        'schema_version': 1, 'client_report_id': str(uuid.uuid4()),
        'created_at': '2026-09-29T12:34:56Z', 'app_version': '1.25',
        'device': {'manufacturer': 'OnePlus', 'model': 'OnePlus 15', 'android': '16', 'sdk': 36, 'media_volume': 0.8, 'audio_gain': 1.2, 'avc_hardware_advertised': True, 'hevc_hardware_advertised': True},
        'network': {'transport': 'WIFI', 'label': '东北中国移动宽带 Wi-Fi', 'vpn_present': True},
        'mode': 'quick', 'status': 'completed',
        'stages': [{
            'label': '流畅 60FPS', 'max_size': 960, 'bitrate': 2_500_000, 'max_fps': 60, 'mode': 'CBR', 'buffer_ms': 0,
            'elapsed_ms': 15_000, 'received_frames': 880, 'rendered_frames': 870, 'video_bytes': 4_000_000,
            'received_fps': 58.67, 'rendered_fps': 58.0, 'receive_mbps': 2.13, 'render_gap_count': 1,
            'max_render_gap_ms': 76.0, 'discarded_frames': 10, 'rtt_p50_ms': 32.5, 'rtt_p95_ms': 60,
            'decoder_name': 'c2.qti.avc.decoder', 'hardware_decoder': True, 'dimensions': '432x960',
            'thermal_start': 0, 'thermal_end': 1, 'battery_start': 80, 'battery_end': 79,
            'network_changed': False, 'transport': 'WIFI', 'vpn_present': True,
            'adaptive_rejected': False, 'accepted_bitrate': 2_500_000, 'source_startup_ms': 600,
            'valid': True, 'invalid_reason': '', 'last_receive_ago_ms': 5, 'last_render_ago_ms': 3,
            'receive_interval_jitter_ms': 5.2, 'render_interval_jitter_ms': 3.4, 'source_interval_jitter_ms': 0.4,
            'samples': [{'elapsed_ms': 1000, 'received_frames': 60, 'rendered_frames': 59, 'video_bytes': 300_000, 'rtt_ms': 31, 'thermal_status': 0}],
        }],
        'feedback': {'smoothness': '顺畅', 'audio': '正常'},
    }


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name) / 'reports'
        self.store = ReportStore(self.root)
        self.report = example_report()

    def tearDown(self):
        self.temporary.cleanup()

    def test_private_atomic_report_roundtrip(self):
        receipt = self.store.save('huoguo', self.report)
        self.assertEqual(self.store.get('huoguo', receipt['report_id'])['report'], self.report)
        self.assertEqual(self.store.listing('huoguo')['reports'][0]['report_id'], receipt['report_id'])
        account = self.root / hashlib.sha256(b'huoguo').hexdigest()
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(account.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(next(account.glob('*.json')).stat().st_mode), 0o600)
        self.assertEqual(list(account.glob('*.tmp')), [])
        self.assertNotIn('huoguo', next(account.glob('*.json')).read_text())

    def test_reports_are_scoped_to_authenticated_account(self):
        first = self.store.save('huoguo', self.report)
        self.assertEqual(self.store.listing('other')['reports'], [])
        with self.assertRaises(DiagnosticsError) as error:
            self.store.get('other', first['report_id'])
        self.assertEqual(error.exception.status, 404)
        second = self.store.save('other', self.report)
        self.assertNotEqual(first['report_id'], second['report_id'])
        self.assertEqual(len(self.store.listing('huoguo')['reports']), 1)

    def test_username_and_report_identifier_cannot_form_paths(self):
        receipt = self.store.save('../untrusted/account', self.report)
        self.assertEqual(len(list(self.root.iterdir())), 1)
        self.assertEqual(next(self.root.iterdir()).name, hashlib.sha256(b'../untrusted/account').hexdigest())
        for identifier in ('../auth.json', '../../secrets', '%2e%2e%2fauth.json', receipt['report_id'] + '/other', '/absolute', ''):
            with self.subTest(identifier=identifier), self.assertRaises(DiagnosticsError) as error:
                self.store.get('huoguo', identifier)
            self.assertEqual(error.exception.status, 400)

    def test_concurrent_retries_publish_one_report_and_conflicting_retry_is_rejected(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.store.save('huoguo', self.report), range(16)))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(len(self.store.listing('huoguo')['reports']), 1)
        changed = copy.deepcopy(self.report)
        changed['status'] = 'cancelled'
        with self.assertRaises(DiagnosticsError) as error:
            self.store.save('huoguo', changed)
        self.assertEqual(error.exception.status, 409)

    def test_failed_publication_cleans_staging_file_and_retry_succeeds(self):
        with patch('diagnostics_reports.os.replace', side_effect=OSError('Disk error')), self.assertRaises(OSError):
            self.store.save('huoguo', self.report)
        self.assertEqual(self.store.listing('huoguo')['reports'], [])
        self.assertEqual(list(self.root.rglob('*.tmp')), [])
        receipt = self.store.save('huoguo', self.report)
        self.assertEqual(self.store.get('huoguo', receipt['report_id'])['report'], self.report)

    def test_report_count_byte_quota_and_retention_are_bounded(self):
        self.store.max_reports = 2
        first = self.store.save('huoguo', self.report)
        size = next(self.root.rglob('*.json')).stat().st_size
        self.store.max_account_bytes = size * 2 + 100
        for _ in range(3):
            report = example_report()
            newest = self.store.save('huoguo', report)
        listing = self.store.listing('huoguo')['reports']
        self.assertLessEqual(len(listing), 2)
        self.assertNotIn(first['report_id'], [item['report_id'] for item in listing])
        self.assertEqual(listing[0]['report_id'], newest['report_id'])
        self.assertLessEqual(sum(path.stat().st_size for path in self.root.rglob('*.json')), self.store.max_account_bytes)
        for path in self.root.rglob('*.json'):
            os.utime(path, (time.time() - 31 * 86400,) * 2)
        self.assertEqual(self.store.listing('huoguo')['reports'], [])

    def test_symlinks_cannot_read_outside_the_account_directory(self):
        receipt = self.store.save('huoguo', self.report)
        path = next(self.root.rglob('*.json'))
        path.unlink()
        secret = pathlib.Path(self.temporary.name) / 'secret.json'
        secret.write_text('{"password":"never expose"}')
        path.symlink_to(secret)
        with self.assertRaises(DiagnosticsError) as error:
            self.store.get('huoguo', receipt['report_id'])
        self.assertEqual(error.exception.status, 404)
        self.assertTrue(secret.exists())

    def test_symlink_storage_directory_is_rejected(self):
        outside = pathlib.Path(self.temporary.name) / 'outside'
        outside.mkdir()
        self.root.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(DiagnosticsError) as error:
            self.store.save('huoguo', self.report)
        self.assertEqual(error.exception.status, 503)
        self.assertEqual(list(outside.iterdir()), [])

    def test_secret_and_unknown_keys_are_rejected_at_every_level(self):
        for location in ('root', 'device', 'network', 'stage', 'sample', 'feedback'):
            report = copy.deepcopy(self.report)
            target = {'root': report, 'device': report['device'], 'network': report['network'], 'stage': report['stages'][0], 'sample': report['stages'][0]['samples'][0], 'feedback': report['feedback']}[location]
            target['password'] = 'not stored'
            with self.subTest(location=location), self.assertRaises(DiagnosticsError) as error:
                self.store.save('huoguo', report)
            self.assertEqual(error.exception.status, 400)
        self.assertFalse(self.root.exists())

    def test_invalid_metrics_text_and_excess_lists_are_rejected(self):
        cases = []
        for value in (float('nan'), float('inf'), -1.0, True, '60', 10 ** 3000):
            report = copy.deepcopy(self.report)
            report['stages'][0]['rendered_fps'] = value
            cases.append(report)
        for field, value in (('label', '\ud800'), ('error', 'x' * 161), ('dimensions', '../../password'), ('samples', [{'elapsed_ms': 1}] * 241)):
            report = copy.deepcopy(self.report)
            report['stages'][0][field] = value
            cases.append(report)
        report = copy.deepcopy(self.report)
        report['stages'] *= 13
        cases.append(report)
        for index, report in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(DiagnosticsError) as error:
                validate_report(report)
            self.assertEqual(error.exception.status, 400)

    def test_partial_cancelled_or_failed_reports_are_accepted(self):
        for status in ('cancelled', 'failed'):
            report = example_report()
            report['status'], report['stages'] = status, []
            self.assertTrue(self.store.save('huoguo', report)['ok'])

    def test_extended_performance_metrics_and_cleanup_status_roundtrip(self):
        report = copy.deepcopy(self.report)
        report.update(cleanup_ok=False, cleanup_error='Diagnostic scene stop timed out')
        report['device']['total_memory_mb'] = 16_384.5
        report['stages'][0].update(battery_temp_start_c=34.2, battery_temp_end_c=37.6,
            app_cpu_p50_percent=120.5, app_cpu_p95_percent=270.25,
            client_pipeline_p50_ms=143.2, client_pipeline_p95_ms=188.6,
            app_heap_peak_mb=120.2, native_heap_peak_mb=81.3, actual_sample_ms=1000)
        report['stages'][0]['samples'][0].update(app_cpu_percent=180.5, java_heap_mb=111.2,
            native_heap_mb=75.8, signal_strength_dbm=-65.5)
        receipt = self.store.save('huoguo', report)
        self.assertEqual(self.store.get('huoguo', receipt['report_id'])['report'], report)

    def test_extended_metrics_remain_finite_typed_and_bounded(self):
        fields = {
            'device': {'total_memory_mb': (0, 131_072)},
            'stage': {'battery_temp_start_c': (-20, 100), 'battery_temp_end_c': (-20, 100),
                'app_cpu_p50_percent': (0, 6400), 'app_cpu_p95_percent': (0, 6400),
                'client_pipeline_p50_ms': (0, 60_000), 'client_pipeline_p95_ms': (0, 60_000),
                'app_heap_peak_mb': (0, 16_384), 'native_heap_peak_mb': (0, 16_384), 'actual_sample_ms': (1, 5000)},
            'sample': {'app_cpu_percent': (0, 6400), 'java_heap_mb': (0, 16_384),
                'native_heap_mb': (0, 16_384), 'signal_strength_dbm': (-150, 0)},
        }
        for location, metrics in fields.items():
            for key, (low, high) in metrics.items():
                for value in (low, high):
                    report = copy.deepcopy(self.report)
                    target = {'device': report['device'], 'stage': report['stages'][0], 'sample': report['stages'][0]['samples'][0]}[location]
                    target[key] = value
                    with self.subTest(location=location, key=key, boundary=value):
                        validate_report(report)
                for value in (low - 1, high + 1, float('nan'), float('inf'), True, '1'):
                    report = copy.deepcopy(self.report)
                    target = {'device': report['device'], 'stage': report['stages'][0], 'sample': report['stages'][0]['samples'][0]}[location]
                    target[key] = value
                    with self.subTest(location=location, key=key, invalid=repr(value)), self.assertRaises(DiagnosticsError) as error:
                        validate_report(report)
                    self.assertEqual(error.exception.status, 400)
        for key, value in (('cleanup_ok', 1), ('cleanup_error', 'x' * 161), ('cleanup_error', 'private\ncontents')):
            report = copy.deepcopy(self.report)
            report[key] = value
            with self.subTest(key=key), self.assertRaises(DiagnosticsError):
                validate_report(report)

    def test_bad_json_duplicate_keys_nonfinite_values_and_oversized_bodies_are_rejected(self):
        for body in (b'{', b'\xff', b'{"mode":"quick","mode":"full"}', b'{"rtt":NaN}', b'{"rtt":Infinity}', b'{"rtt":-Infinity}', b'[' * 2000 + b']' * 2000):
            with self.subTest(body=body[:40]), self.assertRaises(DiagnosticsError) as error:
                decode_json(body)
            self.assertEqual(error.exception.status, 400)
        with self.assertRaises(DiagnosticsError) as error:
            decode_json(b'x' * (MAX_BYTES + 1))
        self.assertEqual(error.exception.status, 413)

    def test_http_lengths_are_bounded_before_reading_and_truncation_is_rejected(self):
        for headers, body, expected in (({}, b'', 400), ({'Content-Length': '-1'}, b'', 400), ({'Content-Length': 'bad'}, b'', 400), ({'Content-Length': str(MAX_BYTES + 1)}, b'', 413), ({'Content-Length': '4'}, b'{}', 400), ({'Content-Length': '2', 'Transfer-Encoding': 'chunked'}, b'{}', 400)):
            request = SimpleNamespace(headers=headers, rfile=io.BytesIO(body), connection=Mock())
            with self.subTest(headers=headers), self.assertRaises(DiagnosticsError) as error:
                read_json(request)
            self.assertEqual(error.exception.status, expected)
            if expected == 413:
                self.assertEqual(request.rfile.tell(), 0)
        headers = Message()
        headers['Content-Length'] = '2'
        headers['Content-Length'] = '2'
        with self.assertRaises(DiagnosticsError):
            read_json(SimpleNamespace(headers=headers, rfile=io.BytesIO(b'{}'), connection=Mock()))


class GatewayAuthTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        directory = pathlib.Path(self.temporary.name)
        self.auth_file = directory / 'auth.json'
        users = {}
        for username in ('huoguo', 'other'):
            salt = bytes(range(16))
            users[username] = {'username': username, 'salt': salt.hex(), 'digest': hashlib.scrypt(b'test-password', salt=salt, n=16384, r=8, p=1).hex()}
        self.auth_file.write_text(json.dumps({'users': users}))
        self.patches = [patch.object(gateway, 'AUTH_FILE', str(self.auth_file)), patch.object(gateway, 'diagnostics', ReportStore(directory / 'reports')), patch.object(gateway, 'auth_failures', [])]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    def request(self, method, path, body=None, username=None, password='test-password'):
        request = gateway.Handler.__new__(gateway.Handler)
        request.path, request.headers = path, Message()
        data = json.dumps(body).encode() if body is not None else b''
        if body is not None:
            request.headers['Content-Length'] = str(len(data))
        if username is not None:
            request.headers['Authorization'] = 'Basic ' + base64.b64encode((username + ':' + password).encode()).decode()
        request.connection, request.rfile = Mock(), io.BytesIO(data)
        request.reply = Mock()
        getattr(request, 'do_' + method)()
        return request.reply.call_args.args

    def test_report_routes_require_auth_and_preserve_login_rate_limit(self):
        self.assertEqual(self.request('GET', '/diagnostics/reports')[0], 401)
        self.assertEqual(self.request('POST', '/diagnostics/reports', example_report(), 'huoguo', 'wrong')[0], 401)
        self.assertEqual(self.request('POST', '/diagnostics/source', {'action': 'start', 'run_id': str(uuid.uuid4())})[0], 401)
        with patch.object(gateway, 'auth_failures', [time.monotonic()] * 20):
            self.assertEqual(self.request('GET', '/diagnostics/reports', username='huoguo')[0], 429)

    def test_authenticated_report_api_enforces_ownership_and_returns_receipt(self):
        report = example_report()
        status, receipt = self.request('POST', '/diagnostics/reports', report, 'huoguo')
        self.assertEqual(status, 201)
        self.assertEqual(set(receipt), {'ok', 'report_id', 'created_at'})
        status, envelope = self.request('GET', '/diagnostics/reports/' + receipt['report_id'], username='huoguo')
        self.assertEqual(status, 200)
        self.assertEqual(envelope['report'], report)
        self.assertEqual(self.request('GET', '/diagnostics/reports/' + receipt['report_id'], username='other')[0], 404)
        self.assertEqual(self.request('GET', '/diagnostics/reports', username='other')[1]['reports'], [])
        self.assertEqual(self.request('GET', '/diagnostics/reports/../auth.json', username='huoguo')[0], 400)

    def test_source_dispatch_uses_the_authenticated_account(self):
        request = {'action': 'start', 'run_id': str(uuid.uuid4())}
        source = Mock()
        source.control.return_value = {'ok': True}
        with patch.object(gateway, 'diagnostic_source', source):
            self.assertEqual(self.request('POST', '/diagnostics/source', request, 'huoguo'), (200, {'ok': True}))
        source.control.assert_called_once_with('huoguo', request)


class FakeTimer:
    def __init__(self, seconds, callback):
        self.seconds, self.callback = seconds, callback
        self.cancelled, self.started = False, False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.calls, self.timers = [], []
        self.now = 0
        def adb(*args):
            self.calls.append(args)
            return SimpleNamespace(stdout='package:/data/app/test.apk\n' if args[:3] == ('shell', 'pm', 'path') else 'Starting: Intent\n')
        def timer(seconds, callback):
            result = FakeTimer(seconds, callback)
            self.timers.append(result)
            return result
        self.adb, self.ensure = Mock(side_effect=adb), Mock()
        self.source = DiagnosticSource(self.adb, self.ensure, clock=lambda: self.now, timer_factory=timer)
        self.run_id = str(uuid.uuid4())

    def control(self, action, account='huoguo', run_id=None):
        return self.source.control(account, {'action': action, 'run_id': run_id or self.run_id})

    def test_source_runs_only_fixed_package_and_owner_can_stop(self):
        result = self.control('start')
        self.assertEqual(result['lease_seconds'], 480)
        self.ensure.assert_called_once()
        self.assertIn(('shell', 'am', 'start', '-S', '-n', SOURCE_COMPONENT, '--es', 'run_id', self.run_id), self.calls)
        with self.assertRaises(DiagnosticsError) as error:
            self.control('stop', account='other')
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.control('stop')['action'], 'stop')
        self.assertEqual(self.calls[-1], ('shell', 'am', 'force-stop', SOURCE_PACKAGE))
        count = len(self.calls)
        self.control('stop')
        self.assertEqual(len(self.calls), count)

    def test_stage_restart_refreshes_lease_and_old_timer_cannot_stop_new_scene(self):
        self.control('start')
        first = self.timers[0]
        self.now = 60
        self.control('start')
        self.assertTrue(first.cancelled)
        count = len(self.calls)
        first.callback()
        self.assertEqual(len(self.calls), count)
        self.assertEqual(self.source.owner, 'huoguo')
        self.timers[-1].callback()
        self.assertEqual(self.calls[-1], ('shell', 'am', 'force-stop', SOURCE_PACKAGE))
        self.assertIsNone(self.source.owner)

    def test_other_owner_and_other_run_are_rejected_until_lease_expires(self):
        self.control('start')
        for account, run in (('other', self.run_id), ('huoguo', str(uuid.uuid4()))):
            with self.assertRaises(DiagnosticsError) as error:
                self.control('start', account, run)
            self.assertEqual(error.exception.status, 409)
        self.now = 481
        self.control('start', account='other')
        self.assertIn(('shell', 'am', 'force-stop', SOURCE_PACKAGE), self.calls)
        self.assertEqual(self.source.owner, 'other')

    def test_missing_scene_and_android_failure_return_503(self):
        self.adb.side_effect = lambda *args: SimpleNamespace(stdout='')
        with self.assertRaises(DiagnosticsError) as error:
            self.control('start')
        self.assertEqual(error.exception.status, 503)
        self.assertIsNone(self.source.owner)
        self.ensure.side_effect = RuntimeError('Android unavailable')
        with self.assertRaises(DiagnosticsError) as error:
            self.control('start')
        self.assertEqual(error.exception.status, 503)

    def test_arbitrary_adb_fields_and_run_identifiers_are_rejected_without_commands(self):
        for request in ({'action': 'shell', 'run_id': self.run_id}, {'action': 'start', 'run_id': '../private'}, {'action': 'start', 'run_id': self.run_id, 'component': 'other/.PrivateActivity'}):
            with self.subTest(request=request), self.assertRaises(DiagnosticsError) as error:
                self.source.control('huoguo', request)
            self.assertEqual(error.exception.status, 400)
        with self.assertRaises(DiagnosticsError) as error:
            self.source.control(None, {'action': 'start', 'run_id': self.run_id})
        self.assertEqual(error.exception.status, 401)
        self.adb.assert_not_called()
        self.ensure.assert_not_called()


if __name__ == '__main__':
    unittest.main()
