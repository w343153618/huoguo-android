"""Actual receipt + UI completion/cancel methods, with narrow offline stubs.
No Android activity/device/media/network or timing/performance acceptance.
"""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
JDK = Path('/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home/bin')
SPEC = importlib.util.spec_from_file_location('hour_json_fixture', ROOT/'tests/test_udp_inbox_numeric_events.py')
JSON = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(JSON)

MAIN = r'''
package local.remoteandroid.direct;
final class MainActivity {
 volatile boolean running;volatile int generation;volatile Object video;
 final UiQueue ui=new UiQueue();final java.io.File directory;
 int reminders;boolean finishing,destroyed;
 MainActivity(java.io.File directory){this.directory=directory;}
 java.io.File getFilesDir(){return directory;}
 boolean isFinishing(){return finishing;}boolean isDestroyed(){return destroyed;}
 static final class UiQueue {
  final java.util.ArrayDeque<Runnable> pending=new java.util.ArrayDeque<>();
  void post(Runnable r){pending.add(r);}void flush(){while(!pending.isEmpty())pending.remove().run();}
 }
}
'''
UI_SHELL = r'''
package local.remoteandroid.direct;
import org.json.JSONObject;import java.io.*;import java.nio.charset.StandardCharsets;import java.net.Socket;
import android.util.AtomicFile;import android.app.AlertDialog;
final class AuthenticatedLanUdpUi {
 final MainActivity activity;final Object lock=new Object();long generation=10;
 Attempt current,retiring;Text status=new Text();int remoteFinishCalls,loginCalls;
 AuthenticatedLanUdpUi(MainActivity a){activity=a;}
 static final class Text {String text="";void setText(String s){text=s;}}
 void showLogin(){loginCalls++;}boolean active(){synchronized(lock){return current!=null&&!current.stopped;}}
 void finishRemote(Attempt a){remoteFinishCalls++;}
'''
HOUR_HARNESS = r'''
package local.remoteandroid.direct;
import org.json.JSONObject;import java.nio.charset.StandardCharsets;
public final class HourCompletionCheck {
 public static void main(String[] args)throws Exception{
  String scenario=args[0],status=args[1];int sourceReason=Integer.parseInt(args[2]),seconds=Integer.parseInt(args[3]);
  boolean reached=Boolean.parseBoolean(args[4]),cancelled=Boolean.parseBoolean(args[5]),runnerFailed=Boolean.parseBoolean(args[6]);
  int audio=Integer.parseInt(args[7]);
  int reason=UdpVideoProbe.CompletionReceipt.fromLocalExit(sourceReason,reached,cancelled,runnerFailed);
  JSONObject raw=new JSONObject().put("session_limit_reached",1).put("requested_seconds",3600).put("session_end_reason_code",1);
  JSONObject accepted=null;Throwable reportFailure=null;
  if(status.equals("accepted"))accepted=UdpVideoProbe.numericAppSummary(raw);
  else if(status.equals("limit")){
   JSONObject bulk=new JSONObject();for(int i=0;i<8000;i++)bulk.put("number_"+i,Long.MAX_VALUE);
   try{UdpVideoProbe.numericAppSummary(raw.put("native_fec",bulk));throw new AssertionError("actual report limit did not reject");}
   catch(java.io.IOException expected){if(!"numeric_app_report_limit".equals(expected.getMessage()))throw expected;reportFailure=expected;}
  }else if(status.equals("invalid"))reportFailure=new IllegalStateException("fixture-only report error");
  else if(!status.equals("unavailable"))throw new AssertionError("status fixture invalid");
  var receipt=UdpVideoProbe.CompletionReceipt.afterReport(audio,accepted,reportFailure,reason,seconds);
  java.nio.file.Path directory=java.nio.file.Files.createTempDirectory("hour-ui-receipt-");
  try{
   MainActivity a=new MainActivity(directory.toFile());AuthenticatedLanUdpUi ui=new AuthenticatedLanUdpUi(a);
   var old=new AuthenticatedLanUdpUi.Attempt(10,"","","","",null,false,0,false,false);ui.current=old;
   if(scenario.equals("cancelled")||scenario.equals("new_generation"))ui.cancelOwned(old,false);
   if(scenario.equals("late_old_after_new")){
    ui.current=new AuthenticatedLanUdpUi.Attempt(++ui.generation,"","","","",null,false,0,false,false);
   }
   ui.finished(old,accepted==null?new JSONObject():accepted,reportFailure!=null,receipt);
   if(scenario.equals("cancel_between"))ui.cancelOwned(old,false);
   if(scenario.equals("new_generation")||scenario.equals("new_start_before_post")){
    if(ui.current!=null||ui.retiring!=null)throw new AssertionError("new start must require completed idle state");
    if(ui.retiring!=null)throw new AssertionError("completed cancelled owner still retiring");
    ui.current=new AuthenticatedLanUdpUi.Attempt(++ui.generation,"","","","",null,false,0,false,false);
   }
   if(scenario.equals("generation_mismatch"))ui.generation++;
   if(scenario.equals("destroyed"))a.destroyed=true;
   if(scenario.equals("finishing"))a.finishing=true;
   a.ui.flush();
   String stored=java.nio.file.Files.readString(directory.resolve("udp-app-last-report.json"),StandardCharsets.UTF_8);
   System.out.println(new JSONObject().put("end_reason",receipt.localEndReason).put("requested_seconds",receipt.requestedSeconds)
    .put("stats_status",receipt.statisticsStatus).put("should_remind",receipt.shouldRemindAfterHour()?1:0)
    .put("reminders",a.reminders).put("status_hour",ui.status.text.contains("休息一下")?1:0)
    .put("current_present",ui.current==null?0:1).put("current_is_old",ui.current==old?1:0)
    .put("retiring_present",ui.retiring==null?0:1).put("retiring_is_old",ui.retiring==old?1:0)
    .put("stored_completion",stored.contains("completion_receipt")?1:0));
  }finally{try(var files=java.nio.file.Files.list(directory)){for(var f:files.toList())java.nio.file.Files.deleteIfExists(f);}java.nio.file.Files.deleteIfExists(directory);}
 }
}
'''


class HourCompletionReasonChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.java = str(JDK/'java') if (JDK/'java').is_file() else shutil.which('java')
        javac = str(JDK/'javac') if (JDK/'javac').is_file() else shutil.which('javac')
        if not cls.java or not javac:
            raise unittest.SkipTest('Existing JDK only')
        cls.folder = tempfile.TemporaryDirectory(prefix='huoguo-hour-receipt-jvm-')
        cls.addClassCleanup(cls.folder.cleanup)
        folder = Path(cls.folder.name)
        probe = (ROOT/'experiments/nps-transport/phone/UdpVideoProbe.java').read_text()
        bound = probe.split('MAX_NATIVE_EVENTS=8192,', 1)[1].split(';', 1)[0]
        nested = '    static final class InboxEvent'+probe.split('    static final class InboxEvent', 1)[1].split('    // App mode is memory-only;', 1)[0]
        numeric = '    static JSONObject numericAppSummary'+probe.split('    static JSONObject numericAppSummary', 1)[1].split('    private static final class Session', 1)[0]
        probe_fixture = 'package local.remoteandroid.direct;import java.util.ArrayDeque;import java.io.IOException;import java.nio.charset.StandardCharsets;import org.json.JSONObject;import org.json.JSONArray;final class UdpVideoProbe {static final int '+bound+';'+nested+numeric+'void cancelApp(){} }'
        ui = (ROOT/'app/src/udp/java/local/remoteandroid/direct/AuthenticatedLanUdpUi.java').read_text()
        attempt = '    private static final class Attempt'+ui.split('    private static final class Attempt', 1)[1].split('    public AuthenticatedLanUdpUi(', 1)[0]
        attempt = attempt.replace('private static final class', 'static final class')
        methods = '    private static boolean cleanupAllowsReconnect'+ui.split('    private static boolean cleanupAllowsReconnect', 1)[1].split('    private void finishRemote(', 1)[0]
        methods = methods.replace('private void finished(', 'void finished(')
        cancel = '    private void cancelOwned'+ui.split('    private void cancelOwned', 1)[1].rsplit('\n}', 1)[0]
        cancel = cancel.replace('private void cancelOwned(', 'void cancelOwned(')
        objects = JSON.OBJECT.replace(' public String toString()', ' public int optInt(String key,int fallback){Object v=opt(key);return v instanceof Number?((Number)v).intValue():fallback;}\n public String toString()')
        files = {
            'UdpVideoProbe.java': probe_fixture,
            'AuthenticatedLanUdpUi.java': UI_SHELL+attempt+methods+cancel+'}',
            'MainActivity.java': MAIN,
            'NpsPhysicalNetwork.java': 'package local.remoteandroid.direct;final class NpsPhysicalNetwork {}',
            'HourCompletionCheck.java': HOUR_HARNESS,
            'org/json/JSONObject.java': objects,
            'org/json/JSONArray.java': JSON.ARRAY,
            'org/json/JsonRender.java': JSON.RENDER,
            'android/util/AtomicFile.java': 'package android.util;import java.io.*;public final class AtomicFile {final File file;public AtomicFile(File f){file=f;}public FileOutputStream startWrite()throws IOException{return new FileOutputStream(file);}public void finishWrite(FileOutputStream out)throws IOException{out.close();}public void failWrite(FileOutputStream out)throws IOException{out.close();}}',
            'android/app/AlertDialog.java': 'package android.app;public final class AlertDialog {public static final class Builder {final Object activity;public Builder(Object a){activity=a;}public Builder setTitle(String s){return this;}public Builder setMessage(String s){return this;}public Builder setPositiveButton(String s,Object o){return this;}public void show(){try{var f=activity.getClass().getDeclaredField("reminders");f.setAccessible(true);f.setInt(activity,f.getInt(activity)+1);}catch(Exception e){throw new AssertionError(e);}}}}',
        }
        for relative, source in files.items():
            path = folder/relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source)
        result = subprocess.run([javac, '-d', str(folder), *map(str, folder.rglob('*.java')),
            str(ROOT/'experiments/nps-transport/phone/CodecStartupGate.java'),
            str(ROOT/'app/src/main/java/local/remoteandroid/direct/MediaPresentationMetrics.java')], capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise AssertionError(result.stdout+result.stderr)

    def case(self, scenario='active', status='accepted', reason=1, seconds=3600, reached=True, cancelled=False, runner_failed=False, audio=1):
        result = subprocess.run([self.java, '-cp', self.folder.name, 'local.remoteandroid.direct.HourCompletionCheck',
            scenario,status,str(reason),str(seconds),str(reached).lower(),str(cancelled).lower(),str(runner_failed).lower(),str(audio)],
            capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertLess(len(result.stdout.encode()),1024)
        return json.loads(result.stdout)

    def test_real_local_hour_is_independent_of_report_acceptance(self):
        for status in ('accepted','limit','invalid','unavailable'):
            with self.subTest(status=status):
                value=self.case(status=status)
                self.assertEqual(value['end_reason'],1)
                self.assertEqual(value['reminders'],1)
                self.assertEqual(value['status_hour'],1)
                self.assertEqual(value['stored_completion'],int(status!='accepted'))

    def test_request_or_report_fields_cannot_fake_local_hour(self):
        for reason,reached,cancelled,failed,expected in [(0,False,False,False,0),(0,False,True,False,4),
            (0,False,False,True,5),(2,False,False,True,2),(3,False,False,True,3),(1,False,False,True,0),
            (0,True,False,False,0),(9,False,False,False,0)]:
            for status in ('accepted','limit','invalid'):
                with self.subTest(reason=reason,reached=reached,status=status):
                    value=self.case(reason=reason,reached=reached,cancelled=cancelled,runner_failed=failed,status=status)
                    self.assertEqual(value['end_reason'],expected)
                    self.assertEqual(value['reminders'],0)

    def test_recorded_receive_reason_precedes_later_cancel_or_error(self):
        for reason,reached,expected in [(1,True,1),(2,False,2),(3,False,3)]:
            value=self.case(reason=reason,reached=reached,cancelled=True,runner_failed=True)
            self.assertEqual(value['end_reason'],expected)
            self.assertEqual(value['reminders'],int(expected==1))

    def test_other_bounded_duration_does_not_trigger_hour_reminder(self):
        for seconds in (0,1,120,3599):
            value=self.case(seconds=seconds)
            self.assertEqual(value['should_remind'],0)
            self.assertEqual(value['reminders'],0)

    def test_audio_cleanup_barrier_precedes_any_reminder(self):
        for state in (0,2):
            value=self.case(status='limit',audio=state)
            self.assertEqual(value['reminders'],0)
            self.assertEqual(value['retiring_is_old'],1)

    def test_cancelled_or_late_old_generation_cannot_remind_new_session(self):
        for scenario in ('cancelled','new_generation','new_start_before_post','late_old_after_new','generation_mismatch','destroyed','finishing'):
            with self.subTest(scenario=scenario):
                value=self.case(scenario=scenario,status='limit')
                self.assertEqual(value['reminders'],0)
                if scenario in ('new_generation','new_start_before_post','late_old_after_new'):
                    self.assertEqual(value['current_present'],1)
                    self.assertEqual(value['current_is_old'],0)
                    self.assertEqual(value['retiring_present'],0)

    def test_clear_before_cancel_cannot_reestablish_completed_retiring_attempt(self):
        # The real finished/cancel methods run in the original bad order. The
        # completed attempt is already atomic, so a later cancel is a no-op.
        value=self.case(scenario='cancel_between')
        self.assertEqual(value['reminders'],1)
        self.assertEqual(value['current_present'],0)
        self.assertEqual(value['retiring_present'],0)

    def test_cancel_before_unknown_close_keeps_barrier_without_reminder(self):
        for state in (0,2):
            value=self.case(scenario='cancelled',status='limit',audio=state)
            self.assertEqual(value['reminders'],0)
            self.assertEqual(value['retiring_is_old'],1)


if __name__=='__main__':
    unittest.main()
