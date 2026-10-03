"""Actual Probe/Inbox JVM report contract; no Android, accounts or services.

The org.json substitutes store typed values and serialize valid JSON. Policy,
ring retention, enum mapping and 64 KiB check come from the actual Java source.
Python parses the serialized result independently; this is not a phone test.
"""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
JDK = Path('/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home/bin')

OBJECT = r'''
package org.json;
public final class JSONObject {
 final java.util.Map<String,Object> values=new java.util.LinkedHashMap<>();
 public JSONObject put(String k,Object v){values.put(k,v);return this;}
 public Object opt(String k){return values.get(k);}
 public Object get(String k){if(!values.containsKey(k))throw new IllegalArgumentException("missing");return values.get(k);}
 public boolean has(String k){return values.containsKey(k);}
 public java.util.Iterator<String> keys(){return values.keySet().iterator();}
 public String optString(String k,String fallback){Object v=opt(k);return v instanceof String?(String)v:fallback;}
 public boolean optBoolean(String k,boolean fallback){Object v=opt(k);return v instanceof Boolean?(Boolean)v:fallback;}
 public String toString(){return JsonRender.object(values);}
}
'''
ARRAY = r'''
package org.json;
public final class JSONArray {
 final java.util.List<Object> values=new java.util.ArrayList<>();
 public JSONArray put(Object v){values.add(v);return this;}
 public int length(){return values.size();}
 public Object get(int i){return values.get(i);}
 public String toString(){return JsonRender.array(values);}
}
'''
RENDER = r'''
package org.json;
final class JsonRender {
 static String quote(String s){StringBuilder b=new StringBuilder("\"");
  for(char c:s.toCharArray()){if(c=='"'||c=='\\')b.append('\\').append(c);
   else if(c<32)b.append(String.format("\\u%04x",(int)c));else b.append(c);}return b.append('"').toString();}
 static String value(Object v){if(v==null)return "null";if(v instanceof String)return quote((String)v);
  if(v instanceof Number||v instanceof Boolean||v instanceof JSONObject||v instanceof JSONArray)return v.toString();
  throw new IllegalArgumentException("unsupported fixture value");}
 static String object(java.util.Map<String,Object> m){StringBuilder b=new StringBuilder("{");boolean first=true;
  for(java.util.Map.Entry<String,Object> e:m.entrySet()){if(!first)b.append(',');first=false;b.append(quote(e.getKey())).append(':').append(value(e.getValue()));}
  return b.append('}').toString();}
 static String array(java.util.List<Object> a){StringBuilder b=new StringBuilder("[");boolean first=true;
  for(Object v:a){if(!first)b.append(',');first=false;b.append(value(v));}return b.append(']').toString();}
}
'''

