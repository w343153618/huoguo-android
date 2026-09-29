package local.remoteandroid.direct;

/** Map scrcpy's monotonic media timestamps to one local audio/video clock. */
final class PlaybackClock {
    static final long BUFFER_NS=80_000_000L;
    private final long bufferNs;
    PlaybackClock(){this(80);}
    PlaybackClock(int bufferMs){if(bufferMs<30||bufferMs>200)throw new IllegalArgumentException("Invalid buffer");bufferNs=bufferMs*1_000_000L;}
    private long offsetNs;
    private long lastArrivalNs;
    private boolean initialized;

    synchronized void observe(long ptsUs,long arrivalNs) {
        long sourceNs=ptsUs*1000L;
        if(!initialized || arrivalNs-(sourceNs+offsetNs)>250_000_000L
                || sourceNs+offsetNs-arrivalNs>1_000_000_000L) {
            // Recover from a large transport stall/reset without accumulating delay forever.
            offsetNs=arrivalNs+bufferNs-sourceNs;
            initialized=true;
        } else {
            // A stall can leave the old mapping far behind fresh arriving frames.
            // Recover at <=5% of wall time, preserving a shared A/V mapping and jitter buffer.
            long elapsed=Math.min(1_000_000_000L,Math.max(0,arrivalNs-lastArrivalNs));
            long excess=offsetNs-(arrivalNs+bufferNs-sourceNs);
            if(excess>20_000_000L)offsetNs-=Math.min(excess-20_000_000L,elapsed/20);
        }
        lastArrivalNs=Math.max(lastArrivalNs,arrivalNs);
    }

    synchronized long deadline(long ptsUs) {
        if(!initialized)throw new IllegalStateException("Missing media timestamp");
        return ptsUs*1000L+offsetNs;
    }
}
