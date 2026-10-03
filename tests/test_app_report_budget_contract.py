"""Exact integer JSON budget of actual App formatters; not phone/ART overhead.

No source/runtime policy edits, ADB, credentials, APK, codec or network. The
fixture composes the actual bounded module formatters and cross-checks every
accepted byte count with actual numericAppSummary. Rejected cases keep size
evidence but never change the production 64 KiB check or truncation behavior.
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
SPEC = importlib.util.spec_from_file_location('budget_json_substitutes', ROOT/'tests/test_udp_inbox_numeric_events.py')
JSON = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(JSON)

HARNESS = r'''
package local.remoteandroid.direct;
import java.nio.charset.StandardCharsets;import org.json.JSONObject;import org.json.JSONArray;
public final class AppReportBudgetCheck {
 static final long ORIGIN=300_000_000_000_000L;
 static int bytes(JSONObject value){return value.toString().getBytes(StandardCharsets.UTF_8).length;}
 static JSONObject inbox(int count,boolean wide)throws Exception{
  UdpVideoProbe.VideoInbox queue=new UdpVideoProbe.VideoInbox(true);JSONObject snapshot=queue.snapshot();
  snapshot.put("epoch_events_retained",count);JSONArray records=new JSONArray();
  for(int i=0;i<count;i++)records.put(new JSONObject().put("event","chain_lost").put("reason","codec_input_timeout")
   .put("epoch",wide?Long.MAX_VALUE:i+1L).put("previous_epoch",wide?Long.MAX_VALUE:i)
   .put("time_ns",wide?Long.MAX_VALUE:ORIGIN+i*100_000_000L).put("pts_us",wide?Long.MAX_VALUE:90_000_000L+i*33333L)
   .put("received_ns",wide?Long.MAX_VALUE:ORIGIN+i*100_000_000L-1_000_000L)
   .put("queue_frames",4).put("queue_bytes",wide?2*1024*1024:37847L));
  return new JSONObject().put("video_input_queue",snapshot).put("inbox_epoch_events",records)
   .put("video_worker_alive",false).put("video_worker_join_timed_out",false);
 }
 static MediaPresentationMetrics.StageSnapshot naturalStage(){return naturalStage(120,120);}
 static MediaPresentationMetrics.StageSnapshot naturalStage(int seconds,int fps){
  MediaPresentationMetrics.StageDiagnostics d=new MediaPresentationMetrics.StageDiagnostics();
  // Fill real fixed arrays via their actual APIs. Supplied synthetic timings
  // are a serialization sample only, never media or scheduler evidence.
  for(int i=0;i<seconds*fps;i++){
   long rx=ORIGIN+i*(1_000_000_000L/fps);
   d.inboxOffer(rx,rx+100_000L);d.inboxDepth(rx,4,2097152);d.inboxTaken(rx+120_000_000L,i,rx,4,2097152);
   if(i%fps==0){d.configureStarted(rx,1_000_000_000_000L+i,rx-100_000_000L);d.configureFinished(rx+200_000_000L,1_000_000_000_000L+i,true);}
   d.consumerStarted(rx+120_000_000L,i,i*10_000_000L);d.consumerFinished(rx+220_000_000L,i,i*10_000_000L+33_000_000L);
   d.inputCopyStarted(rx+120_000_000L,i,false);d.inputCopyFinished(rx+150_000_000L,i,false,true);
   d.inputCallStarted(rx+160_000_000L,i,false);d.inputCallFinished(rx+180_000_000L,i,false,true);
   d.reserveGuard(rx+220_000_000L,i,3,100000+i,100002+i);
   if(i%Math.max(1,fps/10)==0){d.fecPollObserved(rx);d.fecException(rx,-1,1,120);d.fecException(rx,-1,3,100);}
   d.clockReanchor(rx,6,30_000_000L);d.reserve(rx+80_000_000L,i,rx,rx+80_000_000L,80,false,0,-1,-1,0);
   d.inputQueued(rx,rx+80_000_000L);d.scheduled(i,rx+80_000_000L,rx+100_000_000L,rx+160_000_000L,rx+100_000_001L);
   if(i%10==0)d.inboxLoss(rx,i,MediaPresentationMetrics.StageDiagnostics.FRAME_OVERFLOW,4,2097152,i);
  }
  return d.snapshot(ORIGIN+seconds*1_000_000_000L);
 }
 static JSONObject mapping()throws Exception{
  long[] values=NativeMappingDetailsProbe.full();values[19]=1000;values[17]+=1000;values[22]+=1000;values[23]+=1000;
  for(int row=0;row<32;row++)values[36+row*8]+=1000;
  return UdpVideoProbe.mappingDetailsSummary(values,1,true,1202,0,123456789L,1234567L,ORIGIN);
 }
 static JSONObject measure(JSONObject stage,JSONObject mapping,int count,boolean wide)throws Exception{
  JSONObject report=inbox(count,wide),composed=UdpVideoProbe.numericAppSummary(report);
  if(stage!=null){report.put("decoder_stage_metrics",stage);composed.put("decoder_stage_metrics",stage);}
  if(mapping!=null){report.put("native_mapping_details",mapping);composed.put("native_mapping_details",mapping);}
  int represented=bytes(composed);boolean accepted;
  try{JSONObject actual=UdpVideoProbe.numericAppSummary(report);accepted=true;
   if(bytes(actual)!=represented)throw new AssertionError("module composition drift");}
  catch(java.io.IOException failure){if(!failure.getMessage().equals("numeric_app_report_limit"))throw failure;accepted=false;}
  if(accepted!=(represented<=65536))throw new AssertionError("hard byte limit drift");
  return new JSONObject().put("rows",count).put("wide_integer_envelope",wide?1:0).put("represented_utf8_bytes",represented)
   .put("accepted",accepted?1:0).put("remaining_bytes",65536-represented)
   .put("inbox_module_bytes",bytes(UdpVideoProbe.numericInboxEpochEvents(report)));
 }
 static int maximumRows(JSONObject stage,JSONObject mapping,boolean wide)throws Exception{
  int maximum=-1;
  for(int count=0;count<=256;count++)if(Integer.valueOf(1).equals(measure(stage,mapping,count,wide).get("accepted")))maximum=count;
  return maximum;
 }
 public static void main(String[] ignored)throws Exception{
  MediaPresentationMetrics.StageSnapshot natural=naturalStage();JSONObject stage=UdpVideoProbe.stageSummary(natural),map=mapping();
  JSONObject out=new JSONObject().put("schema_version",1).put("limit_bytes",65536)
   .put("stage_segments",natural.segments[0].length).put("stage_segment_columns",natural.segments.length)
   .put("stage_events",natural.events[0].length).put("stage_event_columns",natural.events.length)
   .put("stage_sample_module_bytes",bytes(stage)).put("mapping_sample_module_bytes",bytes(map));
  JSONArray inboxOnly=new JSONArray(),both=new JSONArray(),bothWide=new JSONArray();
  for(int n:new int[]{0,32,64,128,256}){inboxOnly.put(measure(null,null,n,false));both.put(measure(stage,map,n,false));bothWide.put(measure(stage,map,n,true));}
  int widest=maximumRows(stage,map,true);
  MediaPresentationMetrics.StageSnapshot next=naturalStage(45,30);JSONObject nextStage=UdpVideoProbe.stageSummary(next);
  out.put("inbox_only_samples",inboxOnly).put("stage_mapping_inbox_samples",both).put("stage_mapping_wide_inbox_samples",bothWide)
   .put("maximum_sample_rows_with_stage_mapping",maximumRows(stage,map,false))
   .put("maximum_wide_rows_with_stage_mapping",widest)
   .put("wide_boundary_last_accepted",measure(stage,map,widest,true))
   .put("wide_boundary_first_rejected",measure(stage,map,widest+1,true))
   .put("synthetic_45s30_stage_segments",next.segments[0].length)
   .put("synthetic_45s30_stage_module_bytes",bytes(nextStage))
   .put("synthetic_45s30_mapping_inbox256",measure(nextStage,map,256,false))
   .put("synthetic_45s30_mapping_wide_inbox256",measure(nextStage,map,256,true))
   .put("stage_only",measure(stage,null,0,false)).put("mapping_only",measure(null,map,0,false));
  // An integer representation envelope, not a physically reachable 120-second
  // run: widen all actual segment/event slots without inventing extra fields.
  for(long[] column:natural.segments)java.util.Arrays.fill(column,Long.MAX_VALUE);
  for(long[] column:natural.events)java.util.Arrays.fill(column,Long.MIN_VALUE);
  JSONObject integerEnvelope=UdpVideoProbe.stageSummary(natural);
  out.put("stage_integer_envelope_module_bytes",bytes(integerEnvelope))
   .put("stage_integer_envelope_only",measure(integerEnvelope,null,0,false))
   .put("stage_integer_envelope_mapping_max_inbox",measure(integerEnvelope,map,256,true));
  System.out.println(out);
 }
}
'''


class AppReportBudgetChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        java = str(JDK/'java') if (JDK/'java').is_file() else shutil.which('java')
        javac = str(JDK/'javac') if (JDK/'javac').is_file() else shutil.which('javac')
        if not java or not javac:
            raise RuntimeError('Existing JDK required; no dependency installation')
        with tempfile.TemporaryDirectory(prefix='huoguo-app-report-budget-jvm-') as directory:
            folder = Path(directory)
            probe = (ROOT/'experiments/nps-transport/phone/UdpVideoProbe.java').read_text()
            bound = probe.split('MAX_NATIVE_EVENTS=8192,', 1)[1].split(';', 1)[0]
            nested = '    static final class InboxEvent'+probe.split('    static final class InboxEvent', 1)[1].split('    // App mode is memory-only;', 1)[0]
            methods = '    static JSONObject stageSummary'+probe.split('    static JSONObject stageSummary', 1)[1].split('    private static final class Session', 1)[0]
            (folder/'UdpVideoProbe.java').write_text('package local.remoteandroid.direct;import java.util.ArrayDeque;'
                'import java.io.IOException;import java.nio.charset.StandardCharsets;import org.json.JSONObject;import org.json.JSONArray;'
                'final class UdpVideoProbe {static final int '+bound+';'+nested+methods+'}')
            (folder/'MainActivity.java').write_text('package local.remoteandroid.direct;final class MainActivity {'
                'volatile boolean running;volatile int generation;volatile Object video;}')
            (folder/'AppReportBudgetCheck.java').write_text(HARNESS)
            json_dir = folder/'org/json'
            json_dir.mkdir(parents=True)
            for name, source in [('JSONObject', JSON.OBJECT), ('JSONArray', JSON.ARRAY), ('JsonRender', JSON.RENDER)]:
                (json_dir/(name+'.java')).write_text(source)
            files = [*folder.glob('*.java'), *json_dir.glob('*.java'), ROOT/'experiments/nps-transport/phone/CodecStartupGate.java',
                ROOT/'experiments/nps-transport/phone/NativeUdpFec.java', ROOT/'tests/java/local/remoteandroid/direct/NativeMappingDetailsProbe.java',
                ROOT/'app/src/main/java/local/remoteandroid/direct/MediaPresentationMetrics.java']
            compile_result = subprocess.run([javac, '-d', str(folder), *map(str, files)], capture_output=True, text=True, timeout=30)
            if compile_result.returncode:
                raise RuntimeError('Actual formatter fixture compile failed:\n'+compile_result.stderr)
            run = subprocess.run([java, '-cp', str(folder), 'local.remoteandroid.direct.AppReportBudgetCheck'],
                capture_output=True, text=True, timeout=30)
            if run.returncode:
                raise RuntimeError('Actual formatter fixture failed:\n'+run.stderr)
            if len(run.stdout.encode()) > 16384:
                raise RuntimeError('Numeric budget receipt bound')
            cls.receipt = json.loads(run.stdout)

    def test_actual_fixed_shapes_and_module_composition(self):
        self.assertEqual((self.receipt['stage_segments'], self.receipt['stage_segment_columns']), (120, 40))
        self.assertEqual((self.receipt['stage_events'], self.receipt['stage_event_columns']), (64, 13))
        self.assertTrue(all(row['accepted'] == 1 for row in self.receipt['inbox_only_samples']))

    def test_combined_sampling_fields_have_exact_acceptance_boundary(self):
        for key in ('stage_mapping_inbox_samples', 'stage_mapping_wide_inbox_samples'):
            for row in self.receipt[key]:
                self.assertEqual(row['accepted'], int(row['represented_utf8_bytes'] <= 65536))
                self.assertEqual(row['remaining_bytes'], 65536-row['represented_utf8_bytes'])
        self.assertEqual(self.receipt['maximum_sample_rows_with_stage_mapping'], 256)
        self.assertLessEqual(self.receipt['maximum_wide_rows_with_stage_mapping'], self.receipt['maximum_sample_rows_with_stage_mapping'])
        self.assertEqual(self.receipt['wide_boundary_last_accepted']['accepted'], 1)
        self.assertEqual(self.receipt['wide_boundary_first_rejected']['accepted'], 0)

    def test_bounded_array_capacity_does_not_guarantee_a_64k_report(self):
        self.assertGreater(self.receipt['stage_integer_envelope_module_bytes'], 65536)
        self.assertEqual(self.receipt['stage_integer_envelope_only']['accepted'], 0)
        self.assertEqual(self.receipt['stage_integer_envelope_mapping_max_inbox']['accepted'], 0)


if __name__ == '__main__':
    unittest.main()
