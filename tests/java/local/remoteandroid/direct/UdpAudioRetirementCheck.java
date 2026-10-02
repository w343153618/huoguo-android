package local.remoteandroid.direct;

import android.media.AudioTrack;
import android.media.MediaCodec;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.nio.ByteBuffer;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/** Runs the actual Receiver with offline Android API substitutes. This checks
 * Java ownership/concurrency only, not vendor codec behavior or Android audio.
 */
public final class UdpAudioRetirementCheck {
    private static int checks;
    private static void check(boolean okay,String label){if(!okay)throw new AssertionError(label);checks++;}
    private static Object get(Object receiver,String name)throws Exception{
        Field f=UdpAudioReceiver.class.getDeclaredField(name);f.setAccessible(true);return f.get(receiver);}
    private static void set(Object receiver,String name,Object value)throws Exception{
        Field f=UdpAudioReceiver.class.getDeclaredField(name);f.setAccessible(true);f.set(receiver,value);}
    @SuppressWarnings("unchecked")
    private static void media(UdpAudioReceiver receiver)throws Exception{
        long now=System.nanoTime();((ArrayBlockingQueue<UdpAudioAssembler.Frame>)get(receiver,"queue")).offer(
            new UdpAudioAssembler.Frame(1,100000,now,now,false,new byte[]{1,2,3,4}));}
    private static UdpAudioReceiver fixture(MediaCodec codec,AudioTrack track)throws Exception{
        MainActivity activity=new MainActivity();activity.running=true;activity.generation=7;
        UdpAudioReceiver receiver=new UdpAudioReceiver(activity,7,true);
        set(receiver,"decoder",codec);set(receiver,"output",track);activity.audio=codec;activity.track=track;
        return receiver;
    }
    public static void main(String[] args)throws Exception{
        MediaCodec held=new MediaCodec();AudioTrack heldTrack=new AudioTrack();held.blockInput=true;
        UdpAudioReceiver first=fixture(held,heldTrack);media(first);
        check(held.inputEntered.await(2,TimeUnit.SECONDS),"actual input worker enters codec API");
        first.close();
        check((Boolean)get(first,"pcmCleanupIncomplete"),"500ms input worker timeout reports incomplete cleanup");
        check(held.releases==0&&heldTrack.releases==0,"held input epoch cannot release codec or track");
        held.inputPermit.countDown();((Thread)get(first,"worker")).join(2000);
        check(!((Thread)get(first,"worker")).isAlive(),"held input worker eventually quiesces");
        check(held.queuedAfterRelease==0,"no input submission accesses a released codec");
        first.close();
        check(!(Boolean)get(first,"pcmCleanupIncomplete")&&held.releases==1&&heldTrack.releases==1,"retry retires the preserved epoch exactly once");
        first.close();check(held.releases==1&&heldTrack.releases==1,"repeated close cannot double-release resources");

        MediaCodec needsLock=new MediaCodec();AudioTrack lockTrack=new AudioTrack();needsLock.blockInput=true;
        UdpAudioReceiver second=fixture(needsLock,lockTrack);Object lifecycleLock=get(second,"pcmLifecycleLock");
        CountDownLatch lockAcquired=new CountDownLatch(1);
        needsLock.beforeInputReturn=()->{synchronized(lifecycleLock){lockAcquired.countDown();}};
        media(second);check(needsLock.inputEntered.await(2,TimeUnit.SECONDS),"second worker reaches held codec input");
        Thread closer=new Thread(second::close);closer.start();
        // close interrupts first, then joins. The interrupted worker stays in
        // our substitute until explicitly allowed, just like a held native API.
        check(needsLock.inputInterrupted.await(2,TimeUnit.SECONDS),"external close requests input-worker stop");
        needsLock.inputPermit.countDown();closer.join(2000);
        check(!closer.isAlive()&&lockAcquired.getCount()==0,"input quiescence wait does not hold its required lifecycle lock");
        check(!(Boolean)get(second,"pcmCleanupIncomplete")&&needsLock.releases==1,"successful quiescence permits release");
        check(needsLock.queuedAfterRelease==0,"lifecycle-lock completion precedes codec release");

        // Configure naturally runs on the input worker: this must not try to
        // join itself. The API substitutes produce no PCM or sound.
        MainActivity activity=new MainActivity();activity.running=true;activity.generation=9;
        UdpAudioReceiver configured=new UdpAudioReceiver(activity,9,true);MediaCodec next=new MediaCodec();MediaCodec.nextCreated=next;
        long now=System.nanoTime();
        @SuppressWarnings("unchecked") ArrayBlockingQueue<UdpAudioAssembler.Frame> input=(ArrayBlockingQueue<UdpAudioAssembler.Frame>)get(configured,"queue");
        input.offer(new UdpAudioAssembler.Frame(2,0,now,now,true,new byte[]{0x11,(byte)0x90}));
        long until=System.nanoTime()+2_000_000_000L;
        while(get(configured,"decoder")!=next&&System.nanoTime()<until)Thread.sleep(1);
        check(get(configured,"decoder")==next&&!(Boolean)get(configured,"pcmCleanupIncomplete"),"input-worker configure skips self join");
        configured.close();check(next.releases==1&&!(Boolean)get(configured,"pcmCleanupIncomplete"),"configured B epoch retires both workers");

        // A record may be younger than the queue's 80ms age but already 90ms
        // past its original shared-clock target. Exercise the actual consumer.
        MediaCodec lateCodec=new MediaCodec();AudioTrack lateTrack=new AudioTrack();
        UdpAudioReceiver late=fixture(lateCodec,lateTrack);BoundedPcmQueue handoff=new BoundedPcmQueue();
        long arrival=System.nanoTime();handoff.offerCopy(ByteBuffer.allocate(4096),1,arrival-90_000_000L,arrival-20_000_000L,1f,arrival);
        Method consume=UdpAudioReceiver.class.getDeclaredMethod("playQueuedPcm",MediaCodec.class,AudioTrack.class,int.class,BoundedPcmQueue.class);
        consume.setAccessible(true);final Throwable[] consumeFailure={null};
        Thread consumer=new Thread(()->{try{consume.invoke(late,lateCodec,lateTrack,1,handoff);}catch(Throwable error){consumeFailure[0]=error;}});
        consumer.start();long deadline=System.nanoTime()+2_000_000_000L;
        while(handoff.snapshot().consumed==0&&System.nanoTime()<deadline)Thread.sleep(1);
        handoff.close();consumer.join(2000);
        UdpAudioReceiver.AudioDiagnostics diagnostics=(UdpAudioReceiver.AudioDiagnostics)get(late,"diagnostics");
        check(!consumer.isAlive()&&consumeFailure[0]==null,"late PCM consumer exits and returns owned slot");
        check(diagnostics.lateCounts()[5]==1&&handoff.snapshot().consumingFrames==0,"original target 80ms late gate counts and releases queued record");
        check(((java.util.concurrent.atomic.AtomicLong)get(late,"pcmBytes")).get()==0,"late PCM cannot reach AudioTrack write");late.close();
        System.out.println("PASS "+checks+" actual Receiver retirement checks (offline API substitutes; not Android codec or acoustic validation)");
    }
}
