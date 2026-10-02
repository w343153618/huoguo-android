package local.remoteandroid.direct;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Intent;
import android.media.MediaCodec;
import android.os.Bundle;
import android.util.AtomicFile;
import android.view.Display;
import android.view.Gravity;
import android.view.SurfaceView;
import android.view.WindowManager;
import android.widget.FrameLayout;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.DataInputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.concurrent.locks.LockSupport;

/**
 * Isolated, paced decoder component probe. No emulator, sockets, accounts or
 * authentication. Synthetic fixture results are not real-video experience.
 */
public final class CodecFileProbe extends Instrumentation {
    private static final String FIXTURE="codec-test.h264framed",REPORT="codec-test-report.json";
    private Bundle args;
    private String completed="created";
    private int queuedFrames,queuedConfig;
    private long lastQueuedPtsUs=-1,feedStartNs,feedEndNs;
    private final JSONArray feedLateness=new JSONArray(),sourceSizes=new JSONArray(),observations=new JSONArray();

    public void onCreate(Bundle args) {super.onCreate(args);this.args=args;start();}

    public void onStart() {
        Bundle result=new Bundle();JSONObject report=new JSONObject();MainActivity activity=null;
        int oldFps=0,oldBuffer=0,oldAudioBuffer=0,oldDisplayModeId=0;boolean oldImmediate=false;
        float oldRefresh=0;int testGeneration=-1,testDisplayHz=0;
        try {
            boolean experimentalClient=booleanArgument("experimental_client",false);
            boolean decoderReanchorEnabled=!booleanArgument("arrival_clock",false);
            String clientPackage=getTargetContext().getPackageName(),probePackage=getContext().getPackageName();
            report.put("client_package",clientPackage).put("probe_package",probePackage)
                .put("experimental_client",experimentalClient).put("decoder_reanchor_enabled",decoderReanchorEnabled);
            if(!clientPackage.equals(experimentalClient?"local.remoteandroid.direct.experiment":"local.remoteandroid.direct")
                    ||!probePackage.equals(experimentalClient?"local.remoteandroid.phoneprobe.experiment":"local.remoteandroid.phoneprobe"))
                throw new IOException("codec_probe_package_pair_mismatch");
            int fps=Integer.parseInt(args.getString("fps","60"));
            int buffer=Integer.parseInt(args.getString("buffer_ms","80"));
            boolean displayExplicit=args.containsKey("display_hz");
            int displayHz=Integer.parseInt(args.getString("display_hz",Integer.toString(fps)));testDisplayHz=displayHz;
            String release=args.getString("video_release","scheduled"),profile=args.getString("profile","unspecified");
            if(fps!=60&&fps!=120||buffer<30||buffer>100||displayHz!=60&&displayHz!=90&&displayHz!=120
                    ||!release.equals("scheduled")&&!release.equals("immediate")||profile.length()>160)
                throw new IOException("codec_probe_arguments_invalid");
            report.put("test_scope","synthetic_decoder_component_no_streaming_network_or_auth")
                .put("profile",profile).put("fps_limit",fps).put("buffer_ms",buffer).put("video_release_mode",release)
                .put("requested_display_hz",displayHz).put("display_hz_explicit",displayExplicit)
                .put("actual_display_fps_measured",false).put("actual_audio_video_skew_measured",false);
            activity=(MainActivity)startActivitySync(new Intent().setClassName(
                getTargetContext().getPackageName(),"local.remoteandroid.direct.MainActivity")
                .putExtra("huoguo_codec_component_probe",true).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            waitForIdleSync();completed="activity_started";
            oldFps=activity.maxFps;oldBuffer=activity.bufferMs;oldImmediate=activity.probeImmediateVideoRelease;
            oldAudioBuffer=activity.probeAudioBufferFrames;oldRefresh=activity.getWindow().getAttributes().preferredRefreshRate;
            oldDisplayModeId=activity.getWindow().getAttributes().preferredDisplayModeId;
            FramedH264Fixture fixture=readFixture(new File(activity.getFilesDir(),FIXTURE),fps);
            completed="fixture_validated";
            report.put("fixture_bytes",fixture.bytes.length).put("fixture_sha256",sha256(fixture.bytes))
                .put("source_geometry",fixture.width+"x"+fixture.height).put("source_media_frames",fixture.mediaFrames)
                .put("source_duration_ms",fixture.durationUs/1000.0)
                .put("source_pts_fps",(fixture.mediaFrames-1)*1_000_000.0/(fixture.lastPtsUs-fixture.firstPtsUs));
            MainActivity a=activity;final int[] generation={-1};
            final Throwable[] displayFailure={null};
            runOnMainSync(()->{
                a.stop();a.maxFps=fps;a.bufferMs=buffer;a.probeImmediateVideoRelease=release.equals("immediate");
                a.probeAudioBufferFrames=0;a.adaptive=null;a.diagnostics=null;
                a.playback=new PlaybackClock(buffer,0,decoderReanchorEnabled);a.presentationMetrics=new MediaPresentationMetrics(48000);
                a.receivedFrames.set(0);a.receivedVideoBytes.set(0);a.presentedFrames.set(0);a.lateDiscardedFrames.set(0);
                a.running=true;generation[0]=++a.generation;
                a.canvas=new FrameLayout(a);a.screen=new SurfaceView(a);
                a.canvas.addView(a.screen,new FrameLayout.LayoutParams(-1,-1,Gravity.CENTER));a.setContentView(a.canvas);
                try{requestDisplayMode(a,displayHz,displayExplicit,report);}
                catch(Throwable failure){displayFailure[0]=failure;}
            });
            testGeneration=generation[0];completed="surface_created";
            if(displayFailure[0]!=null)throw new IOException("codec_display_mode_unavailable",displayFailure[0]);
            recordDisplayStart(activity,displayHz,report);
            if(displayExplicit&&!report.optBoolean("display_mode_start_matches_requested",false))
                throw new IOException("codec_display_mode_not_applied");
            long configureStartNs=System.nanoTime();
            activity.configure(fixture.width,fixture.height,testGeneration);
            if(activity.video==null||!activity.running)throw new IOException("codec_start_failed");
            completed="decoder_configured";
            report.put("decoder",activity.video.getName()).put("hardware",activity.hardwareVideo)
                .put("configure_elapsed_ms",(System.nanoTime()-configureStartNs)/1e6);
            feed(activity,fixture,testGeneration);
            completed="input_complete";
            long drainDeadline=System.nanoTime()+3_000_000_000L;
            while(activity.running&&activity.generation==testGeneration&&System.nanoTime()<drainDeadline) {
                if(activity.presentedFrames.get()+activity.lateDiscardedFrames.get()>=queuedFrames) {
                    Thread.sleep(300);break;
                }
                Thread.sleep(20);
            }
            completed="bounded_drain_complete";
            report.put("running_at_end",activity.running&&activity.generation==testGeneration);
        } catch(Throwable failure) {
            // Never return payload bytes, exception logs or private-file content.
            result.putString("failure","CodecFileProbe "+failure.getClass().getSimpleName());
            try {report.put("failure_class",failure.getClass().getSimpleName());}catch(Exception ignored){}
        } finally {
            if(activity!=null) {
                try {
                    MainActivity displayActivity=activity;final JSONObject[] displayEnd={null};
                    runOnMainSync(()->displayEnd[0]=displayReadback(displayActivity));
                    report.put("display_mode_end",displayEnd[0])
                        .put("display_mode_end_matches_requested",displayMatches(displayEnd[0],testDisplayHz));
                    report.put("last_completed_stage",completed).put("last_queued_pts_us",lastQueuedPtsUs)
                        .put("queued_media_frames",queuedFrames).put("queued_config_records",queuedConfig)
                        .put("feed_start_ns",feedStartNs).put("feed_end_ns",feedEndNs)
                        .put("observation_end_ns",System.nanoTime())
                        .put("source_access_unit_bytes",sourceSizes).put("feed_lateness_ms",feedLateness)
                        .put("feed_observations",observations).put("codec_callback_count",activity.presentedFrames.get())
                        .put("late_discarded_count",activity.lateDiscardedFrames.get());
                    if(activity.presentationMetrics!=null)appendPresentation(report,activity.presentationMetrics.snapshot());
                    writeReport(new File(activity.getFilesDir(),REPORT),report,result);
                } catch(Throwable reportFailure) {result.putString("failure","CodecFileProbe report "+reportFailure.getClass().getSimpleName());}
                MainActivity a=activity;int restoreFps=oldFps,restoreBuffer=oldBuffer,restoreAudio=oldAudioBuffer;
                int restoreDisplayModeId=oldDisplayModeId;
                boolean restoreImmediate=oldImmediate;float restoreRefresh=oldRefresh;
                runOnMainSync(()->{
                    a.stop();a.maxFps=restoreFps;a.bufferMs=restoreBuffer;a.probeAudioBufferFrames=restoreAudio;
                    a.probeImmediateVideoRelease=restoreImmediate;a.getIntent().removeExtra("huoguo_codec_component_probe");
                    WindowManager.LayoutParams window=a.getWindow().getAttributes();window.preferredRefreshRate=restoreRefresh;
                    window.preferredDisplayModeId=restoreDisplayModeId;
                    a.getWindow().setAttributes(window);a.login();
                });
            }
        }
        finish(result.containsKey("failure")?Activity.RESULT_CANCELED:Activity.RESULT_OK,result);
    }

    private boolean booleanArgument(String name,boolean fallback)throws IOException{
        String value=args.getString(name,Boolean.toString(fallback));
        if(!value.equals("true")&&!value.equals("false"))throw new IOException("codec_boolean_argument_invalid");
        return value.equals("true");
    }

    /** Window-only request. Omission preserves the original fps refresh hint. */
    private static void requestDisplayMode(MainActivity activity,int requestedHz,boolean explicit,JSONObject report)throws Exception{
        WindowManager.LayoutParams window=activity.getWindow().getAttributes();
        if(!explicit){
            window.preferredRefreshRate=requestedHz;activity.getWindow().setAttributes(window);
            report.put("display_mode_selection","legacy_fps_hint");return;
        }
        Display display=activity.getWindowManager().getDefaultDisplay();
        if(display==null)throw new IOException("codec_display_unavailable");
        Display.Mode current=display.getMode(),selected=null;double distance=Double.POSITIVE_INFINITY;
        JSONArray eligible=new JSONArray();
        for(Display.Mode candidate:display.getSupportedModes()){
            if(candidate.getPhysicalWidth()!=current.getPhysicalWidth()||candidate.getPhysicalHeight()!=current.getPhysicalHeight())continue;
            eligible.put(new JSONObject().put("mode_id",candidate.getModeId()).put("refresh_hz",candidate.getRefreshRate()));
            double error=Math.abs(candidate.getRefreshRate()-requestedHz);
            if(error<=1.0&&(error<distance||error==distance&&candidate.getModeId()==current.getModeId())){
                selected=candidate;distance=error;
            }
        }
        report.put("display_mode_supported_same_resolution",eligible);
        if(selected==null)throw new IOException("codec_requested_mode_unavailable_same_resolution");
        window.preferredDisplayModeId=selected.getModeId();window.preferredRefreshRate=selected.getRefreshRate();
        activity.getWindow().setAttributes(window);
        report.put("display_mode_selection","window_mode_request")
            .put("selected_mode_id",selected.getModeId()).put("selected_mode_hz",selected.getRefreshRate())
            .put("selected_mode_width",selected.getPhysicalWidth()).put("selected_mode_height",selected.getPhysicalHeight());
    }

    private static JSONObject displayReadback(MainActivity activity){
        JSONObject value=new JSONObject();
        try{
            Display display=activity.getWindowManager().getDefaultDisplay();
            if(display==null)return value.put("available",false);
            Display.Mode mode=display.getMode();
            value.put("available",true).put("mode_id",mode.getModeId()).put("refresh_hz",mode.getRefreshRate())
                .put("width",mode.getPhysicalWidth()).put("height",mode.getPhysicalHeight()).put("readback_ns",System.nanoTime());
        }catch(Exception failure){try{value.put("available",false).put("failure_class",failure.getClass().getSimpleName());}catch(Exception ignored){}}
        return value;
    }

    private static boolean displayMatches(JSONObject mode,int requestedHz){
        return requestedHz>0&&mode!=null&&mode.optBoolean("available",false)
            &&Math.abs(mode.optDouble("refresh_hz",Double.NaN)-requestedHz)<=1.0;
    }

    private void recordDisplayStart(MainActivity activity,int requestedHz,JSONObject report)throws Exception{
        final JSONObject[] mode={null};long deadline=System.nanoTime()+500_000_000L;
        do{
            runOnMainSync(()->mode[0]=displayReadback(activity));
            if(displayMatches(mode[0],requestedHz)||System.nanoTime()>=deadline)break;
            Thread.sleep(20);
        }while(true);
        report.put("display_mode_start",mode[0]).put("display_mode_start_matches_requested",displayMatches(mode[0],requestedHz))
            .put("display_request_limitation","Window mode/readback is not SurfaceFlinger cadence or optical display measurement.");
    }

    private void feed(MainActivity a,FramedH264Fixture fixture,int generation)throws Exception {
        MediaCodec decoder=a.video;PlaybackClock clock=a.playback;MediaPresentationMetrics metrics=a.presentationMetrics;
        feedStartNs=System.nanoTime()+100_000_000L;
        long hardDeadline=feedStartNs+fixture.durationUs*1000L+8_000_000_000L;
        clock.observe(fixture.firstPtsUs,feedStartNs);
        for(FramedH264Fixture.AccessUnit unit:fixture.units) {
            long due=feedStartNs+(unit.config?0:(unit.ptsUs-fixture.firstPtsUs)*1000L);
            if(!unit.config)waitFor(due,hardDeadline,a,generation);
            if(!a.running||a.generation!=generation||a.video!=decoder)throw new IOException("codec_probe_stopped");
            long received=System.nanoTime();
            if(!unit.config) {
                clock.observe(unit.ptsUs,received);metrics.received(unit.ptsUs,received);
                a.receivedFrames.incrementAndGet();feedLateness.put((received-due)/1e6);sourceSizes.put(unit.size);
            }
            int index;
            while((index=decoder.dequeueInputBuffer(10000))<0) {
                if(System.nanoTime()>hardDeadline||!a.running||a.generation!=generation)
                    throw new IOException("codec_input_deadline");
            }
            ByteBuffer input=decoder.getInputBuffer(index);
            if(input==null||input.capacity()<unit.size)throw new IOException("codec_input_capacity");
            input.clear();input.put(fixture.bytes,unit.offset,unit.size);
            long queued=System.nanoTime();
            if(!unit.config)metrics.inputQueued(unit.ptsUs,queued);
            decoder.queueInputBuffer(index,0,unit.size,unit.ptsUs,unit.config?MediaCodec.BUFFER_FLAG_CODEC_CONFIG:0);
            if(unit.config)queuedConfig++;
            else {
                queuedFrames++;lastQueuedPtsUs=unit.ptsUs;
                observations.put(new JSONObject().put("pts_us",unit.ptsUs).put("bytes",unit.size)
                    .put("paced_due_ns",due).put("received_ns",received).put("input_queued_ns",queued));
            }
            a.receivedVideoBytes.addAndGet(unit.size);completed=unit.config?"config_queued":"media_queued";
        }
        feedEndNs=System.nanoTime();
    }

    private static void waitFor(long due,long deadline,MainActivity a,int generation)throws Exception {
        while(true) {
            long now=System.nanoTime(),remaining=due-now;if(remaining<=0)return;
            if(now>deadline||!a.running||a.generation!=generation)throw new IOException("codec_pacing_deadline");
            if(remaining>3_000_000L)Thread.sleep(Math.min(10,(remaining-1_000_000L)/1_000_000L));
            else LockSupport.parkNanos(Math.min(remaining,1_000_000L));
        }
    }

    private static FramedH264Fixture readFixture(File file,int fps)throws Exception {
        long length=file.length();
        if(!file.isFile()||length<16||length>FramedH264Fixture.MAX_BYTES)throw new IOException("fixed_fixture_missing_or_oversized");
        byte[] bytes=new byte[(int)length];
        try(DataInputStream input=new DataInputStream(new FileInputStream(file))) {
            input.readFully(bytes);if(input.read()!=-1)throw new IOException("fixed_fixture_changed");
        }
        return FramedH264Fixture.parse(bytes,fps);
    }

    private static String sha256(byte[] bytes)throws Exception {
        StringBuilder hex=new StringBuilder();for(byte value:MessageDigest.getInstance("SHA-256").digest(bytes))
            hex.append(String.format(java.util.Locale.ROOT,"%02x",value&255));return hex.toString();
    }

    private static void writeReport(File file,JSONObject report,Bundle result)throws Exception {
        byte[] bytes=report.toString().getBytes(StandardCharsets.UTF_8);AtomicFile destination=new AtomicFile(file);
        FileOutputStream output=null;
        try {output=destination.startWrite();output.write(bytes);destination.finishWrite(output);output=null;}
        finally {if(output!=null)destination.failWrite(output);}
        result.putString("report_file",REPORT);result.putLong("report_bytes",bytes.length);
    }

    static void appendPresentation(JSONObject report,MediaPresentationMetrics.Snapshot snapshot)throws Exception {
        JSONArray records=new JSONArray(),codecLatency=new JSONArray(),inputWait=new JSONArray(),targetLead=new JSONArray();
        JSONArray readyToCallback=new JSONArray();int invalid=0,future=0,before=0,echoes=0,matched=0;
        for(MediaPresentationMetrics.VideoSample frame:snapshot.video) {
            JSONObject row=new JSONObject().put("pts_us",frame.ptsUs).put("vendor_render_ns",frame.actualRenderNs)
                .put("callback_ns",frame.callbackNs).put("vendor_timestamp_causally_plausible",frame.vendorTimestampPlausible)
                .put("vendor_timestamp_future",frame.vendorTimestampFuture).put("vendor_timestamp_before_release",frame.vendorTimestampBeforeRelease)
                .put("vendor_timestamp_echoes_requested_target",frame.vendorTimestampEchoesTarget);
            if(!frame.vendorTimestampPlausible)invalid++;if(frame.vendorTimestampFuture)future++;
            if(frame.vendorTimestampBeforeRelease)before++;if(frame.vendorTimestampEchoesTarget)echoes++;
            if(frame.targetNs!=MediaPresentationMetrics.MISSING) {
                matched++;row.put("scheduled_ns",frame.targetNs).put("released_ns",frame.releasedNs)
                    .put("decoder_ready_ns",frame.decoderReadyNs);
                targetLead.put((frame.targetNs-frame.releasedNs)/1e6);
                readyToCallback.put((frame.callbackNs-frame.decoderReadyNs)/1e6);
                if(frame.inputQueuedNs!=MediaPresentationMetrics.MISSING) {
                    row.put("input_queued_ns",frame.inputQueuedNs);codecLatency.put((frame.decoderReadyNs-frame.inputQueuedNs)/1e6);
                }
                if(frame.arrivalNs!=MediaPresentationMetrics.MISSING) {
                    row.put("received_ns",frame.arrivalNs);
                    if(frame.inputQueuedNs!=MediaPresentationMetrics.MISSING)inputWait.put((frame.inputQueuedNs-frame.arrivalNs)/1e6);
                }
            }
            records.put(row);
        }
        boolean echoSuspected=matched>=30&&echoes*1.0/matched>=0.95;
        long lastCallbackPts=-1,lastCallbackNs=-1;
        if(!snapshot.video.isEmpty()){
            MediaPresentationMetrics.VideoSample last=snapshot.video.get(snapshot.video.size()-1);
            lastCallbackPts=last.ptsUs;lastCallbackNs=last.callbackNs;
        }
        report.put("presentation_frames",records).put("input_queue_to_decoder_ready_ms",codecLatency)
            .put("last_codec_callback_pts_us",lastCallbackPts).put("last_codec_callback_ns",lastCallbackNs)
            .put("receive_to_input_queue_ms",inputWait).put("requested_target_lead_ms",targetLead)
            .put("decoder_ready_to_callback_receipt_ms",readyToCallback)
            .put("presentation_records_evicted",snapshot.videoRecordsEvicted)
            .put("presentation_pending_evicted",snapshot.pendingEvicted).put("surface_unmatched_callbacks",snapshot.unmatchedRenders)
            .put("codec_timestamp_validity",new JSONObject().put("callback_records",records.length())
                .put("invalid_causal_timestamps",invalid).put("future_timestamps",future).put("before_release_timestamps",before)
                .put("matched_requested_targets",matched).put("exact_requested_target_echoes",echoes)
                .put("requested_target_echo_suspected",echoSuspected).put("independent_display_presentation_measured",false)
                .put("usable_for_presentation_timestamp_estimate",records.length()>0&&invalid==0&&!echoSuspected));
    }
}
