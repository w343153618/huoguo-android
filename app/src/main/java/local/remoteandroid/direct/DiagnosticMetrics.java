package local.remoteandroid.direct;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * A single, measured playback stage. All local timestamps use System.nanoTime's clock.
 * Source PTS is kept in microseconds on its own clock; it is never an absolute latency.
 *
 * Start after warm-up, and start a new collector (or reset this one) for every stage.
 * Render-event times come from the caller and their measurement basis must be
 * reported. DiagnosticRunner now passes Java callback receipt time because some
 * codecs return the requested future release target as an unverified vendor time.
 * A callback event is not proof of physical display presentation. Capture the
 * measurement endpoint, then finish with it; data after that endpoint is excluded.
 * Counts cannot establish TCP packet loss, optical tearing, or input-to-photon latency.
 */
public final class DiagnosticMetrics {
    public static final long NO_TIMESTAMP = Long.MIN_VALUE;
    private static final long MIN_VALID_ELAPSED_NS = 1_000_000_000L;
    private static final double NS_PER_MS = 1_000_000.0;

    private final List<Window> windows = new ArrayList<>();
    private final List<Packet> packets = new ArrayList<>();
    private final List<Render> renders = new ArrayList<>();
    private final List<Long> discards = new ArrayList<>();
    private final List<Rtt> rtts = new ArrayList<>();
    private boolean started, collecting, paused, expectedAnimated;
    private long startNs, snapshotNs, previousBytes, previousReceived, previousRendered;
    private long invalidEventCount, lateRenderCallbacks;
    private int fps;
    private Result finished;

    public synchronized void start(long measuredStartNs, int targetFps) {
        start(measuredStartNs, targetFps, true);
    }

    /** expectedAnimated enables initial/tail freeze accounting for controlled motion. */
    public synchronized void start(long measuredStartNs, int targetFps,
                                   boolean sourceExpectedAnimated) {
        if (targetFps < 1 || targetFps > 240) throw new IllegalArgumentException("Invalid FPS");
        packets.clear();
        renders.clear();
        discards.clear();
        rtts.clear();
        windows.clear();
        startNs = snapshotNs = measuredStartNs;
        fps = targetFps;
        expectedAnimated = sourceExpectedAnimated;
        previousBytes = previousReceived = previousRendered = 0;
        invalidEventCount = lateRenderCallbacks = 0;
        started = collecting = true;
        paused = false;
        finished = null;
        windows.add(new Window(measuredStartNs));
    }

    /** Exclude an intentional pause or transition; resume only after its warm-up. */
    public synchronized void pause(long nowNs) {
        if (!collecting || paused) return;
        Window window = windows.get(windows.size() - 1);
        if (nowNs < window.startNs || nowNs < snapshotNs) { invalidEventCount++; return; }
        window.endNs = nowNs;
        window.closed = true;
        paused = true;
    }

    public synchronized void resume(long nowNs) {
        if (!collecting || !paused) return;
        Window previous = windows.get(windows.size() - 1);
        if (nowNs < previous.endNs || nowNs < snapshotNs) { invalidEventCount++; return; }
        windows.add(new Window(nowNs));
        paused = false;
    }

    public synchronized void received(long bytes, long ptsUs, long nowNs) {
        received(bytes, ptsUs, nowNs, false);
    }

    /** Codec configuration contributes encoded bytes, but is not a video frame. */
    public synchronized void received(long bytes, long ptsUs, long nowNs, boolean codecConfig) {
        if (!accept(nowNs)) return;
        if (bytes < 0) { invalidEventCount++; return; }
        packets.add(new Packet(nowNs, bytes, ptsUs, codecConfig));
    }

    public synchronized void rendered(long actualRenderNs) {
        rendered(-1, actualRenderNs);
    }

    /**
     * PTS is an identity used to match the encoded frame's local arrival. The resulting
     * client-pipeline time includes playback buffering and decoding/output queues
     * up to the caller's event. With Java callback receipt it also includes callback
     * delivery. It excludes transport before arrival and does not measure display,
     * source-to-phone or optical latency. actualRenderNs is a legacy parameter name.
     */
    public synchronized void rendered(long ptsUs, long actualRenderNs) {
        if (!accept(actualRenderNs)) return;
        if (actualRenderNs < snapshotNs) lateRenderCallbacks++;
        renders.add(new Render(ptsUs, actualRenderNs));
    }

