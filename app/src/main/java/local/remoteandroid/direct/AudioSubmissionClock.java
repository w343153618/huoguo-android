package local.remoteandroid.direct;

/** Pure timestamp arithmetic; does not schedule, write PCM or change playback policy. */
final class AudioSubmissionClock {
    static final long UNAVAILABLE = Long.MIN_VALUE;
    private static final long NS_PER_SECOND = 1_000_000_000L;
    private static final long MAX_TIMESTAMP_AGE_NS = 500_000_000L;

    private AudioSubmissionClock() { }

    /**
     * Estimate the hardware-timestamp time of the current submitted queue tail.
     * timestampFramePosition must be the unwrapped position for this AudioTrack.
     * The caller must separately confirm timestamp validity and PLAYSTATE_PLAYING.
     * Empty/underrun queues cannot have a future write appended in the past, so
     * clamp the result to now. This is not an acoustic-output timing measurement.
     * Invalid, future/stale, inconsistent or overflowing input is unavailable;
     * it never fabricates a zero-latency or wrapped timestamp estimate.
     */
    static long timestampQueueTailNs(long nowNs, long timestampNs, long writtenFrames,
                                     long timestampFramePosition, int sampleRate) {
        if (nowNs <= 0 || timestampNs <= 0 || timestampNs > nowNs
                || nowNs - timestampNs > MAX_TIMESTAMP_AGE_NS || sampleRate <= 0
                || timestampFramePosition < 0 || writtenFrames < timestampFramePosition)
            return UNAVAILABLE;
        long frames = writtenFrames - timestampFramePosition;
        try {
            // Quotient/remainder avoids frames*1e9 overflow for valid long runs.
            long secondsNs = Math.multiplyExact(frames / sampleRate, NS_PER_SECOND);
            long fractionNs = Math.multiplyExact(frames % sampleRate, NS_PER_SECOND) / sampleRate;
            long durationNs = Math.addExact(secondsNs, fractionNs);
            return Math.max(nowNs, Math.addExact(timestampNs, durationNs));
        } catch (ArithmeticException invalid) {
            return UNAVAILABLE;
        }
    }
}
