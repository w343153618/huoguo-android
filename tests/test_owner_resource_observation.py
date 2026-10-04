"""Actual allocation and original normal stop paths, offline API substitutes.
These observations never establish Android release, Attempt or permission.
"""
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest
from test_udp_audio_retirement import STUBS

ROOT=Path(__file__).resolve().parents[1]
JAVA=Path('/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home/bin')

class OwnerResourceChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='huoguo-resource-calls-')
        cls.folder=Path(cls.tmp.name)
        compiler=JAVA/'javac';runner=JAVA/'java'
        if not compiler.is_file() or not runner.is_file():
            found_compiler,found_runner=shutil.which('javac'),shutil.which('java')
            if not found_compiler or not found_runner:raise RuntimeError('Existing JDK required; no installation or skip')
            compiler,runner=Path(found_compiler).resolve(),Path(found_runner).resolve()
        cls.compiler,cls.runner=str(compiler),str(runner)
        cls.env={'PATH':str(compiler.parent)+':/usr/bin:/bin','JAVA_HOME':str(compiler.parent.parent),'LANG':'C'}
        sources=[]
        for name,source in STUBS.items():
            if name.endswith('/MainActivity.java'):continue
            if name=='android/media/MediaCodec.java':
                source=source.replace('public void configure(MediaFormat f,Object surface,Object crypto,int flags){}',
                    'public static volatile boolean failConfigure,failStop,failRelease,failName; public volatile int stops;'
                    'public void configure(MediaFormat f,Object surface,Object crypto,int flags){if(failConfigure)throw new IllegalStateException("setup");}')
                source=source.replace('public void stop(){}','public void stop(){stops++;if(failStop)throw new IllegalStateException("stop");}')
                source=source.replace('public void release(){releases++;}', 'public void release(){releases++;if(failRelease)throw new AssertionError("release");}')
                source=source.replace('public String getName(){return', 'public MediaCodecInfo getCodecInfo(){return new MediaCodecInfo();} public static MediaCodec createByCodecName(String s){return createDecoderByType(s);} public String getName(){if(failName)throw new IllegalStateException("name");return')
            if name=='android/media/AudioTrack.java':
                source=source.replace('public int getState(){return 1;}', 'public static volatile boolean failState;public int getState(){if(failState)throw new IllegalStateException("unpublished_track");return 1;}')
                source=source.replace('public volatile int releases;', 'public volatile int releases,stops;')
                source=source.replace('public void stop(){}', 'public void stop(){stops++;}')
            if name=='android/media/MediaFormat.java':
                source=source.replace('public static MediaFormat createAudioFormat', 'public static MediaFormat createVideoFormat(String s,int w,int h){return new MediaFormat();} public static MediaFormat createAudioFormat')
            path=cls.folder/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(source);sources.append(path)
        extra={
        'android/content/pm/ActivityInfo.java':'package android.content.pm;public final class ActivityInfo {public static final int SCREEN_ORIENTATION_PORTRAIT=1;}',
        'android/media/MediaCodecInfo.java':'''package android.media;public final class MediaCodecInfo {public boolean isEncoder(){return false;}public boolean isAlias(){return false;}public boolean isHardwareAccelerated(){return false;}public boolean isSoftwareOnly(){return true;}public String getName(){return "fixture";}public String[] getSupportedTypes(){return new String[]{"video/avc"};}public CodecCapabilities getCapabilitiesForType(String s){return new CodecCapabilities();}public static final class CodecCapabilities {public static final String FEATURE_SecurePlayback="secure",FEATURE_TunneledPlayback="tunnel",FEATURE_LowLatency="low";public boolean isFormatSupported(MediaFormat f){return true;}public boolean isFeatureRequired(String s){return false;}public boolean isFeatureSupported(String s){return false;}}}''',
        'android/media/MediaCodecList.java':'''package android.media;public final class MediaCodecList {public static final int REGULAR_CODECS=0;public MediaCodecList(int n){}public MediaCodecInfo[] getCodecInfos(){return new MediaCodecInfo[0];}}'''}
        for name,source in extra.items():
            path=cls.folder/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(source);sources.append(path)
        main=(ROOT/'app/src/main/java/local/remoteandroid/direct/MainActivity.java').read_text()
        methods=main[main.index(' private final java.util.concurrent.atomic.AtomicReference<OwnerMediaObservation> ownerMediaObservation='):main.index(' PasswordStore passwordStore;')]
        decoder=main[main.index(' static boolean virtualCodec('):main.index(' void stats(',main.index(' static boolean virtualCodec('))]
        stop=main[main.index(' synchronized void stop(){'):].split('\n',1)[0]
        source='''package local.remoteandroid.direct;import android.media.*;import java.util.*;import java.util.concurrent.atomic.AtomicLong;import javax.net.ssl.SSLSocket;
final class MainActivity {volatile boolean running,soundEnabled=true;volatile int generation;volatile float audioGain=1f;volatile MediaCodec video,audio;volatile AudioTrack track;int width,height;boolean hardwareVideo;String videoDecoderName="";Object rotateButton,adaptive,control,auth;final List<SSLSocket> sockets=new ArrayList<>();static final int PORTRAIT=1;final InputQueue input=new InputQueue(Runnable::run);void runOnUiThread(Runnable r){r.run();}void setRequestedOrientation(int n){}final PlaybackClock playback=new PlaybackClock(80);final MediaPresentationMetrics presentationMetrics=new MediaPresentationMetrics();final AtomicLong audioOutputBytes=new AtomicLong();'''+methods+decoder+stop+'}'
        path=cls.folder/'local/remoteandroid/direct/MainActivity.java';path.write_text(source);sources.append(path)
        probe=(ROOT/'experiments/nps-transport/phone/UdpVideoProbe.java').read_text();policy=probe[probe.index('    interface AudioCleanup'):probe.index('    // App mode is memory-only;')]
        path=cls.folder/'local/remoteandroid/direct/UdpVideoProbe.java';path.write_text('package local.remoteandroid.direct;import org.json.JSONObject;final class UdpVideoProbe {'+policy+'}');sources.append(path)
        for name in ['app/src/main/java/local/remoteandroid/direct/OwnerResourceObservation.java',
            'app/src/main/java/local/remoteandroid/direct/OwnerMediaObservation.java','app/src/main/java/local/remoteandroid/direct/InputQueue.java',
            'experiments/nps-transport/phone/UdpAudioReceiver.java','experiments/nps-transport/phone/UdpAudioAssembler.java',
            'experiments/nps-transport/phone/BoundedPcmQueue.java','app/src/main/java/local/remoteandroid/direct/PlaybackClock.java',
            'app/src/main/java/local/remoteandroid/direct/AudioSubmissionClock.java','app/src/main/java/local/remoteandroid/direct/PcmGain.java',
            'tests/fixtures/OwnerResourceFixture.java']:
            sources.append(ROOT/name)
        result=subprocess.run([cls.compiler,'-Xlint:all','-Werror','-d',cls.tmp.name,*map(str,sources)],capture_output=True,text=True,timeout=30,env=cls.env)
        if result.returncode:cls.tmp.cleanup();raise AssertionError(result.stdout+result.stderr)
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def case(self,mode):
        result=subprocess.run([self.runner,'-cp',self.tmp.name,'local.remoteandroid.direct.OwnerResourceFixture',mode],capture_output=True,text=True,timeout=6,env=self.env)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertIn('PASS',result.stdout)

for mode in ['main_normal','main_stop_failure','main_release_error','main_unpublished','main_inert','main_foreign',
    'audio_setup_codec','audio_setup_track','audio_stop_failure','audio_release_error','normal_calls','pending_call',
    'foreign_source','foreign_finish','duplicate_allocation','duplicate_finish','overlap','resource_bound','call_bound',
    'expiry','contention','bind_repeated','bind_existing','bind_monitor','audio_bounded_setup_track']:
    setattr(OwnerResourceChecks,'test_'+mode,lambda self,mode=mode:self.case(mode))

if __name__=='__main__':unittest.main()
