package local.remoteandroid.direct;

/** Source-level regression checks, not real-phone performance results. */
public final class MediaPresentationMetricsProbe {
    private static void near(double wanted, double got, String label) {
        if (Math.abs(wanted - got) > 0.001) throw new AssertionError(label + ": " + got + " != " + wanted);
    }
    private static void equal(long wanted, long got, String label) {
        if (wanted != got) throw new AssertionError(label + ": " + got + " != " + wanted);
    }
    public static void main(String[] args) {
        long now = 2_000_000_000L;
        MediaPresentationMetrics.AudioEstimate q = MediaPresentationMetrics.estimateAudio(
                48000, 5760, 0, now - 100_000_000L, now, now, true);
        if (!q.valid) throw new AssertionError("fresh hardware observation rejected");
        near(120, q.rawQueueMs, "uncorrected queue contains timestamp age");
        near(20, q.estimatedQueueMs, "100ms timestamp age removed from queue estimate");
        equal(4800, q.estimatedFramePosition, "projected playback position");
        q = MediaPresentationMetrics.estimateAudio(48000, 5760, 0, now - 100_000_000L, now, now, false);
        near(120, q.estimatedQueueMs, "paused track must not extrapolate");
        q = MediaPresentationMetrics.estimateAudio(48000, 5760, 0, now - 600_000_000L, now, now, true);
        if (q.valid) throw new AssertionError("stale timestamp accepted");
        q = MediaPresentationMetrics.estimateAudio(48000, 5760, 2000, now + 10_000_000L, now, now, true);
        equal(1520, q.estimatedFramePosition, "future committed presentation is allowed");

        MediaPresentationMetrics m = new MediaPresentationMetrics();
        m.audioWritten(0, 4800, 1_000_000L);
        m.audioWritten(4800, 4800, 1_100_000L);
        m.audioTimestamp(true, 2400, now, now, true);
        m.received(1_050_000L, now - 25_000_000L);
        m.inputQueued(1_050_000L, now - 20_000_000L);
        m.scheduled(1_050_000L, now - 15_000_000L, now - 3_000_000L, now - 10_000_000L);
        m.rendered(1_050_000L, now, now + 50_000_000L);
        MediaPresentationMetrics.VideoSample frame = m.snapshot().video.get(0);
        equal(now - 3_000_000L, frame.targetNs, "frozen release target survives later clock changes");
        equal(5_000_000L, frame.inputQueuedNs - frame.arrivalNs, "receiver input-buffer wait separated");
        equal(5_000_000L, frame.decoderReadyNs - frame.inputQueuedNs, "codec output stage separated");
        equal(1_050_000L, frame.audioPtsUs, "audio PCM sample PTS at video render");
        equal(50_000_000L, frame.callbackNs - frame.actualRenderNs, "batched callback separated from render time");
        m.rendered(1_125_000L, now + 75_000_000L, now + 80_000_000L);
        frame = m.snapshot().video.get(1);
        equal(1_125_000L, frame.audioPtsUs, "second PCM buffer mapping");
        equal(MediaPresentationMetrics.MISSING, frame.targetNs, "unmatched frame has no fabricated schedule");
        equal(1, m.snapshot().unmatchedRenders, "unmatched callback counted");
        m.audioTimestamp(false, 0, 0, now + 100_000_000L, true);
        m.rendered(1_150_000L, now + 100_000_000L, now + 100_000_000L);
        equal(MediaPresentationMetrics.MISSING, m.snapshot().video.get(2).audioPtsUs, "failed timestamp invalidates AV estimate");
        int oldEpoch = m.audioEpoch(), epoch = m.resetAudio();
        m.audioWritten(oldEpoch, 9600, 4800, 1_200_000L);
        m.audioTimestamp(oldEpoch, true, 12000, now, now, true);
        m.audioWritten(epoch, 0, 4800, 5_000_000L);
        m.audioTimestamp(epoch, true, 2400, now, now, true);
        m.rendered(5_050_000L, now, now);
        equal(5_050_000L, m.snapshot().video.get(3).audioPtsUs, "track restart resets PCM mapping; old thread observations rejected");
        equal(4, m.snapshot().video.size(), "audio reset preserves video history");
        m.scheduled(5_060_000L, now, now + 100_000_000L, now + 1_000_000L);
        m.rendered(5_060_000L, now + 100_000_000L, now + 2_000_000L);
        frame = m.snapshot().video.get(4);
        if (frame.vendorTimestampPlausible || !frame.vendorTimestampFuture || !frame.vendorTimestampEchoesTarget)
            throw new AssertionError("future target echo incorrectly accepted as presentation");
        equal(MediaPresentationMetrics.MISSING, frame.audioPtsUs, "future vendor timestamp cannot produce AV estimate");
        m.scheduled(5_070_000L, now, now, now + 1_000_000L);
        m.rendered(5_070_000L, now, now + 2_000_000L);
        frame = m.snapshot().video.get(5);
        if (frame.vendorTimestampPlausible || !frame.vendorTimestampBeforeRelease)
            throw new AssertionError("render before release accepted");
        if (MediaPresentationMetrics.plausibleRenderTimestamp(0, now, MediaPresentationMetrics.MISSING))
            throw new AssertionError("zero vendor timestamp accepted");

        MediaPresentationMetrics wrap = new MediaPresentationMetrics();
        wrap.audioTimestamp(true, 0xfffffff0L, now, now, true);
        wrap.audioTimestamp(true, 32, now + 1_000_000L, now + 1_000_000L, true);
        equal((1L << 32) + 32, wrap.audioEstimate(now + 1_000_000L).timestampFramePosition, "32bit frame position unwrapped");
        try { m.audioWritten(0, 10, 0); throw new AssertionError("non-contiguous PCM accepted"); }
        catch (IllegalArgumentException expected) { }
        System.out.println("MediaPresentationMetricsProbe PASS");
    }
}