HARNESS = r'''
package local.remoteandroid.direct;
import org.json.JSONObject;import org.json.JSONArray;
final class InboxNumericCheck {
 static UdpVideoProbe.VideoFrame frame(){return new UdpVideoProbe.VideoFrame(new byte[20],540,960,4,16,100,200,true,0);}
 static JSONObject report(UdpVideoProbe.VideoInbox inbox,boolean alive,boolean timeout)throws Exception{
  JSONObject r=new JSONObject().put("video_input_queue",inbox.snapshot())
   .put("video_worker_alive",alive).put("video_worker_join_timed_out",timeout);
  if(!alive)r.put("inbox_epoch_events",inbox.eventSnapshot());return r;
 }
 static JSONObject event(String event,String reason){return new JSONObject().put("event",event).put("reason",reason)
  .put("epoch",2L).put("previous_epoch",1L).put("time_ns",300L).put("pts_us",100L).put("received_ns",200L)
  .put("queue_frames",4).put("queue_bytes",20L).put("password","DO_NOT_EXPORT_PASSWORD").put("raw_ui","DO_NOT_EXPORT_UI");}
 static JSONObject synthetic(JSONArray rows)throws Exception{
  JSONObject r=report(new UdpVideoProbe.VideoInbox(true),false,false);
  ((JSONObject)r.get("video_input_queue")).put("epoch_events_retained",rows.length());r.put("inbox_epoch_events",rows);return r;
 }
 static void reject(JSONObject r,String message)throws Exception{
  try{UdpVideoProbe.numericAppSummary(r);throw new AssertionError("accepted malformed report");}
  catch(java.io.IOException expected){if(!message.equals(expected.getMessage()))throw expected;}
 }
 public static void main(String[] args)throws Exception{
  String name=args[0];JSONObject r;
  switch(name){
   case "known": {
    String[] names={"chain_lost","idr_admitted","recovered","stale_fail_ignored","startup_prepare_admitted"};
    String[] reasons={"oversized_au","queue_frame_overflow","queue_byte_overflow","worker_complete_au_expired",
     "codec_input_timeout","codec_input_failure","old_epoch","metadata_only_not_media","complete_config_idr",
     "initial_idr_committed","epoch_idr_committed","stale_codec_epoch","codec_stopped"};
    JSONArray rows=new JSONArray();for(int i=0;i<reasons.length;i++)rows.put(event(names[i%names.length],reasons[i]));
    r=synthetic(rows).put("credentials","DO_NOT_EXPORT_PASSWORD");break;
   }
   case "unknown":r=synthetic(new JSONArray().put(event("new_future_event","new_future_reason")));break;
   case "empty":r=report(new UdpVideoProbe.VideoInbox(true),false,false);break;
   case "disabled": {
    UdpVideoProbe.VideoInbox inbox=new UdpVideoProbe.VideoInbox();inbox.fail(inbox.currentEpoch());
    if(!inbox.events.isEmpty()||inbox.eventsEvicted!=0)throw new AssertionError("default-off recording changed");
    r=report(inbox,false,false);break;
   }
   case "missing":r=new JSONObject().put("video_worker_alive",false).put("video_worker_join_timed_out",false);break;
   case "legacy":r=new JSONObject().put("video_input_queue",new JSONObject().put("epoch_event_capacity",256).put("epoch_events_evicted",0));break;
   case "missing_cleanup":r=synthetic(new JSONArray().put(event("recovered","initial_idr_committed")))
    .put("video_worker_alive","false");break;
   case "missing_events":r=report(new UdpVideoProbe.VideoInbox(true),false,false).put("inbox_epoch_events",null);break;
   case "alive":case "timeout": {
    UdpVideoProbe.VideoInbox inbox=new UdpVideoProbe.VideoInbox(true);inbox.fail(inbox.currentEpoch());
    r=report(inbox,name.equals("alive"),name.equals("timeout"));break;
   }
   case "eviction": {
    UdpVideoProbe.VideoInbox inbox=new UdpVideoProbe.VideoInbox(true);
    for(int i=0;i<300;i++)inbox.fail(inbox.currentEpoch());r=report(inbox,false,false);break;
   }
   case "real_ring": {
    UdpVideoProbe.VideoInbox inbox=new UdpVideoProbe.VideoInbox(true);
    inbox.offer(frame());UdpVideoProbe.VideoFrame idr=inbox.take();inbox.success(idr);inbox.fail(idr,"codec_input_timeout");
    inbox.fail(idr,"stale_codec_epoch");r=report(inbox,false,false);break;
   }
   case "numeric_scope": {
    r=synthetic(new JSONArray().put(event("recovered","initial_idr_committed")));
    r.put("native_fec",new JSONObject().put("packets",12).put("secret","DO_NOT_EXPORT_PASSWORD"));break;
   }
   case "mismatch":r=synthetic(new JSONArray().put(event("chain_lost","codec_input_timeout")));
    ((JSONObject)r.get("video_input_queue")).put("epoch_events_retained",0);reject(r,"inbox_event_ring_count_mismatch");System.out.println("PASS rejected");return;
   case "too_many": {
    JSONArray rows=new JSONArray();for(int i=0;i<257;i++)rows.put(event("recovered","initial_idr_committed"));
    reject(synthetic(rows),"inbox_event_ring_contract_invalid");System.out.println("PASS rejected");return;
   }
   case "fraction":r=synthetic(new JSONArray().put(event("chain_lost","codec_input_timeout").put("pts_us",1.5)));
    reject(r,"inbox_event_integral_field_required");System.out.println("PASS rejected");return;
   case "wrong_capacity":r=report(new UdpVideoProbe.VideoInbox(true),false,false);
    ((JSONObject)r.get("video_input_queue")).put("epoch_event_capacity",512);reject(r,"inbox_event_ring_contract_invalid");System.out.println("PASS rejected");return;
   case "overflow_count":r=report(new UdpVideoProbe.VideoInbox(true),false,false);
    ((JSONObject)r.get("video_input_queue")).put("epoch_events_retained",1).put("epoch_events_evicted",Long.MAX_VALUE);
    reject(r,"inbox_event_ring_contract_invalid");System.out.println("PASS rejected");return;
   case "limit": {
    r=report(new UdpVideoProbe.VideoInbox(true),false,false);JSONObject bulk=new JSONObject();
    for(int i=0;i<8000;i++)bulk.put("number_"+i,Long.MAX_VALUE);r.put("native_fec",bulk);
    reject(r,"numeric_app_report_limit");System.out.println("PASS rejected");return;
   }
   case "event_limit": {
    JSONObject bulk=new JSONObject();for(int i=0;i<1200;i++)bulk.put("number_"+i,Long.MAX_VALUE);
    JSONObject without=report(new UdpVideoProbe.VideoInbox(true),false,false).put("native_fec",bulk);
    UdpVideoProbe.numericAppSummary(without); // Existing numeric sections alone fit.
    JSONArray rows=new JSONArray();for(int i=0;i<256;i++)rows.put(event("recovered","epoch_idr_committed")
     .put("epoch",Long.MAX_VALUE).put("previous_epoch",Long.MAX_VALUE).put("time_ns",Long.MAX_VALUE)
     .put("pts_us",Long.MAX_VALUE).put("received_ns",Long.MAX_VALUE).put("queue_bytes",2097152));
    reject(synthetic(rows).put("native_fec",bulk),"numeric_app_report_limit");System.out.println("PASS rejected");return;
   }
   case "maximum": {
    JSONArray rows=new JSONArray();for(int i=0;i<256;i++)rows.put(event("recovered","epoch_idr_committed")
     .put("epoch",Long.MAX_VALUE).put("previous_epoch",Long.MAX_VALUE).put("time_ns",Long.MAX_VALUE)
     .put("pts_us",Long.MAX_VALUE).put("received_ns",Long.MAX_VALUE).put("queue_bytes",2097152));r=synthetic(rows);break;
   }
   default:throw new AssertionError("unknown case");
  }
  System.out.println(UdpVideoProbe.numericAppSummary(r).toString());
 }
}
'''


class InboxNumericEventChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.java = str(JDK/'java') if (JDK/'java').is_file() else shutil.which('java')
        javac = str(JDK/'javac') if (JDK/'javac').is_file() else shutil.which('javac')
        if not cls.java or not javac:
            raise RuntimeError('Existing JDK required for actual report fixture')
        cls.folder = tempfile.TemporaryDirectory(prefix='huoguo-inbox-numeric-fixture-')
        cls.addClassCleanup(cls.folder.cleanup)
        folder = Path(cls.folder.name)
        probe = (ROOT/'experiments/nps-transport/phone/UdpVideoProbe.java').read_text()
        event_bound = probe.split('MAX_NATIVE_EVENTS=8192,', 1)[1].split(';', 1)[0]
        nested = '    static final class InboxEvent' + probe.split('    static final class InboxEvent', 1)[1].split('    // App mode is memory-only;', 1)[0]
        summary = '    static JSONObject numericAppSummary' + probe.split('    static JSONObject numericAppSummary', 1)[1].split('    private static final class Session', 1)[0]
        (folder/'UdpVideoProbe.java').write_text('package local.remoteandroid.direct;\n'
            'import java.util.ArrayDeque;import java.io.IOException;import java.nio.charset.StandardCharsets;'
            'import org.json.JSONObject;import org.json.JSONArray;\n'
            'final class UdpVideoProbe {static final int '+event_bound+';\n'+nested+summary+'}\n')
        (folder/'MainActivity.java').write_text('package local.remoteandroid.direct;final class MainActivity {'
            'volatile boolean running;volatile int generation;volatile Object video;}')
        (folder/'InboxNumericCheck.java').write_text(HARNESS)
        json_dir = folder/'org/json'
        json_dir.mkdir(parents=True)
        for name, source in [('JSONObject', OBJECT), ('JSONArray', ARRAY), ('JsonRender', RENDER)]:
            (json_dir/(name+'.java')).write_text(source)
        files = [*folder.glob('*.java'), *json_dir.glob('*.java'),
            ROOT/'experiments/nps-transport/phone/CodecStartupGate.java',
            ROOT/'app/src/main/java/local/remoteandroid/direct/MediaPresentationMetrics.java']
        built = subprocess.run([javac, '-d', str(folder), *map(str, files)], capture_output=True, text=True, timeout=30)
        if built.returncode:
            raise RuntimeError('Actual Inbox/summary compile failed:\n'+built.stderr)

    def run_case(self, name, rejected=False):
        run = subprocess.run([self.java, '-cp', self.folder.name, 'local.remoteandroid.direct.InboxNumericCheck', name],
            capture_output=True, text=True, timeout=5)
        self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
        if rejected:
            self.assertEqual(run.stdout.strip(), 'PASS rejected')
            return None
        result = json.loads(run.stdout)
        self.assertLessEqual(len(run.stdout.strip().encode()), 64*1024)
        return result

    def module(self, name):
        return self.run_case(name)['inbox_epoch_events_numeric']

    def test_fixed_known_codes_and_all_nine_columns_are_numeric(self):
        section = self.module('known')
        self.assertEqual(section['event_code'], [1, 2, 3, 4, 5, 1, 2, 3, 4, 5, 1, 2, 3])
        self.assertEqual(section['reason_code'], list(range(1, 14)))
        self.assertEqual(section['exported_count'], 13)
        self.assertEqual(section['snapshot_complete'], 1)
        self.assertEqual(section['all_pipeline_events_covered'], 0)
        for key in ('event_code', 'reason_code', 'epoch', 'previous_epoch', 'time_ns', 'pts_us', 'received_ns', 'queue_frames', 'queue_bytes'):
            self.assertEqual(len(section[key]), 13)
            self.assertTrue(all(type(item) is int for item in section[key]))

    def test_unknown_codes_remain_visible_without_exporting_strings(self):
        section = self.module('unknown')
        self.assertEqual(section['event_code'], [0])
        self.assertEqual(section['reason_code'], [0])
        self.assertEqual((section['unknown_event_count'], section['unknown_reason_count']), (1, 1))
        self.assertEqual(section['known_codes_complete'], 0)

    def test_default_off_empty_enabled_and_missing_are_distinct(self):
        disabled, empty, missing, legacy = (self.module(name) for name in ('disabled', 'empty', 'missing', 'legacy'))
        self.assertEqual((disabled['available'], disabled['enabled'], disabled['availability_code'], disabled['snapshot_complete']), (1, 0, 1, 0))
        self.assertEqual((empty['enabled'], empty['availability_code'], empty['snapshot_complete'], empty['exported_count']), (1, 5, 1, 0))
        self.assertEqual((missing['available'], missing['availability_code']), (0, 0))
        self.assertEqual((legacy['available'], legacy['availability_code']), (0, 0))
        self.assertEqual(missing['enabled'], -1)
        self.assertEqual(legacy['enabled'], -1)

    def test_cleanup_unknown_alive_or_timed_out_never_claims_final_events(self):
        for name, code in [('missing_cleanup', 2), ('alive', 3), ('timeout', 3), ('missing_events', 4)]:
            with self.subTest(name=name):
                section = self.module(name)
                self.assertEqual(section['availability_code'], code)
                self.assertEqual(section['snapshot_complete'], 0)
                self.assertEqual(section['exported_count'], 0)
                self.assertEqual(section['event_code'], [])
        self.assertEqual(self.module('missing_cleanup')['worker_alive'], -1)
        self.assertEqual(self.module('alive')['worker_cleanup_confirmed'], 0)
        self.assertEqual(self.module('timeout')['worker_cleanup_confirmed'], 0)

    def test_actual_ring_eviction_preserves_all_256_rows_and_epoch_order(self):
        section = self.module('eviction')
        self.assertEqual((section['capacity'], section['retained_count'], section['exported_count'], section['evicted_count'], section['observed_count']), (256, 256, 256, 44, 300))
        self.assertEqual(section['epoch'], list(range(45, 301)))
        self.assertEqual(section['previous_epoch'], list(range(44, 300)))
        self.assertEqual(section['all_recorded_events_retained'], 0)
        self.assertEqual(section['snapshot_complete'], 1)
        self.assertEqual(section['known_codes_complete'], 1)

    def test_actual_admission_recovery_loss_and_stale_event_export(self):
        section = self.module('real_ring')
        self.assertEqual(section['event_code'], [2, 3, 1, 4])
        self.assertEqual(section['reason_code'], [9, 10, 5, 12])
        self.assertEqual(section['epoch'], [0, 0, 1, 1])
        self.assertEqual(section['pts_us'], [100]*4)

    def test_closed_event_export_cannot_copy_raw_ui_or_credentials(self):
        report = self.run_case('numeric_scope')
        serialized = json.dumps(report)
        self.assertNotIn('DO_NOT_EXPORT', serialized)
        self.assertNotIn('password', serialized)
        self.assertNotIn('raw_ui', serialized)
        def numeric_only(value):
            if isinstance(value, dict):
                return all(numeric_only(item) for item in value.values())
            if isinstance(value, list):
                return all(numeric_only(item) for item in value)
            return type(value) in (int, float)
        self.assertTrue(numeric_only(report))

    def test_bad_ring_counts_capacity_integrality_and_overflow_fail_closed(self):
        for name in ('mismatch', 'too_many', 'fraction', 'wrong_capacity', 'overflow_count'):
            with self.subTest(name=name):
                self.run_case(name, rejected=True)

    def test_whole_app_report_limit_is_preserved_and_maximum_ring_is_bounded(self):
        self.run_case('limit', rejected=True)
        section = self.module('maximum')
        self.assertEqual(section['exported_count'], 256)
        self.assertEqual(section['pts_us'], [2**63-1]*256)

    def test_new_events_cannot_silently_truncate_to_fit_other_numeric_sections(self):
        self.run_case('event_limit', rejected=True)


if __name__ == '__main__':
    unittest.main()
