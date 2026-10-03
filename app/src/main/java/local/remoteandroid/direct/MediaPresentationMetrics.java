package local.remoteandroid.direct;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Records media pipeline observations without changing playback policy.
 * Codec callback timestamps are unverified vendor observations. AudioTrack
 * hardware timestamps are estimates from the
 * platform; they do not measure photons, acoustic output, or network one-way delay.
 * All wall times use System.nanoTime(), all source PTS values use microseconds.
 */
final class MediaPresentationMetrics {
    static final long MISSING = Long.MIN_VALUE;
    private static final int MAX_VIDEO_RECORDS = 8192;
    private static final int MAX_PENDING = 4096;
    private static final int MAX_AUDIO_SEGMENTS = 4096;
    private static final long MAX_TIMESTAMP_DISTANCE_NS = 500_000_000L;
    private final int sampleRate;
    private final LinkedHashMap<Long, Long> arrivals = new LinkedHashMap<>();
    private final LinkedHashMap<Long, Long> inputQueued = new LinkedHashMap<>();
    private final LinkedHashMap<Long, ArrayDeque<Scheduled>> pending = new LinkedHashMap<>();
    private final ArrayDeque<VideoSample> rendered = new ArrayDeque<>();
    private final ArrayDeque<AudioSegment> audioSegments = new ArrayDeque<>();
    private long audioWrittenFrames;
    private int audioEpoch;
    private int pendingFrames;
    private long audioFramePosition = MISSING, audioTimestampNs = MISSING, audioObservedNs = MISSING;
    private long previousRawPosition = MISSING, positionWrap;
    private boolean audioPlaying;
    private long videoRecordsEvicted, pendingEvicted, audioSegmentsEvicted, unmatchedRenders;
    // Allocated only by the independent UDP experiment; formal constructors retain the old path.
    final StageDiagnostics decoderStages;

    MediaPresentationMetrics() { this(48000); }
    MediaPresentationMetrics(int sampleRate) { this(sampleRate, false); }
    MediaPresentationMetrics(int sampleRate, boolean boundedDecoderDiagnostics) {
        if (sampleRate <= 0) throw new IllegalArgumentException("sampleRate");
        this.sampleRate = sampleRate;
        decoderStages = boundedDecoderDiagnostics ? new StageDiagnostics() : null;
    }

    synchronized void received(long ptsUs, long arrivalNs) {
        arrivals.put(ptsUs, arrivalNs);
        trim(arrivals, MAX_PENDING);
        if (decoderStages != null) decoderStages.establishOrigin(arrivalNs);
    }

    /** Call just before queueInputBuffer for a non-config video access unit. */
    synchronized void inputQueued(long ptsUs, long queuedNs) {
        inputQueued.put(ptsUs, queuedNs); trim(inputQueued, MAX_PENDING);
        if (decoderStages != null) {
            Long arrival=arrivals.get(ptsUs);
            if(arrival!=null)decoderStages.inputQueued(arrival,queuedNs);
        }
    }

    /** Call immediately before releaseOutputBuffer(index, targetNs). */
    synchronized void scheduled(long ptsUs, long decoderReadyNs, long targetNs, long releasedNs) {
        Long arrival = arrivals.remove(ptsUs);
        Long queued = inputQueued.remove(ptsUs);
        if (decoderStages != null) decoderStages.scheduled(ptsUs,
                queued == null ? MISSING : queued, decoderReadyNs, targetNs, releasedNs);
        ArrayDeque<Scheduled> frames = pending.get(ptsUs);
        if (frames == null) { frames = new ArrayDeque<>(); pending.put(ptsUs, frames); }
        frames.addLast(new Scheduled(arrival == null ? MISSING : arrival,
                queued == null ? MISSING : queued, decoderReadyNs, targetNs, releasedNs));
        pendingFrames++;
        while (pendingFrames > MAX_PENDING) {
            Iterator<Map.Entry<Long, ArrayDeque<Scheduled>>> it = pending.entrySet().iterator();
            int removed = it.next().getValue().size();
            pendingEvicted += removed; pendingFrames -= removed; it.remove();
        }
    }

    synchronized void discarded(long ptsUs) { arrivals.remove(ptsUs); inputQueued.remove(ptsUs); }

    /** nanoTime comes from OnFrameRenderedListener, callbackNs is when Java received it. */
    synchronized void rendered(long ptsUs, long nanoTime, long callbackNs) {
        ArrayDeque<Scheduled> frames = pending.get(ptsUs);
        Scheduled frame = frames == null ? null : frames.pollFirst();
        if (frame != null) pendingFrames--;
        if (frames != null && frames.isEmpty()) pending.remove(ptsUs);
        if (frame == null) unmatchedRenders++;
        boolean plausible = plausibleRenderTimestamp(nanoTime, callbackNs,
                frame == null ? MISSING : frame.releasedNs);
        boolean targetEcho = frame != null && nanoTime == frame.targetNs;
        // Some QTI codecs echo the requested future release timestamp instead
        // of an actual presentation observation. Do not map audio at that time.
        long audioPts = plausible && !targetEcho ? audioMediaPtsAt(nanoTime) : MISSING;
        rendered.addLast(new VideoSample(ptsUs, nanoTime, callbackNs, frame, audioPts));
        if (rendered.size() > MAX_VIDEO_RECORDS) { rendered.removeFirst(); videoRecordsEvicted++; }
    }

