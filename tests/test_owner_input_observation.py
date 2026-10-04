"""Actual App/queue source JVM checks; Android state is synthetic, no ART or release."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
JDK=Path('/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home/bin')
QUEUE=ROOT/'app/src/main/java/local/remoteandroid/direct/InputQueue.java'
UI=ROOT/'app/src/udp/java/local/remoteandroid/direct/AuthenticatedLanUdpUi.java'

class OwnedInputChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='huoguo-owned-input-')
        cls.java=str(JDK/'java') if (JDK/'java').is_file() else shutil.which('java')
        javac=str(JDK/'javac') if (JDK/'javac').is_file() else shutil.which('javac')
        cls.env={'PATH':str(Path(javac).parent)+':/usr/bin:/bin','LANG':'C','JAVA_HOME':str(Path(javac).parent.parent)}
        source=UI.read_text()
        start=source.index('    /** Same-App candidate only.')
        end=source.index('    @Override public boolean active()',start)
        skeleton=(ROOT/'tests/fixtures/OwnerInputAppSkeleton.java.txt').read_text()
        generated=Path(cls.temp.name)/'AuthenticatedLanUdpUi.java'
        generated.write_text(skeleton.replace('/* ACTUAL_APP_ADAPTER */',source[start:end]))
        stubs={
            'android/os/Looper.java':'package android.os; public final class Looper { private static final Thread OWNER=Thread.currentThread(); private static final Looper MAIN=new Looper(); public static Looper myLooper(){return Thread.currentThread()==OWNER?MAIN:null;} public static Looper getMainLooper(){return MAIN;} }',
            'android/view/Surface.java':'package android.view; public final class Surface { public boolean isValid(){return true;} }',
            'android/view/SurfaceView.java':'package android.view; public final class SurfaceView { public Surface surface=new Surface(); public int reads; public SurfaceView getHolder(){return this;} public Surface getSurface(){reads++;return surface;} }',
        }
        paths=[]
        for name,content in stubs.items():
            path=Path(cls.temp.name)/name
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(content)
            paths.append(str(path))
        result=subprocess.run([javac,'-Xlint:all','-Werror','-d',cls.temp.name,str(QUEUE),
            str(ROOT/'tests/InputQueueCheck.java'),str(ROOT/'tests/fixtures/InputDrainFixture.java'),
            str(generated),str(ROOT/'tests/fixtures/OwnerInputAppFixture.java'),*paths],capture_output=True,text=True,
            timeout=30,env=cls.env)
        if result.returncode:
            cls.temp.cleanup()
            raise AssertionError(result.stdout+result.stderr)
    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()
    def run_case(self,kind,mode):
        result=subprocess.run([self.java,'-cp',self.temp.name,'local.remoteandroid.direct.'+kind,mode],
            capture_output=True,text=True,timeout=8,env=self.env)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('PASS',result.stdout)
    def test_legacy_queue(self): self.run_case('InputQueueCheck','ignored')

for _mode in ['active_capture','actual_active','actual_cleanup','idle','move','clear','foreign_attempt',
        'foreign_queue','foreign_generation','second_capture','before_seal','second_seal','second_observe',
        'after_seal','later_generation','capacity','action_failure','cleanup_failure','shutdown','rejection','marker']:
    setattr(OwnedInputChecks,'test_queue_'+_mode,lambda self,mode=_mode:self.run_case('InputDrainFixture',mode))
for _mode in ['inert','normal','pending_action','later_attempt','cleared_binding','receiver','surface','death','nonmain',
        'later_activity','missing_retirement','later_after_seal','expired','lock_wait','second_capture',
        'repeat_observe','cleanup_unknown']:
    setattr(OwnedInputChecks,'test_App_'+_mode,lambda self,mode=_mode:self.run_case('OwnerInputAppFixture',mode))

if __name__=='__main__': unittest.main()
