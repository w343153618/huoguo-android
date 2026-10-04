"""Actual Java report exporter fixtures, not Android/performance acceptance."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

import test_udp_inbox_numeric_events as shared

HARNESS = r'''
package local.remoteandroid.direct;
import org.json.JSONObject;import org.json.JSONArray;
final class TransportSampleCheck {
 static JSONObject row(long time,long n){return new JSONObject().put("t_ns",time)
  .put("udp_packets",n+10).put("authenticated_packets",n+8).put("authenticated_video_packets",n+5)
  .put("received_media_frames",n).put("codec_callback_count",n)
  .put("password","DO_NOT_EXPORT").put("address","DO_NOT_EXPORT")
  .put("native_fec",new JSONObject().put("packets",n).put("frames_expired",n/10)
    .put("reference_lost",n/10).put("dependency_dropped",n/5).put("keyframe_requests",n/20));}
 public static void main(String[] args)throws Exception{
  String mode=args[0];JSONObject r=new JSONObject();JSONArray rows=new JSONArray();
  if(mode.equals("missing")){System.out.println(UdpVideoProbe.numericTransportSamples(r));return;}
  if(mode.equals("legacy"))rows.put(new JSONObject().put("t_ns",1).put("authenticated_packets",2));
  else if(mode.equals("empty")){}
  else if(mode.equals("prefix")||mode.equals("record_bound")){
   int n=mode.equals("prefix")?70:3601;for(int i=0;i<n;i++)rows.put(row(100+i,10+i));
  }else if(mode.equals("flow")){
   // Authenticated datagrams continue while complete frames/callbacks plateau.
   rows.put(row(100,10));JSONObject second=row(200,50).put("received_media_frames",10).put("codec_callback_count",10);
   rows.put(second);
  }else if(mode.equals("clock"))rows.put(row(100,10)).put(row(100,20));
  else if(mode.equals("counter"))rows.put(row(100,20)).put(row(200,10));
  else if(mode.equals("fraction"))rows.put(row(100,10).put("udp_packets",20.5));
  else if(mode.equals("negative"))rows.put(row(100,10).put("received_media_frames",-1));
  else if(mode.equals("auth"))rows.put(row(100,10).put("authenticated_packets",99));
  else if(mode.equals("FEC_missing"))rows.put(row(100,10).put("native_fec",null));
  else if(mode.equals("FEC_fraction")){JSONObject item=row(100,10);((JSONObject)item.get("native_fec")).put("packets",1.2);rows.put(item);}
  else if(mode.equals("foreign"))rows.put("DO_NOT_EXPORT");
  else throw new AssertionError("unknown fixture");
  r.put("samples",rows).put("credential","DO_NOT_EXPORT");
  try{
   JSONObject output=UdpVideoProbe.numericAppSummary(r);
   if(mode.equals("clock")||mode.equals("counter")||mode.equals("fraction")||mode.equals("negative")
    ||mode.equals("auth")||mode.equals("FEC_missing")||mode.equals("FEC_fraction")||mode.equals("foreign")||mode.equals("record_bound"))
     throw new AssertionError("malformed sample accepted");
   System.out.println(output.get("transport_samples_numeric"));
  }catch(java.io.IOException expected){System.out.println("REJECTED "+expected.getMessage());}
 }
}
'''


class TransportSampleChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Compile the actual source with the existing typed JSON fixture runtime.
        # Module import does not duplicate discovery of its TestCase class.
        shared.InboxNumericEventChecks.setUpClass.__func__(cls)
        folder=Path(cls.folder.name)
        (folder/'TransportSampleCheck.java').write_text(HARNESS)
        javac=str(shared.JDK/'javac') if (shared.JDK/'javac').is_file() else shutil.which('javac')
        run=subprocess.run([javac,'-cp',str(folder),'-d',str(folder),str(folder/'TransportSampleCheck.java')],
            capture_output=True,text=True,timeout=30)
        if run.returncode:raise RuntimeError(run.stderr)

    def result(self,mode,rejected=False):
        run=subprocess.run([self.java,'-cp',self.folder.name,'local.remoteandroid.direct.TransportSampleCheck',mode],
            capture_output=True,text=True,timeout=5)
        self.assertEqual(run.returncode,0,run.stdout+run.stderr)
        if rejected:
            self.assertTrue(run.stdout.startswith('REJECTED transport_sample_'),run.stdout)
            return
        return json.loads(run.stdout)

    def test_authenticated_flow_with_frame_plateau_is_visible(self):
        d=self.result('flow')
        self.assertEqual(d['authenticated_packets'],[18,58])
        self.assertEqual(d['received_media_frames'],[10,10])
        self.assertEqual(d['codec_callback_count'],[10,10])
        self.assertEqual(d['FEC_frames_expired'],[1,5])
        self.assertEqual(d['available'],1)
        self.assertEqual(d['timestamp_is_exact_packet_arrival'],0)

    def test_prefix_retention_and_zero_global_coverage_claim(self):
        d=self.result('prefix')
        self.assertEqual((d['recorded_count'],d['exported_count'],d['omitted_prefix_following_count']),(70,64,6))
        self.assertEqual(d['t_ns'],list(range(100,164)))
        self.assertEqual(d['all_recorded_samples_exported'],0)
        self.assertEqual(d['all_session_observations_covered'],0)

    def test_missing_legacy_and_known_empty_are_distinct(self):
        self.assertEqual(self.result('missing')['available'],0)
        legacy=self.result('legacy');self.assertEqual((legacy['available'],legacy['recorded_count']),(0,1))
        empty=self.result('empty');self.assertEqual((empty['available'],empty['recorded_count']),(1,0))

    def test_integral_monotonic_auth_FEC_and_record_bounds(self):
        for mode in ('clock','counter','fraction','negative','auth','FEC_missing','FEC_fraction','foreign','record_bound'):
            with self.subTest(mode=mode):self.result(mode,rejected=True)

    def test_closed_numeric_only_no_credential_address_or_body(self):
        d=self.result('flow');s=json.dumps(d)
        for forbidden in ('DO_NOT_EXPORT','password','credential','address'):self.assertNotIn(forbidden,s)
        def numeric(v):
            if isinstance(v,dict):return all(numeric(x) for x in v.values())
            if isinstance(v,list):return all(numeric(x) for x in v)
            return type(v) is int
        self.assertTrue(numeric(d))

    def test_receive_hook_reuses_existing_sample_not_per_packet_observer(self):
        source=(shared.ROOT/'experiments/nps-transport/phone/UdpVideoProbe.java').read_text()
        sample=source.split('if(keepDetail(samples))samples.put',1)[1].split('previous[0]=now',1)[0]
        self.assertNotIn('System.nanoTime()',sample)
        for field in ('udp_packets','authenticated_video_packets','received_media_frames','codec_callback_count'):
            self.assertIn('put("'+field+'"',sample)


if __name__=='__main__':unittest.main()
