package local.remoteandroid.direct;

import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioTimestamp;
import android.media.AudioTrack;
import android.media.MediaCodec;
import android.media.MediaFormat;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.util.Arrays;
import java.util.List;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;
import org.json.JSONArray;
import org.json.JSONObject;

/** Experimental UDP AAC receiver. No audio payload/logging is persisted.
 * UDP receive performs bounded parsing/queue admission only. Decoder and PCM
 * scheduling are separate workers sharing the VIDEO PlaybackClock mapping.
 */
final class UdpAudioReceiver implements AutoCloseable,UdpVideoProbe.AudioCleanup {
    private final MainActivity activity;private final int generation;
    private final UdpAudioAssembler assembler=new UdpAudioAssembler();
    private final ArrayBlockingQueue<UdpAudioAssembler.Frame> queue=new ArrayBlockingQueue<>(8);
    private final Thread worker;private volatile Thread drain,pcmWorker;
    private final boolean boundedPcmQueueEnabled;
    private volatile BoundedPcmQueue pcmHandoff;
    private final PcmQueueTotals pcmQueueTotals=new PcmQueueTotals();
    private final Object pcmLifecycleLock=new Object();
    private volatile boolean pcmCleanupIncomplete;
    private volatile boolean closeFinished,cleanupReleaseFailed;
    private MediaCodec retiringDecoder;private AudioTrack retiringTrack;
    private volatile boolean closed;private volatile MediaCodec decoder;private volatile AudioTrack output;
    private volatile String failure="",decoderName="";private volatile boolean configured;
    private byte[] config;private long lastInputPts=-1;private volatile long lastPlaybackFrames;
    private final AtomicLong queueDrops=new AtomicLong(),beforeConfig=new AtomicLong(),
        invalidConfigs=new AtomicLong(),configurations=new AtomicLong(),inputFrames=new AtomicLong(),pcmBytes=new AtomicLong(),
        timestampObservations=new AtomicLong(),validTimestamps=new AtomicLong();
    private final AudioDiagnostics diagnostics=new AudioDiagnostics();

