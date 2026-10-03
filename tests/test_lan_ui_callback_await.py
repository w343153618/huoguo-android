"""Execute the helper's actual await method against a queued callback and bounded clock.

This checks instrumentation sequencing, not Android display/exit acceptance.
The Java method is extracted verbatim; the App exit gate is the real class.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
JDK=Path('/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home/bin')


def actual_method(text, marker):
    start=text.index(marker);open_at=text.index('{',start);depth=1;end=open_at+1
    while depth:
        depth += (text[end]=='{')-(text[end]=='}');end+=1
    return text[start:end]


class LanUiCallbackAwaitChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder=tempfile.TemporaryDirectory(prefix='huoguo-ui-callback-')
        cls.java=str(JDK/'java') if (JDK/'java').is_file() else shutil.which('java')
        javac=str(JDK/'javac') if (JDK/'javac').is_file() else shutil.which('javac')
        helper=(ROOT/'experiments/moonlight-v2/authenticated-lan/LanUiAcceptance.java').read_text()
        method=actual_method(helper,'    private static boolean awaitUiCallback(')
        source=r'''
package local.remoteandroid.direct;
import java.util.*;import java.util.function.LongSupplier;
public final class CallbackAwaitCheck {
 interface CallbackPause {void sleep(long ms)throws InterruptedException;}
 static int checks;static void ok(boolean b){checks++;if(!b)throw new AssertionError("check "+checks);}
 static final class Pump implements LongSupplier,CallbackPause{
  long now;int sleeps,conditions;boolean frozen;long fire=Long.MAX_VALUE;Runnable queued;
  public long getAsLong(){return now;}
  public void sleep(long ms){ok(ms>0&&ms<=20);sleeps++;if(!frozen)now+=ms;if(queued!=null&&now>=fire){Runnable r=queued;queued=null;r.run();}}
 }
 METHOD
 public static void main(String[] args)throws Exception{
  String mode=args[0];Pump pump=new Pump();UdpExitConfirmationGate gate=new UdpExitConfirmationGate();Object a=new Object(),b=new Object();Object[] current={a};long[] gen={1};boolean[] cancelled={false};var token=gate.open(1,a);
  java.util.concurrent.Callable<Boolean> exited=()->{pump.conditions++;if(cancelled[0]&&current[0]!=a)return true;if(current[0]!=a||gen[0]!=1)throw new IllegalStateException("stale");return false;};
  if(mode.equals("queued")){pump.fire=40;pump.queued=()->{if(gate.resolve(token,true,gen[0],current[0])==gate.EXIT_CURRENT){cancelled[0]=true;current[0]=null;gen[0]++;}};ok(!cancelled[0]);ok(awaitUiCallback(exited,pump,pump));ok(cancelled[0]&&pump.conditions>=3&&pump.sleeps==2&&pump.now==40);}
  else if(mode.equals("continue")){pump.fire=40;pump.queued=()->gate.resolve(token,false,gen[0],current[0]);ok(awaitUiCallback(()->{pump.conditions++;return !gate.pending();},pump,pump));ok(current[0]==a&&gen[0]==1&&!cancelled[0]&&pump.conditions>=3);}
  else if(mode.equals("timeout")){ok(!awaitUiCallback(exited,pump,pump));ok(!cancelled[0]&&current[0]==a&&pump.now==1500&&pump.conditions<=76&&pump.sleeps<=75);}
  else if(mode.equals("late")){pump.fire=1520;pump.queued=()->{cancelled[0]=true;current[0]=null;};ok(!awaitUiCallback(exited,pump,pump));ok(pump.queued!=null&&!cancelled[0]&&pump.now==1500);}
  else if(mode.equals("stale")){pump.fire=20;pump.queued=()->{current[0]=b;gen[0]=2;};try{awaitUiCallback(exited,pump,pump);throw new AssertionError("stale passed");}catch(IllegalStateException expected){}ok(current[0]==b&&!cancelled[0]);ok(gate.resolve(token,true,gen[0],current[0])==gate.STALE);}
  else if(mode.equals("clock")){long[] readings={100,99};int[] index={0};try{awaitUiCallback(()->false,()->readings[Math.min(index[0]++,1)],pump);throw new AssertionError("clock passed");}catch(IllegalStateException expected){}ok(pump.sleeps==0);}
  else if(mode.equals("frozen")){pump.frozen=true;ok(!awaitUiCallback(exited,pump,pump));ok(pump.conditions==76&&pump.sleeps==76&&!cancelled[0]);}
  else if(mode.equals("slow_condition")){ok(!awaitUiCallback(()->{pump.now=1501;return true;},pump,pump));ok(pump.sleeps==0);}
  else if(mode.equals("interrupt")){try{awaitUiCallback(()->false,pump,ms->{throw new InterruptedException();});throw new AssertionError("interrupted passed");}catch(InterruptedException expected){}ok(!cancelled[0]);}
  else throw new AssertionError("mode");System.out.println("actual callback wait checks passed "+checks);
 }
}'''.replace(' METHOD',method)
        path=Path(cls.folder.name)/'CallbackAwaitCheck.java';path.write_text(source)
        result=subprocess.run([javac,'-d',cls.folder.name,str(ROOT/'app/src/udp/java/local/remoteandroid/direct/UdpExitConfirmationGate.java'),str(path)],capture_output=True,text=True,timeout=30)
        if result.returncode:cls.folder.cleanup();raise AssertionError(result.stdout+result.stderr)
    @classmethod
    def tearDownClass(cls):cls.folder.cleanup()
    def run_case(self,mode):
        result=subprocess.run([self.java,'-cp',self.folder.name,'local.remoteandroid.direct.CallbackAwaitCheck',mode],capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertIn('actual callback wait checks passed',result.stdout)
    def test_perform_click_return_is_not_posted_listener_completion(self):self.run_case('queued')
    def test_continue_waits_for_queued_handler_and_keeps_owned_attempt(self):self.run_case('continue')
    def test_no_callback_times_out_without_claiming_cancelled(self):self.run_case('timeout')
    def test_callback_beyond_original_bound_is_not_observed_or_executed(self):self.run_case('late')
    def test_new_attempt_cannot_be_cancelled_or_counted_as_old_success(self):self.run_case('stale')
    def test_backward_clock_rejected(self):self.run_case('clock')
    def test_poll_limit_bounds_a_stalled_clock(self):self.run_case('frozen')
    def test_slow_state_read_cannot_report_success_after_the_polling_deadline(self):self.run_case('slow_condition')
    def test_interrupted_wait_propagates_and_does_not_cancel(self):self.run_case('interrupt')
    def test_login_insets_only_wrap_scroll_and_leave_inner_and_stream_coordinates(self):
        source=(ROOT/'app/src/udp/java/local/remoteandroid/direct/AuthenticatedLanUdpUi.java').read_text()
        self.assertEqual(source.count('setOnApplyWindowInsetsListener'),1)
        self.assertIn('scroll.setOnApplyWindowInsetsListener',source)
        self.assertIn('insets.getInsets(android.view.WindowInsets.Type.systemBars())',source)
        self.assertIn('view.setPadding(bars.left,bars.top,bars.right,bars.bottom);return insets',source)
        self.assertIn('box.setPadding(32,32,32,32)',source)
        self.assertIn('activity.setContentView(scroll);scroll.requestApplyInsets()',source)
        self.assertIn('给火锅的安卓 · 测试版',source)
        self.assertIn('单次 120 秒',source)
        self.assertNotIn('HTTPS 仅登录；视频、声音、多指触控均走 UDP',source)

if __name__=='__main__':unittest.main()
