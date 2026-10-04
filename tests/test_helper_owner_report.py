"""Native bounded reader/owned child fixtures; Android outputs are synthetic."""
import copy
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'experiments/moonlight-v2/source-snapshot/helper_owner_report.c'
PAYLOAD = b'public host APK standin\n'
NONCE = b'd2'*12

def hg(kind, sequence, length=0, digest=b'0'*64):
    return b'HGHC0001'+bytes((kind,0,0,0))+struct.pack('!I',length)+NONCE+struct.pack('!Q',sequence)+digest

def schedule(kind, sequence):
    return b'HGDS0001'+bytes((kind,))+b'\0'*7+NONCE+struct.pack('!Q',sequence)

class NativeHelperReport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='helper-report-host-only-')
        cls.base=Path(cls.tmp.name);cls.binary=cls.base/'fixture';cls.inert=cls.base/'inert'
        cls.env={'PATH':os.defpath,'HOME':str(Path.home()),'LANG':'C'}
        compiler=shutil.which('clang') or shutil.which('cc')
        for source,target in ((ROOT/'tests/fixtures/helper_owner_report_fixture.c',cls.binary),(SOURCE,cls.inert)):
            r=subprocess.run((compiler,'-std=c11','-O2','-Wall','-Wextra','-Werror',str(source),'-o',str(target)),
                capture_output=True,timeout=20,env=cls.env)
            if r.returncode:raise RuntimeError(r.stderr.decode())
        cls.value=json.loads((ROOT/'tests/fixtures/helper_single_window_report.json').read_text())

    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def parse(self,value,expected):
        raw=value if isinstance(value,bytes) else json.dumps(value,separators=(',',':')).encode()
        p=subprocess.run((str(self.binary),'--parse-fixture'),input=raw,capture_output=True,timeout=3,env=self.env)
        self.assertEqual(p.returncode,0 if expected else 2)
        row=json.loads(p.stdout);self.assertEqual(row['parsed'],expected)
        self.assertFalse(row['permission']);self.assertFalse(row['release'])

    def changed(self,key,value):
        row=copy.deepcopy(self.value);row[key]=value;return row

    def test_valid_producer_snapshot_is_observation_only(self):self.parse(self.value,True)
    def test_maximum_44_rows_and_whole_64KiB_bound(self):
        x=copy.deepcopy(self.value)
        x['native_window_samples']=[dict(phone_ns=2000000000+i*5000000000//43,worker_received_frames=20+i,codec_callback_count=20+i) for i in range(44)]
        self.parse(x,True)
        raw=json.dumps(x,separators=(',',':')).encode();self.parse(raw+b' '*(65536-len(raw)),True)
        self.parse(raw+b' '*(65537-len(raw)),False)
    def test_duplicate_top_and_nested_keys_rejected(self):
        raw=json.dumps(self.value,separators=(',',':')).encode()
        self.parse(b'{"requested_owner_native_window":false,'+raw[1:],False)
        self.parse(raw.replace(b'"phone_ns":2000000000',b'"phone_ns":2000000000,"phone_ns":2000000000',1),False)
    def test_escaped_key_alias_and_raw_control_bytes_rejected(self):
        raw=json.dumps(self.value,separators=(',',':')).encode()
        self.parse(raw.replace(b'"requested_node"',b'"requested_\\u006eode"',1),False)
        self.parse(raw.replace(b'"saved-ui"',b'"saved\x00-ui"',1),False)
    def test_boolean_as_integer_float_nonfinite_and_overflow_rejected(self):
        for value in (True,5.0,float('nan'),float('inf'),1<<63):
            with self.subTest(value=value):self.parse(self.changed('requested_owner_native_window_seconds',value),False)
    def test_bad_integer_grammar_and_trailing_payload_rejected(self):
        raw=json.dumps(self.value,separators=(',',':')).encode()
        for value in (b'05',b'5e0',b'-0',b'5.0'):
            self.parse(raw.replace(b'"requested_owner_native_window_seconds":5',b'"requested_owner_native_window_seconds":'+value,1),False)
        self.parse(raw+b'{}',False)
    def test_missing_extra_failure_reconnect_or_unknown_object_rejected(self):
        x=copy.deepcopy(self.value);del x['first_exit_UI_callbacks_observed'];self.parse(x,False)
        for key,value in (('failure_class','IllegalStateException'),('normal_UI_reconnected_received_media',True),('unknown',{'a':True})):
            self.parse(self.changed(key,value),False)
    def test_wrong_saved_route_secret_source_and_transport_rejected(self):
        for key,value in (('requested_credential_source','private-file'),('saved_UI_secret_exported',True),
                ('first_saved_UI_nonempty_credential_verified',False),('first_actual_control_port',15556),
                ('first_actual_control_host','100.65.0.2'),('first_actual_media_peer_port',45965),('first_actual_network_scope','nps_owner')):
            self.parse(self.changed(key,value),False)
    def test_wrong_defaults_fps_geometry_or_diagnostic_mode_rejected(self):
        for key,value in (('first_actual_buffer_ms',100),('first_actual_fps_limit',60),('first_actual_video_width',1080),
                ('requested_surface_submit_lead_ms',16),('requested_stage_diagnostics_enabled',True),
                ('requested_codec_startup_ready_enabled',True),('first_surface_submit_wait_count',1)):
            self.parse(self.changed(key,value),False)
    def test_clock_span_margin_overflow_or_wrong_descriptor_rejected(self):
        for key,value in (('native_window_click_ns',-1),('native_window_started_ns',0),('native_window_budget_end_ns',31000000001),
                ('native_window_descriptor_seconds',31),('native_window_close_margin_ns',7000000000),('native_window_finished_ns',6999999999)):
            self.parse(self.changed(key,value),False)
        x=copy.deepcopy(self.value);x['native_window_click_ns']=9223372036854775807;self.parse(x,False)
    def test_samples_decrease_duplicate_time_wrong_type_or_extra_rejected(self):
        for key,value in (('phone_ns',2000000000),('worker_received_frames',0),('codec_callback_count',True),('extra',0)):
            x=copy.deepcopy(self.value);x['native_window_samples'][1][key]=value;self.parse(x,False)
        x=copy.deepcopy(self.value);x['native_window_samples']=x['native_window_samples'][1:];self.parse(x,False)
    def test_sample_progress_not_required_and_counts_not_presented_fps(self):
        x=copy.deepcopy(self.value)
        for row in x['native_window_samples']:row['worker_received_frames']=15;row['codec_callback_count']=10
        self.parse(x,True)
        self.parse(self.changed('native_window_is_presented_FPS',True),False)
    def test_normal_exit_audio_and_false_atomic_hold_required(self):
        for key,value in (('first_exit_captured_attempt_cancelled',False),('first_leave_audio_threads_alive',1),
                ('activity_running_after_leave',True),('native_window_server_atomic_hold_verified',True)):
            self.parse(self.changed(key,value),False)
    def test_codec_off_failure_or_committed_conflict_rejected(self):
        for key,value in (('enabled',1),('phase',6),('failure_code',1),('committed_ns',1),('bootstrap_pts_us',0),('unknown',1)):
            x=copy.deepcopy(self.value);x['first_codec_startup_gate'][key]=value;self.parse(x,False)
    def test_app_digest_missing_invalid_or_not_string_rejected(self):
        for value in ('ab'*31,'zz'*32,123,True):self.parse(self.changed('first_App_report_sha256',value),False)
    def test_recursion_or_node_limit_rejects_whole_payload(self):
        self.parse(b'{"a":'+b'['*9+b'0'+b']'*9+b'}',False)
        self.parse(b'{"a":['+b','.join([b'0']*513)+b']}',False)
    def test_independent_existing_python_readbacks_accept_same_synthetic_helper(self):
        from scripts.probes import owner_native_window as w
        from scripts.probes import run_authenticated_lan_ui as u
        class Check:
            seconds=5
            def __post_init__(self):pass
        self.assertTrue(w.helper_readback(self.value,Check()))
        for call,args in ((u.verify_v50_profile_readback,(True,)),(u.verify_credential_source_readback,('saved-ui',)),
                (u.verify_exit_confirmation_readback,()),(u.verify_surface_submit_readback,(0,)),
                (u.verify_stage_diagnostics_readback,(False,)),(u.verify_codec_startup_readback,(False,))):
            self.assertTrue(call(self.value,*args,stages=('first',)))
        self.assertTrue(u.verify_credential_save_readback(self.value,False))

    def owned(self,mode,value=None):
        with tempfile.TemporaryDirectory(prefix='helper-report-owned-fixture-') as directory:
            base=Path(directory);owner=base/'owner';owner.mkdir();pkg=base/'pkg';file=pkg/'~~fixture/name/base.apk'
            file.parent.mkdir(parents=True);file.write_bytes(PAYLOAD)
            for p in (owner,pkg,file.parent.parent,file.parent,file):
                os.chown(p,os.geteuid(),os.getegid());p.chmod(0o644 if p==file else 0o700)
            raw=json.dumps(self.value if value is None else value,separators=(',',':')).encode()
            path=base/'synthetic-report.json';path.write_bytes(raw)
            from scripts.probes.owner_native_gateway_environment import select
            home=Path('/Users/inert');env=select(home,home/'Library/Android/sdk/platform-tools/adb',base/'state',base/'evidence',
                dict(DIRECT_AUTH_FILE='/restricted/auth.json',DIRECT_CERT='/restricted/cert.pem',DIRECT_KEY='/restricted/key.pem',DIRECT_VIDEO_BACKEND='videotoolbox'))
            p=subprocess.Popen((str(self.binary),'--owned-fixture',str(owner),str(pkg),NONCE.decode(),hashlib.sha256(PAYLOAD).hexdigest(),mode,str(path)),
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0,env=env,close_fds=True)
            q=queue.Queue();eof=[threading.Event(),threading.Event()];errors=bytearray();rows=[]
            def read_output():
                try:
                    for line in p.stdout:q.put(json.loads(line))
                except Exception as e:q.put(e)
                finally:eof[0].set()
            def read_error():
                try:
                    for line in iter(lambda:p.stderr.read(4096),b''):errors.extend(line)
                finally:eof[1].set()
            ts=[threading.Thread(target=read_output),threading.Thread(target=read_error)]
            for t in ts:t.start()
            try:
                p.stdin.write(hg(1,0,len(PAYLOAD),hashlib.sha256(PAYLOAD).hexdigest().encode())+PAYLOAD+hg(2,1));p.stdin.flush()
                end=time.monotonic()+6;seq=0
                while not eof[0].is_set() or not q.empty():
                    try:row=q.get(timeout=.05)
                    except queue.Empty:
                        if time.monotonic()>end:self.fail('owned host fixture exceeded finite budget')
                        continue
                    if isinstance(row,Exception):raise row
                    rows.append(row)
                    if row['event']=='report_read':
                        if row['parsed']:p.stdin.write(hg(4,3));p.stdin.flush()
                        continue
                    if row['event']!='schedule':continue
                    wire=None
                    if row['phase']=='upload':wire=hg(3,2)
                    elif row['phase']=='install' and mode!='before_driver':wire=schedule(1,seq);seq+=1
                    elif row['status'] in ('started','pending'):wire=schedule(2,seq);seq+=1
                    # Qualify actual child output before DRIVER_DONE; parsed row
                    # only schedules the request and cannot provide permission.
                    if wire:p.stdin.write(wire);p.stdin.flush()
                    if row['phase']=='unknown':break
                p.wait(timeout=1)
                for t in ts:t.join(timeout=1)
                self.assertTrue(all(e.is_set() for e in eof));self.assertEqual(errors,b'')
            finally:
                if p.poll() is None:p.kill();p.wait(timeout=1)
                for s in (p.stdin,p.stdout,p.stderr):s.close()
                for t in ts:t.join(timeout=1)
            return rows,p.returncode,raw

    def test_actual_native_child_owned_bytes_digest_and_collect_once(self):
        rows,code,raw=self.owned('natural');self.assertEqual(code,0)
        read=[x for x in rows if x['event']=='report_read'];self.assertEqual(len(read),1)
        r=read[0];self.assertTrue(r['parsed']);self.assertTrue(r['repeat_refused']);self.assertFalse(r['unknown'])
        self.assertEqual(r['payload_sha256'],hashlib.sha256(raw).hexdigest());self.assertEqual(r['App_sha256'],'ab'*32)
        self.assertFalse(r['App_FD_verified']);self.assertFalse(r['permission']);self.assertFalse(r['release'])
    def test_opaque_old_fixture_has_no_helper_qualification(self):
        rows,code,_=self.owned('natural',{'fixture':True});self.assertEqual(code,2)
        read=[x for x in rows if x['event']=='report_read'];self.assertEqual(len(read),1)
        self.assertFalse(read[0]['parsed']);self.assertTrue(read[0]['unknown'])
    def test_before_driver_cancel_and_expired_natural_wait_refuse(self):
        for mode in ('before_driver','cancel_after_wait','expired'):
            rows,code,_=self.owned(mode);self.assertEqual(code,2)
            read=[x for x in rows if x['event']=='report_read'];self.assertEqual(len(read),1)
            self.assertFalse(read[0]['parsed']);self.assertTrue(read[0]['unknown'])
    def test_missing_footer_and_independent_later_attempt_refuse(self):
        for mode in ('missing_footer','later_Attempt'):
            rows,code,_=self.owned(mode);self.assertEqual(code,2)
            self.assertFalse(any(x['event']=='schedule' and x['phase']=='done' for x in rows))
    def test_invalid_actual_owned_helper_failure_cannot_advance_done(self):
        rows,code,_=self.owned('natural',self.changed('activity_running_after_leave',True));self.assertEqual(code,2)
        self.assertFalse(any(x['event']=='schedule' and x['phase']=='done' for x in rows))

    def test_production_activation_and_missing_callback_refusal(self):
        for args,code in (((),0),(('--execute',),2),(('--owned-fixture',),2)):
            p=subprocess.run((str(self.inert),*args),capture_output=True,timeout=2,env=self.env)
            self.assertEqual(p.returncode,code)
        p=subprocess.run((str(self.binary),'--missing-gates-fixture'),capture_output=True,timeout=2,env=self.env)
        self.assertEqual(p.returncode,0)

if __name__=='__main__':unittest.main()