    /**
     * Record each successful PCM write. sourcePtsUs is the PTS of the first
     * sample in this write, including the offset of any partial write.
     */
    synchronized void audioWritten(long startFrame, long frames, long sourcePtsUs) {
        if (startFrame < 0 || frames < 0) throw new IllegalArgumentException("audio frames");
        if (frames == 0) return;
        if (startFrame != audioWrittenFrames) throw new IllegalArgumentException("non-contiguous AudioTrack write");
        audioSegments.addLast(new AudioSegment(startFrame, startFrame + frames, sourcePtsUs));
        audioWrittenFrames += frames;
        if (audioSegments.size() > MAX_AUDIO_SEGMENTS) { audioSegments.removeFirst(); audioSegmentsEvicted++; }
    }

    synchronized void audioWritten(int epoch, long startFrame, long frames, long sourcePtsUs) {
        if (epoch == audioEpoch) audioWritten(startFrame, frames, sourcePtsUs);
    }

    /** Start one new AudioTrack timeline without erasing captured video metrics. */
    synchronized int resetAudio() {
        audioEpoch++; audioSegments.clear(); audioWrittenFrames = 0;
        audioFramePosition = MISSING; audioTimestampNs = MISSING; audioObservedNs = MISSING;
        previousRawPosition = MISSING; positionWrap = 0; audioPlaying = false;
        return audioEpoch;
    }

    synchronized int audioEpoch() { return audioEpoch; }

    /**
     * AudioTrack timestamps expose a wrapping 32-bit frame position. This
     * audio timeline belongs to one AudioTrack, and resetAudio() must be called
     * when the track is recreated. A failed query invalidates extrapolation
     * until the next success.
     */
    synchronized void audioTimestamp(boolean valid, long rawPosition, long timestampNs,
                                     long observedNs, boolean playing) {
        audioPlaying = playing;
        audioObservedNs = observedNs;
        if (!valid || timestampNs <= 0) { audioFramePosition = MISSING; audioTimestampNs = MISSING; return; }
        long low = rawPosition & 0xffffffffL;
        if (previousRawPosition != MISSING && previousRawPosition - low > 0x80000000L) positionWrap += 1L << 32;
        previousRawPosition = low;
        audioFramePosition = positionWrap + low;
        audioTimestampNs = timestampNs;
    }

    synchronized void audioTimestamp(int epoch, boolean valid, long rawPosition, long timestampNs,
                                     long observedNs, boolean playing) {
        if (epoch == audioEpoch) audioTimestamp(valid, rawPosition, timestampNs, observedNs, playing);
    }

    synchronized AudioEstimate audioEstimate(long nowNs) {
        return estimateAudio(sampleRate, audioWrittenFrames, audioFramePosition, audioTimestampNs,
                audioObservedNs, nowNs, audioPlaying);
    }

    /** Separates a stale timestamp's age from the estimated remaining PCM queue. */
    static AudioEstimate estimateAudio(int sampleRate, long writtenFrames, long framePosition,
                                      long timestampNs, long observedNs, long nowNs, boolean playing) {
        boolean valid = sampleRate > 0 && writtenFrames >= 0 && framePosition >= 0
                && framePosition <= writtenFrames && timestampNs > 0 && observedNs > 0
                && Math.abs(nowNs - timestampNs) <= MAX_TIMESTAMP_DISTANCE_NS
                && Math.abs(nowNs - observedNs) <= MAX_TIMESTAMP_DISTANCE_NS;
        if (!valid) return new AudioEstimate(false, writtenFrames, framePosition, MISSING,
                timestampNs == MISSING ? MISSING : nowNs - timestampNs, Double.NaN, Double.NaN, false);
        double position = framePosition + (playing ? (nowNs - timestampNs) * sampleRate / 1e9 : 0.0);
        position = Math.max(0.0, Math.min(writtenFrames, position));
        double rawQueueMs = Math.max(0L, writtenFrames - framePosition) * 1000.0 / sampleRate;
        double queueMs = (writtenFrames - position) * 1000.0 / sampleRate;
        return new AudioEstimate(true, writtenFrames, framePosition, (long) position,
                nowNs - timestampNs, rawQueueMs, queueMs, playing && position < writtenFrames);
    }

    private long audioMediaPtsAt(long queryNs) {
        AudioEstimate estimate = estimateAudio(sampleRate, audioWrittenFrames, audioFramePosition,
                audioTimestampNs, audioObservedNs, queryNs, audioPlaying);
        if (!estimate.valid || !estimate.dataAtPosition) return MISSING;
        for (Iterator<AudioSegment> it = audioSegments.descendingIterator(); it.hasNext();) {
            AudioSegment segment = it.next();
            if (estimate.estimatedFramePosition >= segment.start && estimate.estimatedFramePosition < segment.end)
                return segment.ptsUs + (estimate.estimatedFramePosition - segment.start) * 1_000_000L / sampleRate;
        }
        return MISSING;
    }