    public synchronized void discarded() { discarded(System.nanoTime()); }

    /** A stale decoder output explicitly discarded locally, not a lost TCP packet. */
    public synchronized void discarded(long nowNs) {
        if (accept(nowNs)) discards.add(nowNs);
    }

    public synchronized void rtt(double milliseconds) { rtt(milliseconds, System.nanoTime()); }

    /** HTTP/network round trip as measured by the caller, not end-to-end video latency. */
    public synchronized void rtt(double milliseconds, long nowNs) {
        if (!accept(nowNs)) return;
        if (Double.isNaN(milliseconds) || Double.isInfinite(milliseconds) || milliseconds < 0) {
            invalidEventCount++;
            return;
        }
        rtts.add(new Rtt(nowNs, milliseconds));
    }

    /**
     * Counter deltas since the previous snapshot, divided by actual active elapsed time.
     * A delayed render callback is counted once in the next snapshot; final render gap
     * and jitter statistics use the caller's supplied event timestamp. If the caller
     * supplies Java receipt time, these statistics reflect callback batching rather
     * than independent physical presentation and cannot themselves certify a freeze.
     */
    public synchronized Snapshot snapshot(long nowNs) {
        if (!collecting || nowNs <= snapshotNs) {
            if (collecting && nowNs < snapshotNs) invalidEventCount++;
            return new Snapshot(snapshotNs, nowNs, 0, 0, 0, 0, false,
                    collecting ? "non_positive_snapshot_duration" : "not_measuring");
        }
        Result total = compute(nowNs);
        double elapsedMs = activeDuration(snapshotNs, nowNs) / NS_PER_MS;
        Snapshot value = new Snapshot(snapshotNs, nowNs, elapsedMs,
                total.receivedFrames - previousReceived,
                total.renderedFrames - previousRendered,
                total.receivedBytes - previousBytes, elapsedMs > 0,
                elapsedMs > 0 ? "" : "no_active_snapshot_time");
        snapshotNs = nowNs;
        previousReceived = total.receivedFrames;
        previousRendered = total.renderedFrames;
        previousBytes = total.receivedBytes;
        return value;
    }

    /** Idempotent; late callbacks must be drained before this method is called. */
    public synchronized Result finish(long measurementEndNs) {
        if (finished != null) return finished;
        finished = compute(measurementEndNs);
        collecting = false;
        return finished;
    }

    private boolean accept(long timestampNs) {
        return collecting && windowIndex(timestampNs, Long.MAX_VALUE) >= 0;
    }

    private int windowIndex(long timestampNs, long endpointNs) {
        if (!started || timestampNs < startNs || timestampNs > endpointNs) return -1;
        for (int i = 0; i < windows.size(); i++) {
            Window window = windows.get(i);
            if (timestampNs >= window.startNs
                    && (!window.closed || timestampNs < window.endNs)) return i;
        }
        return -1;
    }

    private long activeDuration(long fromNs, long toNs) {
        if (!started || toNs <= fromNs) return 0;
        long elapsed = 0;
        for (Window window : windows) {
            long lower = Math.max(fromNs, window.startNs);
            long upper = Math.min(toNs, window.closed ? window.endNs : toNs);
            if (upper > lower) elapsed += upper - lower;
        }
        return elapsed;
    }

