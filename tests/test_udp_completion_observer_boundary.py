"""Frozen callback ordering fixture, not a phone or media acceptance test.

The harness extracts the actual terminal callback expression and receipt class.
Its Result double provides a deterministic scheduling pause after Java evaluates
the callback receiver and before arguments finish evaluating. No App, helper,
network, native media or resource cleanup implementation is changed or run.
"""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
JDK = Path('/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home/bin')
PROBE = ROOT / 'experiments/nps-transport/phone/UdpVideoProbe.java'
UI = ROOT / 'app/src/udp/java/local/remoteandroid/direct/AuthenticatedLanUdpUi.java'

JSON_STUB = '''package org.json;
public final class JSONObject {
 public JSONObject put(String name,Object value){return this;}
}
'''

HARNESS = r'''
package local.remoteandroid.direct;
import java.lang.reflect.Field;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import org.json.JSONObject;
public final class CallbackInstallBoundary {
 static void check(boolean value,String name){if(!value)throw new AssertionError(name);}
 static final class Result {
  final boolean pause;final CountDownLatch selected=new CountDownLatch(1),permit=new CountDownLatch(1);
  Result(boolean pause){this.pause=pause;}
  boolean containsKey(String key){
   if(pause){selected.countDown();try{check(permit.await(2,TimeUnit.SECONDS),"argument pause bounded");}
    catch(InterruptedException e){throw new AssertionError(e);}}
   return false;
  }
 }
 static final class Session {int seconds=3600;}
 public static void main(String[] args)throws Exception{
  boolean late=args[0].equals("late");
  Object ownerLock=new Object(),capturedOwner=new Object();Object[] currentOwner={capturedOwner};
  UdpVideoProbe p=new UdpVideoProbe();p.result=new Result(late);
  AtomicInteger originalCalls=new AtomicInteger(),observerCalls=new AtomicInteger();
  AtomicInteger actualReceiptConfirmed=new AtomicInteger();
  UdpVideoProbe.AppListener original=(report,failed,receipt)->{
   check(!failed&&report==p.appReport,"original arguments unchanged");
   check(receipt.audioCleanupState==1&&receipt.statisticsStatus==1
    &&receipt.localEndReason==4&&receipt.requestedSeconds==3600,"actual extracted receipt fields");
   actualReceiptConfirmed.incrementAndGet();originalCalls.incrementAndGet();
   synchronized(ownerLock){check(currentOwner[0]==capturedOwner,"captured owner still current");currentOwner[0]=null;}
  };
  UdpVideoProbe.AppListener observer=(report,failed,receipt)->{
   observerCalls.incrementAndGet();original.complete(report,failed,receipt);
  };
  p.appListener=original;
  Field callback=UdpVideoProbe.class.getDeclaredField("appListener");callback.setAccessible(true);
  if(!late)callback.set(p,observer); // Thread.start publishes this prior write.
  Thread runner=new Thread(p::runActualTail,"callback-boundary-fixture");runner.start();
  if(late){
   check(p.result.selected.await(2,TimeUnit.SECONDS),"actual expression selected callback");
   synchronized(ownerLock){
    check(currentOwner[0]==capturedOwner,"owner check passes even after callback selection");
    check(callback.get(p)==original,"installer sees original listener");
    callback.set(p,observer);
    check(callback.get(p)==observer,"replacement readback succeeds");
   }
   p.result.permit.countDown();
  }
  runner.join(2500);check(!runner.isAlive(),"fixture worker finished");
  check(originalCalls.get()==1&&actualReceiptConfirmed.get()==1,"original callback not lost or duplicated");
  check(observerCalls.get()==(late?0:1),"late replacement can miss actual completion");
  check(currentOwner[0]==null,"original ownership cleanup still executed");
  System.out.println("{\"original_calls\":"+originalCalls+",\"observer_calls\":"+observerCalls
   +",\"actual_receipt_fixture_calls\":"+actualReceiptConfirmed+",\"late_install\":"+(late?1:0)+"}");
 }
}
'''


class CompletionObserverBoundaryChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.java = str(JDK / 'java') if (JDK / 'java').is_file() else shutil.which('java')
        javac = str(JDK / 'javac') if (JDK / 'javac').is_file() else shutil.which('javac')
        if not cls.java or not javac:
            raise unittest.SkipTest('Existing JDK required; no installation')
        source = PROBE.read_text()
        receipt = ('    public static final class CompletionReceipt'
                   + source.split('    public static final class CompletionReceipt', 1)[1]
                   .split('    static int closeOwnedAudio(', 1)[0])
        listener = source.split('    public interface AppListener {', 1)[1].split('}', 1)[0]
        tail = ('if(appListener!=null)appListener.complete('
                + source.split('            appListener.complete(', 1)[1]
                .split(';', 1)[0] + ';')
        fixture = '''package local.remoteandroid.direct;
import org.json.JSONObject;
final class UdpVideoProbe {
 private AppListener appListener;
 final JSONObject appReport=new JSONObject();
 CallbackInstallBoundary.Result result;
 final int audioCleanupState=1,completionEndReason=4;
 final Throwable appReportFailure=null;
 final CallbackInstallBoundary.Session session=new CallbackInstallBoundary.Session();
''' + receipt + ' public interface AppListener {' + listener + '}\n' + (
            ' void runActualTail(){' + tail + '}\n}')
        # Test harness sets the same otherwise-private field via reflection;
        # direct initialization here only avoids changing the extracted call.
        harness = HARNESS.replace('p.appListener=original;',
                                  'Field initial=UdpVideoProbe.class.getDeclaredField("appListener");'
                                  'initial.setAccessible(true);initial.set(p,original);')
        cls.folder = tempfile.TemporaryDirectory(prefix='huoguo-callback-install-boundary-')
        cls.addClassCleanup(cls.folder.cleanup)
        folder = Path(cls.folder.name)
        for relative, text in [('UdpVideoProbe.java', fixture),
                               ('CallbackInstallBoundary.java', harness),
                               ('org/json/JSONObject.java', JSON_STUB)]:
            path = folder / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        result = subprocess.run([javac, '-d', str(folder), *map(str, folder.rglob('*.java'))],
                                capture_output=True, text=True, timeout=20)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def case(self, mode):
        result = subprocess.run([self.java, '-cp', self.folder.name,
                                 'local.remoteandroid.direct.CallbackInstallBoundary', mode],
                                capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertLess(len(result.stdout.encode()), 512)
        return json.loads(result.stdout)

    def test_late_reflective_install_can_miss_actual_selected_callback(self):
        value = self.case('late')
        self.assertEqual(value['original_calls'], 1)
        self.assertEqual(value['observer_calls'], 0)
        self.assertEqual(value['actual_receipt_fixture_calls'], 1)

    def test_prior_install_is_published_by_thread_start(self):
        value = self.case('before_start')
        self.assertEqual(value['original_calls'], 1)
        self.assertEqual(value['observer_calls'], 1)

    def test_frozen_source_has_no_late_install_or_observation_contract(self):
        source = PROBE.read_text()
        self.assertIn('private Session appSession; private AppListener appListener;', source)
        self.assertNotIn('volatile AppListener appListener', source)
        self.assertLess(source.index('runner.appListener=listener;'),
                        source.index('new Thread(runner::onStart,"authenticated-lan-udp").start();'))
        authenticate = UI.read_text().split('    private void authenticate(', 1)[1].split(
            '    private static String connectionFailureMessage(', 1)[0]
        self.assertIn('synchronized(lock)', authenticate)
        self.assertIn('attempt.receiver=UdpVideoProbe.startApp(', authenticate)
        self.assertIn('(report,failed,completion)->finished(attempt,report,failed,completion)', authenticate)
        self.assertNotIn('observer', authenticate)


if __name__ == '__main__':
    unittest.main()
