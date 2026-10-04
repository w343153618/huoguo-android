package local.remoteandroid.direct;

import java.lang.reflect.Field;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import java.util.concurrent.locks.ReentrantLock;

public final class OwnerMediaFixture {
    static void ok(boolean value){if(!value)throw new AssertionError("media observation boundary");}
    static void await(CountDownLatch latch)throws InterruptedException{ok(latch.await(2,TimeUnit.SECONDS));}
    static void finish(OwnerMediaObservation value,Object owner){
        value.closing(owner);value.closeReturns(owner,true,1,true);value.nativeDestroyed(owner,7);
        value.listener(owner,false);value.listener(owner,true);value.returned(owner,true);
    }
    public static void main(String[] args)throws Exception{
        String mode=args[0];Object owner=new Object();
        if(mode.equals("bad_budget")){
            try{OwnerMediaObservation.prepare(owner,System.nanoTime()+31_000_000_000L);throw new AssertionError();}
            catch(IllegalArgumentException expected){System.out.println("PASS");return;}
        }
        OwnerMediaObservation value=OwnerMediaObservation.prepare(owner,System.nanoTime()+20_000_000_000L);
        CountDownLatch listener=new CountDownLatch(1),permit=new CountDownLatch(1);
        CountDownLatch videoPermit=new CountDownLatch(1),audioPermit=new CountDownLatch(1),epochPublished=new CountDownLatch(1);
        AtomicReference<Throwable> failure=new AtomicReference<>();
        Thread video=new Thread(()->{try{await(videoPermit);}catch(Throwable error){failure.set(error);}},"owned-video-fixture");
        Thread input=new Thread(()->{try{await(audioPermit);}catch(Throwable error){failure.set(error);}},"owned-audio-fixture");
        Thread output=new Thread(()->{},"owned-audio-output-fixture");
        Object receiver=new Object(),codec=new Object(),track=new Object(),inbox=new Object();
        Thread runner=new Thread(()->{
            try{
                value.started(owner);
                if(mode.equals("second_start"))value.started(owner);
                if(mode.equals("audio_epoch")||mode.equals("audio_live")||mode.equals("foreign_epoch")||mode.equals("epoch_after_close")){
                    Thread actualInput=new Thread(()->{
                        value.audioEpoch(mode.equals("foreign_epoch")?new Object():receiver,codec,track,null,output,null);
                        epochPublished.countDown();
                        try{await(audioPermit);}catch(Throwable error){failure.set(error);}
                    },"owned-configure-fixture");
                    value.audioCreated(receiver,actualInput);actualInput.start();output.start();await(epochPublished);
                    value.published(owner,new Object(),4,new Object(),7,receiver,null,inbox,video);
                    video.start();
                    value.closing(owner);value.audioClose(receiver,false);
                    if(mode.equals("epoch_after_close"))value.audioEpoch(receiver,new Object(),new Object(),null,new Thread(()->{}),null);
                    value.audioClose(receiver,true);value.closeReturns(owner,true,1,true);value.nativeDestroyed(owner,7);
                    value.listener(owner,false);value.listener(owner,true);value.returned(owner,true);
                    if(!mode.equals("audio_live")){audioPermit.countDown();actualInput.join();}
                    else{listener.countDown();await(permit);audioPermit.countDown();actualInput.join();}
                }else{
                    if(mode.equals("missing_publish")){finish(value,owner);return;}
                    video.start();value.published(owner,new Object(),4,new Object(),7,null,null,inbox,video);
                    if(mode.equals("second_publish"))value.published(owner,new Object(),4,new Object(),7,null,null,inbox,video);
                    if(mode.equals("listener_pending")){
                        value.closing(owner);value.closeReturns(owner,true,1,true);value.nativeDestroyed(owner,7);
                        value.listener(owner,false);listener.countDown();await(permit);
                        value.listener(owner,true);value.returned(owner,true);
                    }else if(mode.equals("run_return_pending")){
                        finish(value,owner);listener.countDown();await(permit);
                    }else if(mode.equals("listener_throw")){
                        value.closing(owner);value.closeReturns(owner,true,1,true);value.nativeDestroyed(owner,7);value.listener(owner,false);value.returned(owner,false);
                    }else if(mode.equals("foreign_owner"))finish(value,new Object());
                    else if(mode.equals("wrong_order")){value.closing(owner);value.nativeDestroyed(owner,7);finish(value,owner);}
                    else if(mode.equals("bad_audio_claim")){value.closing(owner);value.closeReturns(owner,true,3,true);}
                    else if(mode.equals("duplicate_close")){
                        value.closing(owner);value.closeReturns(owner,false,0,false);value.closeReturns(owner,false,0,false);
                    }
                    else if(mode.equals("wrong_handle")){value.closing(owner);value.nativeDestroyed(owner,8);}
                    else if(mode.equals("no_listener")){value.closing(owner);value.returned(owner,true);}
                    else if(mode.equals("incomplete_close")){
                        value.closing(owner);value.closeReturns(owner,false,0,true);value.nativeDestroyed(owner,7);
                        value.listener(owner,false);value.listener(owner,true);value.returned(owner,true);
                    }else finish(value,owner);
                }
            }catch(Throwable error){failure.set(error);}finally{listener.countDown();}
        },"owned-probe-fixture");
        try{
            if(mode.equals("lock_contention")){
                value.started(owner);
                Field field=OwnerMediaObservation.class.getDeclaredField("lock");field.setAccessible(true);
                ReentrantLock lock=(ReentrantLock)field.get(value);CountDownLatch held=new CountDownLatch(1);
                Thread reader=new Thread(()->{lock.lock();try{held.countDown();await(permit);}catch(Throwable e){failure.set(e);}finally{lock.unlock();}});
                reader.start();await(held);long start=System.nanoTime();value.closing(owner);
                ok(System.nanoTime()-start<200_000_000L);permit.countDown();reader.join();ok(value.observe(owner)==null);
            }else if(mode.equals("expired")){
                OwnerMediaObservation expired=OwnerMediaObservation.prepare(owner,System.nanoTime()+10_000_000L);Thread.sleep(20);
                expired.started(owner);ok(expired.observe(owner)==null);
            }else{
                runner.start();await(listener);
                if(mode.equals("listener_pending"))ok(value.observe(owner)==null);
                if(mode.equals("run_return_pending")||mode.equals("audio_live")){
                    OwnerMediaObservation.Snapshot past=value.observe(owner);ok(past!=null&&!past.runnerTerminated&&!past.releaseEligible);
                    if(mode.equals("audio_live"))ok(!past.audioThreadsTerminated);
                }
                permit.countDown();runner.join();
                boolean refused=mode.equals("foreign_owner")||mode.equals("second_start")||mode.equals("second_publish")
                    ||mode.equals("missing_publish")||mode.equals("listener_throw")||mode.equals("no_listener")
                    ||mode.equals("wrong_order")||mode.equals("bad_audio_claim")||mode.equals("duplicate_close")||mode.equals("wrong_handle")||mode.equals("foreign_epoch")||mode.equals("epoch_after_close");
                OwnerMediaObservation.Snapshot past=value.observe(owner);
                if(refused)ok(past==null);
                else{
                    ok(past!=null&&past.runnerTerminated&&!past.videoTerminated);
                    ok(!past.releaseEligible&&!past.attemptQualified&&!past.codecAudioInputQualified);
                    if(mode.equals("audio_epoch"))ok(past.audioEpochCount==1&&past.audioCloseReturned&&past.audioThreadsTerminated);
                    if(mode.equals("incomplete_close"))ok(!past.videoCloseReturned&&past.audioCloseClaim==0);
                    videoPermit.countDown();video.join();ok(value.observe(owner).videoTerminated);
                    if(mode.equals("foreign_reader")){ok(value.observe(new Object())==null);ok(value.observe(owner)==null);}
                }
            }
            ok(failure.get()==null);System.out.println("PASS");
        }finally{permit.countDown();videoPermit.countDown();audioPermit.countDown();runner.join();video.join();input.join();output.join();}
    }
}
