"""Freshness integration with inert children, no ADB, media or service actions."""
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
SPEC = importlib.util.spec_from_file_location('owner_source_driver',
    ROOT/'scripts/probes/run_authenticated_lan_ui.py')
DRIVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIVER)


class Child:
    def __init__(self, loops=0):
        self.loops, self.polls, self.returncode = loops, 0, None
        self.signals = []

    def poll(self):
        self.polls += 1
        if self.polls > self.loops:
            self.returncode = 0
        return self.returncode

    def communicate(self, timeout):
        self.returncode = 0
        return '', ''

    def terminate(self):
        self.signals.append('terminate')
        self.returncode = -15

    def kill(self):
        self.signals.append('kill')
        self.returncode = -9


class OwnerSourceDriverCheck(unittest.TestCase):
    def arguments(self, folder):
        return ['--output', str(folder), '--media-only', '--owner-source-guard',
            'numeric-playing-unknown', '--source-listener-monotonic-ns', '100',
            '--source-expected-pid', '3553', '--source-expected-uid', '10235',
            '--source-expected-start-ticks', '2521', '--source-trace-root',
            str(folder/'trace')]

    def invoke(self, folder, *, guard=True, collect_error=None, qualify_error=None,
               close_error=None, loops=0, target_started_during_collection=False, stats=False,
               stats_error=None, endpoints_match=True):
        events = []
        pid_reads = 0
        reader = Mock()
        def read_prefix():
            events.append(('prefix-read', None))
            return b'owned complete prefix'
        reader.read.side_effect = read_prefix
        reader.close.side_effect = close_error

        def run(command, **kwargs):
            nonlocal pid_reads
            events.append(('run', command[-1]))
            if command[0] == 'lsof':
                return subprocess.CompletedProcess(command, 1, stdout=b'', stderr=b'')
            if command[-1] == 'pidof '+DRIVER.TARGET_PACKAGE:
                pid_reads += 1
                if target_started_during_collection and pid_reads > 1:
                    return subprocess.CompletedProcess(command, 0, stdout='123', stderr='')
                return subprocess.CompletedProcess(command, 1, stdout='', stderr='')
            if command[-1].startswith('cmd package list packages'):
                return subprocess.CompletedProcess(command, 0,
                    stdout='package:'+DRIVER.TARGET_PACKAGE+' uid:10100', stderr='')
            if 'test -f' in command[-1] and 'ready-touch' in command[-1]:
                return subprocess.CompletedProcess(command, 1, stdout='', stderr='')
            return subprocess.CompletedProcess(command, 0, stdout='', stderr='')

        def collect(*args):
            events.append(('collect', args))
            if collect_error:
                raise collect_error
            return {'fixture': 'closed numeric observation'}

        def qualify(*args, **kwargs):
            events.append(('qualify', (args[1], args[2])))
            if qualify_error:
                raise qualify_error
            return {'fixture': 'qualified first capture'}

        def spawn(command, **kwargs):
            events.append(('spawn', command))
            return Child(loops if command[0] == 'adb' else 0)

        argv = self.arguments(folder) if guard else ['--output', str(folder), '--media-only']
        if stats:
            argv.extend(['--source-stats-window', 'on'])
        stats_calls = 0
        def collect_stats(*args, **kwargs):
            nonlocal stats_calls
            stats_calls += 1
            events.append(('Stats', stats_calls))
            if stats_calls == 2 and stats_error:
                raise stats_error
            return {'identity': {'pid': 3553, 'uid': 10235, 'start_ticks': 2521}}
        def pause(*args):
            events.append(('source-control', args[-1]))
            return {'state_after': 2 if args[-1] == 'paused' else 3}
        clock_value = 1000
        def native_clock(domain):
            nonlocal clock_value
            clock_value += 1000
            events.append(('native-clock', clock_value))
            return clock_value
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(DRIVER.sys, 'argv', ['probe']+argv))
            stack.enter_context(patch.object(DRIVER.subprocess, 'run', side_effect=run))
            spawned = stack.enter_context(patch.object(DRIVER.subprocess, 'Popen', side_effect=spawn))
            factory = stack.enter_context(patch.object(DRIVER, 'TracePrefixReader', return_value=reader))
            collected = stack.enter_context(patch.object(DRIVER.owner_source_gate,
                'collect_playing_unknown', side_effect=collect))
            stack.enter_context(patch.object(DRIVER.owner_source_gate, 'validate_launch_fresh'))
            qualified = stack.enter_context(patch.object(DRIVER.owner_source_gate,
                'qualify_first_capture', side_effect=qualify))
            stack.enter_context(patch.object(DRIVER.time, 'clock_gettime_ns', side_effect=native_clock))
            stack.enter_context(patch.object(DRIVER.time, 'monotonic', return_value=0))
            stack.enter_context(patch.object(DRIVER.time, 'sleep'))
            stack.enter_context(patch.object(DRIVER.owner_source_stats_gate, 'collect_fresh', side_effect=collect_stats))
            stack.enter_context(patch.object(DRIVER.owner_source_stats_gate, 'require_fresh'))
            stack.enter_context(patch.object(DRIVER.owner_source_stats_gate, 'endpoint_match',
                return_value={'endpoint_content_matches': endpoints_match}))
            stack.enter_context(patch.object(DRIVER.source_window_control, 'transition', side_effect=pause))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            code = DRIVER.main()
        return (code, json.loads((folder/'ui-acceptance.json').read_text()),
            events, reader, collected.call_count, qualified.call_count,
            factory.call_count, spawned.call_count)

    def test_opt_in_Stats_precedes_instrument_and_pause_precedes_marker(self):
        with tempfile.TemporaryDirectory() as path:
            _, report, events, *_ = self.invoke(Path(path), stats=True, loops=2)
        before = events.index(('Stats', 1))
        instrument = next(i for i,e in enumerate(events) if e[0] == 'spawn' and e[1][0] == 'adb')
        post = events.index(('Stats', 2))
        pause = events.index(('source-control', 'paused'))
        marker = next(i for i,e in enumerate(events) if e[0] == 'run' and "'touch " in e[1] and 'steady-sampled.tmp' in e[1])
        self.assertLess(before, instrument)
        self.assertLess(instrument, post)
        self.assertLess(pause, post)
        self.assertLess(pause, marker)
        self.assertEqual(report['source_pause_receipt']['state_after'], 2)
        self.assertTrue(report['source_Stats_endpoint_match']['endpoint_content_matches'])

    def test_post_Stats_rejection_still_pauses_and_does_not_publish_marker(self):
        with tempfile.TemporaryDirectory() as path:
            code, report, events, *_ = self.invoke(Path(path), stats=True, loops=2,
                stats_error=DRIVER.owner_source_stats_gate.Rejected('collector_failed'))
        self.assertEqual(code, 1)
        self.assertIn(('source-control', 'paused'), events)
        self.assertNotIn('steady_samplers_completed_before_leave', report)
        self.assertEqual(report['source_pause_receipt']['state_after'], 2)

    def test_endpoint_mismatch_pauses_but_refuses_window_acceptance(self):
        with tempfile.TemporaryDirectory() as path:
            code, report, events, *_ = self.invoke(Path(path), stats=True, loops=2, endpoints_match=False)
        self.assertEqual(code, 1)
        self.assertEqual(report['driver_failure_label'], 'source_Stats_endpoint_mismatch')
        self.assertIn(('source-control', 'paused'), events)
        self.assertNotIn('steady_samplers_completed_before_leave', report)

    def test_Stats_default_off_and_missing_guard_or_foreign_guest_refuse(self):
        with tempfile.TemporaryDirectory() as path:
            _, report, events, *_ = self.invoke(Path(path), loops=2)
            self.assertFalse(report['source_stats_window_enabled'])
            self.assertFalse(any(e[0] in ('Stats','source-control') for e in events))
            for args in (['--output',path,'--source-stats-window','on'],
                         self.arguments(Path(path))+['--source-stats-window','on','--guest','emulator-5554']):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    DRIVER.parse_arguments(args)

    def test_default_off_does_not_read_source_trace_or_use_native_gate(self):
        with tempfile.TemporaryDirectory() as path:
            _, report, _, reader, collected, qualified, opened, _ = self.invoke(Path(path), guard=False)
        self.assertFalse(report['owner_source_guard_enabled'])
        self.assertEqual((collected, qualified, opened), (0, 0, 0))
        reader.read.assert_not_called()
        reader.close.assert_not_called()

    def test_gate_runs_after_slow_preflight_then_target_recheck_before_owned_spawn(self):
        with tempfile.TemporaryDirectory() as path:
            _, report, events, reader, collected, _, _, _ = self.invoke(Path(path))
        index = next(i for i, event in enumerate(events) if event[0] == 'collect')
        self.assertIn('rm -f', events[index-1][1])
        self.assertEqual(events[index+1], ('run', 'pidof '+DRIVER.TARGET_PACKAGE))
        self.assertEqual(events[index+2][0], 'native-clock')
        self.assertEqual(events[index+3][0], 'spawn')
        self.assertEqual(collected, 1)
        self.assertTrue(report['source_launch_freshness_verified'])
        self.assertTrue(report['source_final_target_process_absent_verified'])
        self.assertIn('not_reconnect_or_presentation', report['source_freshness_scope'])
        reader.close.assert_called_once()

    def test_target_started_during_collection_is_skipped_without_force_stop(self):
        with tempfile.TemporaryDirectory() as path:
            code, report, events, reader, _, _, _, spawned = self.invoke(Path(path),
                target_started_during_collection=True)
        self.assertEqual((code, spawned), (1, 0))
        self.assertEqual(report['driver_failure_label'], 'target_App_process_active_skip')
        self.assertFalse(report['source_launch_freshness_verified'])
        self.assertFalse(report['source_final_target_process_absent_verified'])
        self.assertNotIn('force-stop', str(events))
        reader.close.assert_called_once()

    def test_failed_source_gate_never_spawns_or_force_stops_target(self):
        error = DRIVER.owner_source_gate.SourceGateError('source_playback_not_playing')
        with tempfile.TemporaryDirectory() as path:
            code, report, events, reader, _, _, _, spawned = self.invoke(Path(path), collect_error=error)
        self.assertEqual((code, spawned), (1, 0))
        self.assertFalse(report['source_launch_freshness_verified'])
        self.assertNotIn('force-stop', str(events))
        reader.close.assert_called_once()

    def test_missing_capture_never_starts_steady_samplers(self):
        error = DRIVER.owner_source_gate.SourceGateError('source_first_capture_missing')
        with tempfile.TemporaryDirectory() as path:
            _, report, events, reader, _, qualified, _, spawned = self.invoke(
                Path(path), loops=2, qualify_error=error)
        self.assertEqual((spawned, qualified), (1, 2))
        self.assertFalse(report['phone_sampler_started'])
        self.assertFalse(report['source_first_capture_freshness_verified'])
        self.assertIn('force-stop', str(events))
        reader.close.assert_called_once()

    def test_invalid_capture_is_fixed_failure_and_stops_only_owned_attempt(self):
        # Use a declared error label rather than leaking arbitrary payload text.
        label = 'source_trace_clock_invalid'
        error = DRIVER.owner_source_gate.SourceGateError(label)
        with tempfile.TemporaryDirectory() as path:
            code, report, events, _, _, qualified, _, spawned = self.invoke(
                Path(path), loops=2, qualify_error=error)
        self.assertEqual((code, spawned, qualified), (1, 1, 1))
        self.assertFalse(report['phone_sampler_started'])
        self.assertIn('force-stop local.huoguo.lanuitest', str(events))
        self.assertIn('force-stop local.remoteandroid.direct.experiment', str(events))

    def test_qualified_first_capture_precedes_steady_samplers_and_is_not_regated(self):
        with tempfile.TemporaryDirectory() as path:
            _, report, events, _, collected, qualified, _, spawned = self.invoke(Path(path), loops=2)
        self.assertEqual((collected, qualified, spawned), (1, 1, 3))
        self.assertTrue(report['phone_sampler_started'])
        self.assertTrue(report['source_first_capture_freshness_verified'])
        first_qualify = next(i for i, event in enumerate(events) if event[0] == 'qualify')
        sampler = next(i for i, event in enumerate(events)
            if event[0] == 'spawn' and event[1][0] != 'adb')
        self.assertLess(first_qualify, sampler)
        # A producer append during read must be compared to the post-read clock,
        # not the earlier deadline-check stamp.
        self.assertEqual(events[first_qualify-2][0], 'prefix-read')
        self.assertEqual(events[first_qualify-1][0], 'native-clock')
        self.assertEqual(events[first_qualify][1][1], events[first_qualify-1][1])
        uid_reads = [event[1] for event in events if event[0] == 'run'
            and event[1].startswith('cmd package list packages')]
        self.assertEqual(uid_reads, ['cmd package list packages --user 0 -U '+DRIVER.TARGET_PACKAGE])

    def test_user0_uid_is_exact_and_never_guesses_from_multiple_users(self):
        command = ['fixture']
        exact = 'package:'+DRIVER.TARGET_PACKAGE+' uid:10316'
        self.assertEqual(DRIVER.target_user0_uid(subprocess.CompletedProcess(
            command, 0, stdout=exact+'\n', stderr='')), '10316')
        for output in (exact+',1010316', exact+'\n'+exact,
                exact.replace(DRIVER.TARGET_PACKAGE, DRIVER.TARGET_PACKAGE+'.test'),
                exact.replace('10316', '999'), exact.replace('10316', '1000000'), ''):
            with self.subTest(output=output), self.assertRaisesRegex(ValueError, '^isolated_App_uid_readback$'):
                DRIVER.target_user0_uid(subprocess.CompletedProcess(command, 0, stdout=output, stderr=''))
        for result in (subprocess.CompletedProcess(command, 1, stdout=exact, stderr=''),
                subprocess.CompletedProcess(command, 0, stdout=exact, stderr='private error'),
                subprocess.CompletedProcess(command, False, stdout=exact, stderr='')):
            with self.assertRaisesRegex(ValueError, '^isolated_App_uid_readback$'):
                DRIVER.target_user0_uid(result)

    def test_reader_cleanup_failure_keeps_primary_and_is_reported(self):
        error = DRIVER.owner_source_gate.SourceGateError('source_first_capture_missing')
        with tempfile.TemporaryDirectory() as path:
            code, report, _, _, _, _, _, _ = self.invoke(Path(path), collect_error=error,
                close_error=OSError('private text must not be exported'))
        self.assertEqual(code, 1)
        self.assertEqual(report['driver_failure_class'], 'SourceGateError')
        self.assertEqual(report['driver_failure_label'], 'source_first_capture_missing')
        self.assertEqual(report['cleanup_failures'],
            [{'operation': 'close_source_trace_prefix', 'failure_class': 'OSError'}])
        self.assertNotIn('private text', json.dumps(report))

    def test_option_requires_explicit_lan_media_identity_and_absolute_trace(self):
        with tempfile.TemporaryDirectory() as path:
            args = self.arguments(Path(path))
            parsed = DRIVER.parse_arguments(args)
            self.assertEqual(parsed.guest, 'emulator-5556')
            bad = [args+['--network-scope', 'tailnet'], args+['--phone-only-sampler'],
                args+['--source-expected-pid', '0'], args+['--source-trace-root', 'relative'],
                args+['--owner-source-guard', 'off'],
                [item for item in args if item != '--media-only']]
            for argv in bad:
                with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    DRIVER.parse_arguments(argv)
