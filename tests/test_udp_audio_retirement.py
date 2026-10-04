"""Compile and exercise actual Receiver Java with narrow offline Android API stubs.
No APK, signing, service, device or claim about native Android codec behavior.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

STUBS = {
    'android/media/AudioTimestamp.java': '''package android.media;
public final class AudioTimestamp { public long framePosition,nanoTime; }''',
    'android/media/AudioFormat.java': '''package android.media;
public final class AudioFormat { public static final int ENCODING_PCM_16BIT=2,CHANNEL_OUT_STEREO=12;
 public static final class Builder { public Builder setSampleRate(int n){return this;}
 public Builder setChannelMask(int n){return this;} public Builder setEncoding(int n){return this;}
 public AudioFormat build(){return new AudioFormat();} } }''',
    'android/media/AudioAttributes.java': '''package android.media;
public final class AudioAttributes { public static final int USAGE_MEDIA=1,CONTENT_TYPE_MOVIE=3;
 public static final class Builder { public Builder setUsage(int n){return this;}
 public Builder setContentType(int n){return this;} public AudioAttributes build(){return new AudioAttributes();} } }''',
    'android/media/MediaFormat.java': '''package android.media; import java.nio.ByteBuffer;
public final class MediaFormat { public static final String KEY_PCM_ENCODING="pcm-encoding";
 public static MediaFormat createAudioFormat(String s,int rate,int channels){return new MediaFormat();}
 public void setInteger(String key,int value){} public void setByteBuffer(String key,ByteBuffer value){} }''',
    'android/media/AudioTrack.java': '''package android.media; import java.nio.ByteBuffer;
public class AudioTrack { public static final int STATE_INITIALIZED=1,MODE_STREAM=1,PERFORMANCE_MODE_LOW_LATENCY=1,
 PLAYSTATE_PLAYING=3,WRITE_NON_BLOCKING=1; public volatile int releases;
 public static int getMinBufferSize(int r,int c,int e){return 4096;}
 public int getState(){return 1;} public void setVolume(float value){} public void play(){}
 public int getPlayState(){return 3;} public boolean getTimestamp(AudioTimestamp s){return false;}
 public int getPlaybackHeadPosition(){return 0;} public void pause(){} public void flush(){} public void stop(){}
 public void release(){releases++;} public int write(ByteBuffer b,int n,int mode){b.position(b.position()+n);return n;}
 public static final class Builder { public Builder setAudioFormat(AudioFormat f){return this;}
 public Builder setAudioAttributes(AudioAttributes a){return this;} public Builder setTransferMode(int m){return this;}
 public Builder setBufferSizeInBytes(int b){return this;} public Builder setPerformanceMode(int m){return this;}
 public AudioTrack build(){return new AudioTrack();} } }''',
    'android/media/MediaCodec.java': '''package android.media; import java.nio.ByteBuffer;
import java.util.concurrent.CountDownLatch;
public class MediaCodec { public volatile boolean blockInput; public volatile int releases,queuedAfterRelease;
 public final CountDownLatch inputEntered=new CountDownLatch(1),inputPermit=new CountDownLatch(1),inputInterrupted=new CountDownLatch(1);
 public volatile Runnable beforeInputReturn; public static volatile MediaCodec nextCreated;
 public static MediaCodec createDecoderByType(String s){MediaCodec c=nextCreated;nextCreated=null;return c==null?new MediaCodec():c;}
 public void configure(MediaFormat f,Object surface,Object crypto,int flags){} public void start(){} public void stop(){}
 public void release(){releases++;} public String getName(){return "offline-api-substitute";}
 public int dequeueInputBuffer(long timeout){inputEntered.countDown();if(blockInput){while(true){
  try{inputPermit.await();break;}catch(InterruptedException ignored){inputInterrupted.countDown();}}}
  Runnable callback=beforeInputReturn;if(callback!=null)callback.run();return 0;}
 public ByteBuffer getInputBuffer(int index){return ByteBuffer.allocate(4096);}
 public void queueInputBuffer(int index,int offset,int size,long pts,int flags){if(releases>0)queuedAfterRelease++;}
 public int dequeueOutputBuffer(BufferInfo info,long timeout){try{Thread.sleep(1);}catch(InterruptedException ignored){}return -1;}
 public ByteBuffer getOutputBuffer(int index){return ByteBuffer.allocate(4096);}
 public void releaseOutputBuffer(int index,boolean render){}
 public static final class BufferInfo { public int offset,size;public long presentationTimeUs; } }''',
    'org/json/JSONArray.java': '''package org.json; public final class JSONArray {
 public JSONArray put(Object value){return this;} }''',
    'org/json/JSONObject.java': '''package org.json; public final class JSONObject {
 public static final Object NULL=new Object();public JSONObject put(String key,Object value){return this;} }''',
    'local/remoteandroid/direct/MainActivity.java': '''package local.remoteandroid.direct;
import android.media.MediaCodec;import android.media.AudioTrack;import java.util.concurrent.atomic.AtomicLong;
final class MainActivity { volatile boolean running,soundEnabled=true;volatile int generation;volatile float audioGain=1f;
 volatile MediaCodec audio;volatile AudioTrack track;final PlaybackClock playback=new PlaybackClock(80);
 final MediaPresentationMetrics presentationMetrics=new MediaPresentationMetrics();final AtomicLong audioOutputBytes=new AtomicLong(); }''',
    'local/remoteandroid/direct/MediaPresentationMetrics.java': '''package local.remoteandroid.direct;
final class MediaPresentationMetrics { int resetAudio(){return 1;}
 void audioTimestamp(int epoch,boolean valid,long frame,long time,long now,boolean playing){}
 AudioEstimate audioEstimate(long now){return new AudioEstimate();}
 void audioWritten(int epoch,long written,long frames,long pts){}
 static final class AudioEstimate { boolean valid;long timestampFramePosition; } }''',
}


class UdpAudioRetirementCheck(unittest.TestCase):
    def test_input_worker_quiescence_resource_retention_and_join_lock_order(self):
        java_home = Path(os.environ.get('JAVA_HOME', '/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home'))
        javac, java = java_home/'bin/javac', java_home/'bin/java'
        if not javac.is_file() or not java.is_file():
            compiler, runner = shutil.which('javac'), shutil.which('java')
            if not compiler or not runner:
                self.skipTest('Existing JDK required; this check installs no toolchain')
            javac, java = Path(compiler), Path(runner)
        with tempfile.TemporaryDirectory(prefix='huoguo-audio-retire-offline-') as temporary:
            folder = Path(temporary)
            sources = []
            for relative, source in STUBS.items():
                path = folder/relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source)
                sources.append(path)
            # Receiver now reports a typed completion independently of JSON.
            # Compile its actual pure receipt/close policy, not a second model.
            probe = (ROOT/'experiments/nps-transport/phone/UdpVideoProbe.java').read_text()
            receipt = '    interface AudioCleanup'+probe.split('    interface AudioCleanup', 1)[1].split('    // App mode is memory-only;', 1)[0]
            policy = folder/'local/remoteandroid/direct/UdpVideoProbe.java'
            policy.write_text('package local.remoteandroid.direct;import org.json.JSONObject;'
                              'final class UdpVideoProbe {\n'+receipt+'}\n')
            sources.append(policy)
            sources.extend(ROOT/relative for relative in (
                'experiments/nps-transport/phone/UdpAudioReceiver.java',
                'app/src/main/java/local/remoteandroid/direct/OwnerMediaObservation.java',
                'experiments/nps-transport/phone/UdpAudioAssembler.java',
                'experiments/nps-transport/phone/BoundedPcmQueue.java',
                'app/src/main/java/local/remoteandroid/direct/PlaybackClock.java',
                'app/src/main/java/local/remoteandroid/direct/AudioSubmissionClock.java',
                'app/src/main/java/local/remoteandroid/direct/PcmGain.java',
                'tests/java/local/remoteandroid/direct/UdpAudioRetirementCheck.java'))
            compile_result = subprocess.run([str(javac), '-d', temporary, *map(str, sources)],
                capture_output=True, text=True, timeout=20)
            self.assertEqual(compile_result.returncode, 0, compile_result.stdout+compile_result.stderr)
            result = subprocess.run([str(java), '-cp', temporary, 'local.remoteandroid.direct.UdpAudioRetirementCheck'],
                capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertIn('actual Receiver retirement checks (offline API substitutes; not Android codec or acoustic validation)', result.stdout)


if __name__ == '__main__':
    unittest.main()
