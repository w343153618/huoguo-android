package local.remoteandroid.direct;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Intent;
import android.media.AudioTimestamp;
import android.media.MediaCodec;
import android.os.Bundle;
import android.os.Handler;
import android.os.SystemClock;
import android.util.Base64;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/** Measures the installed client's actual decoder and Surface, with no saved-setting changes. */
public final class PhoneProbe extends Instrumentation {
    private Bundle args;
    public void onCreate(Bundle args) { super.onCreate(args); this.args=args; start(); }
    public void onStart() {
        Bundle result=new Bundle(); MainActivity a=null;
        int oldFps=0,oldSize=0,oldRate=0,oldBuffer=0;String oldHost=null,oldMode=null;
        try {
            a=(MainActivity)startActivitySync(new Intent().setClassName(
                "local.remoteandroid.direct","local.remoteandroid.direct.MainActivity")
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            waitForIdleSync();oldFps=a.maxFps;oldSize=a.maxSize;oldRate=a.bitRate;
            oldBuffer=a.bufferMs;oldHost=a.host;oldMode=a.bitrateMode;measure(a,result);
        } catch(Throwable e) {
            result.putString("failure",e.getClass().getName()+": "+e.getMessage());
        } finally {
            if(a!=null) {
                a.maxFps=oldFps;a.maxSize=oldSize;a.bitRate=oldRate;a.bufferMs=oldBuffer;
                a.host=oldHost;a.bitrateMode=oldMode;
                MainActivity current=a;runOnMainSync(()->{current.stop();current.login();});
            }
        }
        finish(result.containsKey("failure")?Activity.RESULT_CANCELED:Activity.RESULT_OK,result);
    }
    private long hold(PlaybackClock clock) {
        try { java.lang.reflect.Field f=PlaybackClock.class.getDeclaredField("decoderHoldNs");
            f.setAccessible(true); return f.getLong(clock); } catch(Exception e) { return -1; }
    }
    private void measure(MainActivity a,Bundle result)throws Exception {
        File secret=new File(a.getFilesDir(),"transport-test-credential");
        String credential;
        if(secret.isFile()) {
            credential=new String(Files.readAllBytes(secret.toPath()),StandardCharsets.UTF_8).trim();
            if(!secret.delete())throw new IllegalStateException("Cannot consume test credential");
        } else {
            final String[] loaded={null};
            runOnMainSync(()->{if(a.password.length()>0)loaded[0]=a.user.getText()+":"+a.password.getText();});
            if(loaded[0]==null)throw new IllegalStateException("No test credential available");
            credential=loaded[0]; loaded[0]=null;
        }
        a.host=args.getString("host"); a.maxSize=Integer.parseInt(args.getString("max_size","1200"));
        a.bitRate=Integer.parseInt(args.getString("bit_rate","4000000"));
        a.maxFps=Integer.parseInt(args.getString("fps","60"));
        a.bufferMs=Integer.parseInt(args.getString("buffer_ms","80"));
        if(a.bufferMs<30||a.bufferMs>80)throw new IllegalArgumentException("Buffer outside user limit");
        a.bitrateMode=args.getString("mode","ADAPTIVE_VBR");a.initTLS();
        a.auth="Basic "+Base64.encodeToString(credential.getBytes(StandardCharsets.UTF_8),Base64.NO_WRAP);
        credential=null;
        long connectAt=SystemClock.elapsedRealtime(); String id=a.session();
        runOnMainSync(()->a.show(id));
        for(int i=0;i<400&&a.video==null&&a.running;i++)Thread.sleep(50);
        if(a.video==null||!a.running)throw new IllegalStateException("Video did not start");
        MediaCodec decoder=a.video; List<Long> renderTimes=Collections.synchronizedList(new ArrayList<>());
        List<Long> renderPts=Collections.synchronizedList(new ArrayList<>());
        List<Long> renderLateness=Collections.synchronizedList(new ArrayList<>());
        decoder.setOnFrameRenderedListener((codec,pts,ns)->{
            if(a.running&&a.video==codec) {
                a.presentedFrames.incrementAndGet();renderTimes.add(ns);renderPts.add(pts);
                renderLateness.add(ns-a.playback.deadline(pts));
            }
        },new Handler(a.statsThread.getLooper()));
        JSONObject report=new JSONObject().put("host",a.host).put("mode",a.bitrateMode)
            .put("requested_bps",a.bitRate).put("decoder",decoder.getName()).put("hardware",a.hardwareVideo)
            .put("size",a.width+"x"+a.height).put("fps_limit",a.maxFps).put("buffer_ms",a.bufferMs)
            .put("startup_ms",SystemClock.elapsedRealtime()-connectAt);
        report.put("sample_start_unix_ms",System.currentTimeMillis());
        JSONArray samples=new JSONArray(); long time=SystemClock.elapsedRealtime(),rx=a.receivedFrames.get();
        long shown=a.presentedFrames.get(),late=a.lateDiscardedFrames.get(),bytes=a.receivedVideoBytes.get();
        int seconds=Integer.parseInt(args.getString("seconds","45"));
        for(int i=0;i<seconds;i++) {
            Thread.sleep(1000);long now=SystemClock.elapsedRealtime(),nextRx=a.receivedFrames.get();
            long nextShown=a.presentedFrames.get(),nextLate=a.lateDiscardedFrames.get(),nextBytes=a.receivedVideoBytes.get();
            JSONObject sample=new JSONObject().put("second",i+1).put("rx_fps",(nextRx-rx)*1000.0/(now-time))
                .put("render_fps",(nextShown-shown)*1000.0/(now-time)).put("late_fps",(nextLate-late)*1000.0/(now-time))
                .put("video_mbps",(nextBytes-bytes)*8.0/(now-time)/1000.0).put("rtt_ms",a.networkRttMs)
                .put("target_bps",a.acceptedBitrate).put("decoder_hold_ms",hold(a.playback)/1e6).put("running",a.running);
            if(a.track!=null) {
                AudioTimestamp stamp=new AudioTimestamp(); boolean valid=a.track.getTimestamp(stamp);
                sample.put("audio_timestamp_valid",valid).put("audio_written_frames",a.audioOutputBytes.get()/4);
                if(valid)sample.put("audio_playback_frames",stamp.framePosition).put("audio_playback_time_ns",stamp.nanoTime)
                    .put("audio_queued_ms",Math.max(0,a.audioOutputBytes.get()/4-stamp.framePosition)/48.0);
            }
            samples.put(sample);time=now;rx=nextRx;shown=nextShown;late=nextLate;bytes=nextBytes;
            if(!a.running)break;
        }
        JSONArray gaps=new JSONArray(),ptsGaps=new JSONArray(),lateness=new JSONArray();
        synchronized(renderTimes) { for(int i=1;i<renderTimes.size();i++)gaps.put((renderTimes.get(i)-renderTimes.get(i-1))/1e6); }
        synchronized(renderPts) { for(int i=1;i<renderPts.size();i++)ptsGaps.put((renderPts.get(i)-renderPts.get(i-1))/1e3); }
        synchronized(renderLateness) { for(long value:renderLateness)lateness.put(value/1e6); }
        report.put("samples",samples).put("render_gaps_ms",gaps).put("render_pts_gaps_ms",ptsGaps)
            .put("render_vs_current_deadline_ms",lateness).put("audio_pcm_bytes",a.audioOutputBytes.get())
            .put("late_total",a.lateDiscardedFrames.get()).put("running_at_end",a.running)
            .put("adaptive_rejected",a.adaptiveRejected)
            .put("av_limitation","AudioTrack position observed; PCM source-PTS mapping absent, so actual A/V skew is not measured");
        result.putString("report",report.toString());
    }
}
