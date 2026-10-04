package local.remoteandroid.direct;

import java.util.ArrayDeque;
import java.util.concurrent.Executor;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Serial control writes; keep only the latest pending MOVE between touch boundaries. */
final class InputQueue implements Executor {
    private static final class Work {
        final boolean move;
        final int generation;
        final Runnable action, cleanup;
        final DrainScope observation;
        Work(boolean move, int generation, Runnable action, Runnable cleanup, DrainScope observation) {
            this.move = move; this.generation = generation; this.action = action; this.cleanup = cleanup;
            this.observation = observation;
        }
        void release(boolean actionFailed) {
            boolean normal = false;
            try { if (cleanup != null) cleanup.run(); normal = true; }
            finally { if (observation != null) observation.queue.finished(this, actionFailed || !normal); }
        }
    }
    private final Executor worker;
    private final ArrayDeque<Work> pending = new ArrayDeque<>();
    private boolean draining, closed;
    // Null in all ordinary paths. This is local bookkeeping, not authorization.
    private DrainScope observedScope;

    static final class DrainScope {
        private final InputQueue queue;
        private final Object attempt;
        private final int generation;
        private long outstanding;
        private boolean sealed, unknown, observed;
        private Work failedWork;
        private ArrayDeque<Work> unreleasedBatch;
        private DrainScope(InputQueue queue, Object attempt, int generation) {
            this.queue=queue; this.attempt=attempt; this.generation=generation;
        }
    }
    static final class DrainObservation {
        private final DrainScope scope;
        private DrainObservation(DrainScope scope) { this.scope=scope; }
    }
    /** Explicit same-process candidate. Must begin at a truly idle queue; it
     * cannot retrospectively own an already-running input action. */
    synchronized DrainScope captureDrain(Object attempt, int generation) {
        if(attempt==null || generation<0 || closed || draining || !pending.isEmpty() || observedScope!=null)
            throw new IllegalStateException("input_capture_not_idle");
        return observedScope=new DrainScope(this,attempt,generation);
    }
    private void own(DrainScope scope, Object attempt, int generation) {
        if(scope==null || scope.queue!=this || observedScope!=scope || scope.attempt!=attempt
                || scope.generation!=generation || scope.unknown || scope.observed)
            throw new IllegalStateException("input_scope_changed_or_unknown");
    }
    synchronized void sealDrain(DrainScope scope, Object attempt, int generation) {
        own(scope,attempt,generation);
        if(scope.sealed) { scope.unknown=true; throw new IllegalStateException("input_seal_once"); }
        scope.sealed=true;
    }
    synchronized DrainObservation observeDrain(DrainScope scope, Object attempt, int generation) {
        own(scope,attempt,generation);
        if(!scope.sealed) { scope.unknown=true; throw new IllegalStateException("input_not_sealed"); }
        if(scope.outstanding!=0 || draining || !pending.isEmpty()) return null;
        scope.observed=true; observedScope=null;
        return new DrainObservation(scope);
    }
    synchronized boolean matches(DrainScope scope, DrainObservation observation) {
        return scope!=null && scope.queue==this && scope.observed && !scope.unknown
            && observation!=null && observation.scope==scope;
    }
    synchronized void invalidateDrain(DrainScope scope) {
        if(scope!=null && scope.queue==this) scope.unknown=true;
    }
    private synchronized void finished(Work work, boolean failed) {
        DrainScope scope=work.observation;
        if(scope.queue!=this || scope.outstanding<=0) { scope.unknown=true; return; }
        scope.outstanding--;
        if(failed) { scope.unknown=true; if(scope.failedWork==null) scope.failedWork=work; }
    }

    InputQueue() { this(Executors.newSingleThreadExecutor()); }
    InputQueue(Executor worker) { this.worker = worker; }
    @Override public void execute(Runnable action) { enqueue(false, 0, action, null); }
    void touch(boolean move, int generation, Runnable action, Runnable cleanup) {
        enqueue(move, generation, action, cleanup);
    }
    private void enqueue(boolean move, int generation, Runnable action, Runnable cleanup) {
        Work removed = null;
        synchronized (this) {
            DrainScope observation=observedScope;
            if(observation!=null && observation.unknown) observation=null;
            if(observation!=null) {
                if(observation.sealed || closed || generation!=0 && generation!=observation.generation)
                    observation.unknown=true;
                if(observation.outstanding>=64) { observation.unknown=true; observation=null; }
                else observation.outstanding++;
            }
            Work next = new Work(move, generation, action, cleanup, observation);
            if (closed) removed = next;
            else {
                Work tail = pending.peekLast();
                if (move && tail != null && tail.move && tail.generation == generation) removed = pending.removeLast();
                pending.addLast(next);
                if (!draining) { draining = true;
                    try { worker.execute(this::drain); }
                    catch(RuntimeException | Error failure) {
                        if(observation!=null) observation.unknown=true;
                        throw failure;
                    }
                }
            }
        }
        if (removed != null) removed.release(false);
    }
    private void drain() {
        boolean completed = false;
        try {
            while (true) {
                Work next;
                synchronized (this) {
                    next = pending.pollFirst();
                    if (next == null) { draining = false; completed = true; return; }
                }
                boolean normal=false;
                try { next.action.run(); normal=true; } finally { next.release(!normal); }
            }
        } finally {
            if (!completed) synchronized (this) {
                draining = false;
                if (!closed && !pending.isEmpty()) { draining = true; worker.execute(this::drain); }
            }
        }
    }
    void clear() {
        ArrayDeque<Work> removed;
        synchronized (this) { removed = new ArrayDeque<>(pending); pending.clear(); }
        try { for (Work work : removed) work.release(false); }
        catch(RuntimeException | Error failure) {
            synchronized(this) {
                DrainScope scope=observedScope;
                if(scope!=null && scope.unreleasedBatch==null) {
                    scope.unknown=true; scope.unreleasedBatch=new ArrayDeque<>();
                    for(Work work:removed) if(work.observation==scope) scope.unreleasedBatch.addLast(work);
                }
            }
            throw failure;
        }
    }
    void shutdownNow() {
        synchronized (this) { closed = true; if(observedScope!=null) observedScope.unknown=true; }
        clear();
        if (worker instanceof ExecutorService) ((ExecutorService) worker).shutdownNow();
    }
}