    synchronized Snapshot snapshot() {
        return new Snapshot(new ArrayList<>(rendered), videoRecordsEvicted, pendingEvicted,
                audioSegmentsEvicted, unmatchedRenders);
    }

    static boolean plausibleRenderTimestamp(long vendorNs, long callbackNs, long releasedNs) {
        return vendorNs > 0 && callbackNs > 0 && vendorNs <= callbackNs
                && (releasedNs == MISSING || vendorNs >= releasedNs);
    }

    private static <T> void trim(LinkedHashMap<Long, T> values, int max) {
        while (values.size() > max) { Iterator<Long> it = values.keySet().iterator(); it.next(); it.remove(); }
    }

    private static final class Scheduled {
        final long arrivalNs, inputQueuedNs, decoderReadyNs, targetNs, releasedNs;
        Scheduled(long arrivalNs, long queuedNs, long readyNs, long targetNs, long releasedNs) {
            this.arrivalNs = arrivalNs; inputQueuedNs = queuedNs; this.decoderReadyNs = readyNs;
            this.targetNs = targetNs; this.releasedNs = releasedNs;
        }
    }

    private static final class AudioSegment {
        final long start, end, ptsUs;
        AudioSegment(long start, long end, long ptsUs) { this.start = start; this.end = end; this.ptsUs = ptsUs; }
    }

    static final class VideoSample {
        final long ptsUs, actualRenderNs, callbackNs, arrivalNs, inputQueuedNs, decoderReadyNs, targetNs, releasedNs, audioPtsUs;
        final boolean vendorTimestampPlausible, vendorTimestampFuture, vendorTimestampBeforeRelease, vendorTimestampEchoesTarget;
        VideoSample(long ptsUs, long renderNs, long callbackNs, Scheduled frame, long audioPtsUs) {
            this.ptsUs = ptsUs; actualRenderNs = renderNs; this.callbackNs = callbackNs; this.audioPtsUs = audioPtsUs;
            arrivalNs = frame == null ? MISSING : frame.arrivalNs;
            inputQueuedNs = frame == null ? MISSING : frame.inputQueuedNs;
            decoderReadyNs = frame == null ? MISSING : frame.decoderReadyNs;
            targetNs = frame == null ? MISSING : frame.targetNs;
            releasedNs = frame == null ? MISSING : frame.releasedNs;
            vendorTimestampFuture = renderNs > callbackNs;
            vendorTimestampBeforeRelease = releasedNs != MISSING && renderNs < releasedNs;
            vendorTimestampEchoesTarget = targetNs != MISSING && renderNs == targetNs;
            vendorTimestampPlausible = plausibleRenderTimestamp(renderNs, callbackNs, releasedNs);
        }
    }

    static final class AudioEstimate {
        final boolean valid, dataAtPosition;
        final long writtenFrames, timestampFramePosition, estimatedFramePosition, timestampAgeNs;
        final double rawQueueMs, estimatedQueueMs;
        AudioEstimate(boolean valid, long writtenFrames, long framePosition, long estimatedPosition,
                      long ageNs, double rawQueueMs, double queueMs, boolean active) {
            this.valid = valid; this.writtenFrames = writtenFrames; timestampFramePosition = framePosition;
            estimatedFramePosition = estimatedPosition; timestampAgeNs = ageNs;
            this.rawQueueMs = rawQueueMs; estimatedQueueMs = queueMs; dataAtPosition = active;
        }
    }

    static final class Snapshot {
        final List<VideoSample> video;
        final long videoRecordsEvicted, pendingEvicted, audioSegmentsEvicted, unmatchedRenders;
        Snapshot(List<VideoSample> video, long videoEvicted, long pendingEvicted,
                 long audioEvicted, long unmatched) {
            this.video = video; videoRecordsEvicted = videoEvicted; this.pendingEvicted = pendingEvicted;
            audioSegmentsEvicted = audioEvicted; unmatchedRenders = unmatched;
        }
    }

