"""Execute actual helper profile methods with inert widgets; no phone or login.

The driver readback checks parsed settings and real-frame dimensions supplied by
an owned fixture. This does not establish real V50 decoding, FPS or WAN quality.
"""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('v50_ui_driver',ROOT/'scripts/probes/run_authenticated_lan_ui.py')
DRIVER=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(DRIVER)
JDK=Path('/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home/bin')
HELPER=ROOT/'experiments/moonlight-v2/authenticated-lan/LanUiAcceptance.java'


def method(source,marker):
    start=source.index(marker);begin=source.index('{',start);depth=1;end=begin+1
    while depth:
        depth+=(source[end]=='{')-(source[end]=='}');end+=1
    return source[start:end]


def readback(enabled):
    result={'requested_v50_profile':enabled}
    for index,stage in enumerate(('first','second'),1):
        result.update({stage+'_actual_fps_limit':30 if enabled else 60,
            stage+'_actual_buffer_ms':80,stage+'_actual_video_width':540 if enabled else 1080,
            stage+'_actual_video_height':960 if enabled else 1920,
            stage+'_v50_profile_button_clicked':enabled,
            stage+'_v50_profile_button_click_count':index if enabled else 0,
            stage+'_video_profile_readback_verified':True,
            stage+'_video_profile_is_presented_FPS':False})
    return result