    /** Fixed-memory numeric diagnostics only: no samples, PCM, or packet content.
     * The legacy drop totals and split reasons come from one synchronized copy.
     * Deadline reads used below never replace the original scheduling target.
     */
    static final class AudioDiagnostics {
        static final int ARRIVAL_AGE_EXPIRED=0,NONMONOTONIC_PTS=1,CODEC_INPUT_UNAVAILABLE=2;
        static final int PCM_TARGET_LATE=0,PCM_WRITE_TIMEOUT=1;
        final long[] workerReasons=new long[3],pcmReasons=new long[2];
        final NumericTiming firstArrivalAge=new NumericTiming(),completeToWorker=new NumericTiming(),
            inputWait=new NumericTiming(),inputPtsStep=new NumericTiming(),outputHold=new NumericTiming(),
            targetLead=new NumericTiming(),targetMinusTail=new NumericTiming(),freshTargetDelta=new NumericTiming(),
            schedulingWait=new NumericTiming(),sleepRequested=new NumericTiming(),sleepObserved=new NumericTiming(),
            sleepOvershoot=new NumericTiming(),pcmWrite=new NumericTiming(),pcmQueueResidence=new NumericTiming(),pcmTargetLeadAtConsume=new NumericTiming();
        private long tailTimestampUses,tailFallbackUses;
        synchronized void workerStart(long arrivalAgeNs,long completeAgeNs){firstArrivalAge.add(arrivalAgeNs);completeToWorker.add(completeAgeNs);}
        synchronized void workerLate(int reason){if(reason<0||reason>=workerReasons.length)throw new IllegalArgumentException("worker reason");workerReasons[reason]++;}
        synchronized void pcmLate(int reason){if(reason<0||reason>=pcmReasons.length)throw new IllegalArgumentException("PCM reason");pcmReasons[reason]++;}
        synchronized long[] lateCounts(){
            return new long[]{workerReasons[0]+workerReasons[1]+workerReasons[2],workerReasons[0],workerReasons[1],workerReasons[2],
                pcmReasons[0]+pcmReasons[1],pcmReasons[0],pcmReasons[1]};
        }
        synchronized void input(long waitedNs,long ptsStepNs){inputWait.add(waitedNs);if(ptsStepNs>=0)inputPtsStep.add(ptsStepNs);}
        synchronized void output(long heldNs){outputHold.add(heldNs);}
        synchronized void target(long leadNs){targetLead.add(leadNs);}
        synchronized void tail(long waitNs,long freshDeltaNs,boolean fallback){
            targetMinusTail.add(waitNs);freshTargetDelta.add(freshDeltaNs);
            if(fallback)tailFallbackUses++;else tailTimestampUses++;
        }
        synchronized void waited(long elapsedNs){schedulingWait.add(elapsedNs);}
        synchronized void slept(long requestedNs,long observedNs){
            sleepRequested.add(requestedNs);sleepObserved.add(observedNs);sleepOvershoot.add(Math.max(0,observedNs-requestedNs));
        }
        synchronized void wrote(long elapsedNs){pcmWrite.add(elapsedNs);}
        synchronized void queuedPcm(long elapsedNs,long originalTargetLeadNs){pcmQueueResidence.add(elapsedNs);pcmTargetLeadAtConsume.add(originalTargetLeadNs);}
        synchronized JSONObject timingSnapshot()throws Exception{
            return new JSONObject().put("scope","Phone-local numeric pipeline diagnostics; fixed histograms, no payload or retained frame events. Fresh deadline reads do not alter scheduling.")
                .put("worker_first_arrival_age",firstArrivalAge.json()).put("complete_to_worker_start",completeToWorker.json())
                .put("codec_input_wait",inputWait.json()).put("attempted_input_pts_step",inputPtsStep.json())
                .put("codec_output_dequeue_to_release",outputHold.json()).put("pcm_target_lead_at_output",targetLead.json())
                .put("original_target_minus_queue_tail",targetMinusTail.json()).put("fresh_shared_target_minus_original",freshTargetDelta.json())
                .put("output_scheduling_wait",schedulingWait.json()).put("sleep_requested",sleepRequested.json())
                .put("sleep_observed",sleepObserved.json()).put("sleep_overshoot",sleepOvershoot.json()).put("pcm_write",pcmWrite.json())
                .put("pcm_queue_residence",pcmQueueResidence.json())
                .put("queued_pcm_original_target_lead_at_consume",pcmTargetLeadAtConsume.json())
                .put("queue_tail_timestamp_uses",tailTimestampUses).put("queue_tail_fallback_uses",tailFallbackUses)
                .put("complete_to_worker_scope","Includes the existing 10ms audio reorder wait and worker queue; not pure queue residence.")
                .put("tail_sample_scope","One observation per output wait-loop iteration; repeated observations are not independent PCM frames.")
                .put("output_hold_scope","Dequeue return through release call; legacy includes scheduling/writes, bounded PCM mode does not. See codec_output_hold_scope. Not AAC decoder execution time.");
        }
    }
    /** Signed bins preserve early/late target differences instead of clamping.
     * Counts cover the whole run; constant storage does not evict early events.
     */
    static final class NumericTiming {
        static final long[] UPPER_NS={-80_000_000L,-20_000_000L,0,2_000_000L,5_000_000L,10_000_000L,
            20_000_000L,40_000_000L,80_000_000L,160_000_000L,320_000_000L,640_000_000L};
        final long[] bins=new long[UPPER_NS.length+1];
        private long count,minNs=Long.MAX_VALUE,maxNs=Long.MIN_VALUE;private double totalNs;
        void add(long ns){count++;totalNs+=ns;minNs=Math.min(minNs,ns);maxNs=Math.max(maxNs,ns);
            int bin=0;while(bin<UPPER_NS.length&&ns>UPPER_NS[bin])bin++;bins[bin]++;}
        long[] values(){return new long[]{count,count==0?0:minNs,count==0?0:maxNs};}
        double meanNs(){return count==0?0:totalNs/count;}
        JSONObject json()throws Exception{
            JSONArray bounds=new JSONArray(),counts=new JSONArray();for(long ns:UPPER_NS)bounds.put(ns/1e6);for(long n:bins)counts.put(n);
            return new JSONObject().put("count",count).put("min_ms",count==0?JSONObject.NULL:minNs/1e6)
                .put("max_ms",count==0?JSONObject.NULL:maxNs/1e6).put("mean_ms",count==0?JSONObject.NULL:meanNs()/1e6)
                .put("histogram_upper_inclusive_ms",bounds).put("histogram_counts",counts);
        }
    }