    /**
     * Bounded experiment-only counters, using the phone System.nanoTime domain.
     * No callback timestamp, Surface presentation or clock policy is inferred here.
     * Hot-path methods mutate preallocated primitives only; snapshots allocate after media stops.
     */
    static final class StageDiagnostics {
        static final int SEGMENTS = 120, EVENTS = 64;
        static final long WINDOW_NS = 1_000_000_000L, WARMUP_NS = 2_000_000_000L;
        static final int FRAME_OVERFLOW=1, BYTE_OVERFLOW=2, INPUT_TIMEOUT=3,
                WORKER_EXPIRED=4, STALE_FAILURE=5, SLOW_INPUT_WAIT=6, OTHER_CHAIN_LOSS=7, TIMEOUT_CHAIN_LOSS=8,
                CONFIGURE_PHASE=9, CONSUMER_STALL=10, FEC_POLL_DELTA=11, CLOCK_SHIFT=12,
                COPY_STALL=13, INPUT_CALL_STALL=14, OFFER_GAP=15, TAKE_GAP=16, RESERVE_GUARD=17;
        static final int CONFIGURE=1, TAKE=2, CONSUMER=4, FEC=8, CLOCK=16, GUARD=32, INPUT_CALL=64, COPY=128;
        static final long STALL_NS=20_000_000L,GAP_EVENT_NS=80_000_000L;
        static final String[] EXTRA_TOTAL_NAMES={"configure_starts","configure_finishes","configure_failures",
                "configure_unmatched","configure_wall_sum_ns","configure_wall_max_ns","inbox_takes",
                "offer_interval_missing","take_interval_missing","consumer_starts","consumer_finishes",
                "consumer_unmatched","consumer_wall_sum_ns","consumer_wall_max_ns","consumer_cpu_samples",
                "consumer_cpu_missing","consumer_cpu_invalid","consumer_cpu_sum_ns","consumer_non_cpu_sum_ns",
                "consumer_non_cpu_max_ns","copy_starts","copy_finishes","copy_failures","copy_unmatched",
                "input_call_starts","input_call_finishes","input_call_failures","input_call_unmatched",
                "reserve_guard_calls","reserve_guard_nonzero","reserve_guard_age_exceeded","reserve_guard_stale_epoch",
                "reserve_guard_stopping","reserve_guard_config","reserve_guard_context_missing",
                "fec_poll_delta_observations","fec_counter_delta_sum","fec_expiry_delta","fec_reference_lost_delta",
                "fec_dependency_drop_delta","fec_memory_reject_delta","fec_clock_map_reject_delta","fec_logical_reject_delta",
                "fec_invalid_delta","fec_first_observation_ns","fec_last_observation_ns","fec_max_poll_interval_ns",
                "clock_change_observations","clock_delta_missing","clock_delta_sum_ns","clock_abs_delta_sum_ns",
                "clock_delta_max_abs_ns","clock_video_anchor","clock_transport_reanchor","clock_offset_decay",
                "clock_audio_anchor","clock_hold_gain","clock_decoder_reanchor","clock_hold_decay","clock_invalid_kind","fec_poll_observations"};
        static final String[] EXTRA_HISTOGRAM_NAMES={"configure_wall_ns","offer_interval_ns","take_interval_ns",
                "take_minus_received_ns","consumer_wall_ns","consumer_non_cpu_elapsed_ns",
                "copy_wall_ns","queue_input_call_wall_ns","clock_mapping_delta_ns"};
        static final String[] PHASE_STATE_NAMES={"configure_started_ns","configure_pts_us","configure_received_ns",
                "consumer_started_ns","consumer_pts_us","consumer_cpu_started_ns","copy_started_ns","copy_pts_us",
                "input_call_started_ns","input_call_pts_us","last_offer_ns","last_take_ns","reserve_guard_time_ns",
                "reserve_guard_pts_us","reserve_guard_flags","reserve_guard_frame_epoch","reserve_guard_current_epoch"};
        static final String[] SEGMENT_NAMES = {"offered_frames", "depth_observations", "depth_sum", "depth_max",
                "overflow_events", "cleared_frames", "worker_expired", "codec_reserve_calls", "codec_reserve_polls",
                "codec_reserve_wait_sum_ns", "codec_reserve_wait_max_ns", "codec_timeouts", "scheduled_outputs",
                "ready_minus_input_sum_ns", "ready_minus_input_max_ns", "ready_minus_input_missing",
                "target_minus_release_sum_ns", "target_minus_release_min_ns", "target_minus_release_max_ns", "waiting_idr_drops",
                "media_input_queue_call_starts", "ready_minus_input_invalid", "config_reserve_calls", "media_reserve_calls",
                "configure_starts","configure_finishes","configure_wall_max_ns","inbox_takes","take_interval_max_ns",
                "offer_interval_max_ns","consumer_finishes","consumer_wall_max_ns","consumer_cpu_sum_ns",
                "consumer_non_cpu_max_ns","fec_counter_delta_sum","clock_change_observations","clock_abs_delta_sum_ns",
                "reserve_guard_nonzero","input_call_finishes","copy_wall_max_ns"};
        static final String[] EVENT_NAMES = {"time_ns", "pts_us", "kind", "duration_ns", "queue_frames",
                "queue_bytes", "epoch", "polls", "flags", "frame_id", "delta_ns", "non_cpu_ns", "current_epoch"};
        final long[][] segments = new long[SEGMENT_NAMES.length][SEGMENTS];
        final long[][] events = new long[EVENT_NAMES.length][EVENTS];
        final long[] depthCounts = new long[5];
        final Histogram targetMinusRelease = new Histogram(), readyMinusInput = new Histogram(),
                outputHold = new Histogram(), codecReserveWait = new Histogram(), receiveMinusInput = new Histogram();
        long originNs=MISSING, firstObservationNs=MISSING, lastObservationNs=MISSING;
        long observations, beforeOriginObservations, afterCapacityObservations, eventObserved, eventsEvicted;
        long offered, depthObserved, overflow, cleared, workerExpired, reserveCalls, reservePolls,
                configReserveCalls, mediaReserveCalls, inputTimeouts, scheduled, missingQueued, invalidReady, waitingDrops;
        int lastSegment=-1, eventWrite, eventCount;
        long coverageMask;
        final long[] extraTotals=new long[EXTRA_TOTAL_NAMES.length];
        final Histogram[] extraHistograms=new Histogram[EXTRA_HISTOGRAM_NAMES.length];
        long configureStart=MISSING,configurePts=MISSING,configureReceived=MISSING;
        long consumerStart=MISSING,consumerPts=MISSING,consumerCpu=MISSING;
        long copyStart=MISSING,copyPts=MISSING,callStart=MISSING,callPts=MISSING;
        long lastOffer=MISSING,lastTake=MISSING,guardTime=MISSING,guardPts=MISSING;
        int guardFlags;long guardFrameEpoch=MISSING,guardCurrentEpoch=MISSING;
        StageDiagnostics(){for(int i=0;i<extraHistograms.length;i++)extraHistograms[i]=new Histogram();}
        synchronized void markCoverage(int mask){coverageMask|=mask;}
        private void count(int slot,int column){if(slot>=0)segments[column][slot]++;}
        private void max(int slot,int column,long value){if(slot>=0&&value>=0)segments[column][slot]=Math.max(segments[column][slot],value);}
        private void sum(int slot,int column,long value){if(slot>=0)segments[column][slot]+=value;}

