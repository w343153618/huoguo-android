"""Frame-only branch through actual driver/coordinator; fake phone/transport."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.probes import run_authenticated_lan_ui as driver
from scripts.probes import source_frame_coordinator as frame
from tests.test_source_frame_coordinator import READY, helper, observed
from tests.test_source_pause_recovery import recovery


def result():
    value=recovery()
    for key in ('source_phase_1_local_tap','source_phase_1_external_observer_confirmation','source_pause_only_recovery'):
        value.pop(key)
    value.update(helper());value['source_pause_only_recovery']=False
    return value


class FrameDriverTests(unittest.TestCase):
    def options(self,folder):
        return ['--output',folder,'--source-input','frame-only','--network-scope','nps_owner',
            '--node','m1','--media-only','--credential-source','saved-ui','--source-frame-deployment',
            '/private/tmp/inert-frame.json','--v50-profile','on']

    def test_default_off_and_closed_frame_scope_without_cached_Morphe_identity(self):
        self.assertEqual(driver.parse_arguments(['--output','/tmp/inert']).source_input,'off')
        args=driver.parse_arguments(self.options('/tmp/inert'))
        self.assertEqual(args.source_input,'frame-only');self.assertIsNone(args.source_input_identity)
        for extra in (['--node','m5'],['--network-scope','lan'],['--credential-source','private-file'],
                ['--source-input-identity','3470:10235:1952'],['--source-snapshot-deployment','/tmp/old.json'],
                ['--source-frame-deployment','relative'],['--source-input','off']):
            with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                driver.parse_arguments(self.options('/tmp/inert')+extra)
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            driver.parse_arguments(self.options('/tmp/inert')[:-3])

    def case(self,mode='success'):
        calls=[];publications=[];observers=[]
        ui=result()
        class Markers:
            def require_absent(self):pass
            def read(self,name,bound):
                if name.endswith('ready'):return json.dumps(READY).encode()
                if name.endswith('dispatched'):return b'77\n'
                return None
            def publish(self,name,body):publications.append((name,body))
            def cleanup(self):return {'owned_marker_cleanup_confirmed':True,'cleanup_failures':0}
        class Reader:
            status={'executed':True,'channel_closed':True}
            def observe(self,ready):
                observers.append(ready)
                if mode=='observer':raise ValueError('opaque reader failure')
                return observed()
        class Child:
            returncode=0;polls=0
            def poll(self):
                self.polls+=1;return None if self.polls<4 else 0
            def communicate(self,**kwargs):return 'INSTRUMENTATION_RESULT: numeric_result='+json.dumps(ui)+'\n',''
        child=Child()
        def run(argv,**kwargs):
            calls.append(argv);body=argv[-1]
            if argv[0]=='lsof':return subprocess.CompletedProcess(argv,1,b'',b'')
            if body=='pidof '+driver.TARGET_PACKAGE:return subprocess.CompletedProcess(argv,1,'','')
            if body.startswith('cmd package list'):return subprocess.CompletedProcess(argv,0,'package:'+driver.TARGET_PACKAGE+' uid:10234\n','')
            if body.startswith('pm path --user 0 '):return subprocess.CompletedProcess(argv,0,'package:/data/app/'+body.split()[-1]+'/base.apk\n','')
            if 'sha256sum ' in body:
                name='local.huoguo.lanuitest' if 'lanuitest' in body else driver.TARGET_PACKAGE
                sha=frame.HELPER_SHA256 if 'lanuitest' in name else 'd0437e51e8c2d0d27c89458b3a5e6467ee421f25337551d19ecc0992d18ecf08'
                if mode=='helper_pin' and 'lanuitest' in name:sha=driver.source_authenticated_driver.HELPER_SHA256
                return subprocess.CompletedProcess(argv,0,sha+'  /data/app/'+name+'/base.apk\n','')
            if 'cat '+driver.PRIVATE+'udp-app-last-report.json' in body:
                return subprocess.CompletedProcess(argv,0,json.dumps({'audio_cleanup_confirmed':0 if mode=='audio' else 1}),'')
            return subprocess.CompletedProcess(argv,0,'','')
        with tempfile.TemporaryDirectory() as folder,contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(driver.sys,'argv',['probe']+self.options(folder)))
            stack.enter_context(patch.object(driver.subprocess,'run',side_effect=run))
            popen=stack.enter_context(patch.object(driver.subprocess,'Popen',return_value=child))
            stack.enter_context(patch.object(driver.source_phone_markers,'PhoneMarkers',return_value=Markers()))
            stack.enter_context(patch.object(driver.source_grpc_frame,'read_deployment',return_value={}))
            stack.enter_context(patch.object(driver.source_grpc_frame,'Reader',return_value=Reader()))
            stack.enter_context(patch.object(driver.source_remote_observation,'collect',side_effect=AssertionError('no accessibility')))
            stack.enter_context(patch.object(driver.time,'sleep'))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            code=driver.main();report=json.loads((Path(folder)/'ui-acceptance.json').read_text())
        return code,report,calls,popen.call_count,publications,observers

    def test_actual_driver_branch_has_one_attempt_no_UI_no_sampler_no_input_or_reconnect(self):
        code,report,calls,popen,publications,observers=self.case()
        self.assertEqual(code,0);self.assertEqual(popen,1);self.assertEqual(len(observers),1)
        self.assertEqual([body for _,body in publications],[b'READ 77\n',b'77\n'])
        self.assertFalse(report['phone_sampler_started']);self.assertFalse(report['source_SF_sampled_by_this_driver'])
        self.assertIsNone(report['steady_media_progress_verified']);self.assertFalse(report['reconnect_acceptance_exercised'])
        self.assertTrue(report['source_frame_session_cleanup_verified']);self.assertTrue(report['source_recovery_audio_cleanup_verified'])
        self.assertFalse(report['source_frame_observation']['moving_video_or_FPS_verified'])
        self.assertFalse(any('force-stop' in str(call) or 'uiautomator' in str(call) for call in calls))

    def test_observer_failure_waits_owned_helper_cancel_without_forcestopping_later_UI(self):
        code,report,calls,popen,publications,observers=self.case('observer')
        self.assertEqual(code,1);self.assertEqual(popen,1);self.assertEqual(len(observers),1)
        self.assertEqual([body for _,body in publications],[b'READ 77\n'])
        self.assertIn('instrumentation_cleanup_diagnostic',report)
        self.assertFalse(any('force-stop' in str(call) for call in calls));self.assertFalse(report['phone_sampler_started'])

    def test_old_helper_pin_rejects_before_instrumentation_and_bad_actual_audio_cannot_pass(self):
        code,report,calls,popen,publications,observers=self.case('helper_pin')
        self.assertEqual(code,1);self.assertEqual(popen,0);self.assertEqual(publications,[])
        self.assertEqual(observers,[])
        code,report,*_=self.case('audio')
        self.assertEqual(code,1);self.assertFalse(report.get('source_recovery_audio_cleanup_verified',False))

    def test_cleanup_projection_preserves_only_typed_frame_receipt_without_pixels(self):
        row=result();row['raw_pixels']='must not export'
        projected=driver.instrumentation_cleanup_diagnostic(('INSTRUMENTATION_RESULT: numeric_result='+json.dumps(row)+'\n',''))
        self.assertEqual(projected['numeric_result_status'],'valid')
        self.assertEqual(projected['numeric_result']['source_frame_observation'],helper()['source_frame_observation'])
        self.assertNotIn('raw_pixels',projected['numeric_result'])
        row['source_frame_observation']['input_sent']=0
        projected=driver.instrumentation_cleanup_diagnostic(('INSTRUMENTATION_RESULT: numeric_result='+json.dumps(row)+'\n',''))
        self.assertEqual(projected['numeric_result_status'],'malformed')


if __name__=='__main__':unittest.main()