class V50DriverChecks(unittest.TestCase):
    def test_default_off_and_explicit_on_keep_bounded_driver_and_effective_button_profile(self):
        default=DRIVER.parse_arguments(['--output','/fake/evidence'])
        self.assertEqual((default.v50_profile,default.rate_index,default.stage_diagnostics),('off',2,'on'))
        selected=DRIVER.parse_arguments(['--output','/fake/evidence','--v50-profile','on','--rate-index','4'])
        self.assertEqual((selected.v50_profile,selected.rate_index,selected.stage_diagnostics,
                          selected.pcm_queue,selected.codec_startup,selected.surface_submit_lead_ms),
                         ('on',0,'off','off','off',0))
        for extra in (['--pcm-queue','on'],['--codec-startup','on'],['--surface-submit-lead-ms','16']):
            with self.subTest(extra=extra),contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                DRIVER.parse_arguments(['--output','/fake/evidence','--v50-profile','on',*extra])

    def test_on_requires_both_actual_preset_clicks_and_parsed_settings_and_decoded_geometry(self):
        valid=readback(True);self.assertTrue(DRIVER.verify_v50_profile_readback(valid,True))
        for key in valid:
            bad=copy.deepcopy(valid);del bad[key]
            with self.subTest(missing=key):self.assertFalse(DRIVER.verify_v50_profile_readback(bad,True))
        for key,value in (('first_actual_fps_limit',60),('second_actual_fps_limit',True),
                ('first_actual_buffer_ms',100),('second_actual_buffer_ms',80.0),
                ('first_actual_video_width',0),('first_actual_video_height',1920),
                ('second_actual_video_width','540'),('first_v50_profile_button_clicked',False),
                ('second_v50_profile_button_click_count',1),('first_v50_profile_button_click_count',True),
                ('second_video_profile_readback_verified',1),('second_video_profile_is_presented_FPS',True)):
            bad=copy.deepcopy(valid);bad[key]=value
            with self.subTest(key=key,value=value):self.assertFalse(DRIVER.verify_v50_profile_readback(bad,True))
        self.assertFalse(DRIVER.verify_v50_profile_readback(dict(valid,failure_class='IOException'),True))
        self.assertFalse(DRIVER.verify_v50_profile_readback(valid,1))

    def test_off_keeps_historical_reports_but_new_report_cannot_claim_preset_or_30fps(self):
        self.assertTrue(DRIVER.verify_v50_profile_readback({},False))
        self.assertFalse(DRIVER.verify_v50_profile_readback({},True))
        valid=readback(False);self.assertTrue(DRIVER.verify_v50_profile_readback(valid,False))
        for key,value in (('first_actual_fps_limit',30),('first_v50_profile_button_clicked',True),
                          ('second_v50_profile_button_click_count',1)):
            self.assertFalse(DRIVER.verify_v50_profile_readback(dict(valid,**{key:value}),False))

    def test_actual_driver_135s_on_consumes_two_readbacks_and_forwards_phone_SF_duration(self):
        from tests.test_nps_ui_driver import complete_report
        ui=complete_report('m5');ui.update(readback(True));ui['requested_stage_diagnostics_enabled']=False
        ui.update(requested_steady_seconds=135,steady_media_started_ns=1000000000,
            steady_media_finished_ns=138000000000,steady_media_wait_ms=137000,
            steady_progress_samples=[dict(phone_ns=1000000000+i*3000000000,
                worker_received_frames=100+i*90,codec_callback_count=90+i*90) for i in range(46)])
        for stage in ('first','second'):ui[stage+'_stage_diagnostics_enabled']=0
        class Instrumentation:
            returncode=None;polls=0
            def poll(self):
                self.polls+=1
                if self.polls>1:self.returncode=0
                return self.returncode
            def communicate(self,timeout):
                self.returncode=0
                return 'INSTRUMENTATION_RESULT: numeric_result='+json.dumps(ui),''
        class Sampler:
            returncode=0
            def poll(self):return 0
            def communicate(self,timeout):return b'',b''
        calls=[];spawned=[];children=[Instrumentation(),Sampler()]
        def spawn(args,**kwargs):spawned.append(args);return children[len(spawned)-1]
        def run(args,**kwargs):
            calls.append(args)
            if args[0]=='lsof':return subprocess.CompletedProcess(args,1,stdout=b'',stderr=b'')
            command=args[-1]
            if command=='pidof '+DRIVER.TARGET_PACKAGE:
                return subprocess.CompletedProcess(args,1,stdout='',stderr='')
            if 'cmd package list packages' in command:
                return subprocess.CompletedProcess(args,0,stdout='package:local.remoteandroid.direct.experiment uid:12345\n',stderr='')
            if 'test -f ' in command:
                return subprocess.CompletedProcess(args,0 if 'udp-ui-phase-steady-media' in command else 1,stdout='',stderr='')
            return subprocess.CompletedProcess(args,0,stdout='{}' if 'cat ' in command else '',stderr='')
        with tempfile.TemporaryDirectory() as folder,contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(DRIVER.sys,'argv',['probe','--output',folder,
                '--network-scope','nps_owner','--node','m5','--media-only','--phone-only-sampler',
                '--v50-profile','on','--steady-seconds','135']))
            stack.enter_context(patch.object(DRIVER.subprocess,'run',side_effect=run))
            stack.enter_context(patch.object(DRIVER.subprocess,'Popen',side_effect=spawn))
            stack.enter_context(patch.object(DRIVER.time,'monotonic',side_effect=[0,1]))
            stack.enter_context(patch.object(DRIVER.time,'sleep'))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            code=DRIVER.main();report=json.loads((Path(folder)/'ui-acceptance.json').read_text())
        self.assertEqual(code,0)
        self.assertTrue(report['v50_profile_readback_verified'])
        self.assertEqual(report['video_target_bps'],4000000)
        self.assertFalse(report['requested_stage_diagnostics_enabled'])
        self.assertIn('-e v50_profile on',spawned[0][-1])
        self.assertIn('-e stage_diagnostics off',spawned[0][-1])
        self.assertIn('-e steady_seconds 135',spawned[0][-1])
        self.assertEqual(spawned[1][spawned[1].index('--seconds')+1],'135')
        self.assertTrue(report['steady_media_progress_verified'])
        self.assertTrue(report['steady_window_readback_verified'])
        self.assertFalse(any('emulator-5554' in call or 'emulator-5556' in call for call in calls+spawned))
        self.assertFalse(report['physical_FPS_acceptance'])

    def test_driver_forwards_exact_switch_and_does_not_wait_for_new_3600s_session_ttl(self):
        source=(ROOT/'scripts/probes/run_authenticated_lan_ui.py').read_text()
        self.assertIn("' -e v50_profile '+args.v50_profile",source)
        self.assertIn("deadline=time.monotonic()+instrumentation_budget_seconds(args.steady_seconds)",source)
        self.assertNotIn('time.monotonic()+3600',source)
        helper=HELPER.read_text()
        self.assertIn('actualFps=(Integer)field(parsed,"fps")',helper)
        self.assertIn('actualBuffer=(Integer)field(parsed,"buffer")',helper)
        self.assertIn('actualWidth=(Integer)field(receiver,"width")',helper)
        self.assertIn('actualHeight=(Integer)field(receiver,"height")',helper)
        self.assertIn('verifyNetworkReadback(target,report,"first")',helper)
        self.assertIn('verifyNetworkReadback(target,report,"second")',helper)


