"""Actual retained host/native scheduling; all Android eligibility synthetic."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

from scripts.probes.owner_native_control_fixture import FixtureControl, ControlUnknown
from scripts.probes.owner_native_gateway_environment import select

ROOT=Path(__file__).resolve().parents[1]
PAYLOAD=b'public host APK standin\n'


class OwnedNativeControl(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build=tempfile.TemporaryDirectory(prefix='huoguo-control-build-')
        cls.binary=Path(cls.build.name)/'host-control';cls.inert=Path(cls.build.name)/'inert'
        compiler=shutil.which('clang') or shutil.which('cc')
        if not compiler: raise RuntimeError('host compiler required')
        for source,dest in ((ROOT/'tests/fixtures/helper_owner_control_fixture.c',cls.binary),
                (ROOT/'experiments/moonlight-v2/source-snapshot/helper_owner_control.c',cls.inert)):
            subprocess.run((compiler,'-std=c11','-O2','-Wall','-Wextra','-Werror',str(source),'-o',str(dest)),
                check=True,capture_output=True,timeout=20,env={'PATH':os.defpath,'HOME':str(Path.home()),'LANG':'C'})
            dest.chmod(0o700)
        cls.digest=hashlib.sha256(cls.binary.read_bytes()).hexdigest()

    @classmethod
    def tearDownClass(cls): cls.build.cleanup()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='huoguo-control-fixture-')
        self.base=Path(self.temp.name);self.owner=self.base/'owner';self.owner.mkdir()
        self.pkg=self.base/'pkg';self.file=self.pkg/'~~fixture/name/base.apk'
        self.file.parent.mkdir(parents=True);self.file.write_bytes(PAYLOAD)
        for p in (self.owner,self.pkg,self.file.parent.parent,self.file.parent,self.file):
            os.chown(p,os.geteuid(),os.getegid());p.chmod(0o644 if p==self.file else 0o700)
        home=Path('/Users/inert')
        self.env=select(home,home/'Library/Android/sdk/platform-tools/adb',self.base/'state',self.base/'evidence',
            dict(DIRECT_AUTH_FILE='/restricted/auth.json',DIRECT_CERT='/restricted/cert.pem',
                DIRECT_KEY='/restricted/key.pem',DIRECT_VIDEO_BACKEND='videotoolbox'))
        self.client=None;self.rows=[];self.calls=[]

    def tearDown(self):
        if self.client and self.client.process:
            p=self.client.process
            # Exact process created by this test only. Never remote recovery.
            if p.poll() is None:p.kill()
            p.wait(timeout=2)
            if not p.stdin.closed:p.stdin.close()
            for d in self.client.drains:d.thread.join(timeout=2)
        self.temp.cleanup()

    def make(self,mode='natural',qualify=None,checkpoint=None):
        def gate(label,client,end):
            self.calls.append((label,client.process));return True
        def record(row):self.rows.append(row);return True
        self.client=FixtureControl(self.binary,self.digest,self.env,checkpoint or record,qualify or gate)
        self.client.start_fixture(self.owner,self.pkg,'c1'*12,PAYLOAD,mode)
        return self.client

    def response(self):
        end=time.monotonic()+4
        while time.monotonic()<end:
            if self.client.poll_response():return
            time.sleep(.005)
        self.fail('bounded response not returned')

    def installed(self,mode='natural',**kwargs):
        c=self.make(mode,**kwargs);c.upload();self.response();c.install();self.response();return c

    def finish(self,c):
        c.start_driver();self.response();polls=0
        while c.phase=='running':
            c.poll_driver();self.response();polls+=1
        c.driver_done();self.response()
        end=time.monotonic()+1
        while c.process.poll() is None and time.monotonic()<end:time.sleep(.005)
        return c.collect_fixture_closure(),polls

    def test_actual_same_transport_control_dual_EOF_and_closed_schedule(self):
        c=self.installed();owned=c.process;row,polls=self.finish(c)
        self.assertIs(c.process,owned);self.assertEqual(owned.returncode,0);self.assertGreaterEqual(polls,1)
        self.assertTrue(row['actual_dual_EOF']);self.assertFalse(row['reservation_release'])
        self.assertFalse(row['Android_qualifiers']);self.assertFalse(row['remote_scope_cleanup'])
        self.assertTrue(any(p==owned for _,p in self.calls));self.assertTrue(self.owner.iterdir())

    def test_actual_five_seconds_multiple_commands_no_enlarged_deadline(self):
        c=self.installed('five_seconds');started=time.monotonic();_,polls=self.finish(c)
        self.assertGreaterEqual(time.monotonic()-started,5);self.assertGreaterEqual(polls,4)

    def test_fresh_host_gateway_gate_precedes_start_no_boolean_sent_to_native(self):
        def gate(label,client,end):return label!='before_start'
        c=self.installed(qualify=gate);size=c.sequence
        with self.assertRaises(ControlUnknown):c.start_driver()
        self.assertEqual(c.sequence,size);self.assertEqual(c.phase,'unknown');self.assertIsNotNone(c.process)

    def test_native_missing_gate_cannot_be_repaired_by_true_host_callback(self):
        c=self.installed('native_gate_missing');c.start_driver()
        with self.assertRaises(ControlUnknown):self.response()
        self.assertEqual(c.phase,'unknown');self.assertFalse(c.collected)

    def test_later_native_Attempt_refuses_after_actual_local_closure(self):
        c=self.installed('later_Attempt')
        with self.assertRaises(ControlUnknown):self.finish(c)
        self.assertEqual(c.phase,'unknown');self.assertFalse(c.collected)

    def test_single_outstanding_request_refuses_second_operation(self):
        c=self.make();c.upload()
        with self.assertRaises(ControlUnknown):c.install()
        self.assertEqual(c.phase,'unknown')

    def test_idle_poll_is_not_a_response_or_operation_ACK(self):
        c=self.make();self.assertFalse(c.poll_response())
        c.upload();self.response();c.install();self.response()
        self.assertFalse(c.poll_response())

    def test_duplicate_and_unhashable_extra_events_sticky_unknown(self):
        for mode in ('duplicate_event','extra_event'):
            with self.subTest(mode=mode):
                c=self.make(mode);c.upload()
                with self.assertRaises(ControlUnknown):
                    end=time.monotonic()+1
                    while time.monotonic()<end:
                        c.poll_response();time.sleep(.005)
                self.assertEqual(c.phase,'unknown')
                self.tearDown();self.setUp()

    def test_stderr_and_overflow_rejected_and_drained(self):
        for mode in ('stderr','overflow'):
            with self.subTest(mode=mode):
                c=self.make(mode);c.upload()
                with self.assertRaises(ControlUnknown):
                    self.response();time.sleep(.02);c.install()
                self.assertEqual(c.phase,'unknown')
                self.tearDown();self.setUp()

    def test_late_actual_response_even_if_already_buffered_refuses(self):
        c=self.make('late_response');c.upload();time.sleep(3.2)
        with self.assertRaises(ControlUnknown):c.poll_response()
        self.assertEqual(c.phase,'unknown')

    def test_timeout_keeps_actual_child_control_and_drains_without_signals(self):
        c=self.installed('no_response');c.start_driver();p=c.process
        with self.assertRaises(ControlUnknown):self.response()
        self.assertIs(c.process,p);self.assertIsNone(p.poll());self.assertFalse(p.stdin.closed)
        self.assertTrue(all(d.thread.is_alive() for d in c.drains))

    def test_pre_start_checkpoint_failure_no_process_created(self):
        def checkpoint(row):return False
        with self.assertRaises(ControlUnknown):self.make(checkpoint=checkpoint)
        self.assertIsNone(self.client.process)

    def test_post_start_checkpoint_failure_retains_actual_process_and_drains(self):
        def checkpoint(row):return row['possible_operation']!='transport_started'
        with self.assertRaises(ControlUnknown):self.make(checkpoint=checkpoint)
        self.assertIsNotNone(self.client.process);self.assertEqual(len(self.client.drains),2)
        self.assertEqual(self.client.phase,'unknown')

    def test_request_checkpoint_failure_sticks_before_write(self):
        def checkpoint(row):return row['possible_operation']!='possible_install'
        c=self.make(checkpoint=checkpoint);c.upload();self.response()
        with self.assertRaises(ControlUnknown):c.install()
        self.assertEqual(c.phase,'unknown');self.assertFalse(c.process.stdin.closed)

    def test_callback_interrupt_keeps_actual_transport(self):
        def gate(label,client,end):
            if label=='before_install':raise KeyboardInterrupt()
            return True
        c=self.make(qualify=gate);c.upload();self.response();owned=c.process
        with self.assertRaises(ControlUnknown):c.install()
        self.assertIs(c.process,owned);self.assertEqual(c.phase,'unknown')

    def test_binary_pin_or_ambient_environment_cannot_activate(self):
        with self.assertRaises(ValueError):FixtureControl(self.binary,self.digest,{**self.env,'PYTHONPATH':'/foreign'},lambda _:True,lambda *args:True)
        c=FixtureControl(self.binary,'0'*64,self.env,lambda _:True,lambda *args:True);self.client=c
        with self.assertRaises(ControlUnknown):c.start_fixture(self.owner,self.pkg,'c1'*12,PAYLOAD)
        self.assertIsNone(c.process)

    def test_inert_production_cli_has_no_activation(self):
        for args,code in (((),0),(('--execute',),2),(('--control-fixture',),2)):
            row=subprocess.run((str(self.inert),*args),capture_output=True,timeout=1,env=self.env)
            self.assertEqual(row.returncode,code)

    def test_same_child_collect_once_and_incomplete_order(self):
        c=self.installed();self.finish(c)
        with self.assertRaises(ControlUnknown):c.collect_fixture_closure()
        self.tearDown();self.setUp();c=self.installed()
        with self.assertRaises(ControlUnknown):c.collect_fixture_closure()
        self.assertEqual(c.phase,'unknown')

    def test_start_header_nonce_sequence_flags_and_queued_request_refused(self):
        for change in ('nonce','sequence','reserved','queued'):
            with self.subTest(change=change):
                c=self.installed();wire=b'HGDS0001'+bytes((1,))+b'\0'*7+c.nonce+(0).to_bytes(8,'big')
                if change=='nonce':wire=wire[:16]+b'0'*24+wire[40:]
                if change=='sequence':wire=wire[:40]+(1).to_bytes(8,'big')
                if change=='reserved':wire=wire[:9]+b'\1'+wire[10:]
                if change=='queued':wire+=wire
                os.write(c.process.stdin.fileno(),wire)
                # There is no outstanding valid request after this deliberate
                # foreign wire write. A poll's idle=True is not an ACK; wait
                # for the actual refusal event instead of treating idle as it.
                with self.assertRaises(ControlUnknown):
                    end=time.monotonic()+1
                    while time.monotonic()<end:
                        c.poll_response();time.sleep(.005)
                self.assertEqual(c.process.wait(timeout=1),2)
                self.assertFalse(c.collected)
                self.tearDown();self.setUp()


if __name__=='__main__':unittest.main()
