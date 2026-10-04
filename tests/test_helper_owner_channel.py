"""Actual host duplex FD/strict framing checks; requests never prove authority."""
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import signal
import struct
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'experiments/moonlight-v2/source-snapshot/helper_owner_channel.c'

class HelperChannelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler=shutil.which('clang') or shutil.which('cc')
        if not compiler: raise RuntimeError('host compiler required')
        cls.build=tempfile.TemporaryDirectory(prefix='huoguo-helper-channel-build-')
        cls.fixture=Path(cls.build.name)/'fixture';cls.production=Path(cls.build.name)/'production'
        for binary,flags in [(cls.fixture,['-DHG_HELPER_CHANNEL_FIXTURE','-DHG_HELPER_OWNER_FIXTURE']), (cls.production,[])]:
            subprocess.run([compiler,'-std=c11','-O2','-Wall','-Wextra','-Werror',*flags,
                            str(SOURCE),'-o',str(binary)],capture_output=True,check=True,timeout=20)
    @classmethod
    def tearDownClass(cls): cls.build.cleanup()
    def setUp(self):
        self.base=tempfile.TemporaryDirectory(prefix='huoguo-helper-channel-scope-')
        self.path=Path(self.base.name);self.nonce=b'd'*24
        self.parent=self.path/('huoguo-helper-owner-'+self.nonce.decode())
        self.apk=self.parent/'stage/owned.apk'
        self.data=b'host public test payload, no real APK\n'*8
        self.children=[]
    def tearDown(self):
        for child in self.children:
            if child.poll() is None: child.kill()
            child.wait(timeout=3)
            for f in [child.stdin,child.stdout,child.stderr]:
                if f and not f.closed: f.close()
        self.base.cleanup()
    def header(self,kind,seq,*,size=0,nonce=None,sha=None):
        return b'HGHC0001'+bytes([kind,0,0,0])+struct.pack('!I',size)+(self.nonce if nonce is None else nonce)+struct.pack('!Q',seq)+(b'0'*64 if sha is None else sha)
    def upload(self):
        return self.header(1,0,size=len(self.data),sha=hashlib.sha256(self.data).hexdigest().encode())+self.data+self.header(2,1)
    def commands(self): return b''.join(self.header(kind,kind-1) for kind in range(3,7))
    def start(self,production=False,args=None):
        st=self.path.stat()
        words=['--fixture',str(self.path),self.nonce.decode(),str(st.st_dev),str(st.st_ino),
               str(len(self.data)),hashlib.sha256(self.data).hexdigest()]
        child=subprocess.Popen([str(self.production if production else self.fixture),*(words if args is None else args)],
                               stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,close_fds=True,shell=False)
        self.children.append(child);return child
    def run_bytes(self,data,production=False,args=None):
        child=self.start(production,args);out,err=child.communicate(data,timeout=4)
        self.assertEqual(err,b'');rows=[json.loads(x) for x in out.splitlines()]
        for row in rows: self.assertFalse(row['release_authorized'])
        return child.returncode,rows
    def test_defaults_and_Android_activation_inert(self):
        for production in [False,True]:
            code,rows=self.run_bytes(b'',production,args=[])
            self.assertEqual(code,0);self.assertFalse(rows[0]['operations_started'])
        code,rows=self.run_bytes(b'',True)
        self.assertNotEqual(code,0);self.assertEqual(rows,[]);self.assertFalse(self.parent.exists())
    def test_upload_seal_then_requests_keep_control_FD_open(self):
        child=self.start();child.stdin.write(self.upload());child.stdin.flush()
        self.assertTrue(select.select([child.stdout],[],[],2)[0])
        first=json.loads(child.stdout.readline())
        self.assertTrue(first['upload_verified']);self.assertTrue(first['writer_closed'])
        self.assertTrue(first['control_FD_still_open']);self.assertFalse(child.stdin.closed)
        self.assertEqual(self.apk.read_bytes(),self.data)
        child.stdin.write(self.commands());child.stdin.flush();child.wait(timeout=3)
        rows=[json.loads(x) for x in child.stdout.read().splitlines()]
        self.assertEqual(child.returncode,0,rows)
        self.assertEqual([r['request'] for r in rows if 'request' in r],[3,4,5,6])
        self.assertTrue(rows[-1]['wire_sequence_complete']);self.assertEqual(rows[-1]['PM_invocations'],0)
        self.assertTrue(rows[-1]['scope_may_remain']);self.assertTrue(self.apk.exists())
        self.assertFalse(rows[-1]['retirement_performed'])
    def test_read_short_fragments_without_requiring_EOF(self):
        child=self.start()
        for block in [self.upload()[i:i+7] for i in range(0,len(self.upload()),7)]:
            child.stdin.write(block);child.stdin.flush()
        child.stdin.write(self.commands());child.stdin.flush();child.wait(timeout=3)
        rows=[json.loads(x) for x in child.stdout.read().splitlines()]
        self.assertEqual(child.returncode,0,rows);self.assertEqual(self.apk.read_bytes(),self.data)
    def test_wrong_magic_flags_or_kind_refuse_before_creation(self):
        for index,value in [(0,88),(8,3),(9,1),(10,1),(11,1)]:
            data=bytearray(self.upload());data[index]=value
            code,rows=self.run_bytes(bytes(data))
            self.assertNotEqual(code,0);self.assertFalse(self.parent.exists())
    def test_wrong_initial_nonce_sequence_size_or_pin_refuse_before_creation(self):
        variants=[self.header(1,1,size=len(self.data),sha=hashlib.sha256(self.data).hexdigest().encode()),
                  self.header(1,0,size=len(self.data),nonce=b'e'*24,sha=hashlib.sha256(self.data).hexdigest().encode()),
                  self.header(1,0,size=len(self.data)+1,sha=hashlib.sha256(self.data).hexdigest().encode()),
                  self.header(1,0,size=len(self.data),sha=b'f'*64)]
        for data in variants:
            code,rows=self.run_bytes(data+self.data)
            self.assertNotEqual(code,0);self.assertFalse(self.parent.exists())
    def test_truncated_header_no_scope(self):
        code,rows=self.run_bytes(self.upload()[:111])
        self.assertNotEqual(code,0);self.assertFalse(self.parent.exists())
    def test_partial_payload_keeps_created_scope(self):
        code,rows=self.run_bytes(self.upload()[:112+7])
        self.assertNotEqual(code,0);self.assertFalse(rows[0]['upload_verified'])
        self.assertTrue(rows[-1]['scope_may_remain']);self.assertTrue(self.apk.exists())
    def test_changed_payload_hash_refuses_before_requests(self):
        data=bytearray(self.upload());data[112]^=1
        code,rows=self.run_bytes(bytes(data)+self.commands())
        self.assertNotEqual(code,0);self.assertTrue(rows[0]['writer_closed'])
        self.assertFalse(rows[0]['upload_verified']);self.assertEqual(rows[-1]['requests'],0)
    def test_no_seal_or_extra_payload_byte_cannot_use_EOF_as_success(self):
        for i,data in enumerate([self.upload()[:-112],self.upload()[:-112]+b'X'+self.header(2,1)]):
            if i: shutil.rmtree(self.parent) # this test's own host scope only
            code,rows=self.run_bytes(data)
            self.assertNotEqual(code,0);self.assertFalse(rows[0]['upload_verified'])
            self.assertTrue(rows[-1]['scope_may_remain'])
    def test_skip_or_replayed_command_rejects_and_retains(self):
        for i,data in enumerate([self.header(5,2),self.header(3,1),self.header(3,2,nonce=b'e'*24),self.header(3,2,size=1)]):
            if i: shutil.rmtree(self.parent)
            code,rows=self.run_bytes(self.upload()+data)
            self.assertNotEqual(code,0);self.assertTrue(rows[0]['upload_verified'])
            self.assertEqual(rows[-1]['PM_invocations'],0);self.assertTrue(rows[-1]['scope_may_remain'])
    def test_EOF_after_seal_not_whole_sequence_completion(self):
        code,rows=self.run_bytes(self.upload())
        self.assertNotEqual(code,0);self.assertTrue(rows[0]['upload_verified'])
        self.assertFalse(rows[-1]['wire_sequence_complete']);self.assertTrue(self.apk.exists())
    def test_open_partial_header_timeout_retains(self):
        child=self.start();child.stdin.write(self.upload()[:113]);child.stdin.flush();child.wait(timeout=3)
        rows=[json.loads(x) for x in child.stdout.read().splitlines()]
        self.assertNotEqual(child.returncode,0);self.assertFalse(rows[0]['upload_verified'])
        self.assertTrue(rows[-1]['scope_may_remain'])
    def test_closed_cancel_request_does_not_install_or_remove(self):
        code,rows=self.run_bytes(self.upload()+self.header(7,2))
        self.assertNotEqual(code,0);self.assertEqual(rows[1]['request'],7)
        self.assertEqual(rows[-1]['PM_invocations'],0);self.assertFalse(rows[-1]['retirement_performed'])
        self.assertEqual(self.apk.read_bytes(),self.data)

    def test_parent_cancel_after_seal_does_not_claim_cleanup(self):
        child=self.start();child.stdin.write(self.upload());child.stdin.flush()
        self.assertTrue(select.select([child.stdout],[],[],2)[0])
        row=json.loads(child.stdout.readline());self.assertTrue(row['upload_verified'])
        child.send_signal(signal.SIGTERM) # exact held host Popen, never receipt PID
        child.wait(timeout=3)
        rows=[json.loads(x) for x in child.stdout.read().splitlines()]
        self.assertNotEqual(child.returncode,0)
        self.assertFalse(rows[-1]['wire_sequence_complete'])
        self.assertEqual(rows[-1]['PM_invocations'],0)
        self.assertTrue(rows[-1]['scope_may_remain'])
        self.assertEqual(self.apk.read_bytes(),self.data)

    def test_max_bounded_payload_hash_and_control_are_separate(self):
        self.data=bytes(i % 251 for i in range(131072))
        code,rows=self.run_bytes(self.upload()+self.commands())
        self.assertEqual(code,0,rows);self.assertTrue(rows[0]['writer_closed'])
        self.assertTrue(rows[-1]['wire_sequence_complete'])
        self.assertEqual(self.apk.read_bytes(),self.data)
        self.assertFalse(rows[-1]['driver_cleanup_verified'])

if __name__=='__main__': unittest.main()
