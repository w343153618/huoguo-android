import hashlib,json,os,shutil,struct,subprocess,tempfile,unittest
from pathlib import Path
from scripts.probes.owner_native_gateway_environment import select

ROOT=Path(__file__).resolve().parents[1]
DIRECTORY=ROOT/'experiments/moonlight-v2/source-snapshot'
PAYLOAD=b'public host APK standin\n'
class BoundNativeReadChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler=shutil.which('clang') or shutil.which('cc')
        if not cls.compiler: raise RuntimeError('host compiler required')
        cls.build=tempfile.TemporaryDirectory(prefix='huoguo-bound-read-build-')
        cls.prod=Path(cls.build.name)/'inert';cls.host=Path(cls.build.name)/'host'
        for src,exe in ((DIRECTORY/'helper_owner_read_gate.c',cls.prod),(ROOT/'tests/fixtures/helper_owner_read_gate_fixture.c',cls.host)):
            subprocess.run([cls.compiler,'-std=c11','-O2','-Wall','-Wextra','-Werror',str(src),'-o',str(exe)],
                           check=True,capture_output=True,timeout=20,
                           env={'PATH':os.defpath,'HOME':str(Path.home()),'LANG':'C'})
    @classmethod
    def tearDownClass(cls): cls.build.cleanup()
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='huoguo-bound-read-host-')
        self.root=Path(self.temp.name);self.owner=self.root/'owner';self.owner.mkdir()
        self.pkg=self.root/'pkg';self.pkg.mkdir();self.file=self.pkg/'~~fixture/name/base.apk'
        self.file.parent.mkdir(parents=True);self.file.write_bytes(PAYLOAD)
        for p in (self.owner,self.pkg,self.file.parent.parent,self.file.parent,self.file):
            os.chown(p,os.geteuid(),os.getegid());p.chmod(0o644 if p==self.file else 0o700)
        home=Path('/Users/inert')
        self.env=select(home,home/'Library/Android/sdk/platform-tools/adb',self.root/'state',self.root/'evidence',
            dict(DIRECT_AUTH_FILE='/restricted/auth.json',DIRECT_CERT='/restricted/cert.pem',
                 DIRECT_KEY='/restricted/key.pem',DIRECT_VIDEO_BACKEND='videotoolbox'))
    def tearDown(self): self.temp.cleanup()
    def run_host(self,mode,accepted=False):
        sha=hashlib.sha256(PAYLOAD).hexdigest();nonce=b'c0'*12
        def h(kind,sequence,size=0,digest=b'0'*64):
            return b'HGHC0001'+bytes([kind,0,0,0])+struct.pack('!I',size)+nonce+struct.pack('!Q',sequence)+digest
        wire=h(1,0,len(PAYLOAD),sha.encode())+PAYLOAD+h(2,1)+h(3,2)
        r=subprocess.run([str(self.host),'--fixture',str(self.owner),str(self.pkg),mode,sha],
                         input=wire,capture_output=True,timeout=7,env=self.env,close_fds=True)
        self.assertEqual(r.stderr,b'');rows=[json.loads(line) for line in r.stdout.splitlines()]
        row=rows[-1];self.assertEqual(r.returncode,0 if accepted else 2,row)
        self.assertFalse(row['release_authorized'])
        if row['constructed']:
            for k in ('actual_Android_PM','actual_coordinator_driver','complete_live_bridge','uninstall_or_retirement'):
                self.assertFalse(row[k])
            self.assertTrue(row['owner_close_refused'])
        return row
    def test_production_no_activation(self):
        for args,code in (([],0),(['--execute'],2),(['--fixture'],2)):
            r=subprocess.run([str(self.prod),*args],capture_output=True,timeout=1,env=self.env)
            self.assertEqual(r.returncode,code)
            if not args: self.assertFalse(json.loads(r.stdout)['operations_started'])
    def test_same_process_reads_before_and_after_actual_install_standin(self):
        row=self.run_host('natural',True);self.assertEqual(row['completed_native_reads'],6)
        self.assertEqual(row['independent_calls'],3);self.assertTrue(row['actual_helper_PM_standin_reaped'])
    def test_missing_admission_no_read_child_or_scope(self):
        row=self.run_host('missing_admission');self.assertEqual(row['completed_native_reads'],0)
        self.assertFalse(row['scope_created'])
    def test_native_artifact_success_cannot_supply_missing_operator(self):
        row=self.run_host('missing_operator');self.assertEqual(row['completed_native_reads'],2)
        self.assertTrue(row['scope_created']);self.assertFalse(row['actual_helper_PM_standin_reaped'])
    def test_install_PM_Success_cannot_supply_independent_after_install_gate(self):
        row=self.run_host('missing_after_install');self.assertTrue(row['actual_helper_PM_standin_reaped'])
        self.assertFalse(row['install_host_standin_qualified'])
    def test_later_App_or_package_read_unknown_after_install(self):
        for mode in ('later_active','later_package','package_unknown'):
            row=self.run_host(mode);self.assertTrue(row['unknown']);self.assertTrue(row['scope_created'])
            shutil.rmtree(self.owner);self.owner.mkdir();os.chown(self.owner,os.geteuid(),os.getegid());self.owner.chmod(0o700)
    def test_timeout_retains_actual_query_and_native_owner_scope(self):
        row=self.run_host('query_timeout');self.assertTrue(row['query_unreaped'])
        self.assertTrue(row['actual_query_retained']);self.assertTrue(row['unknown']);self.assertTrue(row['scope_created'])
    def test_missing_same_process_independent_callback_no_constructor_or_ops(self):
        row=self.run_host('null_callback');self.assertFalse(row['constructed']);self.assertEqual(row['actual_queries'],0)

if __name__=='__main__': unittest.main()
