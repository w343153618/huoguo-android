package local.remoteandroid.direct;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Intent;
import android.media.MediaCodec;
import android.os.Bundle;
import android.system.Os;
import android.system.OsConstants;
import android.system.StructStat;
import android.util.AtomicFile;
import android.view.Display;
import android.view.Gravity;
import android.view.Surface;
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
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.SocketTimeoutException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.Base64;
import java.util.ArrayDeque;

/** Isolated authenticated UDP/FEC video, optional AAC audio/native touch components. */
public final class UdpVideoProbe extends Instrumentation {
    private static final String SESSION="udp-video-session.json",REPORT="udp-video-report.json";
    private static final long PTS_MASK=(1L<<61)-1;
    private static final long KEYFRAME=1L<<61,CONFIG=1L<<62;
    private long packets,udpBytes,foreign,oversized,headerErrors,authErrors,replayErrors,authenticated;
    private long receivedMedia,bodyErrors;
    private volatile long queuedMedia,configRecords,inputTimeouts,waitingIdrDrops;
    private long firstServerNs,startNs,endNs,clientSequence,readySent,feedbackSent,lastStatsNs;
    private volatile long lastQueuedPts=-1;
    private long lastBodyPts=-1;
    private volatile UdpAudioReceiver audioReceiver;
    private volatile UdpTouchControl touchControl;
    private long unknownApplicationPackets;
    private volatile int width,height;
    private int recoveryAttempts;
    private volatile boolean waitingIdr=true;
    private boolean recoveryActive;
    private long nextFeedbackNs;
    private volatile String completed="created";
    private final JSONArray inputs=new JSONArray(),samples=new JSONArray(),feedbackTimes=new JSONArray();
    private final JSONArray inputWait=new JSONArray(),auSizes=new JSONArray();
    private final JSONArray videoQueueWait=new JSONArray(); // Video worker only; snapshot after join.
    private volatile VideoInbox videoInbox;
    private Thread videoWorker;
    private volatile Throwable videoWorkerFailure;
    private volatile boolean videoWorkerStop;
    private boolean videoWorkerJoinTimedOut;
    private long videoWorkerFrames,videoWorkerWaitSamples,videoWorkerWaitTotalNs,videoWorkerWaitMaxNs;
    private long videoWorkerExpired,videoWorkerStale,videoInputReservationsCancelled,videoCodecTimeoutRecoveryEvents;
    private long receiveIterations,receiveLoopMaxNs,receiveProcessingMaxNs,receiveSocketWaitMaxNs,receiveLoopOver80;
    private boolean diagnosticEvents;
    private final ArrayDeque<JSONObject> nativeFrameEvents=new ArrayDeque<>(); // RX only.
    private static final int MAX_NATIVE_EVENTS=8192,MAX_INBOX_EVENTS=256;
    private long nativeFrameEventsEvicted,lastNativeDrainNs;
    private long authenticatedVideoPackets,authenticatedVideoWireBytes,highestAuthenticatedFrame;
    private long networkFeedbackSequence,networkFeedbackSent,pingReplies,pingThrottled,lastPingReplyNs;
    private long networkIntervalStartNs,intervalProcessingMaxNs,intervalSocketWaitMaxNs;
    private long networkFeedbackSendMaxNs,diagnosticDrainMaxNs;
    // Probe-only control. -1 preserves MainActivity.configure's existing hint.
    private int contentHintFps=-1;
    private volatile long contentHintApplications;
    // Frozen before the independent App runner thread starts; never a server option.
    boolean stageDiagnosticsEnabled=true;
    // Owner-only explicit App caller option; descriptors cannot enable it.
    private boolean codecStartupReadyEnabled;
    private static final int[] FEC_DIAGNOSTIC_STAT_INDEX={6,9,11,12,13,17};
    private final long[] fecDiagnosticPrevious=new long[FEC_DIAGNOSTIC_STAT_INDEX.length];
    // RX-owned fixed native snapshot; only converted to JSON after receive/cleanup.
    private long[] mappingDetails;
    private int mappingDetailStatus=NativeUdpFec.MAPPING_DETAIL_NOT_READ;
    private long mappingDetailReads,mappingDetailReadFailures,mappingDetailReadTotalNs,mappingDetailReadMaxNs,mappingDetailLastReadNs;
    private boolean mappingDetailEnableAttempted;

    private void enableMappingDetails(long handle)throws NativeUdpFec.MappingDetailsException {
        mappingDetailEnableAttempted=true;
        try{NativeUdpFec.setMappingDiagnosticsChecked(handle,true);}
        catch(NativeUdpFec.MappingDetailsException failure){mappingDetailStatus=failure.status;mappingDetails=null;throw failure;}
        readMappingDetails(handle);
    }
    private void readMappingDetails(long handle)throws NativeUdpFec.MappingDetailsException {
        long start=System.nanoTime();mappingDetailReads++;
        try{
            long[] values=NativeUdpFec.mappingDetailsChecked(handle);
            if(values[1]!=1)throw new NativeUdpFec.MappingDetailsException(NativeUdpFec.MAPPING_DETAIL_INVALID,
                "native_mapping_details_enabled_readback");
            mappingDetails=values;mappingDetailStatus=NativeUdpFec.MAPPING_DETAIL_VALID;
        }catch(NativeUdpFec.MappingDetailsException failure){
            mappingDetails=null;mappingDetailStatus=failure.status;mappingDetailReadFailures++;throw failure;
        }finally{mappingDetailLastReadNs=System.nanoTime();long elapsed=mappingDetailLastReadNs-start;
            mappingDetailReadTotalNs+=elapsed;mappingDetailReadMaxNs=Math.max(mappingDetailReadMaxNs,elapsed);}
    }

    static final class InboxEvent {
        final String event,reason;final long epoch,previousEpoch,timeNs,ptsUs,receivedNs,queueBytes;final int queueFrames;
        InboxEvent(String event,String reason,long epoch,long previousEpoch,long timeNs,VideoFrame frame,int frames,long bytes){
            this.event=event;this.reason=reason;this.epoch=epoch;this.previousEpoch=previousEpoch;this.timeNs=timeNs;
            this.ptsUs=frame==null?-1:frame.ptsUs;this.receivedNs=frame==null?0:frame.receivedNs;
            this.queueFrames=frames;this.queueBytes=bytes;
        }
    }

    /** Complete logical frames only. The byte array is immutable after admission. */
    static final class VideoFrame {
        final byte[] body;final int width,height,configBytes,auBytes;
        final long ptsUs,receivedNs,epoch;final boolean idr,startupPreparation;
        VideoFrame(byte[] body,int width,int height,int configBytes,int auBytes,long ptsUs,long receivedNs,boolean idr,long epoch){
            this(body,width,height,configBytes,auBytes,ptsUs,receivedNs,idr,epoch,false);
        }
        private VideoFrame(byte[] body,int width,int height,int configBytes,int auBytes,long ptsUs,long receivedNs,boolean idr,long epoch,boolean startupPreparation){
            this.body=body;this.width=width;this.height=height;this.configBytes=configBytes;this.auBytes=auBytes;
            this.ptsUs=ptsUs;this.receivedNs=receivedNs;this.idr=idr;this.epoch=epoch;this.startupPreparation=startupPreparation;
        }
        boolean recoveryIdr(){return idr&&configBytes>0;}
        VideoFrame atEpoch(long value){return new VideoFrame(body,width,height,configBytes,auBytes,ptsUs,receivedNs,idr,value,startupPreparation);}
        VideoFrame forStartupPreparation(long value){return new VideoFrame(body,width,height,configBytes,auBytes,ptsUs,receivedNs,idr,value,true);}
    }

