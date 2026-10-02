package local.remoteandroid.direct;

import java.nio.ByteBuffer;
import java.util.IdentityHashMap;
import java.util.concurrent.CountDownLatch;

/** Actual fixed-pool queue checked on the JVM; no codecs, phones or acoustics. */
public final class BoundedPcmQueueCheck {
    private static int checks;
    private static void check(boolean okay,String name){if(!okay)throw new AssertionError(name);checks++;}
    private static ByteBuffer samples(int size){ByteBuffer b=ByteBuffer.allocate(size);for(int i=0;i<size;i++)b.put((byte)i);return b.flip();}
    private static boolean offer(BoundedPcmQueue q,int bytes,long clock){return q.offerCopy(samples(bytes),1234,clock+40_000_000L,clock,1f,clock);}
    public static void main(String[] args)throws Exception{
        long now=10_000_000_000L;
        BoundedPcmQueue q=new BoundedPcmQueue();ByteBuffer original=samples(4096);original.position(4);
        check(q.offerCopy(original,5,now+40_000_000L,now,0.5f,now),"first PCM admission");
        check(original.position()==4,"copy does not consume codec buffer position");
        original.put(4,(byte)99);
        BoundedPcmQueue.Frame frame=q.poll(now+1);
        check(frame!=null&&frame.size()==4092&&frame.pcm().get(0)==4,"owned PCM copy does not alias codec buffer");
        check(frame.ptsUs==5&&frame.targetNs==now+40_000_000L&&frame.dequeuedNs==now&&frame.gain==0.5f,"source target and gain preserved exactly");
        check(q.snapshot().pendingBytes==0&&q.snapshot().consumingFrames==1,"consumption transfers queue ownership");
        boolean rejected=false;try{q.poll(now+2);}catch(IllegalStateException expected){rejected=true;}
        // An empty poll has no record to transfer and is harmless.
        check(!rejected,"empty polling cannot manufacture an extra owned frame");
        q.release(frame);rejected=false;try{frame.pcm();}catch(IllegalStateException expected){rejected=true;}
        check(rejected,"released record cannot be read through queue API");
        rejected=false;try{q.release(frame);}catch(IllegalArgumentException expected){rejected=true;}
        check(rejected,"double release rejected");

        for(int i=0;i<4;i++)check(offer(q,4096,now+i),"bounded four-record admission");
        check(!offer(q,4096,now+5),"fifth queued record rejected immediately");
        BoundedPcmQueue.Snapshot full=q.snapshot();
        check(full.pendingFrames==4&&full.pendingBytes==16384&&full.maximumFrames==4,"frame and byte high water tracked");
        check(full.frameLimitDrops==1,"frame capacity loss has separate reason");
        frame=q.poll(now+6);check(offer(q,4096,now+7),"one consumer plus four queued records fit five fixed slots");
        rejected=false;try{q.poll(now+8);}catch(IllegalStateException expected){rejected=true;}
        check(rejected,"multiple consuming records prohibited");q.release(frame);
        while((frame=q.poll(now+9))!=null)q.release(frame);
        check(q.snapshot().pendingFrames==0&&q.snapshot().pendingBytes==0,"all ownership returns to fixed pool");
        q.close();

        BoundedPcmQueue bytes=new BoundedPcmQueue();
        check(offer(bytes,16384,now)&&offer(bytes,16384,now+1),"two largest records fit byte budget");
        check(!offer(bytes,4096,now+2)&&bytes.snapshot().byteLimitDrops==1,"byte limit can reject before frame limit");
        check(!offer(bytes,16388,now+3)&&bytes.snapshot().oversizeDrops==1,"oversized decoded record rejected before copy");
        check(!offer(bytes,3,now+4)&&bytes.snapshot().oversizeDrops==2,"non-stereo PCM alignment rejected");
        check(bytes.snapshot().pendingBytes<=BoundedPcmQueue.MAX_QUEUED_BYTES,"failed admission cannot exceed bytes");bytes.close();

        BoundedPcmQueue age=new BoundedPcmQueue();
        check(offer(age,4096,now),"fresh record for age check");
        frame=age.poll(now+79_999_999L);check(frame!=null,"record younger than 80ms consumable");age.release(frame);
        check(offer(age,4096,now),"second record for inclusive expiry");
        check(age.poll(now+80_000_000L)==null&&age.snapshot().ageDrops==1,"80ms queued age expires inclusively");
        check(!age.offerCopy(samples(4096),1,now,now,1f,now+80_000_000L)&&age.snapshot().admissionAgeDrops==1,"already-old copy rejected before admission separately");
        check(!age.offerCopy(samples(4096),1,now,now+1,1f,now)&&age.snapshot().admissionAgeDrops==2,"future dequeue clock rejected rather than negative age");
        check(offer(age,4096,now+1),"fresh record after clock rejection");
        check(age.poll(now)==null&&age.snapshot().ageDrops==2,"reverse poll clock cannot preserve a stale record");
        check(age.snapshot().accepted==age.snapshot().consumed+age.snapshot().ageDrops+age.snapshot().pendingFrames+age.snapshot().closingCleared,"admitted records conserve across consumption and queue drops");age.close();

        BoundedPcmQueue closing=new BoundedPcmQueue();offer(closing,4096,now);offer(closing,4096,now+1);
        frame=closing.poll(now+2);ByteBuffer inFlightView=frame.pcm();byte retained=inFlightView.get(1);
        closing.close();closing.close();
        check(closing.snapshot().pendingFrames==0&&closing.snapshot().pendingBytes==0&&closing.snapshot().closingCleared==1,"idempotent close clears queued records once");
        check(inFlightView.get(1)==retained,"close never wipes a writer-owned buffer concurrently");
        closing.release(frame);check(inFlightView.get(1)==0&&closing.snapshot().consumingFrames==0,"consumer final release wipes closed buffer");
        check(!offer(closing,4096,now+3)&&closing.snapshot().closedDrops==1,"closed queue rejects later codec output explicitly");
        BoundedPcmQueue stranger=new BoundedPcmQueue();offer(stranger,4096,now);frame=stranger.poll(now+1);
        rejected=false;try{closing.release(frame);}catch(IllegalArgumentException expected){rejected=true;}
        check(rejected,"another epoch cannot release a foreign slot");stranger.release(frame);stranger.close();

        BoundedPcmQueue wake=new BoundedPcmQueue();CountDownLatch entering=new CountDownLatch(1);
        final Throwable[] failure={null};Thread waiter=new Thread(()->{
            try{entering.countDown();while(!wake.isClosed())wake.awaitFrame(20_000_000L);}
            catch(Throwable error){failure[0]=error;}
        });waiter.start();entering.await();wake.close();waiter.join(1000);
        check(!waiter.isAlive()&&failure[0]==null,"close wakes a bounded consumer poll");
        rejected=false;try{wake.awaitFrame(20_000_001L);}catch(IllegalArgumentException expected){rejected=true;}
        check(rejected,"unbounded poll interval rejected");

        BoundedPcmQueue longRun=new BoundedPcmQueue();IdentityHashMap<BoundedPcmQueue.Frame,Boolean> identities=new IdentityHashMap<>();
        ByteBuffer input=samples(4096);
        for(int i=0;i<100000;i++){
            check(longRun.offerCopy(input,i,now+1,now,1f,now),"long run fixed-pool admission");
            frame=longRun.poll(now+1);identities.put(frame,Boolean.TRUE);longRun.release(frame);
        }
        check(identities.size()<=BoundedPcmQueue.POOL_FRAMES&&longRun.snapshot().accepted==100000,"long runs reuse fixed slot identities with exact totals");
        longRun.close();
        System.out.println("PASS "+checks+" bounded PCM queue checks (offline JVM; no codec, phone or acoustic measurement)");
    }
}
