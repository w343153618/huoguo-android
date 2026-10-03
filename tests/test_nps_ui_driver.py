"""Owned public-UI driver fixtures; no ADB, listener, credential or phone.

The real driver is exercised with fake child processes and explicit reports.
These tests establish selection/readback and sampler scope, not media quality.
"""
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
SPEC = importlib.util.spec_from_file_location('nps_ui_driver', ROOT/'scripts/probes/run_authenticated_lan_ui.py')
DRIVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIVER)


def network_report(node):
    profile = DRIVER.planned_profile(node)
    result = {'requested_network_scope': 'nps_owner', 'requested_node': node}
    for stage in ('first', 'second'):
        result.update({stage+'_actual_network_scope': 'nps_owner', stage+'_actual_node': node,
            stage+'_actual_control_host': profile.public_control.host,
            stage+'_actual_control_port': profile.public_control.port,
            stage+'_actual_media_peer_host': profile.public_media.host,
            stage+'_actual_media_peer_port': profile.public_media.port,
            stage+'_network_readback_verified': True, stage+'_media_transport_code': 1,
            stage+'_media_transport_is_App_UDP_not_NPC_outer_verification': True,
            stage+'_actual_received_frames': 1300, stage+'_actual_codec_callback_count': 1250})
    return result


def complete_report(node):
    result = network_report(node)
    result['requested_credential_save_acceptance']=False
    for stage in ('first','second'):
        for field in ('exit_dialog_shown','exit_repeated_back_same_dialog','exit_continue_preserved_attempt','exit_continue_media_progress','exit_positive_button_clicked','exit_captured_attempt_cancelled','exit_used_actual_UI_buttons'):result[stage+'_'+field]=True
        result.update({stage+'_physical_network_same_lease':True,stage+'_physical_network_handle':123,
            stage+'_physical_network_transport':1,stage+'_physical_https_bind_calls':1,stage+'_physical_udp_bind_calls':1,
            stage+'_physical_packet_route_verified':False,stage+'_physical_domestic_country_verified':False})
    result.update(requested_surface_submit_lead_ms=0, requested_stage_diagnostics_enabled=True,
        requested_codec_startup_ready_enabled=False, requested_steady_seconds=20,
        steady_sampler_completion_observed=True, steady_media_started_ns=1000000000,
        steady_media_finished_ns=23000000000, steady_media_wait_ms=22000,
        steady_progress_monitor_enabled=True, steady_media_progress_healthy=True,
        steady_progress_max_idle_ns=250000000, steady_progress_stall_threshold_ns=3000000000,
        steady_progress_samples=[dict(phone_ns=1000000000+i*1000000000,
            worker_received_frames=100+i*60,codec_callback_count=90+i*60) for i in range(23)])
    for stage in ('first', 'second'):
        result.update({stage+'_surface_submit_lead_ms':0, stage+'_surface_submit_status_code':0,
            stage+'_surface_submit_wait_count':0, stage+'_surface_submit_applications':0,
            stage+'_surface_submit_execution_verified':True, stage+'_stage_diagnostics_enabled':1,
            stage+'_stage_diagnostics_verified':True, stage+'_codec_startup_ready_enabled':0,
            stage+'_codec_startup_readback_verified':True,
            stage+'_codec_startup_gate':dict(enabled=0,phase_before_close=0,failure_code=0,
                ready_ns=0,fresh_received_ns=0,committed_ns=0,bootstrap_pts_us=-1,fresh_pts_us=-1)})
    return result


