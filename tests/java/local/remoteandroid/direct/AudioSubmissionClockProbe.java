package local.remoteandroid.direct;

/** Offline arithmetic boundaries; no Android device or audible-output assertion. */
public final class AudioSubmissionClockProbe {
    private static final long NOW = 10_000_000_000L;
    private static void same(long actual,long expected,String reason) {
        if(actual!=expected)throw new AssertionError(reason+": "+actual+" != "+expected);
    }
    private static void invalid(long now,long stamp,long written,long position,int rate,String reason) {
        same(AudioSubmissionClock.timestampQueueTailNs(now,stamp,written,position,rate),
            AudioSubmissionClock.UNAVAILABLE,reason);
    }
    public static void main(String[] args) {
        // Timestamp is 50ms old, 100ms of PCM followed that position: tail is 50ms ahead.
        same(AudioSubmissionClock.timestampQueueTailNs(NOW,NOW-50_000_000L,7200,2400,48000),
            NOW+50_000_000L,"timestamp age is already part of queue prediction");
        same(AudioSubmissionClock.timestampQueueTailNs(NOW,NOW-100_000_000L,2400,2400,48000),
            NOW,"empty queue cannot append in the past");
        same(AudioSubmissionClock.timestampQueueTailNs(NOW,NOW-100_000_000L,4800,2400,48000),
            NOW,"timestamp tail passed during an underrun");
        same(AudioSubmissionClock.timestampQueueTailNs(NOW,NOW,1,0,48000),
            NOW+20_833L,"fractional sample duration floors once");
        same(AudioSubmissionClock.timestampQueueTailNs(NOW,NOW,4_294_967_296L+4800,4_294_967_296L,48000),
            NOW+100_000_000L,"unwrapped per-track long position");
        same(AudioSubmissionClock.timestampQueueTailNs(1,1,10_000_000_000L,0,48000),
            208_333_333_333_334L,"long valid queue avoids intermediate product overflow");
        invalid(0,1,0,0,48000,"invalid observation clock");
        invalid(NOW,0,0,0,48000,"missing timestamp");
        invalid(NOW,NOW+1,0,0,48000,"future timestamp");
        invalid(NOW,NOW-500_000_001L,0,0,48000,"stale timestamp");
        invalid(NOW,NOW,100,101,48000,"position ahead of writes");
        invalid(NOW,NOW,0,-1,48000,"negative position");
        invalid(NOW,NOW,-1,0,48000,"negative written count");
        invalid(NOW,NOW,1,0,0,"invalid rate");
        invalid(NOW,NOW,Long.MAX_VALUE,0,1,"duration overflow");
        invalid(Long.MAX_VALUE,Long.MAX_VALUE,1,0,48000,"absolute timestamp overflow");
        System.out.println("AudioSubmissionClockProbe PASS");
    }
}
