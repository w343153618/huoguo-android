package local.remoteandroid.direct;

/** Map scrcpy's monotonic media timestamps to one local audio/video clock. */
final class PlaybackClock {
    static final long BUFFER_NS=80_000_000L;
    private static final long PRESENT_LEAD_NS=3_000_000L;
    private final long bufferNs;
    private final long maxHoldNs;
    PlaybackClock(){this(80);}
    PlaybackClock(int bufferMs){
        int clamped = Math.max(30, Math.min(80, bufferMs));
        bufferNs=clamped*1_000_000L;
        maxHoldNs=Math.max(0L,80_000_000L-bufferNs);
    }
    private long offsetNs;
    private long lastArrivalNs;
    private long decoderHoldNs;
    private boolean initialized;
    private boolean videoAnchored;
    private int lateVideoFrames;

    synchronized void observe(long ptsUs,long arrivalNs) {
        long sourceNs=ptsUs*1000L;
        if(!videoAnchored || arrivalNs-(sourceNs+offsetNs)>250_000_000L
                || sourceNs+offsetNs-arrivalNs>1_000_000_000L) {
            // Recover from a large transport stall/reset without accumulating delay forever.
            offsetNs=arrivalNs+bufferNs-sourceNs;
            initialized=true;
            videoAnchored=true;
        } else {
            // A stall can leave the old mapping far behind fresh arriving frames.
            // Recover at <=5% of wall time, preserving a shared A/V mapping and jitter buffer.
            long elapsed=Math.min(1_000_000_000L,Math.max(0,arrivalNs-lastArrivalNs));
            long excess=offsetNs-(arrivalNs+bufferNs-sourceNs);
            if(excess>20_000_000L)offsetNs-=Math.min(excess-20_000_000L,elapsed/20);
        }
        lastArrivalNs=Math.max(lastArrivalNs,arrivalNs);
    }

    synchronized void observeAudio(long ptsUs,long arrivalNs) {
        // Audio may arrive first, but an earlier audio packet must not pull the
        // video presentation clock ahead of frames still being encoded/delivered.
        if(!initialized) {
            offsetNs=arrivalNs+bufferNs-ptsUs*1000L;
            lastArrivalNs=arrivalNs;
            initialized=true;
        }
    }

    synchronized long videoDeadline(long ptsUs,long decoderReadyNs) {
        long scheduled=deadline(ptsUs);
        long behind=decoderReadyNs+PRESENT_LEAD_NS-scheduled;
        if(behind>40_000_000L) {
            if(++lateVideoFrames>=3) {
                long holdRoom=Math.max(0L,maxHoldNs-decoderHoldNs);
                if(holdRoom>0) {
                    decoderHoldNs+=Math.min(behind,holdRoom);
                    scheduled=deadline(ptsUs);
                }
                // The bounded hold cannot recover a larger decoder backlog.
                // Re-anchor to the decoded frame instead of dropping every
                // subsequent output while the receive stream stays healthy.
                if(decoderReadyNs+PRESENT_LEAD_NS-scheduled>40_000_000L) {
                    offsetNs=decoderReadyNs+bufferNs-ptsUs*1000L-decoderHoldNs;
                    scheduled=deadline(ptsUs);
                    lateVideoFrames=0;
                }
            }
        } else {
            lateVideoFrames=0;
            if(behind<-10_000_000L&&decoderHoldNs>0) {
                decoderHoldNs=Math.max(0L,decoderHoldNs-2_000_000L);
            }
        }
        return scheduled;
    }

    synchronized long deadline(long ptsUs) {
        if(!initialized)throw new IllegalStateException("Missing media timestamp");
        return ptsUs*1000L+offsetNs+decoderHoldNs;
    }
}
