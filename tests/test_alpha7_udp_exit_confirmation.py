"""Actual bounded gate and strict acceptance checks; no phone/GUI/media operation."""
from pathlib import Path
import copy
import importlib.util
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
UDP=ROOT/'app/src/udp/java/local/remoteandroid/direct'
JDK=Path('/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home/bin')
SPEC=importlib.util.spec_from_file_location('alpha7_ui_driver',ROOT/'scripts/probes/run_authenticated_lan_ui.py')
DRIVER=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(DRIVER)


class UdpExitConfirmationChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder=tempfile.TemporaryDirectory(prefix='huoguo-alpha7-exit-')
        cls.java=str(JDK/'java') if (JDK/'java').is_file() else shutil.which('java')
        javac=str(JDK/'javac') if (JDK/'javac').is_file() else shutil.which('javac')
        source=r'''
package local.remoteandroid.direct;
import java.util.concurrent.*;import java.util.concurrent.atomic.*;
public final class ExitCheck{
 static int checks;static void ok(boolean b){checks++;if(!b)throw new AssertionError("check "+checks);}
 public static void main(String[] args)throws Exception{
  UdpExitConfirmationGate gate=new UdpExitConfirmationGate();Object a=new Object(),b=new Object();String mode=args[0];
  if(mode.equals("continue")){var t=gate.open(1,a);ok(t!=null&&gate.pending());ok(gate.resolve(t,false,1,a)==gate.CONTINUE);ok(!gate.pending());ok(gate.resolve(t,true,1,a)==gate.STALE);}
  else if(mode.equals("exit")){var t=gate.open(1,a);ok(gate.resolve(t,true,1,a)==gate.EXIT_CURRENT);ok(!gate.pending());ok(gate.resolve(t,true,1,a)==gate.STALE);}
  else if(mode.equals("stack")){var t=gate.open(1,a);ok(gate.open(1,a)==null);ok(gate.open(2,b)==null);ok(gate.resolve(t,false,1,a)==gate.CONTINUE);ok(gate.open(2,b)!=null);}
  else if(mode.equals("generation")){var t=gate.open(1,a);ok(gate.resolve(t,true,2,a)==gate.STALE);ok(!gate.pending());var n=gate.open(2,b);ok(gate.resolve(t,true,2,b)==gate.STALE);ok(gate.pending());ok(gate.resolve(n,true,2,b)==gate.EXIT_CURRENT);}
  else if(mode.equals("identity")){Object x=new String("same"),y=new String("same");var t=gate.open(3,x);ok(gate.resolve(t,true,3,y)==gate.STALE);var n=gate.open(3,x);ok(gate.resolve(n,true,3,null)==gate.STALE);}
  else if(mode.equals("dismiss")){var t=gate.open(1,a);ok(gate.dismiss(t));ok(!gate.dismiss(t));var n=gate.open(2,b);ok(!gate.dismiss(t)&&gate.pending());ok(gate.resolve(n,true,2,b)==gate.EXIT_CURRENT);ok(!gate.dismiss(null));}
  else if(mode.equals("bound")){try{gate.open(-1,a);throw new AssertionError();}catch(IllegalArgumentException expected){}try{gate.open(1,null);throw new AssertionError();}catch(IllegalArgumentException expected){}ok(!gate.pending());ok(gate.resolve(null,true,1,a)==gate.STALE);}
  else if(mode.equals("concurrency")){int count=16;CountDownLatch start=new CountDownLatch(1),done=new CountDownLatch(count);AtomicInteger winners=new AtomicInteger();AtomicReference<UdpExitConfirmationGate.Token> token=new AtomicReference<>();AtomicReference<Throwable> failure=new AtomicReference<>();for(int i=0;i<count;i++)new Thread(()->{try{start.await();var t=gate.open(5,a);if(t!=null){winners.incrementAndGet();token.set(t);}}catch(Throwable e){failure.set(e);}finally{done.countDown();}}).start();start.countDown();ok(done.await(3,TimeUnit.SECONDS));ok(failure.get()==null&&winners.get()==1);ok(gate.resolve(token.get(),true,5,a)==gate.EXIT_CURRENT);}
  else throw new AssertionError("mode");System.out.println("actual gate checks passed "+checks);
 }
}'''
        path=Path(cls.folder.name)/'ExitCheck.java';path.write_text(source)
        result=subprocess.run([javac,'-d',cls.folder.name,str(UDP/'UdpExitConfirmationGate.java'),str(path)],capture_output=True,text=True,timeout=30)
        if result.returncode:cls.folder.cleanup();raise AssertionError(result.stdout+result.stderr)
    @classmethod
    def tearDownClass(cls):cls.folder.cleanup()
    def gate(self,mode):
        result=subprocess.run([self.java,'-cp',self.folder.name,'local.remoteandroid.direct.ExitCheck',mode],capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertIn('actual gate checks passed',result.stdout)
    def test_continue_never_produces_exit(self):self.gate('continue')
    def test_exit_requires_one_live_token(self):self.gate('exit')
    def test_repeated_back_cannot_stack(self):self.gate('stack')
    def test_old_generation_does_not_cancel_new_attempt(self):self.gate('generation')
    def test_equal_objects_are_not_the_same_owned_attempt(self):self.gate('identity')
    def test_dismissal_cannot_clear_new_token(self):self.gate('dismiss')
    def test_invalid_attempt_and_generation_fail_closed(self):self.gate('bound')
    def test_synchronized_gate_has_one_concurrent_dialog_owner(self):self.gate('concurrency')
    def test_readback_requires_real_buttons_and_media_continuity_in_both_sessions(self):
        valid={stage+'_'+field:True for stage in ('first','second') for field in ('exit_dialog_shown','exit_repeated_back_same_dialog','exit_continue_preserved_attempt','exit_continue_media_progress','exit_positive_button_clicked','exit_captured_attempt_cancelled','exit_used_actual_UI_buttons','exit_UI_callbacks_observed')}
        self.assertTrue(DRIVER.verify_exit_confirmation_readback(valid))
        for key in valid:
            for bad in (False,1,'true',None):
                changed=dict(valid);changed[key]=bad;self.assertFalse(DRIVER.verify_exit_confirmation_readback(changed),key)
            changed=dict(valid);del changed[key];self.assertFalse(DRIVER.verify_exit_confirmation_readback(changed))
        valid['failure_class']='IOException';self.assertFalse(DRIVER.verify_exit_confirmation_readback(valid))
    def test_credential_readback_requires_real_save_reopens_clear_and_no_secret_export(self):
        off={'requested_credential_save_acceptance':False};self.assertTrue(DRIVER.verify_credential_save_readback(off,False))
        on={'requested_credential_save_acceptance':True,'credential_secret_exported':False,'credential_other_package_modified':False,
            **{field:True for field in ('credential_save_used_actual_UI','credential_save_reopen_restored','credential_clear_reopen_empty','credential_final_save_reopen_retained')}}
        self.assertTrue(DRIVER.verify_credential_save_readback(on,True))
        for key in on:
            for bad in (None,1,'true'):
                changed=dict(on);changed[key]=bad;self.assertFalse(DRIVER.verify_credential_save_readback(changed,True),key)
            changed=dict(on);del changed[key];self.assertFalse(DRIVER.verify_credential_save_readback(changed,True))
        self.assertFalse(DRIVER.verify_credential_save_readback(on,False))
        off['credential_save_used_actual_UI']=True;self.assertFalse(DRIVER.verify_credential_save_readback(off,False))
    def test_physical_network_readback_is_strict_and_cannot_assert_country_or_packet_acceptance(self):
        valid={}
        for stage in ('first','second'):
            valid.update({stage+'_physical_network_same_lease':True,stage+'_physical_network_handle':123,
                stage+'_physical_network_transport':1,stage+'_physical_https_bind_calls':1,stage+'_physical_udp_bind_calls':1,
                stage+'_physical_packet_route_verified':False,stage+'_physical_domestic_country_verified':False})
        self.assertTrue(DRIVER.verify_nps_physical_network_binding(valid))
        for key in valid:
            changed=dict(valid);del changed[key];self.assertFalse(DRIVER.verify_nps_physical_network_binding(changed))
            for bad in (None,'true',1.0):
                changed=dict(valid);changed[key]=bad;self.assertFalse(DRIVER.verify_nps_physical_network_binding(changed))
        for key,value in (('first_physical_network_handle',True),('second_physical_network_transport',3),('first_physical_https_bind_calls',0),
                ('second_physical_udp_bind_calls',2),('first_physical_packet_route_verified',True),('second_physical_domestic_country_verified',True)):
            changed=dict(valid);changed[key]=value;self.assertFalse(DRIVER.verify_nps_physical_network_binding(changed))
    def test_ui_and_acceptance_use_owned_capture_and_actual_button_listeners(self):
        source=(UDP/'AuthenticatedLanUdpUi.java').read_text();helper=(ROOT/'experiments/moonlight-v2/authenticated-lan/LanUiAcceptance.java').read_text()
        self.assertIn('exitGate.open(generation,captured)',source);self.assertIn('exitGate.resolve(token,true,generation,current)',source)
        self.assertIn('if(exit)cancelOwned(captured,true)',source)
        self.assertIn('attempt!=expected||generation!=expected.generation',source)
        for text in ('要退出远程连接吗？','继续使用','退出连接'):self.assertIn(text,source)
        self.assertIn('BUTTON_NEGATIVE).performClick()',helper);self.assertIn('BUTTON_POSITIVE).performClick()',helper)
        self.assertIn('boolean continued=awaitUiCallback(',helper);self.assertIn('boolean exited=awaitUiCallback(',helper)
        self.assertEqual(helper.count('throw new IllegalStateException("exit_positive_captured_attempt_changed")'),2)
        self.assertIn('leaveThroughConfirmation(target,report,"first")',helper);self.assertIn('leaveThroughConfirmation(target,report,"second")',helper)
        self.assertIn('lanUdpEntry.requestBack()', (ROOT/'app/src/main/java/local/remoteandroid/direct/MainActivity.java').read_text())
        self.assertIn('default void requestBack(){cancel(true);}', (ROOT/'app/src/main/java/local/remoteandroid/direct/LanUdpEntry.java').read_text())

if __name__=='__main__':unittest.main()
