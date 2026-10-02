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

    MediaPresentationMetrics() { this(48000); }
    MediaPresentationMetrics(int sampleRate) {
        if (sampleRate <= 0) throw new IllegalArgumentException("sampleRate");
        this.sampleRate = sampleRate;
    }

    synchronized void received(long ptsUs, long arrivalNs) {
        arrivals.put(ptsUs, arrivalNs);
        trim(arrivals, MAX_PENDING);
    }

    /** Call just before queueInputBuffer for a non-config video access unit. */
    synchronized void inputQueued(long ptsUs, long queuedNs) {
        inputQueued.put(ptsUs, queuedNs); trim(inputQueued, MAX_PENDING);
    }

    /** Call immediately before releaseOutputBuffer(index, targetNs). */
    synchronized void scheduled(long ptsUs, long decoderReadyNs, long targetNs, long releasedNs) {
        Long arrival = arrivals.remove(ptsUs);
        Long queued = inputQueued.remove(ptsUs);
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
}