    /** Pure bounded FIFO / dependency state, separate from codec calls and JSON. */
    static final class VideoInbox {
        static final int MAX_FRAMES=4,MAX_BYTES=2*1024*1024;
        final ArrayDeque<VideoFrame> queue=new ArrayDeque<>();
        long epoch,bytes,admitted,overflowEvents,overflowRejected,cleared,waitingDrops,oversizeDrops,closingDrops;
        long recoveryEpochs,recoveryCompleted,lastRecoveredEpoch=-1,maxDepth,maxBytes;
        final boolean diagnosticEvents;final ArrayDeque<InboxEvent> events=new ArrayDeque<>();long eventsEvicted;
        final MediaPresentationMetrics.StageDiagnostics stages;
        final CodecStartupGate startup;
        boolean waiting=true,acceptingChain,closed;
        VideoInbox(){this(false);}
        VideoInbox(boolean diagnosticEvents){this(diagnosticEvents,null);}
        VideoInbox(boolean diagnosticEvents,MediaPresentationMetrics.StageDiagnostics stages){
            this(diagnosticEvents,stages,false,System.nanoTime());}
        VideoInbox(boolean diagnosticEvents,MediaPresentationMetrics.StageDiagnostics stages,boolean codecStartupReadyEnabled,long startedNs){
            this.diagnosticEvents=diagnosticEvents;this.stages=stages;startup=new CodecStartupGate(codecStartupReadyEnabled,startedNs);}
        private void record(String event,String reason,long previousEpoch,VideoFrame frame){
            if(!diagnosticEvents)return;
            if(events.size()>=MAX_INBOX_EVENTS){events.removeFirst();eventsEvicted++;}
            events.addLast(new InboxEvent(event,reason,epoch,previousEpoch,System.nanoTime(),frame,queue.size(),bytes));
        }
        synchronized boolean offer(VideoFrame frame){
            if(stages!=null)stages.inboxOffer(frame.receivedNs,System.nanoTime());
            try{
            if(closed){closingDrops++;return false;}
            int startupAdmission=startup.admission(frame.width,frame.height,frame.ptsUs,frame.receivedNs,
                frame.recoveryIdr(),frame.body.length,System.nanoTime());
            if(startupAdmission==CodecStartupGate.DROP)return false;
            if(startupAdmission==CodecStartupGate.PREPARE){
                queue.addLast(frame.forStartupPreparation(epoch));bytes+=frame.body.length;admitted++;
                maxDepth=Math.max(maxDepth,queue.size());maxBytes=Math.max(maxBytes,bytes);
                record("startup_prepare_admitted","metadata_only_not_media",epoch,frame);notifyAll();return true;
            }
            if(frame.body.length>MAX_BYTES){oversizeDrops++;lose(frame,"oversized_au");return false;}
            if(queue.size()>=MAX_FRAMES||bytes+frame.body.length>MAX_BYTES){
                overflowEvents++;lose(frame,queue.size()>=MAX_FRAMES?"queue_frame_overflow":"queue_byte_overflow");
                if(!frame.recoveryIdr()){overflowRejected++;return false;}
                // Overflow may have retired a startup CHAIN_PENDING epoch.
                // The replacement complete IDR must also re-enter its startup gate.
                if(startup.admission(frame.width,frame.height,frame.ptsUs,frame.receivedNs,
                        frame.recoveryIdr(),frame.body.length,System.nanoTime())!=CodecStartupGate.ADMIT){overflowRejected++;return false;}
            }
            if(!acceptingChain&&!frame.recoveryIdr()){waitingDrops++;if(stages!=null)stages.waitingIdrDrop(System.nanoTime());return false;}
            if(frame.recoveryIdr()){acceptingChain=true;record("idr_admitted","complete_config_idr",epoch,frame);} // Following P frames may wait behind this complete IDR.
            queue.addLast(frame.atEpoch(epoch));bytes+=frame.body.length;admitted++;
            maxDepth=Math.max(maxDepth,queue.size());maxBytes=Math.max(maxBytes,bytes);notifyAll();return true;
            }finally{if(stages!=null)stages.inboxDepth(System.nanoTime(),queue.size(),bytes);}
        }
        private void lose(VideoFrame frame,String reason){
            if(stages!=null){int kind=reason.equals("queue_frame_overflow")?MediaPresentationMetrics.StageDiagnostics.FRAME_OVERFLOW
                    :reason.equals("queue_byte_overflow")?MediaPresentationMetrics.StageDiagnostics.BYTE_OVERFLOW
                    :reason.equals("worker_complete_au_expired")?MediaPresentationMetrics.StageDiagnostics.WORKER_EXPIRED
                    :reason.equals("codec_input_timeout")?MediaPresentationMetrics.StageDiagnostics.TIMEOUT_CHAIN_LOSS
                    :MediaPresentationMetrics.StageDiagnostics.OTHER_CHAIN_LOSS;
                stages.inboxLoss(System.nanoTime(),frame==null?-1:frame.ptsUs,kind,queue.size(),bytes,epoch);}
            record("chain_lost",reason,epoch,frame);
            cleared+=queue.size();queue.clear();bytes=0;epoch++;recoveryEpochs++;
            waiting=true;acceptingChain=false;notifyAll();
            startup.chainLost(System.nanoTime());
        }
        synchronized void fail(long observedEpoch){if(observedEpoch==epoch)lose(null,"codec_input_failure");else record("stale_fail_ignored","old_epoch",observedEpoch,null);}
        synchronized void fail(VideoFrame frame,String reason){
            if(frame.epoch==epoch)lose(frame,reason);else record("stale_fail_ignored",reason,frame.epoch,frame);
        }
        synchronized boolean valid(VideoFrame frame){return frame.epoch==epoch&&(!waiting||frame.recoveryIdr());}
        synchronized void success(VideoFrame frame){
            if(frame.epoch!=epoch||!frame.recoveryIdr()||frame.startupPreparation)return;
            if(startup.enabled()&&(closed||startup.phase()==CodecStartupGate.FAILED||startup.phase()==CodecStartupGate.CLOSED))return;
            // The gate and FIFO commit are one monitor transition; a timeout
            // or unmatched selected IDR cannot open one without the other.
            if(startup.enabled()&&!startup.committed(frame.ptsUs,System.nanoTime()))return;
            boolean wasWaiting=waiting;
            waiting=false;
            if(epoch>0&&lastRecoveredEpoch!=epoch){recoveryCompleted++;lastRecoveredEpoch=epoch;}
            if(wasWaiting)record("recovered",epoch==0?"initial_idr_committed":"epoch_idr_committed",epoch,frame);
        }
        synchronized void success(VideoFrame frame,MainActivity activity,int generation,boolean stopping){
            // Read generation/stop under the same Inbox monitor as the epoch
            // transition. cancelStartup uses this monitor before a late commit.
            if(startup.enabled()&&(stopping||!activity.running||activity.generation!=generation||activity.video==null))return;
            success(frame);
        }
        synchronized boolean needsIdr(){return waiting;}
        synchronized VideoFrame take()throws InterruptedException{
            while(queue.isEmpty()&&!closed)wait(20);
            VideoFrame frame=queue.pollFirst();if(frame!=null)bytes-=frame.body.length;
            if(stages!=null){long takenNs=System.nanoTime();stages.inboxDepth(takenNs,queue.size(),bytes);
                if(frame!=null)stages.inboxTaken(takenNs,frame.ptsUs,frame.receivedNs,queue.size(),bytes);}
            return frame;
        }
        synchronized long currentEpoch(){return epoch;}
        synchronized boolean beginStartupPreparation(VideoFrame frame,long nowNs){
            if(closed||frame.epoch!=epoch||!frame.startupPreparation||!startup.preparing(frame.ptsUs,frame.width,frame.height))return false;
            if(nowNs<frame.receivedNs||nowNs-frame.receivedNs>=CodecStartupGate.MAX_AGE_NS){startup.bootstrapExpired();return false;}
            startup.preparationStarted(frame.ptsUs,nowNs);return true;
        }
        synchronized boolean completeStartupPreparation(VideoFrame frame,long nowNs){
            return !closed&&frame.epoch==epoch&&startup.prepared(frame.ptsUs,frame.width,frame.height,nowNs);
        }
        synchronized void failStartupPreparation(){startup.configureFailed();}
        synchronized boolean startupRequestDue(long nowNs){return startup.requestDue(nowNs);}
        synchronized boolean startupOwnsRecovery(){int phase=startup.phase();return phase!=CodecStartupGate.DISABLED&&phase!=CodecStartupGate.STREAMING;}
        synchronized int startupFailure(long nowNs){startup.checkTimeout(nowNs);return startup.failureCode();}
        synchronized void cancelStartup(){if(startup.enabled())startup.close(System.nanoTime());}
        synchronized JSONObject startupSnapshot()throws Exception{
            long[] values=startup.snapshot();JSONObject result=new JSONObject();
            for(int i=0;i<values.length;i++)result.put(CodecStartupGate.STAT_NAMES[i],values[i]);return result;
        }
        synchronized void close(){closed=true;startup.close(System.nanoTime());notifyAll();}
        synchronized JSONObject snapshot()throws Exception{
            return new JSONObject().put("capacity_frames",MAX_FRAMES).put("capacity_bytes",MAX_BYTES)
                .put("maximum_depth",maxDepth).put("maximum_bytes",maxBytes).put("admitted_frames",admitted)
                .put("overflow_events",overflowEvents).put("overflow_rejected_frames",overflowRejected)
                .put("queue_cleared_frames",cleared).put("waiting_idr_admission_drops",waitingDrops)
                .put("oversize_admission_drops",oversizeDrops).put("closing_drops",closingDrops)
                .put("recovery_epochs",recoveryEpochs).put("recovery_idrs_completed",recoveryCompleted)
                .put("needs_idr",waiting).put("pending_frames",queue.size()).put("pending_bytes",bytes)
                .put("epoch_event_capacity",MAX_INBOX_EVENTS).put("epoch_events_evicted",eventsEvicted);
        }
        synchronized JSONArray eventSnapshot()throws Exception{
            JSONArray result=new JSONArray();
            for(InboxEvent e:events)result.put(new JSONObject().put("event",e.event).put("reason",e.reason)
                .put("epoch",e.event.equals("chain_lost")?e.epoch+1:e.epoch).put("previous_epoch",e.previousEpoch)
                .put("time_ns",e.timeNs).put("pts_us",e.ptsUs).put("received_ns",e.receivedNs)
                .put("queue_frames",e.queueFrames).put("queue_bytes",e.queueBytes));
            return result;
        }
    }

    // App mode is memory-only; instrumentation retains its restrictive temporary file contract.
    private MainActivity appActivity; private Session appSession; private AppListener appListener;
    private NpsPhysicalNetwork appPhysicalNetwork;
    private volatile boolean appCancelled;
    private volatile int appGeneration=-1;
    public interface AppListener { void complete(JSONObject numericReport, boolean failed); }
    public static UdpVideoProbe startApp(MainActivity activity,JSONObject descriptor,AppListener listener)throws Exception {
        return startApp(activity,descriptor,false,listener);
    }
    public static UdpVideoProbe startApp(MainActivity activity,JSONObject descriptor,boolean boundedPcmQueueEnabled,AppListener listener)throws Exception {
        return startApp(activity,descriptor,boundedPcmQueueEnabled,true,listener);
    }
    public static UdpVideoProbe startApp(MainActivity activity,JSONObject descriptor,boolean boundedPcmQueueEnabled,boolean stageDiagnosticsEnabled,AppListener listener)throws Exception {
        return startApp(activity,descriptor,boundedPcmQueueEnabled,stageDiagnosticsEnabled,false,listener);
    }
    public static UdpVideoProbe startApp(MainActivity activity,JSONObject descriptor,boolean boundedPcmQueueEnabled,boolean stageDiagnosticsEnabled,boolean codecStartupReadyEnabled,AppListener listener)throws Exception {
        return startApp(activity,descriptor,boundedPcmQueueEnabled,stageDiagnosticsEnabled,codecStartupReadyEnabled,null,listener);
    }
    public static UdpVideoProbe startApp(MainActivity activity,JSONObject descriptor,boolean boundedPcmQueueEnabled,boolean stageDiagnosticsEnabled,boolean codecStartupReadyEnabled,NpsPhysicalNetwork physicalNetwork,AppListener listener)throws Exception {
        NpsPhysicalNetwork.validateScope(descriptor.getString("network_scope"),physicalNetwork);
        if(physicalNetwork!=null&&physicalNetwork.httpsBindings()<1)throw new IOException("public_nps_authenticated_control_network_required");
        UdpVideoProbe runner=new UdpVideoProbe();runner.appActivity=activity;
        runner.appSession=parseSession(descriptor,true);runner.appSession.boundedPcmQueueEnabled=boundedPcmQueueEnabled;runner.appListener=listener;
        runner.appPhysicalNetwork=physicalNetwork;
        runner.stageDiagnosticsEnabled=stageDiagnosticsEnabled;
        runner.codecStartupReadyEnabled=codecStartupReadyEnabled;
        new Thread(runner::onStart,"authenticated-lan-udp").start();return runner;
    }
    public void cancelApp(){
        appCancelled=true;
        VideoInbox inbox=videoInbox;if(inbox!=null)inbox.cancelStartup();
        MainActivity a=appActivity;
        if(a!=null&&appGeneration>=0&&a.generation==appGeneration){a.running=false;}
        // Keep the socket open until close sends CANCEL and STOP; 20ms receive timeout bounds exit.
    }
    private void mainSync(Runnable runnable){
        if(appActivity==null){runOnMainSync(runnable);return;}
        if(android.os.Looper.myLooper()==android.os.Looper.getMainLooper()){runnable.run();return;}
        final Throwable[] failure={null};java.util.concurrent.CountDownLatch done=new java.util.concurrent.CountDownLatch(1);
        appActivity.ui.post(()->{try{runnable.run();}catch(Throwable e){failure[0]=e;}finally{done.countDown();}});
        try{if(!done.await(5,java.util.concurrent.TimeUnit.SECONDS))throw new IllegalStateException("UI timeout");}
        catch(InterruptedException e){Thread.currentThread().interrupt();throw new IllegalStateException(e);}
        if(failure[0]!=null)throw new IllegalStateException(failure[0]);
    }

    public void onCreate(Bundle args){super.onCreate(args);start();}

