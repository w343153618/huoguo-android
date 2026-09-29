package local.remoteandroid.direct;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicReference;

/** Run with javac/java; no Android runtime, mocked decoder, or network is required. */
public final class DiagnosticMetricsCheck {
    private static final long BASE = 20_000_000_000L;
    private static final long MS = 1_000_000L;

    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }

    private static void close(double actual, double expected, String message) {
        if (Double.isNaN(actual) || Math.abs(actual - expected) > 0.00001)
            throw new AssertionError(message + ": " + actual + " != " + expected);
    }

    private static void irregularDurationAndEncodedBytes() {
        DiagnosticMetrics metrics = new DiagnosticMetrics();
        metrics.start(BASE, 30);
        metrics.received(40, 0, BASE, true);
        metrics.received(100, 0, BASE);
        metrics.received(200, 300_000, BASE + 300 * MS);
        metrics.received(300, 800_000, BASE + 800 * MS);
        metrics.rendered(BASE + 100 * MS);
        metrics.rendered(BASE + 400 * MS);
        metrics.rendered(BASE + 900 * MS);
        DiagnosticMetrics.Snapshot first = metrics.snapshot(BASE + 1250 * MS);
        close(first.elapsedMs, 1250, "snapshot uses elapsed time, not a nominal second");
        check(first.receivedFrames == 3 && first.renderedFrames == 3, "first counter delta");
        check(first.receivedBytes == 640, "configuration bytes included, configuration frame excluded");
        close(first.receiveFps, 2.4, "irregular receive rate");
        metrics.received(400, 1_450_000, BASE + 1450 * MS);
        metrics.rendered(BASE + 1550 * MS);
        DiagnosticMetrics.Snapshot second = metrics.snapshot(BASE + 2000 * MS);
        close(second.elapsedMs, 750, "second actual snapshot interval");
        check(second.receivedFrames == 1 && second.renderedFrames == 1, "incremental snapshot");
        check(second.receivedBytes == 400, "incremental bytes");
        close(second.receiveFps, 1000.0 / 750, "shorter snapshot rate");
        DiagnosticMetrics.Result result = metrics.finish(BASE + 2600 * MS);
        check(result.valid, "active rendered stage is a usable measurement");
        close(result.elapsedMs, 2600, "actual final duration");
        close(result.receiveFps, 4.0 / 2.6, "final rate uses actual duration");
        close(result.receiveMbps, 1040.0 * 8 / 2.6 / 1_000_000, "actual encoded throughput");
        check(result.receivedFrames == 4 && result.receivedBytes == 1040, "final totals");
        check(result.renderGapCount == 4, "internal and tail display gaps");
        close(result.maxRenderGapMs, 1050, "final freeze reaches measurement endpoint");
        check(metrics.finish(BASE + 9000 * MS) == result, "finish is idempotent");
        metrics.received(999, 3_000_000, BASE + 3000 * MS);
        check(metrics.finish(BASE + 9000 * MS).receivedFrames == 4, "finished stage is immutable");
    }

    private static void batchedAndOutOfOrderRenderCallbacks() {
        DiagnosticMetrics metrics = new DiagnosticMetrics();
        metrics.start(BASE, 30);
        for (int i = 0; i < 60; i++)
            metrics.received(1000, i * 33_333L, BASE + 10 * MS + i * 33_333_000L);
        DiagnosticMetrics.Snapshot beforeCallbacks = metrics.snapshot(BASE + 1000 * MS);
        check(beforeCallbacks.renderedFrames == 0, "delivery may trail actual rendering");
        // All callbacks arrive together, in the opposite order from actual display.
        for (int i = 59; i >= 0; i--)
            metrics.rendered(BASE + 20 * MS + i * 33_333_000L);
        DiagnosticMetrics.Snapshot afterCallbacks = metrics.snapshot(BASE + 2000 * MS);
        check(afterCallbacks.renderedFrames == 60, "delayed callbacks counted exactly once");
        check(beforeCallbacks.receivedFrames + afterCallbacks.receivedFrames == 60,
                "snapshot counts reconcile");
        DiagnosticMetrics.Result result = metrics.finish(BASE + 2000 * MS);
        check(result.renderedFrames == 60 && result.renderGapCount == 0,
                "callback coalescing is not a display freeze");
        close(result.renderFps, 30, "render cadence follows actual timestamps");
        close(result.maxRenderGapMs, 33.333, "sorted actual render gaps");
        close(result.renderIntervalJitterMs, 0, "batching does not fabricate render jitter");
        check(result.lateRenderCallbacks == 30, "late callbacks disclosed");
    }

    private static void warmupResetAndIntentionalPause() {
        DiagnosticMetrics metrics = new DiagnosticMetrics();
        metrics.received(900, 0, BASE - MS);
        metrics.rendered(BASE - MS);
        metrics.start(BASE, 30);
        metrics.received(900, 0, BASE + MS);
        metrics.rendered(BASE + 2 * MS);
        long measuredStart = BASE + 5000 * MS;
        metrics.start(measuredStart, 30);
        metrics.received(900, 0, BASE + 3 * MS);
        metrics.rendered(BASE + 4 * MS);
        metrics.discarded(BASE + 5 * MS);
        metrics.rtt(80, BASE + 6 * MS);
        metrics.received(50, 0, measuredStart, true);
        metrics.received(200, 100_000, measuredStart + 80 * MS);
        metrics.rendered(measuredStart + 100 * MS);
        DiagnosticMetrics.Result reset = metrics.finish(measuredStart + 1200 * MS);
        check(reset.receivedFrames == 1 && reset.renderedFrames == 1
                && reset.receivedBytes == 250, "warm-up and previous stage are excluded");
        check(reset.discardedFrames == 0 && reset.rttSampleCount == 0, "all counters reset");

        metrics.start(BASE, 30);
        metrics.received(100, 0, BASE + 100 * MS);
        metrics.rendered(BASE + 100 * MS);
        metrics.pause(BASE + 250 * MS);
        metrics.received(999, 1_000_000, BASE + 5000 * MS);
        metrics.discarded(BASE + 5000 * MS);
        metrics.rtt(999, BASE + 5000 * MS);
        metrics.resume(BASE + 10_250 * MS);
        metrics.rendered(BASE + 5000 * MS); // Delayed callback from the intentional pause.
        metrics.received(100, 10_000_000, BASE + 10_350 * MS);
        metrics.rendered(BASE + 10_350 * MS);
        DiagnosticMetrics.Result pause = metrics.finish(BASE + 11_000 * MS);
        check(pause.valid && pause.receivedFrames == 2 && pause.renderedFrames == 2,
                "only active periods measured");
        close(pause.elapsedMs, 1000, "intentional pause excluded from denominator");
        close(pause.wallElapsedMs, 11_000, "wall duration remains auditable");
        close(pause.maxRenderGapMs, 650, "no freeze crosses intentional transition");
        check(pause.renderGapCount == 1 && pause.renderIntervalSamples == 0,
                "interval jitter does not span a paused period");
        check(pause.discardedFrames == 0 && pause.rttSampleCount == 0, "paused events excluded");
    }

    private static void emptyStageAndDecodeDisplayStall() {
        DiagnosticMetrics metrics = new DiagnosticMetrics();
        metrics.start(BASE, 30);
        DiagnosticMetrics.Result empty = metrics.finish(BASE + 2000 * MS);
        check(!empty.valid && "no_video_frames_received".equals(empty.invalidReason),
                "empty stage cannot fabricate success");
        check(empty.renderGapCount == 1 && empty.receiveGapCount == 1, "entire empty stage is a gap");
        close(empty.maxRenderGapMs, 2000, "no callback still counts full animated freeze");
        check(Double.isNaN(empty.lastRenderAgoMs) && Double.isNaN(empty.rttP50Ms),
                "missing timestamp and RTT stay unavailable");

        metrics.start(BASE, 30);
        for (int i = 0; i < 31; i++) {
            metrics.received(100, i * 64_000L, BASE + i * 64 * MS);
            if (i < 5) metrics.rendered(BASE + 20 * MS + i * 64 * MS);
        }
        DiagnosticMetrics.Result stalled = metrics.finish(BASE + 2000 * MS);
        check(stalled.valid && stalled.renderGapCount == 1 && stalled.receiveGapCount == 0,
                "usable measurements preserve a serious display stall");
        close(stalled.lastReceiveAgoMs, 80, "encoded video continues arriving");
        close(stalled.lastRenderAgoMs, 1724, "display no longer advances");
        close(stalled.maxRenderGapMs, 1724, "tail freeze included");
        close(stalled.maxReceiveGapMs, 80, "receive and render gaps remain separate");

        metrics.start(BASE, 30, false);
        DiagnosticMetrics.Result staticSource = metrics.finish(BASE + 2000 * MS);
        check(staticSource.renderGapCount == 0 && Double.isNaN(staticSource.maxRenderGapMs),
                "unknown/static motion does not claim animated freezing");
    }

    private static void invalidInputsAndRetrospectiveEndpoint() {
        DiagnosticMetrics metrics = new DiagnosticMetrics();
        DiagnosticMetrics.Result unstarted = metrics.finish(BASE);
        check(!unstarted.valid && "not_started".equals(unstarted.invalidReason)
                && Double.isNaN(unstarted.wallElapsedMs), "unstarted invalid");
        metrics.start(BASE, 30);
        metrics.received(10, 0, BASE + MS);
        metrics.rendered(BASE + 2 * MS);
        DiagnosticMetrics.Result shortRun = metrics.finish(BASE + 999 * MS);
        check(!shortRun.valid && "measurement_too_short".equals(shortRun.invalidReason),
                "subsecond stage cannot be accepted");
        check(Double.isNaN(shortRun.renderIntervalJitterMs), "one frame is not a jitter sample");

        metrics.start(BASE, 30);
        metrics.received(10, 0, BASE + MS);
        metrics.received(10, 1_000_000, BASE + 1000 * MS);
        metrics.received(-9, 1_100_000, BASE + 1100 * MS);
        metrics.received(999, 2_000_000, BASE + 2000 * MS); // Callback-drain period.
        metrics.rendered(BASE + 2 * MS);
        metrics.rendered(BASE + 1480 * MS); // Arrives late but was rendered before endpoint.
        metrics.rendered(BASE + 1510 * MS); // Actual render after endpoint.
        metrics.rtt(Double.NaN, BASE + 10 * MS);
        metrics.rtt(-1, BASE + 10 * MS);
        double[] samples = {80, 20, 30, 40, 50};
        for (double sample : samples) metrics.rtt(sample, BASE + 500 * MS);
        metrics.rtt(999, BASE + 2000 * MS);
        DiagnosticMetrics.Result drained = metrics.finish(BASE + 1500 * MS);
        check(drained.valid && drained.receivedFrames == 2 && drained.renderedFrames == 2
                && drained.receivedBytes == 20, "drain excludes post-end traffic/rendering");
        check(drained.invalidEventCount == 3, "invalid observations disclosed");
        check(drained.rttSampleCount == 5, "only finite in-stage RTTs");
        close(drained.rttP50Ms, 40, "RTT median");
        close(drained.rttP95Ms, 80, "RTT upper percentile");

        metrics.start(BASE, 30);
        metrics.snapshot(BASE + 1000 * MS);
        metrics.pause(BASE + 900 * MS); // Must not erase already-sampled active time.
        metrics.received(10, 1_100_000, BASE + 1100 * MS);
        DiagnosticMetrics.Snapshot following = metrics.snapshot(BASE + 1200 * MS);
        check(following.receivedFrames == 1 && following.valid,
                "backdated pause rejected without corrupting counter deltas");
        DiagnosticMetrics.Snapshot reversed = metrics.snapshot(BASE + 900 * MS);
        check(!reversed.valid && Double.isNaN(reversed.receiveFps), "clock reversal cannot produce rate");
        DiagnosticMetrics.Result reversedEnd = metrics.finish(BASE - MS);
        check(!reversedEnd.valid && "measurement_clock_reversed".equals(reversedEnd.invalidReason)
                && Double.isNaN(reversedEnd.renderFps), "reversed endpoint invalid");
    }

    private static void cadenceAndSourceClockDistinction() {
        DiagnosticMetrics metrics = new DiagnosticMetrics();
        metrics.start(BASE, 30);
        // Local clock and source clock differ by an arbitrary large origin.
        long ptsOrigin = 987_000_000_000L;
        for (int i = 0; i < 5; i++) {
            metrics.received(10, ptsOrigin + i * 250_000L, BASE + i * 250 * MS);
            metrics.rendered(BASE + i * 250 * MS);
        }
        DiagnosticMetrics.Result equalThreshold = metrics.finish(BASE + 1000 * MS);
        check(equalThreshold.valid && equalThreshold.renderGapCount == 0,
                "gap must exceed the threshold, equality is not a stall");
        close(equalThreshold.gapThresholdMs, 250, "30 FPS freeze threshold");
        close(equalThreshold.sourceIntervalMeanMs, 250, "PTS intervals retain source units");
        close(equalThreshold.arrivalResidualJitterMs, 0, "separate clocks cannot create absolute latency");

        metrics.start(BASE, 5);
        metrics.received(10, 0, BASE);
        metrics.rendered(BASE);
        DiagnosticMetrics.Result lowFps = metrics.finish(BASE + 1000 * MS);
        close(lowFps.gapThresholdMs, 1000, "threshold scales to five target frame periods");
        check(lowFps.renderGapCount == 0, "low FPS threshold boundary");
    }

    private static void concurrentCounters() throws Exception {
        DiagnosticMetrics metrics = new DiagnosticMetrics();
        metrics.start(BASE, 30);
        CountDownLatch start = new CountDownLatch(1);
        AtomicReference<Throwable> error = new AtomicReference<>();
        Thread[] workers = new Thread[4];
        for (int t = 0; t < workers.length; t++) {
            workers[t] = new Thread(() -> {
                try {
                    start.await();
                    for (int i = 0; i < 250; i++) {
                        long ns = BASE + (10 + i) * MS;
                        metrics.received(17, i * 1000L, ns);
                        metrics.rendered(ns);
                        if (i % 10 == 0) metrics.discarded(ns);
                        if (i % 25 == 0) metrics.rtt(25, ns);
                    }
                } catch (Throwable failure) { error.compareAndSet(null, failure); }
            }, "diagnostic-metrics-check-" + t);
            workers[t].start();
        }
        start.countDown();
        for (Thread worker : workers) worker.join();
        if (error.get() != null) throw new AssertionError("worker failure", error.get());
        DiagnosticMetrics.Result result = metrics.finish(BASE + 2000 * MS);
        check(result.receivedFrames == 1000 && result.renderedFrames == 1000
                && result.receivedBytes == 17_000, "parallel receive/render counters cannot race");
        check(result.discardedFrames == 100 && result.rttSampleCount == 40,
                "parallel ancillary counters cannot race");
    }

    private static void matchedPtsClientPipelineTime() {
        DiagnosticMetrics metrics = new DiagnosticMetrics();
        metrics.start(BASE, 30);
        // PTS has an arbitrary different origin; only local render minus local arrival matters.
        long ptsOrigin = 9_876_000_000_000L;
        metrics.received(20, ptsOrigin, BASE + MS, true); // Config must not become the arrival.
        metrics.received(100, ptsOrigin, BASE + 10 * MS);
        metrics.received(100, ptsOrigin + 1, BASE + 20 * MS);
        metrics.received(100, ptsOrigin + 2, BASE + 30 * MS);
        metrics.received(100, ptsOrigin, BASE + 40 * MS); // First actual arrival wins.
        metrics.rendered(ptsOrigin + 2, BASE + 230 * MS);
        metrics.rendered(ptsOrigin, BASE + 60 * MS);
        metrics.rendered(ptsOrigin + 1, BASE + 120 * MS);
        metrics.rendered(BASE + 240 * MS); // Legacy callback has no matchable PTS.
        metrics.received(100, ptsOrigin + 3, BASE + 250 * MS);
        metrics.rendered(ptsOrigin + 3, BASE + 250 * MS); // Zero pipeline time is not a sample.
        metrics.received(100, ptsOrigin + 4, BASE + 400 * MS);
        metrics.rendered(ptsOrigin + 4, BASE + 390 * MS); // Negative pipeline time is rejected.
        metrics.received(100, -1, BASE + 500 * MS);
        metrics.rendered(-1, BASE + 550 * MS); // Unknown PTS still counts as rendered.
        metrics.received(100, ptsOrigin + 5, BASE - MS); // Warm-up arrival cannot be matched.
        metrics.rendered(ptsOrigin + 5, BASE + 600 * MS);
        metrics.received(100, ptsOrigin + 6, BASE + 1380 * MS);
        metrics.received(999, ptsOrigin + 7, BASE + 1600 * MS); // During callback drain.
        metrics.rendered(ptsOrigin + 7, BASE + 1650 * MS);
        metrics.rendered(ptsOrigin + 6, BASE + 1480 * MS); // Late callback, actual pre-end render.
        DiagnosticMetrics.Result result = metrics.finish(BASE + 1500 * MS);
        check(result.valid && result.clientPipelineSamples == 4,
                "only matched positive in-window pairs become pipeline samples");
        close(result.clientPipelineP50Ms, 100, "pipeline median uses local clocks and first receive");
        close(result.clientPipelineP95Ms, 200, "pipeline p95 is independent of source PTS origin");

        metrics.start(BASE, 30);
        metrics.received(100, 0, BASE + 50 * MS); // PTS zero is a valid source identity.
        metrics.rendered(0, BASE + 150 * MS);
        metrics.pause(BASE + 250 * MS);
        metrics.resume(BASE + 1250 * MS);
        metrics.rendered(0, BASE + 1450 * MS); // Must not match across the intentional pause.
        metrics.received(100, 0, BASE + 1460 * MS);
        metrics.rendered(0, BASE + 1490 * MS);
        DiagnosticMetrics.Result paused = metrics.finish(BASE + 2000 * MS);
        check(paused.clientPipelineSamples == 2, "matching is confined to one active window");
        close(paused.clientPipelineP50Ms, 30, "new window starts its own first arrival");
        close(paused.clientPipelineP95Ms, 100, "transition does not fabricate pipeline latency");

        metrics.start(BASE, 30);
        metrics.received(100, 10, BASE + 50 * MS);
        metrics.rendered(BASE + 100 * MS);
        DiagnosticMetrics.Result legacy = metrics.finish(BASE + 2000 * MS);
        check(legacy.clientPipelineSamples == 0 && Double.isNaN(legacy.clientPipelineP50Ms)
                && Double.isNaN(legacy.clientPipelineP95Ms), "unmatched latency remains unavailable");
    }

    public static void main(String[] args) throws Exception {
        irregularDurationAndEncodedBytes();
        batchedAndOutOfOrderRenderCallbacks();
        warmupResetAndIntentionalPause();
        emptyStageAndDecodeDisplayStall();
        invalidInputsAndRetrospectiveEndpoint();
        cadenceAndSourceClockDistinction();
        concurrentCounters();
        matchedPtsClientPipelineTime();
        System.out.println("DiagnosticMetrics: elapsed deltas, config bytes, actual render timing, "
                + "warm-up/pause reset, tail/source/display stalls, invalid stages, RTT, concurrency, "
                + "matched-PTS client pipeline timing PASS");
    }
}
