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
        PlaybackClock buffered=new PlaybackClock(80);buffered.observe(source,arrival);check(buffered.deadline(source)==arrival+80_000_000L);
        PlaybackClock low=new PlaybackClock(30);low.observe(source,arrival);check(low.deadline(source)==arrival+30_000_000L);
        PlaybackClock hundred=new PlaybackClock(100);hundred.observe(source,arrival);
        check(hundred.videoDeadline(source,arrival+25_000_000L)==arrival+100_000_000L);
        check(hundred.audioDeadline(source)==arrival+100_000_000L);
        for(int ms:new int[]{29,101,201})try{new PlaybackClock(ms);throw new AssertionError();}catch(IllegalArgumentException expected){}
        // A 600ms stall below the timestamp-reset threshold must not leave playback behind forever.
        PlaybackClock recovery=new PlaybackClock(80);recovery.observe(source,arrival);
        recovery.observe(source+2_000_000L,arrival+2_600_000_000L);
        long previous=recovery.deadline(source+2_700_000L);
        for(int i=0;i<350;i++){
            long pts=source+2_700_000L+i*40_000L,now=arrival+2_700_000_000L+i*40_000_000L;
            recovery.observe(pts,now);long next=recovery.deadline(pts);
            if(i>0)check(next-previous>=38_000_000L&&next-previous<=40_000_000L);
            check(next-now>=80_000_000L);previous=next;
        }
        long last=arrival+2_700_000_000L+349*40_000_000L;
        check(previous-last<=100_000_000L);
        // Delayed callback observation must not create a negative catch-up step.
        long fixed=recovery.deadline(source+20_000_000L);
        recovery.observe(source+2_700_000L+349*40_000L,last-1_000_000L);
        check(recovery.deadline(source+20_000_000L)==fixed);
        System.out.println("PlaybackClock: cadence, jitter, shared A/V mapping, stall/reset, 30ms selection and gradual backlog recovery PASS");
    }
}
