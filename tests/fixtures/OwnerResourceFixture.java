package local.remoteandroid.direct;

import android.media.MediaCodec;
import android.media.MediaFormat;
import android.media.AudioTrack;
import java.lang.reflect.Field;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.locks.ReentrantLock;

public final class OwnerResourceFixture {
    static void ok(boolean b){if(!b)throw new AssertionError("resource path");}
    static Object field(Object object,String name)throws Exception{Field f=object.getClass().getDeclaredField(name);f.setAccessible(true);return f.get(object);}
    static OwnerResourceObservation ledger(){return new OwnerResourceObservation(System.nanoTime()+10_000_000_000L);}
    static void unqualified(OwnerResourceObservation.Past p){ok(!p.resourceReleasedQualified&&!p.attemptQualified&&!p.releaseEligible);}
    @SuppressWarnings("unchecked")
    public static void main(String[] args)throws Exception{
        String mode=args[0];
        if(mode.startsWith("main_")||mode.startsWith("bind_")){
            MainActivity activity=new MainActivity();activity.generation=4;
            OwnerMediaObservation observer=OwnerMediaObservation.prepare(new Object(),System.nanoTime()+10_000_000_000L);
            if(mode.equals("bind_monitor")){
                CountDownLatch held=new CountDownLatch(1),release=new CountDownLatch(1);
                Thread t=new Thread(()->{synchronized(activity){held.countDown();try{release.await();}catch(InterruptedException e){Thread.currentThread().interrupt();}}});
                t.start();ok(held.await(1,TimeUnit.SECONDS));long begin=System.nanoTime();
                activity.ownerBindResourceObservation(observer,4);ok(System.nanoTime()-begin<100_000_000L);
                release.countDown();t.join();ok(observer.resourceCalls.snapshot().length==0);activity.input.shutdownNow();
                System.out.println("PASS binding does not wait on normal Activity monitor");return;
            }
            if(mode.equals("bind_existing"))activity.video=new MediaCodec();
            if(!mode.equals("main_inert"))activity.ownerBindResourceObservation(observer,4);
            if(mode.startsWith("bind_")){
                if(mode.equals("bind_repeated"))activity.ownerBindResourceObservation(observer,4);
                ok(observer.resourceCalls.snapshot()==null);
            }else if(mode.equals("main_foreign")){
                activity.video=new MediaCodec();activity.stop();ok(observer.resourceCalls.snapshot()==null);
            }else{
                MediaCodec codec=new MediaCodec();MediaCodec.nextCreated=codec;
                MediaCodec.failName=mode.equals("main_unpublished");
                try{activity.video=activity.fastDecoder(new MediaFormat());}catch(IllegalStateException expected){ok(MediaCodec.failName);}
                MediaCodec.failStop=mode.equals("main_stop_failure");MediaCodec.failRelease=mode.equals("main_release_error");
                boolean error=false;try{activity.stop();}catch(AssertionError expected){error=true;}
                ok(error==MediaCodec.failRelease);
                OwnerResourceObservation.Past[] past=observer.resourceCalls.snapshot();
                if(mode.equals("main_inert")){ok(past.length==0&&codec.stops==1&&codec.releases==1);}
                else {ok(past.length==1&&past[0].value==codec&&past[0].source==activity);unqualified(past[0]);
                    if(MediaCodec.failName){ok(activity.video==null&&past[0].releaseAttempts==0&&codec.releases==0);}
                    else {ok(activity.video==null&&past[0].stopAttempts==1);
                        ok(past[0].stopFailures==(MediaCodec.failStop?1:0));
                        ok(past[0].releaseAttempts==(MediaCodec.failStop?0:1));
                        ok(past[0].releaseFailures==(MediaCodec.failRelease?1:0));}}
            }
            activity.input.shutdownNow();
        }else if(mode.startsWith("audio_")){
            MainActivity activity=new MainActivity();activity.running=true;activity.generation=4;
            Object owner=new Object();OwnerMediaObservation observer=OwnerMediaObservation.prepare(owner,System.nanoTime()+10_000_000_000L);
            observer.started(owner);
            MediaCodec codec=new MediaCodec();MediaCodec.nextCreated=codec;
            MediaCodec.failConfigure=mode.equals("audio_setup_codec");AudioTrack.failState=mode.equals("audio_setup_track")||mode.equals("audio_bounded_setup_track");
            MediaCodec.failStop=mode.equals("audio_stop_failure");MediaCodec.failRelease=mode.equals("audio_release_error");
            UdpAudioReceiver receiver=new UdpAudioReceiver(activity,4,mode.equals("audio_bounded_setup_track"),observer);
            try{
                ArrayBlockingQueue<UdpAudioAssembler.Frame> queue=(ArrayBlockingQueue<UdpAudioAssembler.Frame>)field(receiver,"queue");
                long now=System.nanoTime();queue.offer(new UdpAudioAssembler.Frame(2,0,now,now,true,new byte[]{0x11,(byte)0x90}));
                Thread input=(Thread)field(receiver,"worker");
                boolean setup=MediaCodec.failConfigure||AudioTrack.failState;
                if(setup){input.join(2000);ok(!input.isAlive());}
                else {
                    long end=System.nanoTime()+2_000_000_000L;
                    while(System.nanoTime()<end){Thread d=(Thread)field(receiver,"drain");if(d!=null&&d.getState()!=Thread.State.NEW)break;Thread.yield();}
                    ok(field(receiver,"drain")!=null);
                }
                observer.published(owner,activity,4,new Object(),7,receiver,null,null,null);observer.closing(owner);
                boolean error=false;try{receiver.close();}catch(AssertionError expected){error=true;}
                ok(error==MediaCodec.failRelease);
                OwnerResourceObservation.Past[] past=observer.resourceCalls.snapshot();ok(past!=null&&past.length==(MediaCodec.failConfigure?1:2));
                OwnerResourceObservation.Past p=past[0];ok(p.value==codec&&p.source==receiver&&p.allocationThread==input);unqualified(p);
                ok(p.stopAttempts==1&&p.releaseAttempts==1&&p.stopFailures==(MediaCodec.failStop?1:0)&&p.releaseFailures==(MediaCodec.failRelease?1:0));
                if(setup){ok(field(receiver,"decoder")==null&&field(receiver,"output")==null&&codec.releases==1);}
                if(AudioTrack.failState){unqualified(past[1]);ok(past[1].releaseAttempts==1&&past[1].stopAttempts==0);}
            }finally{activity.running=false;Thread input=(Thread)field(receiver,"worker");input.interrupt();input.join(2000);activity.input.shutdownNow();}
        }else{
            OwnerResourceObservation observer=ledger();Object source=new Object(),resource=new Object();
            if(mode.equals("resource_bound")){for(int i=0;i<129;i++)observer.allocated(source,new Object(),1);ok(observer.snapshot()==null);}
            else if(mode.equals("expiry")){observer=new OwnerResourceObservation(System.nanoTime()+100_000_000L);Thread.sleep(150);observer.allocated(source,resource,1);ok(observer.snapshot()==null);}
            else if(mode.equals("contention")){
                ReentrantLock lock=(ReentrantLock)field(observer,"lock");CountDownLatch held=new CountDownLatch(1),release=new CountDownLatch(1);
                Thread t=new Thread(()->{lock.lock();try{held.countDown();release.await();}catch(InterruptedException e){Thread.currentThread().interrupt();}finally{lock.unlock();}});
                t.start();ok(held.await(1,TimeUnit.SECONDS));long begin=System.nanoTime();observer.allocated(source,resource,1);ok(System.nanoTime()-begin<100_000_000L);release.countDown();t.join();ok(observer.snapshot()==null);
            }else{
                observer.allocated(source,resource,1);
                if(mode.equals("duplicate_allocation")){observer.allocated(source,resource,1);ok(observer.snapshot()==null);}
                else if(mode.equals("foreign_source")){ok(observer.begin(new Object(),resource,1)==null&&observer.snapshot()==null);}
                else if(mode.equals("call_bound")){for(int i=0;i<513;i++){OwnerResourceObservation.Call call=observer.begin(source,resource,1);if(call!=null)call.finish(true);}ok(observer.snapshot()==null);}
                else{
                    OwnerResourceObservation.Call call=observer.begin(source,resource,1);ok(call!=null);
                    if(mode.equals("pending_call")){ok(observer.snapshot()[0].pending);unqualified(observer.snapshot()[0]);call.finish(false);ok(observer.snapshot()[0].stopFailures==1);}
                    else if(mode.equals("overlap")){ok(observer.begin(source,resource,2)==null&&observer.snapshot()==null);}
                    else if(mode.equals("foreign_finish")){Thread t=new Thread(()->call.finish(true));t.start();t.join();ok(observer.snapshot()==null);}
                    else {call.finish(true);if(mode.equals("duplicate_finish")){call.finish(true);ok(observer.snapshot()==null);}else{OwnerResourceObservation.Call next=observer.begin(source,resource,2);next.finish(false);OwnerResourceObservation.Past p=observer.snapshot()[0];ok(p.stopReturns==1&&p.releaseFailures==1);unqualified(p);}}
                }
            }
        }
        System.out.println("PASS actual allocation/call paths; Android resources synthetic");
    }
}
