package local.remoteandroid.direct;

/** Inert cooperative interface candidate; no App/helper call site or transport.
 * Actual references stay in their creating JVM. Observation is not permission,
 * server lease, native resource quiescence, PM cleanup, or gateway release.
 * A reviewed App adapter must supply state under its existing lock, and close
 * callbacks must use the actual captured codec/audio/input resources. */
final class CapturedAttemptAssociation {
    interface Clock { long nanoTime(); }
    interface StateReader { State read(); }
    interface Operation { void run(View view) throws Exception; }
    interface NormalClose { void close() throws Exception; }
    enum Phase { INERT, OBSERVING, RETIRING, CLOSED_OBSERVATION, UNKNOWN }
    static final long COMMAND_NS = 3_000_000_000L;
    static final long MAX_ASSOCIATION_NS = 30_000_000_000L;

    static final class State {
        final Object current, retiring, receiver, surface, phoneClock;
        final long generation, activityGeneration;
        final boolean alive, running, validSurface, cancelled, stopped;
        State(Object current, Object retiring, Object receiver, Object surface,
                Object phoneClock, long generation, long activityGeneration,
                boolean alive, boolean running, boolean validSurface,
                boolean cancelled, boolean stopped) {
            this.current=current; this.retiring=retiring; this.receiver=receiver;
            this.surface=surface; this.phoneClock=phoneClock;
            this.generation=generation; this.activityGeneration=activityGeneration;
            this.alive=alive; this.running=running; this.validSurface=validSurface;
            this.cancelled=cancelled; this.stopped=stopped;
        }
    }
    static final class Resource {
        final Object identity;
        final Thread worker;
        final NormalClose closer;
        Resource(Object identity, Thread worker, NormalClose closer) {
            if(identity==null || worker==null || closer==null)
                throw new IllegalArgumentException("missing_actual_resource");
            this.identity=identity; this.worker=worker; this.closer=closer;
        }
    }
    /** A phase-scoped same-JVM capability. It is unusable after callback return,
     * on another thread, or outside the original lock. No serialization API. */
    final class View {
        private final long serial;
        private final Thread thread;
        private View(long serial) { this.serial=serial; thread=Thread.currentThread(); }
        private void check() {
            boolean held=Thread.holdsLock(lock);
            synchronized(lock) {
                if(!held || !busy || thread!=Thread.currentThread()
                        || serial!=phaseSerial || phase==Phase.UNKNOWN)
                    throw fail("escaped_or_foreign_view");
            }
        }
        Object attempt() { check(); return captured.current; }
        Object receiver() { check(); return captured.receiver; }
        Object surface() { check(); return captured.surface; }
        Object phoneClock() { check(); return captured.phoneClock; }
        Object resource(int index) { check(); return resources[index].identity; }
    }

    private final Object lock;
    private final StateReader reader;
    private final Clock clock;
    private Phase phase=Phase.INERT;
    private State captured;
    private Resource[] resources;
    private final boolean[] normalReturned=new boolean[3];
    private boolean busy;
    private long endNs, lastNs, phaseSerial;

