package local.remoteandroid.direct;

import java.util.ArrayDeque;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executor;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

public final class InputDrainFixture {
    static final class Manual implements Executor {
        final ArrayDeque<Runnable> tasks=new ArrayDeque<>();
        public void execute(Runnable task) { tasks.add(task); }
        void run() { while(!tasks.isEmpty()) tasks.remove().run(); }
    }
    static void check(boolean ok) { if(!ok) throw new AssertionError(); }
    static void refuse(Runnable action) {
        try { action.run(); } catch(IllegalStateException expected) { return; }
        throw new AssertionError("not refused");
    }
    static void await(CountDownLatch latch) {
        try { check(latch.await(2,TimeUnit.SECONDS)); }
        catch(InterruptedException e) { Thread.currentThread().interrupt(); throw new AssertionError(e); }
    }
    public static void main(String[] args) throws Exception {
        String mode=args[0]; Manual worker=new Manual(); InputQueue q=new InputQueue(worker);
        Object attempt=new Object(); AtomicInteger actions=new AtomicInteger(),cleanups=new AtomicInteger();
        if(mode.equals("active_capture")) {
            q.execute(actions::incrementAndGet); refuse(()->q.captureDrain(attempt,3)); worker.run();
        } else if(mode.equals("actual_active") || mode.equals("actual_cleanup")) {
            ExecutorService executor=Executors.newSingleThreadExecutor(); InputQueue real=new InputQueue(executor);
            CountDownLatch entered=new CountDownLatch(1),release=new CountDownLatch(1);
            InputQueue.DrainScope s=real.captureDrain(attempt,3);
            try {
                boolean inAction=mode.equals("actual_active");
                real.touch(false,3,()->{if(inAction){entered.countDown();await(release);}actions.incrementAndGet();},
                    ()->{if(!inAction){entered.countDown();await(release);}cleanups.incrementAndGet();});
                await(entered); real.clear(); real.sealDrain(s,attempt,3);
                check(real.observeDrain(s,attempt,3)==null); check(release.getCount()==1);
                release.countDown(); executor.shutdown(); check(executor.awaitTermination(2,TimeUnit.SECONDS));
                check(real.matches(s,real.observeDrain(s,attempt,3)) && actions.get()==1 && cleanups.get()==1);
            } finally { release.countDown(); executor.shutdownNow(); }
        } else {
            InputQueue.DrainScope s=q.captureDrain(attempt,3);
            switch(mode) {
                case "idle": q.sealDrain(s,attempt,3);check(q.matches(s,q.observeDrain(s,attempt,3)));break;
                case "move":
                    q.touch(false,3,actions::incrementAndGet,cleanups::incrementAndGet);
                    for(int i=0;i<1000;i++) q.touch(true,3,actions::incrementAndGet,cleanups::incrementAndGet);
                    q.sealDrain(s,attempt,3);check(q.observeDrain(s,attempt,3)==null);worker.run();
                    check(actions.get()==2 && cleanups.get()==1001 && q.matches(s,q.observeDrain(s,attempt,3)));break;
                case "clear":
                    q.touch(false,3,actions::incrementAndGet,cleanups::incrementAndGet);q.clear();q.sealDrain(s,attempt,3);
                    check(q.observeDrain(s,attempt,3)==null);worker.run();
                    check(actions.get()==0 && cleanups.get()==1 && q.matches(s,q.observeDrain(s,attempt,3)));break;
                case "foreign_attempt": refuse(()->q.sealDrain(s,new Object(),3));q.sealDrain(s,attempt,3);break;
                case "foreign_queue": refuse(()->new InputQueue(worker).sealDrain(s,attempt,3));q.sealDrain(s,attempt,3);break;
                case "foreign_generation": refuse(()->q.sealDrain(s,attempt,4));q.sealDrain(s,attempt,3);break;
                case "second_capture": refuse(()->q.captureDrain(attempt,3));q.sealDrain(s,attempt,3);break;
                case "before_seal": refuse(()->q.observeDrain(s,attempt,3));refuse(()->q.sealDrain(s,attempt,3));break;
                case "second_seal": q.sealDrain(s,attempt,3);refuse(()->q.sealDrain(s,attempt,3));refuse(()->q.observeDrain(s,attempt,3));break;
                case "second_observe": q.sealDrain(s,attempt,3);q.observeDrain(s,attempt,3);refuse(()->q.observeDrain(s,attempt,3));break;
                case "after_seal": q.sealDrain(s,attempt,3);q.execute(actions::incrementAndGet);worker.run();check(actions.get()==1);refuse(()->q.observeDrain(s,attempt,3));break;
                case "later_generation": q.touch(false,4,actions::incrementAndGet,cleanups::incrementAndGet);worker.run();check(actions.get()==1 && cleanups.get()==1);refuse(()->q.sealDrain(s,attempt,3));break;
                case "capacity": for(int i=0;i<70;i++)q.touch(false,3,actions::incrementAndGet,cleanups::incrementAndGet);worker.run();check(actions.get()==70 && cleanups.get()==70);refuse(()->q.sealDrain(s,attempt,3));break;
                case "action_failure": q.execute(()->{throw new IllegalStateException("owned action failure");});refuse(worker::run);refuse(()->q.sealDrain(s,attempt,3));break;
                case "cleanup_failure":
                    q.touch(false,3,actions::incrementAndGet,()->{throw new IllegalStateException("owned cleanup failure");});
                    q.touch(false,3,actions::incrementAndGet,cleanups::incrementAndGet);refuse(q::clear);worker.run();
                    check(actions.get()==0 && cleanups.get()==0);refuse(()->q.sealDrain(s,attempt,3));break;
                case "shutdown": q.shutdownNow();refuse(()->q.sealDrain(s,attempt,3));break;
                case "rejection":
                    InputQueue rejected=new InputQueue(task->{throw new IllegalStateException("reject");});
                    InputQueue.DrainScope rs=rejected.captureDrain(attempt,3);refuse(()->rejected.execute(actions::incrementAndGet));
                    refuse(()->rejected.sealDrain(rs,attempt,3));break;
                case "marker":
                    q.sealDrain(s,attempt,3);InputQueue.DrainObservation marker=q.observeDrain(s,attempt,3);
                    q.execute(actions::incrementAndGet);worker.run();check(q.matches(s,marker) && actions.get()==1);
                    q.invalidateDrain(s);check(!q.matches(s,marker));break;
                default:throw new AssertionError(mode);
            }
        }
        System.out.println("input boundary PASS; codec audio remote input PM lease release false");
    }
}
