package local.remoteandroid.direct;

/** Experimental network feedback around VBR; it is not a native AVBR codec mode. */
final class AdaptiveBitrate {
    private final int ceiling;
    private int target,stable;
    private long baselineDelay=Long.MAX_VALUE,baselineRtt=Long.MAX_VALUE;
    private long windowDelay,windowPackets,lastPts=-1;
    private long previousDelay=Long.MIN_VALUE;
    AdaptiveBitrate(int ceiling){
        if(ceiling<500000||ceiling>40000000)throw new IllegalArgumentException("Invalid ceiling");
        this.ceiling=ceiling;target=ceiling;
    }
    synchronized int target(){return target;}
    synchronized void packet(long ptsUs,long arrivalNs){
        if(lastPts>=0&&ptsUs<lastPts){baselineDelay=Long.MAX_VALUE;previousDelay=Long.MIN_VALUE;}
        lastPts=ptsUs;
        long delay=arrivalNs/1000000L-ptsUs/1000L;
        baselineDelay=Math.min(baselineDelay,delay);
        windowDelay=Math.max(windowDelay,delay-baselineDelay);
        windowPackets++;
    }
    /** Called about every 3 seconds. Silence/static screens alone never lower bitrate. */
    synchronized int update(long rttMs){
        if(rttMs>=0)baselineRtt=Math.min(baselineRtt,rttMs);
        long packets=windowPackets,delay=windowDelay;windowPackets=0;windowDelay=0;
        boolean rttCongestion=rttMs>=0&&baselineRtt!=Long.MAX_VALUE&&rttMs-baselineRtt>120;
        boolean growing=previousDelay!=Long.MIN_VALUE&&delay>200&&delay>previousDelay+60;
        boolean congested=packets>=8&&(delay>300||growing||rttCongestion);
        boolean healthy=packets>=8&&delay<120&&!rttCongestion&&rttMs>=0;
        if(congested){target=Math.max(1000000,target*4/5);stable=0;}
        else if(healthy){if(++stable>=1){target=Math.min(ceiling,target+Math.max(500000,target/6));stable=0;}}
        else stable=0;
        if(packets>=8)previousDelay=delay;
        // After backing off, allow a stable lower-latency path to establish a new baseline.
        if(packets>=8&&delay>2000){baselineDelay+=delay;previousDelay=0;}
        return target;
    }
}
