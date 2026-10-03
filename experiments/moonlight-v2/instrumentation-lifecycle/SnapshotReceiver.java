package local.huoguo.instrumentationlifecyclefixture;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Explicit broadcast only; returns process-local numeric state as resultData. */
public final class SnapshotReceiver extends BroadcastReceiver {
    public static final String ACTION="local.huoguo.instrumentationlifecyclefixture.SNAPSHOT";
    @Override public void onReceive(Context context,Intent intent){
        if(intent==null||!ACTION.equals(intent.getAction())){
            setResultCode(0);setResultData("{\"schema_version\":1,\"error_code\":1}");return;
        }
        setResultCode(-1);setResultData(ProcessSnapshot.snapshot());
    }
}
