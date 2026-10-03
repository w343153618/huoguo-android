package local.huoguo.instrumentationlifecyclefixture;

import android.app.Instrumentation;
import android.os.Bundle;

/** Runs headlessly and instruments this fixture package only. */
public final class LifecycleInstrumentation extends Instrumentation {
    private static final String PACKAGE="local.huoguo.instrumentationlifecyclefixture";
    @Override public void onCreate(Bundle arguments){super.onCreate(arguments);start();}
    @Override public void onStart(){
        Bundle result=new Bundle();int code=0;
        try{
            if(!PACKAGE.equals(getTargetContext().getPackageName()))throw new IllegalStateException("fixture_target_required");
            // Instrumentation may have its own classloader. Always ask the
            // target loader for the static state sampled by its receiver.
            Class<?> state=getTargetContext().getClassLoader().loadClass(PACKAGE+".ProcessSnapshot");
            String snapshot=(String)state.getMethod("snapshot").invoke(null);
            if(snapshot==null||snapshot.length()>256)throw new IllegalStateException("fixture_snapshot_bound");
            result.putString("fixture_snapshot",snapshot);code=-1;
        }catch(Exception failure){result.putInt("fixture_error_code",1);}
        finish(code,result);
    }
}
