package local.remoteandroid.direct;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

public final class OwnerInputAppFixture {
    static void retire(AuthenticatedLanUdpUi ui,boolean complete) {
        AuthenticatedLanUdpUi.Attempt captured=ui.current;
        captured.cancelled=true;captured.stopped=true;ui.retiring=captured;ui.current=null;ui.generation++;
        if(complete) finish(ui);
    }
    static void finish(AuthenticatedLanUdpUi ui) {
        ui.activity.running=false;ui.activity.generation++;ui.activity.input.clear();ui.retiring=null;
    }
    public static void main(String[] args) throws Exception {
        String mode=args[0]; InputDrainFixture.Manual manual=new InputDrainFixture.Manual();
        InputQueue q=new InputQueue(manual); AuthenticatedLanUdpUi ui=new AuthenticatedLanUdpUi(q);
        if(mode.equals("inert")) {
            InputDrainFixture.check(ui.current.ownerInputObservation==null);q.execute(()->{});manual.run();
        } else if(mode.equals("expired")) {
            InputDrainFixture.refuse(()->ui.ownerCaptureInput(System.nanoTime()-1));
        } else if(mode.equals("lock_wait")) {
            CountDownLatch held=new CountDownLatch(1);
            Thread holder=new Thread(()->{synchronized(ui.lock){held.countDown();try{Thread.sleep(3100);}
                catch(InterruptedException e){Thread.currentThread().interrupt();}}});
            holder.start();InputDrainFixture.await(held);
            long begin=System.nanoTime();InputDrainFixture.refuse(()->ui.ownerCaptureInput(begin+20_000_000_000L));
            holder.join(1000);InputDrainFixture.check(!holder.isAlive() && System.nanoTime()-begin>=3_000_000_000L);
            // Refusal before capture must leave no retrospectively claimed queue scope.
            InputDrainFixture.check(ui.current.ownerInputObservation==null && ui.activity.screen.reads==0);
        } else if(mode.equals("pending_action")) {
            ExecutorService executor=Executors.newSingleThreadExecutor();InputQueue real=new InputQueue(executor);
            AuthenticatedLanUdpUi live=new AuthenticatedLanUdpUi(real);CountDownLatch entered=new CountDownLatch(1),release=new CountDownLatch(1);
            AuthenticatedLanUdpUi.OwnerInputObservation obs=live.ownerCaptureInput(System.nanoTime()+20_000_000_000L);
            try {
                real.touch(false,3,()->{entered.countDown();InputDrainFixture.await(release);},()->{});
                InputDrainFixture.await(entered);retire(live,true);obs.sealAfterNormalCancel();
                InputDrainFixture.check(!obs.observeRetiredInput());release.countDown();executor.shutdown();
                InputDrainFixture.check(executor.awaitTermination(2,TimeUnit.SECONDS) && obs.observeRetiredInput());
            } finally {release.countDown();executor.shutdownNow();}
        } else {
            final AuthenticatedLanUdpUi app=ui;
            AuthenticatedLanUdpUi.OwnerInputObservation obs=ui.ownerCaptureInput(System.nanoTime()+20_000_000_000L);
            switch(mode) {
                case "normal": obs.observeLive();retire(ui,false);obs.sealAfterNormalCancel();finish(ui);InputDrainFixture.check(obs.observeRetiredInput());break;
                case "later_attempt": ui.current=new AuthenticatedLanUdpUi.Attempt();InputDrainFixture.refuse(obs::observeLive);break;
                case "cleared_binding": ui.current.ownerInputObservation=null;InputDrainFixture.refuse(obs::observeLive);break;
                case "receiver": ui.current.receiver=new AuthenticatedLanUdpUi.Attempt().receiver;InputDrainFixture.refuse(obs::observeLive);break;
                case "surface": ui.activity.screen.surface=new android.view.Surface();InputDrainFixture.refuse(obs::observeLive);break;
                case "death": ui.activity.dead=true;InputDrainFixture.refuse(obs::observeLive);break;
                case "nonmain":
                    Thread foreign=new Thread(()->InputDrainFixture.refuse(obs::observeLive));foreign.start();foreign.join(2000);
                    InputDrainFixture.check(!foreign.isAlive());InputDrainFixture.refuse(obs::observeLive);break;
                case "later_activity": ui.activity.generation++;InputDrainFixture.refuse(obs::observeLive);break;
                case "missing_retirement": InputDrainFixture.refuse(obs::sealAfterNormalCancel);InputDrainFixture.refuse(obs::observeLive);break;
                case "later_after_seal": retire(ui,true);obs.sealAfterNormalCancel();ui.current=new AuthenticatedLanUdpUi.Attempt();InputDrainFixture.refuse(obs::observeRetiredInput);break;
                case "second_capture": InputDrainFixture.refuse(()->app.ownerCaptureInput(System.nanoTime()+20_000_000_000L));obs.observeLive();break;
                case "repeat_observe": retire(ui,true);obs.sealAfterNormalCancel();InputDrainFixture.check(obs.observeRetiredInput());InputDrainFixture.refuse(obs::observeRetiredInput);break;
                case "cleanup_unknown":
                    q.touch(false,3,()->{},()->{throw new IllegalStateException("cleanup");});InputDrainFixture.refuse(q::clear);manual.run();
                    retire(ui,true);InputDrainFixture.refuse(obs::sealAfterNormalCancel);break;
                default: throw new AssertionError(mode);
            }
        }
        System.out.println("App input association PASS; Android adapters synthetic; normal codec audio remote input PM lease release false");
    }
}
