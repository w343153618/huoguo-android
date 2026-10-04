"""Actual helper-only Java observation ordering, without Android/device I/O."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'experiments/moonlight-v2/authenticated-lan/OwnerSourceFrame.java'
JDK = Path('/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home/bin')
HARNESS = r'''
package local.remoteandroid.direct;
import java.nio.charset.StandardCharsets;
public final class FrameCheck {
 static void ok(boolean value){if(!value)throw new AssertionError();}
 static final class Hooks implements OwnerSourceFrame.Hooks {
  int checks,publish,confirm;String mode;Throwable original;
  Hooks(String mode){this.mode=mode;}
  public boolean owns(){checks++;return !(mode.equals("before")||mode.equals("after")&&checks==2);}
  public void published(byte[] body){ok(new String(body,StandardCharsets.US_ASCII).equals("77\n"));publish++;
   if(mode.equals("publish")){original=new AssertionError("publish");throw (Error)original;}}
  public byte[] confirmation()throws Exception{confirm++;
   if(mode.equals("confirm")){original=new InterruptedException();throw (Exception)original;}
   return (mode.equals("nonce")?"78\n":"77\n").getBytes(StandardCharsets.US_ASCII);}
 }
 public static void main(String[] args)throws Exception{
  String mode=args[0];Hooks h=new Hooks(mode);OwnerSourceFrame.Receipt r=new OwnerSourceFrame.Receipt();
  if(mode.equals("schema")){
   String[] bad={"READ 78\n","READ 077\n","READ 77", "READ 77\r\n", "READ 77\nREAD 77\n",
    "READ 77 extra\n","1 77 32768 32768 1080 1920\n","KEY 77\n","READ -77\n","READ 77 \n"};
   for(String value:bad){try{OwnerSourceFrame.observe(value.getBytes(StandardCharsets.US_ASCII),1,77,h,r);throw new AssertionError();}
    catch(IllegalArgumentException expected){}}
   for(int phase:new int[]{0,2}){try{OwnerSourceFrame.observe("READ 77\n".getBytes(),phase,77,h,r);throw new AssertionError();}
    catch(IllegalArgumentException expected){}}
   for(byte[] raw:new byte[][]{null,new byte[0],new byte[33],new byte[]{(byte)255}}){
    try{OwnerSourceFrame.observe(raw,1,77,h,r);throw new AssertionError();}catch(IllegalArgumentException expected){}}
   try{OwnerSourceFrame.observe("READ 77\n".getBytes(),1,0,h,r);throw new AssertionError();}catch(IllegalArgumentException expected){}
   ok(h.checks==0&&h.publish==0&&h.confirm==0);return;
  }
  Throwable failure=null;try{OwnerSourceFrame.observe("READ 77\n".getBytes(),1,77,h,r);}catch(Throwable e){failure=e;}
  if(mode.equals("success"))ok(failure==null&&r.ownedBefore&&r.publicationReturned&&r.confirmationMatched&&r.ownedAfter&&h.checks==2&&h.publish==1&&h.confirm==1);
  else if(mode.equals("before"))ok(failure instanceof IllegalStateException&&!r.ownedBefore&&!r.publicationAttempted&&h.publish==0&&h.confirm==0);
  else if(mode.equals("after"))ok(failure instanceof IllegalStateException&&r.ownedBefore&&r.confirmationMatched&&!r.ownedAfter&&h.publish==1&&h.confirm==1);
  else if(mode.equals("nonce"))ok(failure instanceof IllegalStateException&&!r.confirmationMatched&&!r.ownedAfter&&h.checks==1);
  else if(mode.equals("publish"))ok(failure==h.original&&r.publicationAttempted&&!r.publicationReturned&&h.confirm==0&&!r.ownedAfter);
  else if(mode.equals("confirm"))ok(failure==h.original&&r.publicationReturned&&!r.confirmationMatched&&!r.ownedAfter&&h.checks==1);
  else throw new AssertionError();
 }
}
'''


class OwnerSourceFrameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix='huoguo-source-frame-')
        cls.java = str(JDK/'java') if (JDK/'java').is_file() else shutil.which('java')
        javac = str(JDK/'javac') if (JDK/'javac').is_file() else shutil.which('javac')
        harness = Path(cls.folder.name)/'FrameCheck.java'
        harness.write_text(HARNESS)
        result = subprocess.run([javac,'-d',cls.folder.name,str(SOURCE),str(harness)],
                                capture_output=True,text=True,timeout=20)
        if result.returncode:
            cls.folder.cleanup()
            raise AssertionError(result.stdout+result.stderr)

    @classmethod
    def tearDownClass(cls): cls.folder.cleanup()

    def case(self, name):
        result = subprocess.run([self.java,'-cp',self.folder.name,
                                'local.remoteandroid.direct.FrameCheck',name],
                               capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_owned_noninput_observation_checks_both_ends(self): self.case('success')
    def test_changed_owner_before_never_publishes(self): self.case('before')
    def test_replacement_during_external_read_cannot_confirm_old_owner(self): self.case('after')
    def test_foreign_nonce_cannot_complete_phase(self): self.case('nonce')
    def test_publication_Error_propagates_without_wait_or_retry(self): self.case('publish')
    def test_confirmation_interrupt_preserves_primary_without_retry(self): self.case('confirm')
    def test_closed_noninput_command_rejects_coordinate_key_replay_and_bounds(self): self.case('schema')


if __name__=='__main__': unittest.main()
