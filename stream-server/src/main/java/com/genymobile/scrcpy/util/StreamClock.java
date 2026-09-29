package com.genymobile.scrcpy.util;

/** One local clock sample for mapping scrcpy audio to host framebuffer time. */
public final class StreamClock {
    private StreamClock() { }

    private static void sample() {
        long before = System.nanoTime();
        long unixMillis = System.currentTimeMillis();
        long after = System.nanoTime();
        long monotonicUs = (before + (after - before) / 2) / 1000;
        System.out.println("{\"unix_us\":" + unixMillis * 1000
                + ",\"monotonic_us\":" + monotonicUs
                + ",\"sample_span_us\":" + (after - before) / 1000 + "}");
        System.out.flush();
    }

    public static void main(String[] args) throws java.io.IOException {
        if (args.length != 0 && "interactive".equals(args[0])) {
            int request;
            while ((request = System.in.read()) != -1) {
                if (request == 'p') sample();
            }
        } else {
            sample();
        }
    }
}
