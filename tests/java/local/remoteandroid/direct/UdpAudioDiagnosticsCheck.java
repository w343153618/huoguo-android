package local.remoteandroid.direct;

import java.util.concurrent.CountDownLatch;

/** Offline checks of the actual audio diagnostic accumulator, without codecs,
 * Android calls, payloads, a phone, or any change to playout decisions.
 */
public final class UdpAudioDiagnosticsCheck {
    private static int checks;
    private static void check(boolean okay,String name){if(!okay)throw new AssertionError(name);checks++;}
    private static void invariant(long[] counts){
        if(counts.length!=7||counts[0]!=counts[1]+counts[2]+counts[3]||counts[4]!=counts[5]+counts[6])
            throw new AssertionError("A snapshot must preserve both legacy totals and split reason sums");
    }
    public static void main(String[] args)throws Exception{
        UdpAudioReceiver.AudioDiagnostics metrics=new UdpAudioReceiver.AudioDiagnostics();
        invariant(metrics.lateCounts());check(metrics.lateCounts()[0]==0,"empty legacy worker total");
        for(int i=0;i<3;i++)for(int j=0;j<=i;j++)metrics.workerLate(i);
        metrics.pcmLate(UdpAudioReceiver.AudioDiagnostics.PCM_TARGET_LATE);
        metrics.pcmLate(UdpAudioReceiver.AudioDiagnostics.PCM_WRITE_TIMEOUT);
        metrics.pcmLate(UdpAudioReceiver.AudioDiagnostics.PCM_WRITE_TIMEOUT);
        long[] initial=metrics.lateCounts();invariant(initial);
        check(initial[0]==6&&initial[1]==1&&initial[2]==2&&initial[3]==3,"distinct worker reasons keep exact total");
        check(initial[4]==3&&initial[5]==1&&initial[6]==2,"PCM reasons keep exact total");
        initial[0]=12345;check(metrics.lateCounts()[0]==6,"reader cannot mutate live counters");
        boolean invalid=false;try{metrics.workerLate(3);}catch(IllegalArgumentException expected){invalid=true;}
        check(invalid&&metrics.lateCounts()[0]==6,"invalid worker reason cannot alter totals");
        invalid=false;try{metrics.pcmLate(-1);}catch(IllegalArgumentException expected){invalid=true;}
        check(invalid&&metrics.lateCounts()[4]==3,"invalid PCM reason cannot alter totals");

        UdpAudioReceiver.NumericTiming timing=new UdpAudioReceiver.NumericTiming();
        check(timing.values()[0]==0&&timing.meanNs()==0,"empty timing has no invented sample");
        timing.add(-80_000_000L);timing.add(-80_000_001L);timing.add(-20_000_000L);
        timing.add(0);timing.add(2_000_000L);timing.add(2_000_001L);timing.add(640_000_000L);timing.add(640_000_001L);
        long[] values=timing.values();check(values[0]==8&&values[1]==-80_000_001L&&values[2]==640_000_001L,"signed timing min/max preserve direction");
        check(timing.bins[0]==2&&timing.bins[1]==1&&timing.bins[2]==1,"negative and zero inclusive boundaries");
        check(timing.bins[3]==1&&timing.bins[4]==1,"positive boundary does not leak to adjacent bin");
        check(timing.bins[11]==1&&timing.bins[12]==1,"large wait overflow bin stays bounded");
        long binSum=0;for(long n:timing.bins)binSum+=n;check(binSum==8,"each timing observation enters exactly one fixed bin");
        int capacity=timing.bins.length;for(int i=0;i<100000;i++)timing.add(i);
        check(timing.bins.length==capacity&&timing.values()[0]==100008,"long runs retain aggregate counts with fixed storage");
        UdpAudioReceiver.NumericTiming large=new UdpAudioReceiver.NumericTiming();
        large.add(Long.MAX_VALUE);large.add(Long.MAX_VALUE);
        check(Double.isFinite(large.meanNs())&&large.meanNs()>0,"large diagnostic durations do not overflow signed sums");

        metrics.workerStart(81_000_000L,70_000_000L);metrics.input(60_000_000L,-1);
        metrics.input(2_000_000L,21_333_000L);metrics.output(310_000_000L);metrics.target(-90_000_000L);
        metrics.tail(200_000_000L,-150_000_000L,true);metrics.waited(210_000_000L);
        metrics.slept(10_000_000L,35_000_000L);metrics.wrote(1_000_000L);
        check(metrics.firstArrivalAge.values()[2]==81_000_000L&&metrics.completeToWorker.values()[2]==70_000_000L,"worker age keeps reorder and first-arrival domains distinct");
        check(metrics.inputWait.values()[0]==2&&metrics.inputPtsStep.values()[0]==1,"first input has no fabricated PTS step");
        check(metrics.outputHold.values()[2]==310_000_000L&&metrics.targetLead.values()[1]==-90_000_000L,"output hold and target lateness remain separate");
        check(metrics.targetMinusTail.values()[2]==200_000_000L&&metrics.freshTargetDelta.values()[1]==-150_000_000L,"fresh deadline changes preserve signed difference only");
        check(metrics.sleepOvershoot.values()[2]==25_000_000L,"sleep overshoot separates requested and observed wait");
        metrics.slept(10_000_000L,9_000_000L);check(metrics.sleepOvershoot.values()[1]==0,"early wake is not negative oversleep");

        final Throwable[] failure={null};
        CountDownLatch writerEntered=new CountDownLatch(1),readerEntered=new CountDownLatch(1);
        Thread writer=new Thread(()->{
            try{for(int i=0;i<20000;i++){
                metrics.workerLate(i%3);metrics.pcmLate(i%2);
                if(i==0){writerEntered.countDown();readerEntered.await();}
            }}
            catch(Throwable error){failure[0]=error;}
        });
        writer.start();writerEntered.await();invariant(metrics.lateCounts());int readSnapshots=1;readerEntered.countDown();
        while(writer.isAlive()){invariant(metrics.lateCounts());readSnapshots++;}
        writer.join();if(failure[0]!=null)throw new AssertionError(failure[0]);
        long[] finalCounts=metrics.lateCounts();invariant(finalCounts);
        check(finalCounts[0]==20006&&finalCounts[4]==20003,"concurrent reads cannot lose diagnostic increments");
        check(readSnapshots>0,"sum invariant checked during concurrent recording");
        System.out.println("PASS "+checks+" bounded audio diagnostics checks (offline; no APK, codec, device, or acoustics)");
    }
}
