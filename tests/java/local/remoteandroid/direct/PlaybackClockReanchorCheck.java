package local.remoteandroid.direct;

/** Offline single-variable clock experiment. No Android/JSON dependencies. */
public final class PlaybackClockReanchorCheck {
    private static void equal(long wanted,long actual,String reason){
        if(wanted!=actual)throw new AssertionError(reason+": "+actual+" != "+wanted);
    }
    private static void check(boolean value,String reason){if(!value)throw new AssertionError(reason);}
    public static void main(String[] args){
        PlaybackClock legacy=new PlaybackClock(60,25),explicit=new PlaybackClock(60,25,true);
        legacy.observeAudio(0,900_000_000L);explicit.observeAudio(0,900_000_000L);
        equal(legacy.audioDeadline(0),explicit.audioDeadline(0),"legacy constructor audio-first equivalence");
        // Include healthy frames, 500 ms decoder backlog, slow mapping recovery
        // and transport stalls. This exercises the existing feedback algorithm
        // through both the legacy and explicit-true constructor entrypoints.
        for(int i=0;i<500;i++){
            long pts=i*16_667L,arrival=1_000_000_000L+i*16_667_000L;
            if(i>=200&&i<260)arrival+=600_000_000L;
            legacy.observe(pts,arrival);explicit.observe(pts,arrival);
            long delay=i>=40&&i<180?500_000_000L:25_000_000L;
            equal(legacy.videoDeadline(pts,arrival+delay),explicit.videoDeadline(pts,arrival+delay),"legacy deadline frame "+i);
            equal(legacy.deadline(pts+16_667L),explicit.deadline(pts+16_667L),"legacy future mapping frame "+i);
            equal(legacy.audioDeadline(pts),explicit.audioDeadline(pts),"legacy shared audio mapping frame "+i);
        }
        PlaybackClock arrivalOnly=new PlaybackClock(60,25,false);
        arrivalOnly.observe(0,1_000_000_000L);
        long initial=arrivalOnly.deadline(0);
        for(int i=0;i<200;i++){
            long pts=i*16_667L,arrival=1_000_000_000L+i*16_667_000L;
            arrivalOnly.observe(pts,arrival);
            equal(arrival+60_000_000L,arrivalOnly.videoDeadline(pts,arrival+500_000_000L),"late decoder cannot move false clock frame "+i);
            equal(initial,arrivalOnly.deadline(0),"no hidden decoder hold or reanchor frame "+i);
            equal(arrival+85_000_000L,arrivalOnly.audioDeadline(pts),"audio remains on common arrival timeline frame "+i);
        }
        long freshPts=200*16_667L;
        long freshTarget=1_000_000_000L+freshPts*1000L+60_000_000L;
        equal(freshTarget,arrivalOnly.videoDeadline(freshPts,freshTarget-20_000_000L),"fresh frame unaffected by previous decoder backlog");
        equal(freshTarget+25_000_000L,arrivalOnly.audioDeadline(freshPts),"fresh audio remains calibrated");
        arrivalOnly.setAvSyncOffsetMs(-25);
        equal(freshTarget-25_000_000L,arrivalOnly.audioDeadline(freshPts),"false mode keeps audio calibration setter");

        // The arrival observer's existing transport reset is deliberately NOT
        // disabled. False means no decoder feedback, not an immutable clock.
        arrivalOnly.observe(freshPts,6_000_000_000L);
        equal(6_060_000_000L,arrivalOnly.deadline(freshPts),"arrival transport reanchor still applies");

        // A startup burst of old frames still exhibits the legacy slow mapping
        // decay in false mode. Keep this independent issue for a later A/B.
        PlaybackClock decayLegacy=new PlaybackClock(60),decayFalse=new PlaybackClock(60,0,false);
        decayLegacy.observe(0,1_000_000_000L);decayFalse.observe(0,1_000_000_000L);
        decayLegacy.observe(300_000L,1_070_000_000L);decayFalse.observe(300_000L,1_070_000_000L);
        equal(decayLegacy.deadline(300_000L),decayFalse.deadline(300_000L),"startup arrival decay unchanged");
        check(decayFalse.deadline(300_000L)>1_300_000_000L,"startup burst mapping intentionally not fixed here");
        PlaybackClock hundred=new PlaybackClock(100,25,false);
        hundred.observe(0,1_000_000_000L);
        equal(1_100_000_000L,hundred.videoDeadline(0,1_500_000_000L),"100ms buffer retains arrival target with late decoder");
        equal(1_125_000_000L,hundred.audioDeadline(0),"100ms buffer shares calibrated audio target");
        for(int invalid:new int[]{29,101,120}){
            try{new PlaybackClock(invalid,0,false);throw new AssertionError("invalid buffer accepted");}
            catch(IllegalArgumentException expected){}
        }
        System.out.println("PlaybackClockReanchorCheck PASS: legacy equivalence, decoder-only toggle, shared audio, arrival recovery unchanged");
    }
}
