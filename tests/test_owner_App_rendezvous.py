"""Actual UI/factory adapter with own JVM threads; Android resources synthetic."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
JDK=Path('/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home/bin')
class RendezvousChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='huoguo-App-rendezvous-')
        base=Path(cls.temp.name)
        javac=str(JDK/'javac') if (JDK/'javac').is_file() else shutil.which('javac')
        cls.java=str(JDK/'java') if (JDK/'java').is_file() else shutil.which('java')
        cls.env={'PATH':str(Path(javac).parent)+':/usr/bin:/bin','LANG':'C','JAVA_HOME':str(Path(javac).parent.parent)}
        ui=(ROOT/'app/src/udp/java/local/remoteandroid/direct/AuthenticatedLanUdpUi.java').read_text()
        adapter=ui[ui.index('    /** Same-App candidate only.'):ui.index('    /** Same-App future cooperative caller only.')]+ui[ui.index('    /** Explicit same-App two-phase observation.'):ui.index('    @Override public boolean active()')]
        skeleton=(ROOT/'tests/fixtures/OwnerInputAppSkeleton.java.txt').read_text()
        skeleton=skeleton.replace('final class UdpVideoProbe { }','')
        skeleton=skeleton.replace('UdpVideoProbe receiver=new UdpVideoProbe();','UdpVideoProbe receiver;')
        skeleton=skeleton.replace('OwnerInputObservation ownerInputObservation;','OwnerInputObservation ownerInputObservation;OwnerRendezvous ownerRendezvous;')
        main=skeleton[skeleton.index('final class MainActivity {'):skeleton.index('public final class AuthenticatedLanUdpUi')]
        (base/'MainActivity.java').write_text('package local.remoteandroid.direct;'+main)
        skeleton=skeleton[:skeleton.index('final class MainActivity {')]+skeleton[skeleton.index('public final class AuthenticatedLanUdpUi'):]
        (base/'AuthenticatedLanUdpUi.java').write_text(skeleton.replace('/* ACTUAL_APP_ADAPTER */',adapter))
        probe=(ROOT/'experiments/nps-transport/phone/UdpVideoProbe.java').read_text()
        preparation=probe[probe.index('    private final java.util.concurrent.atomic.AtomicBoolean ownerStarted='):probe.index('    public interface AppListener')]
        factory=probe[probe.index('    public static UdpVideoProbe startApp(MainActivity activity,JSONObject descriptor,boolean boundedPcmQueueEnabled,boolean stageDiagnosticsEnabled,boolean codecStartupReadyEnabled,NpsPhysicalNetwork physicalNetwork,AppListener listener)'):probe.index('    public void cancelApp()')]
        wrapper=probe[probe.index('    public void onStart(){'):probe.index('    private void runOwnerStart(){')]
        text=(ROOT/'tests/fixtures/OwnerRendezvousProbe.java.txt').read_text()
        for name in ['JSONObject','NpsPhysicalNetwork']:
            line=next(x for x in text.splitlines() if x.startswith('final class '+name+' '))
            (base/(name+'.java')).write_text('package local.remoteandroid.direct;'+line)
            text=text.replace(line+'\n','')
        (base/'UdpVideoProbe.java').write_text(text.replace('/* PREPARATION */',preparation).replace('/* FACTORY */',factory).replace('/* WRAPPER */',wrapper))
        stubs={
            'android/os/Looper.java':'package android.os; public final class Looper { private static final Thread OWNER=Thread.currentThread(); private static final Looper MAIN=new Looper(); public static Looper myLooper(){return Thread.currentThread()==OWNER?MAIN:null;} public static Looper getMainLooper(){return MAIN;} }',
            'android/view/Surface.java':'package android.view; public final class Surface { public boolean valid=true;public boolean isValid(){return valid;} }',
            'android/view/SurfaceView.java':'package android.view; public final class SurfaceView { public Surface surface=new Surface();public int reads;public Runnable callback;public SurfaceView getHolder(){return this;}public Surface getSurface(){reads++;Runnable c=callback;callback=null;if(c!=null)c.run();return surface;} }',
        }
        paths=[]
        for name,content in stubs.items():
            p=base/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(content);paths.append(str(p))
        paths+=list(map(str,[base/'MainActivity.java',base/'JSONObject.java',base/'NpsPhysicalNetwork.java',base/'AuthenticatedLanUdpUi.java',base/'UdpVideoProbe.java',ROOT/'app/src/main/java/local/remoteandroid/direct/InputQueue.java',ROOT/'app/src/main/java/local/remoteandroid/direct/OwnerMediaObservation.java',ROOT/'app/src/main/java/local/remoteandroid/direct/OwnerResourceObservation.java',ROOT/'tests/fixtures/InputDrainFixture.java',ROOT/'tests/fixtures/OwnerRendezvousFixture.java']))
        result=subprocess.run([javac,'-Xlint:all','-Werror','-d',str(base),*paths],env=cls.env,capture_output=True,text=True,timeout=30)
        if result.returncode:cls.temp.cleanup();raise AssertionError(result.stdout+result.stderr)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def run_case(self,mode):
        p=subprocess.run([self.java,'-cp',self.temp.name,'local.remoteandroid.direct.OwnerRendezvousFixture',mode],env=self.env,capture_output=True,text=True,timeout=10)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr);self.assertIn('PASS',p.stdout)
    def test_default_off_and_factory_order(self):
        source=(ROOT/'experiments/nps-transport/phone/UdpVideoProbe.java').read_text()
        self.assertIn('physicalNetwork,listener,null);',source)
        self.assertLess(source.index('preparation.prepare(runner,thread)'),source.index('thread.start();return runner;'))
        ui=(ROOT/'app/src/udp/java/local/remoteandroid/direct/AuthenticatedLanUdpUi.java').read_text()
        self.assertEqual(ui.count('ownerPrepareRendezvous('),2)
        self.assertEqual(ui.count('ownerNormalStartRendezvous('),1)
        self.assertEqual(ui.count('void captureLive('),1)
        self.assertIn('OwnerRendezvous ownerRendezvous;',ui)
for mode in ['normal','inert','prepare_expired','prepare_budget','prepare_late','prepare_twice','prepare_nonmain','prepare_lock_wait','receiver_later_attempt','receiver_later_generation','before_start_not_new','capture_before_start','capture_dead','capture_invalid_surface','capture_later_activity','capture_later_attempt','capture_receiver','capture_cleared_binding','capture_nonmain','capture_twice','capture_terminated','capture_expired','capture_reentrant','capture_lock_wait','retire_pending','retire_pending_runner','retire_later_attempt','retire_surface','retire_media_generation','retire_cleared_input','retire_missing_cancel','retire_repeat']:
    setattr(RendezvousChecks,'test_'+mode,lambda self,mode=mode:self.run_case(mode))
if __name__=='__main__':unittest.main()
