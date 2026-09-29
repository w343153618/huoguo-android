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
        Work(boolean move, int generation, Runnable action, Runnable cleanup) {
            this.move = move; this.generation = generation; this.action = action; this.cleanup = cleanup;
        }
        void release() { if (cleanup != null) cleanup.run(); }
    }
    private final Executor worker;
    private final ArrayDeque<Work> pending = new ArrayDeque<>();
    private boolean draining, closed;

    InputQueue() { this(Executors.newSingleThreadExecutor()); }
    InputQueue(Executor worker) { this.worker = worker; }
    @Override public void execute(Runnable action) { enqueue(false, 0, action, null); }
    void touch(boolean move, int generation, Runnable action, Runnable cleanup) {
        enqueue(move, generation, action, cleanup);
    }
    private void enqueue(boolean move, int generation, Runnable action, Runnable cleanup) {
        Work next = new Work(move, generation, action, cleanup), removed = null;
        synchronized (this) {
            if (closed) removed = next;
            else {
                Work tail = pending.peekLast();
                if (move && tail != null && tail.move && tail.generation == generation) removed = pending.removeLast();
                pending.addLast(next);
                if (!draining) { draining = true; worker.execute(this::drain); }
            }
        }
        if (removed != null) removed.release();
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
                try { next.action.run(); } finally { next.release(); }
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
        for (Work work : removed) work.release();
    }
    void shutdownNow() {
        synchronized (this) { closed = true; }
        clear();
        if (worker instanceof ExecutorService) ((ExecutorService) worker).shutdownNow();
    }
}
