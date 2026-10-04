"""Actual host FD/child observations; Android metadata and permission remain synthetic."""
import copy,hashlib,json,os,queue,shutil,subprocess,tempfile,threading,time,unittest
from pathlib import Path
from tests.test_helper_owner_report import hg,schedule,PAYLOAD,NONCE
ROOT=Path(__file__).resolve().parents[1]
APP_BYTES=b'{"audio_cleanup_confirmed":1,"fixture":true}'
class AppReportFD(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build=tempfile.TemporaryDirectory(prefix='huoguo-App-FD-build-');cls.binary=Path(cls.build.name)/'host-fixture';cls.inert=Path(cls.build.name)/'host-inert';cls.env={'PATH':os.defpath,'HOME':str(Path.home()),'LANG':'C'}
        compiler=shutil.which('clang') or shutil.which('cc')
        for source,target in ((ROOT/'tests/fixtures/helper_owner_App_report_fixture.c',cls.binary),(ROOT/'experiments/moonlight-v2/source-snapshot/helper_owner_App_report.c',cls.inert)):
            r=subprocess.run((compiler,'-std=c11','-O2','-Wall','-Wextra','-Werror',str(source),'-o',str(target)),capture_output=True,timeout=20,env=cls.env)
            if r.returncode:raise RuntimeError(r.stderr.decode())
        cls.value=json.loads((ROOT/'tests/fixtures/helper_single_window_report.json').read_text())
    @classmethod
    def tearDownClass(cls):cls.build.cleanup()
    def owned(self,mode,value=None):
        with tempfile.TemporaryDirectory(prefix='helper-report-owned-fixture-') as directory:
            base=Path(directory);os.chown(base,os.geteuid(),os.getegid());owner=base/'owner';owner.mkdir();pkg=base/'pkg';file=pkg/'~~fixture/name/base.apk'
            file.parent.mkdir(parents=True);file.write_bytes(PAYLOAD)
            for p in (owner,pkg,file.parent.parent,file.parent,file):
                os.chown(p,os.geteuid(),os.getegid());p.chmod(0o644 if p==file else 0o700)
            app=base/'data/user/0/local.remoteandroid.direct.experiment/files/udp-app-last-report.json'
            app.parent.mkdir(parents=True);app.write_bytes(APP_BYTES);app.chmod(0o600)
            for p in [base/'data',base/'data/user',base/'data/user/0',app.parent.parent,app.parent,app]:
                os.chown(p,os.geteuid(),os.getegid());p.chmod(0o600 if p==app else 0o700)
            if mode=='symlink':app.rename(app.with_name('actual'));app.symlink_to('actual')
            if mode=='hardlink':os.link(app,app.with_name('second'))
            if mode=='world_readable':app.chmod(0o644)
            if mode=='empty':app.write_bytes(b'')
            if mode=='oversize':app.write_bytes(b'x'*65537)
            if mode=='changed_App':app.write_bytes(APP_BYTES+b' ')
            value=copy.deepcopy(self.value if value is None else value)
            value['first_App_report_sha256']=hashlib.sha256(APP_BYTES).hexdigest()
            raw=json.dumps(value,separators=(',',':')).encode()
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
                    if row['event']=='App_FD_read':
                        if row['observed']:p.stdin.write(hg(4,3));p.stdin.flush()
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


    def observe(self,mode,expected):
        rows,code,raw=self.owned(mode);self.assertEqual(code,0 if expected else 2)
        app=[r for r in rows if r['event']=='App_FD_read'];self.assertEqual(len(app),1)
        r=app[0];self.assertEqual(r['observed'],expected);self.assertEqual(r['unknown'],not expected)
        self.assertTrue(r['repeat_refused']);self.assertFalse(r['atomic_hold']);self.assertFalse(r['permission']);self.assertFalse(r['release']);self.assertFalse(r['App_fields_verified'])
        return r
    def test_actual_FD_read_matches_actual_owned_helper_digest(self):
        r=self.observe('natural',True);self.assertEqual(r['sha256'],hashlib.sha256(APP_BYTES).hexdigest());self.assertEqual(r['queries'],2);self.assertTrue(r['retained_file_FD'])
    def test_symlink_refuses(self):self.observe('symlink',False)
    def test_hardlink_refuses(self):self.observe('hardlink',False)
    def test_foreign_mode_refuses(self):self.observe('world_readable',False)
    def test_foreign_UID_GID_refuses(self):self.observe('wrong_UID',False)
    def test_empty_refuses(self):self.observe('empty',False)
    def test_oversize_refuses(self):self.observe('oversize',False)
    def test_changed_App_bytes_refuses(self):self.observe('changed_App',False)
    def test_replace_after_read_retains_actual_old_FD(self):self.assertTrue(self.observe('replace_after_read',False)['retained_file_FD'])
    def test_growth_after_read_retains_actual_FD(self):self.assertTrue(self.observe('grow_after_read',False)['retained_file_FD'])
    def test_mode_after_read_retains_actual_FD(self):self.assertTrue(self.observe('mode_after_read',False)['retained_file_FD'])
    def test_later_UID_callback_refuses(self):self.observe('later_uid',False)
    def test_missing_independent_qualifier_refuses(self):self.observe('missing_qualifier',False)
    def test_expired_read_cannot_borrow_good_helper(self):self.observe('expired_App_read',False)
    def test_inert_production_and_missing_constructor_callback(self):
        for args,code in (((),0),(('--execute',),2)):
            p=subprocess.run((str(self.inert),*args),capture_output=True,timeout=2,env=self.env);self.assertEqual(p.returncode,code)
        p=subprocess.run((str(self.binary),'--missing-gates-fixture'),capture_output=True,timeout=2,env=self.env);self.assertEqual(p.returncode,0)
if __name__=='__main__':unittest.main()