class NpsUiDriverChecks(unittest.TestCase):
    def test_exact_public_scope_and_node_required_but_legacy_defaults_preserved(self):
        common=['--output','/fake/evidence']
        legacy=DRIVER.parse_arguments(common)
        self.assertEqual((legacy.network_scope,legacy.node,legacy.guest,legacy.phone_only_sampler),
                         ('lan',None,'emulator-5556',False))
        tailnet=DRIVER.parse_arguments([*common,'--network-scope','tailnet'])
        self.assertEqual(tailnet.guest,'emulator-5556')
        for node,serial in (('m1','emulator-5556'),('m5','emulator-5554')):
            args=DRIVER.parse_arguments([*common,'--network-scope','nps_owner','--node',node,
                                        '--media-only','--phone-only-sampler'])
            self.assertEqual((args.node,args.guest),(node,serial))
        for extra in (['--network-scope','nps_owner'], ['--node','m5'],
                      ['--network-scope','tailnet','--node','m1'],
                      ['--network-scope','nps_owner','--node','m5','--media-only'],
                      ['--network-scope','nps_owner','--node','m5','--guest','emulator-5556'],
                      ['--phone-only-sampler']):
            with self.subTest(extra=extra),contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):DRIVER.parse_arguments([*common,*extra])

    def test_actual_public_tuple_readback_requires_both_sessions_and_exact_types(self):
        for node in ('m1','m5'):
            valid=network_report(node)
            self.assertTrue(DRIVER.verify_nps_network_readback(valid,node))
            for key in valid:
                changed=copy.deepcopy(valid);del changed[key]
                self.assertFalse(DRIVER.verify_nps_network_readback(changed,node))
            for key,value in (('first_actual_node','m1' if node=='m5' else 'm5'),
                    ('first_actual_network_scope','lan'),('first_actual_media_peer_host','127.0.0.1'),
                    ('second_actual_control_port',15558),('first_media_transport_code',True),
                    ('second_actual_media_peer_port',float(valid['second_actual_media_peer_port'])),
                    ('first_network_readback_verified',1),('second_actual_received_frames',14),
                    ('second_actual_codec_callback_count',9),('first_actual_received_frames',True)):
                changed=copy.deepcopy(valid);changed[key]=value
                self.assertFalse(DRIVER.verify_nps_network_readback(changed,node))
            valid['failure_class']='IOException'
            self.assertFalse(DRIVER.verify_nps_network_readback(valid,node))
        self.assertFalse(DRIVER.verify_nps_network_readback(network_report('m1'),'m5'))
        self.assertFalse(DRIVER.verify_nps_network_readback({},'m2'))

    def test_M5_phone_only_sampler_does_not_query_local_source_or_claim_M5_SF(self):
        calls=[];spawned=[]
        class Instrumentation:
            returncode=None
            polls=0
            def poll(self):
                self.polls+=1
                if self.polls>1:self.returncode=0
                return self.returncode
            def communicate(self,timeout):
                self.returncode=0
                return 'INSTRUMENTATION_RESULT: numeric_result='+json.dumps(complete_report('m5')),''
        class Sampler:
            returncode=0
            def poll(self):return 0
            def communicate(self,timeout):return b'',b''
        children=[Instrumentation(),Sampler()]
        def spawn(args,**kwargs):spawned.append(args);return children[len(spawned)-1]
        def run(args,**kwargs):
            calls.append(args)
            if args[0]=='lsof':return subprocess.CompletedProcess(args,1,stdout=b'',stderr=b'')
            command=args[-1]
            if 'cmd package list packages' in command:
                return subprocess.CompletedProcess(args,0,stdout='package:local.remoteandroid.direct.experiment uid:12345\n',stderr='')
            if 'test -f ' in command:
                return subprocess.CompletedProcess(args,0 if 'udp-ui-phase-steady-media' in command else 1,
                                                   stdout='',stderr='')
            return subprocess.CompletedProcess(args,0,stdout='{}' if 'cat ' in command else '',stderr='')
        with tempfile.TemporaryDirectory() as folder,contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(DRIVER.sys,'argv',['probe','--output',folder,
                '--network-scope','nps_owner','--node','m5','--phone','owner-test-phone',
                '--media-only','--phone-only-sampler']))
            stack.enter_context(patch.object(DRIVER.subprocess,'run',side_effect=run))
            stack.enter_context(patch.object(DRIVER.subprocess,'Popen',side_effect=spawn))
            stack.enter_context(patch.object(DRIVER.time,'monotonic',side_effect=[0,1]))
            stack.enter_context(patch.object(DRIVER.time,'sleep'))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            code=DRIVER.main();report=json.loads((Path(folder)/'ui-acceptance.json').read_text())
        self.assertEqual(code,0)
        self.assertEqual(len(spawned),2)
        self.assertIn('-e network_scope nps_owner -e node m5',spawned[0][-1])
        self.assertEqual(spawned[1][spawned[1].index('--serial')+1],'owner-test-phone')
        self.assertFalse(any('emulator-5554' in call or 'emulator-5556' in call for call in calls+spawned))
        self.assertTrue(report['nps_network_profile_readback_verified'])
        self.assertFalse(report['source_SF_sampled_by_this_driver'])
        self.assertFalse(report['physical_FPS_acceptance'])
        self.assertFalse(report['NPC_outer_path_verified'])
        self.assertEqual((report['advertised_control_port'],report['advertised_media_port']),(49558,15558))
        self.assertTrue(any('touch '+DRIVER.PRIVATE+'udp-ui-phase-steady-sampled.tmp' in call[-1] for call in calls))
        self.assertTrue(any('udp-test-login.json' in call[-1] and 'rm -f ' in call[-1] for call in calls))
        self.assertFalse(any('force-stop' in call[-1] for call in calls))

    def test_helper_declining_preexisting_phone_session_does_not_force_stop_unowned_App(self):
        calls=[]
        class Declined:
            returncode=0
            def poll(self):return 0
            def communicate(self,timeout):return ('INSTRUMENTATION_RESULT: numeric_result='+json.dumps({
                'failure_class':'IllegalStateException','bounded_failure_label':'existing_UI_attempt_busy'}),'')
        def run(args,**kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args,1 if args[0]=='lsof' else 0,
                stdout=b'' if args[0]=='lsof' else '{}',stderr='')
        with tempfile.TemporaryDirectory() as folder,contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(DRIVER.sys,'argv',['probe','--output',folder,
                '--network-scope','nps_owner','--node','m5','--media-only','--phone-only-sampler']))
            stack.enter_context(patch.object(DRIVER.subprocess,'run',side_effect=run))
            stack.enter_context(patch.object(DRIVER.subprocess,'Popen',return_value=Declined()))
            stack.enter_context(patch.object(DRIVER.time,'monotonic',return_value=0))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            code=DRIVER.main();report=json.loads((Path(folder)/'ui-acceptance.json').read_text())
        self.assertEqual(code,1)
        self.assertTrue(report['existing_phone_UI_attempt_busy_skip'])
        self.assertFalse(any('force-stop' in call[-1] for call in calls))
        self.assertTrue(any('udp-test-login.json' in call[-1] and 'rm -f ' in call[-1] for call in calls))


if __name__=='__main__':unittest.main()
