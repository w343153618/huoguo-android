package local.remoteandroid.direct;

/** Owned JVM fixtures; neither vendor codec, phone display nor network measurements. */
public final class DecoderStageMetricsProbe {
    private static void eq(long wanted,long actual,String name){if(wanted!=actual)throw new AssertionError(name+": "+actual+" != "+wanted);}
    public static void main(String[] args)throws Exception {
        MediaPresentationMetrics formal=new MediaPresentationMetrics(48000);
        if(formal.decoderStages!=null)throw new AssertionError("formal constructor enabled experimental diagnostics");
        MediaPresentationMetrics metrics=new MediaPresentationMetrics(48000,true);
        MediaPresentationMetrics.StageDiagnostics d=metrics.decoderStages;
        long origin=10_000_000_000L;
        d.inboxDepth(origin-1,0,0); // Explicitly counted before origin, never fabricated into a time segment.
        d.inboxOffer(origin); d.inboxDepth(origin+1,4,1000);
        d.inboxLoss(origin+500_000_000L,1,MediaPresentationMetrics.StageDiagnostics.FRAME_OVERFLOW,4,1000,0);
        d.waitingIdrDrop(origin+600_000_000L);
        d.reserve(origin+25_000_000L,1,origin,origin+25_000_000L,25,false,1,-1,-1,0);
        metrics.received(1,origin);metrics.inputQueued(1,origin+5_000_000L);
        metrics.scheduled(1,origin+15_000_000L,origin+80_000_000L,origin+20_000_000L);
        // No rendered callback is required: codec scheduling is an independent input/output stage observation.
        MediaPresentationMetrics.StageSnapshot s=d.snapshot(origin+1_000_000_000L);
        eq(origin,s.originNs,"first complete AU time origin");eq(1,s.beforeOrigin,"pre-origin observed, not discarded");
        eq(1,s.segments[0].length,"only touched segments exported");
        eq(1,s.segments[4][0],"overflow phase");eq(4,s.segments[5][0],"cleared depth phase");
        eq(1,s.segments[11][0],"input timeout phase");eq(1,s.segments[19][0],"waiting IDR phase");
        eq(60_000_000L,s.histograms[0].sum,"target minus release signed lead");
        eq(10_000_000L,s.histograms[1].sum,"ready minus actual input call start");
        eq(5_000_000L,s.histograms[2].sum,"output hold");eq(25_000_000L,s.histograms[3].sum,"dequeue reserve wait");
        eq(5_000_000L,s.histograms[4].sum,"input call start minus receive");
        eq(1,s.segments[20][0],"media input call start phase");
        eq(1,s.segments[23][0],"media reservation phase");
        eq(0,metrics.snapshot().video.size(),"schedule measurements do not invent presented frames");
        eq(2,s.events[0].length,"bounded abnormal events");eq(-1,s.events[4][1],"unknown reserve depth kept unknown");

        d.scheduled(2,MediaPresentationMetrics.MISSING,origin+2_000_000_000L,
                origin+1_980_000_000L,origin+2_010_000_000L);
        d.scheduled(3,origin+2_050_000_000L,origin+2_040_000_000L,
                origin+2_080_000_000L,origin+2_041_000_000L);
        s=d.snapshot(origin+3_000_000_000L);
        eq(1,s.totals[11],"missing input not substituted");eq(1,s.totals[12],"negative ready-input rejected");
        eq(1,s.histograms[1].invalid,"negative stage count");eq(3,s.histograms[0].valid,"signed lead samples retained");
        eq(-30_000_000L,s.histograms[0].min,"negative lead kept in histogram");
        eq(1,s.histograms[0].counts[2],"signed -30ms inclusive bucket");
        eq(1,s.segments[15][2],"missing input phase");
        eq(1,s.segments[21][2],"invalid input-ready phase");

        for(int i=0;i<100;i++)d.inboxLoss(origin+3_000_000_000L+i,100+i,
                MediaPresentationMetrics.StageDiagnostics.WORKER_EXPIRED,0,0,i);
        s=d.snapshot(origin+4_000_000_000L);
        eq(64,s.events[0].length,"fixed event capacity");eq(102,s.eventObserved,"all event count");
        eq(38,s.eventsEvicted,"eviction coverage explicit");eq(136,s.events[1][0],"only oldest events evicted");
        d.inboxDepth(origin+120_000_000_000L,1,1);
        s=d.snapshot(origin+121_000_000_000L);
        eq(1,s.afterCapacity,"observations after bounded segment coverage counted");
        eq(4,s.segments[0].length,"no fabricated trailing empty segments");
        long old=s.segments[0][0];d.inboxOffer(origin);eq(old,s.segments[0][0],"snapshot cannot alias live arrays");

        MediaPresentationMetrics.StageDiagnostics concurrent=new MediaPresentationMetrics.StageDiagnostics();
        concurrent.establishOrigin(origin);Thread[] threads=new Thread[3];
        for(int t=0;t<threads.length;t++){final int worker=t;threads[t]=new Thread(()->{
            for(int i=0;i<1000;i++)concurrent.inboxLoss(origin+worker*1_000_000_000L+i,i,
                    MediaPresentationMetrics.StageDiagnostics.FRAME_OVERFLOW,4,100,worker);
        });threads[t].start();}for(Thread t:threads)t.join();
        s=concurrent.snapshot(origin+4_000_000_000L);eq(3000,s.eventObserved,"concurrent event count");
        eq(2936,s.eventsEvicted,"concurrent fixed ring coverage");eq(12000,s.totals[3],"concurrent cleared frame accounting");
        eq(3000,s.totals[2],"concurrent overflow accounting");
        extendedPhases();
        if(args.length>0&&args[0].equals("--bench")){benchmark();concurrentBenchmark();}
        System.out.println("DecoderStageMetricsProbe PASS (owned JVM only)");
    }
    private static void extendedPhases(){
        MediaPresentationMetrics.StageDiagnostics d=new MediaPresentationMetrics.StageDiagnostics();
        long t=300_000_000_000_000L;d.establishOrigin(t);
        d.configureStarted(t-50_000_000L,1,t-60_000_000L);
        d.configureFinished(t+150_000_000L,1,true);
        d.inboxOffer(t,t+1);d.inboxOffer(t+20_000_000L,t+21_000_000L);
        d.inboxTaken(t+200_000_000L,1,t,3,900);d.inboxTaken(t+250_000_000L,2,t+20_000_000L,2,600);
        d.consumerStarted(t+200_000_000L,1,1_000_000L);
        d.consumerFinished(t+230_000_000L,1,6_000_000L);
        d.consumerStarted(t+240_000_000L,2);d.consumerFinished(t+250_000_000L,2);
        d.consumerStarted(t+260_000_000L,3,0);d.consumerFinished(t+270_000_000L,3,20_000_000L);
        d.inputCopyStarted(t+300_000_000L,4,false);d.inputCopyFinished(t+305_000_000L,4,false,true);
        d.inputCallStarted(t+306_000_000L,4,false);d.inputCallFinished(t+336_000_000L,4,false,true);
        d.reserveGuard(t+400_000_000L,5,3,0,2);
        d.reserve(t+400_001_000L,5,t+400_000_000L,t+400_001_000L,0,false,1,-1,-1,0);
        d.fecPollObserved(t+500_000_000L);d.fecPollObserved(t+600_000_000L);
        d.fecException(t+600_000_000L,-1,1,2);d.fecException(t+600_000_000L,-1,3,7);
        d.fecException(t+600_000_000L,-1,1,-1);
        d.clockReanchor(t+700_000_000L,1,MediaPresentationMetrics.MISSING);
        d.clockReanchor(t+710_000_000L,3,-50_000L);
        d.clockReanchor(t+720_000_000L,6,40_000_000L);
        d.configureFinished(t+800_000_000L,99,false); // Explicit missing begin.
        d.inputCopyFinished(t+801_000_000L,99,false,false);
        d.consumerStarted(t+900_000_000L,6,9_000_000L); // Active at snapshot, not silently finished.
        MediaPresentationMetrics.StageSnapshot s=d.snapshot(t+1_000_000_000L);
        eq(255,s.coverageMask,"all primitive hook paths observed");
        eq(200_000_000L,s.extraHistograms[0].sum,"configure wall includes startup");
        eq(1,s.beforeOrigin,"configure pre-origin retained");eq(1,s.extraTotals[3],"unmatched configure finish");
        eq(21_000_000L-1,s.extraHistograms[1].sum,"offer uses operation time not receive");
        eq(50_000_000L,s.extraHistograms[2].sum,"take cadence");eq(430_000_000L,s.extraHistograms[3].sum,"take age");
        eq(1,s.extraTotals[14],"valid thread CPU sample count");eq(1,s.extraTotals[15],"CPU missing explicit");
        eq(1,s.extraTotals[16],"CPU greater than wall invalid");eq(25_000_000L,s.extraHistograms[5].sum,"nonCPU elapsed, not scheduler attribution");
        eq(5_000_000L,s.extraHistograms[6].sum,"copy call span");eq(30_000_000L,s.extraHistograms[7].sum,"nonempty queue call span");
        eq(1,s.extraTotals[30],"age flag");eq(1,s.extraTotals[31],"stale epoch simultaneous flag");
        eq(2,s.extraTotals[60],"zero-delta FEC polls retained");eq(100_000_000L,s.extraTotals[46],"actual FEC poll interval");
        eq(9,s.extraTotals[36],"FEC counter delta sum");eq(1,s.extraTotals[43],"invalid FEC delta explicit");
        eq(3,s.extraTotals[47],"all actual clock changes counted");eq(1,s.extraTotals[48],"initial mapping delta unknown");
        eq(39_950_000L,s.extraTotals[49],"signed mapping changes");eq(40_050_000L,s.extraTotals[50],"absolute mapping changes");
        eq(t+900_000_000L,s.phaseState[3],"active consumer span retained");
        boolean timeout=false;for(int i=0;i<s.events[0].length;i++)if(s.events[2][i]==3){
            eq(3,s.events[8][i],"timeout entry age and stale flags");eq(0,s.events[6][i],"frame epoch");
            eq(2,s.events[12][i],"current epoch");eq(0,s.events[7][i],"no vendor dequeue poll");timeout=true;}
        if(!timeout)throw new AssertionError("timeout event missing");
        long snapshotOld=s.extraTotals[47];d.clockReanchor(t+950_000_000L,7,-2_000_000L);
        eq(snapshotOld,s.extraTotals[47],"extra snapshot arrays not aliased");
    }
    /** Contended 3-owner numeric calls with matched arithmetic baseline; no Android thread CPU API here. */
    private static void concurrentBenchmark()throws Exception{
        for(int pass=0;pass<4;pass++)for(boolean enabled:new boolean[]{false,true}){
            final MediaPresentationMetrics.StageDiagnostics d=enabled?new MediaPresentationMetrics.StageDiagnostics():null;
            if(d!=null)d.establishOrigin(300_000_000_000_000L);
            java.util.concurrent.CountDownLatch ready=new java.util.concurrent.CountDownLatch(3),start=new java.util.concurrent.CountDownLatch(1);
            Thread[] workers=new Thread[3];long[] checksums=new long[3];int count=pass==0?5000:50000;
            for(int role=0;role<3;role++){final int r=role;workers[role]=new Thread(()->{
                ready.countDown();try{start.await();}catch(InterruptedException e){throw new AssertionError(e);}
                long checksum=0;for(int i=0;i<count;i++){
                    long now=300_000_000_000_000L+i*100_000L;checksum^=now+i*31+r;
                    if(d!=null){if(r==0){d.inboxOffer(now,now+100);d.inboxDepth(now+100,2,10000);}
                        else if(r==1){d.inboxTaken(now+1000,i,now,1,5000);d.consumerStarted(now+1000,i,i*100L);
                            d.inputCopyStarted(now+2000,i,false);d.inputCopyFinished(now+3000,i,false,true);
                            d.inputCallStarted(now+4000,i,false);d.inputCallFinished(now+5000,i,false,true);
                            d.consumerFinished(now+6000,i,i*100L+3000);}
                        else {d.scheduled(i,now+5000,now+10000,now+80000000,now+11000);
                            d.clockReanchor(now+11000,3,-100);}}
                }checksums[r]=checksum;
            });workers[role].start();}ready.await();long begin=System.nanoTime();start.countDown();
            for(Thread thread:workers)thread.join();long elapsed=System.nanoTime()-begin;
            sink=checksums[0]^checksums[1]^checksums[2];if(d!=null)sink^=d.snapshot(System.nanoTime()).observations;
            System.out.println("three_owner_host_jvm_pass="+pass+" diagnostics="+(enabled?1:0)+" rounds_per_owner="+count+
                    " elapsed_ns="+elapsed+" ns_per_3_owner_round="+(elapsed/count));
        }
    }
    private static volatile long sink;
    private static void benchmark(){
        for(int pass=0;pass<4;pass++){
            MediaPresentationMetrics.StageDiagnostics d=new MediaPresentationMetrics.StageDiagnostics();
            long begin=System.nanoTime();int count=pass==0?20000:200000;
            for(int i=0;i<count;i++){
                long received=10_000_000_000L+i*166667L;
                d.inboxOffer(received);d.inboxDepth(received+1,2,10000);
                d.reserve(received+1_000_000L,i,received,received+1_000_000L,1,false,0,-1,-1,0);
                d.inputQueued(received,received+1_000_000L);
                d.scheduled(i,received+1_000_000L,received+10_000_000L,received+80_000_000L,received+10_100_000L);
            }
            long elapsed=System.nanoTime()-begin;sink=d.snapshot(System.nanoTime()).observations;
            System.out.println("stage_only_host_jvm_pass="+pass+" frames="+count+" elapsed_ns="+elapsed+" ns_per_frame="+(elapsed/count));
        }
    }
}
