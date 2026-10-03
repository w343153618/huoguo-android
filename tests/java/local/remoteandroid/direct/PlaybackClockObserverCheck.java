package local.remoteandroid.direct;

/** Owned policy-equivalence fixtures; no Android, SF, display or audio hardware claims. */
public final class PlaybackClockObserverCheck {
    private static void equal(long expected,long actual,String name){
        if(expected!=actual)throw new AssertionError(name+": "+actual+" != "+expected);
    }
    public static void main(String[] args){
        PlaybackClock formal=new PlaybackClock(80),observed=new PlaybackClock(80);
        MediaPresentationMetrics.StageDiagnostics d=new MediaPresentationMetrics.StageDiagnostics();
        // No first mapping or SF equivalence may be manufactured by attaching the observer.
        observed.setObserver(d::clockReanchor);
        equal(0,d.snapshot(System.nanoTime()).coverageMask,"attach does not invent clock observations");
        formal.observe(0,1_000_000_000L);observed.observe(0,1_000_000_000L);
        long initial=observed.deadline(0);
        for(int i=0;i<500;i++){
            long pts=i*16_667L,arrival=1_000_000_000L+i*16_667_000L;
            if(i>=200&&i<260)arrival+=600_000_000L;
            formal.observe(pts,arrival);observed.observe(pts,arrival);
            long delay=i>=40&&i<180?500_000_000L:25_000_000L;
            equal(formal.videoDeadline(pts,arrival+delay),observed.videoDeadline(pts,arrival+delay),"same returned schedule frame "+i);
            equal(formal.deadline(0),observed.deadline(0),"same shared mapping frame "+i);
            equal(formal.audioDeadline(pts),observed.audioDeadline(pts),"same audio mapping frame "+i);
        }
        MediaPresentationMetrics.StageSnapshot s=d.snapshot(System.nanoTime());
        equal(MediaPresentationMetrics.StageDiagnostics.CLOCK,s.coverageMask,"only clock observation hook coverage");
        equal(1,s.extraTotals[48],"initial absolute mapping not reported as delta");
        equal(observed.deadline(0)-initial,s.extraTotals[49],"signed primitive deltas match actual mapping changes");
        if(s.extraTotals[47]<=1)throw new AssertionError("changes beyond initial anchor not exercised");
        if(s.firstNs<=0||s.lastNs<s.firstNs)throw new AssertionError("observer uses fresh local System.nanoTime domain");
        long before=s.extraTotals[47];observed.setObserver(null);
        observed.observe(9_000_000L,100_000_000_000L);
        equal(before,d.snapshot(System.nanoTime()).extraTotals[47],"detaching observer disables further diagnostics");
        PlaybackClock audioFirst=new PlaybackClock(80);
        MediaPresentationMetrics.StageDiagnostics audio=new MediaPresentationMetrics.StageDiagnostics();
        audioFirst.setObserver(audio::clockReanchor);audioFirst.observeAudio(0,500_000_000L);
        s=audio.snapshot(System.nanoTime());equal(1,s.extraTotals[55],"audio initial anchor kind");equal(1,s.extraTotals[48],"audio initial delta unavailable");
        // Hold decay preserves the legacy return target computed before the decay.
        PlaybackClock decay=new PlaybackClock(80);
        MediaPresentationMetrics.StageDiagnostics decayMetrics=new MediaPresentationMetrics.StageDiagnostics();
        decay.setObserver(decayMetrics::clockReanchor);decay.observe(0,1_000_000_000L);decay.observe(300_000L,1_070_000_000L);
        equal(1,decayMetrics.snapshot(System.nanoTime()).extraTotals[54],"tiny offset decay observed");
        PlaybackClock hold=new PlaybackClock(80);
        MediaPresentationMetrics.StageDiagnostics holdMetrics=new MediaPresentationMetrics.StageDiagnostics();
        hold.setObserver(holdMetrics::clockReanchor);hold.observe(0,1_000_000_000L);
        for(int i=0;i<3;i++)hold.videoDeadline(0,1_300_000_000L);
        long prior=hold.deadline(0),returned=hold.videoDeadline(0,1_200_000_000L);
        equal(prior,returned,"do not change legacy pre-decay returned target");
        equal(prior-2_000_000L,hold.deadline(0),"actual mapping still decays 2ms");
        hold.observe(0,5_000_000_000L);
        s=holdMetrics.snapshot(System.nanoTime());equal(1,s.extraTotals[56],"decoder hold increase observed");
        equal(1,s.extraTotals[57],"decoder reanchor observed");equal(1,s.extraTotals[58],"hold decay observed");
        equal(1,s.extraTotals[53],"transport reanchor observed");
        System.out.println("PlaybackClockObserverCheck PASS: optional primitive observations preserve policy, missing initial delta, fresh local clock");
    }
}