    private Result compute(long endpointNs) {
        long elapsedNs = activeDuration(startNs, endpointNs);
        long bytes = 0, discarded = 0;
        List<Frame> received = new ArrayList<>();
        List<Frame> rendered = new ArrayList<>();
        List<Double> rttValues = new ArrayList<>();
        List<Double> clientPipelineValues = new ArrayList<>();
        for (Packet packet : packets) {
            int window = windowIndex(packet.ns, endpointNs);
            if (window < 0) continue;
            bytes += packet.bytes;
            if (!packet.config) received.add(new Frame(packet.ns, packet.ptsUs, window));
        }
        for (Render render : renders) {
            int window = windowIndex(render.ns, endpointNs);
            if (window >= 0) rendered.add(new Frame(render.ns, render.ptsUs, window));
        }
        for (long timestamp : discards) if (windowIndex(timestamp, endpointNs) >= 0) discarded++;
        for (Rtt rtt : rtts) if (windowIndex(rtt.ns, endpointNs) >= 0) rttValues.add(rtt.ms);
        Comparator<Frame> byTime = (left, right) -> Long.compare(left.ns, right.ns);
        Collections.sort(received, byTime);
        Collections.sort(rendered, byTime);
        Collections.sort(rttValues);

        List<Map<Long, Long>> firstArrivalByWindow = new ArrayList<>();
        for (int i = 0; i < windows.size(); i++) firstArrivalByWindow.add(new HashMap<>());
        for (Frame frame : received) {
            if (frame.ptsUs < 0) continue;
            Map<Long, Long> firstArrival = firstArrivalByWindow.get(frame.window);
            if (!firstArrival.containsKey(frame.ptsUs)) firstArrival.put(frame.ptsUs, frame.ns);
        }
        for (Frame frame : rendered) {
            if (frame.ptsUs < 0) continue;
            Long arrivalNs = firstArrivalByWindow.get(frame.window).get(frame.ptsUs);
            if (arrivalNs != null && frame.ns > arrivalNs)
                clientPipelineValues.add((frame.ns - arrivalNs) / NS_PER_MS);
        }
        Collections.sort(clientPipelineValues);

        Intervals receiveIntervals = new Intervals();
        Intervals renderIntervals = new Intervals();
        Intervals sourceIntervals = new Intervals();
        Intervals arrivalResiduals = new Intervals();
        long sourceTimestampReversals = 0, sourceTimestampDuplicates = 0;
        for (int i = 1; i < received.size(); i++) {
            Frame previous = received.get(i - 1), current = received.get(i);
            if (previous.window != current.window) continue;
            double arrivalMs = (current.ns - previous.ns) / NS_PER_MS;
            receiveIntervals.add(arrivalMs);
            if (previous.ptsUs < 0 || current.ptsUs < 0) continue;
            if (current.ptsUs < previous.ptsUs) { sourceTimestampReversals++; continue; }
            if (current.ptsUs == previous.ptsUs) { sourceTimestampDuplicates++; continue; }
            double sourceMs = ((double) current.ptsUs - previous.ptsUs) / 1000.0;
            sourceIntervals.add(sourceMs);
            arrivalResiduals.add(arrivalMs - sourceMs);
        }
        for (int i = 1; i < rendered.size(); i++) {
            Frame previous = rendered.get(i - 1), current = rendered.get(i);
            if (previous.window == current.window)
                renderIntervals.add((current.ns - previous.ns) / NS_PER_MS);
        }

        double thresholdMs = Math.max(250.0, 5000.0 / Math.max(1, fps));
        GapStats receiveGaps = gaps(received, endpointNs, thresholdMs);
        GapStats renderGaps = gaps(rendered, endpointNs, thresholdMs);
        long lastReceiveNs = received.isEmpty() ? NO_TIMESTAMP : received.get(received.size() - 1).ns;
        long lastRenderNs = rendered.isEmpty() ? NO_TIMESTAMP : rendered.get(rendered.size() - 1).ns;
        String invalidReason = !started ? "not_started" : endpointNs < startNs
                ? "measurement_clock_reversed" : elapsedNs < MIN_VALID_ELAPSED_NS
                ? "measurement_too_short" : received.isEmpty() ? "no_video_frames_received"
                : rendered.isEmpty() ? "no_video_frames_rendered" : "";
        return new Result(startNs, endpointNs, elapsedNs, received.size(), rendered.size(), bytes,
                discarded, receiveGaps, renderGaps, thresholdMs, lastReceiveNs, lastRenderNs,
                lastReceiveNs == NO_TIMESTAMP ? Double.NaN
                        : activeDuration(lastReceiveNs, endpointNs) / NS_PER_MS,
                lastRenderNs == NO_TIMESTAMP ? Double.NaN
                        : activeDuration(lastRenderNs, endpointNs) / NS_PER_MS,
                receiveIntervals, renderIntervals, sourceIntervals, arrivalResiduals,
                rttValues, clientPipelineValues, invalidReason, invalidEventCount, lateRenderCallbacks,
                sourceTimestampReversals, sourceTimestampDuplicates, expectedAnimated);
    }

