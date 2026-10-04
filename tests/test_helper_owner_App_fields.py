"""NEW private own-FD graph fixtures. Android/Attempt callbacks synthetic."""
import copy,hashlib,json,os,subprocess,sys,time,unittest,tempfile,shutil
from pathlib import Path
ROOT=None
REPO=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(REPO),str(REPO/'tests')]
import tests.test_helper_owner_App_report as old
from scripts.probes.owner_native_gateway_environment import select
HOME=Path('/Users/inert')
ENV=None
BUILD=None
def setUpModule():
 global ROOT,ENV,BUILD
 BUILD=tempfile.TemporaryDirectory(prefix='huoguo-owned-App-fields-check-');ROOT=Path(BUILD.name)
 ENV=select(HOME,HOME/'Library/Android/sdk/platform-tools/adb',ROOT/'state',ROOT/'evidence',dict(DIRECT_AUTH_FILE='/restricted/auth.json',DIRECT_CERT='/restricted/cert.pem',DIRECT_KEY='/restricted/key.pem',DIRECT_VIDEO_BACKEND='videotoolbox'))
 compiler=shutil.which('clang') or shutil.which('cc')
 for src,out in [('tests/fixtures/helper_App_fields_parse_fixture.c','parse-host'),('tests/fixtures/helper_owner_App_fields_fixture.c','owned-host'),('experiments/moonlight-v2/source-snapshot/helper_owner_App_fields.c','inert-host')]:
  p=subprocess.run((compiler,'-std=c11','-O2','-Wall','-Wextra','-Werror',str(REPO/src),'-o',str(ROOT/out)),capture_output=True,timeout=30,env=ENV)
  if p.returncode:raise RuntimeError(p.stderr.decode())
def tearDownModule():
 if BUILD:BUILD.cleanup()
GATE=json.loads((REPO/'tests/fixtures/helper_single_window_report.json').read_text())['first_codec_startup_gate']
def report():
 d=dict(requested_seconds=30,session_limit_reached=0,session_end_reason_code=0,start_ns=500000000,first_server_packet_ns=750000000,
 receive_end_ns=8000000000,observation_end_ns=8100000000,fps_limit=30,buffer_ms=80,udp_packets=1000,udp_payload_bytes=50000,
 foreign_peer_packets=0,authentication_errors=0,replay_errors=0,received_media_frames=60,queued_media_frames=50,source_width=540,
 source_height=960,decoder_input_timeouts=0,late_discarded_count=0,codec_callback_count=45,receive_loop_max_ms=1.25,
 receive_processing_max_ms=2.5,receive_socket_wait_max_ms=1e-3,video_worker_expired_frames=0,video_worker_stale_epoch_drops=0,
 audio_cleanup_confirmed=1,surface_submit_lead_ms=0,surface_submit_applications=0,surface_submit_wait_count=0,
 surface_submit_wait_total_ms=0.0,surface_submit_max_output_hold_ms=0.0,surface_submit_max_park_ms=0.0,surface_submit_budget_fallbacks=0,
 stage_diagnostics_enabled=0,surface_submit_status_code=0,hardware_video=1,codec_startup_ready_enabled=0,codec_startup_gate=copy.deepcopy(GATE))
 i=dict(schema_version=1,available=0,enabled=-1,capacity=256,retained_count=0,exported_count=0,evicted_count=0,observed_count=0,
 unknown_event_count=0,unknown_reason_count=0,worker_alive=-1,worker_join_timed_out=-1,worker_cleanup_confirmed=0,snapshot_complete=0,
 all_recorded_events_retained=0,known_codes_complete=0,availability_code=0,all_pipeline_events_covered=0)
 for k in ['event_code','reason_code','epoch','previous_epoch','time_ns','pts_us','received_ns','queue_frames','queue_bytes']:i[k]=[]
 t=dict(schema_version=1,available=0,capacity=64,recorded_count=0,exported_count=0,omitted_prefix_following_count=0,
 all_recorded_samples_exported=0,all_session_observations_covered=0,timestamp_is_exact_packet_arrival=0,physical_route_or_latency_verified=0)
 for k in ['t_ns','udp_packets','authenticated_packets','authenticated_video_packets','received_media_frames','codec_callback_count',
           'FEC_packets','FEC_frames_expired','FEC_reference_lost','FEC_dependency_dropped','FEC_keyframe_requests']:t[k]=[]
 n=dict(schema_version=1,available=0,enabled=-1,capacity=64,source_ring_capacity=8192,native_ring_capacity=256,
 all_generated_events_exported=0,all_pipeline_events_covered=0,timestamp_is_physical_packet_arrival=0,quorum_is_codec_ready=0,
 host_phone_clock_subtraction_valid=0,frame_identity_from_JSON_verified=0,omitted_rows_validated=0)
 d.update(inbox_epoch_events_numeric=i,transport_samples_numeric=t,native_frame_events_numeric=n)
 return d

