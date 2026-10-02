"""Owned JVM counters only; no phone, codec, display, transport or account actions."""
from pathlib import Path
import json
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
JDK = Path('/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home/bin')


class DecoderStageMetricsCheck(unittest.TestCase):
    def test_fixed_bounds_signed_histograms_phase_coverage_and_concurrent_ring(self):
        javac = str(JDK/'javac') if (JDK/'javac').exists() else shutil.which('javac')
        java = str(JDK/'java') if (JDK/'java').exists() else shutil.which('java')
        with tempfile.TemporaryDirectory(prefix='huoguo-decoder-stage-') as folder:
            subprocess.run([javac, '-d', folder,
                str(ROOT/'app/src/main/java/local/remoteandroid/direct/MediaPresentationMetrics.java'),
                str(ROOT/'tests/java/local/remoteandroid/direct/DecoderStageMetricsProbe.java'),
                str(ROOT/'tests/java/local/remoteandroid/direct/MediaPresentationMetricsProbe.java')],
                check=True, capture_output=True, timeout=30)
            for target in ('DecoderStageMetricsProbe', 'MediaPresentationMetricsProbe'):
                result = subprocess.run([java, '-cp', folder, 'local.remoteandroid.direct.'+target],
                    check=True, capture_output=True, text=True, timeout=10)
                self.assertIn('PASS', result.stdout)

    def test_app_summary_retains_fixed_stage_arrays_and_submission_readback(self):
        source = (ROOT/'experiments/nps-transport/phone/UdpVideoProbe.java').read_text()
        summary = source.split('static JSONObject numericAppSummary', 1)[1]
        for field in ('decoder_stage_metrics', 'surface_submit_lead_ms', 'surface_submit_applications',
                      'surface_submit_wait_count', 'surface_submit_max_output_hold_ms', 'surface_submit_status_code'):
            self.assertIn('"'+field+'"', summary)
        self.assertIn('64*1024', summary)
        self.assertIn('"sf_time_domain_verified",0', source)
        self.assertIn('"clock_reanchor_coverage",0', source)
        # The formal App does not opt into this independent UDP diagnostics constructor.
        main = (ROOT/'app/src/main/java/local/remoteandroid/direct/MainActivity.java').read_text()
        self.assertNotIn('new MediaPresentationMetrics(48000,true)', main)

    def test_actual_numeric_summary_methods_retain_segments_and_fail_closed_at_64k(self):
        # Narrow JSON API substitutes exercise the actual source methods. This is
        # not Android JSONObject/ART runtime verification or a phone measurement.
        source = (ROOT/'experiments/nps-transport/phone/UdpVideoProbe.java').read_text()
        methods = source.split('    static JSONObject stageSummary', 1)[1].split('    private static final class Session', 1)[0]
        wrapper = '''package local.remoteandroid.direct;
import org.json.*;import java.io.IOException;import java.nio.charset.StandardCharsets;
final class NumericStageSource { static JSONObject stageSummary'''+methods+'}\n'
        stubs = {
            'org/json/JSONObject.java': '''package org.json;import java.util.*;
public final class JSONObject {
 final Map<String,Object> values=new LinkedHashMap<>();
 public JSONObject put(String k,Object v){values.put(k,v);return this;}
 public Object get(String k){if(!values.containsKey(k))throw new IllegalArgumentException(k);return values.get(k);}
 public Object opt(String k){return values.get(k);}public Iterator<String> keys(){return values.keySet().iterator();}
 public String optString(String k,String d){Object v=values.get(k);return v instanceof String?(String)v:d;}
 public boolean optBoolean(String k,boolean d){Object v=values.get(k);return v instanceof Boolean?(Boolean)v:d;}
 public String toString(){StringBuilder b=new StringBuilder("{");for(Map.Entry<String,Object> e:values.entrySet()){
  if(b.length()>1)b.append(',');b.append('"').append(e.getKey()).append("\\\":").append(render(e.getValue()));}
  return b.append('}').toString();}
 static String render(Object v){return v instanceof String?"\\\""+v+"\\\"":String.valueOf(v);}
}''',
            'org/json/JSONArray.java': '''package org.json;import java.util.*;
public final class JSONArray {
 final List<Object> values=new ArrayList<>();public JSONArray put(Object v){values.add(v);return this;}
 public int length(){return values.size();}public Object get(int i){return values.get(i);}
 public String toString(){StringBuilder b=new StringBuilder("[");for(Object v:values){
  if(b.length()>1)b.append(',');b.append(JSONObject.render(v));}return b.append(']').toString();}
}''',
            'local/remoteandroid/direct/NumericStageSource.java': wrapper,
            'local/remoteandroid/direct/NumericStageDriver.java': '''package local.remoteandroid.direct;
import org.json.*;import java.io.IOException;import java.nio.charset.StandardCharsets;
public final class NumericStageDriver {public static void main(String[] args)throws Exception{
 MediaPresentationMetrics.StageDiagnostics d=new MediaPresentationMetrics.StageDiagnostics();long origin=300_000_000_000_000L;
 for(int i=0;i<14400;i++){long rx=origin+i*8_333_333L;d.inboxOffer(rx);d.inboxDepth(rx,4,2097152);
  d.reserve(rx+80_000_000L,i,rx,rx+80_000_000L,80,false,0,-1,-1,0);
  d.inputQueued(rx,rx+80_000_000L);d.scheduled(i,rx+80_000_000L,rx+100_000_000L,rx+160_000_000L,rx+100_000_001L);
  if(i%10==0)d.inboxLoss(rx,i,MediaPresentationMetrics.StageDiagnostics.FRAME_OVERFLOW,4,2097152,i);}
 JSONObject report=new JSONObject().put("decoder_stage_metrics",NumericStageSource.stageSummary(d.snapshot(origin+120_000_000_000L)))
  .put("surface_submit_lead_ms",16).put("surface_submit_applications",1000).put("surface_submit_wait_count",30000)
  .put("surface_submit_status","applied_bounded_wait").put("hardware",true);
 JSONObject audio=new JSONObject();for(int i=0;i<200;i++)audio.put("fixed_audio_metric_"+i,1234567890123L);report.put("udp_audio",audio);
 JSONObject numeric=NumericStageSource.numericAppSummary(report);System.out.println(numeric);
 System.out.println("numeric_report_bytes="+numeric.toString().getBytes(StandardCharsets.UTF_8).length);
 for(int i=200;i<5000;i++)audio.put("fixed_audio_metric_"+i,1234567890123L);
 try{NumericStageSource.numericAppSummary(report);throw new AssertionError("oversize numeric report accepted");}
 catch(IOException wanted){if(!wanted.getMessage().equals("numeric_app_report_limit"))throw wanted;}
 System.out.println("NumericStageDriver PASS (offline JSON substitutes)");}}
''',
        }
        javac = str(JDK/'javac') if (JDK/'javac').exists() else shutil.which('javac')
        java = str(JDK/'java') if (JDK/'java').exists() else shutil.which('java')
        with tempfile.TemporaryDirectory(prefix='huoguo-stage-json-offline-') as folder:
            paths = []
            for relative, text in stubs.items():
                path = Path(folder)/relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
                paths.append(str(path))
            subprocess.run([javac, '-d', folder,
                str(ROOT/'app/src/main/java/local/remoteandroid/direct/MediaPresentationMetrics.java'), *paths],
                check=True, capture_output=True, timeout=30)
            result = subprocess.run([java, '-cp', folder, 'local.remoteandroid.direct.NumericStageDriver'],
                check=True, capture_output=True, text=True, timeout=10)
            lines = result.stdout.splitlines()
            report = json.loads(lines[0])
            stages = report['decoder_stage_metrics']
            self.assertEqual(stages['segment_capacity'], 120)
            self.assertEqual(stages['segments_retained'], 120)
            self.assertEqual(len(stages['segments']['offered_frames']), 120)
            self.assertEqual(len(stages['events']['time_ns']), 64)
            self.assertGreater(stages['events_evicted'], 0)
            self.assertEqual(report['surface_submit_status_code'], 1)
            self.assertEqual(report['surface_submit_lead_ms'], 16)
            self.assertLess(len(lines[0].encode()), 65536)
            self.assertIn('PASS', lines[-1])


if __name__ == '__main__':
    unittest.main()
