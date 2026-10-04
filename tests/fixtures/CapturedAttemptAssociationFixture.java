package local.remoteandroid.direct;

import java.nio.channels.Pipe;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/** Real JVM refs/monitor/workers/pipe closures, synthetic Android state adapter. */
public final class CapturedAttemptAssociationFixture {
    static final long NS=1_000_000_000L;
    static void ok(boolean value) { if(!value) throw new AssertionError(); }
    interface Action { void run() throws Exception; }
    static void refused(Action action) throws Exception {
        try { action.run(); throw new AssertionError("unexpected success"); }
        catch(IllegalStateException expected) { }
    }
    static final class Setup implements AutoCloseable {
        final Object lock=new Object(), attempt=new Object(), receiver=new Object(),
                surface=new Object(), phoneClock=new Object();
        final Pipe[] pipes=new Pipe[3];
        final Thread[] workers=new Thread[3];
        final CountDownLatch go=new CountDownLatch(1), closed=new CountDownLatch(3),
                exit=new CountDownLatch(1);
        final AtomicReference<Throwable> problem=new AtomicReference<>();
        final CapturedAttemptAssociation association;
        CapturedAttemptAssociation.State state;
        long time=NS;
        boolean realClock;
        int reads,closeCalls; boolean closeSlow,throwClock,firstInvalid;
        Setup() throws Exception {
            state=current(attempt,receiver,surface,phoneClock,1);
            association=new CapturedAttemptAssociation(lock,()->{
                ok(Thread.holdsLock(lock)); reads++;
                if(firstInvalid && reads==1) return new CapturedAttemptAssociation.State(attempt,null,
                        receiver,surface,phoneClock,1,1,true,true,false,false,false);
                return state;
            },()->{if(throwClock)throw new IllegalStateException("clock source failure");return realClock?System.nanoTime():time;});
            for(int i=0;i<3;i++) {
                final int index=i; pipes[i]=Pipe.open();
                workers[i]=new Thread(()->{
                    try {
                        if(!go.await(2,TimeUnit.SECONDS)) throw new AssertionError("fixture start timeout");
                        association.normalCloseOnWorker(index);
                    } catch(Throwable failure) { problem.compareAndSet(null,failure); }
                    finally { closed.countDown(); }
                    try { if(!exit.await(2,TimeUnit.SECONDS)) throw new AssertionError("fixture exit timeout"); }
                    catch(Throwable failure) { problem.compareAndSet(null,failure); }
                },"owned-fixture-worker-"+i);
            }
        }
        CapturedAttemptAssociation.State current(Object a,Object r,Object s,Object c,long generation) {
            return new CapturedAttemptAssociation.State(a,null,r,s,c,generation,1,true,true,true,false,false);
        }
        CapturedAttemptAssociation.State retirement(boolean finished) {
            return new CapturedAttemptAssociation.State(null,finished?null:attempt,receiver,surface,
                    phoneClock,2,1,true,false,false,true,true);
        }
        CapturedAttemptAssociation.Resource resource(int i) {
            return new CapturedAttemptAssociation.Resource(pipes[i].source(),workers[i],()->{
                closeCalls++; pipes[i].source().close(); ok(!pipes[i].source().isOpen());
                if(closeSlow)time+=3*NS;
            });
        }
        void capture() { association.capture((realClock?System.nanoTime():time)+10*NS,
                resource(0),resource(1),resource(2)); }
        void startNormalClose() throws Exception {
            state=retirement(false); association.beginRetirement();
            for(Thread worker:workers) worker.start(); go.countDown();
            ok(closed.await(2,TimeUnit.SECONDS));
            if(problem.get()!=null) throw new AssertionError(problem.get());
            state=retirement(true);
        }
        void joined() throws Exception { exit.countDown(); association.completeObservation(); }
        public void close() throws java.io.IOException {
            // Only this fixture's actual threads and pipe endpoints. Not remote cleanup.
            go.countDown(); exit.countDown();
            for(Thread worker:workers) if(worker.isAlive()) {
                try { worker.join(2500); }
                catch(InterruptedException interrupted) {
                    Thread.currentThread().interrupt(); throw new java.io.IOException(interrupted);
                }
                ok(!worker.isAlive());
            }
            for(Pipe pipe:pipes) { pipe.source().close(); pipe.sink().close(); }
        }
    }
    public static void main(String[] args) throws Exception {
        String mode=args[0];
        ok(System.getProperty("association.ambient") == null);
        try(Setup s=new Setup()) {
            if(mode.equals("inert")) {
                ok(s.reads==0 && s.closeCalls==0);
                ok(s.association.phase()==CapturedAttemptAssociation.Phase.INERT);
            } else {
                if(mode.equals("real_workers") || mode.equals("death_join") || mode.equals("lock_wait")) s.realClock=true;
                if(mode.equals("initial_invalid")) {
                    s.firstInvalid=true;refused(s::capture);
                    ok(s.association.phase()==CapturedAttemptAssociation.Phase.UNKNOWN);
                    System.out.println("PASS initial_invalid actual JVM association; Android adapter synthetic; release false");return;
                }
                s.capture();
                if(mode.equals("identity") || mode.equals("real_workers")) {
                    s.association.observe(v->{
                        ok(v.attempt()==s.attempt && v.receiver()==s.receiver
                                && v.surface()==s.surface && v.phoneClock()==s.phoneClock);
                        ok(v.resource(0)==s.pipes[0].source());
                    });
                    if(mode.equals("real_workers")) {
                        s.startNormalClose(); ok(s.closeCalls==3); s.joined();
                        ok(s.association.phase()==CapturedAttemptAssociation.Phase.CLOSED_OBSERVATION);
                        for(Thread worker:s.workers) ok(worker.getState()==Thread.State.TERMINATED);
                        refused(()->s.association.observe(v->{}));
                    }
                } else if(mode.equals("later_attempt")) {
                    s.state=s.current(new Object(),s.receiver,s.surface,s.phoneClock,2);
                    refused(()->s.association.observe(v->{}));
                } else if(mode.equals("receiver") || mode.equals("surface") || mode.equals("clock_domain")) {
                    s.state=s.current(s.attempt,mode.equals("receiver")?new Object():s.receiver,
                            mode.equals("surface")?new Object():s.surface,
                            mode.equals("clock_domain")?new Object():s.phoneClock,1);
                    refused(()->s.association.observe(v->{}));
                } else if(mode.equals("mutation_inside")) {
                    refused(()->s.association.observe(v->{s.state=s.current(new Object(),s.receiver,s.surface,s.phoneClock,2);}));
                } else if(mode.equals("slow_callback")) {
                    refused(()->s.association.observe(v->{s.time+=3*NS;}));
                } else if(mode.equals("clock_exception")) {
                    s.throwClock=true;refused(()->s.association.observe(v->{}));
                } else if(mode.equals("death_callback")) {
                    refused(()->s.association.observe(v->s.association.peerDied()));
                } else if(mode.equals("slow_close")) {
                    s.closeSlow=true;s.state=s.retirement(false);s.association.beginRetirement();
                    for(Thread worker:s.workers)worker.start();s.go.countDown();
                    ok(s.closed.await(2,TimeUnit.SECONDS));ok(s.closeCalls==1 && s.problem.get()!=null);
                } else if(mode.equals("lock_wait")) {
                    Thread caller=new Thread(()->{
                        try {refused(()->s.association.observe(v->{throw new AssertionError("late callback executed");}));}
                        catch(Throwable failure) {s.problem.set(failure);}
                    });
                    synchronized(s.lock) {
                        caller.start();long end=System.nanoTime()+NS;
                        while(caller.getState()!=Thread.State.BLOCKED && System.nanoTime()<end)Thread.yield();
                        ok(caller.getState()==Thread.State.BLOCKED);Thread.sleep(3100);
                    }
                    caller.join(1000);ok(!caller.isAlive() && s.problem.get()==null);
                } else if(mode.equals("pending_worker_deadline")) {
                    s.startNormalClose();
                    // Reuse no deadline: the original capture end stays exactly fixed.
                    s.time+=10*NS;refused(s.association::completeObservation);
                    for(Thread worker:s.workers)ok(worker.isAlive());
                } else if(mode.equals("repeat_capture")) {
                    refused(s::capture);
                } else if(mode.equals("expired")) {
                    s.time+=10*NS; refused(()->s.association.observe(v->{}));
                } else if(mode.equals("backward")) {
                    s.time--; refused(()->s.association.observe(v->{}));
                } else if(mode.equals("escaped")) {
                    CapturedAttemptAssociation.View[] saved={null};
                    s.association.observe(v->{saved[0]=v;ok(v.attempt()==s.attempt);});
                    refused(()->saved[0].attempt());
                } else if(mode.equals("foreign_thread")) {
                    s.association.observe(v->{
                        Thread foreign=new Thread(()->{
                            try { refused(()->v.attempt()); } catch(Exception e) { s.problem.set(e); }
                        });
                        foreign.start(); // Cannot run through the held original lock yet.
                        s.exit.countDown(); s.workers[0]=foreign;
                    });
                    s.workers[0].join(1000);ok(!s.workers[0].isAlive() && s.problem.get()==null);
                } else if(mode.equals("reentrant")) {
                    refused(()->s.association.observe(v->s.association.observe(x->{})));
                } else if(mode.equals("callback_exception")) {
                    try { s.association.observe(v->{throw new java.io.IOException("actual callback failure");});
                        throw new AssertionError(); } catch(java.io.IOException expected) { }
                } else if(mode.equals("peer_death")) {
                    s.association.peerDied(); refused(()->s.association.observe(v->{}));
                } else if(mode.equals("missing_close")) {
                    s.state=s.retirement(false);s.association.beginRetirement();
                    s.state=s.retirement(true);refused(s.association::completeObservation);
                    ok(s.closeCalls==0);
                } else if(mode.equals("foreign_close")) {
                    s.state=s.retirement(false);s.association.beginRetirement();
                    refused(()->s.association.normalCloseOnWorker(0));ok(s.closeCalls==0);
                } else if(mode.equals("later_before_close")) {
                    s.state=s.retirement(false);s.association.beginRetirement();
                    s.state=s.current(new Object(),s.receiver,s.surface,s.phoneClock,3);
                    for(Thread worker:s.workers)worker.start();s.go.countDown();
                    ok(s.closed.await(2,TimeUnit.SECONDS));ok(s.closeCalls==0 && s.problem.get()!=null);
                } else if(mode.equals("death_join")) {
                    s.startNormalClose();
                    Thread collector=new Thread(()->{
                        try { refused(s.association::completeObservation); }
                        catch(Exception e) { s.problem.set(e); }
                    });collector.start();
                    long end=System.nanoTime()+NS;
                    while(collector.getState()!=Thread.State.TIMED_WAITING && System.nanoTime()<end) Thread.yield();
                    ok(collector.getState()==Thread.State.TIMED_WAITING);
                    s.association.peerDied();s.exit.countDown();collector.join(1500);
                    ok(!collector.isAlive() && s.problem.get()==null);
                } else if(mode.equals("later_after_close")) {
                    s.startNormalClose();s.exit.countDown();
                    s.state=s.current(new Object(),s.receiver,s.surface,s.phoneClock,3);
                    refused(s.association::completeObservation);
                } else if(mode.equals("interrupted_join")) {
                    s.startNormalClose();Thread.currentThread().interrupt();
                    try {s.association.completeObservation();throw new AssertionError();}
                    catch(InterruptedException expected) {} finally {Thread.interrupted();}
                } else throw new AssertionError(mode);
                if(!mode.equals("identity"))ok(s.association.phase()==CapturedAttemptAssociation.Phase.UNKNOWN);
            }
        }
        System.out.println("PASS "+mode+" actual JVM association; Android adapter synthetic; release false");
    }
}
