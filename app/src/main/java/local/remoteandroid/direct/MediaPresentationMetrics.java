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
                WORKER_EXPIRED=4, STALE_FAILURE=5, SLOW_INPUT_WAIT=6, OTHER_CHAIN_LOSS=7, TIMEOUT_CHAIN_LOSS=8;
        static final String[] SEGMENT_NAMES = {"offered_frames", "depth_observations", "depth_sum", "depth_max",
                "overflow_events", "cleared_frames", "worker_expired", "codec_reserve_calls", "codec_reserve_polls",
                "codec_reserve_wait_sum_ns", "codec_reserve_wait_max_ns", "codec_timeouts", "scheduled_outputs",
                "ready_minus_input_sum_ns", "ready_minus_input_max_ns", "ready_minus_input_missing",
                "target_minus_release_sum_ns", "target_minus_release_min_ns", "target_minus_release_max_ns", "waiting_idr_drops",
                "media_input_queue_call_starts", "ready_minus_input_invalid", "config_reserve_calls", "media_reserve_calls"};
        static final String[] EVENT_NAMES = {"time_ns", "pts_us", "kind", "duration_ns", "queue_frames",
                "queue_bytes", "epoch", "polls"};
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
            eventWrite=(slot+1)%EVENTS; eventObserved++;
            if(eventCount<EVENTS)eventCount++;else eventsEvicted++;
        }
        synchronized void inboxOffer(long receivedNs) {
            if(originNs==MISSING)originNs=receivedNs;
            offered++;int slot=observe(receivedNs);if(slot>=0)segments[0][slot]++;
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
            return new StageSnapshot(nowNs,originNs,firstObservationNs,lastObservationNs,observations,
                beforeOriginObservations,afterCapacityObservations,eventObserved,eventsEvicted,
                new long[]{offered,depthObserved,overflow,cleared,workerExpired,reserveCalls,reservePolls,
                    configReserveCalls,mediaReserveCalls,inputTimeouts,scheduled,missingQueued,invalidReady,waitingDrops},
                depthCounts.clone(),rows,retained,new HistogramSnapshot[]{targetMinusRelease.snapshot(),
                    readyMinusInput.snapshot(),outputHold.snapshot(),codecReserveWait.snapshot(),receiveMinusInput.snapshot()});
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
        StageSnapshot(long snapshotNs,long originNs,long firstNs,long lastNs,long observations,long beforeOrigin,
                      long afterCapacity,long eventObserved,long eventsEvicted,long[] totals,long[] depths,
                      long[][] segments,long[][] events,HistogramSnapshot[] histograms){
            this.snapshotNs=snapshotNs;this.originNs=originNs;this.firstNs=firstNs;this.lastNs=lastNs;
            this.observations=observations;this.beforeOrigin=beforeOrigin;this.afterCapacity=afterCapacity;
            this.eventObserved=eventObserved;this.eventsEvicted=eventsEvicted;this.totals=totals;
            depthCounts=depths;this.segments=segments;this.events=events;this.histograms=histograms;}
    }
}