        private int observe(long nowNs) {
            observations++;
            if (firstObservationNs==MISSING || nowNs<firstObservationNs) firstObservationNs=nowNs;
            if (lastObservationNs==MISSING || nowNs>lastObservationNs) lastObservationNs=nowNs;
            if (originNs==MISSING) { beforeOriginObservations++; return -1; }
            long elapsed=nowNs-originNs;
            if (elapsed<0) { beforeOriginObservations++; return -1; }
            long slot=elapsed/WINDOW_NS;
            if (slot>=SEGMENTS) { afterCapacityObservations++; return -1; }
            lastSegment=Math.max(lastSegment,(int)slot); return (int)slot;
        }
        private void event(long nowNs,long ptsUs,int kind,long durationNs,int frames,long bytes,long epoch,int polls) {
            int slot=eventWrite;
            events[0][slot]=nowNs; events[1][slot]=ptsUs; events[2][slot]=kind; events[3][slot]=durationNs;
            events[4][slot]=frames; events[5][slot]=bytes; events[6][slot]=epoch; events[7][slot]=polls;
            events[8][slot]=0;events[9][slot]=-1;events[10][slot]=MISSING;events[11][slot]=MISSING;events[12][slot]=MISSING;
            eventWrite=(slot+1)%EVENTS; eventObserved++;
            if(eventCount<EVENTS)eventCount++;else eventsEvicted++;
        }
        private void extraEvent(long nowNs,long ptsUs,int kind,long duration,int flags,long frameId,
                                long delta,long nonCpu,long frameEpoch,long currentEpoch){
            event(nowNs,ptsUs,kind,duration,-1,-1,frameEpoch,0);int index=(eventWrite-1+EVENTS)%EVENTS;
            events[8][index]=flags;events[9][index]=frameId;events[10][index]=delta;
            events[11][index]=nonCpu;events[12][index]=currentEpoch;
        }
        synchronized void inboxOffer(long receivedNs){inboxOffer(receivedNs,receivedNs);}
        synchronized void inboxOffer(long receivedNs,long offerNs) {
            if(originNs==MISSING)originNs=receivedNs;
            offered++;int slot=observe(offerNs);count(slot,0);
            if(lastOffer==MISSING)extraTotals[7]++;
            else {long gap=offerNs-lastOffer;extraHistograms[1].add(gap);max(slot,29,gap);
                if(gap>=GAP_EVENT_NS)extraEvent(offerNs,-1,OFFER_GAP,gap,0,-1,MISSING,MISSING,MISSING,MISSING);}
            lastOffer=offerNs;
        }
        synchronized void inboxTaken(long nowNs,long ptsUs,long receivedNs,int frames,long bytes){
            coverageMask|=TAKE;extraTotals[6]++;int slot=observe(nowNs);count(slot,27);
            extraHistograms[3].add(nowNs-receivedNs);
            if(lastTake==MISSING)extraTotals[8]++;
            else {long gap=nowNs-lastTake;extraHistograms[2].add(gap);max(slot,28,gap);
                if(gap>=GAP_EVENT_NS)extraEvent(nowNs,ptsUs,TAKE_GAP,gap,0,-1,MISSING,MISSING,MISSING,MISSING);}
            lastTake=nowNs;
        }
        synchronized void configureStarted(long nowNs,long ptsUs,long receivedNs){
            coverageMask|=CONFIGURE;extraTotals[0]++;if(configureStart!=MISSING)extraTotals[3]++;
            configureStart=nowNs;configurePts=ptsUs;configureReceived=receivedNs;count(observe(nowNs),24);
        }
        synchronized void configureFinished(long nowNs,long ptsUs,boolean success){
            extraTotals[1]++;if(!success)extraTotals[2]++;int slot=observe(nowNs);count(slot,25);
            if(configureStart==MISSING||configurePts!=ptsUs){extraTotals[3]++;return;}
            long elapsed=nowNs-configureStart;extraHistograms[0].add(elapsed);
            if(elapsed>=0){extraTotals[4]+=elapsed;extraTotals[5]=Math.max(extraTotals[5],elapsed);max(slot,26,elapsed);}
            extraEvent(nowNs,ptsUs,CONFIGURE_PHASE,elapsed,success?0:1,-1,MISSING,MISSING,MISSING,MISSING);
            configureStart=MISSING;configurePts=MISSING;configureReceived=MISSING;
        }
        synchronized void consumerStarted(long nowNs,long ptsUs){consumerStarted(nowNs,ptsUs,MISSING);}
        synchronized void consumerStarted(long nowNs,long ptsUs,long cpuNs){
            coverageMask|=CONSUMER;extraTotals[9]++;if(consumerStart!=MISSING)extraTotals[11]++;
            consumerStart=nowNs;consumerPts=ptsUs;consumerCpu=cpuNs;observe(nowNs);
        }
        synchronized void consumerFinished(long nowNs,long ptsUs){consumerFinished(nowNs,ptsUs,MISSING);}
        synchronized void consumerFinished(long nowNs,long ptsUs,long cpuNs){
            extraTotals[10]++;int slot=observe(nowNs);count(slot,30);
            if(consumerStart==MISSING||consumerPts!=ptsUs){extraTotals[11]++;return;}
            long wall=nowNs-consumerStart,nonCpu=MISSING;extraHistograms[4].add(wall);
            if(wall>=0){extraTotals[12]+=wall;extraTotals[13]=Math.max(extraTotals[13],wall);max(slot,31,wall);}
            if(cpuNs==MISSING||consumerCpu==MISSING)extraTotals[15]++;
            else {long cpu=cpuNs-consumerCpu;
                if(cpu<0||wall<0||cpu>wall)extraTotals[16]++;
                else {extraTotals[14]++;extraTotals[17]+=cpu;nonCpu=wall-cpu;extraTotals[18]+=nonCpu;
                    extraTotals[19]=Math.max(extraTotals[19],nonCpu);extraHistograms[5].add(nonCpu);
                    sum(slot,32,cpu);max(slot,33,nonCpu);}}
            if(wall>=STALL_NS)extraEvent(nowNs,ptsUs,CONSUMER_STALL,wall,0,-1,MISSING,nonCpu,MISSING,MISSING);
            consumerStart=MISSING;consumerPts=MISSING;consumerCpu=MISSING;
        }
        synchronized void inputCopyStarted(long nowNs,long ptsUs,boolean config){
            coverageMask|=COPY;extraTotals[20]++;if(copyStart!=MISSING)extraTotals[23]++;
            copyStart=nowNs;copyPts=ptsUs;observe(nowNs);
        }
        synchronized void inputCopyFinished(long nowNs,long ptsUs,boolean config,boolean success){
            extraTotals[21]++;if(!success)extraTotals[22]++;int slot=observe(nowNs);
            if(copyStart==MISSING||copyPts!=ptsUs){extraTotals[23]++;return;}
            long wall=nowNs-copyStart;extraHistograms[6].add(wall);max(slot,39,wall);
            if(wall>=STALL_NS||!success)extraEvent(nowNs,ptsUs,COPY_STALL,wall,(config?8:0)|(success?0:16),-1,MISSING,MISSING,MISSING,MISSING);
            copyStart=MISSING;copyPts=MISSING;
        }
        synchronized void inputCallStarted(long nowNs,long ptsUs,boolean config){
            coverageMask|=INPUT_CALL;extraTotals[24]++;if(callStart!=MISSING)extraTotals[27]++;
            callStart=nowNs;callPts=ptsUs;observe(nowNs);
        }
        synchronized void inputCallFinished(long nowNs,long ptsUs,boolean config,boolean success){
            extraTotals[25]++;if(!success)extraTotals[26]++;int slot=observe(nowNs);count(slot,38);
            if(callStart==MISSING||callPts!=ptsUs){extraTotals[27]++;return;}
            long wall=nowNs-callStart;extraHistograms[7].add(wall);
            if(wall>=STALL_NS||!success)extraEvent(nowNs,ptsUs,INPUT_CALL_STALL,wall,(config?8:0)|(success?0:16),-1,MISSING,MISSING,MISSING,MISSING);
            callStart=MISSING;callPts=MISSING;
        }
        synchronized void reserveGuard(long nowNs,long ptsUs,int flags,long frameEpoch,long currentEpoch){
            coverageMask|=GUARD;extraTotals[28]++;guardTime=nowNs;guardPts=ptsUs;guardFlags=flags;
            guardFrameEpoch=frameEpoch;guardCurrentEpoch=currentEpoch;int slot=observe(nowNs);
            if(flags!=0){extraTotals[29]++;count(slot,37);
                extraEvent(nowNs,ptsUs,RESERVE_GUARD,0,flags,-1,MISSING,MISSING,frameEpoch,currentEpoch);}
            if((flags&1)!=0)extraTotals[30]++;if((flags&2)!=0)extraTotals[31]++;
            if((flags&4)!=0)extraTotals[32]++;if((flags&8)!=0)extraTotals[33]++;
        }
        synchronized void fecPollObserved(long observedNs){
            coverageMask|=FEC;extraTotals[60]++;observe(observedNs);
            if(extraTotals[44]==0)extraTotals[44]=observedNs;
            if(extraTotals[45]!=0)extraTotals[46]=Math.max(extraTotals[46],observedNs-extraTotals[45]);
            extraTotals[45]=observedNs;
        }
        /** Timestamp is counter-poll observation, not native exception occurrence. */
        synchronized void fecException(long observedNs,long frameId,int kind,long delta){
            coverageMask|=FEC;int slot=observe(observedNs);
            if(delta<=0||kind<1||kind>6){extraTotals[43]++;return;}
            extraTotals[35]++;extraTotals[36]+=delta;extraTotals[36+kind]+=delta;sum(slot,34,delta);
            extraEvent(observedNs,-1,FEC_POLL_DELTA,0,kind,frameId,delta,MISSING,MISSING,MISSING);
        }
        /** Actual shared mapping changes; tiny decays counted without consuming the ring. */
        synchronized void clockReanchor(long observedNs,int kind,long deltaNs){
            coverageMask|=CLOCK;extraTotals[47]++;int slot=observe(observedNs);count(slot,35);
            if(kind>=1&&kind<=7)extraTotals[51+kind]++;else extraTotals[59]++;
            if(deltaNs==MISSING)extraTotals[48]++;
            else {long absolute=Math.abs(deltaNs);extraTotals[49]+=deltaNs;extraTotals[50]+=absolute;
                extraTotals[51]=Math.max(extraTotals[51],absolute);extraHistograms[8].addSigned(deltaNs);sum(slot,36,absolute);}
            if(deltaNs==MISSING||kind==1||kind==2||kind==4||kind==6||Math.abs(deltaNs)>=STALL_NS)
                extraEvent(observedNs,-1,CLOCK_SHIFT,0,kind,-1,deltaNs,MISSING,MISSING,MISSING);
        }
        synchronized void establishOrigin(long receivedNs) { if(originNs==MISSING)originNs=receivedNs; }
        /** Operation-sampled depth, not a time-weighted occupancy estimate. */
        synchronized void inboxDepth(long nowNs,int frames,long bytes) {
            depthObserved++;if(frames>=0&&frames<depthCounts.length)depthCounts[frames]++;
            int slot=observe(nowNs);if(slot>=0){segments[1][slot]++;segments[2][slot]+=frames;
                segments[3][slot]=Math.max(segments[3][slot],frames);}
        }
        synchronized void inboxLoss(long nowNs,long ptsUs,int kind,int frames,long bytes,long epoch) {
            int slot=observe(nowNs);cleared+=frames;
            if(kind==FRAME_OVERFLOW||kind==BYTE_OVERFLOW){overflow++;if(slot>=0)segments[4][slot]++;}
            if(kind==WORKER_EXPIRED){workerExpired++;if(slot>=0)segments[6][slot]++;}
            if(slot>=0)segments[5][slot]+=frames;
            event(nowNs,ptsUs,kind,0,frames,bytes,epoch,0);
        }
        synchronized void waitingIdrDrop(long nowNs) {
            waitingDrops++;int slot=observe(nowNs);if(slot>=0)segments[19][slot]++;
        }
        synchronized void reserve(long nowNs,long ptsUs,long startedNs,long acquiredNs,
                                  int polls,boolean config,int failure,int frames,long bytes,long epoch) {
            reserveCalls++;reservePolls+=polls;if(config)configReserveCalls++;else mediaReserveCalls++;
            long waited=acquiredNs-startedNs;codecReserveWait.add(waited);
            int slot=observe(nowNs);if(slot>=0){segments[7][slot]++;segments[8][slot]+=polls;
                segments[config?22:23][slot]++;
                if(waited>=0){segments[9][slot]+=waited;segments[10][slot]=Math.max(segments[10][slot],waited);}}
            if(failure==1){inputTimeouts++;if(slot>=0)segments[11][slot]++;
                event(nowNs,ptsUs,INPUT_TIMEOUT,waited,frames,bytes,epoch,polls);}
            else if(waited>=20_000_000L)event(nowNs,ptsUs,SLOW_INPUT_WAIT,waited,frames,bytes,epoch,polls);
            if(failure==1||waited>=20_000_000L){int index=(eventWrite-1+EVENTS)%EVENTS;
                if(guardPts==ptsUs&&guardTime!=MISSING){events[8][index]=guardFlags;events[12][index]=guardCurrentEpoch;}
                else extraTotals[34]++;}
            // Receive-to-input is recorded separately only at the actual successful media queue.
        }
        synchronized void inputQueued(long receivedNs,long inputNs) {
            receiveMinusInput.add(inputNs-receivedNs);int slot=observe(inputNs);if(slot>=0)segments[20][slot]++;
        }
        synchronized void scheduled(long ptsUs,long inputNs,long readyNs,long targetNs,long releasedNs) {
            scheduled++;int slot=observe(releasedNs);long lead=targetNs-releasedNs;
            targetMinusRelease.addSigned(lead);outputHold.add(releasedNs-readyNs);
            if(slot>=0){if(segments[12][slot]++==0){segments[17][slot]=lead;segments[18][slot]=lead;}
                segments[16][slot]+=lead;segments[17][slot]=Math.min(segments[17][slot],lead);
                segments[18][slot]=Math.max(segments[18][slot],lead);}
            if(inputNs==MISSING){missingQueued++;if(slot>=0)segments[15][slot]++;}
            else if(readyNs<inputNs){invalidReady++;readyMinusInput.invalid++;if(slot>=0)segments[21][slot]++;}
            else {long value=readyNs-inputNs;readyMinusInput.add(value);
                if(slot>=0){segments[13][slot]+=value;segments[14][slot]=Math.max(segments[14][slot],value);}}
        }
        synchronized StageSnapshot snapshot(long nowNs) {
            int count=lastSegment+1;long[][] rows=new long[segments.length][];
            for(int i=0;i<rows.length;i++)rows[i]=java.util.Arrays.copyOf(segments[i],count);
            long[][] retained=new long[events.length][eventCount];int first=(eventWrite-eventCount+EVENTS)%EVENTS;
            for(int i=0;i<retained.length;i++)for(int j=0;j<eventCount;j++)retained[i][j]=events[i][(first+j)%EVENTS];
            HistogramSnapshot[] extras=new HistogramSnapshot[extraHistograms.length];
            for(int i=0;i<extras.length;i++)extras[i]=extraHistograms[i].snapshot();
            return new StageSnapshot(nowNs,originNs,firstObservationNs,lastObservationNs,observations,
                beforeOriginObservations,afterCapacityObservations,eventObserved,eventsEvicted,
                new long[]{offered,depthObserved,overflow,cleared,workerExpired,reserveCalls,reservePolls,
                    configReserveCalls,mediaReserveCalls,inputTimeouts,scheduled,missingQueued,invalidReady,waitingDrops},
                depthCounts.clone(),rows,retained,new HistogramSnapshot[]{targetMinusRelease.snapshot(),
                    readyMinusInput.snapshot(),outputHold.snapshot(),codecReserveWait.snapshot(),receiveMinusInput.snapshot()},
                coverageMask,extraTotals.clone(),extras,new long[]{configureStart,configurePts,configureReceived,
                    consumerStart,consumerPts,consumerCpu,copyStart,copyPts,callStart,callPts,lastOffer,lastTake,
                    guardTime,guardPts,guardFlags,guardFrameEpoch,guardCurrentEpoch});
        }
        static final class Histogram {
            // Signed target lead uses every bucket. Nonnegative stage intervals reject negative observations.
            static final long[] UPPER_NS={-80_000_000L,-40_000_000L,-16_000_000L,0,2_000_000L,4_000_000L,
                8_000_000L,16_000_000L,32_000_000L,64_000_000L,80_000_000L,120_000_000L,250_000_000L};
            final long[] counts=new long[UPPER_NS.length+1];long valid,invalid,sum,min=Long.MAX_VALUE,max=Long.MIN_VALUE;
            void add(long value){if(value<0){invalid++;return;}addSigned(value);}
            void addSigned(long value){int index=0;while(index<UPPER_NS.length&&value>UPPER_NS[index])index++;
                counts[index]++;valid++;sum+=value;min=Math.min(min,value);max=Math.max(max,value);}
            HistogramSnapshot snapshot(){return new HistogramSnapshot(counts.clone(),valid,invalid,sum,
                valid==0?0:min,valid==0?0:max);}
        }
    }
    static final class HistogramSnapshot {
        final long[] counts;final long valid,invalid,sum,min,max;
        HistogramSnapshot(long[] counts,long valid,long invalid,long sum,long min,long max){
            this.counts=counts;this.valid=valid;this.invalid=invalid;this.sum=sum;this.min=min;this.max=max;}
    }
    static final class StageSnapshot {
        final long snapshotNs,originNs,firstNs,lastNs,observations,beforeOrigin,afterCapacity,eventObserved,eventsEvicted;
        final long[] totals,depthCounts;final long[][] segments,events;final HistogramSnapshot[] histograms;
        final long coverageMask;final long[] extraTotals,phaseState;final HistogramSnapshot[] extraHistograms;
        StageSnapshot(long snapshotNs,long originNs,long firstNs,long lastNs,long observations,long beforeOrigin,
                      long afterCapacity,long eventObserved,long eventsEvicted,long[] totals,long[] depths,
                      long[][] segments,long[][] events,HistogramSnapshot[] histograms,long coverageMask,
                      long[] extraTotals,HistogramSnapshot[] extraHistograms,long[] phaseState){
            this.snapshotNs=snapshotNs;this.originNs=originNs;this.firstNs=firstNs;this.lastNs=lastNs;
            this.observations=observations;this.beforeOrigin=beforeOrigin;this.afterCapacity=afterCapacity;
            this.eventObserved=eventObserved;this.eventsEvicted=eventsEvicted;this.totals=totals;
            depthCounts=depths;this.segments=segments;this.events=events;this.histograms=histograms;
            this.coverageMask=coverageMask;this.extraTotals=extraTotals;this.extraHistograms=extraHistograms;this.phaseState=phaseState;}
    }
}
