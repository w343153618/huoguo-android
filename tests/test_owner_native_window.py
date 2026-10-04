"""Inert driver/JVM adapter fixtures; no phone, credential store or listener."""
import contextlib
import copy
import io
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.probes import owner_native_window as window
from scripts.probes import run_authenticated_lan_ui as driver
from scripts.probes.owner_native_diagnostic_preflight import Plan, SIGNER
from tests.test_source_pause_recovery import recovery
from tests.test_lan_ui_saved_credentials import actual_method

ROOT = Path(__file__).resolve().parents[1]


def selection(seconds=5):
    return window.Window(Plan(app_source_commit='3d476566ee8b4f2c9126713f0f3559e637a0958a',
        app_sha256=window.APP_SHA256, app_version_code=40, jni_sha256=window.JNI_SHA256,
        helper_sha256=window.HELPER_SHA256, signer_sha256=SIGNER,
        application_id=driver.TARGET_PACKAGE, helper_application_id='local.huoguo.lanuitest',
        helper_target_package=driver.TARGET_PACKAGE, process_max_seconds=300, sample_seconds=30), seconds)


def helper():
    value = recovery()
    for key in list(value):
        if key.startswith('source_'):
            del value[key]
    value.update(requested_authenticated_source_input=False, requested_network_scope='lan', requested_node='',
        requested_owner_native_window=True, requested_owner_native_window_seconds=5,
        native_window_current_attempt_observed=True, native_window_diagnostic_events_observed=True,
        native_window_no_reconnect=True, native_window_no_source_input=True, native_window_no_UiAutomation=True,
        native_window_server_atomic_hold_verified=False, native_window_is_presented_FPS=False,
        native_window_click_ns=1_000_000_000, native_window_started_ns=3_000_000_000,
        native_window_finished_ns=8_000_000_000, native_window_budget_end_ns=31_000_000_000,
        native_window_close_margin_ns=8_000_000_000, native_window_descriptor_seconds=30,
        native_window_samples=[dict(phone_ns=3_000_000_000+i*250_000_000,
            worker_received_frames=15, codec_callback_count=10) for i in range(21)],
        first_actual_network_scope='lan', first_actual_node='', first_actual_control_host='192.168.9.128',
        first_actual_control_port=45560, first_actual_media_peer_host='192.168.9.128', first_actual_media_peer_port=45963)
    return value


def empty_native():
    value=dict(schema_version=1,available=1,enabled=1,capacity=64,source_ring_capacity=8192,
        native_ring_capacity=256,all_generated_events_exported=1,omitted_rows_validated=1,
        all_pipeline_events_covered=0,timestamp_is_physical_packet_arrival=0,quorum_is_codec_ready=0,
        host_phone_clock_subtraction_valid=0,frame_identity_from_JSON_verified=0,
        source_retained_count=0,exported_count=0,omitted_prefix_following_count=0,generated_count=0,
        native_pending_count=0,native_evicted_count=0,java_evicted_count=0,exported_sequence_contiguous_from_one=1)
    for key in ('event_code', *window.native_frame_progress_analysis.COLUMNS):
        value[key]=[]
    return value


def options(folder):
    return ['probe','--output',folder,'--media-only','--credential-source','saved-ui','--v50-profile','on']


