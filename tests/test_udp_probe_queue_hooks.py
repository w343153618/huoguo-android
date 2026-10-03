"""Actual queue source cloned into SDK substitutes; no real codec, phone or service."""
from pathlib import Path
import shutil
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
JDK = Path('/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home/bin')


class QueueHookChecks(unittest.TestCase):
    def test_fec_counter_positions_match_actual_jni_contract(self):
        # The hook uses primitive indices instead of hot-path string lookup.
        # Pin those indices to the separately declared JNI contract so a source
        # reordering cannot quietly attach an exception kind to another counter.
        probe=(ROOT/'experiments/nps-transport/phone/UdpVideoProbe.java').read_text()
        native=(ROOT/'experiments/nps-transport/phone/NativeUdpFec.java').read_text()
        positions=[int(value) for value in re.search(r'FEC_DIAGNOSTIC_STAT_INDEX=\{([^}]+)\}',probe)[1].split(',')]
        names=re.findall(r'"([a-z_]+)"',native.split('STAT_NAMES={',1)[1].split('};',1)[0])
        self.assertEqual([names[index] for index in positions],
            ['frames_expired','reference_lost','dependency_dropped','memory_rejected',
             'clock_mapping_rejected','logical_body_rejected'])

    def test_entry_observations_and_phase_finally_preserve_queue_policy(self):
        source = (ROOT/'experiments/nps-transport/phone/UdpVideoProbe.java').read_text()
        methods = 'private boolean queue(' + source.split('private boolean queue(', 1)[1].split('    private static boolean annexB', 1)[0]
        fixture = '''import java.io.IOException;import java.nio.ByteBuffer;import java.util.concurrent.atomic.AtomicLong;
public final class QueueHookFixture {
 boolean videoWorkerStop;VideoInbox videoInbox;long lastInputNs,videoInputReservationsCancelled,configRecords,queuedMedia;int lastQueueFailure;
'''+methods+'''
 static void check(boolean ok,String label){if(!ok)throw new AssertionError(label);}
 static class VideoFrame {long ptsUs=7,receivedNs,epoch;byte[] body=new byte[32];}
 static class VideoInbox {long epoch;boolean valid(VideoFrame f){return epoch==f.epoch;}long currentEpoch(){return epoch;}}
 static class MediaPresentationMetrics {
  StageDiagnostics decoderStages;int queued;void inputQueued(long pts,long when){queued++;}
  static class StageDiagnostics {
   int guardFlags,reservePolls,reserveClassification,copyStarts,copyEnds,callStarts,callEnds;boolean copySuccess,callSuccess;
   long seenEpoch,currentEpoch;StringBuilder order=new StringBuilder();
   void reserveGuard(long now,long pts,int flags,long seen,long current){guardFlags=flags;seenEpoch=seen;currentEpoch=current;order.append("guard,");}
   void reserve(long now,long pts,long start,long acquired,int polls,boolean config,int failure,int depth,long bytes,long epoch){reservePolls=polls;reserveClassification=failure;order.append("reserve,");}
   void inputCopyStarted(long now,long pts,boolean config){copyStarts++;order.append("copy_start,");}
   void inputCopyFinished(long now,long pts,boolean config,boolean success){copyEnds++;copySuccess=success;order.append("copy_end,");}
   void inputCallStarted(long now,long pts,boolean config){callStarts++;order.append("call_start,");}
   void inputCallFinished(long now,long pts,boolean config,boolean success){callEnds++;callSuccess=success;order.append("call_end,");}
  }
 }
 static class MainActivity {MediaCodec video;boolean running=true;int generation=9;MediaPresentationMetrics presentationMetrics=new MediaPresentationMetrics();AtomicLong receivedVideoBytes=new AtomicLong();}
 static class MediaCodec {
  static final int BUFFER_FLAG_CODEC_CONFIG=2;int dequeues,queues,capacity=64;boolean loseEpoch,throwQueue;QueueHookFixture owner;
  int dequeueInputBuffer(long wait){dequeues++;if(loseEpoch)owner.videoInbox.epoch++;return 0;}
  ByteBuffer getInputBuffer(int index){return ByteBuffer.allocate(capacity);}
  void queueInputBuffer(int index,int offset,int bytes,long pts,int flags)throws IOException{queues++;if(throwQueue)throw new IOException("fixture_sdk_failure");}
 }
 static MainActivity activity(QueueHookFixture owner,boolean diagnostic){MainActivity a=new MainActivity();a.video=new MediaCodec();a.video.owner=owner;
  if(diagnostic)a.presentationMetrics.decoderStages=new MediaPresentationMetrics.StageDiagnostics();return a;}
 static VideoFrame frame(long ageNs,long epoch){VideoFrame f=new VideoFrame();f.receivedNs=System.nanoTime()-ageNs;f.epoch=epoch;return f;}
 public static void main(String[] args)throws Exception{
  QueueHookFixture q=new QueueHookFixture();q.videoInbox=new VideoInbox();q.videoInbox.epoch=2;MainActivity a=activity(q,true);VideoFrame expired=frame(1_000_000_000L,0);
  check(!q.queue(a,9,expired,0,16,true),"expired original queue fails");
  MediaPresentationMetrics.StageDiagnostics d=a.presentationMetrics.decoderStages;
  check(d.guardFlags==11&&d.seenEpoch==0&&d.currentEpoch==2,"entry records simultaneous age stale config");
  check(d.reservePolls==0&&d.reserveClassification==1&&q.lastQueueFailure==1&&a.video.dequeues==0,"polls0 remains existing timeout classification, no SDK wait invented");
  check(d.copyStarts==0&&d.callStarts==0,"unreserved frame has no copy/call sample");
  check(reserveObservedFlags(80,80,2,2,false,false)==1,"deadline boundary exceeded");
  check(reserveObservedFlags(79,80,2,2,true,true)==12,"independent stop config observations");
  check(reserveObservedFlags(1,80,2,2,false,false)==0,"valid entry has no exceptional bits");
  for(boolean diagnostic:new boolean[]{false,true}){
   q=new QueueHookFixture();q.videoInbox=new VideoInbox();a=activity(q,diagnostic);VideoFrame live=frame(-100_000_000L,0);
   check(q.queue(a,9,live,0,16,false)&&q.queuedMedia==1&&q.configRecords==0&&a.receivedVideoBytes.get()==16,"successful admission identical on/off");
   check(a.video.dequeues==1&&a.video.queues==1&&a.presentationMetrics.queued==1,"same SDK calls and input marker on/off");
   if(diagnostic){d=a.presentationMetrics.decoderStages;check(d.guardFlags==0&&d.reservePolls==1&&d.reserveClassification==0,"normal reservation readback");
    check(d.copyStarts==1&&d.copyEnds==1&&d.copySuccess&&d.callStarts==1&&d.callEnds==1&&d.callSuccess,"paired successful copy/call spans");
    check(d.order.toString().equals("guard,reserve,copy_start,copy_end,call_start,call_end,"),"phase ordering");}
  }
  q=new QueueHookFixture();q.videoInbox=new VideoInbox();a=activity(q,true);a.video.loseEpoch=true;
  check(!q.queue(a,9,frame(-100_000_000L,0),0,16,false)&&q.lastQueueFailure==2&&q.videoInputReservationsCancelled==1,"post-reservation stale epoch returns empty reservation");
  d=a.presentationMetrics.decoderStages;check(a.video.queues==1&&a.presentationMetrics.queued==0&&!d.copySuccess&&d.copyEnds==1&&d.callStarts==0,"cancellation is distinct from successful media SDK call");
  q=new QueueHookFixture();q.videoInbox=new VideoInbox();a=activity(q,true);a.video.throwQueue=true;
  try{q.queue(a,9,frame(-100_000_000L,0),0,16,false);throw new AssertionError("SDK throw swallowed");}catch(IOException expected){check(expected.getMessage().equals("fixture_sdk_failure"),"original SDK failure propagates");}
  d=a.presentationMetrics.decoderStages;check(d.copySuccess&&!d.callSuccess&&d.callEnds==1&&q.queuedMedia==0,"failed SDK call retains finally span and original counters");
  q=new QueueHookFixture();q.videoInbox=new VideoInbox();a=activity(q,true);a.video.capacity=8;
  try{q.queue(a,9,frame(-100_000_000L,0),0,16,false);throw new AssertionError("capacity error swallowed");}catch(IOException expected){check(expected.getMessage().equals("codec_input_capacity"),"capacity failure propagates");}
  d=a.presentationMetrics.decoderStages;check(!d.copySuccess&&d.copyEnds==1&&d.callStarts==0&&a.video.queues==0,"capacity fail closes copy span without queue call");
  System.out.println("QueueHookFixture PASS (actual queue source, SDK substitutes, no phone)");
 }
}
'''
        javac = str(JDK/'javac') if (JDK/'javac').exists() else shutil.which('javac')
        java = str(JDK/'java') if (JDK/'java').exists() else shutil.which('java')
        with tempfile.TemporaryDirectory(prefix='huoguo-queue-hook-') as folder:
            path = Path(folder)/'QueueHookFixture.java'
            path.write_text(fixture)
            subprocess.run([javac, '-d', folder, str(path)], check=True, capture_output=True, timeout=30)
            result = subprocess.run([java, '-cp', folder, 'QueueHookFixture'], check=True,
                                    capture_output=True, text=True, timeout=10)
            self.assertIn('PASS', result.stdout)


if __name__ == '__main__':
    unittest.main()
