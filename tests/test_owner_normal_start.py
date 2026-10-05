"""Actual App transaction/restore/allocation/factory source; Android and auth synthetic."""
from pathlib import Path
import subprocess
import unittest
import test_owner_App_rendezvous as rdv
ROOT=rdv.ROOT
class NormalStartChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rdv.RendezvousChecks.setUpClass();cls.base=Path(rdv.RendezvousChecks.temp.name);cls.java=rdv.RendezvousChecks.java;cls.env=rdv.RendezvousChecks.env
        source=(ROOT/'app/src/udp/java/local/remoteandroid/direct/AuthenticatedLanUdpUi.java').read_text()
        ui=(cls.base/'AuthenticatedLanUdpUi.java').read_text().replace('import android.os.Looper;','import android.os.Looper;import java.net.Socket;import java.io.IOException;')
        start=source.index('    /** Same-App future cooperative caller only.');end=source.index('    /** Explicit same-App two-phase observation.',start)
        ui=ui.replace('    /** Explicit same-App two-phase observation.',source[start:end]+'    /** Explicit same-App two-phase observation.',1)
        begin=ui.index('    static final class Attempt {');finish=ui.index('    AuthenticatedLanUdpUi(InputQueue queue)',begin)
        actual=source[source.index('    private static final class Attempt {'):source.index('    public AuthenticatedLanUdpUi(MainActivity activity)')].replace('private static final class Attempt','static final class Attempt',1)
        ui=ui[:begin]+actual+ui[finish:];ui=ui.replace('current=new Attempt();','current=null;')
        restore=source[source.index('    private void restorePassword(){'):source.index('    private void start(){',source.index('    private void restorePassword(){'))]
        create=source[source.index('        Attempt attempt;\n        synchronized(lock){if(current'):source.index('        password.setText("");LinearLayout wait=')]
        auth=source[source.index('            synchronized(lock){\n                if(attempt.cancelled'):source.index('        }catch(Exception failure){\n            String failureLabel=')]
        extra=(ROOT/'tests/fixtures/OwnerNormalStartShell.java.txt').read_text().replace('/* RESTORE */',restore).replace('/* CREATE */',create).replace('/* AUTH */',auth)
        ui=ui[:-2]+extra+'\n}\n';(cls.base/'AuthenticatedLanUdpUi.java').write_text(ui)
        for name in ['Button','EditText','Spinner','CheckBox','PasswordStore','Endpoint','LanUdpContract','Text']:
            text=next(line for line in (ROOT/'tests/fixtures/OwnerNormalStartStubs.java.txt').read_text().splitlines() if line.startswith('final class '+name+' '))
            (cls.base/(name+'.java')).write_text('package local.remoteandroid.direct;'+text+'\n')
        probe=cls.base/'UdpVideoProbe.java';probe.write_text(probe.read_text().replace('void complete();','void complete(Object report,boolean failed,Object completion);'))
        paths=[probe,cls.base/'AuthenticatedLanUdpUi.java',ROOT/'tests/fixtures/OwnerNormalStartFixture.java']+[cls.base/(name+'.java') for name in ['Button','EditText','Spinner','CheckBox','PasswordStore','Endpoint','LanUdpContract','Text']]
        javac=str(Path(cls.java).with_name('javac'))
        p=subprocess.run([javac,'-Xlint:all','-Werror','-cp',str(cls.base),'-d',str(cls.base),*map(str,paths)],env=cls.env,capture_output=True,text=True,timeout=30)
        if p.returncode:rdv.RendezvousChecks.tearDownClass();raise AssertionError(p.stdout+p.stderr)
    @classmethod
    def tearDownClass(cls):rdv.RendezvousChecks.tearDownClass()
    def run_case(self,mode):
        p=subprocess.run([self.java,'-cp',str(self.base),'local.remoteandroid.direct.OwnerNormalStartFixture',mode],env=self.env,capture_output=True,text=True,timeout=10)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr);self.assertIn('PASS',p.stdout)
    def test_default_off_original_creation_hook(self):
        source=(ROOT/'app/src/udp/java/local/remoteandroid/direct/AuthenticatedLanUdpUi.java').read_text()
        self.assertEqual(source.count('ownerNormalStartRendezvous('),1)
        self.assertIn('ownerStartTransaction;if(observation!=null)observation.created(attempt);',source)
        adapter=source[source.index('    /** Same-App future cooperative caller only.'):source.index('    /** Explicit same-App two-phase observation.')]
        self.assertNotIn('passwordStore.load',adapter);self.assertIn('ui.restorePassword();',adapter)
for mode in ['normal','ordinary','expired','oversize','lock_wait','nonmain','repeat','active','retiring','destroyed','scope','route','account','detached','disabled','restore_empty','restore_failure','restore_reentrant','restore_replaced_secret','button_reentrant','click_refused','click_no_attempt','click_throws','click_deadline','button_read_timeout','later_page','later_attempt','factory_blocks','after_unknown']:
    setattr(NormalStartChecks,'test_'+mode,lambda self,mode=mode:self.run_case(mode))
if __name__=='__main__':unittest.main()
