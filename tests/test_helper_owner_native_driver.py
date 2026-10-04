"""Source-only host ownership fixtures. Android/UI/eligibility output is synthetic."""
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
DIRECTORY=ROOT/'experiments/moonlight-v2/source-snapshot'
sys.path.insert(0,str(ROOT))
from scripts.probes.owner_native_gateway_environment import select

PAYLOAD=b'public host APK standin\n'
NONCE=b'd0'*12
def frame(kind,sequence,size=0,digest=b'0'*64):
    return b'HGHC0001'+bytes([kind,0,0,0])+struct.pack('!I',size)+NONCE+struct.pack('!Q',sequence)+digest

class NativeOwnedDriver(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build=tempfile.TemporaryDirectory(prefix='huoguo-native-driver-build-')
        cls.host=Path(cls.build.name)/'host';cls.inert=Path(cls.build.name)/'inert'
        compiler=shutil.which('clang') or shutil.which('cc')
        if not compiler: raise RuntimeError('host compiler required')
        for source,dest in ((DIRECTORY/'helper_owner_native_driver.c',cls.inert),(ROOT/'tests/fixtures/helper_owner_native_driver_fixture.c',cls.host)):
            subprocess.run([compiler,'-std=c11','-O2','-Wall','-Wextra','-Werror',str(source),'-o',str(dest)],
                capture_output=True,check=True,timeout=20,
                env={'PATH':os.defpath,'HOME':str(Path.home()),'LANG':'C'})
    @classmethod
    def tearDownClass(cls): cls.build.cleanup()
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='huoguo-native-driver-fixture-')
        self.base=Path(self.temp.name);self.owner=self.base/'owner';self.owner.mkdir()
        self.pkg=self.base/'pkg';self.file=self.pkg/'~~fixture/name/base.apk'
        self.file.parent.mkdir(parents=True);self.file.write_bytes(PAYLOAD)
        for p in (self.owner,self.pkg,self.file.parent.parent,self.file.parent,self.file):
            os.chown(p,os.geteuid(),os.getegid());p.chmod(0o644 if p==self.file else 0o700)
        home=Path('/Users/inert')
        self.env=select(home,home/'Library/Android/sdk/platform-tools/adb',self.base/'state',self.base/'evidence',
            dict(DIRECT_AUTH_FILE='/restricted/auth.json',DIRECT_CERT='/restricted/cert.pem',
                DIRECT_KEY='/restricted/key.pem',DIRECT_VIDEO_BACKEND='videotoolbox'))
    def tearDown(self): self.temp.cleanup()
    def run_fixture(self,mode,accepted=False):
        digest=hashlib.sha256(PAYLOAD).hexdigest()
        child=subprocess.Popen([str(self.host),'--fixture',str(self.owner),str(self.pkg),mode,digest],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0,
            close_fds=True,env=self.env)
        lines=queue.Queue();errors=bytearray();stdout_eof=threading.Event();stderr_eof=threading.Event()
        def stdout():
            try:
                for line in child.stdout: lines.put(json.loads(line))
            except Exception as error: lines.put(error)
            finally: stdout_eof.set()
        def stderr():
            try:
                for part in iter(lambda:child.stderr.read(4096),b''): errors.extend(part)
            finally: stderr_eof.set()
        threads=[threading.Thread(target=stdout),threading.Thread(target=stderr)]
        for t in threads:t.start()
        try:
            wire=frame(1,0,len(PAYLOAD),digest.encode())+PAYLOAD+frame(2,1)+frame(3,2)
            # A constructor refusal can exit before any payload write. Force
            # that real race in its fixture and still collect the same child,
            # final closed refusal record and both actual EOFs.
            if mode=='null_gate':self.assertTrue(stdout_eof.wait(timeout=1))
            write_failed=False
            try:child.stdin.write(wire);child.stdin.flush()
            except BrokenPipeError:write_failed=True
            end=time.monotonic()+12
            final=None;events=[]
            while final is None:
                row=lines.get(timeout=max(.01,end-time.monotonic()))
                if isinstance(row,Exception): raise row
                if 'event' not in row: final=row;break
                events.append(row['event'])
                if row['event']=='driver_started':
                    if mode=='early_done': child.stdin.write(frame(4,3));child.stdin.flush()
                    if mode=='control_EOF': child.stdin.close()
                elif row['event']=='driver_local_closed' and mode!='control_EOF':
                    if mode=='trailing_request':
                        child.stdin.write(frame(4,3)+frame(5,4))
                    else: child.stdin.write(frame(4,3))
                    child.stdin.flush()
            code=child.wait(timeout=max(.01,end-time.monotonic()))
            for t in threads:t.join(timeout=1)
            self.assertTrue(stdout_eof.is_set());self.assertTrue(stderr_eof.is_set());self.assertEqual(errors,b'')
            if write_failed:self.assertFalse(final['constructed'])
            self.assertEqual(code,0 if accepted else 2,final)
            for k in ('release_authorized',): self.assertFalse(final[k])
            if final['constructed']:
                for k in ('actual_Mac_coordinator_bridge','actual_Android_PM_driver','production_activation_available',
                          'uninstall_retirement_or_release'): self.assertFalse(final[k])
                self.assertTrue(final['scope_retained']);self.assertEqual(final['driver_signal_calls'],0)
            return final,events
        finally:
            # Fixture's sole actual host Popen only, never remote recovery.
            if child.poll() is None: child.kill();child.wait(timeout=1)
            for pipe in (child.stdin,child.stdout,child.stderr):
                if not pipe.closed:pipe.close()
            for t in threads:t.join(timeout=1)
    def test_inert_cli_and_activation_refusal(self):
        for args,code in (([],0),(['--execute'],2),(['--fixture'],2)):
            r=subprocess.run([str(self.inert),*args],capture_output=True,timeout=1,env=self.env)
            self.assertEqual(r.returncode,code)
            if not args:self.assertFalse(json.loads(r.stdout)['operations_started'])
    def test_actual_same_native_parent_driver_natural_wait_dual_EOF(self):
        row,_=self.run_fixture('natural',True)
        self.assertTrue(row['actual_native_child_reaped']);self.assertTrue(row['actual_dual_EOF'])
        self.assertTrue(row['driver_done_qualified'])
    def test_actual_five_seconds_across_bounded_polls(self):
        row,_=self.run_fixture('five_seconds',True)
        self.assertGreaterEqual(row['elapsed_ms'],5000);self.assertGreater(row['poll_calls'],2)
    def test_missing_native_callback_constructor_refuses(self):
        row,_=self.run_fixture('null_gate');self.assertFalse(row['constructed']);self.assertFalse(row['started'])
    def test_missing_operator_cannot_start_from_valid_packages(self):
        row,_=self.run_fixture('missing_operator');self.assertFalse(row['started']);self.assertTrue(row['unknown'])
    def test_local_child_zero_footer_cannot_supply_current_Attempt(self):
        row,_=self.run_fixture('later_Attempt');self.assertTrue(row['actual_native_child_reaped']);self.assertTrue(row['unknown'])
    def test_local_child_zero_footer_cannot_supply_input_cleanup(self):
        row,_=self.run_fixture('input_unknown');self.assertFalse(row['driver_done_qualified'])
    def test_local_child_zero_footer_cannot_supply_codec_cleanup(self):
        row,_=self.run_fixture('codec_unknown');self.assertFalse(row['driver_done_qualified'])
    def test_missing_independent_normal_cleanup(self):
        row,_=self.run_fixture('missing_cleanup');self.assertFalse(row['driver_done_qualified'])
    def test_later_installed_package_refuses_after_driver(self):
        row,_=self.run_fixture('later_package');self.assertTrue(row['unknown'])
    def test_early_DRIVER_DONE_cannot_replace_actual_child_exit(self):
        row,_=self.run_fixture('early_done');self.assertTrue(row['unreaped_retained']);self.assertTrue(row['unknown'])
    def test_nonzero_stderr_overflow_and_missing_or_duplicate_footer(self):
        for mode in ('nonzero','stderr','overflow','missing_footer','duplicate_footer'):
            with self.subTest(mode=mode):
                row,_=self.run_fixture(mode);self.assertTrue(row['unknown'])
                # Each independent fixture needs fresh own scope/base.
                shutil.rmtree(self.owner);self.owner.mkdir();os.chown(self.owner,os.geteuid(),os.getegid());self.owner.chmod(0o700)
    def test_waited_child_with_inherited_pipe_is_not_closed(self):
        row,_=self.run_fixture('held_EOF');self.assertTrue(row['actual_native_child_reaped'])
        self.assertFalse(row['actual_dual_EOF']);self.assertFalse(row['driver_done_qualified'])
    def test_cancel_and_ignored_TERM_retain_actual_child_without_signals(self):
        for mode in ('cancel','ignore_TERM'):
            with self.subTest(mode=mode):
                row,_=self.run_fixture(mode);self.assertTrue(row['unreaped_retained']);self.assertTrue(row['unknown'])
                shutil.rmtree(self.owner);self.owner.mkdir();os.chown(self.owner,os.geteuid(),os.getegid());self.owner.chmod(0o700)
    def test_controller_EOF_refuses_DRIVER_DONE_after_actual_exit(self):
        row,_=self.run_fixture('control_EOF');self.assertTrue(row['unknown']);self.assertFalse(row['driver_done_qualified'])
    def test_trailing_request_cannot_advance_cleanup(self):
        row,_=self.run_fixture('trailing_request');self.assertTrue(row['unknown']);self.assertFalse(row['driver_done_qualified'])
    def test_child_closes_inherited_high_FD(self):
        row,_=self.run_fixture('high_FD',True);self.assertTrue(row['actual_dual_EOF'])
    def test_insufficient_original_process_ceiling_cannot_start_driver(self):
        row,_=self.run_fixture('insufficient_ceiling');self.assertFalse(row['started']);self.assertTrue(row['unknown'])
    def test_cancel_remains_unknown_after_actual_child_natural_exit_and_EOF(self):
        row,_=self.run_fixture('cancel_then_natural');self.assertTrue(row['actual_native_child_reaped'])
        self.assertTrue(row['actual_dual_EOF']);self.assertTrue(row['unknown']);self.assertFalse(row['driver_done_qualified'])
    def test_late_first_collection_of_actual_child_after_deadline_refuses(self):
        row,_=self.run_fixture('late_first_poll');self.assertTrue(row['actual_native_child_reaped'])
        self.assertTrue(row['actual_dual_EOF']);self.assertTrue(row['unknown']);self.assertFalse(row['driver_done_qualified'])

if __name__=='__main__':unittest.main()
