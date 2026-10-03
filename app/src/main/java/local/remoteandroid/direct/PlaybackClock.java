package local.remoteandroid.direct;

/** Map scrcpy's monotonic media timestamps to one local audio/video clock. */
final class PlaybackClock {
    static final long BUFFER_NS=80_000_000L;
    private static final long MAX_DECODER_HOLD_NS=30_000_000L;
    private static final long PRESENT_LEAD_NS=3_000_000L;
    private final long bufferNs;
    private final boolean decoderReanchorEnabled;
    private long avSyncOffsetNs;
    // Independent primitive observer keeps this class compilable without Android or metrics.
    interface Observer { void onClockShift(long phoneNs,int kind,long deltaNs); }
    static final int VIDEO_ANCHOR=1,TRANSPORT_REANCHOR=2,OFFSET_DECAY=3,AUDIO_ANCHOR=4,
            DECODER_HOLD_GAIN=5,DECODER_REANCHOR=6,DECODER_HOLD_DECAY=7;
    private Observer observer;
    synchronized void setObserver(Observer observer){this.observer=observer;}
    private void mappingChanged(int kind,long oldOffset,long oldHold,boolean wasInitialized){
        if(observer==null)return;
        long delta=wasInitialized?(offsetNs-oldOffset)+(decoderHoldNs-oldHold):Long.MIN_VALUE;
        if(delta==0)return;
        // Sampling time is when the mapping changed, not a packet's earlier arrival timestamp.
        observer.onClockShift(System.nanoTime(),kind,delta);
    }
    PlaybackClock(){this(80,0);}
    PlaybackClock(int bufferMs){this(bufferMs,0);}
    PlaybackClock(int bufferMs,int avSyncOffsetMs){
        this(bufferMs,avSyncOffsetMs,true);
    }
    /** Experimental false mode isolates decoder-induced shared clock shifts.
     * Arrival mapping, startup recovery and audio calibration remain unchanged.
     * All existing constructors preserve the legacy true behavior.
     */
    PlaybackClock(int bufferMs,int avSyncOffsetMs,boolean decoderReanchorEnabled){
        if(bufferMs<30||bufferMs>100)throw new IllegalArgumentException("Invalid buffer");
        bufferNs=bufferMs*1_000_000L;
        avSyncOffsetNs=avSyncOffsetMs*1_000_000L;
        this.decoderReanchorEnabled=decoderReanchorEnabled;
    }
    synchronized void setAvSyncOffsetMs(int offsetMs){
        avSyncOffsetNs=offsetMs*1_000_000L;
    }
    synchronized long audioDeadline(long ptsUs){
        return deadline(ptsUs)+avSyncOffsetNs;
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
            long oldOffset=offsetNs,oldHold=decoderHoldNs;boolean wasInitialized=initialized;
            int kind=videoAnchored?TRANSPORT_REANCHOR:VIDEO_ANCHOR;
            offsetNs=arrivalNs+bufferNs-sourceNs;
            initialized=true;
            videoAnchored=true;
            mappingChanged(kind,oldOffset,oldHold,wasInitialized);
        } else {
            // A stall can leave the old mapping far behind fresh arriving frames.
            // Recover at <=5% of wall time, preserving a shared A/V mapping and jitter buffer.
            long elapsed=Math.min(1_000_000_000L,Math.max(0,arrivalNs-lastArrivalNs));
            long excess=offsetNs-(arrivalNs+bufferNs-sourceNs);
            if(excess>20_000_000L){long oldOffset=offsetNs;
                offsetNs-=Math.min(excess-20_000_000L,elapsed/20);
                mappingChanged(OFFSET_DECAY,oldOffset,decoderHoldNs,true);}
        }
        lastArrivalNs=Math.max(lastArrivalNs,arrivalNs);
    }

    synchronized void observeAudio(long ptsUs,long arrivalNs) {
        // Audio may arrive first, but an earlier audio packet must not pull the
        // video presentation clock ahead of frames still being encoded/delivered.
        if(!initialized) {
            long oldOffset=offsetNs,oldHold=decoderHoldNs;
            offsetNs=arrivalNs+bufferNs-ptsUs*1000L;
            lastArrivalNs=arrivalNs;
            initialized=true;
            mappingChanged(AUDIO_ANCHOR,oldOffset,oldHold,false);
        }
    }

    synchronized long videoDeadline(long ptsUs,long decoderReadyNs) {
        long scheduled=deadline(ptsUs);
        // The probe may disable only this feedback path. observe() still owns
        // the common arrival-to-source mapping; do not alter its slow decay in
        // the same experiment. A late decoded frame can remain late here and
        // the renderer applies its existing late-output policy.
        if(!decoderReanchorEnabled)return scheduled;
        long behind=decoderReadyNs+PRESENT_LEAD_NS-scheduled;
        if(behind>40_000_000L) {
            if(++lateVideoFrames>=3) {
                long holdRoom=Math.max(0L,MAX_DECODER_HOLD_NS-decoderHoldNs);
                if(holdRoom>0) {
                    long oldHold=decoderHoldNs;
                    decoderHoldNs+=Math.min(behind,holdRoom);
                    scheduled=deadline(ptsUs);
                    mappingChanged(DECODER_HOLD_GAIN,offsetNs,oldHold,true);
                }
                // The bounded hold cannot recover a larger decoder backlog.
                // Re-anchor to the decoded frame instead of dropping every
                // subsequent output while the receive stream stays healthy.
                if(decoderReadyNs+PRESENT_LEAD_NS-scheduled>40_000_000L) {
                    long oldOffset=offsetNs;
                    offsetNs=decoderReadyNs+PRESENT_LEAD_NS-ptsUs*1000L-decoderHoldNs;
                    scheduled=deadline(ptsUs);
                    mappingChanged(DECODER_REANCHOR,oldOffset,decoderHoldNs,true);
                    lateVideoFrames=0;
                }
            }
        } else {
            lateVideoFrames=0;
            if(behind<-10_000_000L&&decoderHoldNs>0) {
                long oldHold=decoderHoldNs;
                decoderHoldNs=Math.max(0L,decoderHoldNs-2_000_000L);
                mappingChanged(DECODER_HOLD_DECAY,offsetNs,oldHold,true);
                // Retain the existing return value: scheduled was calculated before this decay.
            }
        }
        return scheduled;
    }

    synchronized long deadline(long ptsUs) {
        if(!initialized)throw new IllegalStateException("Missing media timestamp");
        return ptsUs*1000L+offsetNs+decoderHoldNs;
    }
}
