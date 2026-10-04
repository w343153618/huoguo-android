"""Actual host framed bridge retains a native parent across >3s driver work.

Events schedule requests only. The native callback independently checks its
actual sole forked host driver, waitpid and both EOFs. Android gates synthetic.
"""
import hashlib
import json
from pathlib import Path
import selectors
import shutil
import signal
import struct
import subprocess
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'experiments/moonlight-v2/source-snapshot/helper_owner_idle.c'
FIXTURE=ROOT/'tests/fixtures/helper_owner_idle_fixture.c'

class HelperIdleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler=shutil.which('clang') or shutil.which('cc')
        if not compiler: raise RuntimeError('host compiler required')
        cls.build=tempfile.TemporaryDirectory(prefix='huoguo-helper-idle-build-')
        cls.fixture=Path(cls.build.name)/'fixture';cls.production=Path(cls.build.name)/'production'
        for source,binary in ((SOURCE,cls.production),(FIXTURE,cls.fixture)):
            subprocess.run([compiler,'-std=c11','-O2','-Wall','-Wextra','-Werror',str(source),'-o',str(binary)],
                           check=True,capture_output=True,timeout=20)
    @classmethod
    def tearDownClass(cls): cls.build.cleanup()
    def setUp(self):
        self.base=tempfile.TemporaryDirectory(prefix='huoguo-helper-idle-scope-')
        self.path=Path(self.base.name);self.nonce=b'b'*24;self.payload=b'host public fixture\n'*20
        self.parent=self.path/('huoguo-helper-owner-'+self.nonce.decode())
        self.proc=None;self.pending=[];self.partial=b'';self.selector=selectors.DefaultSelector()
    def tearDown(self):
        if self.proc:
            # Actual owned fixture Popen only, never a production/discovered PID.
            if self.proc.poll() is None: self.proc.terminate()
            try: self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired: self.proc.kill();self.proc.wait(timeout=2)
            for pipe in (self.proc.stdin,self.proc.stdout,self.proc.stderr):
                if pipe and not pipe.closed: pipe.close()
        self.selector.close();self.base.cleanup()
    def header(self,kind,sequence,size=0,sha=None):
        return b'HGHC0001'+bytes([kind,0,0,0])+struct.pack('!I',size)+self.nonce+struct.pack('!Q',sequence)+(sha or b'0'*64)
    def send(self,data): self.proc.stdin.write(data);self.proc.stdin.flush()
    def event(self,name,seconds=2):
        names=(name,) if isinstance(name,str) else name
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            for i,row in enumerate(self.pending):
                if row.get('event') in names: return self.pending.pop(i)
            for key,_ in self.selector.select(max(0,end-time.monotonic())):
                chunk=key.fileobj.read(4096)
                if not chunk: self.selector.unregister(key.fileobj);continue
                self.partial+=chunk
                while b'\n' in self.partial:
                    line,self.partial=self.partial.split(b'\n',1);self.pending.append(json.loads(line))
        for i,row in enumerate(self.pending):
            if row.get('event') in names: return self.pending.pop(i)
        self.fail('bounded fixture event unavailable: '+repr(names)+'; pending='+repr(self.pending)
                  +'; actual_child_status='+str(self.proc.poll()))
    def start(self,mode):
        st=self.path.stat();sha=hashlib.sha256(self.payload).hexdigest()
        self.proc=subprocess.Popen([str(self.fixture),'--fixture',str(self.path),self.nonce.decode(),
            str(st.st_dev),str(st.st_ino),str(len(self.payload)),sha,mode],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0,close_fds=True)
        self.selector.register(self.proc.stdout,selectors.EVENT_READ)
        self.send(self.header(1,0,len(self.payload),sha.encode())+self.payload+self.header(2,1)+self.header(3,2))
        self.event('host_helper_installed')
    def finish(self,mode='natural',early=False):
        self.start(mode)
        if not early: self.event('host_driver_reaped',7 if mode=='five_seconds' else 2)
        self.send(self.header(4,3))
        row=self.event(('host_driver_done_checked','helper_idle_fixture_footer'))
        if row['event']=='host_driver_done_checked':
            self.send(self.header(5,4));row=self.event(('host_helper_uninstalled_checked','helper_idle_fixture_footer'))
            if row['event']=='host_helper_uninstalled_checked':
                self.send(self.header(6,5));row=self.event('helper_idle_fixture_footer')
        code=self.proc.wait(timeout=2);self.assertEqual(self.proc.stderr.read(),b'')
        self.assertFalse(row['release_authorized']);self.assertFalse(row['complete_live_binding_implemented'])
        self.assertTrue(row['Android_qualifiers_synthetic']);self.assertEqual(row['driver_signals'],0)
        return code,row
    def test_production_no_activation(self):
        for args,code in (([],0),(['--execute'],2),(['--fixture'],2)):
            r=subprocess.run([str(self.production),*args],capture_output=True,timeout=1)
            self.assertEqual(r.returncode,code)
            if not args: self.assertFalse(json.loads(r.stdout)['production_activation_available'])
        self.assertFalse(self.parent.exists())
    def test_retained_parent_and_control_across_actual_five_second_driver(self):
        code,row=self.finish('five_seconds');self.assertEqual(code,0)
        self.assertGreaterEqual(row['idle_ms'],4900);self.assertGreater(row['services'],10)
        self.assertTrue(row['driver_reaped']);self.assertTrue(row['driver_dual_EOF'])
        self.assertTrue(row['retired']);self.assertFalse(self.parent.exists())
    def test_natural_short_driver_then_new_bounded_phase_read(self):
        code,row=self.finish();self.assertEqual(code,0);self.assertTrue(row['closed'])
    def test_early_wire_cannot_stand_in_for_actual_driver(self):
        code,row=self.finish('early_frame',early=True);self.assertNotEqual(code,0)
        self.assertFalse(row['driver_done']);self.assertFalse(row['retired'])
        self.assertTrue(row['unknown_retains_scope_and_control'])
    def test_partial_command_has_its_own_bounded_read(self):
        self.start('partial_frame');self.event('host_driver_reaped');self.send(self.header(4,3)[:9])
        row=self.event('helper_idle_fixture_footer');self.proc.wait(timeout=2)
        self.assertFalse(row['driver_done']);self.assertTrue(row['readable'])
        self.assertTrue(row['unknown_retains_scope_and_control']);self.assertLess(row['idle_ms'],1500)
    def test_controller_EOF_retains_scope_not_prequeued_cleanup(self):
        self.start('control_EOF');self.proc.stdin.close()
        row=self.event('helper_idle_fixture_footer');self.proc.wait(timeout=2)
        self.assertFalse(row['readable']);self.assertFalse(row['retired'])
        self.assertTrue((self.parent/'stage/owned.apk').exists())
    def test_original_process_ceiling_exhaustion_no_signals_or_release(self):
        self.start('ceiling');row=self.event('helper_idle_fixture_footer');self.proc.wait(timeout=2)
        self.assertFalse(row['retired']);self.assertTrue(row['unknown_retains_scope_and_control'])
        self.assertEqual(row['driver_signals'],0)
    def test_service_unknown_is_sticky_and_holds_scope(self):
        self.start('service_unknown');row=self.event('helper_idle_fixture_footer');self.proc.wait(timeout=2)
        self.assertFalse(row['readable']);self.assertTrue(row['unknown_retains_scope_and_control'])
    def test_scope_change_during_idle_refuses_before_more_PM(self):
        self.start('scope_changed');row=self.event('helper_idle_fixture_footer');self.proc.wait(timeout=2)
        self.assertFalse(row['retired']);self.assertTrue((self.parent/'stage/owned.apk').exists())
    def test_actual_driver_wait_without_dual_EOF_does_not_qualify(self):
        code,row=self.finish('driver_EOF_unknown');self.assertNotEqual(code,0)
        self.assertTrue(row['driver_reaped']);self.assertFalse(row['driver_dual_EOF']);self.assertFalse(row['retired'])
    def test_actual_driver_nonzero_blocks_DRIVER_DONE(self):
        code,row=self.finish('driver_nonzero');self.assertNotEqual(code,0);self.assertFalse(row['driver_done'])
    def test_actual_driver_overflow_still_drains_but_refuses(self):
        code,row=self.finish('driver_overflow');self.assertNotEqual(code,0);self.assertTrue(row['driver_dual_EOF'])
        self.assertFalse(row['driver_done'])
    def test_later_Attempt_blocks_cleanup_after_natural_driver_exit(self):
        code,row=self.finish('later_Attempt');self.assertNotEqual(code,0);self.assertFalse(row['retired'])
    def test_cancel_sets_unknown_without_signaling_driver(self):
        self.start('cancel');self.proc.send_signal(signal.SIGTERM)
        row=self.event('helper_idle_fixture_footer');self.proc.wait(timeout=2)
        self.assertTrue(row['unknown_retains_scope_and_control']);self.assertEqual(row['driver_signals'],0)
    def test_idle_cannot_be_reentered_to_reset_process_budget(self):
        code,row=self.finish('repeat_idle');self.assertNotEqual(code,0);self.assertTrue(row['repeat_refused'])
        self.assertTrue(row['unknown_retains_scope_and_control'])
    def test_constructor_bound_once_to_original_lifecycle_clock(self):
        st=self.path.stat();sha=hashlib.sha256(self.payload).hexdigest()
        r=subprocess.run([str(self.fixture),'--fixture',str(self.path),self.nonce.decode(),
            str(st.st_dev),str(st.st_ino),str(len(self.payload)),sha,'contract'],capture_output=True,timeout=1)
        self.assertEqual(r.returncode,0);row=json.loads(r.stdout)
        self.assertTrue(row['invalid_refused']);self.assertTrue(row['original_ceiling_once'])
        self.assertFalse(row['scope_created']);self.assertFalse(self.parent.exists())
    def test_command_read_over_three_seconds_refused_without_scope_retirement(self):
        code,row=self.finish('long_deadline');self.assertNotEqual(code,0)
        self.assertFalse(row['driver_done']);self.assertTrue(row['unknown_retains_scope_and_control'])
    def test_queued_cleanup_plus_controller_EOF_is_unknown(self):
        self.start('control_EOF');self.event('host_driver_reaped')
        self.send(self.header(4,3)+self.header(5,4)+self.header(6,5));self.proc.stdin.close()
        row=self.event('helper_idle_fixture_footer');self.proc.wait(timeout=2)
        self.assertFalse(row['retired']);self.assertTrue((self.parent/'stage/owned.apk').exists())

if __name__=='__main__': unittest.main()
