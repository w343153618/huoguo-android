package local.remoteandroid.direct;

/** Map scrcpy's monotonic media timestamps to one local audio/video clock. */
final class PlaybackClock {
    static final long BUFFER_NS=80_000_000L;
    private long offsetNs;
    private boolean initialized;

    synchronized void observe(long ptsUs,long arrivalNs) {
        long sourceNs=ptsUs*1000L;
        if(!initialized || arrivalNs-(sourceNs+offsetNs)>250_000_000L
                || sourceNs+offsetNs-arrivalNs>1_000_000_000L) {
            // Recover from a large transport stall/reset without accumulating delay forever.
            offsetNs=arrivalNs+BUFFER_NS-sourceNs;
            initialized=true;
        }
    }

    synchronized long deadline(long ptsUs) {
        if(!initialized)throw new IllegalStateException("Missing media timestamp");
        return ptsUs*1000L+offsetNs;
    }
}
