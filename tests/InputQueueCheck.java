package local.remoteandroid.direct;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executor;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

public final class InputQueueCheck {
    static final class Manual implements Executor {
        final ArrayDeque<Runnable> tasks = new ArrayDeque<>();
        public void execute(Runnable task) { tasks.add(task); }
        void run() { while (!tasks.isEmpty()) tasks.remove().run(); }
    }
    static void check(boolean value) { if (!value) throw new AssertionError(); }
    public static void main(String[] args) throws Exception {
        Manual manual = new Manual(); InputQueue q = new InputQueue(manual);
        List<String> sent = new ArrayList<>(); AtomicInteger released = new AtomicInteger();
        q.touch(false, 1, () -> sent.add("down"), released::incrementAndGet);
        for (int i = 1; i <= 1000; i++) { final int n = i; q.touch(true, 1, () -> sent.add("move" + n), released::incrementAndGet); }
        q.touch(false, 1, () -> sent.add("pointerDown"), released::incrementAndGet);
        for (int i = 1001; i <= 2000; i++) { final int n = i; q.touch(true, 1, () -> sent.add("move" + n), released::incrementAndGet); }
        q.touch(false, 1, () -> sent.add("pointerUp"), released::incrementAndGet);
        for (int i = 2001; i <= 3000; i++) { final int n = i; q.touch(true, 1, () -> sent.add("move" + n), released::incrementAndGet); }
        q.touch(false, 1, () -> sent.add("up"), released::incrementAndGet);
        check(manual.tasks.size() == 1); manual.run();
        check(sent.equals(Arrays.asList("down", "move1000", "pointerDown", "move2000", "pointerUp", "move3000", "up")));
        check(released.get() == 3004);
        // Control commands and generation boundaries must not be crossed by MOVE replacement.
        sent.clear(); q.touch(true, 1, () -> sent.add("g1"), null); q.touch(true, 2, () -> sent.add("g2"), null);
        q.execute(() -> sent.add("key")); q.touch(true, 2, () -> sent.add("afterKey"), null); manual.run();
        check(sent.equals(Arrays.asList("g1", "g2", "key", "afterKey")));
        // Clear/shutdown recycle pending events exactly once and never execute them.
        sent.clear(); int before = released.get();
        q.touch(false, 3, () -> sent.add("oldDown"), released::incrementAndGet);
        q.touch(true, 3, () -> sent.add("oldMove"), released::incrementAndGet); q.clear(); manual.run();
        check(sent.isEmpty() && released.get() == before + 2);
        q.shutdownNow(); q.touch(true, 4, () -> sent.add("closed"), released::incrementAndGet);
        check(sent.isEmpty() && released.get() == before + 3);
        // A blocked writer must not hold the queue lock and prevent UI-side cancellation.
        ExecutorService worker = Executors.newSingleThreadExecutor(); InputQueue blocked = new InputQueue(worker);
        CountDownLatch entered = new CountDownLatch(1), unblock = new CountDownLatch(1), done = new CountDownLatch(1);
        AtomicInteger skipped = new AtomicInteger();
        try {
            blocked.execute(() -> { entered.countDown(); try { check(unblock.await(5, TimeUnit.SECONDS)); }
                catch (InterruptedException e) { Thread.currentThread().interrupt(); } });
            check(entered.await(5, TimeUnit.SECONDS));
            for (int i = 0; i < 1000; i++) blocked.touch(true, 1, () -> { throw new AssertionError("stale MOVE sent"); }, skipped::incrementAndGet);
            blocked.clear(); check(unblock.getCount() == 1 && skipped.get() == 1000);
            blocked.execute(done::countDown); unblock.countDown(); check(done.await(5, TimeUnit.SECONDS));
        } finally { unblock.countDown(); blocked.shutdownNow(); }
        System.out.println("InputQueue: latest pending MOVE, multi-pointer boundaries, generation/key order, recycling and blocked-writer cancellation PASS");
    }
}
