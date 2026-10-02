package local.remoteandroid.direct;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Intent;
import android.os.Bundle;
import android.os.SystemClock;
import android.view.MotionEvent;
import android.widget.Button;
import android.widget.EditText;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.lang.reflect.Field;
import java.nio.charset.StandardCharsets;

/** Test-only normal UI login, ART diagnostics microbench and OS touch injection.
 * Reads one owner-only existing-account input, removes it before HTTPS login.
 * Never provisions a media key or exports any credential. */
public final class LanUiAcceptance extends Instrumentation {
    public void onCreate(Bundle arguments){super.onCreate(arguments);start();}
    private static volatile long sink;
    private static Object field(Object target,String name)throws Exception{
        Field field=target.getClass().getDeclaredField(name);field.setAccessible(true);return field.get(target);
    }
    private void waitReport(File report)throws Exception{
        long deadline=SystemClock.elapsedRealtime()+20000;
        while((report.length()<1||field(field(getCurrentActivity(),"lanUdpEntry"),"retiring")!=null)&&SystemClock.elapsedRealtime()<deadline)Thread.sleep(100);
        if(report.length()<1||report.length()>65536||field(field(getCurrentActivity(),"lanUdpEntry"),"retiring")!=null)throw new IllegalStateException("report_unavailable_after_leave");
    }
    private MainActivity testActivity;
    private MainActivity getCurrentActivity(){return testActivity;}
    private static long bench(boolean enabled,int count){
        UdpAudioReceiver.AudioDiagnostics d=new UdpAudioReceiver.AudioDiagnostics();
        long start=System.nanoTime();
        for(int i=0;i<count;i++){
            long t=(i%100)*100000L;
            if(enabled){d.workerStart(t,t/2);d.input(t,21333000);d.output(t);d.target(t);
                d.tail(t,t/3,false);d.slept(1000000,1000000+t);d.wrote(t);}
            else sink=t+t/2+21333000+t+t+t+t/3+1000000+1000000+t+t;
        }
        sink+=d.firstArrivalAge.values()[0];return System.nanoTime()-start;
    }
    private void pointers(MainActivity a,int count,boolean osInjection,boolean cancel)throws Exception{
        final int[] location=new int[2],size=new int[2];
        runOnMainSync(()->{a.screen.getLocationOnScreen(location);size[0]=a.screen.getWidth();size[1]=a.screen.getHeight();});
        long down=SystemClock.uptimeMillis();
        for(int n=1;n<=count;n++)event(a,location,size,n,n==1?MotionEvent.ACTION_DOWN:MotionEvent.ACTION_POINTER_DOWN|((n-1)<<8),down,false,osInjection);
        event(a,location,size,count,MotionEvent.ACTION_MOVE,down,true,osInjection);
        if(!cancel)return;
        event(a,location,size,count,MotionEvent.ACTION_CANCEL,down,true,osInjection);
        // New gesture proves the old contact IDs can be reused after true CANCEL.
        long freshDown=SystemClock.uptimeMillis();
        event(a,location,size,1,MotionEvent.ACTION_DOWN,freshDown,false,osInjection);
        event(a,location,size,1,MotionEvent.ACTION_UP,freshDown,false,osInjection);
    }
    private void event(MainActivity a,int[] location,int[] size,int count,int action,long down,boolean moved,boolean osInjection)throws Exception{
        MotionEvent.PointerProperties[] properties=new MotionEvent.PointerProperties[count];
        MotionEvent.PointerCoords[] coords=new MotionEvent.PointerCoords[count];
        for(int i=0;i<count;i++){
            properties[i]=new MotionEvent.PointerProperties();properties[i].id=i;properties[i].toolType=MotionEvent.TOOL_TYPE_FINGER;
            coords[i]=new MotionEvent.PointerCoords();coords[i].pressure=1;coords[i].size=1;
            coords[i].x=(osInjection?location[0]:0)+size[0]*(.05f+.09f*i)+(moved?2:0);
            coords[i].y=(osInjection?location[1]:0)+size[1]*(.10f+.07f*i)+(moved?2:0);
        }
        MotionEvent event=MotionEvent.obtain(down,SystemClock.uptimeMillis(),action,count,properties,coords,
            0,0,1,1,0,0,android.view.InputDevice.SOURCE_TOUCHSCREEN,0);
        try{if(osInjection){if(!getUiAutomation().injectInputEvent(event,true))throw new IllegalStateException("OS_touch_injection_rejected");}
            else runOnMainSync(()->a.screen.dispatchTouchEvent(event));}
        finally{event.recycle();}Thread.sleep(45);
    }
    public void onStart(){
        JSONObject report=new JSONObject();Bundle result=new Bundle();MainActivity a=null;
        File credential=new File(getTargetContext().getFilesDir(),"udp-test-login.json");
        try{
            bench(true,10000);bench(false,10000);JSONArray rows=new JSONArray();
            for(boolean enabled:new boolean[]{false,true,true,false})rows.put(new JSONObject()
                .put("diagnostics_enabled",enabled).put("iterations",100000).put("elapsed_ns",bench(enabled,100000)));
            report.put("audio_diagnostics_ART_microbench",rows).put("microbench_scope","Numeric histogram operations only; not real codec, concurrent snapshot or PCM scheduling overhead");
            if(credential.length()<1||credential.length()>4096)throw new IllegalStateException("private_login_input_bound");
            byte[] bytes=new byte[(int)credential.length()];
            try(FileInputStream in=new FileInputStream(credential)){if(in.read(bytes)!=bytes.length)throw new IllegalStateException("input_read");}
            JSONObject login=new JSONObject(new String(bytes,StandardCharsets.UTF_8));java.util.Arrays.fill(bytes,(byte)0);
            if(!credential.delete())throw new IllegalStateException("input_cleanup");
            a=(MainActivity)startActivitySync(new Intent().setClassName(getTargetContext().getPackageName(),"local.remoteandroid.direct.MainActivity").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            waitForIdleSync();MainActivity target=a;testActivity=a;
            final Throwable[] problem={null};int oldGeneration=target.generation;
            runOnMainSync(()->{try{
                Object ui=target.lanUdpEntry;
                ((EditText)field(ui,"address")).setText("192.168.9.128:15560");
                ((EditText)field(ui,"user")).setText(login.getString("username"));
                ((EditText)field(ui,"password")).setText(login.getString("password"));
                android.view.ViewGroup decor=(android.view.ViewGroup)target.getWindow().getDecorView();
                if(!clickStart(decor))throw new IllegalStateException("normal_UI_start_button_missing");
            }catch(Throwable e){problem[0]=e;}});
            final String username=login.getString("username"),password=login.getString("password");
            login.remove("password");login.remove("username");
            if(problem[0]!=null)throw new IllegalStateException("normal_UI_start",problem[0]);
            long deadline=SystemClock.elapsedRealtime()+25000;
            while((target.generation<=oldGeneration||target.receivedFrames.get()<15||target.presentedFrames.get()<10)&&SystemClock.elapsedRealtime()<deadline)Thread.sleep(100);
            if(target.generation<=oldGeneration||target.receivedFrames.get()<15||target.presentedFrames.get()<10)throw new IllegalStateException("no_authenticated_media");
            report.put("normal_UI_login_received_media",true);Thread.sleep(25000);
            report.put("before_touch_received_frames",target.receivedFrames.get()).put("before_touch_callback_count",target.presentedFrames.get());
            File phase=new File(getTargetContext().getFilesDir(),"udp-ui-phase-ready-touch");
            try(FileOutputStream out=new FileOutputStream(phase)){out.write(1);}
            File ready=new File(getTargetContext().getFilesDir(),"udp-ui-phase-touch-ready");deadline=SystemClock.elapsedRealtime()+15000;
            while(!ready.exists()&&SystemClock.elapsedRealtime()<deadline)Thread.sleep(100);
            if(!ready.exists())throw new IllegalStateException("touch_receipt_source_not_ready");
            ready.delete();phase.delete();
            // OS delivery is a separate layer. The phone vendor's intercepting
            // gesture panel has made repeated OS injection fail; do not silently
            // count direct View dispatch as OS or physical finger acceptance.
            pointers(target,2,false,true);Thread.sleep(400);
            pointers(target,10,false,true);Thread.sleep(2000);
            report.put("OS_injected_two_contacts_cancel_and_fresh_down",false)
                .put("OS_injection_attempted_this_round",false)
                .put("App_dispatched_two_and_ten_native_MotionEvent_contacts_cancel_and_fresh_down",true)
                .put("ten_contacts_delivered_through_phone_OS",false);
            runOnMainSync(()->target.handleBack());
            File first=new File(getTargetContext().getFilesDir(),"udp-app-last-report.json");waitReport(first);
            report.put("left_through_App_back",true).put("activity_running_after_leave",target.running)
                .put("actual_optical_latency_measured",false).put("actual_acoustic_sync_measured",false);
            try(FileInputStream in=new FileInputStream(first);FileOutputStream out=new FileOutputStream(new File(getTargetContext().getFilesDir(),"udp-app-first-report.json"))){
                byte[] chunk=new byte[1024];int n;while((n=in.read(chunk))!=-1)out.write(chunk,0,n);
            }
            if(!first.delete())throw new IllegalStateException("first_report_checkpoint_cleanup");
            // New HTTPS account auth, fresh session key/receiver, normal start UI.
            oldGeneration=target.generation;
            runOnMainSync(()->{try{
                Object ui=target.lanUdpEntry;
                ((EditText)field(ui,"address")).setText("192.168.9.128:15560");
                ((EditText)field(ui,"user")).setText(username);((EditText)field(ui,"password")).setText(password);
                if(!clickStart(target.getWindow().getDecorView()))throw new IllegalStateException("reconnect_UI_button_missing");
            }catch(Throwable e){problem[0]=e;}});
            if(problem[0]!=null)throw new IllegalStateException("normal_UI_reconnect",problem[0]);
            deadline=SystemClock.elapsedRealtime()+25000;
            while((target.generation<=oldGeneration||target.receivedFrames.get()<15||target.presentedFrames.get()<10)&&SystemClock.elapsedRealtime()<deadline)Thread.sleep(100);
            if(target.generation<=oldGeneration||target.receivedFrames.get()<15||target.presentedFrames.get()<10)throw new IllegalStateException("reconnect_no_authenticated_media");
            Thread.sleep(2500);report.put("normal_UI_reconnected_received_media",true);
            pointers(target,2,false,false);Thread.sleep(400);
            runOnMainSync(()->target.handleBack());waitReport(first);
            report.put("disconnect_with_two_contacts_still_down",true).put("running_after_second_leave",target.running);
        }catch(Throwable failure){try{report.put("failure_class",failure.getClass().getSimpleName());if(failure.getMessage()!=null&&failure.getMessage().matches("[a-zA-Z_]+"))report.put("bounded_failure_label",failure.getMessage());if(failure.getCause()!=null)report.put("failure_cause_class",failure.getCause().getClass().getSimpleName());}catch(Exception ignored){}}
        finally{credential.delete();if(a!=null){MainActivity target=a;runOnMainSync(()->{if(target.lanUdpEntry!=null)target.lanUdpEntry.cancel(true);});}}
        result.putString("numeric_result",report.toString());finish(report.has("failure_class")?Activity.RESULT_CANCELED:Activity.RESULT_OK,result);
    }
    private static boolean clickStart(android.view.View view){
        if(view instanceof Button&&((Button)view).getText().toString().equals("启动认证 UDP 测试")){view.performClick();return true;}
        if(view instanceof android.view.ViewGroup){android.view.ViewGroup group=(android.view.ViewGroup)view;for(int i=0;i<group.getChildCount();i++)if(clickStart(group.getChildAt(i)))return true;}
        return false;
    }
}
