package local.remoteandroid.direct;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;

/** Exercises the actual production token gate; no Android, files or HTTP. */
public final class UpdateOperationGateCheck {
    private static int checks;
    private static void ok(boolean value) {
        checks++;
        if (!value) throw new AssertionError("check " + checks);
    }
    private static long acquire(UpdateOperationGate gate, int phase) {
        long token = gate.acquire(phase);
        ok(token > 0 && gate.current(token) && gate.phase(token) == phase);
        return token;
    }
    private static void transferred(UpdateOperationGate gate, long old, long next, int phase) {
        ok(next > 0 && next != old && gate.current(next));
        ok(!gate.current(old) && gate.phase(old) == UpdateOperationGate.IDLE);
        ok(gate.phase(next) == phase);
        ok(!gate.release(old));
        ok(!gate.transition(old, phase, UpdateOperationGate.CHECKING));
        ok(gate.current(next) && gate.phase(next) == phase);
    }
    public static void main(String[] args) throws Exception {
        UpdateOperationGate gate = new UpdateOperationGate();
        String mode = args[0];
        if (mode.equals("lease")) {
            ok(gate.phase() == UpdateOperationGate.IDLE && !gate.current(0));
            long token = acquire(gate, UpdateOperationGate.SELECTING);
            ok(gate.acquire(UpdateOperationGate.DOWNLOADING) == 0);
            ok(!gate.transition(token, UpdateOperationGate.DIALOG, UpdateOperationGate.DOWNLOADING));
            ok(gate.phase(token) == UpdateOperationGate.SELECTING);
            ok(gate.transition(token, UpdateOperationGate.SELECTING, UpdateOperationGate.CHECKING));
            ok(gate.phase(token) == UpdateOperationGate.CHECKING);
            ok(!gate.release(token + 100));
            ok(gate.release(token));
            ok(!gate.current(token) && gate.phase() == UpdateOperationGate.IDLE);
        } else if (mode.equals("stale")) {
            long old = acquire(gate, UpdateOperationGate.DOWNLOADING);
            ok(gate.release(old));
            long next = acquire(gate, UpdateOperationGate.CHECKING);
            ok(next != old);
            for (int i = 0; i < 8; i++) {
                ok(!gate.release(old));
                ok(!gate.transition(old, UpdateOperationGate.CHECKING, UpdateOperationGate.DIALOG));
                ok(gate.current(next) && gate.phase(next) == UpdateOperationGate.CHECKING);
            }
            ok(gate.release(next));
        } else if (mode.equals("flow")) {
            long token = acquire(gate, UpdateOperationGate.SELECTING);
            int[] phases = {UpdateOperationGate.SELECTING, UpdateOperationGate.CHECKING,
                UpdateOperationGate.DIALOG, UpdateOperationGate.DOWNLOADING,
                UpdateOperationGate.VERIFYING, UpdateOperationGate.WAIT_PERMISSION};
            for (int i = 1; i < phases.length; i++) {
                ok(gate.transition(token, phases[i - 1], phases[i]));
                ok(gate.acquire(UpdateOperationGate.SELECTING) == 0);
                ok(gate.current(token) && gate.phase(token) == phases[i]);
            }
            ok(gate.release(token));
        } else if (mode.equals("permission")) {
            long old = acquire(gate, UpdateOperationGate.WAIT_PERMISSION);
            ok(gate.resume(UpdateOperationGate.INSTALLER, UpdateOperationGate.IDLE) == 0);
            long next = gate.resume(UpdateOperationGate.WAIT_PERMISSION, UpdateOperationGate.VERIFYING);
            transferred(gate, old, next, UpdateOperationGate.VERIFYING);
            ok(gate.resume(UpdateOperationGate.WAIT_PERMISSION, UpdateOperationGate.VERIFYING) == 0);
            ok(gate.acquire(UpdateOperationGate.SELECTING) == 0);
            ok(gate.transition(next, UpdateOperationGate.VERIFYING, UpdateOperationGate.INSTALLER));
            ok(gate.release(next));
            long denied = acquire(gate, UpdateOperationGate.WAIT_PERMISSION);
            long completion = gate.resume(UpdateOperationGate.WAIT_PERMISSION, UpdateOperationGate.IDLE);
            ok(completion > 0 && completion != denied);
            ok(gate.phase() == UpdateOperationGate.IDLE && !gate.current(completion));
            ok(!gate.current(denied) && !gate.release(denied));
        } else if (mode.equals("installer")) {
            long old = acquire(gate, UpdateOperationGate.INSTALLER);
            ok(gate.resume(UpdateOperationGate.WAIT_PERMISSION, UpdateOperationGate.VERIFYING) == 0);
            long next = gate.resume(UpdateOperationGate.INSTALLER, UpdateOperationGate.IDLE);
            ok(next > 0 && next != old);
            ok(!gate.current(next) && !gate.current(old));
            ok(gate.phase() == UpdateOperationGate.IDLE && gate.phase(old) == UpdateOperationGate.IDLE);
            ok(!gate.release(old) && !gate.release(next));
            ok(!gate.transition(old, UpdateOperationGate.INSTALLER, UpdateOperationGate.CHECKING));
            ok(gate.resume(UpdateOperationGate.INSTALLER, UpdateOperationGate.IDLE) == 0);
            long fresh = acquire(gate, UpdateOperationGate.CHECKING);
            ok(fresh != old && fresh != next);
            ok(!gate.release(next));
            ok(gate.release(fresh));
        } else if (mode.equals("no_steal")) {
            for (int phase : new int[]{UpdateOperationGate.SELECTING, UpdateOperationGate.CHECKING,
                    UpdateOperationGate.DIALOG, UpdateOperationGate.DOWNLOADING, UpdateOperationGate.VERIFYING}) {
                long token = acquire(gate, phase);
                ok(gate.resume(UpdateOperationGate.WAIT_PERMISSION, UpdateOperationGate.VERIFYING) == 0);
                ok(gate.resume(UpdateOperationGate.INSTALLER, UpdateOperationGate.IDLE) == 0);
                ok(gate.current(token) && gate.phase(token) == phase);
                ok(gate.release(token));
            }
        } else if (mode.equals("concurrency")) {
            final int threads = 16;
            CountDownLatch ready = new CountDownLatch(threads), start = new CountDownLatch(1), done = new CountDownLatch(threads);
            AtomicInteger winners = new AtomicInteger();
            AtomicLong token = new AtomicLong();
            AtomicReference<Throwable> failure = new AtomicReference<>();
            for (int i = 0; i < threads; i++) {
                Thread worker = new Thread(() -> {
                    ready.countDown();
                    try {
                        if (!start.await(3, TimeUnit.SECONDS)) throw new AssertionError("start timeout");
                        long acquired = gate.acquire(UpdateOperationGate.DOWNLOADING);
                        if (acquired != 0) { winners.incrementAndGet(); token.set(acquired); }
                    } catch (Throwable problem) { failure.compareAndSet(null, problem); }
                    finally { done.countDown(); }
                }, "owned-update-gate-" + i);
                worker.setDaemon(true); worker.start();
            }
            ok(ready.await(3, TimeUnit.SECONDS));
            start.countDown();
            ok(done.await(3, TimeUnit.SECONDS));
            if (failure.get() != null) throw new AssertionError(failure.get());
            ok(winners.get() == 1 && gate.current(token.get()));
            ok(gate.phase(token.get()) == UpdateOperationGate.DOWNLOADING);
            ok(gate.release(token.get()));
        } else throw new AssertionError("unknown fixture");
        System.out.println(mode + ": " + checks + " actual updater operation checks passed");
    }
}
