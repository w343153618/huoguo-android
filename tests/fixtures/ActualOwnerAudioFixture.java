package local.remoteandroid.direct;

import android.media.MediaCodec;
import java.lang.reflect.Field;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

public final class ActualOwnerAudioFixture {
    static Object get(Object source,String name)throws Exception{
        Field field=UdpAudioReceiver.class.getDeclaredField(name);field.setAccessible(true);return field.get(source);
    }
    static void ok(boolean value){if(!value)throw new AssertionError("actual audio owner path");}
    @SuppressWarnings("unchecked")
    public static void main(String[] args)throws Exception{
        boolean bounded=args[0].equals("bounded");boolean fail=args[1].equals("release_failure");
        Object owner=new Object();OwnerMediaObservation value=OwnerMediaObservation.prepare(owner,System.nanoTime()+10_000_000_000L);
        CountDownLatch published=new CountDownLatch(1),permit=new CountDownLatch(1);
        Throwable[] failure={null};
        Thread runner=new Thread(()->{
            UdpAudioReceiver audio=null;
            try{
                value.started(owner);MainActivity activity=new MainActivity();activity.running=true;activity.generation=4;
                MediaCodec codec=fail?new MediaCodec(){@Override public void release(){super.release();throw new IllegalStateException("fixture-only release failure");}}:new MediaCodec();
                MediaCodec.nextCreated=codec;
                audio=new UdpAudioReceiver(activity,4,bounded,value);
                ArrayBlockingQueue<UdpAudioAssembler.Frame> queue=(ArrayBlockingQueue<UdpAudioAssembler.Frame>)get(audio,"queue");
                long now=System.nanoTime();queue.offer(new UdpAudioAssembler.Frame(2,0,now,now,true,new byte[]{0x11,(byte)0x90}));
                long end=System.nanoTime()+2_000_000_000L;
                while(System.nanoTime()<end){
                    Thread drain=(Thread)get(audio,"drain");
                    if(drain!=null&&drain.getState()!=Thread.State.NEW)break;
                    Thread.yield();
                }
                ok(get(audio,"drain")!=null&&((Thread)get(audio,"drain")).getState()!=Thread.State.NEW);
                // Acquire the journal's own lock via publication after the real
                // configure path has captured resources; no JSON adoption.
                value.published(owner,activity,4,new Object(),7,audio,null,null,null);
                value.closing(owner);int claim=UdpVideoProbe.closeOwnedAudio(audio);
                value.closeReturns(owner,true,claim,true);value.nativeDestroyed(owner,7);
                value.listener(owner,false);value.listener(owner,true);value.returned(owner,true);
                published.countDown();ok(permit.await(2,TimeUnit.SECONDS));
            }catch(Throwable error){failure[0]=error;published.countDown();}
            finally{if(audio!=null)audio.close();}
        },"actual-audio-owner-fixture");
        try{
            runner.start();ok(published.await(3,TimeUnit.SECONDS));ok(failure[0]==null);
            OwnerMediaObservation.Snapshot past=value.observe(owner);
            ok(past!=null&&past.audioEpochCount==1&&past.audioCloseReturned&&!past.runnerTerminated);
            ok(past.audioCloseClaim==(fail?0:1));
            ok(!past.releaseEligible&&!past.codecAudioInputQualified&&!past.attemptQualified);
            permit.countDown();runner.join();
            // A later close attempt is sticky UNKNOWN, not a new receipt.
            ok(value.observe(owner)==null);System.out.println("PASS actual receiver with synthetic Android resources");
        }finally{permit.countDown();runner.join();}
    }
}