    public void onStart(){
        Bundle result=new Bundle();JSONObject report=new JSONObject();MainActivity activity=null;
        File keyFile=appActivity==null?new File(getTargetContext().getFilesDir(),SESSION):null;
        DatagramSocket socket=null;Session session=null;UdpVideoSecurity security=null;long nativeHandle=0;JSONObject appReport=null;
        int oldFps=0,oldBuffer=0,oldDisplayModeId=0,oldSubmissionLeadMs=0;boolean oldImmediate=false;float oldRefresh=0;
        try{
            session=appActivity==null?readSession(keyFile):appSession;if(appCancelled)throw new IOException("cancelled");diagnosticEvents=session.diagnosticEvents;
            if(codecStartupReadyEnabled&&!session.asyncVideo)throw new IOException("codec_startup_requires_async_video");
            contentHintFps=session.contentHintFps;completed="session_validated";
            report.put("test_scope",session.audioEnabled||session.touchEnabled
                    ?"isolated_real_phone_authenticated_udp_media_and_touch_components"
                    :"isolated_real_phone_authenticated_udp_video_component")
                .put("profile",session.profile).put("fps_limit",session.fps).put("buffer_ms",session.buffer)
                .put("video_release_mode",session.release).put("requested_seconds",session.seconds)
                .put("async_video_enabled",session.asyncVideo)
                .put("stage_diagnostics_enabled",stageDiagnosticsEnabled?1:0)
                .put("codec_startup_ready_enabled",codecStartupReadyEnabled)
                .put("decoder_reanchor_enabled",session.decoderReanchorEnabled)
                .put("diagnostic_events_enabled",session.diagnosticEvents).put("network_feedback_enabled",session.networkFeedback)
                .put("requested_display_hz",session.displayHz)
                .put("content_hint_explicit",session.contentHintFps>=0)
                .put("requested_content_hint_fps",session.contentHintFps)
                .put("intended_content_hint_fps",session.contentHintFps<0?session.fps:session.contentHintFps)
                .put("content_hint_scope","Probe-only Surface.setFrameRate request after existing decoder configure, before first input; absent preserves existing configure; 0 clears the content hint. This is not an actual panel or displayed FPS measurement.")
                .put("surface_submit_lead_ms",session.surfaceSubmitLeadMs)
                .put("surface_submit_scope","Probe-only delayed Surface submission; 0 preserves existing release path, 8/16 wait in at most 2ms requested parks with 80ms per-output budget. Absolute targets and shared audio clock are retained; counts do not prove physical latency or FPS improvement.")
                .put("actual_display_fps_measured",false).put("actual_audio_video_skew_measured",false)
                .put("audio_requested",session.audioEnabled).put("audio_tested",false)
                .put("native_touch_requested",session.touchEnabled).put("native_touch_tested",false)
                .put("tcp_video_used",false).put("tcp_audio_used",false)
                .put("transport","AES256_GCM_authenticated_UDP_native_10_plus_2_FEC")
                .put("assembly_clock_limitation","80ms assembly budget starts at first phone packet arrival; not a WAN one-way latency measurement");
            if(appActivity==null)NativeUdpFec.load(getContext().getApplicationInfo().nativeLibraryDir);else NativeUdpFec.loadApp();
            nativeHandle=NativeUdpFec.nativeCreate();if(nativeHandle==0)throw new IOException("native_create");
            enableMappingDetails(nativeHandle);
            if(session.diagnosticEvents)NativeUdpFec.nativeSetDiagnostics(nativeHandle,true);
            activity=appActivity!=null?appActivity:(MainActivity)startActivitySync(new Intent().setClassName(
                getTargetContext().getPackageName(),"local.remoteandroid.direct.MainActivity")
                .putExtra("huoguo_codec_component_probe",true).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            if(appActivity==null)waitForIdleSync();completed="activity_started";
            oldFps=activity.maxFps;oldBuffer=activity.bufferMs;oldImmediate=activity.probeImmediateVideoRelease;
            oldSubmissionLeadMs=activity.probeVideoSubmissionLeadMs;
            oldRefresh=activity.getWindow().getAttributes().preferredRefreshRate;
            oldDisplayModeId=activity.getWindow().getAttributes().preferredDisplayModeId;
            MainActivity a=activity;Session settings=session;final int[] generation={-1};
            final Throwable[] displayFailure={null};
            mainSync(()->{
                if(appCancelled)return;a.stop();a.maxFps=settings.fps;a.bufferMs=settings.buffer;
                a.probeImmediateVideoRelease=settings.release.equals("immediate");a.adaptive=null;a.diagnostics=null;
                a.probeVideoSubmissionLeadMs=settings.surfaceSubmitLeadMs;
                a.probeVideoSubmissionWaits.set(0);a.probeVideoSubmissionWaitNs.set(0);a.probeVideoSubmissionWaitedOutputs.set(0);
                a.probeVideoSubmissionMaxHoldNs.set(0);a.probeVideoSubmissionMaxParkNs.set(0);a.probeVideoSubmissionBudgetFallbacks.set(0);
                a.playback=new PlaybackClock(settings.buffer,0,settings.decoderReanchorEnabled);a.presentationMetrics=new MediaPresentationMetrics(48000,stageDiagnosticsEnabled);
                MediaPresentationMetrics.StageDiagnostics stages=a.presentationMetrics.decoderStages;
                if(stages!=null){stages.markCoverage(MediaPresentationMetrics.StageDiagnostics.CONFIGURE
                        |MediaPresentationMetrics.StageDiagnostics.CONSUMER|MediaPresentationMetrics.StageDiagnostics.FEC
                        |MediaPresentationMetrics.StageDiagnostics.CLOCK|MediaPresentationMetrics.StageDiagnostics.GUARD
                        |MediaPresentationMetrics.StageDiagnostics.INPUT_CALL|MediaPresentationMetrics.StageDiagnostics.COPY
                        |(settings.asyncVideo?MediaPresentationMetrics.StageDiagnostics.TAKE:0));
                    a.playback.setObserver(stages::clockReanchor);}
                a.receivedFrames.set(0);a.receivedVideoBytes.set(0);a.presentedFrames.set(0);a.lateDiscardedFrames.set(0);
                a.audioOutputBytes.set(0);
                a.running=true;generation[0]=++a.generation;appGeneration=generation[0];
                a.canvas=new FrameLayout(a);a.screen=new SurfaceView(a);
                a.canvas.addView(a.screen,new FrameLayout.LayoutParams(-1,-1,Gravity.CENTER));
                if(appActivity!=null){android.widget.Button leave=new android.widget.Button(a);leave.setText("离开 UDP 测试");
                    leave.setOnClickListener(v->a.lanUdpEntry.cancel(true));
                    FrameLayout.LayoutParams button=new FrameLayout.LayoutParams(-2,-2,Gravity.BOTTOM|Gravity.END);a.canvas.addView(leave,button);}
                a.setContentView(a.canvas);
                try{requestDisplayMode(a,settings.displayHz,settings.fps,report);}
                catch(Throwable failure){displayFailure[0]=failure;}
            });
            if(appCancelled||generation[0]<0)throw new IOException("cancelled");
            if(displayFailure[0]!=null)throw new IOException("requested_display_mode_unavailable",displayFailure[0]);
            recordDisplayStart(activity,session.displayHz,report);
            completed="surface_created";
            socket=new DatagramSocket(null);socket.setReuseAddress(false);
            if(appPhysicalNetwork!=null){appPhysicalNetwork.bind(socket);
                report.put("nps_physical_network_binding",new JSONObject().put("api_bind_succeeded",true)
                    .put("network_handle",appPhysicalNetwork.handle()).put("transport_at_selection",appPhysicalNetwork.transport())
                    .put("https_bind_count_at_udp_start",appPhysicalNetwork.httpsBindings()).put("udp_bind_count",appPhysicalNetwork.udpBindings())
                    .put("same_network_for_control_and_media",appPhysicalNetwork.httpsBindings()>0&&appPhysicalNetwork.udpBindings()>0).put("default_network_fallback",false)
                    .put("packet_route_verified",false).put("domestic_country_verified",false));}
            socket.setReceiveBufferSize(4*1024*1024);socket.bind(new InetSocketAddress(session.bindPort));socket.setSoTimeout(20);
            report.put("socket_receive_buffer_bytes",socket.getReceiveBufferSize());
            security=new UdpVideoSecurity(session.key,session.tag);
            if(session.audioEnabled)audioReceiver=new UdpAudioReceiver(activity,generation[0],session.boundedPcmQueueEnabled);
            if(session.touchEnabled){
                DatagramSocket sharedSocket=socket;Session sharedSession=session;MainActivity target=activity;UdpVideoSecurity sharedSecurity=security;
                mainSync(()->touchControl=new UdpTouchControl(target.screen,
                    plaintext->sendPayload(sharedSocket,sharedSession,sharedSecurity,plaintext)));
            }
            if(session.asyncVideo)startVideoWorker(activity,generation[0]);
            receive(activity,generation[0],socket,session,security,nativeHandle);
            completed="receive_window_complete";
            finishVideoWorker(true);checkVideoWorker();
            long drain=System.nanoTime()+1_000_000_000L;
            while(activity.running&&activity.generation==generation[0]&&System.nanoTime()<drain){
                if(activity.presentedFrames.get()+activity.lateDiscardedFrames.get()>=queuedMedia){Thread.sleep(100);break;}
                Thread.sleep(20);
            }
            completed="bounded_drain_complete";
            report.put("running_at_end",activity.running&&activity.generation==generation[0]);
        }catch(Throwable failure){
            result.putString("failure","UdpVideoProbe "+failure.getClass().getSimpleName());
            try{report.put("failure_class",failure.getClass().getSimpleName());}catch(Exception ignored){}
        }finally{
            try{finishVideoWorker(false);checkVideoWorker();}
            catch(Throwable failure){result.putString("failure","UdpVideoProbe video worker "+failure.getClass().getSimpleName());
                try{report.put("video_worker_failure_class",failure.getClass().getSimpleName());}catch(Exception ignored){}}
            if(touchControl!=null){try{touchControl.close();}catch(Exception ignored){}}
            if(audioReceiver!=null)audioReceiver.close();
            if(appActivity!=null&&socket!=null&&session!=null&&security!=null){
                for(int i=0;i<3;i++)try{sendPayload(socket,session,security,"STOP".getBytes(StandardCharsets.US_ASCII));}catch(Exception ignored){}
            }
            if(socket!=null)socket.close();
            try{
                report.put("last_completed_stage",completed).put("start_ns",startNs).put("first_server_packet_ns",firstServerNs)
                    .put("receive_end_ns",endNs).put("observation_end_ns",System.nanoTime())
                    .put("udp_packets",packets).put("udp_payload_bytes",udpBytes).put("foreign_peer_packets",foreign)
                    .put("oversized_packets",oversized).put("header_errors",headerErrors).put("authentication_errors",authErrors)
                    .put("replay_errors",replayErrors).put("authenticated_packets",authenticated).put("ready_requests",readySent)
                    .put("keyframe_feedback_requests",feedbackSent).put("keyframe_feedback_times_ns",feedbackTimes)
                    .put("received_media_frames",receivedMedia).put("queued_media_frames",queuedMedia).put("queued_config_records",configRecords)
                    .put("logical_body_errors",bodyErrors).put("decoder_input_timeouts",inputTimeouts).put("waiting_idr_dropped_frames",waitingIdrDrops)
                    .put("last_body_pts_us",lastBodyPts).put("last_queued_pts_us",lastQueuedPts)
                    .put("source_access_unit_bytes",auSizes)
                    .put("samples",samples).put("recovery_feedback_limitation","Request counts do not prove IDR delivery or completed visible recovery");
                boolean workerAlive=videoWorker!=null&&videoWorker.isAlive();
                if(!workerAlive)report.put("media_input_observations",inputs).put("accepted_media_receive_to_input_queue_ms",inputWait)
                    .put("video_worker_queue_wait_ms",videoQueueWait);
                else report.put("video_worker_arrays_omitted","worker_not_joined");
                report.put("video_worker_alive",workerAlive).put("video_worker_join_timed_out",videoWorkerJoinTimedOut)
                    .put("video_worker_failure_class",videoWorkerFailure==null?"":videoWorkerFailure.getClass().getSimpleName())
                    .put("receive_loop_iterations",receiveIterations).put("receive_loop_max_ms",receiveLoopMaxNs/1e6)
                    .put("receive_processing_max_ms",receiveProcessingMaxNs/1e6).put("receive_socket_wait_max_ms",receiveSocketWaitMaxNs/1e6)
                    .put("receive_loop_over_80ms",receiveLoopOver80);
                if(videoInbox!=null){report.put("video_input_queue",videoInbox.snapshot()).put("codec_startup_gate",videoInbox.startupSnapshot());
                    if(!workerAlive)report.put("inbox_epoch_events",videoInbox.eventSnapshot());
                    if(!workerAlive)report.put("video_worker_frames",videoWorkerFrames).put("video_worker_expired_frames",videoWorkerExpired)
                        .put("video_worker_stale_epoch_drops",videoWorkerStale).put("video_input_reservations_cancelled",videoInputReservationsCancelled)
                        .put("video_codec_timeout_recovery_events",videoCodecTimeoutRecoveryEvents)
                        .put("video_worker_wait_observations",videoWorkerWaitSamples)
                        .put("video_worker_wait_mean_ms",videoWorkerWaitSamples==0?0:videoWorkerWaitTotalNs/1e6/videoWorkerWaitSamples)
                        .put("video_worker_wait_max_ms",videoWorkerWaitMaxNs/1e6);}
                report.put("unknown_application_packets",unknownApplicationPackets);
                report.put("content_hint_applications",contentHintApplications)
                    .put("content_hint_application_status",contentHintFps<0?"inherited_main_activity_configure"
                        :contentHintApplications>0?"explicit_request_applied":"explicit_request_not_applied");
                if(audioReceiver!=null){JSONObject audioReport=audioReceiver.snapshot();
                    report.put("udp_audio",audioReport).put("audio_tested",audioReceiver.hasDecodedAudio());
                    report.put("audio_cleanup_confirmed",audioReport.optBoolean("pcm_cleanup_incomplete",false)?0:1);
                    if(audioReport.optBoolean("pcm_cleanup_incomplete",false))result.putString("failure","UdpVideoProbe audio cleanup unconfirmed");
                }else report.put("audio_cleanup_confirmed",1);
                if(touchControl!=null)report.put("udp_touch",touchControl.snapshot());
                if(nativeHandle!=0){
                    try{readMappingDetails(nativeHandle);}
                    catch(NativeUdpFec.MappingDetailsException failure){result.putString("failure","UdpVideoProbe mapping diagnostics "+failure.status);}
                    if(diagnosticEvents){drainNativeEvents(nativeHandle);report.put("native_frame_events",new JSONArray(nativeFrameEvents))
                        .put("native_frame_event_capacity",MAX_NATIVE_EVENTS).put("native_frame_events_evicted",nativeFrameEventsEvicted)
                        .put("native_frame_event_stats",nativeEventStats(nativeHandle))
                        .put("native_frame_event_clock_scope","Phone RX arrival/tick decision times in us; fec_quorum_ready is distinct from logical validation/delivery, not capture-to-display latency.");}
                    long[] finalStats=NativeUdpFec.nativeStats(nativeHandle);
                    if(finalStats.length!=NativeUdpFec.STAT_NAMES.length)throw new IOException("native_stats_contract");
                    if(activity!=null)observeFecDiagnostics(activity,System.nanoTime(),finalStats);
                    report.put("native_fec",nativeStats(finalStats));
                }
                report.put("native_mapping_details",mappingDetailsSummary(mappingDetails,mappingDetailStatus,
                    mappingDetailEnableAttempted,mappingDetailReads,mappingDetailReadFailures,
                    mappingDetailReadTotalNs,mappingDetailReadMaxNs,mappingDetailLastReadNs));
                report.put("network_feedback_sent",networkFeedbackSent).put("network_feedback_send_max_ms",networkFeedbackSendMaxNs/1e6)
                    .put("diagnostic_drain_max_ms",diagnosticDrainMaxNs/1e6).put("ping_replies",pingReplies).put("ping_replies_throttled",pingThrottled)
                    .put("authenticated_hgud_datagrams",authenticatedVideoPackets).put("estimated_hgud_ipv4_wire_bytes",authenticatedVideoWireBytes)
                    .put("network_feedback_scope","RX-only cumulative video counters; loss proxies are FEC recovered shards/reference expiry, not measured packet loss; IPv4 bytes exclude Ethernet/VPN/radio overhead.");
                if(activity!=null){
                    MainActivity displayActivity=activity;final JSONObject[] currentDisplay={null};
                    mainSync(()->currentDisplay[0]=displayReadback(displayActivity));
                    report.put("display_mode_end",currentDisplay[0]);
                    report.put("display_mode_end_matches_requested",displayMatches(currentDisplay[0],session==null?0:session.displayHz));
                    report.put("codec_callback_count",activity.presentedFrames.get()).put("late_discarded_count",activity.lateDiscardedFrames.get())
                        .put("source_geometry",width+"x"+height).put("source_width",width).put("source_height",height).put("decoder",activity.videoDecoderName).put("hardware",activity.hardwareVideo);
                    long submissionWaits=activity.probeVideoSubmissionWaits.get(),waitedOutputs=activity.probeVideoSubmissionWaitedOutputs.get();
                    report.put("surface_submit_applications",waitedOutputs).put("surface_submit_wait_count",submissionWaits)
                        .put("surface_submit_wait_total_ms",activity.probeVideoSubmissionWaitNs.get()/1e6)
                        .put("surface_submit_max_output_hold_ms",activity.probeVideoSubmissionMaxHoldNs.get()/1e6)
                        .put("surface_submit_max_park_ms",activity.probeVideoSubmissionMaxParkNs.get()/1e6)
                        .put("surface_submit_budget_fallbacks",activity.probeVideoSubmissionBudgetFallbacks.get())
                        .put("surface_submit_status",session==null?"session_not_validated":session.surfaceSubmitLeadMs==0?"disabled_existing_release_path"
                            :submissionWaits>0?"applied_bounded_wait":"enabled_no_wait_observed");
                    if(activity.presentationMetrics!=null){CodecFileProbe.appendPresentation(report,activity.presentationMetrics.snapshot());
                        if(activity.presentationMetrics.decoderStages!=null)report.put("decoder_stage_metrics",
                            stageSummary(activity.presentationMetrics.decoderStages.snapshot(System.nanoTime())));}
                }
                if(appActivity==null)writeReport(new File(getTargetContext().getFilesDir(),REPORT),report,result);
                else appReport=numericAppSummary(report);
            }catch(Throwable failure){result.putString("failure","UdpVideoProbe report "+failure.getClass().getSimpleName());}
            finally{
                if(nativeHandle!=0)NativeUdpFec.nativeDestroy(nativeHandle);
                if(session!=null)Arrays.fill(session.key,(byte)0);
                // Fixed temporary test key file only. Never delete arbitrary app files.
                if(keyFile!=null&&keyFile.exists()&&!keyFile.delete())result.putString("key_cleanup","fixed_session_delete_failed");
                if(activity!=null){
                    MainActivity a=activity;int fps=oldFps,buffer=oldBuffer,modeId=oldDisplayModeId,submissionLead=oldSubmissionLeadMs;boolean immediate=oldImmediate;float refresh=oldRefresh;
                    try{mainSync(()->{
                        if(appActivity!=null&&a.generation!=appGeneration)return;
                        a.stop();a.maxFps=fps;a.bufferMs=buffer;a.probeImmediateVideoRelease=immediate;
                        a.probeVideoSubmissionLeadMs=submissionLead;
                        WindowManager.LayoutParams window=a.getWindow().getAttributes();window.preferredRefreshRate=refresh;
                        window.preferredDisplayModeId=modeId;a.getWindow().setAttributes(window);
                        if(appActivity==null){a.getIntent().removeExtra("huoguo_codec_component_probe");a.login();}
                    });}catch(Throwable cleanupFailure){result.putString("failure","UdpVideoProbe cleanup "+cleanupFailure.getClass().getSimpleName());}
                }
            }
        }
        if(appActivity==null)finish(result.containsKey("failure")?Activity.RESULT_CANCELED:Activity.RESULT_OK,result);
        else if(appListener!=null)appListener.complete(appReport==null?new JSONObject():appReport,result.containsKey("failure"));
    }

    /** Window-only experiment request; never changes global display settings. */
    private static void requestDisplayMode(MainActivity activity,int requestedHz,int legacyFps,JSONObject report)throws Exception{
        WindowManager.LayoutParams window=activity.getWindow().getAttributes();
        if(requestedHz==0){
            window.preferredRefreshRate=legacyFps;activity.getWindow().setAttributes(window);
            report.put("display_mode_selection","legacy_fps_hint").put("selected_mode_id",0).put("selected_mode_hz",0);
            return;
        }
        Display display=activity.getWindowManager().getDefaultDisplay();
        if(display==null){report.put("display_mode_selection","display_unavailable");throw new IOException("display_unavailable");}
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
        if(selected==null){
            report.put("display_mode_selection","requested_mode_unavailable_same_resolution")
                .put("selected_mode_id",0).put("selected_mode_hz",0);
            throw new IOException("requested_mode_unavailable_same_resolution");
        }
        window.preferredDisplayModeId=selected.getModeId();window.preferredRefreshRate=selected.getRefreshRate();
        activity.getWindow().setAttributes(window);
        report.put("display_mode_selection","window_mode_request")
            .put("selected_mode_id",selected.getModeId()).put("selected_mode_hz",selected.getRefreshRate())
            .put("selected_mode_width",selected.getPhysicalWidth()).put("selected_mode_height",selected.getPhysicalHeight());
    }

    private static JSONObject displayReadback(MainActivity activity){
        JSONObject result=new JSONObject();
        try{
            Display display=activity.getWindowManager().getDefaultDisplay();
            if(display==null)return result.put("available",false);
            Display.Mode mode=display.getMode();
            result.put("available",true).put("mode_id",mode.getModeId()).put("refresh_hz",mode.getRefreshRate())
                .put("width",mode.getPhysicalWidth()).put("height",mode.getPhysicalHeight())
                .put("readback_ns",System.nanoTime());
        }catch(Exception failure){try{result.put("available",false).put("failure_class",failure.getClass().getSimpleName());}catch(Exception ignored){}}
        return result;
    }

    private static boolean displayMatches(JSONObject mode,int requestedHz){
        return requestedHz>0&&mode!=null&&mode.optBoolean("available",false)
            &&Math.abs(mode.optDouble("refresh_hz",Double.NaN)-requestedHz)<=1.0;
    }

    private void recordDisplayStart(MainActivity activity,int requestedHz,JSONObject report)throws Exception{
        final JSONObject[] readback={null};long deadline=System.nanoTime()+500_000_000L;
        do{
            mainSync(()->readback[0]=displayReadback(activity));
            if(requestedHz==0||displayMatches(readback[0],requestedHz)||System.nanoTime()>=deadline)break;
            Thread.sleep(20);
        }while(true);
        report.put("display_mode_start",readback[0])
            .put("display_mode_start_matches_requested",displayMatches(readback[0],requestedHz))
            .put("display_request_limitation","Window mode requests are hints; readbacks and independent SurfaceFlinger cadence determine actual display behavior.");
    }

    private void receive(MainActivity a,int gen,DatagramSocket socket,Session session,UdpVideoSecurity security,long handle)throws Exception{
        startNs=System.nanoTime();networkIntervalStartNs=startNs;long overall=startNs+(session.seconds+30L)*1_000_000_000L,nextReady=0;
        long nextAlive=0;long[] previous={startNs,0,0,0,0};byte[] data=new byte[UdpVideoSecurity.MAX_DATAGRAM+1];DatagramPacket packet=new DatagramPacket(data,data.length);
        while(a.running&&a.generation==gen&&!appCancelled){
            checkVideoWorker();
            long now=System.nanoTime();long deadline=firstServerNs==0?overall:Math.min(overall,firstServerNs+session.seconds*1_000_000_000L);
            if(now>=deadline)break;
            if(appActivity!=null&&now>=nextAlive){if(appPhysicalNetwork!=null)appPhysicalNetwork.requireUsable();sendPayload(socket,session,security,"ALIVE".getBytes(StandardCharsets.US_ASCII));nextAlive=now+750_000_000L;}
            if(firstServerNs==0&&now>=nextReady){sendPayload(socket,session,security,"READY".getBytes(StandardCharsets.US_ASCII));readySent++;nextReady=now+500_000_000L;}
            packet.setLength(data.length);
            long iterationStarted=System.nanoTime(),processingStarted=iterationStarted,socketStarted=iterationStarted;
            try{
                socket.receive(packet);long arrival=System.nanoTime();processingStarted=arrival;
                receiveSocketWaitMaxNs=Math.max(receiveSocketWaitMaxNs,arrival-socketStarted);packets++;udpBytes+=packet.getLength();
                intervalSocketWaitMaxNs=Math.max(intervalSocketWaitMaxNs,arrival-socketStarted);
                if(!packet.getAddress().equals(session.peer)||packet.getPort()!=session.peerPort)foreign++;
                else if(packet.getLength()>UdpVideoSecurity.MAX_DATAGRAM)oversized++;
                else{
                    try{
                        byte[] plaintext=security.open(data,packet.getLength());authenticated++;
                        int application=plaintext.length>=4?ByteBuffer.wrap(plaintext).getInt():0;
                        if(application==0x48475544){
                            authenticatedVideoPackets++;authenticatedVideoWireBytes+=packet.getLength()+28L;
                            if(plaintext.length>=56){
                                if(firstServerNs==0){firstServerNs=arrival;networkIntervalStartNs=arrival;}
                                long id=ByteBuffer.wrap(plaintext).getLong(16);if(id>highestAuthenticatedFrame)highestAuthenticatedFrame=id;
                            }
                            consume(a,gen,NativeUdpFec.nativeAccept(handle,plaintext,arrival/1000L));
                        }else if(application==UdpAudioAssembler.MAGIC&&audioReceiver!=null)audioReceiver.accept(plaintext,arrival);
                        else if(application==0x48475441&&touchControl!=null){if(!touchControl.onAck(plaintext))unknownApplicationPackets++;}
                        else if(application==0x48475051&&session.networkFeedback){
                            if(plaintext.length!=16)unknownApplicationPackets++;
                            else if(arrival-lastPingReplyNs<50_000_000L)pingThrottled++;
                            else{
                                long sending=System.nanoTime();byte[] reply=pingReply(plaintext,arrival/1000L,sending/1000L);
                                sendPayload(socket,session,security,reply);lastPingReplyNs=sending;pingReplies++;
                            }
                        }
                        else unknownApplicationPackets++;
                    }catch(UdpVideoSecurity.Rejected invalid){
                        if(invalid.reason.equals("authentication"))authErrors++;
                        else if(invalid.reason.equals("replay"))replayErrors++;else headerErrors++;
                    }
                }
            }catch(SocketTimeoutException idle){processingStarted=System.nanoTime();
                receiveSocketWaitMaxNs=Math.max(receiveSocketWaitMaxNs,processingStarted-socketStarted);
                intervalSocketWaitMaxNs=Math.max(intervalSocketWaitMaxNs,processingStarted-socketStarted);}
            now=System.nanoTime();consume(a,gen,NativeUdpFec.nativeExpire(handle,now/1000L));
            if(audioReceiver!=null)audioReceiver.poll(now);
            if(now-lastStatsNs>=100_000_000L){
                long[] stats=NativeUdpFec.nativeStats(handle);if(stats.length!=NativeUdpFec.STAT_NAMES.length)throw new IOException("native_stats_contract");
                observeFecDiagnostics(a,System.nanoTime(),stats);
                readMappingDetails(handle);
                if(videoInbox!=null&&videoInbox.startupOwnsRecovery()){
                    // Preparing never spends the post-ready request budget. RX owns
                    // nonce allocation/sending; no codec call holds the Inbox monitor.
                    if(videoInbox.startupRequestDue(now)){
                        sendPayload(socket,session,security,"KEYFRAME".getBytes(StandardCharsets.US_ASCII));feedbackSent++;feedbackTimes.put(now);
                    }
                    recoveryActive=false;recoveryAttempts=0;
                }else{
                    boolean needs=(videoInbox==null?waitingIdr:videoInbox.needsIdr())||stats[19]!=0;
                    if(needs&&!recoveryActive){recoveryActive=true;recoveryAttempts=0;nextFeedbackNs=now;}
                    if(!needs){recoveryActive=false;recoveryAttempts=0;}
                    if(firstServerNs!=0&&needs&&recoveryAttempts<3&&now>=nextFeedbackNs){
                        sendPayload(socket,session,security,"KEYFRAME".getBytes(StandardCharsets.US_ASCII));feedbackSent++;feedbackTimes.put(now);
                        recoveryAttempts++;nextFeedbackNs=now+(500_000_000L<<(recoveryAttempts-1));
                    }
                }
                lastStatsNs=now;
            }
            if(now-previous[0]>=1_000_000_000L){
                double seconds=(now-previous[0])/1e9;
                samples.put(new JSONObject().put("t_ns",now).put("media_receive_fps",(receivedMedia-previous[1])/seconds)
                    .put("codec_callback_fps",(a.presentedFrames.get()-previous[2])/seconds)
                    .put("authenticated_packets",authenticated).put("udp_payload_mbps",(udpBytes-previous[3])*8/seconds/1e6)
                    .put("late_discarded_fps",(a.lateDiscardedFrames.get()-previous[4])/seconds).put("native_fec",nativeStats(handle)));
                previous[0]=now;previous[1]=receivedMedia;previous[2]=a.presentedFrames.get();previous[3]=udpBytes;previous[4]=a.lateDiscardedFrames.get();
            }
            if(diagnosticEvents&&now-lastNativeDrainNs>=100_000_000L){
                long drainStarted=System.nanoTime();drainNativeEvents(handle);lastNativeDrainNs=System.nanoTime();
                diagnosticDrainMaxNs=Math.max(diagnosticDrainMaxNs,lastNativeDrainNs-drainStarted);
            }
            long iterationEnded=System.nanoTime(),loopElapsed=iterationEnded-iterationStarted;
            receiveIterations++;receiveLoopMaxNs=Math.max(receiveLoopMaxNs,loopElapsed);
            receiveProcessingMaxNs=Math.max(receiveProcessingMaxNs,iterationEnded-processingStarted);
            intervalProcessingMaxNs=Math.max(intervalProcessingMaxNs,iterationEnded-processingStarted);
            if(loopElapsed>80_000_000L)receiveLoopOver80++;
            if(session.networkFeedback&&firstServerNs!=0&&iterationEnded-networkIntervalStartNs>=100_000_000L){
                long[] stats=NativeUdpFec.nativeStats(handle);if(stats.length!=NativeUdpFec.STAT_NAMES.length)throw new IOException("native_stats_contract");
                byte[] feedback=networkFeedbackPayload(new long[]{++networkFeedbackSequence,networkIntervalStartNs/1000L,iterationEnded/1000L,
                    authenticatedVideoPackets,authenticatedVideoWireBytes,stats[18],stats[6],stats[9],stats[8],receiveLoopOver80,
                    intervalProcessingMaxNs/1000L,intervalSocketWaitMaxNs/1000L,highestAuthenticatedFrame});
                long sending=System.nanoTime();sendPayload(socket,session,security,feedback);networkFeedbackSent++;
                long sent=System.nanoTime();networkFeedbackSendMaxNs=Math.max(networkFeedbackSendMaxNs,sent-sending);
                receiveLoopMaxNs=Math.max(receiveLoopMaxNs,sent-iterationStarted);
                receiveProcessingMaxNs=Math.max(receiveProcessingMaxNs,sent-processingStarted);
                if(loopElapsed<=80_000_000L&&sent-iterationStarted>80_000_000L)receiveLoopOver80++;
                networkIntervalStartNs=iterationEnded;intervalProcessingMaxNs=0;intervalSocketWaitMaxNs=0;
                // The current feedback send cannot appear in its own payload;
                // include its processing pressure in the following interval.
                intervalProcessingMaxNs=Math.max(intervalProcessingMaxNs,sent-processingStarted);
            }
        }
        endNs=System.nanoTime();checkVideoWorker();if(firstServerNs==0)throw new IOException("no_authenticated_media_within_bound");
    }

    private void consume(MainActivity a,int gen,byte[][] bodies)throws Exception{
        if(bodies==null)return;
        for(byte[] body:bodies){
            long receivedNs=System.nanoTime();
            if(body==null||body.length<25||body.length>8*1024*1024+65556){bodyErrors++;continue;}
            ByteBuffer metadata=ByteBuffer.wrap(body).order(ByteOrder.BIG_ENDIAN);
            int w=metadata.getInt(),h=metadata.getInt();long flags=metadata.getLong();int config=metadata.getInt();
            int au=body.length-20-config;boolean idr=(flags&KEYFRAME)!=0;long pts=flags&PTS_MASK;
            if(w<16||h<16||w>4096||h>4096||flags<0||(flags&CONFIG)!=0||pts>Long.MAX_VALUE/1000L
                    ||config<0||config>65536||au<5||au>8*1024*1024||config>0&&!idr
                    ||!annexB(body,20+config,au)||config>0&&!annexB(body,20,config)){bodyErrors++;continue;}
            lastBodyPts=pts;receivedMedia++;auSizes.put(au);
            VideoFrame frame=new VideoFrame(body,w,h,config,au,pts,receivedNs,idr,0);
            if(videoInbox!=null)videoInbox.offer(frame);else processVideo(a,gen,frame);
        }
    }

    private void startVideoWorker(MainActivity activity,int generation){
        videoInbox=new VideoInbox(diagnosticEvents,activity.presentationMetrics==null?null:activity.presentationMetrics.decoderStages,
            codecStartupReadyEnabled,System.nanoTime());
        videoWorker=new Thread(()->{
            try{
                while(!videoWorkerStop&&activity.running&&activity.generation==generation){
                    VideoFrame frame=videoInbox.take();if(frame==null)break;
                    MediaPresentationMetrics.StageDiagnostics stages=activity.presentationMetrics==null?null:activity.presentationMetrics.decoderStages;
                    beginConsumer(stages,frame.ptsUs);
                    try{
                    long waited=Math.max(0,System.nanoTime()-frame.receivedNs);
                    videoWorkerWaitSamples++;videoWorkerWaitTotalNs+=waited;videoWorkerWaitMaxNs=Math.max(videoWorkerWaitMaxNs,waited);
                    if(videoQueueWait.length()<3600)videoQueueWait.put(waited/1e6);
                    videoWorkerFrames++;
                    if(frame.startupPreparation){prepareStartupDecoder(activity,generation,frame);continue;}
                    if(!videoInbox.valid(frame)){videoWorkerStale++;continue;}
                    if(waited>=80_000_000L){videoWorkerExpired++;videoInbox.fail(frame,"worker_complete_au_expired");continue;}
                    processVideo(activity,generation,frame);
                    }finally{finishConsumer(stages,frame.ptsUs);}
                }
            }catch(InterruptedException stopped){
                if(!videoWorkerStop)videoWorkerFailure=stopped;
                Thread.currentThread().interrupt();
            }catch(Throwable failure){videoWorkerFailure=failure;}
        },"udp-video-input");
        videoWorker.setDaemon(true);videoWorker.start();
    }

    private static void beginConsumer(MediaPresentationMetrics.StageDiagnostics stages,long ptsUs){
        if(stages!=null)stages.consumerStarted(System.nanoTime(),ptsUs,android.os.Debug.threadCpuTimeNanos());
    }
    private static void finishConsumer(MediaPresentationMetrics.StageDiagnostics stages,long ptsUs){
        if(stages!=null){long cpuNs=android.os.Debug.threadCpuTimeNanos();stages.consumerFinished(System.nanoTime(),ptsUs,cpuNs);}
    }

    /** Cumulative native counters: only the poll observation time is known here.
     * No frame identity or native exception occurrence timestamp is invented.
     */
    private void observeFecDiagnostics(MainActivity activity,long observedNs,long[] stats){
        MediaPresentationMetrics.StageDiagnostics stages=activity.presentationMetrics==null?null:activity.presentationMetrics.decoderStages;
        if(stages==null)return;
        stages.fecPollObserved(observedNs);
        for(int index=0;index<FEC_DIAGNOSTIC_STAT_INDEX.length;index++){
            long value=stats[FEC_DIAGNOSTIC_STAT_INDEX[index]],delta=value-fecDiagnosticPrevious[index];
            if(delta!=0)stages.fecException(observedNs,-1,index+1,delta);
            fecDiagnosticPrevious[index]=value;
        }
    }

    private void checkVideoWorker()throws IOException{
        if(videoWorkerFailure!=null)throw new IOException("video_worker_failed",videoWorkerFailure);
        if(videoInbox!=null){int failure=videoInbox.startupFailure(System.nanoTime());
            if(failure!=0)throw new IOException("codec_startup_failed_"+failure);}
    }

    private void finishVideoWorker(boolean drain)throws InterruptedException,IOException{
        Thread worker=videoWorker;if(worker==null)return;
        videoInbox.close();
        if(!drain){videoWorkerStop=true;worker.interrupt();}
        worker.join(drain?1000:500);
        if(worker.isAlive()){
            videoWorkerStop=true;worker.interrupt();worker.join(500);
            if(worker.isAlive()){videoWorkerJoinTimedOut=true;throw new IOException("video_worker_join_timeout");}
        }
    }

    /** Called by exactly one input owner: receive thread (A), or video worker (B). */
    private void processVideo(MainActivity a,int gen,VideoFrame frame)throws Exception{
        if(frame.startupPreparation)throw new IOException("metadata_only_bootstrap_reached_media_input");
        MediaPresentationMetrics.StageDiagnostics stages=a.presentationMetrics==null?null:a.presentationMetrics.decoderStages;
        boolean ownsConsumer=videoInbox==null;
        if(ownsConsumer)beginConsumer(stages,frame.ptsUs);
        try{
        if(videoInbox!=null&&!videoInbox.valid(frame)){videoWorkerStale++;return;}
        if(videoInbox==null&&waitingIdr&&!frame.recoveryIdr()){waitingIdrDrops++;return;}
        if((width!=frame.width||height!=frame.height)&&!frame.recoveryIdr()){
            waitingIdrDrops++;loseVideoChain(frame,"geometry_without_idr");return;
        }
        a.playback.observe(frame.ptsUs,frame.receivedNs);
        a.presentationMetrics.received(frame.ptsUs,frame.receivedNs);a.receivedFrames.incrementAndGet();
        if(width!=frame.width||height!=frame.height){
            boolean configured=false;
            if(stages!=null)stages.configureStarted(System.nanoTime(),frame.ptsUs,frame.receivedNs);
            try{a.configure(frame.width,frame.height,gen);configured=true;}
            finally{if(stages!=null)stages.configureFinished(System.nanoTime(),frame.ptsUs,configured);}
            applyContentHint(a,gen);
            width=frame.width;height=frame.height;completed="decoder_configured";
        }
        if(touchControl!=null)touchControl.setGeometry(frame.width,frame.height,0);
        if(!a.running||a.generation!=gen||a.video==null)throw new IOException("codec_stopped");
        if(frame.configBytes>0&&!queue(a,gen,frame,20,frame.configBytes,true)){
            rejectInput(a,frame);return;
        }
        if(!queue(a,gen,frame,20+frame.configBytes,frame.auBytes,false)){
            rejectInput(a,frame);return;
        }
        if(videoInbox!=null)videoInbox.success(frame,a,gen,videoWorkerStop||appCancelled);else waitingIdr=false;
        lastQueuedPts=frame.ptsUs;
        inputs.put(new JSONObject().put("pts_us",frame.ptsUs).put("received_ns",frame.receivedNs).put("input_queued_ns",lastInputNs)
            .put("access_unit_bytes",frame.auBytes).put("config_bytes",frame.configBytes).put("keyframe",frame.idr));
        inputWait.put((lastInputNs-frame.receivedNs)/1e6);completed="media_queued";
        }finally{if(ownsConsumer)finishConsumer(stages,frame.ptsUs);}
    }

    /** Bootstrap geometry prepares the codec; its AU/PTS never enters media/clock. */
    private void prepareStartupDecoder(MainActivity a,int gen,VideoFrame frame)throws Exception{
        if(videoWorkerStop||appCancelled||!a.running||a.generation!=gen){videoInbox.cancelStartup();return;}
        if(!videoInbox.beginStartupPreparation(frame,System.nanoTime())){checkVideoWorker();return;}
        MediaPresentationMetrics.StageDiagnostics stages=a.presentationMetrics==null?null:a.presentationMetrics.decoderStages;
        boolean configured=false;
        if(stages!=null)stages.configureStarted(System.nanoTime(),frame.ptsUs,frame.receivedNs);
        try{
            // configure owns its existing codec lock. Keep the Inbox monitor free
            // so RX can authenticate/control/drop preparing traffic and cancel.
            if(videoWorkerStop||appCancelled||!a.running||a.generation!=gen){videoInbox.cancelStartup();return;}
            a.configure(frame.width,frame.height,gen);
            if(videoWorkerStop||appCancelled||!a.running||a.generation!=gen){videoInbox.cancelStartup();return;}
            if(a.video==null)throw new IOException("codec_missing_after_startup_prepare");
            configured=true;applyContentHint(a,gen);
            width=frame.width;height=frame.height;
            if(videoInbox.completeStartupPreparation(frame,System.nanoTime()))completed="startup_decoder_ready_waiting_fresh_idr";
            else checkVideoWorker();
        }catch(Exception failure){
            if(videoWorkerStop||appCancelled||!a.running||a.generation!=gen){
                videoInbox.cancelStartup();if(failure instanceof InterruptedException)Thread.currentThread().interrupt();return;
            }
            videoInbox.failStartupPreparation();throw failure;
        }
        finally{if(stages!=null)stages.configureFinished(System.nanoTime(),frame.ptsUs,configured);}
    }

    private void applyContentHint(MainActivity a,int gen)throws Exception{
        if(contentHintFps<0)return;
        if(!a.running||a.generation!=gen||a.video==null)throw new IOException("codec_stopped_before_content_hint");
        Surface surface=a.screen.getHolder().getSurface();
        if(!surface.isValid())throw new IOException("surface_invalid_for_content_hint");
        // No codec format, clock, listener, decoder output thread or absolute
        // release timestamp changes in this existing content-hint option.
        surface.setFrameRate(contentHintFps,Surface.FRAME_RATE_COMPATIBILITY_FIXED_SOURCE);contentHintApplications++;
    }

    private void loseVideoChain(VideoFrame frame,String reason){
        if(videoInbox!=null)videoInbox.fail(frame,reason);else waitingIdr=true;
    }

    private void rejectInput(MainActivity activity,VideoFrame frame){
        if(videoInbox==null||lastQueueFailure==1){inputTimeouts++;if(videoInbox!=null)videoCodecTimeoutRecoveryEvents++;}
        else if(lastQueueFailure==2)videoWorkerStale++;
        loseVideoChain(frame,lastQueueFailure==1?"codec_input_timeout":lastQueueFailure==2?"stale_codec_epoch":"codec_stopped");activity.presentationMetrics.discarded(frame.ptsUs);
    }

    private long lastInputNs;
    private int lastQueueFailure; // Input-owner only: 1 timeout, 2 stale epoch, 3 stopping.
    private boolean queue(MainActivity a,int gen,VideoFrame frame,int offset,int bytes,boolean config)throws Exception{
        MediaCodec decoder=a.video;lastQueueFailure=0;
        MediaPresentationMetrics.StageDiagnostics stages=a.presentationMetrics==null?null:a.presentationMetrics.decoderStages;
        // A retains its original per-call wait; B bounds queue plus codec-input
        // delay from the original complete-frame arrival, not worker start.
        long deadline=(videoInbox==null?System.nanoTime():frame.receivedNs)+80_000_000L;int index=-1,polls=0;
        long reserveStartedNs=stages==null?0:System.nanoTime();
        if(stages!=null){
            // Diagnostic snapshots are observational, not an atomic admission guard.
            // In particular an expired epoch may retain the existing timeout classification.
            long currentEpoch=videoInbox==null?frame.epoch:videoInbox.currentEpoch();
            boolean stopping=videoWorkerStop||!a.running||a.generation!=gen||a.video!=decoder;
            stages.reserveGuard(reserveStartedNs,frame.ptsUs,
                reserveObservedFlags(reserveStartedNs,deadline,frame.epoch,currentEpoch,stopping,config),frame.epoch,currentEpoch);
        }
        try{
            while(!videoWorkerStop&&a.running&&a.generation==gen&&a.video==decoder&&System.nanoTime()<deadline){
                if(videoInbox!=null&&!videoInbox.valid(frame)){lastQueueFailure=2;return false;}
                polls++;index=decoder.dequeueInputBuffer(videoInbox==null?10000:1000);if(index>=0)break;
            }
            if(index<0){lastQueueFailure=videoWorkerStop||!a.running||a.generation!=gen||a.video!=decoder?3:1;return false;}
        }finally{
            if(stages!=null){long acquiredNs=System.nanoTime();stages.reserve(acquiredNs,frame.ptsUs,reserveStartedNs,acquiredNs,
                polls,config,lastQueueFailure,-1,-1,frame.epoch);}
        }
        boolean copySuccess=false;
        if(stages!=null)stages.inputCopyStarted(System.nanoTime(),frame.ptsUs,config);
        try{
            ByteBuffer input=decoder.getInputBuffer(index);if(input==null||input.capacity()<bytes)throw new IOException("codec_input_capacity");
            if(videoInbox!=null&&(!videoInbox.valid(frame)||videoWorkerStop)){
                // MediaCodec has no input-buffer release API. Return the
                // reservation using empty non-EOS data, never a stale P AU.
                // This cancellation call is outside successful media queue-call samples.
                decoder.queueInputBuffer(index,0,0,frame.ptsUs,0);videoInputReservationsCancelled++;
                lastQueueFailure=videoWorkerStop?3:2;return false;
            }
            // Never hold the inbox lock across a codec call. If RX overflows
            // just after this check, this sole in-flight AU precedes every
            // cleared queued AU and is still safe to submit. success() checks
            // its epoch, so an old IDR cannot reopen the new broken chain.
            input.clear();input.put(frame.body,offset,bytes);copySuccess=true;
        }finally{if(stages!=null)stages.inputCopyFinished(System.nanoTime(),frame.ptsUs,config,copySuccess);}
        lastInputNs=System.nanoTime();
        if(!config)a.presentationMetrics.inputQueued(frame.ptsUs,lastInputNs);
        boolean callSuccess=false;
        if(stages!=null)stages.inputCallStarted(System.nanoTime(),frame.ptsUs,config);
        try{decoder.queueInputBuffer(index,0,bytes,frame.ptsUs,config?MediaCodec.BUFFER_FLAG_CODEC_CONFIG:0);callSuccess=true;}
        finally{if(stages!=null)stages.inputCallFinished(System.nanoTime(),frame.ptsUs,config,callSuccess);}
        a.receivedVideoBytes.addAndGet(bytes);if(config)configRecords++;else queuedMedia++;return true;
    }

    /** Independent observations; these bits never change queue/recovery policy. */
    static int reserveObservedFlags(long observedNs,long deadlineNs,long epoch,long currentEpoch,boolean stopping,boolean config){
        return (observedNs>=deadlineNs?1:0)|(epoch!=currentEpoch?2:0)|(stopping?4:0)|(config?8:0);
    }

    private static boolean annexB(byte[] bytes,int start,int length){
        return length>=5&&start>=0&&start+length<=bytes.length&&bytes[start]==0&&bytes[start+1]==0
            &&(bytes[start+2]==1||bytes[start+2]==0&&bytes[start+3]==1);
    }
    private static void send(DatagramSocket socket,Session session,byte[] data)throws Exception{
        socket.send(new DatagramPacket(data,data.length,session.peer,session.peerPort));
    }
    /** All feedback/touch senders share one directional AES-GCM nonce counter. */
    private synchronized void sendPayload(DatagramSocket socket,Session session,UdpVideoSecurity security,byte[] payload)throws Exception{
        if(clientSequence==Long.MAX_VALUE)throw new IOException("client_sequence_exhausted");
        send(socket,session,security.seal(payload,clientSequence++,UdpVideoSecurity.CLIENT_NONCE_PREFIX));
    }
    private static JSONObject nativeStats(long handle)throws Exception{
        long[] values=NativeUdpFec.nativeStats(handle);if(values.length!=NativeUdpFec.STAT_NAMES.length)throw new IOException("native_stats_contract");
        return nativeStats(values);
    }
    private static JSONObject nativeStats(long[] values)throws Exception{
        JSONObject result=new JSONObject();for(int index=0;index<values.length;index++)result.put(NativeUdpFec.STAT_NAMES[index],values[index]);return result;
    }
    private void drainNativeEvents(long handle)throws Exception{
        // At most 128 records per RX tick: no all-stats JSON object per packet.
        for(int batch=0;batch<2;batch++){
            long[][] rows=NativeUdpFec.nativeDrainEvents(handle);if(rows==null||rows.length>64)throw new IOException("native_events_contract");
            for(long[] row:rows){
                if(row==null||row.length!=NativeUdpFec.EVENT_NAMES.length||row[0]<1||row[0]>=NativeUdpFec.EVENT_TYPES.length
                        ||row[11]<0||row[11]>=NativeUdpFec.EVENT_REASONS.length)throw new IOException("native_event_row_contract");
                JSONObject e=new JSONObject().put("event",NativeUdpFec.EVENT_TYPES[(int)row[0]])
                    .put("reason",NativeUdpFec.EVENT_REASONS[(int)row[11]]).put("pts_available",row[0]==2);
                for(int index=1;index<row.length;index++)e.put(NativeUdpFec.EVENT_NAMES[index],row[index]);
                if(nativeFrameEvents.size()>=MAX_NATIVE_EVENTS){nativeFrameEvents.removeFirst();nativeFrameEventsEvicted++;}
                nativeFrameEvents.addLast(e);
            }
            if(rows.length<64)break;
        }
    }
    private static JSONObject nativeEventStats(long handle)throws Exception{
        long[] values=NativeUdpFec.nativeEventStats(handle);if(values==null||values.length!=4)throw new IOException("native_event_stats_contract");
        return new JSONObject().put("enabled",values[0]!=0).put("pending",values[1]).put("native_events_evicted",values[2]).put("generated",values[3]);
    }
    /** HGUF v1: exact 112B, BE magic/u32 version/13 nonnegative u64 values. */
    static byte[] networkFeedbackPayload(long[] values)throws IOException{
        if(values==null||values.length!=13)throw new IOException("network_feedback_fields");
        for(long value:values)if(value<0)throw new IOException("network_feedback_negative");
        if(values[0]==0||values[1]==0||values[2]<values[1])throw new IOException("network_feedback_interval");
        ByteBuffer out=ByteBuffer.allocate(112).order(ByteOrder.BIG_ENDIAN).putInt(0x48475546).putInt(1);
        for(long value:values)out.putLong(value);return out.array();
    }
    /** The host token is opaque and copied exactly; only the host measures RTT. */
    static byte[] pingReply(byte[] request,long arrivalUs,long sendUs)throws IOException{
        if(request==null||request.length!=16||ByteBuffer.wrap(request).getInt()!=0x48475051
                ||arrivalUs<=0||sendUs<arrivalUs)throw new IOException("ping_contract");
        return ByteBuffer.allocate(32).order(ByteOrder.BIG_ENDIAN).putInt(0x48475052)
            .put(request,4,12).putLong(arrivalUs).putLong(sendUs).array();
    }
    /** Strict opt-in JSON numeric control; reject coercion and impossible hints. */
    static int parseContentHintFps(Object value)throws IOException{
        if(!(value instanceof Number))throw new IOException("content_hint_fps_numeric_integer_required");
        double numeric=((Number)value).doubleValue();
        if(Double.isNaN(numeric)||Double.isInfinite(numeric)||numeric<0||numeric>240||numeric!=Math.rint(numeric))
            throw new IOException("content_hint_fps_range_or_integer");
        return (int)numeric;
    }
    static int parseSurfaceSubmitLeadMs(Object value)throws IOException{
        if(!(value instanceof Number))throw new IOException("surface_submit_lead_ms_numeric_integer_required");
        double numeric=((Number)value).doubleValue();
        if(numeric!=0&&numeric!=8&&numeric!=16)throw new IOException("surface_submit_lead_ms_supported_values");
        return (int)numeric;
    }
    private static void writeReport(File file,JSONObject report,Bundle result)throws Exception{
        byte[] bytes=report.toString().getBytes(StandardCharsets.UTF_8);if(bytes.length>64*1024*1024)throw new IOException("report_bound");
        AtomicFile atomic=new AtomicFile(file);FileOutputStream output=null;
        try{output=atomic.startWrite();output.write(bytes);atomic.finishWrite(output);output=null;}
        finally{if(output!=null)atomic.failWrite(output);}
        result.putString("report_file",REPORT);result.putLong("report_bytes",bytes.length);
    }

    private static Session readSession(File file)throws Exception{
        StructStat stat=Os.lstat(file.getAbsolutePath());
        if(!OsConstants.S_ISREG(stat.st_mode)||(stat.st_mode&0777)!=0600||stat.st_uid!=android.os.Process.myUid()
                ||stat.st_size<1||stat.st_size>4096)throw new IOException("session_file_permissions_or_bound");
        byte[] bytes=new byte[(int)stat.st_size];
        try(DataInputStream input=new DataInputStream(new FileInputStream(file))){input.readFully(bytes);if(input.read()!=-1)throw new IOException("session_file_changed");}
        JSONObject json=new JSONObject(new String(bytes,StandardCharsets.UTF_8));Arrays.fill(bytes,(byte)0);
        return parseSession(json,false);
    }
    private static Session parseSession(JSONObject json,boolean appMode)throws Exception{
        String encoded=json.getString("key_b64"),tag=json.getString("session_tag_hex"),host=json.getString("peer_host");
        if(!encoded.matches("[A-Za-z0-9+/]{43}=")||!tag.matches("[0-9a-fA-F]{16}"))throw new IOException("session_key_encoding");
        Session result=new Session();result.key=Base64.getDecoder().decode(encoded);
        if(result.key.length!=32)throw new IOException("session_key_length");result.tag=Long.parseUnsignedLong(tag,16);
        String[] octets=host.split("\\.",-1);if(octets.length!=4)throw new IOException("literal_peer_required");byte[] address=new byte[4];
        for(int index=0;index<4;index++){
            if(!octets[index].matches("0|[1-9][0-9]{0,2}"))throw new IOException("literal_peer_required");
            int value=Integer.parseInt(octets[index]);if(value>255)throw new IOException("literal_peer_required");address[index]=(byte)value;
        }
        result.peer=InetAddress.getByAddress(address);if(result.peer.isAnyLocalAddress()||result.peer.isMulticastAddress())throw new IOException("unicast_peer_required");
        result.peerPort=json.getInt("peer_port");result.bindPort=json.getInt("bind_port");result.seconds=json.getInt("seconds");
        result.fps=json.getInt("fps");result.buffer=json.getInt("buffer_ms");result.release=json.getString("video_release");result.profile=json.optString("profile","unspecified");
        result.displayHz=json.has("display_hz")?json.getInt("display_hz"):0;
        result.contentHintFps=json.has("content_hint_fps")?parseContentHintFps(json.get("content_hint_fps")):-1;
        result.surfaceSubmitLeadMs=json.has("surface_submit_lead_ms")?parseSurfaceSubmitLeadMs(json.get("surface_submit_lead_ms")):0;
        if(json.has("display_hz")&&json.getDouble("display_hz")!=result.displayHz)throw new IOException("display_hz_integer_required");
        result.audioEnabled=json.optBoolean("audio_enabled",false);result.touchEnabled=json.optBoolean("touch_enabled",false);
        result.asyncVideo=json.optBoolean("async_video",false);result.decoderReanchorEnabled=json.optBoolean("decoder_reanchor_enabled",true);
        for(String option:new String[]{"diagnostic_events","network_feedback"})if(json.has(option)&&!(json.get(option) instanceof Boolean))throw new IOException("feedback_option_boolean_required");
        result.diagnosticEvents=json.optBoolean("diagnostic_events",false);result.networkFeedback=json.optBoolean("network_feedback",false);
        // App NPS profiles are exact node/scope/public UDP tuples. Standalone
        // component probes retain their separately frozen 15961/15960 contract.
        if(appMode)LanUdpContract.validateAppMediaPeer(host,result.peerPort,json.getString("network_scope"),json.optString("node",""));
        if(!appMode&&result.peerPort!=15961||result.bindPort!=(appMode?0:15960)||result.seconds<1||result.seconds>120||result.fps!=60&&result.fps!=120
                ||result.buffer<30||result.buffer>100||!result.release.equals("scheduled")&&!result.release.equals("immediate")||result.profile.length()>160
                ||result.displayHz!=0&&result.displayHz!=60&&result.displayHz!=90&&result.displayHz!=120)
            throw new IOException("session_options_invalid");
        if(result.surfaceSubmitLeadMs>0&&!result.release.equals("scheduled"))throw new IOException("surface_submit_requires_scheduled_release");
        return result;
    }
    static JSONObject stageSummary(MediaPresentationMetrics.StageSnapshot s)throws Exception{
        long eventMinNs=0,eventMaxNs=0;
        if(s.events[0].length>0){eventMinNs=Long.MAX_VALUE;eventMaxNs=Long.MIN_VALUE;
            for(long timeNs:s.events[0]){eventMinNs=Math.min(eventMinNs,timeNs);eventMaxNs=Math.max(eventMaxNs,timeNs);}}
        JSONObject out=new JSONObject().put("schema_version",1).put("clock_domain_code",1)
            .put("phone_system_nano_time",1).put("sf_time_domain_verified",0).put("clock_reanchor_coverage",(s.coverageMask&MediaPresentationMetrics.StageDiagnostics.CLOCK)!=0?1:0)
            .put("origin_present",s.originNs==MediaPresentationMetrics.MISSING?0:1)
            .put("clock_origin_ns",s.originNs==MediaPresentationMetrics.MISSING?0:s.originNs)
            .put("fixed_initial_band_ns",MediaPresentationMetrics.StageDiagnostics.WARMUP_NS)
            .put("fixed_initial_band_is_stability_measurement",0).put("snapshot_ns",s.snapshotNs)
            .put("first_observation_ns",s.firstNs==MediaPresentationMetrics.MISSING?0:s.firstNs)
            .put("last_observation_ns",s.lastNs==MediaPresentationMetrics.MISSING?0:s.lastNs)
            .put("observations",s.observations).put("before_origin_observations",s.beforeOrigin)
            .put("after_segment_capacity_observations",s.afterCapacity)
            .put("segment_width_ns",MediaPresentationMetrics.StageDiagnostics.WINDOW_NS)
            .put("segment_capacity",MediaPresentationMetrics.StageDiagnostics.SEGMENTS)
            .put("segments_retained",s.segments[0].length).put("event_capacity",MediaPresentationMetrics.StageDiagnostics.EVENTS)
            .put("events_observed",s.eventObserved).put("events_retained",s.events[0].length).put("events_evicted",s.eventsEvicted)
            .put("event_window_start_ns",eventMinNs).put("event_window_end_ns",eventMaxNs)
            .put("event_retention_chronological",0)
            .put("slow_input_wait_threshold_ns",20_000_000L)
            .put("gap_event_threshold_ns",MediaPresentationMetrics.StageDiagnostics.GAP_EVENT_NS).put("operation_sampled_depth",1)
            .put("input_queue_call_start_not_completion",1)
            .put("codec_reserve_duration_covers_reservation_loop",1).put("reserve_event_depth_unknown",1)
            .put("coverage_mask",s.coverageMask).put("reserve_guard_bits_age_stale_stop_config",15)
            .put("reserve_guard_snapshot_is_atomic_admission",0)
            .put("consumer_cpu_clock_thread_cpu_time_nanos",1).put("consumer_non_cpu_time_is_scheduler_diagnosis",0)
            .put("copy_span_includes_get_buffer_capacity_guard_and_copy",1).put("cancellation_queue_calls_in_input_call_span",0)
            .put("fec_observation_period_ns",100_000_000L).put("fec_observations_are_exception_occurrence_times",0)
            .put("fec_frame_identity_known",0).put("final_fec_observation_after_worker_cleanup_attempt",1)
            .put("independent_surface_presentation_measured",0);
        String[] totalNames={"offered_frames","depth_observations","overflow_events","cleared_frames","worker_expired",
            "codec_reserve_calls","codec_reserve_polls","config_reserve_calls","media_reserve_calls","codec_input_timeouts",
            "scheduled_outputs","ready_minus_input_missing","ready_minus_input_invalid","waiting_idr_drops"};
        JSONObject totals=new JSONObject();for(int i=0;i<totalNames.length;i++)totals.put(totalNames[i],s.totals[i]);
        for(int i=0;i<s.extraTotals.length;i++)totals.put(MediaPresentationMetrics.StageDiagnostics.EXTRA_TOTAL_NAMES[i],s.extraTotals[i]);
        JSONObject phaseState=new JSONObject();
        for(int i=0;i<s.phaseState.length;i++)phaseState.put(MediaPresentationMetrics.StageDiagnostics.PHASE_STATE_NAMES[i],s.phaseState[i]);
        out.put("totals",totals).put("phase_state",phaseState).put("depth_counts_0_through_4",numericArray(s.depthCounts));
        JSONObject segments=new JSONObject(),events=new JSONObject(),histograms=new JSONObject();
        for(int i=0;i<s.segments.length;i++)segments.put(MediaPresentationMetrics.StageDiagnostics.SEGMENT_NAMES[i],numericArray(s.segments[i]));
        for(int i=0;i<s.events.length;i++)events.put(MediaPresentationMetrics.StageDiagnostics.EVENT_NAMES[i],numericArray(s.events[i]));
        String[] histogramNames={"target_minus_release","ready_minus_input","release_minus_ready","codec_reserve_wait","input_minus_receive"};
        out.put("histogram_upper_bounds_ns",numericArray(MediaPresentationMetrics.StageDiagnostics.Histogram.UPPER_NS))
            .put("histogram_bounds_inclusive",1).put("histogram_last_bucket_unbounded",1);
        for(int i=0;i<s.histograms.length;i++){MediaPresentationMetrics.HistogramSnapshot h=s.histograms[i];
            histograms.put(histogramNames[i],new JSONObject().put("counts",numericArray(h.counts)).put("valid",h.valid)
                .put("invalid",h.invalid).put("sum_ns",h.sum).put("min_ns",h.min).put("max_ns",h.max));}
        for(int i=0;i<s.extraHistograms.length;i++){MediaPresentationMetrics.HistogramSnapshot h=s.extraHistograms[i];
            histograms.put(MediaPresentationMetrics.StageDiagnostics.EXTRA_HISTOGRAM_NAMES[i],new JSONObject()
                .put("counts",numericArray(h.counts)).put("valid",h.valid).put("invalid",h.invalid)
                .put("sum_ns",h.sum).put("min_ns",h.min).put("max_ns",h.max));}
        return out.put("segments",segments).put("events",events).put("histograms",histograms);
    }
    private static JSONArray numericArray(long[] values){JSONArray array=new JSONArray();for(long value:values)array.put(value);return array;}

    static JSONObject mappingDetailsSummary(long[] values,int status,boolean enableAttempted,long reads,long failures,
            long elapsedTotalNs,long elapsedMaxNs,long lastReadNs)throws Exception {
        JSONObject out=new JSONObject().put("contract_schema_version",NativeUdpFec.MAPPING_DETAIL_SCHEMA)
            .put("status_code",status).put("available",status==NativeUdpFec.MAPPING_DETAIL_VALID?1:0)
            .put("requested_enabled",1).put("enable_attempted",enableAttempted?1:0)
            .put("jni_values_expected",NativeUdpFec.MAPPING_DETAIL_LENGTH)
            .put("read_attempts",reads).put("read_failures",failures).put("jni_and_validation_total_ns",elapsedTotalNs)
            .put("jni_and_validation_max_ns",elapsedMaxNs).put("last_read_phone_system_nano_time_ns",lastReadNs)
            .put("read_period_ns",100_000_000L).put("read_period_is_guaranteed",0)
            .put("includes_cold_and_final_read",1).put("final_read_after_worker_cleanup_attempt",1)
            .put("arrival_clock_phone_rx_system_nano_time_div_1000",1).put("arrival_unit_us",1)
            .put("arrival_is_java_poll_time",0).put("snapshot_is_java_poll_time",0)
            .put("event_retention_chronological",1).put("snapshot_non_destructive",1)
            .put("coverage_mask_declares_hook_support_only",1).put("event_ring_covers_all_rejections",0)
            .put("reason_1_old_frame",1).put("reason_2_mapping_capacity",2).put("reason_3_header_mismatch",3)
            .put("reason_4_new_mapping_after_capacity_reject",4).put("reason_4_extends_assembly_grant",0)
            .put("frame_id_int64_saturation_is_unique",0).put("physical_latency_measured",0);
        if(status!=NativeUdpFec.MAPPING_DETAIL_VALID)return out;
        NativeUdpFec.validateMappingDetails(values);
        for(int i=0;i<NativeUdpFec.MAPPING_DETAIL_HEADER_NAMES.length;i++)out.put(NativeUdpFec.MAPPING_DETAIL_HEADER_NAMES[i],values[i]);
        JSONObject events=new JSONObject();int retained=(int)values[18];
        for(int col=0;col<NativeUdpFec.MAPPING_DETAIL_COLUMNS;col++){
            JSONArray column=new JSONArray();for(int row=0;row<retained;row++)column.put(values[NativeUdpFec.MAPPING_DETAIL_HEADER
                +row*NativeUdpFec.MAPPING_DETAIL_COLUMNS+col]);
            events.put(NativeUdpFec.MAPPING_DETAIL_EVENT_NAMES[col],column);
        }
        return out.put("events",events);
    }

    static JSONObject numericAppSummary(JSONObject report)throws Exception{
        JSONObject out=new JSONObject();
        String[] scalars={"start_ns","first_server_packet_ns","receive_end_ns","observation_end_ns","fps_limit","buffer_ms",
            "udp_packets","udp_payload_bytes","foreign_peer_packets","authentication_errors","replay_errors","received_media_frames",
            "queued_media_frames","source_width","source_height","decoder_input_timeouts","late_discarded_count","codec_callback_count","receive_loop_max_ms",
            "receive_processing_max_ms","receive_socket_wait_max_ms","video_worker_expired_frames","video_worker_stale_epoch_drops","audio_cleanup_confirmed",
            "surface_submit_lead_ms","surface_submit_applications","surface_submit_wait_count","surface_submit_wait_total_ms",
            "surface_submit_max_output_hold_ms","surface_submit_max_park_ms","surface_submit_budget_fallbacks","stage_diagnostics_enabled"};
        for(String key:scalars)if(report.opt(key) instanceof Number)out.put(key,report.get(key));
        String status=report.optString("surface_submit_status","");
        out.put("surface_submit_status_code",status.equals("disabled_existing_release_path")?0:status.equals("applied_bounded_wait")?1
            :status.equals("enabled_no_wait_observed")?2:-1);
        out.put("hardware_video",report.optBoolean("hardware",false)?1:0);
        out.put("codec_startup_ready_enabled",report.optBoolean("codec_startup_ready_enabled",false)?1:0);
        for(String key:new String[]{"nps_physical_network_binding","native_fec","native_mapping_details","udp_audio","udp_touch","video_input_queue","codec_startup_gate","codec_timestamp_validity","display_mode_start","display_mode_end","decoder_stage_metrics"}){
            Object value=report.opt(key);if(value instanceof JSONObject)out.put(key,numericTree((JSONObject)value));
        }
        if(out.toString().getBytes(StandardCharsets.UTF_8).length>64*1024)throw new IOException("numeric_app_report_limit");
        return out;
    }
    private static JSONObject numericTree(JSONObject input)throws Exception{
        JSONObject out=new JSONObject();java.util.Iterator<String> keys=input.keys();
        while(keys.hasNext()){
            String key=keys.next();Object value=input.get(key);
            if(value instanceof Number)out.put(key,value);
            else if(value instanceof Boolean)out.put(key,(Boolean)value?1:0);
            else if(value instanceof JSONObject)out.put(key,numericTree((JSONObject)value));
            else if(value instanceof JSONArray){JSONArray numeric=new JSONArray(),items=(JSONArray)value;
                for(int i=0;i<items.length()&&i<128;i++)if(items.get(i) instanceof Number)numeric.put(items.get(i));
                if(numeric.length()>0)out.put(key,numeric);
            }
        }return out;
    }

    private static final class Session{
        byte[] key;long tag;InetAddress peer;int peerPort,bindPort,seconds,fps,buffer,displayHz,contentHintFps,surfaceSubmitLeadMs;String release,profile;
        boolean audioEnabled,touchEnabled,asyncVideo,decoderReanchorEnabled,diagnosticEvents,networkFeedback,boundedPcmQueueEnabled;
    }
}