    // Constructor reads nothing; no UI, threads, sockets, or live opt-in.
    CapturedAttemptAssociation(Object lock, StateReader reader, Clock clock) {
        if(lock==null || reader==null || clock==null)
            throw new IllegalArgumentException("missing_cooperative_adapter");
        this.lock=lock; this.reader=reader; this.clock=clock;
    }
    private IllegalStateException fail(String reason) {
        phase=Phase.UNKNOWN; return new IllegalStateException(reason);
    }
    private long now() {
        long value=clock.nanoTime();
        if(value<0 || value<lastNs || (phase!=Phase.INERT && value>=endNs))
            throw fail("original_clock_or_deadline");
        lastNs=value; return value;
    }
    private long callerStart() {
        try {
            long value=clock.nanoTime();
            if(value<0) throw new IllegalStateException("invalid_caller_clock");
            return value;
        } catch(RuntimeException | Error failure) {
            synchronized(lock) { phase=Phase.UNKNOWN; } throw failure;
        }
    }
    private long enter(Phase wanted, long callerStart) {
        if(busy || phase!=wanted) throw fail("closed_phase_or_reentrancy");
        busy=true; phaseSerial++;
        try {
            long current=now();
            if(current<callerStart || current-callerStart>=COMMAND_NS)
                throw fail("original_caller_budget");
            return commandEnd(callerStart);
        }
        catch(RuntimeException | Error failure) { phase=Phase.UNKNOWN; throw failure; }
    }
    private long commandEnd(long start) {
        return Math.min(endNs,start>Long.MAX_VALUE-COMMAND_NS
                ? Long.MAX_VALUE : start+COMMAND_NS);
    }
    private void within(long commandEnd) {
        if(now()>=commandEnd) throw fail("command_deadline");
    }
    private State state() {
        State value=reader.read();
        if(value==null || !value.alive) throw fail("peer_dead_or_missing_state");
        return value;
    }
    private void observing(State value) {
        if(value.current!=captured.current || value.retiring!=null
                || value.receiver!=captured.receiver || value.surface!=captured.surface
                || value.phoneClock!=captured.phoneClock
                || value.generation!=captured.generation
                || value.activityGeneration!=captured.activityGeneration
                || !value.running || !value.validSurface || value.cancelled || value.stopped)
            throw fail("later_or_changed_capture");
    }
    private void retirement(State value, boolean finished) {
        // This candidate models normal explicit cancellation, not natural expiry.
        // A later generation never inherits old cleanup rights.
        if(value.current!=null || value.retiring!=(finished?null:captured.current)
                || value.generation!=captured.generation+1
                || value.activityGeneration!=captured.activityGeneration
                || value.receiver!=captured.receiver || value.surface!=captured.surface
                || value.phoneClock!=captured.phoneClock || !value.cancelled || !value.stopped)
            throw fail("retirement_not_same_capture");
    }
    void capture(long originalEndNs, Resource codec, Resource audio, Resource input) {
        long callerStart=callerStart();
        synchronized(lock) {
            if(phase!=Phase.INERT || busy) throw fail("capture_once");
            busy=true; phaseSerial++;
            try {
                long start=now();
                if(start<callerStart || start-callerStart>=COMMAND_NS)
                    throw fail("original_caller_budget");
                if(originalEndNs<=start || originalEndNs-start>MAX_ASSOCIATION_NS
                        || codec==null || audio==null || input==null)
                    throw fail("missing_resources_or_original_bound");
                resources=new Resource[]{codec,audio,input};
                for(int i=0;i<3;i++) for(int j=0;j<i;j++)
                    if(resources[i].identity==resources[j].identity
                            || resources[i].worker==resources[j].worker)
                        throw fail("ambiguous_resource_or_worker");
                captured=state(); endNs=originalEndNs; phase=Phase.OBSERVING;
                if(captured.current==null || captured.receiver==null || captured.surface==null
                        || captured.phoneClock==null || captured.generation<0
                        || captured.generation==Long.MAX_VALUE || captured.activityGeneration<0)
                    throw fail("missing_actual_capture");
                observing(captured); observing(state()); within(commandEnd(callerStart));
            } catch(RuntimeException | Error failure) { phase=Phase.UNKNOWN; throw failure; }
            finally { busy=false; phaseSerial++; }
        }
    }
    void observe(Operation operation) throws Exception {
        long callerStart=callerStart();
        synchronized(lock) {
            long commandEnd=enter(Phase.OBSERVING,callerStart);
            try {
                if(operation==null) throw fail("missing_same_process_operation");
                observing(state()); within(commandEnd);
                operation.run(new View(phaseSerial));
                if(phase!=Phase.OBSERVING) throw fail("revoked_during_callback");
                observing(state()); within(commandEnd);
            } catch(Exception | Error failure) { phase=Phase.UNKNOWN; throw failure; }
            finally { busy=false; phaseSerial++; }
        }
    }
    // Observation only: this method never cancels an App or changes its state.
    void beginRetirement() {
        long callerStart=callerStart();
        synchronized(lock) {
            long commandEnd=enter(Phase.OBSERVING,callerStart);
            try { retirement(state(),false); within(commandEnd); phase=Phase.RETIRING; }
            catch(RuntimeException | Error failure) { phase=Phase.UNKNOWN; throw failure; }
            finally { busy=false; phaseSerial++; }
        }
    }
    /** Called cooperatively by the exact captured worker during its normal close.
     * Successful callback return is observed, not an assertion of native quiescence. */
    void normalCloseOnWorker(int index) throws Exception {
        long callerStart=callerStart();
        synchronized(lock) {
            long commandEnd=enter(Phase.RETIRING,callerStart);
            try {
                if(index<0 || index>=3 || normalReturned[index]
                        || Thread.currentThread()!=resources[index].worker)
                    throw fail("foreign_or_repeat_resource_close");
                retirement(state(),false); within(commandEnd);
                resources[index].closer.close();
                if(phase!=Phase.RETIRING) throw fail("revoked_during_close");
                retirement(state(),false); within(commandEnd); normalReturned[index]=true;
            } catch(Exception | Error failure) { phase=Phase.UNKNOWN; throw failure; }
            finally { busy=false; phaseSerial++; }
        }
    }
    void completeObservation() throws InterruptedException {
        long callerStart=callerStart(),commandEnd;
        synchronized(lock) {
            commandEnd=enter(Phase.RETIRING,callerStart);
            try {
                retirement(state(),true); within(commandEnd);
                for(int i=0;i<3;i++) if(!normalReturned[i]
                        || resources[i].worker==Thread.currentThread())
                    throw fail("normal_close_unobserved");
            } catch(RuntimeException | Error failure) {
                phase=Phase.UNKNOWN; busy=false; phaseSerial++; throw failure;
            }
        }
        // Never join while holding the App lock needed by normal completion.
        try {
            for(Resource resource:resources) {
                long left;
                synchronized(lock) { within(commandEnd); left=commandEnd-lastNs; }
                resource.worker.join(left/1_000_000L,(int)(left%1_000_000L));
                synchronized(lock) {
                    within(commandEnd);
                    if(resource.worker.getState()!=Thread.State.TERMINATED)
                        throw fail("actual_worker_not_terminated");
                }
            }
            synchronized(lock) {
                if(phase!=Phase.RETIRING) throw fail("peer_died_during_join");
                retirement(state(),true); within(commandEnd); phase=Phase.CLOSED_OBSERVATION;
            }
        } catch(InterruptedException | RuntimeException | Error failure) {
            synchronized(lock) { phase=Phase.UNKNOWN; } throw failure;
        } finally { synchronized(lock) { busy=false; phaseSerial++; } }
    }
    void peerDied() { synchronized(lock) { phase=Phase.UNKNOWN; phaseSerial++; } }
    Phase phase() { synchronized(lock) { return phase; } }
    // No release/destructor: UNKNOWN retains captured refs/resources. Even closed
    // observation does not establish PM, codec/audio/input or gateway release.
}
