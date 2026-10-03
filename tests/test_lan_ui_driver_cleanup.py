"""Owned-process and private-input teardown, with no phone or host operations."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('lan_ui_driver', ROOT/'scripts/probes/run_authenticated_lan_ui.py')
DRIVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIVER)


def with_absent_target(run):
    """These historical cleanup fixtures start with no target App process."""
    def wrapped(command, **kwargs):
        if command[0] == 'adb' and command[-1] == 'pidof '+DRIVER.TARGET_PACKAGE:
            return subprocess.CompletedProcess(command, 1, stdout='', stderr='')
        return run(command, **kwargs)
    return wrapped


class FakeChild:
    def __init__(self, timeouts=0):
        self.timeouts = timeouts
        self.returncode = None
        self.signals = []
        self.waits = []

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
            raise subprocess.TimeoutExpired('owned-child', timeout, output='private output')
        if self.returncode is None:
            self.returncode = -15
        return '', ''


class LanUiDriverCleanupCheck(unittest.TestCase):
    def invoke(self, folder, run, children=(), clock=None):
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(DRIVER.sys, 'argv', ['probe', '--output', str(folder)]))
            stack.enter_context(patch.object(DRIVER.subprocess, 'run', side_effect=with_absent_target(run)))
            spawned = stack.enter_context(patch.object(DRIVER.subprocess, 'Popen', side_effect=children))
            if clock is not None:
                stack.enter_context(patch.object(DRIVER.time, 'monotonic', side_effect=clock))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            exit_code = DRIVER.main()
        return exit_code, json.loads((folder/'ui-acceptance.json').read_text()), spawned.call_count

    def test_gate_timeout_removes_one_use_input_and_records_failed_cleanup(self):
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            if command[0] == 'lsof':
                raise subprocess.TimeoutExpired(command, kwargs['timeout'], output='sensitive output')
            return subprocess.CompletedProcess(command, 7, stdout='sensitive cleanup output', stderr='')
        with tempfile.TemporaryDirectory() as folder:
            code, report, spawned = self.invoke(Path(folder), run)
        self.assertEqual((code, spawned), (1, 0))
        self.assertEqual(report['driver_failure_class'], 'TimeoutExpired')
        self.assertEqual(report['cleanup_failures'], [{'operation':'remove_private_test_input','return_code':7}])
        self.assertIn('udp-test-login.json', calls[1][-1])
        self.assertNotIn('force-stop', calls[1][-1])
        self.assertNotIn('sensitive', json.dumps(report))

    @staticmethod
    def readback(lead):
        report={'requested_surface_submit_lead_ms': lead}
        for stage in ('first', 'second'):
            report.update({stage+'_surface_submit_lead_ms': lead,
                stage+'_surface_submit_status_code': 0 if lead==0 else 1,
                stage+'_surface_submit_wait_count': 0 if lead==0 else 20,
                stage+'_surface_submit_applications': 0 if lead==0 else 10,
                stage+'_surface_submit_execution_verified': True})
        return report

    def test_surface_submit_execution_readback_requires_both_actual_sessions_and_strict_types(self):
        for lead in (0, 16):
            valid=self.readback(lead)
            self.assertTrue(DRIVER.verify_surface_submit_readback(valid,lead))
            for key in valid:
                for value in (None, '16', False, 16.0):
                    changed=copy.deepcopy(valid);changed[key]=value
                    with self.subTest(key=key,value=value):
                        self.assertFalse(DRIVER.verify_surface_submit_readback(changed,lead))
                changed=copy.deepcopy(valid);del changed[key]
                self.assertFalse(DRIVER.verify_surface_submit_readback(changed,lead))
            self.assertFalse(DRIVER.verify_surface_submit_readback(valid,16-lead))
            changed=copy.deepcopy(valid);changed['failure_class']='IOException'
            self.assertFalse(DRIVER.verify_surface_submit_readback(changed,lead))
        for status in (-1,0,2,3):
            changed=self.readback(16);changed['first_surface_submit_status_code']=status
            self.assertFalse(DRIVER.verify_surface_submit_readback(changed,16))
        for field in ('surface_submit_wait_count','surface_submit_applications'):
            changed=self.readback(16);changed['second_'+field]=0
            self.assertFalse(DRIVER.verify_surface_submit_readback(changed,16))
            changed=self.readback(0);changed['first_'+field]=1
            self.assertFalse(DRIVER.verify_surface_submit_readback(changed,0))
        for lead in (True,0.0,8,17,None):
            self.assertFalse(DRIVER.verify_surface_submit_readback(self.readback(16),lead))

    def test_surface_lead_argument_is_forwarded_exactly_and_defaults_to_zero(self):
        for lead in (0,16):
            command=[]
            child=FakeChild(timeouts=2)
            def run(args,**kwargs):
                return subprocess.CompletedProcess(args,1 if args[0]=='lsof' else 0,
                    stdout=b'' if args[0]=='lsof' else '',stderr='')
            def spawn(args,**kwargs):
                command.extend(args);return child
            with tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
                argv=['probe','--output',folder]+(['--surface-submit-lead-ms',str(lead)] if lead else [])
                stack.enter_context(patch.object(DRIVER.sys,'argv',argv))
                stack.enter_context(patch.object(DRIVER.subprocess,'run',side_effect=with_absent_target(run)))
                stack.enter_context(patch.object(DRIVER.subprocess,'Popen',side_effect=spawn))
                stack.enter_context(patch.object(DRIVER.time,'monotonic',side_effect=[0,121]))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                self.assertEqual(DRIVER.main(),1)
                report=json.loads((Path(folder)/'ui-acceptance.json').read_text())
            self.assertIn('-e surface_submit_lead_ms '+str(lead),command[-1])
            self.assertEqual(report['requested_surface_submit_lead_ms'],lead)

    def test_steady_readback_requires_observed_completion_and_untampered_bounded_duration(self):
        for seconds in (20,30,135,150):
            started=1000000000;duration=(seconds+2)*1000
            valid={'requested_steady_seconds':seconds,'steady_sampler_completion_observed':True,
                'steady_media_started_ns':started,'steady_media_finished_ns':started+duration*1000000,
                'steady_media_wait_ms':duration}
            self.assertTrue(DRIVER.verify_steady_window_readback(valid,seconds))
            for key in valid:
                bad=dict(valid);del bad[key]
                self.assertFalse(DRIVER.verify_steady_window_readback(bad,seconds))
            for key,value in (('steady_sampler_completion_observed',False),
                    ('requested_steady_seconds',float(seconds)),('steady_media_started_ns',True),
                    ('steady_media_finished_ns',started-1),('steady_media_wait_ms',duration-1),
                    ('steady_media_wait_ms',duration+1),('steady_media_wait_ms',float('nan')),
                    ('steady_media_wait_ms',float('inf'))):
                bad=dict(valid);bad[key]=value
                self.assertFalse(DRIVER.verify_steady_window_readback(bad,seconds))
        for seconds in (True,20.0,19,151,None):
            self.assertFalse(DRIVER.verify_steady_window_readback(valid,seconds))

    def test_steady_30_argument_is_forwarded_with_a_preserved_finite_budget(self):
        commands=[];child=FakeChild(timeouts=2)
        def run(args,**kwargs):
            return subprocess.CompletedProcess(args,1 if args[0]=='lsof' else 0,
                stdout=b'' if args[0]=='lsof' else '',stderr='')
        def spawn(args,**kwargs):commands.append(args);return child
        with tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(DRIVER.sys,'argv',['probe','--output',folder,'--steady-seconds','30']))
            stack.enter_context(patch.object(DRIVER.subprocess,'run',side_effect=with_absent_target(run)))
            stack.enter_context(patch.object(DRIVER.subprocess,'Popen',side_effect=spawn))
            stack.enter_context(patch.object(DRIVER.time,'monotonic',side_effect=[0,131]))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            self.assertEqual(DRIVER.main(),1)
            report=json.loads((Path(folder)/'ui-acceptance.json').read_text())
        self.assertIn('-e steady_seconds 30',commands[0][-1])
        self.assertEqual(report['requested_steady_seconds'],30)
        self.assertEqual(report['driver_failure_label'],'instrumentation_timeout')

    def test_135s_continuity_uses_finite_global_budget_and_supported_phone_SF_duration(self):
        for seconds,budget in ((20,120),(30,120),(135,225),(150,240)):
            self.assertEqual(DRIVER.instrumentation_budget_seconds(seconds),budget)
            args=DRIVER.parse_arguments(['--output','/fake/evidence','--steady-seconds',str(seconds)])
            self.assertEqual(args.steady_seconds,seconds)
        for invalid in (True,20.0,19,151,None):
            with self.assertRaises(ValueError):DRIVER.instrumentation_budget_seconds(invalid)
        for invalid in ('19','151'):
            with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                DRIVER.parse_arguments(['--output','/fake/evidence','--steady-seconds',invalid])
        sampler=(ROOT/'scripts/probes/measure_surface_cadence.py').read_text()
        self.assertIn('if not 2 <= args.seconds <= 300:',sampler)
        driver=(ROOT/'scripts/probes/run_authenticated_lan_ui.py').read_text()
        self.assertIn("'--seconds',str(args.steady_seconds),'--wait-layer','3'",driver)
        self.assertIn("deadline=time.monotonic()+instrumentation_budget_seconds(args.steady_seconds)",driver)
        helper=(ROOT/'experiments/moonlight-v2/authenticated-lan/LanUiAcceptance.java').read_text()
        self.assertIn('if(value<20||value>150)',helper)
        self.assertIn('rows.length()<48',helper)
        self.assertIn('nextSample=now+sampleIntervalNs',helper)
        self.assertIn('while(true){long now=System.nanoTime()',helper)
        self.assertIn('Thread.sleep(250)',helper)

    def test_135s_monitor_rejects_a_healthy_prefix_without_post120s_progress_coverage(self):
        def rows(step,count):
            return [dict(phone_ns=1000000000+i*step*1000000000,
                worker_received_frames=100+i*step*30,codec_callback_count=90+i*step*30) for i in range(count)]
        report=dict(requested_steady_seconds=135,steady_progress_monitor_enabled=True,
            steady_media_progress_healthy=True,steady_progress_max_idle_ns=500000000,
            steady_progress_stall_threshold_ns=3000000000,steady_progress_samples=rows(3,46))
        self.assertTrue(DRIVER.verify_steady_media_progress(report))
        for samples in (rows(1,48),rows(3,40)):
            with self.subTest(last_ns=samples[-1]['phone_ns']):
                self.assertFalse(DRIVER.verify_steady_media_progress(dict(report,steady_progress_samples=samples)))
        self.assertFalse(DRIVER.verify_steady_media_progress(dict(report,steady_media_progress_healthy=False)))
        self.assertFalse(DRIVER.verify_steady_media_progress(dict(report,requested_steady_seconds=151)))

    def test_135s_instrumentation_forwards_duration_and_times_out_after225s_with_owned_cleanup(self):
        commands=[];child=FakeChild(timeouts=2)
        def run(args,**kwargs):
            return subprocess.CompletedProcess(args,1 if args[0]=='lsof' else 0,
                stdout=b'' if args[0]=='lsof' else '',stderr='')
        def spawn(args,**kwargs):commands.append(args);return child
        with tempfile.TemporaryDirectory() as folder,contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(DRIVER.sys,'argv',['probe','--output',folder,'--steady-seconds','135']))
            stack.enter_context(patch.object(DRIVER.subprocess,'run',side_effect=with_absent_target(run)))
            stack.enter_context(patch.object(DRIVER.subprocess,'Popen',side_effect=spawn))
            stack.enter_context(patch.object(DRIVER.time,'monotonic',side_effect=[0,226]))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            self.assertEqual(DRIVER.main(),1)
            report=json.loads((Path(folder)/'ui-acceptance.json').read_text())
        self.assertIn('-e steady_seconds 135',commands[0][-1])
        self.assertEqual(report['requested_steady_seconds'],135)
        self.assertEqual(report['driver_failure_label'],'instrumentation_timeout')
        self.assertIn('kill',child.signals)
        self.assertTrue(all(timeout<=2 for timeout in child.waits))

    def test_steady_completion_marker_requires_both_successful_children_and_is_removed_on_failure(self):
        for failed in (False,True):
            with self.subTest(failed=failed):
                calls=[];spawned=[]
                class Instrumentation:
                    returncode=None
                    polls=0
                    def poll(self):
                        self.polls+=1
                        if self.polls>1:self.returncode=0
                        return self.returncode
                    def terminate(self):self.returncode=-15
                    def kill(self):self.returncode=-9
                    def communicate(self,timeout):
                        self.returncode=0
                        ui=LanUiDriverCleanupCheck.readback(0)
                        ui["requested_credential_save_acceptance"]=False
                        for stage in ('first','second'):
                            for field in ('exit_dialog_shown','exit_repeated_back_same_dialog','exit_continue_preserved_attempt','exit_continue_media_progress','exit_positive_button_clicked','exit_captured_attempt_cancelled','exit_used_actual_UI_buttons','exit_UI_callbacks_observed'):ui[stage+'_'+field]=True
                        ui.update(requested_stage_diagnostics_enabled=True,
                            first_stage_diagnostics_enabled=1,second_stage_diagnostics_enabled=1,
                            first_stage_diagnostics_verified=True,second_stage_diagnostics_verified=True)
                        ui['requested_codec_startup_ready_enabled']=False
                        for stage in ('first','second'):
                            ui[stage+'_codec_startup_ready_enabled']=0
                            ui[stage+'_codec_startup_readback_verified']=True
                            ui[stage+'_codec_startup_gate']=dict(enabled=0,phase_before_close=0,failure_code=0,
                                ready_ns=0,fresh_received_ns=0,committed_ns=0,bootstrap_pts_us=-1,fresh_pts_us=-1)
                        ui.update(steady_progress_monitor_enabled=True,steady_media_progress_healthy=True,
                            steady_progress_max_idle_ns=250000000,steady_progress_stall_threshold_ns=3000000000,
                            steady_progress_samples=[dict(phone_ns=1000000000+i*1000000000,
                                worker_received_frames=100+i*60,codec_callback_count=90+i*60) for i in range(33)])
                        ui.update(requested_steady_seconds=30,steady_sampler_completion_observed=True,
                            steady_media_started_ns=1000000000,steady_media_finished_ns=33000000000,
                            steady_media_wait_ms=32000)
                        return 'INSTRUMENTATION_RESULT: numeric_result='+json.dumps(ui),' '
                class Sampler:
                    def __init__(self,code):self.returncode=code
                    def poll(self):return self.returncode
                    def communicate(self,timeout):return b'',b''
                children=[Instrumentation(),Sampler(0),Sampler(1 if failed else 0)]
                def spawn(args,**kwargs):spawned.append(args);return children[len(spawned)-1]
                def run(args,**kwargs):
                    calls.append(args)
                    if args[0]=='lsof':return subprocess.CompletedProcess(args,1,stdout=b'',stderr=b'')
                    command=args[-1]
                    if 'cmd package list packages' in command:
                        return subprocess.CompletedProcess(args,0,stdout='package:local.remoteandroid.direct.experiment uid:12345\n',stderr='')
                    if 'test -f ' in command:
                        code=0 if 'udp-ui-phase-steady-media' in command else 1
                        return subprocess.CompletedProcess(args,code,stdout='',stderr='')
                    return subprocess.CompletedProcess(args,0,stdout='{}' if 'cat ' in command else '',stderr='')
                with tempfile.TemporaryDirectory() as folder,contextlib.ExitStack() as stack:
                    stack.enter_context(patch.object(DRIVER.sys,'argv',['probe','--output',folder,'--steady-seconds','30','--media-only']))
                    stack.enter_context(patch.object(DRIVER.subprocess,'run',side_effect=with_absent_target(run)))
                    stack.enter_context(patch.object(DRIVER.subprocess,'Popen',side_effect=spawn))
                    stack.enter_context(patch.object(DRIVER.time,'monotonic',side_effect=[0,1]))
                    stack.enter_context(patch.object(DRIVER.time,'sleep'))
                    stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                    code=DRIVER.main();report=json.loads((Path(folder)/'ui-acceptance.json').read_text())
                published=[call for call in calls if 'touch '+DRIVER.PRIVATE+'udp-ui-phase-steady-sampled.tmp' in call[-1]]
                cleanup=[call for call in calls if 'rm -f ' in call[-1]]
                self.assertIn('udp-ui-phase-steady-sampled',cleanup[-1][-1])
                self.assertIn('udp-test-login.json',cleanup[-1][-1])
                for sampler in spawned[1:]:
                    self.assertEqual(sampler[sampler.index('--seconds')+1],'30')
                if failed:
                    self.assertEqual(code,1)
                    self.assertEqual(report['driver_failure_label'],'steady_sampler_failed')
                    self.assertEqual(published,[])
                    self.assertTrue(any('force-stop local.remoteandroid.direct.experiment' in call[-1] for call in calls))
                else:
                    self.assertEqual(code,0)
                    self.assertEqual(len(published),1)
                    self.assertTrue(report['steady_samplers_completed_before_leave'])
                    self.assertTrue(report['steady_window_readback_verified'])
                    self.assertTrue(report['surface_submit_execution_verified'])

    def test_busy_gate_does_not_stop_any_app_but_removes_input(self):
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, stdout=b'123\n' if command[0]=='lsof' else '', stderr='')
        with tempfile.TemporaryDirectory() as folder:
            code, report, spawned = self.invoke(Path(folder), run)
        self.assertEqual((code, spawned), (1, 0))
        self.assertEqual(report['driver_failure_label'], 'formal_session_active')
        self.assertEqual(len(calls), 2)
        self.assertNotIn('force-stop', calls[-1][-1])

    def test_adb_preflight_error_still_removes_input_without_exception_text(self):
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            if command[0]=='lsof':
                return subprocess.CompletedProcess(command, 1, stdout=b'', stderr=b'')
            if 'test -s' in command[-1]:
                raise RuntimeError('do-not-record-secret')
            raise subprocess.TimeoutExpired(command, kwargs['timeout'], output='more private output')
        with tempfile.TemporaryDirectory() as folder:
            code, report, spawned = self.invoke(Path(folder), run)
        self.assertEqual((code, spawned), (1, 0))
        self.assertEqual(report['driver_failure_class'], 'RuntimeError')
        self.assertNotIn('driver_failure_label', report)
        self.assertEqual(report['cleanup_failures'][0]['failure_class'], 'TimeoutExpired')
        self.assertIn('udp-test-login.json', calls[-1][-1])
        self.assertNotIn('secret', json.dumps(report))

    def test_instrumentation_timeout_stops_only_isolated_packages_and_reaps_after_kill(self):
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 1 if command[0]=='lsof' else 0, stdout=b'' if command[0]=='lsof' else '', stderr='')
        child = FakeChild(timeouts=2)
        with tempfile.TemporaryDirectory() as folder:
            code, report, spawned = self.invoke(Path(folder), run, [child], clock=[0,121])
        self.assertEqual((code, spawned), (1, 1))
        self.assertTrue(report['timeout'])
        self.assertEqual(report['driver_failure_label'], 'instrumentation_timeout')
        stops = [command[-1] for command in calls if 'force-stop' in command[-1]]
        self.assertEqual(stops, ['am force-stop local.huoguo.lanuitest',
                                'am force-stop local.remoteandroid.direct.experiment'])
        self.assertEqual(child.signals, ['terminate','terminate','kill'])
        self.assertEqual(child.waits, [2,2,2])
        self.assertEqual(report['instrumentation_exit_code'], -9)
        self.assertIn('udp-test-login.json', calls[-1][-1])
        self.assertNotIn('private output', json.dumps(report))

    def test_phase_failure_reaps_both_already_started_samplers(self):
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 1 if command[0]=='lsof' else 0,
                                               stdout=b'' if command[0]=='lsof' else '', stderr='')
        children = [FakeChild(), FakeChild(), FakeChild()]
        with tempfile.TemporaryDirectory() as folder:
            code, report, spawned = self.invoke(Path(folder), run, children, clock=[0,1,2,7])
        self.assertEqual((code, spawned), (1, 3))
        self.assertEqual(report['driver_failure_label'], 'guest_receipt_not_focused')
        self.assertTrue(report['phone_sampler_started'])
        for child in children:
            self.assertEqual(child.signals, ['terminate'])
            self.assertEqual(len(child.waits), 1)
        self.assertEqual(report['sampler_exit_codes'], [-15,-15])
        self.assertIn('udp-test-login.json', calls[-1][-1])


if __name__ == '__main__':
    unittest.main()