    private GapStats gaps(List<Frame> frames, long endpointNs, double thresholdMs) {
        GapStats stats = new GapStats();
        if (!expectedAnimated) return stats;
        int frameIndex = 0;
        for (int i = 0; i < windows.size(); i++) {
            Window window = windows.get(i);
            long end = Math.min(endpointNs, window.closed ? window.endNs : endpointNs);
            if (end <= window.startNs) continue;
            long previousNs = window.startNs;
            while (frameIndex < frames.size() && frames.get(frameIndex).window < i) frameIndex++;
            while (frameIndex < frames.size() && frames.get(frameIndex).window == i) {
                Frame frame = frames.get(frameIndex++);
                stats.add((frame.ns - previousNs) / NS_PER_MS, thresholdMs);
                previousNs = frame.ns;
            }
            // Includes the initial wait, an entirely empty stage, and the final freeze.
            stats.add((end - previousNs) / NS_PER_MS, thresholdMs);
        }
        return stats;
    }

    private static double percentile(List<Double> values, double fraction) {
        if (values.isEmpty()) return Double.NaN;
        return values.get(Math.max(0, (int) Math.ceil(fraction * values.size()) - 1));
    }

    private static double rate(long count, double elapsedMs, double multiplier) {
        return elapsedMs > 0 ? count * multiplier / elapsedMs : Double.NaN;
    }

    public static final class Snapshot {
        public final long fromNs, toNs, receivedFrames, renderedFrames, receivedBytes;
        public final double elapsedMs, receiveFps, renderFps, receiveMbps;
        public final boolean valid;
        public final String invalidReason;

        private Snapshot(long fromNs, long toNs, double elapsedMs, long receivedFrames,
                         long renderedFrames, long receivedBytes, boolean valid, String reason) {
            this.fromNs = fromNs;
            this.toNs = toNs;
            this.elapsedMs = elapsedMs;
            this.receivedFrames = receivedFrames;
            this.renderedFrames = renderedFrames;
            this.receivedBytes = receivedBytes;
            receiveFps = rate(receivedFrames, elapsedMs, 1000.0);
            renderFps = rate(renderedFrames, elapsedMs, 1000.0);
            receiveMbps = rate(receivedBytes, elapsedMs, 0.008);
            this.valid = valid;
            invalidReason = reason;
        }
    }

    /** Immutable scalar values; NaN means unavailable and should become JSON null. */
    public static final class Result {
        public final long startNs, endNs, receivedFrames, renderedFrames, receivedBytes,
                discardedFrames, receiveGapCount, renderGapCount, lastReceiveNs, lastRenderNs,
                receiveIntervalSamples, renderIntervalSamples, sourceIntervalSamples,
                arrivalResidualSamples, rttSampleCount, invalidEventCount, lateRenderCallbacks,
                sourceTimestampReversals, sourceTimestampDuplicates, clientPipelineSamples;
        public final double elapsedMs, wallElapsedMs, receiveFps, renderFps, receiveMbps,
                maxReceiveGapMs, maxRenderGapMs, gapThresholdMs, lastReceiveAgoMs,
                lastRenderAgoMs, receiveIntervalMeanMs, renderIntervalMeanMs,
                sourceIntervalMeanMs, receiveIntervalJitterMs, renderIntervalJitterMs,
                sourceIntervalJitterMs, arrivalResidualJitterMs, rttP50Ms, rttP95Ms,
                clientPipelineP50Ms, clientPipelineP95Ms;
        public final boolean valid, expectedAnimated;
        public final String invalidReason;