def encoded(d):return json.dumps(d,separators=(',',':')).encode()
class SyntaxClaims(unittest.TestCase):
 def check(self,value,expected=True):
  raw=value if isinstance(value,bytes) else encoded(value)
  p=subprocess.run((str(ROOT/'parse-host'),),input=raw,capture_output=True,timeout=3,env=ENV)
  self.assertEqual(p.returncode,0 if expected else 2,p.stdout+p.stderr);d=json.loads(p.stdout)
  self.assertEqual(d['observed'],expected);self.assertFalse(d['permission']);self.assertFalse(d['release']);return d
 def test_complete_core_missing_prefix_states_remain_unknown(self):
  d=self.check(report());self.assertEqual((d['native_available'],d['native_enabled']),(0,-1))
 def test_completion_receipt_is_not_statistics(self):self.check({'completion_receipt':{'statistics_report_accepted':0}},False)
 def test_missing_core_or_prefix_whole_refusal(self):
  for key in ['receive_end_ns','codec_callback_count','inbox_epoch_events_numeric','transport_samples_numeric','native_frame_events_numeric']:
   d=report();del d[key];self.check(d,False)
 def test_extra_attempt_identity_cannot_be_recovered_from_JSON(self):
  d=report();d['AttemptID']=123;self.check(d,False)
 def test_decimal_and_exponent_clocks_refused(self):
  for value in [8e9,8000000000.0]:d=report();d['receive_end_ns']=value;self.check(d,False)
 def test_bad_defaults_closed_stage_and_startup(self):
  for key,value in [('buffer_ms',120),('surface_submit_lead_ms',16),('codec_startup_ready_enabled',1),('stage_diagnostics_enabled',1),('requested_seconds',120)]:
   d=report();d[key]=value;self.check(d,False)
  d=report();d['decoder_stage_metrics']={};self.check(d,False)
  d=report();d['codec_startup_gate']['phase']=6;self.check(d,False)
 def test_nonzero_surface_wait_rejects_even_decimal(self):
  d=report();d['surface_submit_wait_total_ms']=.25;self.check(d,False)
 def test_audio_claim_zero_not_automatic_cleanup_refusal(self):
  d=report();d['audio_cleanup_confirmed']=0;self.check(d)
 def test_optional_objects_explicitly_not_fully_qualified(self):
  d=report();d['udp_audio']={'pcm_cleanup_incomplete':1};self.assertEqual(self.check(d)['unqualified_optional_objects'],1)
 def test_native_OFF_cannot_claim_empty_complete(self):
  d=report();d['native_frame_events_numeric']['enabled']=0;self.check(d)
  d['native_frame_events_numeric']['all_generated_events_exported']=1;self.check(d,False)
 def test_unknown_inbox_code_count_refuses_fake_known_coverage(self):
  d=report();i=d['inbox_epoch_events_numeric'];i.update(available=1,enabled=1,retained_count=1,exported_count=1,
   observed_count=1,unknown_event_count=1,unknown_reason_count=1,worker_alive=0,worker_join_timed_out=0,
   worker_cleanup_confirmed=1,snapshot_complete=1,all_recorded_events_retained=1,known_codes_complete=0,availability_code=5)
  for k in ['event_code','reason_code','epoch','previous_epoch','time_ns','received_ns','queue_frames','queue_bytes']:i[k]=[0]
  i['pts_us']=[-1];self.check(d);i['known_codes_complete']=1;self.check(d,False)
 def test_unavailable_inbox_cannot_claim_snapshot(self):
  d=report();d['inbox_epoch_events_numeric']['snapshot_complete']=1;self.check(d,False)
 def test_legacy_transport_missing_not_zero_fault(self):
  d=report();d['transport_samples_numeric']['recorded_count']=70;self.check(d)
 def test_transport_prefix_mismatch_or_counter_decrease(self):
  d=report();t=d['transport_samples_numeric'];t.update(available=1,recorded_count=2,exported_count=2,all_recorded_samples_exported=1)
  for k,v in list(t.items()):
   if isinstance(v,list):t[k]=[1,2]
  t['t_ns']=[1000000000,2000000000];self.check(d)
  t['FEC_frames_expired']=[2,1];self.check(d,False)
 def test_arrays_reject_decimal_or_mismatched_count(self):
  d=report();d['inbox_epoch_events_numeric']['event_code']=[1.0];self.check(d,False)
 def test_duplicate_alias_nonfinite_bounds(self):
  raw=encoded(report());self.check(raw.replace(b'"fps_limit":30',b'"fps_limit":30,"fps_limit":30'),False)
  self.check(raw.replace(b'"receive_end_ns"',b'"receive_end_n\\u0073"'),False)
  self.check(raw.replace(b'1.25',b'1e999'),False)
  self.check(raw+b' '*(65537-len(raw)),False)
 def test_positive_underflow_cannot_become_zero_surface_wait(self):
  self.check(encoded(report()).replace(b'"surface_submit_wait_total_ms":0.0',b'"surface_submit_wait_total_ms":1e-999'),False)
 def test_negative_underflow_cannot_become_nonnegative_metric(self):
  self.check(encoded(report()).replace(b'"receive_loop_max_ms":1.25',b'"receive_loop_max_ms":-1e-999'),False)
 def test_exact_zero_mantissa_with_exponent_remains_zero(self):
  self.check(encoded(report()).replace(b'"surface_submit_wait_total_ms":0.0',b'"surface_submit_wait_total_ms":0.0e-999'))
 def test_phone_clock_order_refusal(self):
  d=report();d['observation_end_ns']=500;self.check(d,False)

