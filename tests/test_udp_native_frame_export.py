"""Actual report-time Java projection, with typed JSON substitutes; no phone."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

import test_udp_inbox_numeric_events as shared


HARNESS = r'''
package local.remoteandroid.direct;
import org.json.JSONObject;import org.json.JSONArray;
final class NativeFrameExportCheck {
 static JSONObject row(long sequence,int type,long at){
  String[] types={"invalid","fec_quorum_ready","frame_delivered","frame_expired","settled_by_delivered_frame","logical_rejected"};
  String[] reasons={"none","keyframe","predictive","assembly_deadline","superseded_by_delivered_frame","malformed_logical_body","logical_chain_blocked"};
  int reason=type==1?0:type==2?1:type==3?3:type==4?4:6;
  return new JSONObject().put("event",types[type]).put("reason",reasons[reason])
   .put("frame_id",sequence).put("reference_id",0L).put("flags",3L)
   .put("first_arrival_us",at).put("last_arrival_us",at+10)
   .put("fec_quorum_ready_us",type==3?0L:at+10).put("event_phone_us",at+(type==3?80000:10))
   .put("deadline_phone_us",at+80000).put("logical_bytes",100L).put("reason_code",reason)
   .put("event_sequence",sequence).put("pts_us",0L)
   .put("host_capture_us",Long.MAX_VALUE).put("password","DO_NOT_EXPORT").put("raw_media","DO_NOT_EXPORT");
 }
 static JSONObject report(JSONArray rows,long javaEvicted,long nativeEvicted,long pending){
  return new JSONObject().put("diagnostic_events_enabled",true).put("native_frame_events",rows)
   .put("native_frame_event_capacity",8192).put("native_frame_events_evicted",javaEvicted)
   .put("native_frame_event_stats",new JSONObject().put("enabled",true).put("pending",pending)
    .put("native_events_evicted",nativeEvicted).put("generated",rows.length()+javaEvicted+nativeEvicted+pending));
 }
 public static void main(String[] args)throws Exception{
  String mode=args[0];JSONObject r;JSONArray rows=new JSONArray();
  if(mode.equals("missing"))r=new JSONObject();
  else if(mode.equals("off"))r=new JSONObject().put("diagnostic_events_enabled",false).put("native_frame_events","unused");
  else if(mode.equals("empty"))r=report(rows,0,0,0);
  else if(mode.equals("types")){for(int type=1;type<=5;type++)rows.put(row(type,type,1000000+type*100000));r=report(rows,0,0,0);}
  else if(mode.equals("prefix")){for(int i=1;i<=70;i++)rows.put(row(i,2,1000000+i*100000));r=report(rows,0,0,0);}
  else if(mode.equals("evicted")){rows.put(row(5,3,1000000));r=report(rows,2,2,1);}
  else if(mode.equals("pending"))r=report(rows,0,0,2);
  else{
   JSONObject item=row(1,3,1000000);rows.put(item);r=report(rows,0,0,0);
   switch(mode){
    case "enabled":r.put("diagnostic_events_enabled",1);break;
    case "missing_snapshot":r.put("native_frame_event_stats",null);break;
    case "disabled_snapshot":((JSONObject)r.get("native_frame_event_stats")).put("enabled",false);break;
    case "accounting":((JSONObject)r.get("native_frame_event_stats")).put("generated",2L);break;
    case "overflow":r.put("native_frame_events_evicted",Long.MAX_VALUE);break;
    case "capacity":r.put("native_frame_event_capacity",256);break;
    case "pending_bound":((JSONObject)r.get("native_frame_event_stats")).put("pending",257L).put("generated",258L);break;
    case "reason":item.put("reason_code",0);break;
    case "foreign_type":item.put("event","future_unknown");break;
    case "fraction":item.put("frame_id",1.5);break;
    case "negative":item.put("first_arrival_us",-1L);break;
    case "expire_before_deadline":item.put("event_phone_us",1000010L);break;
    case "quorum_outside":item.put("fec_quorum_ready_us",1000020L);break;
    case "invalid_grant":item.put("deadline_phone_us",1080001L);break;
    case "sequence":rows.put(row(1,3,1200000));((JSONObject)r.get("native_frame_event_stats")).put("generated",2L);break;
    case "time":rows.put(row(2,3,900000));((JSONObject)r.get("native_frame_event_stats")).put("generated",2L);break;
    case "limit":{JSONObject bulk=new JSONObject();for(int i=0;i<8000;i++)bulk.put("number_"+i,Long.MAX_VALUE);r.put("native_fec",bulk);break;}
    default:throw new AssertionError("fixture mode");
   }
  }
  try{JSONObject full=UdpVideoProbe.numericAppSummary(r);System.out.println(full.get("native_frame_events_numeric"));}
  catch(java.io.IOException failure){System.out.println("REJECTED "+failure.getMessage());}
 }
}
'''


class NativeFrameExportChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        shared.InboxNumericEventChecks.setUpClass.__func__(cls)
        folder = Path(cls.folder.name)
        (folder / 'NativeFrameExportCheck.java').write_text(HARNESS)
        javac = str(shared.JDK / 'javac') if (shared.JDK / 'javac').is_file() else shutil.which('javac')
        run = subprocess.run([javac, '-cp', str(folder), '-d', str(folder), str(folder / 'NativeFrameExportCheck.java')],
                             capture_output=True, text=True, timeout=30)
        if run.returncode:
            raise RuntimeError(run.stderr)

    def result(self, mode, rejected=None):
        run = subprocess.run([self.java, '-cp', self.folder.name, 'local.remoteandroid.direct.NativeFrameExportCheck', mode],
                             capture_output=True, text=True, timeout=5)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        if rejected:
            self.assertTrue(run.stdout.startswith('REJECTED ' + rejected), run.stdout)
            return
        return json.loads(run.stdout)

    def test_missing_disabled_known_empty_and_pending_are_distinct(self):
        self.assertEqual((self.result('missing')['available'], self.result('missing')['enabled']), (0, -1))
        off = self.result('off')
        self.assertEqual((off['available'], off['enabled']), (0, 0))
        self.assertNotIn('generated_count', off)
        empty = self.result('empty')
        self.assertEqual((empty['available'], empty['exported_count'], empty['all_generated_events_exported']), (1, 0, 1))
        pending = self.result('pending')
        self.assertEqual((pending['native_pending_count'], pending['all_generated_events_exported']), (2, 0))

    def test_existing_event_types_and_reasons_retain_same_phone_metadata(self):
        d = self.result('types')
        self.assertEqual(d['event_code'], [1, 2, 3, 4, 5])
        self.assertEqual(d['reason_code'], [0, 1, 3, 4, 6])
        self.assertEqual(d['event_sequence'], [1, 2, 3, 4, 5])
        self.assertEqual(d['all_generated_events_exported'], 1)
        self.assertEqual(d['all_pipeline_events_covered'], 0)
        for flag in ('timestamp_is_physical_packet_arrival', 'quorum_is_codec_ready',
                     'host_phone_clock_subtraction_valid', 'frame_identity_from_JSON_verified'):
            self.assertEqual(d[flag], 0)

    def test_prefix_is_explicit_and_does_not_claim_omitted_validation(self):
        d = self.result('prefix')
        self.assertEqual((d['generated_count'], d['source_retained_count'], d['exported_count']), (70, 70, 64))
        self.assertEqual(d['omitted_prefix_following_count'], 6)
        self.assertEqual(d['event_sequence'], list(range(1, 65)))
        self.assertEqual(d['omitted_rows_validated'], 0)
        self.assertEqual(d['all_generated_events_exported'], 0)

    def test_native_java_eviction_and_pending_accounting_remain_separate(self):
        d = self.result('evicted')
        self.assertEqual((d['generated_count'], d['native_evicted_count'], d['java_evicted_count'], d['native_pending_count']), (6, 2, 2, 1))
        self.assertEqual(d['exported_sequence_contiguous_from_one'], 0)
        self.assertEqual(d['all_generated_events_exported'], 0)

    def test_invalid_snapshot_accounting_integral_bounds_are_rejected(self):
        for mode in ('enabled', 'missing_snapshot', 'disabled_snapshot', 'accounting', 'overflow',
                     'capacity', 'pending_bound', 'fraction', 'negative'):
            with self.subTest(mode=mode):
                self.result(mode, rejected='native_frame_')

    def test_grant_quorum_expiry_order_and_enum_mismatch_are_rejected(self):
        for mode in ('reason', 'foreign_type', 'expire_before_deadline', 'quorum_outside', 'invalid_grant', 'sequence', 'time'):
            with self.subTest(mode=mode):
                self.result(mode, rejected='native_frame_')

    def test_closed_numeric_projection_cannot_export_credentials_host_clock_or_body(self):
        d = self.result('types')
        serialized = json.dumps(d)
        for forbidden in ('DO_NOT_EXPORT', 'password', 'raw_media', 'host_capture_us'):
            self.assertNotIn(forbidden, serialized)
        def numeric(value):
            if isinstance(value, dict): return all(numeric(x) for x in value.values())
            if isinstance(value, list): return all(numeric(x) for x in value)
            return type(value) is int
        self.assertTrue(numeric(d))

    def test_original_whole_report_budget_still_rejects_without_truncation(self):
        self.result('limit', rejected='numeric_app_report_limit')


if __name__ == '__main__':
    unittest.main()
