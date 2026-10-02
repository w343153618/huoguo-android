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
        if(args.length>0&&args[0].equals("--bench"))benchmark();
        System.out.println("DecoderStageMetricsProbe PASS (owned JVM only)");
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
