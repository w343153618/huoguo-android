package com.genymobile.scrcpy.control;

/** Explicit candidate guest contract; stock scrcpy does not implement this capability.
 * Launch this class from the same verified JAR that will host the candidate control
 * service. This readback advertises the compiled implementation, not successful
 * Android input injection or a physical touch latency measurement.
 */
public final class TouchCapabilities {
    public static final String CANCEL = "touch_cancel_clears_pointers_v1";

    private TouchCapabilities() {
    }

    public static void main(String[] args) {
        if (args.length != 0) {
            throw new IllegalArgumentException("No capability-query arguments are supported");
        }
        System.out.println(CANCEL);
    }
}