class NativeWindowChecks(unittest.TestCase):
    def test_no_CLI_environment_or_account_opt_in_and_existing_frame_selection_unchanged(self):
        self.assertFalse(driver.parse_arguments(['--output','/tmp/inert']).owner_native_window)
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            driver.parse_arguments(options('/tmp/inert')[1:])
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            driver.parse_arguments(['--output','/tmp/inert','--owner-native-window','single'])
        args=driver.parse_arguments(options('/tmp/inert')[1:],owner_native_window=selection())
        self.assertTrue(args.owner_native_window)
        self.assertEqual((args.guest,args.network_scope,args.node,args.stage_diagnostics),('emulator-5556','lan',None,'off'))
        for extra in (['--network-scope','tailnet'], ['--network-scope','nps_owner','--node','m1'],
                ['--credential-source','private-file'],['--guest','emulator-5554'],['--credential-save','on'],
                ['--phone-only-sampler'],['--source-input','native'],['--surface-submit-lead-ms','16']):
            with self.subTest(extra=extra),contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                driver.parse_arguments(options('/tmp/inert')[1:]+extra,owner_native_window=selection())

    def test_local_plan_exact_new_helper_App_JNI_and_duration_not_old_hashes_or_dict_permission(self):
        chosen=selection()
        for field,wrong in (('helper_sha256','ec805aedd85f9ab282666d1e5fb772b1cce66205ca232aff7b5c57b402a32171'),
                ('app_sha256','a'*64),('jni_sha256','b'*64),('sample_seconds',29)):
            changed=copy.deepcopy(chosen.plan);object.__setattr__(changed,field,wrong)
            with self.assertRaises(ValueError):window.Window(changed)
        for seconds in (0,11,True,5.0):
            with self.assertRaises(ValueError):selection(seconds)
        with self.assertRaises(ValueError):window.Window(chosen.plan.artifacts())

    def test_full_captured_window_accepts_stall_without_claiming_no_faults_or_presented_FPS(self):
        self.assertTrue(window.helper_readback(helper(),selection()))
        for key,wrong in (('native_window_server_atomic_hold_verified',True),
                ('native_window_descriptor_seconds',120),('native_window_close_margin_ns',True),
                ('native_window_budget_end_ns',30_000_000_000),('native_window_finished_ns',7_999_999_999),
                ('native_window_no_reconnect',1),('normal_UI_reconnected_received_media',True)):
            v=helper();v[key]=wrong;self.assertFalse(window.helper_readback(v,selection()),key)
        for wrong in ([],[helper()['native_window_samples'][0]],helper()['native_window_samples'][:20]):
            v=helper();v['native_window_samples']=wrong;self.assertFalse(window.helper_readback(v,selection()))
        for field,wrong in (('phone_ns',True),('worker_received_frames',-1),('codec_callback_count',9),('foreign',1)):
            v=helper();v['native_window_samples'][1][field]=wrong
            self.assertFalse(window.helper_readback(v,selection()),field)

    def test_existing_numeric_export_required_whole_report_cleanup_and_OFF_remain_unknown(self):
        actual=window.numeric_report(dict(audio_cleanup_confirmed=1,native_frame_events_numeric=empty_native()))
        self.assertFalse(actual['same_App_attempt_verified_by_JSON'])
        self.assertEqual(actual['native_retained_rows'],0)
        for value in ({},dict(audio_cleanup_confirmed=True,native_frame_events_numeric=empty_native()),
                      dict(audio_cleanup_confirmed=1),dict(audio_cleanup_confirmed=1,native_frame_events_numeric={'available':0})):
            with self.assertRaises(ValueError):window.numeric_report(value)

    def invoke(self,mode='success'):
        calls=[];spawned=[];ui=helper()
        App=dict(audio_cleanup_confirmed=1,native_frame_events_numeric=empty_native())
        if mode=='report_OFF':App['native_frame_events_numeric']['enabled']=0
        ui['first_App_report_sha256']=hashlib.sha256(json.dumps(App).encode()).hexdigest()
        if mode=='short':ui['native_window_finished_ns']-=1
        if mode=='later':ui['bounded_failure_label']='existing_UI_attempt_busy';ui['failure_class']='IllegalStateException';ui['helper_owned_attempt_started']=False
        class Child:
            returncode=0
            def poll(self):return 0
            def communicate(self,timeout):
                marker='INSTRUMENTATION_RESULT: numeric_result='+json.dumps(ui)+'\n'
                if mode=='ambiguous':marker+=marker
                return marker+'INSTRUMENTATION_CODE: -1\n',''
        def run(command,**kwargs):
            calls.append(command);last=command[-1]
            if command[0]=='lsof':return subprocess.CompletedProcess(command,1,stdout=b'',stderr=b'')
            if last.startswith('pidof '):return subprocess.CompletedProcess(command,1,stdout='',stderr='offline' if mode=='unavailable' else '')
            if 'pm path --user 0 ' in last:
                name='helper' if 'lanuitest' in last else 'app'
                return subprocess.CompletedProcess(command,0,stdout='package:/data/app/'+name+'/base.apk\n',stderr='')
            if 'sha256sum ' in last:
                name='helper' if '/helper/' in last else 'app';digest=window.HELPER_SHA256 if name=='helper' else window.APP_SHA256
                if mode=='old_helper' and name=='helper':digest='0'*64
                return subprocess.CompletedProcess(command,0,stdout=digest+'  /data/app/'+name+'/base.apk\n',stderr='')
            value=copy.deepcopy(App)
            if mode=='report_changed':value['audio_cleanup_confirmed']=0
            return subprocess.CompletedProcess(command,0,stdout=json.dumps(value) if 'cat ' in last else '',stderr='')
        def spawn(command,**kwargs):spawned.append(command);return Child()
        with tempfile.TemporaryDirectory() as folder,contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(driver.sys,'argv',options(folder)))
            stack.enter_context(patch.object(driver.subprocess,'run',side_effect=run))
            stack.enter_context(patch.object(driver.subprocess,'Popen',side_effect=spawn))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            code=driver.main(owner_native_window=selection())
            out=json.loads((Path(folder)/'ui-acceptance.json').read_text())
        return code,out,calls,spawned

    def test_actual_driver_mock_single_process_no_sampler_secret_input_source_or_reconnect(self):
        code,out,calls,spawned=self.invoke()
        self.assertEqual(code,0,out)
        self.assertEqual(len(spawned),1)
        self.assertIn('-e owner_native_window single -e owner_native_window_seconds 5',spawned[0][-1])
        self.assertFalse(out['phone_sampler_started']);self.assertFalse(out['reconnect_acceptance_exercised'])
        self.assertIsNone(out['steady_window_readback_verified'])
        self.assertFalse(any('udp-test-login.json' in c[-1] or 'force-stop' in c[-1] or 'uiautomator' in c[-1] or 'udp-ui-phase-' in c[-1] for c in calls))

    def test_actual_driver_old_helper_and_unavailable_phone_refuse_before_instrument(self):
        for mode in ('old_helper','unavailable'):
            code,out,calls,spawned=self.invoke(mode)
            self.assertEqual(code,1);self.assertEqual(spawned,[])
            self.assertFalse(any('udp-test-login.json' in c[-1] or 'force-stop' in c[-1] for c in calls))

    def test_actual_driver_short_window_OFF_report_and_later_attempt_fail_without_global_stop(self):
        for mode in ('short','report_OFF','later','report_changed','ambiguous'):
            code,out,calls,spawned=self.invoke(mode)
            self.assertEqual(code,1);self.assertEqual(len(spawned),1)
            self.assertFalse(any('force-stop' in c[-1] for c in calls))


class NativeWindowActualJavaChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder=tempfile.TemporaryDirectory(prefix='huoguo-native-window-java-')
        jdk=Path('/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home/bin')
        cls.java=str(jdk/'java') if (jdk/'java').is_file() else shutil.which('java')
        javac=str(jdk/'javac') if (jdk/'javac').is_file() else shutil.which('javac')
        original=(ROOT/'experiments/moonlight-v2/authenticated-lan/LanUiAcceptance.java').read_text()
        budget='\n'.join(actual_method(original,m) for m in ('    private static long nativeWindowBudgetEnd(', '    private boolean ownerNativeWindow(', '    private int nativeWindowSeconds(', '    private int surfaceLeadMsUnchecked('))
        source='''public final class BudgetCheck {
 static final class Args extends java.util.HashMap<String,String>{String getString(String k,String fallback){return getOrDefault(k,fallback);}}
 final Args arguments=new Args();String networkScope(){return arguments.getString("network_scope","lan");}String node(){return arguments.getString("node","");}
 boolean mediaOnly(){return arguments.getString("media_only","false").equals("true");}boolean v50Profile(){return arguments.getString("v50_profile","off").equals("on");}
 boolean credentialSave(){return arguments.getString("credential_save","off").equals("on");}boolean codecStartup(){return arguments.getString("codec_startup","off").equals("on");}
 boolean stageDiagnostics(){return false;}METHODS
 public static void main(String[] args){
  if(args[0].equals("scope")){BudgetCheck t=new BudgetCheck();if(t.ownerNativeWindow())throw new AssertionError("default on");
   t.arguments.put("owner_native_window","single");t.arguments.put("media_only","true");t.arguments.put("v50_profile","on");t.arguments.put("credential_source","saved-ui");if(!t.ownerNativeWindow())throw new AssertionError();
   for(String[] pair:new String[][]{{"network_scope","nps_owner"},{"network_scope","tailnet"},{"node","m5"},{"credential_source","private-file"},{"credential_save","on"},{"source_input","frame-only"},{"pcm_queue","on"},{"surface_submit_lead_ms","16"},{"owner_native_window_seconds","11"},{"owner_native_window","foreign"}}){
    String prior=t.arguments.put(pair[0],pair[1]);try{t.ownerNativeWindow();throw new AssertionError("foreign selection accepted");}catch(IllegalArgumentException expected){}if(prior==null)t.arguments.remove(pair[0]);else t.arguments.put(pair[0],prior);
   }System.out.println("actual helper scope passed");return;}
  if(!args[0].equals("bounds"))throw new AssertionError();
  if(nativeWindowBudgetEnd(1,8_000_000_001L,30,10)!=30_000_000_001L)throw new AssertionError();
  for(long[] v:new long[][]{{0,1,30,5},{10,9,30,5},{1,1,120,5},{1,1,30,0},{1,1,30,11},{1,18_000_000_002L,30,5},{Long.MAX_VALUE,Long.MAX_VALUE,30,5}}){
   try{nativeWindowBudgetEnd(v[0],v[1],(int)v[2],(int)v[3]);throw new AssertionError("unqualified lease accepted");}
   catch(IllegalStateException expected){}
  }System.out.println("actual helper budget passed");
 }
}'''.replace('METHODS',budget)
        path=Path(cls.folder.name)/'BudgetCheck.java';path.write_text(source)
        r=subprocess.run([javac,'-d',cls.folder.name,str(path)],capture_output=True,text=True,timeout=15)
        if r.returncode:cls.folder.cleanup();raise AssertionError(r.stdout+r.stderr)
    @classmethod
    def tearDownClass(cls):cls.folder.cleanup()
    def test_actual_helper_selection_is_default_OFF_and_closed_to_normal_saved_M1_LAN(self):
        r=subprocess.run([self.java,'-cp',self.folder.name,'BudgetCheck','scope'],capture_output=True,text=True,timeout=5)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertIn('actual helper scope passed',r.stdout)

    def test_actual_helper_budget_uses_prior_phone_click_and_refuses_late_short_lease_overflow(self):
        r=subprocess.run([self.java,'-cp',self.folder.name,'BudgetCheck','bounds'],capture_output=True,text=True,timeout=5)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertIn('actual helper budget passed',r.stdout)


if __name__=='__main__':unittest.main()