class V50ActualHelperChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder=tempfile.TemporaryDirectory(prefix='huoguo-v50-ui-fixture-')
        cls.java=str(JDK/'java') if (JDK/'java').is_file() else shutil.which('java')
        javac=str(JDK/'javac') if (JDK/'javac').is_file() else shutil.which('javac')
        source=HELPER.read_text()
        methods='\n'.join(method(source,m) for m in ('    private boolean v50Profile(',
            '    private static void selectSpinnerLabel(', '    private void applyVideoProfile(',
            '    private static boolean clickLabel(', '    private static Object field('))
        # Android-qualified view names are rewritten only to their inert twins.
        methods=methods.replace('android.view.ViewGroup','ViewGroup').replace('android.view.View','View')
        harness=r'''
import java.util.*;import java.lang.reflect.Field;
public final class V50HelperCheck {
 final Bundle arguments=new Bundle();boolean lastV50ButtonClicked;int v50ButtonClicks;
 static class Bundle {final Map<String,String> entries=new HashMap<>();String getString(String key,String fallback){return entries.getOrDefault(key,fallback);}}
 static class View {int clicks;void performClick(){clicks++;}}
 static class ViewGroup extends View {final List<View> children=new ArrayList<>();int getChildCount(){return children.size();}View getChildAt(int i){return children.get(i);}}
 static class Button extends View {String label;Runnable listener;Button(String s){label=s;}String getText(){return label;}void performClick(){super.performClick();if(listener!=null)listener.run();}}
 static class Spinner {final String[] labels;int selected=-1,writes;Spinner(String... labels){this.labels=labels;}int getCount(){return labels.length;}Object getItemAtPosition(int i){return labels[i];}void setSelection(int i){if(i<0||i>=labels.length)throw new AssertionError();selected=i;writes++;}}
 static class Ui {Spinner quality=new Spinner("540P","720P","1080P"),rate=new Spinner("4M","8M","12M","16M","24M"),buffer=new Spinner("30ms","50ms","80ms","100ms"),fps;Ui(String... f){fps=new Spinner(f);}}
 static class Window {final ViewGroup decor=new ViewGroup();View getDecorView(){return decor;}}
 static class MainActivity {final Window window=new Window();Window getWindow(){return window;}}
 int rateIndex(){return 2;}
 METHODS
 static void ok(boolean value){if(!value)throw new AssertionError();}
 public static void main(String[] argv)throws Exception{
  V50HelperCheck test=new V50HelperCheck();MainActivity activity=new MainActivity();Ui ui;
  String mode=argv[0];
  if(mode.equals("on")){
   test.arguments.entries.put("v50_profile","on");ui=new Ui("30 FPS · V50 均衡","60 FPS");
   Button button=new Button("真我 V50 · 一键均衡优化");button.listener=()->{ui.quality.selected=0;ui.rate.selected=0;ui.fps.selected=0;ui.buffer.selected=2;};
   ViewGroup nested=new ViewGroup();nested.children.add(button);activity.window.decor.children.add(nested);
   test.applyVideoProfile(activity,ui);ok(button.clicks==1&&test.lastV50ButtonClicked&&test.v50ButtonClicks==1);
   ok(ui.quality.writes==0&&ui.rate.writes==0&&ui.fps.writes==0&&ui.buffer.writes==0&&ui.fps.selected==0);
   test.applyVideoProfile(activity,ui);ok(button.clicks==2&&test.v50ButtonClicks==2);
  }else if(mode.equals("historical")||mode.equals("reordered")){
   ui=mode.equals("historical")?new Ui("60 FPS","120 FPS","30 FPS · V50 均衡"):new Ui("30 FPS · V50 均衡","60 FPS");
   test.applyVideoProfile(activity,ui);ok(ui.fps.labels[ui.fps.selected].equals("60 FPS")&&ui.rate.selected==2&&ui.quality.selected==2&&ui.buffer.selected==2);
   ok(!test.lastV50ButtonClicked&&test.v50ButtonClicks==0);
  }else if(mode.equals("missing_button")){
   test.arguments.entries.put("v50_profile","on");ui=new Ui("30 FPS","60 FPS");
   try{test.applyVideoProfile(activity,ui);throw new AssertionError();}catch(IllegalStateException expected){ok(expected.getMessage().equals("v50_profile_button_missing"));}
   ok(!test.lastV50ButtonClicked&&test.v50ButtonClicks==0&&ui.fps.writes==0);
  }else if(mode.equals("missing_60")){
   ui=new Ui("30 FPS");try{test.applyVideoProfile(activity,ui);throw new AssertionError();}catch(IllegalStateException expected){ok(expected.getMessage().equals("fps_label_missing"));}ok(ui.fps.writes==0);
  }else if(mode.equals("invalid")){
   test.arguments.entries.put("v50_profile","yes");try{test.v50Profile();throw new AssertionError();}catch(IllegalArgumentException expected){}
  }else throw new AssertionError();System.out.println("actual V50 helper methods passed");
 }
}'''.replace(' METHODS',methods)
        path=Path(cls.folder.name)/'V50HelperCheck.java';path.write_text(harness)
        result=subprocess.run([javac,'-d',cls.folder.name,path],capture_output=True,text=True,timeout=30)
        if result.returncode:cls.folder.cleanup();raise AssertionError(result.stdout+result.stderr)

    @classmethod
    def tearDownClass(cls):cls.folder.cleanup()

    def run_case(self,mode):
        result=subprocess.run([self.java,'-cp',self.folder.name,'V50HelperCheck',mode],capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('actual V50 helper methods passed',result.stdout)

    def test_on_uses_real_nested_button_click_and_never_hard_sets_profile_spinners(self):self.run_case('on')
    def test_off_preserves_historical_60fps_list(self):self.run_case('historical')
    def test_off_selects_60fps_after_app_reorders_to_30_60(self):self.run_case('reordered')
    def test_missing_optimize_button_fails_instead_of_manually_emulating_profile(self):self.run_case('missing_button')
    def test_missing_60fps_label_fails_instead_of_wrong_index(self):self.run_case('missing_60')
    def test_invalid_instrumentation_switch_rejected(self):self.run_case('invalid')


if __name__=='__main__':unittest.main()
