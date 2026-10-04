"""Execute helper-only tap transactions against ownership and cleanup failures.

Actual Java class, not a parallel implementation. No device or playback claim.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
JDK = Path('/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home/bin')
SOURCE = ROOT/'experiments/moonlight-v2/authenticated-lan/OwnerSourceTap.java'
HARNESS = r'''
package local.remoteandroid.direct;
import java.util.*;import java.nio.charset.StandardCharsets;
public final class OwnerSourceTapCheck {
 static void ok(boolean b){if(!b)throw new AssertionError();}
 static final class Hooks implements OwnerSourceTap.Hooks {
  boolean owned=true;String mode;int reads;ArrayList<Integer> actions=new ArrayList<>();
  Throwable original;float x,y;long down;Hooks(String mode){this.mode=mode;}
  public boolean owns(){reads++;return owned;}
  public long uptimeMillis(){return 1234;}
  public void pause(long ms)throws Exception{
   ok(ms==35);if(mode.equals("replaced"))owned=false;
   if(mode.equals("interrupt")){original=new InterruptedException();throw (InterruptedException)original;}
  }
  public void dispatch(int action,long time,float x,float y)throws Exception{
   ok(owned);actions.add(action);this.x=x;this.y=y;down=time;
   if(action==0&&(mode.equals("partial")||mode.equals("partial_cancel_failure"))){original=new IllegalStateException("partial");throw (Exception)original;}
   if(action==1&&mode.equals("up_failure")){original=new IllegalStateException("up");throw (Exception)original;}
   if(action==3&&mode.equals("partial_cancel_failure"))throw new AssertionError("cleanup");
  }
 }
 static OwnerSourceTap.Command command(){return OwnerSourceTap.Command.parse("1 77 32768 32768 1080 1920\n".getBytes(StandardCharsets.US_ASCII),1,77);}
 public static void main(String[] args)throws Exception {
  String mode=args[0];
  if(mode.equals("modes")){
   ok(!OwnerSourceTap.enabled("off")&&!OwnerSourceTap.pauseOnly("off"));
   ok(OwnerSourceTap.enabled("native")&&!OwnerSourceTap.pauseOnly("native"));
   ok(OwnerSourceTap.enabled("pause-only")&&OwnerSourceTap.pauseOnly("pause-only"));
   for(String bad:new String[]{null,"", "on", "pause", "native ", "PAUSE-ONLY"}){
    try{OwnerSourceTap.enabled(bad);throw new AssertionError("accepted mode");}catch(IllegalArgumentException expected){}
   }
  }else if(mode.equals("commands")){
   String[] bad={"1 78 1 1 1080 1920\n","2 77 1 1 1080 1920\n","1 77 -1 1 1080 1920\n",
    "1 77 65536 1 1080 1920\n","1 77 1 1 0 1920\n","1 77 1 1 8193 1920\n","1 77 1 1 1080 1920 extra\n",
    "1 77 1 1 1080 1920", "1 77 1 1 1080 1920\n1 77 1 1 1080 1920\n","1 9223372036854775808 1 1 1080 1920\n",
    "1 77 1.5 1 1080 1920\n","1 77 1 1 1080 1920\r\n"};
   for(String raw:bad){try{OwnerSourceTap.Command.parse(raw.getBytes(StandardCharsets.UTF_8),1,77);throw new AssertionError("accepted malformed");}catch(IllegalArgumentException expected){}}
   try{OwnerSourceTap.Command.parse(new byte[129],1,77);throw new AssertionError();}catch(IllegalArgumentException expected){}
   try{OwnerSourceTap.Command.parse(new byte[]{(byte)255},1,77);throw new AssertionError();}catch(IllegalArgumentException expected){}
   try{OwnerSourceTap.Command.parse("1 77 1 1 1080 1920\n".getBytes(),1,0);throw new AssertionError();}catch(IllegalArgumentException expected){}
   ok(OwnerSourceTap.Command.parse("2 77 1 1 1080 1920\n".getBytes(),2,77).phase==2);
  }else{
   Hooks h=new Hooks(mode);OwnerSourceTap.Receipt r=new OwnerSourceTap.Receipt();
   OwnerSourceTap.Geometry g=new OwnerSourceTap.Geometry(1080,2400,540,960);
   if(mode.equals("before_down"))h.owned=false;
   if(mode.equals("aspect"))g=new OwnerSourceTap.Geometry(1080,2400,720,720);
   Throwable thrown=null;try{OwnerSourceTap.run(h,command(),g,r);}catch(Throwable failure){thrown=failure;}
   if(mode.equals("success")){ok(thrown==null&&h.actions.equals(Arrays.asList(0,1))&&r.downReturned&&r.upReturned&&!r.cancelAttempted);ok(Math.abs(h.x-540)<.02&&Math.abs(h.y-1200)<.03&&h.down==1234);}
   else if(mode.equals("before_down")){ok(thrown instanceof IllegalStateException&&h.actions.isEmpty()&&!r.downAttempted&&!r.cancelAttempted);}
   else if(mode.equals("replaced")){ok(thrown instanceof IllegalStateException&&h.actions.equals(Arrays.asList(0))&&r.cancelSkippedForChangedOwner&&!r.upAttempted&&!r.cancelAttempted);}
   else if(mode.equals("aspect")){ok(thrown instanceof IllegalArgumentException&&h.actions.isEmpty()&&h.reads==0);}
   else if(mode.equals("partial")||mode.equals("interrupt")){ok(thrown==h.original&&h.actions.equals(Arrays.asList(0,3))&&r.cancelAttempted&&r.cancelReturned&&!r.upReturned);}
   else if(mode.equals("partial_cancel_failure")){ok(thrown==h.original&&thrown.getSuppressed().length==1&&thrown.getSuppressed()[0] instanceof AssertionError&&r.cancelAttempted&&!r.cancelReturned);}
   else if(mode.equals("up_failure")){ok(thrown==h.original&&h.actions.equals(Arrays.asList(0,1,3))&&r.upAttempted&&!r.upReturned&&r.cancelReturned);}
   else throw new AssertionError("unknown fixture");
  }
  System.out.println("actual owner tap transaction checks passed");
 }
}
'''


class OwnerSourceTapChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix='huoguo-source-tap-')
        cls.java = str(JDK/'java') if (JDK/'java').is_file() else shutil.which('java')
        javac = str(JDK/'javac') if (JDK/'javac').is_file() else shutil.which('javac')
        harness = Path(cls.folder.name)/'OwnerSourceTapCheck.java'
        harness.write_text(HARNESS)
        result = subprocess.run([javac, '-d', cls.folder.name, str(SOURCE), str(harness)],
                                capture_output=True, text=True, timeout=30)
        if result.returncode:
            cls.folder.cleanup()
            raise AssertionError(result.stdout+result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def case(self, name):
        result = subprocess.run([self.java, '-cp', self.folder.name,
                                 'local.remoteandroid.direct.OwnerSourceTapCheck', name],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertIn('actual owner tap transaction checks passed', result.stdout)

    def test_native_pair_is_single_and_maps_only_actual_image_rect(self): self.case('success')
    def test_stale_owner_before_down_never_injects(self): self.case('before_down')
    def test_replacement_before_up_never_cancels_new_owner(self): self.case('replaced')
    def test_partial_down_is_cancelled_only_inside_same_owner(self): self.case('partial')
    def test_interruption_preserves_primary_and_cancels_owned_down(self): self.case('interrupt')
    def test_cleanup_Error_is_suppressed_without_overwriting_primary(self): self.case('partial_cancel_failure')
    def test_up_failure_keeps_failure_and_cancels_partial_gesture(self): self.case('up_failure')
    def test_source_aspect_change_rejected_before_ownership_or_input(self): self.case('aspect')
    def test_numeric_phase_nonce_schema_and_byte_bound_reject_stale_or_malformed(self): self.case('commands')
    def test_closed_modes_preserve_default_and_explicit_one_phase_pause(self): self.case('modes')

    def test_helper_opt_in_retains_auth_default_and_main_thread_identity_checks(self):
        source = (SOURCE.parent/'LanUiAcceptance.java').read_text()
        self.assertIn('arguments.getString("source_input","off")', source)
        for token in ('!node().equals("m1")', '!savedUiCredentials()', 'field(ui,"current")!=ownedAttempt',
                      'field(ownedAttempt,"receiver")==receiver[0]', 'target.generation==appGen[0]',
                      'field(receiver[0],"touchControl")==touch[0]', 'target.screen==surface[0]',
                      'android.system.Os.fstat(fd)', 'O_NOFOLLOW', 'source_command_cleanup',
                      'source_input_native_contact_not_observed', 'remote_playback_verified_by_helper",false'):
            self.assertIn(token, source)
        phase = source.split('private void sourceInputPhase(', 1)[1].split('private void verifyNetworkReadback(', 1)[0]
        self.assertNotIn('setOnTouchListener', phase)
        self.assertNotIn('sendPayload', phase)
        self.assertNotIn('ProcessBuilder', phase)
        self.assertNotIn('Thread.sleep', phase.split('public void dispatch(', 1)[1])


if __name__ == '__main__':
    unittest.main()
