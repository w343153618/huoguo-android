package local.remoteandroid.direct;

/** JVM regression probe for the phone's shared audio/video playback clock. */
public final class PlaybackClockProbe {
    private static void equal(long expected, long actual, String label) {
        if (expected != actual) throw new AssertionError(label + ": " + actual + " != " + expected);
    }

    public static void main(String[] args) {
        PlaybackClock clock = new PlaybackClock(100);
        clock.observeAudio(0, 900_000_000L);
        equal(1_000_000_000L, clock.deadline(0), "audio can initialize playback");
        clock.observe(0, 1_000_000_000L);
        equal(1_100_000_000L, clock.deadline(0), "first video anchors its arrival");
        clock.observeAudio(0, 800_000_000L);
        equal(1_100_000_000L, clock.deadline(0), "early audio cannot pull video earlier");

        // A hardware decoder with a steady extra 150 ms delay must not have
        // every output discarded merely because the initial buffer was 100 ms.
        long frameNs = 33_333_000L;
        for (int i = 0; i < 3; i++) {
            long ready = 1_250_000_000L + i * frameNs;
            long target = clock.videoDeadline(i * 33_333L, ready);
            if (i == 2 && target < ready) throw new AssertionError("steady decoder delay was not absorbed");
        }
        long adjusted = clock.deadline(3 * 33_333L);
        if (adjusted < 1_350_000_000L || adjusted > 1_550_000_000L)
            throw new AssertionError("decoder hold out of bounded range: " + adjusted);
        long held = clock.deadline(0);
        clock.observeAudio(0, 800_000_000L);
        equal(held, clock.deadline(0), "audio cannot undo decoder hold");

        PlaybackClock healthy = new PlaybackClock(60);
        healthy.observe(0, 1_000_000_000L);
        for (int i = 0; i < 10; i++) {
            long ptsUs = i * 16_667L;
            long ready = 1_025_000_000L + i * 16_667_000L;
            healthy.videoDeadline(ptsUs, ready);
        }
        equal(1_060_000_000L, healthy.deadline(0), "healthy decoder adds no delay");

        // A decoder backlog greater than the bounded hold must recover instead
        // of discarding every subsequent frame indefinitely.
        PlaybackClock overloaded = new PlaybackClock(80);
        overloaded.observe(0, 1_000_000_000L);
        long target = 0;
        for (int i = 0; i < 12; i++) {
            long ptsUs = i * 33_333L;
            long arrival = 1_000_000_000L + i * 33_333_000L;
            overloaded.observe(ptsUs, arrival);
            long ready = arrival + 500_000_000L;
            target = overloaded.videoDeadline(ptsUs, ready);
            if (i >= 3 && target < ready - 40_000_000L)
                throw new AssertionError("decoder backlog never resynchronized at frame " + i);
        }
        System.out.println("PlaybackClockProbe PASS");
    }
}
