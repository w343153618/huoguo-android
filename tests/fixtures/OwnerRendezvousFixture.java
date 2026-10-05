package local.remoteandroid.direct;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
public final class OwnerRendezvousFixture {
    static void ok(boolean b){InputDrainFixture.check(b);}
    static void refuse(Runnable run){InputDrainFixture.refuse(run);}
    static void lockWait(AuthenticatedLanUdpUi ui,Runnable run)throws Exception{
        CountDownLatch held=new CountDownLatch(1);
        Thread holder=new Thread(()->{synchronized(ui.lock){held.countDown();try{Thread.sleep(3100);}catch(InterruptedException e){Thread.currentThread().interrupt();}}});
        holder.start();InputDrainFixture.await(held);refuse(run);holder.join();
    }
    static void retire(AuthenticatedLanUdpUi ui){
        AuthenticatedLanUdpUi.Attempt a=ui.current;a.cancelled=true;a.stopped=true;ui.current=null;ui.retiring=a;ui.generation++;
    }
    static void finish(AuthenticatedLanUdpUi ui){ui.retiring=null;ui.activity.generation++;ui.activity.running=false;ui.activity.input.clear();}
    public static void main(String[] args)throws Exception{
        String mode=args[0];InputDrainFixture.Manual manual=new InputDrainFixture.Manual();InputQueue queue=new InputQueue(manual);
        ExecutorService executor=mode.equals("retire_pending")?Executors.newSingleThreadExecutor():null;
        CountDownLatch actionEntered=new CountDownLatch(1),actionRelease=new CountDownLatch(1);
        if(executor!=null)queue=new InputQueue(executor);
        AuthenticatedLanUdpUi ui=new AuthenticatedLanUdpUi(queue);long end=System.nanoTime()+(mode.equals("capture_expired")?300_000_000L:20_000_000_000L);
        if(mode.equals("prepare_expired")){refuse(()->ui.ownerPrepareRendezvous(System.nanoTime()-1));ok(ui.activity.screen.reads==0);}
        else if(mode.equals("prepare_budget")){refuse(()->ui.ownerPrepareRendezvous(System.nanoTime()+31_000_000_000L));}
        else if(mode.equals("prepare_late")){ui.current.receiver=new UdpVideoProbe();refuse(()->ui.ownerPrepareRendezvous(end));}
        else if(mode.equals("prepare_nonmain")){android.os.Looper.myLooper();Thread t=new Thread(()->refuse(()->ui.ownerPrepareRendezvous(end)));t.start();t.join();ok(ui.current.ownerRendezvous==null);}
        else if(mode.equals("prepare_lock_wait")){lockWait(ui,()->ui.ownerPrepareRendezvous(end));ok(ui.current.ownerRendezvous==null && ui.activity.screen.reads==0);}
        else if(mode.equals("inert")){
            UdpVideoProbe receiver=UdpVideoProbe.startApp(ui.activity,new JSONObject(),false,false,false,null,()->{});
            ok(ui.current.ownerRendezvous==null && ui.current.ownerInputObservation==null);InputDrainFixture.await(receiver.entered);
            receiver.permit.countDown();receiver.actualThread.join();
        }else{
            AuthenticatedLanUdpUi.OwnerRendezvous value=ui.ownerPrepareRendezvous(end);
            if(mode.equals("prepare_twice")){refuse(()->ui.ownerPrepareRendezvous(end));System.out.println("PASS");return;}
            if(mode.equals("receiver_later_attempt")){ui.current=new AuthenticatedLanUdpUi.Attempt();refuse(()->value.prepare(new UdpVideoProbe(),new Thread()));}
            else if(mode.equals("receiver_later_generation")){ui.activity.generation++;refuse(()->value.prepare(new UdpVideoProbe(),new Thread()));}
            else if(mode.equals("before_start_not_new")){Thread t=new Thread(()->{});t.start();t.join();refuse(()->value.prepare(new UdpVideoProbe(),t));}
            else if(mode.equals("capture_before_start")){refuse(value::captureLive);}
            else{
                if(mode.equals("retire_media_generation"))UdpVideoProbe.nextPublicationGeneration=6;
                AuthenticatedLanUdpUi.Attempt original=ui.current;
                UdpVideoProbe receiver=UdpVideoProbe.startAppObserved(ui.activity,new JSONObject(),false,false,false,null,()->{},value);
                InputDrainFixture.await(receiver.entered);ok(ui.current.receiver==receiver);ui.activity.generation+=2;
                try{
                    switch(mode){
                        case "capture_terminated":receiver.permit.countDown();receiver.actualThread.join();refuse(value::captureLive);break;
                        case "capture_expired":Thread.sleep(350);refuse(value::captureLive);break;
                        case "capture_reentrant":ui.activity.screen.callback=()->refuse(value::captureLive);refuse(value::captureLive);break;
                        case "capture_dead":ui.activity.dead=true;refuse(value::captureLive);break;
                        case "capture_invalid_surface":ui.activity.screen.surface.valid=false;refuse(value::captureLive);break;
                        case "capture_later_activity":ui.activity.generation++;refuse(value::captureLive);break;
                        case "capture_later_attempt":ui.current=new AuthenticatedLanUdpUi.Attempt();refuse(value::captureLive);break;
                        case "capture_receiver":ui.current.receiver=new UdpVideoProbe();refuse(value::captureLive);break;
                        case "capture_cleared_binding":ui.current.ownerRendezvous=null;refuse(value::captureLive);break;
                        case "capture_nonmain":Thread t=new Thread(()->refuse(value::captureLive));t.start();t.join();refuse(value::captureLive);break;
                        case "capture_lock_wait":lockWait(ui,value::captureLive);ok(ui.current.ownerInputObservation==null && ui.activity.screen.reads==0);break;
                        default:
                            value.captureLive();
                            if(mode.equals("capture_twice")){refuse(value::captureLive);break;}
                            if(mode.equals("retire_missing_cancel")){refuse(value::sealAfterNormalCancel);break;}
                            if(mode.equals("retire_pending")){queue.touch(false,5,()->{actionEntered.countDown();InputDrainFixture.await(actionRelease);},()->{});InputDrainFixture.await(actionEntered);}
                            retire(ui);value.sealAfterNormalCancel();finish(ui);
                            if(mode.equals("retire_later_attempt")){ui.current=new AuthenticatedLanUdpUi.Attempt();refuse(value::observeRetired);break;}
                            if(mode.equals("retire_surface")){ui.activity.screen.surface=new android.view.Surface();refuse(value::observeRetired);break;}
                            if(mode.equals("retire_cleared_input")){original.ownerInputObservation=null;refuse(value::observeRetired);break;}
                            if(mode.equals("retire_pending_runner"))ok(value.observeRetired()==null);
                            receiver.permit.countDown();receiver.actualThread.join();
                            if(mode.equals("retire_media_generation")){refuse(value::observeRetired);break;}
                            if(mode.equals("retire_pending")){ok(value.observeRetired()==null);actionRelease.countDown();executor.shutdown();ok(executor.awaitTermination(2,TimeUnit.SECONDS));}
                            manual.run();AuthenticatedLanUdpUi.OwnerRendezvous.Past past=value.observeRetired();
                            ok(past!=null && past.thread==receiver.actualThread && past.receiver==receiver
                                && !past.attemptQualified && !past.codecAudioInputQualified && !past.releaseEligible);
                            if(mode.equals("retire_repeat"))refuse(value::observeRetired);
                    }
                }finally{actionRelease.countDown();receiver.permit.countDown();receiver.actualThread.join();if(executor!=null){executor.shutdown();ok(executor.awaitTermination(2,TimeUnit.SECONDS));}}
            }
        }
        System.out.println("PASS actual factory/UI objects and own threads; Android resources synthetic; permission release false");
    }
}
