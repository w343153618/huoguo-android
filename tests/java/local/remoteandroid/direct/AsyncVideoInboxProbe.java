package local.remoteandroid.direct;

/** Offline state checks against the actual probe inbox, without codecs or a phone. */
public final class AsyncVideoInboxProbe {
    private static int checks;

    private static void check(boolean value, String name) {
        if (!value) throw new AssertionError(name);
        checks++;
    }

    private static UdpVideoProbe.VideoFrame frame(long pts, boolean idr, boolean config, int size) {
        // Synthetic complete-body placeholders test queue/dependency state only.
        // These bytes are not fed to a H.264 parser or decoder.
        return new UdpVideoProbe.VideoFrame(new byte[size], 540, 960,
                config ? 8 : 0, size - 20, pts, 1, idr, 0);
    }

    public static void main(String[] args) throws Exception {
        UdpVideoProbe.VideoInbox queue = new UdpVideoProbe.VideoInbox();
        check(!queue.offer(frame(0, false, false, 32)), "startup P blocked");
        check(!queue.offer(frame(0, true, false, 32)), "configless IDR blocked");
        check(queue.offer(frame(1, true, true, 32)), "complete IDR admitted");
        check(queue.offer(frame(2, false, false, 32))
                && queue.offer(frame(3, false, false, 32))
                && queue.offer(frame(4, false, false, 32)), "P chain staged behind queued IDR");
        UdpVideoProbe.VideoFrame idr = queue.take();
        check(idr.ptsUs == 1 && queue.valid(idr), "FIFO IDR valid");
        queue.success(idr);
        check(!queue.needsIdr(), "successful IDR anchors chain");
        UdpVideoProbe.VideoFrame inflight = queue.take();
        check(inflight.ptsUs == 2 && queue.valid(inflight), "FIFO P valid after IDR");
        check(queue.offer(frame(5, false, false, 32))
                && queue.offer(frame(6, false, false, 32)), "fills 4 pending frames");
        check(!queue.offer(frame(7, false, false, 32)), "overflow rejects reference AU");
        check(queue.queue.isEmpty() && queue.needsIdr() && queue.cleared == 4,
                "overflow clears dependent chain");
        check(!queue.valid(inflight), "inflight old epoch invalidated");
        check(!queue.offer(frame(8, false, false, 32)), "P after lost reference blocked");
        check(queue.offer(frame(9, true, true, 32)), "fresh recovery IDR admitted");
        queue.fail(inflight.epoch);
        check(queue.queue.size() == 1, "stale codec timeout cannot clear new IDR epoch");
        UdpVideoProbe.VideoFrame recovery = queue.take();
        queue.success(recovery);
        check(!queue.needsIdr() && queue.recoveryCompleted == 1,
                "complete new IDR restores chain");

        UdpVideoProbe.VideoInbox bytes = new UdpVideoProbe.VideoInbox();
        check(bytes.offer(frame(1, true, true, 1024 * 1024))
                && bytes.offer(frame(2, false, false, 1024 * 1024)),
                "byte budget exact bound accepted");
        check(!bytes.offer(frame(3, false, false, 32))
                && bytes.maxBytes == 2 * 1024 * 1024 && bytes.queue.isEmpty(),
                "byte overflow bounded and chain blocked");
        check(!bytes.offer(frame(4, true, true, 2 * 1024 * 1024 + 1))
                && bytes.oversizeDrops == 1, "oversized whole AU rejected");

        UdpVideoProbe.VideoInbox next = new UdpVideoProbe.VideoInbox();
        next.offer(frame(1, true, true, 32));
        for (int index = 2; index <= 4; index++) next.offer(frame(index, false, false, 32));
        check(next.offer(frame(5, true, true, 32)) && next.queue.size() == 1,
                "new complete IDR may replace overflowed chain");
        UdpVideoProbe.VideoFrame candidate = next.take();
        next.fail(candidate.epoch);
        check(next.needsIdr() && !next.offer(frame(6, false, false, 32)),
                "codec timeout blocks future P");
        next.offer(frame(7, true, true, 32));
        next.close();
        check(!next.offer(frame(8, false, false, 32)) && next.closingDrops == 1,
                "close stops new admissions");
        check(next.take().ptsUs == 7 && next.take() == null, "close drains owned FIFO and ends");
        System.out.println("PASS " + checks + " bounded FIFO/reference recovery checks (offline, no codecs or phone)");
    }
}
