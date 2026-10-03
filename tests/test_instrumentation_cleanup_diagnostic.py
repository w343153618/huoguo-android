"""Sanitized cleanup evidence with inert children; no device or service calls."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('cleanup_diagnostic_driver',
    ROOT/'scripts/probes/run_authenticated_lan_ui.py')
DRIVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIVER)
MARKER = DRIVER.INSTRUMENTATION_NUMERIC_MARKER


def marker(value, ending='\n'):
    return MARKER+json.dumps(value)+ending


class InstrumentationCleanupDiagnosticCheck(unittest.TestCase):
    def diagnostic(self, stdout='', stderr=''):
        return DRIVER.instrumentation_cleanup_diagnostic((stdout, stderr))

    def test_complete_single_stdout_marker_projects_only_closed_fields(self):
        for ending in ('\n', '\r\n'):
            raw = marker(dict(helper_owned_attempt_started=True,
                failure_class='IllegalStateException', failure_cause_class='IOException',
                bounded_failure_label='no_authenticated_media', connection_failure_code=503,
                username='secret account', password='secret password',
                nested={'raw': 'secret raw log'}), ending)
            report = self.diagnostic(raw, 'private stderr\n')
            self.assertEqual(report['numeric_result_status'], 'valid')
            self.assertEqual(report['numeric_result'], dict(helper_owned_attempt_started=True,
                failure_class='IllegalStateException', failure_cause_class='IOException',
                bounded_failure_label='no_authenticated_media', connection_failure_code=503))
            self.assertEqual(report['stdout_bytes'], len(raw.encode('utf-8')))
            self.assertEqual((report['stdout_lines'], report['stderr_lines']), (1, 1))
            self.assertNotIn('secret', json.dumps(report))
            self.assertNotIn('private stderr', json.dumps(report))

    def test_unavailable_output_does_not_infer_helper_ownership(self):
        report = DRIVER.instrumentation_cleanup_diagnostic(None)
        self.assertEqual(report['numeric_result_status'], 'absent')
        self.assertFalse(report['output_available'])
        self.assertIsNone(report['stdout_bytes'])
        self.assertNotIn('numeric_result', report)

    def test_empty_or_stderr_only_result_is_absent(self):
        for stdout, stderr, count in (('', '', 0), ('ordinary private output\n', '', 0),
                ('', marker({'helper_owned_attempt_started': True}), 1)):
            report = self.diagnostic(stdout, stderr)
            self.assertEqual(report['numeric_result_status'], 'absent')
            self.assertEqual(report['stderr_numeric_marker_count'], count)
            self.assertNotIn('numeric_result', report)

    def test_indented_marker_is_not_a_result(self):
        self.assertEqual(self.diagnostic(' '+marker({}))['numeric_result_status'], 'absent')
        for separator in ('\r', '\u2028', '\u2029', '\x0b', '\x85'):
            self.assertEqual(self.diagnostic('ordinary'+separator+marker({}))[
                'numeric_result_status'], 'absent')

    def test_numeric_marker_requires_complete_line(self):
        self.assertEqual(self.diagnostic(marker({}, ''))['numeric_result_status'], 'malformed')

    def test_multiple_markers_are_ambiguous_even_if_one_malformed(self):
        for stdout, stderr in ((marker({})+marker({}), ''),
                (marker({})+MARKER+'broken\n', ''),
                (marker({}), marker({})), (marker({}), MARKER+'broken\n')):
            self.assertEqual(self.diagnostic(stdout, stderr)['numeric_result_status'], 'ambiguous')

    def test_strict_json_rejects_nonobjects_duplicates_nonfinite_and_trailing_data(self):
        for payload in ('[]', 'null', 'true', '"private"', '{} trailing', '{broken}',
                '{"x":NaN}', '{"x":Infinity}', '{"x":-Infinity}',
                '{"failure_class":"IOException","failure_class":"RuntimeException"}',
                '{"ignored":{"x":1,"x":2}}'):
            with self.subTest(payload=payload):
                report = self.diagnostic(MARKER+payload+'\n')
                self.assertEqual(report['numeric_result_status'], 'malformed')
                self.assertNotIn('numeric_result', report)

    def test_arbitrary_alphabetic_class_and_label_are_not_exported(self):
        report = self.diagnostic(marker(dict(failure_class='PrivateSecret',
            failure_cause_class='PrivateCause', bounded_failure_label='PrivateLabel')))
        self.assertEqual(report['numeric_result_status'], 'valid')
        self.assertEqual(set(report['numeric_result'].values()), {'unclassified'})
        self.assertNotIn('Private', json.dumps(report))

    def test_helper_ownership_is_strict_bool_and_absence_stays_unknown(self):
        for value in (False, True):
            report = self.diagnostic(marker({'helper_owned_attempt_started': value}))
            self.assertIs(report['numeric_result']['helper_owned_attempt_started'], value)
        for value in (0, 1, None, 'false', [], {}):
            with self.subTest(value=value):
                self.assertEqual(self.diagnostic(marker({'helper_owned_attempt_started': value}))[
                    'numeric_result_status'], 'malformed')
        self.assertEqual(self.diagnostic(marker({}))['numeric_result'], {})

    def test_failure_enum_types_are_strict(self):
        for field in ('failure_class', 'failure_cause_class', 'bounded_failure_label'):
            for value in (None, True, 1, [], {}):
                with self.subTest(field=field, value=value):
                    self.assertEqual(self.diagnostic(marker({field: value}))[
                        'numeric_result_status'], 'malformed')

    def test_connection_code_is_closed_integer_not_bool_or_unbounded(self):
        for code in (0, 1, 2, 100, 200, 401, 503, 599):
            report = self.diagnostic(marker({'connection_failure_code': code}))
            self.assertEqual(report['numeric_result']['connection_failure_code'], code)
        for code in (False, True, 0.0, '503', -1, 3, 99, 600, 65535, None):
            with self.subTest(code=code):
                self.assertEqual(self.diagnostic(marker({'connection_failure_code': code}))[
                    'numeric_result_status'], 'malformed')

    def test_limit_is_utf8_bytes_across_both_channels(self):
        raw = marker({})
        exact = raw+'x'*(65536-len(raw.encode('utf-8')))
        report = self.diagnostic(exact)
        self.assertEqual((report['stdout_bytes'], report['numeric_result_status']), (65536, 'valid'))
        self.assertEqual(self.diagnostic(exact, 'x')['numeric_result_status'], 'over_bound')
        unicode_raw = marker({})+'火'*21850
        self.assertLess(len(unicode_raw), 65536)
        report = self.diagnostic(unicode_raw)
        self.assertEqual(report['stdout_bytes'], len(unicode_raw.encode('utf-8')))
        self.assertEqual(report['numeric_result_status'], 'over_bound')
        self.assertIsNone(report['numeric_result_count'])
        self.assertNotIn('numeric_result', report)

    def test_invalid_unicode_or_output_types_are_malformed(self):
        self.assertEqual(self.diagnostic('\ud800')['numeric_result_status'], 'malformed')
        for output in ([], ('only',), (b'bytes', ''), ('', None), ('', '', ''), 'text'):
            with self.subTest(output=output):
                self.assertEqual(DRIVER.instrumentation_cleanup_diagnostic(output)[
                    'numeric_result_status'], 'malformed')

    def test_fixed_markers_and_signed_instrumentation_code_never_export_text(self):
        report = self.diagnostic('INSTRUMENTATION_FAILED: private launch\n'+
            'private java.lang.SecurityException: secret\n',
            'INSTRUMENTATION_ABORTED: private abort\nINSTRUMENTATION_CODE: -1\n')
        self.assertEqual(report['instrumentation_failed_marker_count'], 1)
        self.assertEqual(report['instrumentation_aborted_marker_count'], 1)
        self.assertEqual(report['security_exception_line_count'], 1)
        self.assertEqual((report['instrumentation_code_status'], report['instrumentation_code']), ('valid', -1))
        self.assertNotIn('private launch', json.dumps(report))
        self.assertNotIn('private abort', json.dumps(report))
        for code in (-2147483648, 2147483647):
            self.assertEqual(self.diagnostic('INSTRUMENTATION_CODE: '+str(code)+'\n')[
                'instrumentation_code'], code)
        for raw in ('INSTRUMENTATION_CODE: 2147483648\n', 'INSTRUMENTATION_CODE: -2147483649\n',
                'INSTRUMENTATION_CODE: 1', 'INSTRUMENTATION_CODE: private\n'):
            self.assertEqual(self.diagnostic(raw)['instrumentation_code_status'], 'malformed')
        self.assertEqual(self.diagnostic('INSTRUMENTATION_CODE: 1\n',
            'INSTRUMENTATION_CODE: 1\n')['instrumentation_code_status'], 'ambiguous')


class InertChild:
    def __init__(self, output, *, timeouts=0, error=None):
        self.output, self.timeouts, self.error = output, timeouts, error
        self.returncode = None
        self.signals, self.waits, self.final_waits = [], [], []

    def poll(self):
        return self.returncode

    def terminate(self):
        self.signals.append('terminate')

    def kill(self):
        self.signals.append('kill')
        self.returncode = -9

    def communicate(self, timeout):
        self.waits.append(timeout)
        if self.timeouts:
            self.timeouts -= 1
            raise subprocess.TimeoutExpired('inert-owned-child', timeout, output='private timed output')
        if self.error:
            raise self.error
        self.returncode = self.returncode if self.returncode is not None else 0
        return self.output

    def wait(self, timeout):
        self.final_waits.append(timeout)
        self.returncode = -9
        return -9


class InstrumentationCleanupIntegrationCheck(unittest.TestCase):
    def invoke(self, child, *, diagnostic_error=None):
        events = []
        reader = Mock()
        def run(command, **kwargs):
            events.append(command[-1])
            if command[0] == 'lsof':
                return subprocess.CompletedProcess(command, 1, stdout=b'', stderr=b'')
            if command[-1] == 'pidof '+DRIVER.TARGET_PACKAGE:
                return subprocess.CompletedProcess(command, 1, stdout='', stderr='')
            return subprocess.CompletedProcess(command, 0, stdout='', stderr='')
        with tempfile.TemporaryDirectory() as path, contextlib.ExitStack() as stack:
            folder = Path(path)
            argv = ['probe', '--output', path, '--media-only', '--owner-source-guard',
                'numeric-playing-unknown', '--source-listener-monotonic-ns', '100',
                '--source-expected-pid', '3553', '--source-expected-uid', '10235',
                '--source-expected-start-ticks', '2521', '--source-trace-root', str(folder/'trace')]
            stack.enter_context(patch.object(DRIVER.sys, 'argv', argv))
            stack.enter_context(patch.object(DRIVER.subprocess, 'run', side_effect=run))
            spawned = stack.enter_context(patch.object(DRIVER.subprocess, 'Popen', return_value=child))
            stack.enter_context(patch.object(DRIVER, 'TracePrefixReader', return_value=reader))
            stack.enter_context(patch.object(DRIVER.owner_source_gate, 'collect_playing_unknown', return_value={}))
            validated = stack.enter_context(patch.object(DRIVER.owner_source_gate, 'validate_launch_fresh',
                side_effect=[None, DRIVER.owner_source_gate.SourceGateError('source_launch_stale')]))
            stack.enter_context(patch.object(DRIVER.time, 'clock_gettime_ns', side_effect=[1000, 2000]))
            stack.enter_context(patch.object(DRIVER.time, 'monotonic', return_value=0))
            if diagnostic_error:
                stack.enter_context(patch.object(DRIVER, 'instrumentation_cleanup_diagnostic',
                    side_effect=diagnostic_error))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            code = DRIVER.main()
            report = json.loads((folder/'ui-acceptance.json').read_text())
        self.assertEqual((code, spawned.call_count, validated.call_count), (1, 1, 2))
        self.assertEqual(report['driver_failure_class'], 'SourceGateError')
        self.assertEqual(report['driver_failure_label'], 'source_launch_stale')
        self.assertFalse(report['phone_sampler_started'])
        reader.read.assert_not_called()
        reader.close.assert_called_once()
        for secret in ('private timed output', 'private pipe error', 'private diagnostic text',
                'private stderr', 'private password'):
            self.assertNotIn(secret, json.dumps(report))
        return report, events

    def test_failure_before_communicate_preserves_primary_and_records_safe_output(self):
        child = InertChild((marker(dict(helper_owned_attempt_started=False,
            failure_class='IllegalStateException', bounded_failure_label='no_authenticated_media',
            password='private password'))+'INSTRUMENTATION_CODE: -1\n', 'private stderr\n'))
        report, events = self.invoke(child)
        diagnostic = report['instrumentation_cleanup_diagnostic']
        self.assertEqual(diagnostic['numeric_result_status'], 'valid')
        self.assertIs(diagnostic['numeric_result']['helper_owned_attempt_started'], False)
        self.assertEqual(diagnostic['instrumentation_code'], -1)
        self.assertEqual(child.signals, ['terminate'])
        self.assertEqual(child.waits, [2])
        stops = [event for event in events if 'am force-stop' in event]
        self.assertEqual(stops, ['am force-stop local.huoguo.lanuitest',
            'am force-stop '+DRIVER.TARGET_PACKAGE])
        self.assertNotIn('cleanup_failures', report)

    def test_timeout_escalation_is_unchanged_and_diagnostic_is_not_success(self):
        child = InertChild(('', ''), timeouts=2)
        report, _ = self.invoke(child)
        self.assertEqual(child.signals, ['terminate', 'terminate', 'kill'])
        self.assertEqual(child.waits, [2, 2, 2])
        self.assertEqual(report['instrumentation_exit_code'], -9)
        self.assertEqual(report['cleanup_failures'], [
            {'operation': 'instrumentation_wait', 'failure_class': 'TimeoutExpired'},
            {'operation': 'instrumentation_wait', 'failure_class': 'TimeoutExpired'}])
        self.assertEqual(report['instrumentation_cleanup_diagnostic']['numeric_result_status'], 'absent')

    def test_failed_pipe_read_keeps_primary_and_records_unavailable_diagnostic(self):
        child = InertChild(('', ''), error=OSError('private pipe error'))
        report, _ = self.invoke(child)
        self.assertEqual(child.signals, ['terminate', 'kill'])
        self.assertEqual(child.final_waits, [2])
        self.assertEqual(report['cleanup_failures'], [
            {'operation': 'instrumentation_reap', 'failure_class': 'OSError'}])
        self.assertFalse(report['instrumentation_cleanup_diagnostic']['output_available'])
        self.assertNotIn('numeric_result', report['instrumentation_cleanup_diagnostic'])

    def test_diagnostic_exception_never_overwrites_primary_or_prevents_private_cleanup(self):
        child = InertChild(('', ''))
        report, events = self.invoke(child, diagnostic_error=OSError('private diagnostic text'))
        self.assertEqual(report['cleanup_failures'], [
            {'operation': 'instrumentation_cleanup_diagnostic', 'failure_class': 'OSError'}])
        self.assertNotIn('instrumentation_cleanup_diagnostic', report)
        self.assertIn('rm -f', events[-1])
        self.assertEqual(child.signals, ['terminate'])


if __name__ == '__main__':
    unittest.main()
