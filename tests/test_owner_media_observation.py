"""Actual Java owner ledger/threads; resource and Android objects are synthetic.
No phone, codec, native, package, input lease or gateway release qualification.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
JDK=Path('/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home/bin')
MEDIA=ROOT/'app/src/main/java/local/remoteandroid/direct/OwnerMediaObservation.java'
PROBE=ROOT/'experiments/nps-transport/phone/UdpVideoProbe.java'
AUDIO=ROOT/'experiments/nps-transport/phone/UdpAudioReceiver.java'

class OwnerMediaChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='huoguo-owner-media-')
        javac=str(JDK/'javac') if (JDK/'javac').is_file() else shutil.which('javac')
        cls.java=str(JDK/'java') if (JDK/'java').is_file() else shutil.which('java')
        if not javac or not cls.java: raise RuntimeError('Existing JDK required')
        cls.env={'PATH':str(Path(javac).parent)+':/usr/bin:/bin','LANG':'C','JAVA_HOME':str(Path(javac).parent.parent)}
        result=subprocess.run([javac,'-Xlint:all','-Werror','-d',cls.temp.name,str(MEDIA),str(ROOT/'app/src/main/java/local/remoteandroid/direct/OwnerResourceObservation.java'),
            str(ROOT/'tests/fixtures/OwnerMediaFixture.java')],capture_output=True,text=True,timeout=30,env=cls.env)
        if result.returncode: cls.temp.cleanup();raise AssertionError(result.stdout+result.stderr)
        probe=PROBE.read_text()
        preparation=probe[probe.index('    private final java.util.concurrent.atomic.AtomicBoolean ownerStarted='):probe.index('    public interface AppListener')]
        wrapper=probe[probe.index('    public void onStart(){'):probe.index('    private void runOwnerStart(){')]
        skeleton=(ROOT/'tests/fixtures/OwnerProbeWrapperFixture.java.txt').read_text()
        fixture=Path(cls.temp.name)/'OwnerProbeWrapperFixture.java'
        fixture.write_text(skeleton.replace('/* ACTUAL_PREPARATION */',preparation).replace('/* ACTUAL_WRAPPER */',wrapper))
        result=subprocess.run([javac,'-Xlint:all','-Werror','-cp',cls.temp.name,'-d',cls.temp.name,str(fixture)],
            capture_output=True,text=True,timeout=30,env=cls.env)
        if result.returncode:cls.temp.cleanup();raise AssertionError(result.stdout+result.stderr)
        from test_udp_audio_retirement import STUBS
        cls.audio_temp=Path(cls.temp.name)/'audio';cls.audio_temp.mkdir()
        paths=[]
        for relative,content in STUBS.items():
            path=cls.audio_temp/relative;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(content);paths.append(str(path))
        probe=PROBE.read_text()
        policy=probe[probe.index('    interface AudioCleanup'):probe.index('    // App mode is memory-only;')]
        stub=cls.audio_temp/'local/remoteandroid/direct/UdpVideoProbe.java'
        stub.write_text('package local.remoteandroid.direct;import org.json.JSONObject;final class UdpVideoProbe {'+policy+'}')
        paths.append(str(stub))
        for relative in ['experiments/nps-transport/phone/UdpAudioReceiver.java',
            'experiments/nps-transport/phone/UdpAudioAssembler.java','experiments/nps-transport/phone/BoundedPcmQueue.java',
            'app/src/main/java/local/remoteandroid/direct/PlaybackClock.java',
            'app/src/main/java/local/remoteandroid/direct/AudioSubmissionClock.java',
            'app/src/main/java/local/remoteandroid/direct/PcmGain.java','tests/fixtures/ActualOwnerAudioFixture.java']:
            paths.append(str(ROOT/relative))
        result=subprocess.run([javac,'-Xlint:all','-Werror','-cp',cls.temp.name,'-d',str(cls.audio_temp),*paths],
            capture_output=True,text=True,timeout=30,env=cls.env)
        if result.returncode:cls.temp.cleanup();raise AssertionError(result.stdout+result.stderr)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def run_case(self,mode):
        result=subprocess.run([self.java,'-cp',self.temp.name,'local.remoteandroid.direct.OwnerMediaFixture',mode],
            capture_output=True,text=True,timeout=6,env=self.env)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('PASS',result.stdout)
    def actual_wrapper(self,mode):
        result=subprocess.run([self.java,'-cp',self.temp.name,'local.remoteandroid.direct.OwnerProbeWrapperFixture',mode],
            capture_output=True,text=True,timeout=6,env=self.env)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('PASS actual source wrapper',result.stdout)
    def actual_audio(self,bounded,mode):
        result=subprocess.run([self.java,'-cp',str(self.audio_temp)+':'+self.temp.name,'local.remoteandroid.direct.ActualOwnerAudioFixture',bounded,mode],
            capture_output=True,text=True,timeout=8,env=self.env)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('PASS actual receiver',result.stdout)
    def test_actual_owner_path_hooks_and_no_preparation_callsite(self):
        probe=PROBE.read_text();audio=AUDIO.read_text()
        self.assertEqual(probe.count('ownerPrepareResources('),1)
        self.assertIn('Thread thread=new Thread(runner::onStart,"authenticated-lan-udp");',probe)
        self.assertLess(probe.index('preparation.prepare(runner,thread)'),probe.index('thread.start();return runner;'))
        self.assertLess(probe.index('observation.published('),probe.index('            receive(activity,'))
        self.assertLess(probe.index('observation.listener(this,false)'),probe.index('            appListener.complete('))
        self.assertLess(probe.index('            appListener.complete('),probe.index('observation.listener(this,true)'))
        self.assertIn('this(activity,generation,boundedPcmQueueEnabled,null);',audio)
        self.assertLess(audio.index('ownerObservation.audioCreated('),audio.index('worker.start();'))
        self.assertLess(audio.index('ownerObservation.audioEpoch(this,owned,track,handoff,'),audio.index('pcmWorker.start();'))
        self.assertIn('ownerObservation.audioClose(this,false);',audio)
        self.assertIn('ownerObservation.audioClose(this,true);',audio)

for _mode in ['normal','listener_pending','run_return_pending','audio_epoch','audio_live','foreign_epoch','epoch_after_close',
    'bad_budget','expired','foreign_owner','foreign_reader','second_start','second_publish','missing_publish',
    'wrong_order','bad_audio_claim','duplicate_close','wrong_handle','listener_throw','no_listener','incomplete_close','lock_contention']:
    setattr(OwnerMediaChecks,'test_'+_mode,lambda self,mode=_mode:self.run_case(mode))

for _bounded in ['legacy','bounded']:
    for _mode in ['normal','release_failure']:
        setattr(OwnerMediaChecks,'test_actual_audio_'+_bounded+'_'+_mode,
            lambda self,bounded=_bounded,mode=_mode:self.actual_audio(bounded,mode))

for _mode in ['inert','normal','double','pending','throw','late']:
    setattr(OwnerMediaChecks,'test_actual_wrapper_'+_mode,lambda self,mode=_mode:self.actual_wrapper(mode))

if __name__=='__main__':unittest.main()
