package local.remoteandroid.direct;

import java.io.File;

/** JNI component loaded from the instrumentation APK, never the production app's library directory. */
public final class NativeUdpFec {
    private static boolean loaded;
    private NativeUdpFec() { }

    static synchronized void load(String instrumentationNativeDirectory) {
        if(!loaded){
            System.load(new File(instrumentationNativeDirectory,"libhuoguo_udp_fec.so").getAbsolutePath());
            loaded=true;
        }
    }
    public static native long nativeCreate();
    public static native byte[][] nativeAccept(long handle,byte[] authenticatedPacket,long arrivalUs);
    public static native byte[][] nativeExpire(long handle,long nowUs);
    public static native long[] nativeStats(long handle);
    public static native void nativeSetDiagnostics(long handle,boolean enabled);
    public static native long[][] nativeDrainEvents(long handle);
    public static native long[] nativeEventStats(long handle);
    public static native void nativeDestroy(long handle);

    static final String[] STAT_NAMES={"packets","wire_bytes","invalid","duplicate","settled_packets",
        "expired_packets","frames_expired","frames_delivered","recovered_shards","reference_lost",
        "keyframe_requests","dependency_dropped","memory_rejected","clock_mapping_rejected",
        "clock_mappings_active","clock_mapping_evictions","clock_mapping_expired","logical_body_rejected",
        "completed_bodies","needs_keyframe","max_assembly_latency_us"};
    static final String[] EVENT_NAMES={"type","frame_id","host_capture_us","reference_id","flags",
        "first_arrival_us","last_arrival_us","fec_quorum_ready_us","event_phone_us","deadline_phone_us",
        "logical_bytes","reason_code","event_sequence","pts_us"};
    static final String[] EVENT_TYPES={"invalid","fec_quorum_ready","frame_delivered","frame_expired",
        "settled_by_delivered_frame","logical_rejected"};
    static final String[] EVENT_REASONS={"none","keyframe","predictive","assembly_deadline",
        "superseded_by_delivered_frame","malformed_logical_body","logical_chain_blocked"};
}
