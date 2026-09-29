package local.remoteandroid.direct;
public final class PlaybackClockCheck {
    static void check(boolean value){if(!value)throw new AssertionError();}
    public static void main(String[] args){
        PlaybackClock c=new PlaybackClock();long source=9_000_000L,arrival=20_000_000_000L;
        c.observe(source,arrival);check(c.deadline(source)==arrival+80_000_000L);
        for(int i=1;i<120;i++){
            long pts=source+i*16667L;
            c.observe(pts,arrival+i*16667000L+(i%3)*15_000_000L);
            check(c.deadline(pts)==arrival+80_000_000L+i*16667000L);
        }
        // Both audio and video use the same source-to-client mapping, not arrival order.
        check(c.deadline(source+1_000_000L)-c.deadline(source)==1_000_000_000L);
        long stalled=arrival+4_000_000_000L;
        c.observe(source+2_000_000L,stalled);
        check(c.deadline(source+2_000_000L)==stalled+80_000_000L);
        c.observe(0,stalled+100_000_000L);
        check(c.deadline(0)==stalled+180_000_000L);
        PlaybackClock buffered=new PlaybackClock(100);buffered.observe(source,arrival);check(buffered.deadline(source)==arrival+100_000_000L);
        System.out.println("PlaybackClock: cadence, jitter, shared A/V mapping, stall/reset PASS");
    }
}
