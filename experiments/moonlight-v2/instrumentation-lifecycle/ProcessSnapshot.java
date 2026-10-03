package local.huoguo.instrumentationlifecyclefixture;

import android.os.Process;
import android.os.SystemClock;

/** Fixture state only. No disk, account, network, UI or target-App data. */
public final class ProcessSnapshot {
    // Static initialization occurs once in the target application's classloader.
    // The monotonic clock seed is an ephemeral process identity, not a secret.
    private static final long NONCE_NS=SystemClock.elapsedRealtimeNanos();
    private ProcessSnapshot(){}
    public static String snapshot(){
        return "{\"schema_version\":1,\"pid\":"+Process.myPid()+",\"nonce_ns\":"+NONCE_NS
            +",\"snapshot_ns\":"+SystemClock.elapsedRealtimeNanos()+"}";
    }
}