        private Result(long start, long end, long elapsedNs, long received, long rendered,
                       long bytes, long discarded, GapStats receiveGaps, GapStats renderGaps,
                       double threshold, long lastReceive, long lastRender, double receiveAge,
                       double renderAge, Intervals receiveIntervals, Intervals renderIntervals,
                       Intervals sourceIntervals, Intervals residuals, List<Double> rtts,
                       List<Double> clientPipelineValues,
                       String reason, long invalidEvents, long lateCallbacks, long sourceReversals,
                       long sourceDuplicates, boolean animated) {
            startNs = start;
            endNs = end;
            elapsedMs = elapsedNs / NS_PER_MS;
            wallElapsedMs = !"not_started".equals(reason) && end >= start
                    ? (end - start) / NS_PER_MS : Double.NaN;
            receivedFrames = received;
            renderedFrames = rendered;
            receivedBytes = bytes;
            discardedFrames = discarded;
            receiveFps = rate(received, elapsedMs, 1000.0);
            renderFps = rate(rendered, elapsedMs, 1000.0);
            receiveMbps = rate(bytes, elapsedMs, 0.008);
            receiveGapCount = receiveGaps.count;
            renderGapCount = renderGaps.count;
            maxReceiveGapMs = receiveGaps.maxMs;
            maxRenderGapMs = renderGaps.maxMs;
            gapThresholdMs = threshold;
            lastReceiveNs = lastReceive;
            lastRenderNs = lastRender;
            lastReceiveAgoMs = receiveAge;
            lastRenderAgoMs = renderAge;
            receiveIntervalSamples = receiveIntervals.count;
            renderIntervalSamples = renderIntervals.count;
            sourceIntervalSamples = sourceIntervals.count;
            arrivalResidualSamples = residuals.count;
            receiveIntervalMeanMs = receiveIntervals.mean();
            renderIntervalMeanMs = renderIntervals.mean();
            sourceIntervalMeanMs = sourceIntervals.mean();
            receiveIntervalJitterMs = receiveIntervals.jitter();
            renderIntervalJitterMs = renderIntervals.jitter();
            sourceIntervalJitterMs = sourceIntervals.jitter();
            arrivalResidualJitterMs = residuals.jitter();
            rttSampleCount = rtts.size();
            rttP50Ms = percentile(rtts, 0.50);
            rttP95Ms = percentile(rtts, 0.95);
            clientPipelineSamples = clientPipelineValues.size();
            clientPipelineP50Ms = percentile(clientPipelineValues, 0.50);
            clientPipelineP95Ms = percentile(clientPipelineValues, 0.95);
            valid = reason.isEmpty();
            invalidReason = reason;
            invalidEventCount = invalidEvents;
            lateRenderCallbacks = lateCallbacks;
            sourceTimestampReversals = sourceReversals;
            sourceTimestampDuplicates = sourceDuplicates;
            expectedAnimated = animated;
        }
    }

    private static final class Window {
        final long startNs;
        long endNs;
        boolean closed;
        Window(long startNs) { this.startNs = startNs; }
    }
    private static final class Packet {
        final long ns, bytes, ptsUs;
        final boolean config;
        Packet(long ns, long bytes, long ptsUs, boolean config) {
            this.ns = ns; this.bytes = bytes; this.ptsUs = ptsUs; this.config = config;
        }
    }
    private static final class Frame {
        final long ns, ptsUs;
        final int window;
        Frame(long ns, long ptsUs, int window) {
            this.ns = ns; this.ptsUs = ptsUs; this.window = window;
        }
    }
    private static final class Rtt {
        final long ns;
        final double ms;
        Rtt(long ns, double ms) { this.ns = ns; this.ms = ms; }
    }
    private static final class Render {
        final long ptsUs, ns;
        Render(long ptsUs, long ns) { this.ptsUs = ptsUs; this.ns = ns; }
    }
    private static final class GapStats {
        long count;
        double maxMs = Double.NaN;
        void add(double milliseconds, double thresholdMs) {
            maxMs = Double.isNaN(maxMs) ? milliseconds : Math.max(maxMs, milliseconds);
            if (milliseconds > thresholdMs) count++;
        }
    }
    /** Welford's algorithm; population deviation of intervals, with sample count exposed. */
    private static final class Intervals {
        long count;
        double average, sumSquares;
        void add(double value) {
            count++;
            double difference = value - average;
            average += difference / count;
            sumSquares += difference * (value - average);
        }
        double mean() { return count == 0 ? Double.NaN : average; }
        double jitter() { return count == 0 ? Double.NaN : Math.sqrt(Math.max(0, sumSquares / count)); }
    }
}
