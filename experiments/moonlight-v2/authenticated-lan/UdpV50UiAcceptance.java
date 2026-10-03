package local.remoteandroid.direct;

import android.app.*;
import android.content.*;
import android.os.Bundle;
import android.widget.*;
import android.view.View;
import android.view.ViewGroup;
import java.lang.reflect.Field;
import java.util.Map;

/** Actual beta UI/prefs fixture, no credential reads, authentication or media. */
public final class UdpV50UiAcceptance extends Instrumentation {
    public void onCreate(Bundle args){super.onCreate(args);start();}
    private static Object field(Object target,String name)throws Exception{
        Field f=target.getClass().getDeclaredField(name);f.setAccessible(true);return f.get(target);
    }
    private static Button button(View root,String label){
        if(root instanceof Button&&((Button)root).getText().toString().equals(label))return (Button)root;
        if(root instanceof ViewGroup)for(int i=0;i<((ViewGroup)root).getChildCount();i++){
            Button found=button(((ViewGroup)root).getChildAt(i),label);if(found!=null)return found;
        }return null;
    }
    public void onStart(){
        Bundle report=new Bundle();SharedPreferences prefs=null;Map<String,?> original=null;MainActivity app=null;
        try{
            prefs=getTargetContext().getSharedPreferences("authenticated_udp_candidate",0);original=prefs.getAll();
            app=(MainActivity)startActivitySync(new Intent().setClassName(getTargetContext().getPackageName(),"local.remoteandroid.direct.MainActivity").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            waitForIdleSync();MainActivity first=app;Throwable[] error={null};
            runOnMainSync(()->{try{
                if(first.lanUdpEntry.active())throw new IllegalStateException("active_media");
                Button optimize=button(first.getWindow().getDecorView(),"真我 V50 · 一键均衡优化");
                if(optimize==null||!optimize.performClick())throw new IllegalStateException("button_missing");
            }catch(Throwable failure){error[0]=failure;}});waitForIdleSync();if(error[0]!=null)throw new IllegalStateException(error[0]);
            Object ui=app.lanUdpEntry;
            if(((Spinner)field(ui,"quality")).getSelectedItemPosition()!=0||((Spinner)field(ui,"rate")).getSelectedItemPosition()!=0
                ||((Spinner)field(ui,"fps")).getSelectedItemPosition()!=0||((Spinner)field(ui,"buffer")).getSelectedItemPosition()!=2
                ||(Boolean)field(ui,"ownerStageDiagnosticsEnabled"))throw new IllegalStateException("preset_readback");
            report.putBoolean("actual_V50_button_preset_readback",true);
            runOnMainSync(()->{try{((Spinner)field(first.lanUdpEntry,"fps")).setSelection(1);}catch(Exception failure){error[0]=failure;}});
            waitForIdleSync();if(error[0]!=null)throw new IllegalStateException(error[0]);runOnMainSync(first::finish);waitForIdleSync();
            app=(MainActivity)startActivitySync(new Intent().setClassName(getTargetContext().getPackageName(),"local.remoteandroid.direct.MainActivity").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));waitForIdleSync();
            ui=app.lanUdpEntry;
            if(((Spinner)field(ui,"fps")).getSelectedItemPosition()!=1||((Spinner)field(ui,"quality")).getSelectedItemPosition()!=0
                ||((Boolean)field(ui,"ownerStageDiagnosticsEnabled")))throw new IllegalStateException("manual_choice_reopen");
            report.putBoolean("manual_60fps_preserved_after_reopen",true);
            report.putString("advertised_codec_capabilities",UdpDeviceCapabilities.summary());
            report.putBoolean("V50_physical_device_tested",false);report.putBoolean("media_started",false);report.putBoolean("credentials_read",false);
        }catch(Throwable failure){report.putString("failure_class",failure.getClass().getSimpleName());}
        finally{
            if(prefs!=null&&original!=null){SharedPreferences.Editor edit=prefs.edit().clear();
                for(Map.Entry<String,?> entry:original.entrySet()){
                    Object value=entry.getValue();String key=entry.getKey();
                    if(value instanceof String)edit.putString(key,(String)value);else if(value instanceof Integer)edit.putInt(key,(Integer)value);
                    else if(value instanceof Boolean)edit.putBoolean(key,(Boolean)value);else if(value instanceof Long)edit.putLong(key,(Long)value);
                    else if(value instanceof Float)edit.putFloat(key,(Float)value);
                }report.putBoolean("prior_connection_preferences_restored",edit.commit());
            }
            if(app!=null){MainActivity last=app;runOnMainSync(()->{if(!last.lanUdpEntry.active())last.lanUdpEntry.showLogin();});}
        }
        finish(report.containsKey("failure_class")?Activity.RESULT_CANCELED:Activity.RESULT_OK,report);
    }
}