    /** Existing callers retain the original codec-held output scheduler. */
    UdpAudioReceiver(MainActivity activity,int generation){this(activity,generation,false);}
    UdpAudioReceiver(MainActivity activity,int generation,boolean boundedPcmQueueEnabled){
        this.activity=activity;this.generation=generation;
        this.boundedPcmQueueEnabled=boundedPcmQueueEnabled;
        worker=new Thread(this::decode,"udp-audio-input");worker.start();
    }
    private boolean active(){return !closed&&activity.running&&activity.generation==generation;}
    synchronized void accept(byte[] plaintext,long nowNs){
        if(closed)return;assembler.accept(plaintext,nowNs);admit(assembler.poll(nowNs));
    }
    synchronized void poll(long nowNs){if(!closed)admit(assembler.poll(nowNs));}
    private void admit(List<UdpAudioAssembler.Frame> frames){
        for(UdpAudioAssembler.Frame frame:frames){
            if(!queue.offer(frame)){
                // Configuration is needed to decode future data. Keep its
                // admission bounded; evict oldest queued media rather than wait.
                if(frame.config){queue.poll();queueDrops.incrementAndGet();if(!queue.offer(frame))queueDrops.incrementAndGet();}
                else queueDrops.incrementAndGet();
            }
        }
    }
    private void decode(){
        try{
            while(active()){
                UdpAudioAssembler.Frame frame=queue.poll(20,TimeUnit.MILLISECONDS);if(frame==null)continue;
                long workerStartedNs=System.nanoTime();diagnostics.workerStart(workerStartedNs-frame.firstArrivalNs,workerStartedNs-frame.completeNs);
                if(workerStartedNs-frame.firstArrivalNs>=UdpAudioAssembler.DEADLINE_NS){diagnostics.workerLate(AudioDiagnostics.ARRIVAL_AGE_EXPIRED);continue;}
                if(frame.config){
                    if(!UdpAudioAssembler.supportedConfig(frame.data)){invalidConfigs.incrementAndGet();continue;}
                    if(!Arrays.equals(config,frame.data))configure(frame.data);
                    continue;
                }
                MediaCodec current=decoder;if(current==null){beforeConfig.incrementAndGet();continue;}
                if(frame.ptsUs<=lastInputPts){diagnostics.workerLate(AudioDiagnostics.NONMONOTONIC_PTS);continue;}
                activity.playback.observeAudio(frame.ptsUs,frame.completeNs);
                long deadline=frame.firstArrivalNs+UdpAudioAssembler.DEADLINE_NS;int index=-1;
                long inputWaitStartedNs=System.nanoTime();
                while(active()&&decoder==current&&System.nanoTime()<deadline){index=current.dequeueInputBuffer(1000);if(index>=0)break;}
                diagnostics.input(System.nanoTime()-inputWaitStartedNs,lastInputPts<0?-1:(frame.ptsUs-lastInputPts)*1000L);
                if(index<0){diagnostics.workerLate(AudioDiagnostics.CODEC_INPUT_UNAVAILABLE);continue;}
                ByteBuffer input=current.getInputBuffer(index);
                if(input==null||input.capacity()<frame.data.length)throw new IOException("audio_input_capacity");
                input.clear();input.put(frame.data);current.queueInputBuffer(index,0,frame.data.length,frame.ptsUs,0);
                inputFrames.incrementAndGet();lastInputPts=frame.ptsUs;
            }
        }catch(InterruptedException stop){Thread.currentThread().interrupt();}
        catch(Throwable error){if(active())failure=error.getClass().getSimpleName();}
    }
    private void configure(byte[] asc)throws Exception{
        releaseCurrent();if(pcmCleanupIncomplete)throw new IOException("audio_cleanup_incomplete");if(!active())return;
        MediaFormat format=MediaFormat.createAudioFormat("audio/mp4a-latm",48000,2);
        format.setInteger(MediaFormat.KEY_PCM_ENCODING,AudioFormat.ENCODING_PCM_16BIT);
        format.setByteBuffer("csd-0",ByteBuffer.wrap(asc));
        MediaCodec next=MediaCodec.createDecoderByType("audio/mp4a-latm");
        AudioTrack nextOutput=null;
        boolean ownershipTransferred=false;
        try{
            next.configure(format,null,null,0);next.start();
            int min=AudioTrack.getMinBufferSize(48000,AudioFormat.CHANNEL_OUT_STEREO,AudioFormat.ENCODING_PCM_16BIT);
            if(min<=0)throw new IOException("audio_min_buffer");
            nextOutput=new AudioTrack.Builder().setAudioFormat(new AudioFormat.Builder().setSampleRate(48000)
                .setChannelMask(AudioFormat.CHANNEL_OUT_STEREO).setEncoding(AudioFormat.ENCODING_PCM_16BIT).build())
                .setAudioAttributes(new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA)
                .setContentType(AudioAttributes.CONTENT_TYPE_MOVIE).build()).setTransferMode(AudioTrack.MODE_STREAM)
                .setBufferSizeInBytes(Math.max(min,4096)).setPerformanceMode(AudioTrack.PERFORMANCE_MODE_LOW_LATENCY).build();
            if(nextOutput.getState()!=AudioTrack.STATE_INITIALIZED)throw new IOException("audio_track_not_initialized");
            nextOutput.setVolume(activity.soundEnabled?1f:0f);nextOutput.play();
            MediaCodec owned=next;AudioTrack track=nextOutput;
            if(boundedPcmQueueEnabled){
                synchronized(pcmLifecycleLock){
                    if(!active())throw new InterruptedException("audio closed before PCM handoff");
                    decoder=next;output=nextOutput;ownershipTransferred=true;
                    activity.audio=next;activity.track=nextOutput;
                    config=asc.clone();configured=true;lastInputPts=-1;decoderName=next.getName();configurations.incrementAndGet();
                    int epoch=activity.presentationMetrics.resetAudio();
                    BoundedPcmQueue handoff=new BoundedPcmQueue();
                    synchronized(pcmQueueTotals){pcmHandoff=handoff;}
                    pcmWorker=new Thread(()->playQueuedPcm(owned,track,epoch,handoff),"udp-audio-pcm");
                    drain=new Thread(()->copyDecodedPcm(owned,handoff),"udp-audio-output");
                    pcmWorker.start();drain.start();
                }
            }else{
                decoder=next;output=nextOutput;activity.audio=next;activity.track=nextOutput;
                config=asc.clone();configured=true;lastInputPts=-1;decoderName=next.getName();configurations.incrementAndGet();
                int epoch=activity.presentationMetrics.resetAudio();
                drain=new Thread(()->render(owned,track,epoch),"udp-audio-output");drain.start();
            }
        }catch(Throwable error){
            if(boundedPcmQueueEnabled&&ownershipTransferred){releaseQueuedCurrent();}
            else{
                if(nextOutput!=null){try{nextOutput.release();}catch(Exception ignored){}}
                try{next.stop();}catch(Exception ignored){}try{next.release();}catch(Exception ignored){}
            }
            throw error;
        }
    }
    private void render(MediaCodec current,AudioTrack track,int epoch){
        AudioTimestamp stamp=new AudioTimestamp();MediaCodec.BufferInfo info=new MediaCodec.BufferInfo();
        long writtenFrames=0;ByteBuffer amplified=ByteBuffer.allocateDirect(16384);
        try{
            while(active()&&decoder==current){
                int index=current.dequeueOutputBuffer(info,10000);if(index<0)continue;
                long outputAcquiredNs=System.nanoTime();
                try{
                    if(info.size==0)continue;
                    ByteBuffer pcm=current.getOutputBuffer(index);if(pcm==null||info.size%4!=0)throw new IOException("audio_pcm_alignment");
                    pcm.position(info.offset);pcm.limit(info.offset+info.size);
                    if(activity.audioGain!=1f){if(amplified.capacity()<pcm.remaining())amplified=ByteBuffer.allocateDirect(pcm.remaining());
                        PcmGain.process(pcm,amplified,activity.audioGain);pcm=amplified;}
                    long target=activity.playback.audioDeadline(info.presentationTimeUs);
                    diagnostics.target(target-System.nanoTime());
                    // A frame arriving too late must not increase playout delay
                    // forever. Dropping this PCM record is bounded and explicit.
                    if(System.nanoTime()-target>80_000_000L){diagnostics.pcmLate(AudioDiagnostics.PCM_TARGET_LATE);continue;}
                    long schedulingStartedNs=System.nanoTime();
                    while(active()&&decoder==current){
                        long now=System.nanoTime();boolean valid=track.getTimestamp(stamp);
                        timestampObservations.incrementAndGet();if(valid)validTimestamps.incrementAndGet();
                        activity.presentationMetrics.audioTimestamp(epoch,valid,stamp.framePosition,stamp.nanoTime,now,
                            track.getPlayState()==AudioTrack.PLAYSTATE_PLAYING);
                        MediaPresentationMetrics.AudioEstimate estimate=activity.presentationMetrics.audioEstimate(now);
                        long tail=valid&&estimate.valid&&track.getPlayState()==AudioTrack.PLAYSTATE_PLAYING
                            ?AudioSubmissionClock.timestampQueueTailNs(now,stamp.nanoTime,writtenFrames,estimate.timestampFramePosition,48000)
                            :AudioSubmissionClock.UNAVAILABLE;
                        boolean tailFallback=tail==AudioSubmissionClock.UNAVAILABLE;
                        if(tailFallback){long queued=Math.max(0,writtenFrames-(track.getPlaybackHeadPosition()&0xffffffffL));
                            tail=now+queued*1_000_000_000L/48000L+25_000_000L;}
                        long wait=target-tail;
                        diagnostics.tail(wait,activity.playback.audioDeadline(info.presentationTimeUs)-target,tailFallback);
                        if(wait<=2_000_000L)break;
                        long sleepMs=Math.min(10,Math.max(1,wait/1_000_000L)),sleepStartedNs=System.nanoTime();
                        Thread.sleep(sleepMs);diagnostics.slept(sleepMs*1_000_000L,System.nanoTime()-sleepStartedNs);
                    }
                    diagnostics.waited(System.nanoTime()-schedulingStartedNs);
                    long chunkFrames=0,writeDeadline=System.nanoTime()+80_000_000L;
                    long writeStartedNs=System.nanoTime();
                    while(pcm.hasRemaining()&&active()&&decoder==current){
                        int count=track.write(pcm,pcm.remaining(),AudioTrack.WRITE_NON_BLOCKING);
                        if(count<0||count%4!=0)throw new IOException("audio_write");
                        if(count==0){if(System.nanoTime()>writeDeadline){diagnostics.pcmLate(AudioDiagnostics.PCM_WRITE_TIMEOUT);break;}Thread.sleep(1);continue;}
                        long frames=count/4;
                        activity.presentationMetrics.audioWritten(epoch,writtenFrames,frames,info.presentationTimeUs+chunkFrames*1_000_000L/48000L);
                        writtenFrames+=frames;chunkFrames+=frames;pcmBytes.addAndGet(count);activity.audioOutputBytes.addAndGet(count);
                    }
                    diagnostics.wrote(System.nanoTime()-writeStartedNs);
                    lastPlaybackFrames=track.getPlaybackHeadPosition()&0xffffffffL;
                }finally{if(decoder==current)current.releaseOutputBuffer(index,false);diagnostics.output(System.nanoTime()-outputAcquiredNs);}
            }
        }catch(InterruptedException stop){Thread.currentThread().interrupt();}
        catch(Throwable error){if(active()&&decoder==current)failure=error.getClass().getSimpleName();}
    }

    /** B only: release codec output after a fixed-pool copy, before any sleep
     * or AudioTrack write. The original target is captured here, never rebased
     * by the consumer. Queue rejection is explicit rather than blocking drain.
     */
    private void copyDecodedPcm(MediaCodec current,BoundedPcmQueue handoff){
        MediaCodec.BufferInfo info=new MediaCodec.BufferInfo();
        try{
            while(active()&&decoder==current){
                int index=current.dequeueOutputBuffer(info,10000);if(index<0)continue;
                long outputAcquiredNs=System.nanoTime();
                try{
                    if(info.size==0)continue;
                    ByteBuffer pcm=current.getOutputBuffer(index);if(pcm==null||info.size%4!=0)throw new IOException("audio_pcm_alignment");
                    pcm.position(info.offset);pcm.limit(info.offset+info.size);
                    long target=activity.playback.audioDeadline(info.presentationTimeUs);
                    diagnostics.target(target-System.nanoTime());
                    if(System.nanoTime()-target>80_000_000L){diagnostics.pcmLate(AudioDiagnostics.PCM_TARGET_LATE);continue;}
                    handoff.offerCopy(pcm,info.presentationTimeUs,target,outputAcquiredNs,activity.audioGain,System.nanoTime());
                }finally{if(decoder==current)current.releaseOutputBuffer(index,false);diagnostics.output(System.nanoTime()-outputAcquiredNs);}
            }
        }catch(Throwable error){if(active()&&decoder==current)failure=error.getClass().getSimpleName();}
        finally{handoff.close();}
    }
    /** B's PCM worker deliberately preserves A's target/tail wait and 80ms
     * write timeout. Queue age is checked before consumption, independently.
     */
    private void playQueuedPcm(MediaCodec current,AudioTrack track,int epoch,BoundedPcmQueue handoff){
        AudioTimestamp stamp=new AudioTimestamp();long writtenFrames=0;
        ByteBuffer amplified=ByteBuffer.allocateDirect(BoundedPcmQueue.MAX_FRAME_BYTES);
        try{
            while(active()&&decoder==current){
                BoundedPcmQueue.Frame frame=handoff.awaitFrame(20_000_000L);if(frame==null){if(handoff.isClosed())break;continue;}
                try{
                    long consumeNs=System.nanoTime();diagnostics.queuedPcm(consumeNs-frame.dequeuedNs,frame.targetNs-consumeNs);
                    // Queue residence must not turn a record admitted 70ms
                    // late into playable PCM 90ms late. Keep the same original
                    // target and late threshold; do not move the shared clock.
                    if(consumeNs-frame.targetNs>80_000_000L){diagnostics.pcmLate(AudioDiagnostics.PCM_TARGET_LATE);continue;}
                    ByteBuffer pcm=frame.pcm();
                    if(frame.gain!=1f){PcmGain.process(pcm,amplified,frame.gain);pcm=amplified;}
                    long target=frame.targetNs;
                    long schedulingStartedNs=System.nanoTime();
                    while(active()&&decoder==current){
                        long now=System.nanoTime();boolean valid=track.getTimestamp(stamp);
                        timestampObservations.incrementAndGet();if(valid)validTimestamps.incrementAndGet();
                        activity.presentationMetrics.audioTimestamp(epoch,valid,stamp.framePosition,stamp.nanoTime,now,
                            track.getPlayState()==AudioTrack.PLAYSTATE_PLAYING);
                        MediaPresentationMetrics.AudioEstimate estimate=activity.presentationMetrics.audioEstimate(now);
                        long tail=valid&&estimate.valid&&track.getPlayState()==AudioTrack.PLAYSTATE_PLAYING
                            ?AudioSubmissionClock.timestampQueueTailNs(now,stamp.nanoTime,writtenFrames,estimate.timestampFramePosition,48000)
                            :AudioSubmissionClock.UNAVAILABLE;
                        boolean tailFallback=tail==AudioSubmissionClock.UNAVAILABLE;
                        if(tailFallback){long queued=Math.max(0,writtenFrames-(track.getPlaybackHeadPosition()&0xffffffffL));
                            tail=now+queued*1_000_000_000L/48000L+25_000_000L;}
                        long wait=target-tail;
                        diagnostics.tail(wait,activity.playback.audioDeadline(frame.ptsUs)-target,tailFallback);
                        if(wait<=2_000_000L)break;
                        long sleepMs=Math.min(10,Math.max(1,wait/1_000_000L)),sleepStartedNs=System.nanoTime();
                        Thread.sleep(sleepMs);diagnostics.slept(sleepMs*1_000_000L,System.nanoTime()-sleepStartedNs);
                    }
                    diagnostics.waited(System.nanoTime()-schedulingStartedNs);
                    long chunkFrames=0,writeDeadline=System.nanoTime()+80_000_000L;
                    long writeStartedNs=System.nanoTime();
                    while(pcm.hasRemaining()&&active()&&decoder==current){
                        int count=track.write(pcm,pcm.remaining(),AudioTrack.WRITE_NON_BLOCKING);
                        if(count<0||count%4!=0)throw new IOException("audio_write");
                        if(count==0){if(System.nanoTime()>writeDeadline){diagnostics.pcmLate(AudioDiagnostics.PCM_WRITE_TIMEOUT);break;}Thread.sleep(1);continue;}
                        long frames=count/4;
                        activity.presentationMetrics.audioWritten(epoch,writtenFrames,frames,frame.ptsUs+chunkFrames*1_000_000L/48000L);
                        writtenFrames+=frames;chunkFrames+=frames;pcmBytes.addAndGet(count);activity.audioOutputBytes.addAndGet(count);
                    }
                    diagnostics.wrote(System.nanoTime()-writeStartedNs);
                    lastPlaybackFrames=track.getPlaybackHeadPosition()&0xffffffffL;
                }finally{handoff.release(frame);}
            }
        }catch(InterruptedException stop){Thread.currentThread().interrupt();}
        catch(Throwable error){if(active()&&decoder==current)failure=error.getClass().getSimpleName();}
    }
    /** Fixed totals across codec reconfiguration; never retain PCM records. */
    private static final class PcmQueueTotals {
        long accepted,consumed,frameLimitDrops,byteLimitDrops,oversizeDrops,ageDrops,admissionAgeDrops,closedDrops,closingCleared;
        int maximumFrames,maximumBytes;
        void accumulate(BoundedPcmQueue.Snapshot s){accepted+=s.accepted;consumed+=s.consumed;frameLimitDrops+=s.frameLimitDrops;byteLimitDrops+=s.byteLimitDrops;
            oversizeDrops+=s.oversizeDrops;ageDrops+=s.ageDrops;admissionAgeDrops+=s.admissionAgeDrops;closedDrops+=s.closedDrops;closingCleared+=s.closingCleared;
            maximumFrames=Math.max(maximumFrames,s.maximumFrames);maximumBytes=Math.max(maximumBytes,s.maximumBytes);}
        JSONObject json(BoundedPcmQueue current)throws Exception{
            BoundedPcmQueue.Snapshot s=current==null?null:current.snapshot();
            return new JSONObject().put("capacity_frames",BoundedPcmQueue.MAX_QUEUED_FRAMES)
                .put("capacity_bytes",BoundedPcmQueue.MAX_QUEUED_BYTES).put("maximum_record_bytes",BoundedPcmQueue.MAX_FRAME_BYTES)
                .put("fixed_pool_frames",BoundedPcmQueue.POOL_FRAMES).put("fixed_pool_bytes",BoundedPcmQueue.POOL_FRAMES*BoundedPcmQueue.MAX_FRAME_BYTES)
                .put("maximum_queue_age_ms",BoundedPcmQueue.MAX_QUEUE_AGE_NS/1_000_000L)
                .put("pending_frames",s==null?0:s.pendingFrames).put("pending_bytes",s==null?0:s.pendingBytes)
                .put("consumer_owned_frames",s==null?0:s.consumingFrames)
                .put("maximum_depth",Math.max(maximumFrames,s==null?0:s.maximumFrames)).put("maximum_bytes",Math.max(maximumBytes,s==null?0:s.maximumBytes))
                .put("admitted_records",accepted+(s==null?0:s.accepted))
                .put("consumed_records",consumed+(s==null?0:s.consumed))
                .put("drop_reasons",new JSONObject().put("frame_limit",frameLimitDrops+(s==null?0:s.frameLimitDrops))
                    .put("byte_limit",byteLimitDrops+(s==null?0:s.byteLimitDrops)).put("oversize_or_invalid_alignment",oversizeDrops+(s==null?0:s.oversizeDrops))
                    .put("queue_age_expired",ageDrops+(s==null?0:s.ageDrops)).put("already_old_or_invalid_clock_admission",admissionAgeDrops+(s==null?0:s.admissionAgeDrops))
                    .put("closed_admission",closedDrops+(s==null?0:s.closedDrops)))
                .put("closing_cleared_records",closingCleared+(s==null?0:s.closingCleared))
                .put("scope","Queued PCM age is measured since codec dequeue. Consumer scheduling still uses the original target and write timeout; it is not acoustic timing.");
        }
    }
    synchronized JSONObject snapshot()throws Exception{
        long[] late=diagnostics.lateCounts();
        JSONObject pcmQueue; synchronized(pcmQueueTotals){pcmQueue=pcmQueueTotals.json(pcmHandoff);}
        return new JSONObject().put("protocol","HGUA_AAC_over_authenticated_UDP").put("configuration_received",configured)
            .put("bounded_pcm_queue_enabled",boundedPcmQueueEnabled).put("pcm_queue",pcmQueue).put("pcm_cleanup_incomplete",pcmCleanupIncomplete)
            .put("codec_output_hold_scope",boundedPcmQueueEnabled?"Dequeue through fixed-pool copy and codec release; excludes PCM scheduling and AudioTrack writes.":"Dequeue through scheduling, AudioTrack writes and codec release.")
            .put("codec",decoderName).put("decoder_configurations",configurations.get()).put("accepted_fragments",assembler.acceptedFragments)
            .put("malformed",assembler.malformed).put("duplicates",assembler.duplicate).put("assembly_expired",assembler.expired)
            .put("assembly_evicted",assembler.evicted).put("complete_records",assembler.completed).put("reorder_drops",assembler.reorderDrops)
            .put("pending_records",assembler.pendingCount()).put("pending_complete_records",assembler.readyCount())
            .put("worker_queue_drops",queueDrops.get()).put("worker_late_drops",late[0])
            .put("worker_late_drop_reasons",new JSONObject().put("arrival_age_expired",late[1]).put("nonmonotonic_pts",late[2]).put("codec_input_unavailable",late[3]))
            .put("media_before_configuration",beforeConfig.get())
            .put("unsupported_configurations",invalidConfigs.get()).put("decoder_input_records",inputFrames.get()).put("pcm_written_bytes",pcmBytes.get())
            .put("pcm_late_drops",late[4]).put("pcm_late_drop_reasons",new JSONObject().put("target_late",late[5]).put("write_timeout",late[6]))
            .put("diagnostic_timing",diagnostics.timingSnapshot())
            .put("timestamp_observations",timestampObservations.get()).put("valid_audio_timestamps",validTimestamps.get())
            .put("last_playback_head_frames",lastPlaybackFrames).put("failure_class",failure)
            .put("actual_acoustic_output_measured",false).put("actual_lip_sync_measured",false);
    }
    boolean hasDecodedAudio(){return pcmBytes.get()>0;}
    private void releaseCurrent(){
        if(boundedPcmQueueEnabled){releaseQueuedCurrent();return;}
        MediaCodec old=decoder;AudioTrack oldTrack=output;decoder=null;output=null;
        if(activity.audio==old)activity.audio=null;if(activity.track==oldTrack)activity.track=null;
        Thread previous=drain;if(previous!=null&&previous!=Thread.currentThread()){previous.interrupt();try{previous.join(300);}catch(InterruptedException ignored){}}
        if(oldTrack!=null){try{lastPlaybackFrames=oldTrack.getPlaybackHeadPosition()&0xffffffffL;}catch(Exception ignored){}
            try{oldTrack.pause();oldTrack.flush();oldTrack.stop();}catch(Exception ignored){}try{oldTrack.release();}catch(Exception ignored){cleanupReleaseFailed=true;}}
        if(old!=null){try{old.stop();}catch(Exception ignored){}try{old.release();}catch(Exception ignored){cleanupReleaseFailed=true;}}
    }
    /** Retire a whole B epoch before a new codec can publish. A failed bounded
     * join retains at most one old resource set and prohibits reconfiguration;
     * it never releases a buffer/track under a still-running writer.
     */
    private void releaseQueuedCurrent(){
        // Only configure()/its catch run on the input worker itself. External
        // close must wait until queueInputBuffer/configure can no longer touch
        // this epoch. Do not hold pcmLifecycleLock here: input configure may
        // need that lock to finish retiring or abort an unpublished resource.
        boolean inputStopped=Thread.currentThread()==worker||joinOwned(worker,500);
        synchronized(pcmLifecycleLock){
            if(retiringDecoder==null){retiringDecoder=decoder;retiringTrack=output;}
            MediaCodec old=retiringDecoder;AudioTrack oldTrack=retiringTrack;decoder=null;output=null;
            if(activity.audio==old)activity.audio=null;if(activity.track==oldTrack)activity.track=null;
            BoundedPcmQueue handoff=pcmHandoff;if(handoff!=null)handoff.close();
            Thread previousDrain=drain,previousPcm=pcmWorker;
            if(previousDrain!=null)previousDrain.interrupt();if(previousPcm!=null)previousPcm.interrupt();
            boolean drainStopped=joinOwned(previousDrain,500),pcmStopped=joinOwned(previousPcm,500);
            boolean stopped=inputStopped&&drainStopped&&pcmStopped;
            if(!stopped){pcmCleanupIncomplete=true;failure="audio_cleanup_incomplete";return;}
            pcmCleanupIncomplete=false;drain=null;pcmWorker=null;
            synchronized(pcmQueueTotals){
                if(handoff!=null){pcmQueueTotals.accumulate(handoff.snapshot());pcmHandoff=null;}
            }
            if(oldTrack!=null){try{lastPlaybackFrames=oldTrack.getPlaybackHeadPosition()&0xffffffffL;}catch(Exception ignored){}
                try{oldTrack.pause();oldTrack.flush();oldTrack.stop();}catch(Exception ignored){}try{oldTrack.release();}catch(Exception ignored){cleanupReleaseFailed=true;}}
            if(old!=null){try{old.stop();}catch(Exception ignored){}try{old.release();}catch(Exception ignored){cleanupReleaseFailed=true;}}
            retiringDecoder=null;retiringTrack=null;
        }
    }
    private static boolean joinOwned(Thread thread,long timeoutMs){
        if(thread==null)return true;if(thread==Thread.currentThread())return false;
        try{thread.join(timeoutMs);}catch(InterruptedException stop){Thread.currentThread().interrupt();}
        return !thread.isAlive();
    }
    /** Read only after close; release uncertainty and every owned live thread remain fail-closed. */
    public int cleanupState(){
        synchronized(pcmLifecycleLock){
            if(!closed||!closeFinished||cleanupReleaseFailed)return UdpVideoProbe.CompletionReceipt.AUDIO_UNKNOWN;
            if(pcmCleanupIncomplete||worker.isAlive()||drain!=null&&drain.isAlive()||pcmWorker!=null&&pcmWorker.isAlive()
                    ||decoder!=null||output!=null||retiringDecoder!=null||retiringTrack!=null||pcmHandoff!=null)
                return UdpVideoProbe.CompletionReceipt.AUDIO_INCOMPLETE;
            return UdpVideoProbe.CompletionReceipt.AUDIO_CONFIRMED;
        }
    }
    public void close(){closeFinished=false;closed=true;worker.interrupt();
        if(!boundedPcmQueueEnabled){try{worker.join(500);}catch(InterruptedException ignored){}}
        queue.clear();releaseCurrent();if(config!=null)Arrays.fill(config,(byte)0);closeFinished=true;}
}
