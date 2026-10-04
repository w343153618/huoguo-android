"""Actual JVM completion/close policy; no phone, vendor codec, APK or service.

The accepted numeric report is produced by the real formatter. Oversize rejection
is crossed with the actual UI reconnect/payload helpers, independent of report
fields. Audio ownership is separately exercised in the complete real receiver
with narrow Android API substitutes; release errors and blocked workers remain
fail-closed. These checks make no timing/performance/acoustic claims.
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


def fixture_module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT/relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


JSON = fixture_module('completion_json_fixture', 'tests/test_udp_inbox_numeric_events.py')
AUDIO = fixture_module('completion_audio_fixture', 'tests/test_udp_audio_retirement.py')

RECEIPT_HARNESS = r'''
package local.remoteandroid.direct;
import java.lang.reflect.Method;import org.json.JSONObject;
public final class CompletionReceiptCheck {
 static final class Owned implements UdpVideoProbe.AudioCleanup {
  final int state;final boolean throwsClose,throwsState;int closeCalls;
  Owned(int state,boolean close,boolean read){this.state=state;throwsClose=close;throwsState=read;}
  public void close(){closeCalls++;if(throwsClose)throw new IllegalStateException("fixture-only release error");}
  public int cleanupState(){if(throwsState)throw new IllegalStateException("fixture-only read error");return state;}
 }
 static JSONObject source()throws Exception{
  return new JSONObject().put("video_input_queue",new UdpVideoProbe.VideoInbox().snapshot())
    .put("video_worker_alive",false).put("video_worker_join_timed_out",false)
    .put("audio_cleanup_confirmed",1).put("credentials","MUST_NOT_EXPORT");
 }
 static boolean allowed(UdpVideoProbe.CompletionReceipt c)throws Exception{
  Method m=AuthenticatedLanUdpUi.class.getDeclaredMethod("cleanupAllowsReconnect",UdpVideoProbe.CompletionReceipt.class);
  m.setAccessible(true);return (Boolean)m.invoke(null,c);
 }
 static JSONObject payload(JSONObject report,UdpVideoProbe.CompletionReceipt c)throws Exception{
  Method m=AuthenticatedLanUdpUi.class.getDeclaredMethod("completedReportPayload",JSONObject.class,UdpVideoProbe.CompletionReceipt.class);
  m.setAccessible(true);return (JSONObject)m.invoke(null,report,c);
 }
 public static void main(String[] args)throws Exception{
  int state=Integer.parseInt(args[0]);String status=args[1];boolean throwsClose=args[2].equals("close"),throwsRead=args[2].equals("read");
  Owned owned=new Owned(state,throwsClose,throwsRead);
  int actual=args[2].equals("absent")?UdpVideoProbe.closeOwnedAudio(null):UdpVideoProbe.closeOwnedAudio(owned);
  JSONObject accepted=null;Throwable failure=null;
  if(status.equals("accepted"))accepted=UdpVideoProbe.numericAppSummary(source());
  else if(status.equals("limit")){
   JSONObject bulk=new JSONObject();for(int i=0;i<8000;i++)bulk.put("number_"+i,Long.MAX_VALUE);
   try{accepted=UdpVideoProbe.numericAppSummary(source().put("native_fec",bulk));throw new AssertionError("actual 64KiB rejection missing");}
   catch(java.io.IOException expected){if(!"numeric_app_report_limit".equals(expected.getMessage()))throw expected;failure=expected;}
  }else if(status.equals("invalid"))failure=new IllegalArgumentException("MUST_NOT_EXPORT_ERROR_DETAILS");
  else if(!status.equals("unavailable"))throw new AssertionError("fixture case invalid");
  UdpVideoProbe.CompletionReceipt receipt=UdpVideoProbe.CompletionReceipt.afterReport(actual,accepted,failure);
  JSONObject supplied=accepted==null?source():accepted;
  JSONObject stored=payload(supplied,receipt);
  System.out.println(new JSONObject().put("close_calls",owned.closeCalls).put("receipt",receipt.numeric())
   .put("reconnect_allowed",allowed(receipt)?1:0).put("stored",stored)
   .put("accepted_payload_identity",stored==accepted?1:0).put("stored_bytes",stored.toString().getBytes(java.nio.charset.StandardCharsets.UTF_8).length)
   .put("null_receipt_allowed",allowed(null)?1:0).put("null_receipt_payload",payload(source(),null)));
 }
}
'''

AUDIO_HARNESS = r'''
package local.remoteandroid.direct;
import android.media.MediaCodec;import android.media.AudioTrack;import java.lang.reflect.Field;
import java.util.concurrent.ArrayBlockingQueue;import java.util.concurrent.CountDownLatch;import java.util.concurrent.TimeUnit;
public final class ActualAudioCompletionCheck {
 static Object get(Object o,String name)throws Exception{Field f=UdpAudioReceiver.class.getDeclaredField(name);f.setAccessible(true);return f.get(o);}
 static void set(Object o,String name,Object value)throws Exception{Field f=UdpAudioReceiver.class.getDeclaredField(name);f.setAccessible(true);f.set(o,value);}
 static void check(boolean b,String message){if(!b)throw new AssertionError(message);}
 static UdpAudioReceiver receiver(boolean bounded)throws Exception{
  MainActivity a=new MainActivity();a.running=true;a.generation=4;return new UdpAudioReceiver(a,4,bounded);
 }
 @SuppressWarnings("unchecked")static void media(UdpAudioReceiver r){try{
  long at=System.nanoTime();((ArrayBlockingQueue<UdpAudioAssembler.Frame>)get(r,"queue")).offer(new UdpAudioAssembler.Frame(1,100000,at,at,false,new byte[]{1,2}));
 }catch(Exception e){throw new AssertionError(e);}}
 public static void main(String[] args)throws Exception{
  String mode=args[0];boolean bounded=args[1].equals("bounded");UdpAudioReceiver r=receiver(bounded);
  int before=r.cleanupState();check(before==0,"no confirmation before close");
  if(mode.equals("success")){
   MediaCodec c=new MediaCodec();AudioTrack t=new AudioTrack();set(r,"decoder",c);set(r,"output",t);
   int state=UdpVideoProbe.closeOwnedAudio(r);check(state==1&&c.releases==1&&t.releases==1,"actual resources release before confirmation");
   check(UdpVideoProbe.closeOwnedAudio(r)==1&&c.releases==1&&t.releases==1,"idempotent successful close");
   System.out.println("{\"before\":"+before+",\"after\":"+state+",\"releases\":2}");
  }else if(mode.equals("codec_error")||mode.equals("track_error")){
   MediaCodec c=new MediaCodec();AudioTrack t=new AudioTrack();c.releaseFailure=mode.equals("codec_error");t.releaseFailure=mode.equals("track_error");
   set(r,"decoder",c);set(r,"output",t);int state=UdpVideoProbe.closeOwnedAudio(r);
   check(state==0&&c.releases==1&&t.releases==1,"release uncertainty never confirms audio");
   check(UdpVideoProbe.closeOwnedAudio(r)==0,"release uncertainty is sticky and cannot be erased by empty pointers");
   System.out.println("{\"before\":"+before+",\"after\":"+state+",\"releases\":2}");
  }else if(mode.equals("unpublished_codec_error")||mode.equals("unpublished_track_error")||mode.equals("unpublished_clean")){
   MediaCodec next=new MediaCodec();AudioTrack nextTrack=new AudioTrack();MediaCodec.nextCreated=next;
   boolean trackError=mode.equals("unpublished_track_error");
   boolean releaseError=!mode.equals("unpublished_clean");
   boolean fatal=args.length>2&&args[2].equals("fatal");
   next.configureFailure=!trackError;next.releaseFailure=!trackError&&releaseError&&!fatal;next.releaseFatal=!trackError&&releaseError&&fatal;
   if(trackError){nextTrack.playFailure=true;nextTrack.releaseFailure=!fatal;nextTrack.releaseFatal=fatal;AudioTrack.nextCreated=nextTrack;}
   long now=System.nanoTime();
   @SuppressWarnings("unchecked")ArrayBlockingQueue<UdpAudioAssembler.Frame> q=(ArrayBlockingQueue<UdpAudioAssembler.Frame>)get(r,"queue");
   q.offer(new UdpAudioAssembler.Frame(2,0,now,now,true,new byte[]{0x11,(byte)0x90}));
   ((Thread)get(r,"worker")).join(2000);
   check(!((Thread)get(r,"worker")).isAlive(),"real input worker finishes failed unpublished configure");
   check(next.releases==(trackError&&fatal?0:1)&&(!trackError||nextTrack.releases==1),"actual unpublished release follows original Error propagation");
   check(get(r,"decoder")==null&&get(r,"output")==null,"failed configure did not publish resources");
   int state=UdpVideoProbe.closeOwnedAudio(r);
   check(state==(releaseError?0:1),"failed unpublished release stays unknown; successful cleanup may confirm");
   check(UdpVideoProbe.closeOwnedAudio(r)==state,"retry empty close cannot clear unpublished release uncertainty");
   System.out.println("{\"before\":"+before+",\"after\":"+state+",\"unpublished_release_calls\":"+(next.releases+(trackError?nextTrack.releases:0))+"}");
  }else if(mode.equals("input_blocked")){
   check(bounded,"safe retained-resource timeout uses existing bounded PCM lifecycle");
   MediaCodec c=new MediaCodec();AudioTrack t=new AudioTrack();c.blockInput=true;set(r,"decoder",c);set(r,"output",t);media(r);
   check(c.inputEntered.await(2,TimeUnit.SECONDS),"actual worker entered substitute native input");
   int first=UdpVideoProbe.closeOwnedAudio(r);check(first==2&&c.releases==0&&t.releases==0,"incomplete input retains resources");
   c.inputPermit.countDown();((Thread)get(r,"worker")).join(2000);
   int second=UdpVideoProbe.closeOwnedAudio(r);check(second==1&&c.releases==1&&t.releases==1&&c.queuedAfterRelease==0,"later actual quiescence retires resources");
   System.out.println("{\"before\":"+before+",\"after\":"+first+",\"after_retry\":"+second+"}");
  }else if(mode.equals("drain_blocked")){
   CountDownLatch entered=new CountDownLatch(1),permit=new CountDownLatch(1);
   Thread held=new Thread(()->{entered.countDown();while(permit.getCount()!=0)try{permit.await();}catch(InterruptedException ignored){}});
   held.start();check(entered.await(1,TimeUnit.SECONDS),"drain fixture entered");set(r,"drain",held);
   int first=UdpVideoProbe.closeOwnedAudio(r);check(first==2,"live drain cannot confirm even absent decoder pointers");
   permit.countDown();held.join(1000);int second=UdpVideoProbe.closeOwnedAudio(r);check(second==1,"quiescent drain permits confirmation");
   System.out.println("{\"before\":"+before+",\"after\":"+first+",\"after_retry\":"+second+"}");
  }else if(mode.equals("unknown")){
   UdpVideoProbe.closeOwnedAudio(r);set(r,"closeFinished",false);
   check(r.cleanupState()==0,"incomplete close invocation stays unknown");
   System.out.println("{\"before\":"+before+",\"after\":0}");
  }else throw new AssertionError("fixture mode invalid");
 }
}
'''


class CompletionReceiptChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.java = str(JDK/'java') if (JDK/'java').is_file() else shutil.which('java')
        cls.javac = str(JDK/'javac') if (JDK/'javac').is_file() else shutil.which('javac')
        if not cls.java or not cls.javac:
            raise unittest.SkipTest('Existing JDK required; no toolchain installation')
        cls.folders = [tempfile.TemporaryDirectory(prefix='huoguo-completion-receipt-'),
                       tempfile.TemporaryDirectory(prefix='huoguo-actual-audio-cleanup-')]
        cls.addClassCleanup(lambda: [folder.cleanup() for folder in cls.folders])
        probe = (ROOT/'experiments/nps-transport/phone/UdpVideoProbe.java').read_text()
        policy = '    interface AudioCleanup'+probe.split('    interface AudioCleanup', 1)[1].split('    // App mode is memory-only;', 1)[0]
        folder = Path(cls.folders[0].name)
        nested = '    static final class InboxEvent'+probe.split('    static final class InboxEvent', 1)[1].split('    // App mode is memory-only;', 1)[0]
        numeric = '    static JSONObject numericAppSummary'+probe.split('    static JSONObject numericAppSummary', 1)[1].split('    private static final class Session', 1)[0]
        bound = probe.split('MAX_NATIVE_EVENTS=8192,', 1)[1].split(';', 1)[0]
        cls.write(folder, 'UdpVideoProbe.java', 'package local.remoteandroid.direct;import java.util.ArrayDeque;import java.io.IOException;'
                  'import java.nio.charset.StandardCharsets;import org.json.JSONObject;import org.json.JSONArray;'
                  'final class UdpVideoProbe {static final int '+bound+';'+nested+numeric+'}')
        ui = (ROOT/'app/src/udp/java/local/remoteandroid/direct/AuthenticatedLanUdpUi.java').read_text()
        helpers = '    private static boolean cleanupAllowsReconnect'+ui.split('    private static boolean cleanupAllowsReconnect', 1)[1].split('    private void finished(', 1)[0]
        cls.write(folder, 'AuthenticatedLanUdpUi.java', 'package local.remoteandroid.direct;import org.json.JSONObject;final class AuthenticatedLanUdpUi {'+helpers+'}')
        cls.write(folder, 'MainActivity.java', 'package local.remoteandroid.direct;final class MainActivity {volatile boolean running;volatile int generation;volatile Object video;}')
        for name, source in [('JSONObject', JSON.OBJECT), ('JSONArray', JSON.ARRAY), ('JsonRender', JSON.RENDER)]:
            cls.write(folder, 'org/json/'+name+'.java', source)
        cls.write(folder, 'CompletionReceiptCheck.java', RECEIPT_HARNESS)
        cls.compile(folder, [ROOT/'experiments/nps-transport/phone/CodecStartupGate.java',
                            ROOT/'app/src/main/java/local/remoteandroid/direct/MediaPresentationMetrics.java'])
        audio_folder = Path(cls.folders[1].name)
        for relative, source in AUDIO.STUBS.items():
            if relative in ('android/media/MediaCodec.java', 'android/media/AudioTrack.java'):
                source = source.replace('public volatile int releases;', 'public volatile int releases;public boolean releaseFailure;')
                source = source.replace('public volatile boolean blockInput;', 'public boolean releaseFailure;public volatile boolean blockInput;')
                source = source.replace('public boolean releaseFailure;', 'public boolean releaseFatal;public boolean releaseFailure;')
                source = source.replace('public void release(){releases++;}', 'public void release(){releases++;if(releaseFatal)throw new AssertionError("fixture-only release Error");if(releaseFailure)throw new IllegalStateException("fixture-only release uncertainty");}')
                if relative == 'android/media/MediaCodec.java':
                    source = source.replace('public boolean releaseFailure;', 'public boolean configureFailure;public boolean releaseFailure;')
                    source = source.replace('public void configure(MediaFormat f,Object surface,Object crypto,int flags){}',
                        'public void configure(MediaFormat f,Object surface,Object crypto,int flags){if(configureFailure)throw new IllegalStateException("fixture-only configure failure");}')
                else:
                    source = source.replace('public boolean releaseFailure;', 'public boolean playFailure;public boolean releaseFailure;public static volatile AudioTrack nextCreated;')
                    source = source.replace('public void play(){}', 'public void play(){if(playFailure)throw new IllegalStateException("fixture-only play failure");}')
                    source = source.replace('public AudioTrack build(){return new AudioTrack();}',
                        'public AudioTrack build(){AudioTrack t=nextCreated;nextCreated=null;return t==null?new AudioTrack():t;}')
            cls.write(audio_folder, relative, source)
        cls.write(audio_folder, 'local/remoteandroid/direct/UdpVideoProbe.java', 'package local.remoteandroid.direct;import org.json.JSONObject;final class UdpVideoProbe {'+policy+'}')
        cls.write(audio_folder, 'ActualAudioCompletionCheck.java', AUDIO_HARNESS)
        cls.compile(audio_folder, [ROOT/path for path in ('experiments/nps-transport/phone/UdpAudioReceiver.java',
                'app/src/main/java/local/remoteandroid/direct/OwnerMediaObservation.java',
                     'experiments/nps-transport/phone/UdpAudioAssembler.java', 'experiments/nps-transport/phone/BoundedPcmQueue.java',
                     'app/src/main/java/local/remoteandroid/direct/PlaybackClock.java',
                     'app/src/main/java/local/remoteandroid/direct/AudioSubmissionClock.java', 'app/src/main/java/local/remoteandroid/direct/PcmGain.java')])

    @classmethod
    def write(cls, folder, relative, source):
        path = folder/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)

    @classmethod
    def compile(cls, folder, additional):
        result = subprocess.run([cls.javac, '-d', str(folder), *map(str, folder.rglob('*.java')), *map(str, additional)],
                                capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise AssertionError(result.stdout+result.stderr)

    def run_case(self, state, status, close_mode='normal'):
        return self.run_java(0, 'CompletionReceiptCheck', str(state), status, close_mode)

    def run_java(self, index, harness, *args):
        result = subprocess.run([self.java, '-cp', self.folders[index].name,
                                 'local.remoteandroid.direct.'+harness, *args], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertLess(len(result.stdout.encode()), 4096)
        return json.loads(result.stdout)

    def test_statistics_status_never_overrides_real_cleanup_state(self):
        for state in (0, 1, 2):
            for status, expected in [('accepted', 1), ('limit', 2), ('invalid', 3), ('unavailable', 0)]:
                with self.subTest(state=state, status=status):
                    value = self.run_case(state, status)
                    self.assertEqual(value['receipt']['audio_cleanup_state'], state)
                    self.assertEqual(value['receipt']['statistics_report_status'], expected)
                    self.assertEqual(value['reconnect_allowed'], int(state == 1))
                    self.assertEqual(value['null_receipt_allowed'], 0)
                    self.assertEqual(value['close_calls'], 1)

    def test_actual_report_limit_is_explicit_numeric_receipt_not_truncated_statistics(self):
        value = self.run_case(1, 'limit')
        self.assertEqual(set(value['stored']), {'completion_receipt'})
        self.assertEqual(value['stored']['completion_receipt'], value['receipt'])
        self.assertEqual(value['receipt']['statistics_report_accepted'], 0)
        self.assertLess(value['stored_bytes'], 256)
        self.assertNotIn('audio_cleanup_confirmed', value['stored'])
        self.assertNotIn('MUST_NOT_EXPORT', json.dumps(value))

    def test_accepted_report_object_and_bytes_unchanged(self):
        value = self.run_case(1, 'accepted')
        self.assertEqual(value['accepted_payload_identity'], 1)
        self.assertNotIn('completion_receipt', value['stored'])
        self.assertEqual(value['stored']['audio_cleanup_confirmed'], 1)
        self.assertNotIn('credentials', value['stored'])

    def test_absent_audio_is_confirmed_but_close_error_or_unknown_return_is_not(self):
        self.assertEqual(self.run_case(2, 'limit', 'absent')['reconnect_allowed'], 1)
        for mode, state in [('close', 1), ('read', 1), ('normal', 77)]:
            with self.subTest(mode=mode, state=state):
                self.assertEqual(self.run_case(state, 'accepted', mode)['receipt']['audio_cleanup_state'], 0)

    def test_actual_receiver_close_success_and_release_uncertainty(self):
        for mode in ('success', 'codec_error', 'track_error', 'unknown'):
            for lifecycle in ('legacy', 'bounded'):
                with self.subTest(mode=mode, lifecycle=lifecycle):
                    value = self.run_java(1, 'ActualAudioCompletionCheck', mode, lifecycle)
                    self.assertEqual(value['before'], 0)
                    self.assertEqual(value['after'], int(mode == 'success'))

    def test_actual_unpublished_configure_resource_release_uncertainty_is_sticky(self):
        for mode in ('unpublished_codec_error', 'unpublished_track_error', 'unpublished_clean'):
            for lifecycle in ('legacy', 'bounded'):
                for error_kind in ('exception', 'fatal'):
                    with self.subTest(mode=mode, lifecycle=lifecycle, error_kind=error_kind):
                        value = self.run_java(1, 'ActualAudioCompletionCheck', mode, lifecycle, error_kind)
                        self.assertEqual(value['before'], 0)
                        self.assertEqual(value['after'], int(mode == 'unpublished_clean'))
                        # A track Error preserves original propagation and
                        # may stop before unpublished codec release. The whole
                        # attempt stays UNKNOWN, never falsely confirmed.
                        expected = 2 if mode == 'unpublished_track_error' and error_kind == 'exception' else 1
                        self.assertEqual(value['unpublished_release_calls'], expected)

    def test_actual_receiver_live_workers_remain_incomplete_until_quiescent(self):
        for mode, lifecycle in [('input_blocked', 'bounded'), ('drain_blocked', 'legacy'), ('drain_blocked', 'bounded')]:
            with self.subTest(mode=mode, lifecycle=lifecycle):
                value = self.run_java(1, 'ActualAudioCompletionCheck', mode, lifecycle)
                self.assertEqual(value['after'], 2)
                self.assertEqual(value['after_retry'], 1)


if __name__ == '__main__':
    unittest.main()