class OwnedGraph(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.binary=ROOT/'owned-host';cls.value=json.loads((REPO/'tests/fixtures/helper_single_window_report.json').read_text())
 def observe(self,mode,expected=True,edit=None):
  d=report()
  if edit:edit(d)
  prior=old.APP_BYTES;old.APP_BYTES=encoded(d)
  try:rows,code,raw=old.AppReportFD.owned(self,mode)
  finally:old.APP_BYTES=prior
  self.assertEqual(code,0 if expected else 2)
  a=[r for r in rows if r['event']=='App_FD_read'];self.assertEqual(len(a),1)
  r=a[0];self.assertEqual(r['observed'],expected);self.assertEqual(r['unknown'],not expected)
  for k in ['atomic_hold','whole_App_fields_verified','Android_Attempt_verified','permission','release']:self.assertFalse(r[k])
  self.assertTrue(r['repeat_refused']);self.assertTrue(r['retained_file_FD']);self.assertEqual(r['queries'],2);return r
 def test_actual_own_FD_wait_dualEOF_and_core_prefix(self):self.assertTrue(self.observe('natural')['association_observed'])
 def test_missing_independent_association_refuses(self):self.observe('missing_association',False)
 def test_later_Attempt_refuses(self):self.observe('later_attempt_association',False)
 def test_later_receiver_refuses(self):self.observe('later_receiver',False)
 def test_later_Surface_refuses(self):self.observe('later_surface',False)
 def test_later_phoneclock_refuses(self):self.observe('later_clock',False)
 def test_backwards_phoneclock_refuses(self):self.observe('phone_clock_backwards',False)
 def test_wrong_phone_domain_refuses(self):self.observe('wrong_phone_domain',False)
 def test_callback_file_replace_refuses_retains_oldFD(self):self.observe('callback_replace_file',False)
 def test_report_missing_count_refuses_same_valid_digest(self):self.observe('natural',False,lambda d:d.pop('codec_callback_count'))
 def test_helper_window_outside_report_refuses(self):self.observe('natural',False,lambda d:d.update(receive_end_ns=6000000000))
 def test_inert_and_missing_callback_constructor(self):
  for args,code in [((),0),(('--execute',),2)]:
   r=subprocess.run((str(ROOT/'inert-host'),*args),env=ENV,capture_output=True,timeout=3);self.assertEqual(r.returncode,code)
  r=subprocess.run((str(self.binary),'--missing-gates-fixture'),env=ENV,capture_output=True,timeout=3);self.assertEqual(r.returncode,0)

class ActualJavaPrefix(unittest.TestCase):
 check=SyntaxClaims.check
 @classmethod
 def setUpClass(cls):
  import test_udp_native_frame_export as j
  from unittest.mock import patch
  RUN=subprocess.run
  def selected(*a,**k):k['env']=ENV;return RUN(*a,**k)
  with patch.object(subprocess,'run',side_effect=selected):j.NativeFrameExportChecks.setUpClass.__func__(cls)
 @classmethod
 def tearDownClass(cls):cls.folder.cleanup()
 def projection(self,main,case):
  r=subprocess.run((self.java,'-cp',self.folder.name,'local.remoteandroid.direct.'+main,case),env=ENV,capture_output=True,timeout=5)
  self.assertEqual(r.returncode,0,r.stderr);return json.loads(r.stdout)
 def test_actual_java_native_prefix_pending_evicted_and_omitted(self):
  for mode in ['missing','off','empty','types','prefix','evicted','pending']:
   d=report();d['native_frame_events_numeric']=self.projection('NativeFrameExportCheck',mode);self.check(d)
 def test_actual_java_inbox_256_and_unknown_cleanup_states(self):
  for mode in ['known','unknown','eviction','missing','disabled','missing_cleanup','alive','timeout','missing_events']:
   d=report();d['inbox_epoch_events_numeric']=self.projection('InboxNumericCheck',mode)['inbox_epoch_events_numeric'];self.check(d)

if __name__=='__main__':unittest.main()
