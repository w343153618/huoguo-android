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
import android.util.AtomicFile;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/** Measures the installed client's actual decoder and Surface. The optional
 * button path exercises normal login, which remembers connection choices, but
 * never clicks the password-save button. The direct path keeps saved settings. */
public final class PhoneProbe extends Instrumentation {
    private static final String REPORT_FILE="transport-test-report.json";
    private Bundle args;
    public void onCreate(Bundle args) { super.onCreate(args); this.args=args; start(); }
    public void onStart() {
        Bundle result=new Bundle(); MainActivity a=null;
        int oldFps=0,oldSize=0,oldRate=0,oldBuffer=0,oldSync=0,oldAudioBuffer=0;
        boolean oldImmediate=false;String oldHost=null,oldMode=null;
        try {
            a=(MainActivity)startActivitySync(new Intent().setClassName(
                getTargetContext().getPackageName(),"local.remoteandroid.direct.MainActivity")
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            waitForIdleSync();oldFps=a.maxFps;oldSize=a.maxSize;oldRate=a.bitRate;
            oldBuffer=a.bufferMs;oldSync=a.avSyncOffsetMs;oldHost=a.host;oldMode=a.bitrateMode;
            oldAudioBuffer=a.probeAudioBufferFrames;oldImmediate=a.probeImmediateVideoRelease;measure(a,result);
        } catch(Throwable e) {
            result.putString("failure",e.getClass().getName()+": "+e.getMessage());
        } finally {
            if(a!=null) {
                a.maxFps=oldFps;a.maxSize=oldSize;a.bitRate=oldRate;a.bufferMs=oldBuffer;
                a.host=oldHost;a.bitrateMode=oldMode;a.avSyncOffsetMs=oldSync;
                a.probeAudioBufferFrames=oldAudioBuffer;a.probeImmediateVideoRelease=oldImmediate;
                MainActivity current=a;runOnMainSync(()->{current.stop();current.login();});
            }
        }
        finish(result.containsKey("failure")?Activity.RESULT_CANCELED:Activity.RESULT_OK,result);
    }
    private long hold(PlaybackClock clock) {
        try { java.lang.reflect.Field f=PlaybackClock.class.getDeclaredField("decoderHoldNs");
            f.setAccessible(true); return f.getLong(clock); } catch(Exception e) { return -1; }
    }
    private MediaPresentationMetrics presentation(MainActivity a) {
        try { java.lang.reflect.Field f=MainActivity.class.getDeclaredField("presentationMetrics");
            f.setAccessible(true);return (MediaPresentationMetrics)f.get(a);
        } catch(Exception absent) { return null; }
    }
    private Button connectionButton(View view) {
        if(view instanceof Button&&"连接虚拟安卓".contentEquals(((Button)view).getText()))
            return (Button)view;
        if(view instanceof ViewGroup) {
            ViewGroup group=(ViewGroup)view;
            for(int i=0;i<group.getChildCount();i++) {
                Button found=connectionButton(group.getChildAt(i));if(found!=null)return found;
            }
        }
        return null;
    }
    private void measure(MainActivity a,Bundle result)throws Exception {
        String loginPath=args.getString("login_path","direct");
        if(!loginPath.equals("direct")&&!loginPath.equals("button"))
            throw new IllegalArgumentException("Invalid login path");
        boolean buttonPath=loginPath.equals("button");
        File secret=new File(a.getFilesDir(),"transport-test-credential");
        String credential;
        if(secret.isFile()) {
            credential=new String(Files.readAllBytes(secret.toPath()),StandardCharsets.UTF_8).trim();
            if(!secret.delete())throw new IllegalStateException("Cannot consume test credential");
        } else {
            if(buttonPath)throw new IllegalStateException("Button login requires a private test credential");
            final String[] loaded={null};
            runOnMainSync(()->{if(a.password.length()>0)loaded[0]=a.user.getText()+":"+a.password.getText();});
            if(loaded[0]==null)throw new IllegalStateException("No test credential available");
            credential=loaded[0]; loaded[0]=null;
        }
        String requestedHost=args.getString("host");
        if(requestedHost==null||requestedHost.isEmpty())throw new IllegalArgumentException("Missing test endpoint");
        a.host=requestedHost; a.maxSize=Integer.parseInt(args.getString("max_size","960"));
        a.bitRate=Integer.parseInt(args.getString("bit_rate","4000000"));
        a.maxFps=Integer.parseInt(args.getString("fps","60"));
        a.bufferMs=Integer.parseInt(args.getString("buffer_ms","80"));
        if(a.bufferMs<30||a.bufferMs>100)throw new IllegalArgumentException("Buffer outside user limit");
        a.bitrateMode=args.getString("mode","ADAPTIVE_VBR");
        a.avSyncOffsetMs=Integer.parseInt(args.getString("av_sync_ms","0"));
        String releaseMode=args.getString("video_release","scheduled");
        if(!releaseMode.equals("scheduled")&&!releaseMode.equals("immediate"))
            throw new IllegalArgumentException("Invalid video release experiment");
        int audioFrames=Integer.parseInt(args.getString("audio_buffer_frames","0"));
        if(audioFrames!=0&&audioFrames!=2048&&audioFrames!=3072&&audioFrames!=4096)
            throw new IllegalArgumentException("Invalid audio buffer experiment");
        a.probeImmediateVideoRelease=releaseMode.equals("immediate");a.probeAudioBufferFrames=audioFrames;
        JSONObject requestedSettings=new JSONObject().put("host",requestedHost).put("max_size",a.maxSize)
            .put("bit_rate",a.bitRate).put("fps",a.maxFps).put("mode",a.bitrateMode).put("buffer_ms",a.bufferMs);
        long connectAt=SystemClock.elapsedRealtime();
        if(buttonPath) {
            int separator=credential.indexOf(':');
            if(separator<=0||separator==credential.length()-1)
                throw new IllegalArgumentException("Invalid private test credential format");
            final String username=credential.substring(0,separator),password=credential.substring(separator+1);
            credential=null;
            final Button[] clicked={null};
            runOnMainSync(()->{
                if(a.running||a.address==null||a.user==null||a.password==null)
                    throw new IllegalStateException("Login view is unavailable");
                Button button=connectionButton(a.getWindow().getDecorView());
                if(button==null||!button.isEnabled())throw new IllegalStateException("Connection button is unavailable");
                // Address/account restoration may replace the password. The
                // explicit unsaved password must be typed last, just as a user does.
                a.address.setText(requestedHost);a.user.setText(username);a.password.setText(password);
                if(!button.performClick())throw new IllegalStateException("Connection button did not accept the click");
                clicked[0]=button;
                if(button.isEnabled()&&!a.running)throw new IllegalStateException("Button login did not begin");
            });
            // Normal login initially has running=false while /session is in
            // flight. Never create a session or invoke show() on this path.
            while(SystemClock.elapsedRealtime()-connectAt<160000) {
                if(a.running&&a.video!=null&&a.receivedFrames.get()>0)break;
                final boolean[] failed={false};
                runOnMainSync(()->failed[0]=clicked[0].isEnabled()&&!a.running);
                if(failed[0])throw new IllegalStateException("Button login failed before video startup");
                Thread.sleep(50);
            }
            if(a.video==null||!a.running||a.receivedFrames.get()==0)
                throw new IllegalStateException("Button login video did not start");
        } else {
            a.initTLS();
            a.auth="Basic "+Base64.encodeToString(credential.getBytes(StandardCharsets.UTF_8),Base64.NO_WRAP);
            credential=null;
            String id=a.session();runOnMainSync(()->a.show(id));
            for(int i=0;i<400&&a.video==null&&a.running;i++)Thread.sleep(50);
            if(a.video==null||!a.running)throw new IllegalStateException("Video did not start");
        }
        MediaPresentationMetrics presentation=presentation(a);
        MediaCodec decoder=a.video; List<Long> renderTimes=Collections.synchronizedList(new ArrayList<>());
        List<Long> renderPts=Collections.synchronizedList(new ArrayList<>());
        List<Long> renderLateness=Collections.synchronizedList(new ArrayList<>());
        decoder.setOnFrameRenderedListener((codec,pts,ns)->{
            if(a.running&&a.video==codec) {
                a.presentedFrames.incrementAndGet();renderTimes.add(ns);renderPts.add(pts);
                renderLateness.add(ns-a.playback.deadline(pts));
                if(presentation!=null)presentation.rendered(pts,ns,System.nanoTime());
            }
        },new Handler(a.statsThread.getLooper()));
        // The real button may replace requested hints with its selected UI
        // choices. Publish the actual final fields, and keep hints separate.
        JSONObject report=new JSONObject().put("login_path",loginPath).put("probe_requested_settings",requestedSettings)
            .put("host",a.host).put("mode",a.bitrateMode).put("max_size",a.maxSize)
            .put("requested_bps",a.bitRate).put("decoder",decoder.getName()).put("hardware",a.hardwareVideo)
            .put("size",a.width+"x"+a.height).put("fps_limit",a.maxFps).put("buffer_ms",a.bufferMs)
            .put("video_release_mode",releaseMode).put("requested_audio_buffer_frames",audioFrames)
            .put("av_sync_ms",a.avSyncOffsetMs).put("startup_ms",SystemClock.elapsedRealtime()-connectAt);
        report.put("presentation_hooks_available",presentation!=null);
        report.put("actual_display_fps_measured",false).put("actual_audio_video_skew_measured",false);
        report.put("metric_definitions",new JSONObject()
            .put("audio_queued_ms","Legacy written minus hardware timestamp position; includes timestamp age; NOT remaining queue")
            .put("audio_queued_estimate_ms","PCM remaining at sample time, extrapolated at 1x sample rate from hardware timestamp; estimate, not acoustic latency")
            .put("render_vs_current_deadline_ms","Legacy render versus callback-time mutable playback clock; NOT original scheduling error")
            .put("render_fps","Legacy codec callback count per second, NOT independently observed display FPS")
            .put("render_gaps_ms","Legacy gaps in unverified vendor callback timestamp, NOT physical display cadence")
            .put("surface_render_vs_scheduled_ms","Raw vendor-reported timestamp minus requested target; vendor may echo target; zero does NOT prove presentation")
            .put("surface_callback_delay_ms","Java receipt minus raw vendor timestamp; negative means the vendor reports a future time")
            .put("receive_to_input_queue_ms","App complete access-unit receipt to queueInputBuffer call; includes input-buffer wait")
            .put("input_queue_to_decoder_ready_ms","queueInputBuffer call to output dequeue; includes codec and thread scheduling, not pure hardware execution")
            .put("audio_minus_video_pts_estimate_ms","Estimated PCM source PTS at video Surface render minus video PTS; positive means audio ahead; NOT lip-sync measured at speaker/display"));
        long sampleStartNs=System.nanoTime();
        report.put("sample_start_unix_ms",System.currentTimeMillis()).put("sample_start_monotonic_ns",sampleStartNs);
        JSONArray samples=new JSONArray(); long time=SystemClock.elapsedRealtime(),rx=a.receivedFrames.get();
        long shown=a.presentedFrames.get(),late=a.lateDiscardedFrames.get(),bytes=a.receivedVideoBytes.get();
        int seconds=Integer.parseInt(args.getString("seconds","45"));
        for(int i=0;i<seconds;i++) {
            Thread.sleep(1000);long now=SystemClock.elapsedRealtime(),nextRx=a.receivedFrames.get();
            long nextShown=a.presentedFrames.get(),nextLate=a.lateDiscardedFrames.get(),nextBytes=a.receivedVideoBytes.get();
            JSONObject sample=new JSONObject().put("second",i+1).put("rx_fps",(nextRx-rx)*1000.0/(now-time))
                .put("render_fps",(nextShown-shown)*1000.0/(now-time)).put("late_fps",(nextLate-late)*1000.0/(now-time))
                .put("codec_callback_fps",(nextShown-shown)*1000.0/(now-time))
                .put("video_mbps",(nextBytes-bytes)*8.0/(now-time)/1000.0).put("rtt_ms",a.networkRttMs)
                .put("target_bps",a.acceptedBitrate).put("decoder_hold_ms",hold(a.playback)/1e6).put("running",a.running);
            if(a.track!=null) {
                AudioTimestamp stamp=new AudioTimestamp(); boolean valid=a.track.getTimestamp(stamp);
                long audioNow=System.nanoTime(),written=a.audioOutputBytes.get()/4;
                sample.put("audio_timestamp_valid",valid).put("audio_written_frames",written)
                    .put("audio_underrun_count",a.track.getUnderrunCount())
                    .put("audio_buffer_frames",a.track.getBufferSizeInFrames())
                    .put("audio_buffer_capacity_frames",a.track.getBufferCapacityInFrames())
                    .put("audio_performance_mode",a.track.getPerformanceMode());
                if(valid)sample.put("audio_playback_frames",stamp.framePosition).put("audio_playback_time_ns",stamp.nanoTime)
                    .put("audio_timestamp_age_ms",(audioNow-stamp.nanoTime)/1e6)
                    .put("audio_queued_ms",Math.max(0,written-(stamp.framePosition&0xffffffffL))/48.0);
                MediaPresentationMetrics.AudioEstimate queue=MediaPresentationMetrics.estimateAudio(
                    48000,written,valid?(stamp.framePosition&0xffffffffL):MediaPresentationMetrics.MISSING,
                    valid?stamp.nanoTime:MediaPresentationMetrics.MISSING,audioNow,audioNow,
                    a.track.getPlayState()==android.media.AudioTrack.PLAYSTATE_PLAYING);
                sample.put("audio_queue_estimate_valid",queue.valid);
                if(queue.valid)sample.put("audio_queued_estimate_ms",queue.estimatedQueueMs)
                    .put("audio_playback_frames_estimate",queue.estimatedFramePosition)
                    .put("audio_has_queued_pcm_estimate",queue.dataAtPosition);
            }
            samples.put(sample);time=now;rx=nextRx;shown=nextShown;late=nextLate;bytes=nextBytes;
            if(!a.running)break;
        }
        JSONArray gaps=new JSONArray(),ptsGaps=new JSONArray(),lateness=new JSONArray();
        synchronized(renderTimes) { for(int i=1;i<renderTimes.size();i++)gaps.put((renderTimes.get(i)-renderTimes.get(i-1))/1e6); }
        synchronized(renderPts) { for(int i=1;i<renderPts.size();i++)ptsGaps.put((renderPts.get(i)-renderPts.get(i-1))/1e3); }
        synchronized(renderLateness) { for(long value:renderLateness)lateness.put(value/1e6); }
        if(presentation!=null) {
            MediaPresentationMetrics.Snapshot snapshot=presentation.snapshot();
            long sampleEndNs=System.nanoTime();report.put("sample_end_monotonic_ns",sampleEndNs);
            JSONArray targetErrors=new JSONArray(),callbacks=new JSONArray(),readyToSurface=new JSONArray();
            JSONArray receiveToReady=new JSONArray(),releaseToSurface=new JSONArray(),avSkew=new JSONArray();
            JSONArray receiveToInput=new JSONArray(),inputToReady=new JSONArray();
            JSONArray callbackGaps=new JSONArray(),readyToCallback=new JSONArray(),targetLead=new JSONArray();
            JSONArray frameRecords=new JSONArray();gaps=new JSONArray();ptsGaps=new JSONArray();
            long previousRender=MediaPresentationMetrics.MISSING,previousPts=MediaPresentationMetrics.MISSING;
            long previousCallback=MediaPresentationMetrics.MISSING;
            int invalidTimes=0,futureTimes=0,beforeReleaseTimes=0,targetEchoes=0,matchedTargets=0;
            for(MediaPresentationMetrics.VideoSample frame:snapshot.video) {
                if(frame.callbackNs<sampleStartNs||frame.callbackNs>sampleEndNs)continue;
                JSONObject row=new JSONObject().put("pts_us",frame.ptsUs).put("surface_ns",frame.actualRenderNs)
                    .put("vendor_render_ns",frame.actualRenderNs).put("callback_ns",frame.callbackNs)
                    .put("vendor_timestamp_causally_plausible",frame.vendorTimestampPlausible)
                    .put("vendor_timestamp_future",frame.vendorTimestampFuture)
                    .put("vendor_timestamp_before_release",frame.vendorTimestampBeforeRelease)
                    .put("vendor_timestamp_echoes_requested_target",frame.vendorTimestampEchoesTarget);
                if(!frame.vendorTimestampPlausible)invalidTimes++;
                if(frame.vendorTimestampFuture)futureTimes++;
                if(frame.vendorTimestampBeforeRelease)beforeReleaseTimes++;
                if(frame.vendorTimestampEchoesTarget)targetEchoes++;
                if(frame.targetNs!=MediaPresentationMetrics.MISSING)matchedTargets++;
                callbacks.put((frame.callbackNs-frame.actualRenderNs)/1e6);
                if(previousCallback!=MediaPresentationMetrics.MISSING)
                    callbackGaps.put((frame.callbackNs-previousCallback)/1e6);
                previousCallback=frame.callbackNs;
                if(previousRender!=MediaPresentationMetrics.MISSING)gaps.put((frame.actualRenderNs-previousRender)/1e6);
                if(previousPts!=MediaPresentationMetrics.MISSING)ptsGaps.put((frame.ptsUs-previousPts)/1e3);
                previousRender=frame.actualRenderNs;previousPts=frame.ptsUs;
                if(frame.targetNs!=MediaPresentationMetrics.MISSING) {
                    row.put("scheduled_ns",frame.targetNs).put("decoder_ready_ns",frame.decoderReadyNs)
                        .put("released_ns",frame.releasedNs);
                    targetErrors.put((frame.actualRenderNs-frame.targetNs)/1e6);
                    readyToSurface.put((frame.actualRenderNs-frame.decoderReadyNs)/1e6);
                    releaseToSurface.put((frame.actualRenderNs-frame.releasedNs)/1e6);
                    readyToCallback.put((frame.callbackNs-frame.decoderReadyNs)/1e6);
                    targetLead.put((frame.targetNs-frame.releasedNs)/1e6);
                    if(frame.arrivalNs!=MediaPresentationMetrics.MISSING) {
                        row.put("received_ns",frame.arrivalNs);receiveToReady.put((frame.decoderReadyNs-frame.arrivalNs)/1e6);
                    }
                    if(frame.inputQueuedNs!=MediaPresentationMetrics.MISSING) {
                        row.put("input_queued_ns",frame.inputQueuedNs);
                        inputToReady.put((frame.decoderReadyNs-frame.inputQueuedNs)/1e6);
                        if(frame.arrivalNs!=MediaPresentationMetrics.MISSING)
                            receiveToInput.put((frame.inputQueuedNs-frame.arrivalNs)/1e6);
                    }
                }
                if(frame.audioPtsUs!=MediaPresentationMetrics.MISSING) {
                    row.put("audio_pts_estimate_us",frame.audioPtsUs);avSkew.put((frame.audioPtsUs-frame.ptsUs)/1e3);
                }
                frameRecords.put(row);
            }
            report.put("surface_render_vs_scheduled_ms",targetErrors).put("surface_callback_delay_ms",callbacks)
                .put("decoder_ready_to_surface_ms",readyToSurface).put("receive_to_decoder_ready_ms",receiveToReady)
                .put("receive_to_input_queue_ms",receiveToInput).put("input_queue_to_decoder_ready_ms",inputToReady)
                .put("codec_callback_receipt_gaps_ms",callbackGaps)
                .put("decoder_ready_to_callback_receipt_ms",readyToCallback).put("requested_target_lead_ms",targetLead)
                .put("release_to_surface_ms",releaseToSurface).put("audio_minus_video_pts_estimate_ms",avSkew)
                .put("presentation_frames",frameRecords).put("presentation_records_evicted",snapshot.videoRecordsEvicted)
                .put("presentation_pending_evicted",snapshot.pendingEvicted).put("audio_segments_evicted",snapshot.audioSegmentsEvicted)
                .put("surface_unmatched_callbacks",snapshot.unmatchedRenders);
            report.put("surface_callback_records_in_window",frameRecords.length());
            boolean echoesSuspected=matchedTargets>=30&&targetEchoes*1.0/matchedTargets>=0.95;
            boolean usable=frameRecords.length()>0&&invalidTimes==0&&!echoesSuspected;
            String status=invalidTimes>0?"invalid_vendor_timestamp"
                :echoesSuspected?"unverified_requested_target_echo":"causally_plausible_vendor_timestamp_unverified_display";
            report.put("codec_timestamp_validity",new JSONObject().put("status",status)
                .put("callback_records",frameRecords.length()).put("invalid_causal_timestamps",invalidTimes)
                .put("future_timestamps",futureTimes).put("before_release_timestamps",beforeReleaseTimes)
                .put("matched_requested_targets",matchedTargets).put("exact_requested_target_echoes",targetEchoes)
                .put("requested_target_echo_suspected",echoesSuspected)
                .put("usable_for_presentation_timestamp_estimate",usable)
                .put("independent_display_presentation_measured",false));
            report.put("audio_video_pts_estimate_valid",usable&&avSkew.length()>0);
        }
        report.put("samples",samples).put("render_gaps_ms",gaps).put("render_pts_gaps_ms",ptsGaps)
            .put("render_vs_current_deadline_ms",lateness).put("audio_pcm_bytes",a.audioOutputBytes.get())
            .put("late_total",a.lateDiscardedFrames.get()).put("running_at_end",a.running)
            .put("adaptive_rejected",a.adaptiveRejected)
            .put("av_limitation",presentation==null
                ?"PCM source-PTS mapping absent; actual A/V skew is not measured"
                :"Vendor codec timestamps are checked for future times, render-before-release and requested-target echoes. Invalid series cannot establish actual presentation or A/V skew. Independent SurfaceFlinger/display evidence is required; PCM queue remains a timestamp estimate.");
        JSONArray observedAudioBuffers=new JSONArray();java.util.TreeSet<Integer> bufferSizes=new java.util.TreeSet<>();
        for(int i=0;i<samples.length();i++)if(samples.getJSONObject(i).has("audio_buffer_frames"))
            bufferSizes.add(samples.getJSONObject(i).getInt("audio_buffer_frames"));
        for(int size:bufferSizes)observedAudioBuffers.put(size);
        report.put("audio_buffer_frames_observed",observedAudioBuffers);
        if(a.track!=null)report.put("actual_audio_buffer_frames",a.track.getBufferSizeInFrames());
        // Binder's transaction limit is shared by instrumentation and system
        // bookkeeping. Preserve every frame in app-private storage; send only
        // a small fixed-name pointer through finishInstrumentation.
        byte[] reportBytes=report.toString().getBytes(StandardCharsets.UTF_8);
        AtomicFile destination=new AtomicFile(new File(a.getFilesDir(),REPORT_FILE));
        FileOutputStream output=null;
        try {
            output=destination.startWrite();output.write(reportBytes);
            destination.finishWrite(output);output=null;
        } finally {
            if(output!=null)destination.failWrite(output);
        }
        result.putString("report_file",REPORT_FILE);
        result.putLong("report_bytes",reportBytes.length);
    }
}
