package local.remoteandroid.direct;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Intent;
import android.os.Bundle;
import android.os.SystemClock;
import android.view.MotionEvent;
import android.view.Window;
import android.widget.Spinner;
import android.widget.Button;
import android.widget.EditText;
import android.widget.CheckBox;
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
    private Bundle arguments;
    private final JSONArray injected=new JSONArray(),delivered=new JSONArray();
    private int osCleanupFailures;private boolean osCleanup;
    public void onCreate(Bundle arguments){this.arguments=arguments;super.onCreate(arguments);start();}
    private int rateIndex(){int value=Integer.parseInt(arguments.getString("rate_index","2"));if(value<0||value>4)throw new IllegalArgumentException("rate_index_bound");return value;}
    private boolean mediaOnly(){return arguments.getString("media_only","false").equals("true");}
    private void prepareUi(MainActivity target,String username,String password)throws Exception{
        Object ui=target.lanUdpEntry;String scope=arguments.getString("network_scope","lan");
        if(!scope.equals("lan")&&!scope.equals("tailnet"))throw new IllegalArgumentException("scope_bound");
        ((Spinner)field(ui,"scope")).setSelection(scope.equals("tailnet")?1:0);
        ((EditText)field(ui,"address")).setText(scope.equals("tailnet")?"100.65.0.2:15560":"192.168.9.128:15560");
        ((EditText)field(ui,"user")).setText(username);((EditText)field(ui,"password")).setText(password);
        ((Spinner)field(ui,"rate")).setSelection(rateIndex());
        ((Spinner)field(ui,"quality")).setSelection(2);((Spinner)field(ui,"fps")).setSelection(0);
        ((Spinner)field(ui,"buffer")).setSelection(2);((CheckBox)field(ui,"sound")).setChecked(true);
        String pcm=arguments.getString("pcm_queue","off");if(!pcm.equals("on")&&!pcm.equals("off"))throw new IllegalArgumentException("pcm_choice_bound");
        ((CheckBox)field(ui,"pcmQueue")).setChecked(pcm.equals("on"));
    }
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
        int lastCount=0,attemptedCount=0;boolean ended=false;long activeDown=down;
        try{
            for(int n=1;n<=count;n++){
                attemptedCount=n;
                event(a,location,size,n,n==1?MotionEvent.ACTION_DOWN:MotionEvent.ACTION_POINTER_DOWN|((n-1)<<8),down,false,osInjection);
                lastCount=n;
            }
            event(a,location,size,count,MotionEvent.ACTION_MOVE,down,true,osInjection);
            if(!cancel){ended=true;return;}
            event(a,location,size,count,MotionEvent.ACTION_CANCEL,down,true,osInjection);
            // New gesture proves contact IDs can be reused after true CANCEL.
            activeDown=SystemClock.uptimeMillis();lastCount=0;attemptedCount=1;
            event(a,location,size,1,MotionEvent.ACTION_DOWN,activeDown,false,osInjection);lastCount=1;
            event(a,location,size,1,MotionEvent.ACTION_UP,activeDown,false,osInjection);ended=true;
        }finally{
            // UDP session cancellation cannot clear a phone InputDispatcher
            // gesture. Always try local OS CANCEL after an injection failure.
            if(osInjection&&!ended&&attemptedCount>0){osCleanup=true;
                try{event(a,location,size,Math.max(1,lastCount),MotionEvent.ACTION_CANCEL,activeDown,true,true);}
                catch(Exception failure){osCleanupFailures++;}finally{osCleanup=false;}}
        }
    }
    private void event(MainActivity a,int[] location,int[] size,int count,int action,long down,boolean moved,boolean osInjection)throws Exception{
        MotionEvent.PointerProperties[] properties=new MotionEvent.PointerProperties[count];
        MotionEvent.PointerCoords[] coords=new MotionEvent.PointerCoords[count];
        for(int i=0;i<count;i++){
            properties[i]=new MotionEvent.PointerProperties();properties[i].id=i;properties[i].toolType=MotionEvent.TOOL_TYPE_FINGER;
            coords[i]=new MotionEvent.PointerCoords();coords[i].pressure=1;coords[i].size=1;
            coords[i].x=(osInjection?location[0]:0)+size[0]*(osInjection?.4f+.1f*i:.05f+.09f*i)+(moved?2:0);
            coords[i].y=(osInjection?location[1]:0)+size[1]*(osInjection?.4f+.05f*i:.10f+.07f*i)+(moved?2:0);
        }
        MotionEvent event=MotionEvent.obtain(down,SystemClock.uptimeMillis(),action,count,properties,coords,
            0,0,1,1,-1,0,android.view.InputDevice.SOURCE_TOUCHSCREEN,0);
        try{if(osInjection){long start=SystemClock.elapsedRealtimeNanos();boolean ok=getUiAutomation().injectInputEvent(event,true);
                injected.put(new JSONObject().put("action",event.getActionMasked()).put("count",count).put("accepted",ok)
                    .put("actual_event_flags",event.getFlags()).put("activity_display_id",a.getDisplay().getDisplayId())
                    .put("event_time_ms",event.getEventTime()).put("down_time_ms",event.getDownTime()).put("cleanup",osCleanup)
                    .put("duration_ns",SystemClock.elapsedRealtimeNanos()-start).put("activity_has_focus",a.hasWindowFocus()));
                if(!ok)throw new IllegalStateException("OS_touch_injection_rejected");}
            else runOnMainSync(()->a.screen.dispatchTouchEvent(event));}
        finally{event.recycle();}Thread.sleep(45);
    }
    private void cornerTaps(MainActivity a)throws Exception{
        for(float[] corner:new float[][]{{.01f,.01f},{.99f,.01f},{.01f,.99f},{.99f,.99f}}){
            final float[] point=new float[2];runOnMainSync(()->{
            int w=a.screen.getWidth(),h=a.screen.getHeight();float scale=Math.min(w/1080f,h/1920f);
            float shownW=1080*scale,shownH=1920*scale,left=(w-shownW)/2,top=(h-shownH)/2;
                point[0]=left+shownW*corner[0];point[1]=top+shownH*corner[1];});
            long down=SystemClock.uptimeMillis();
            for(int action:new int[]{MotionEvent.ACTION_DOWN,MotionEvent.ACTION_UP}){
                runOnMainSync(()->{
                    MotionEvent e=MotionEvent.obtain(down,SystemClock.uptimeMillis(),action,point[0],point[1],0);
                    e.setSource(android.view.InputDevice.SOURCE_TOUCHSCREEN);a.screen.dispatchTouchEvent(e);e.recycle();
                });Thread.sleep(45);
            }
        }Thread.sleep(800);
    }
    public void onStart(){
        JSONObject report=new JSONObject();Bundle result=new Bundle();MainActivity a=null;
        File credential=new File(getTargetContext().getFilesDir(),"udp-test-login.json");Window.Callback original=null;
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
            final Window.Callback[] previous={null};runOnMainSync(()->previous[0]=target.getWindow().getCallback());
            original=previous[0];final Window.Callback delegate=original;
            runOnMainSync(()->target.getWindow().setCallback((Window.Callback)java.lang.reflect.Proxy.newProxyInstance(
                Window.Callback.class.getClassLoader(),new Class[]{Window.Callback.class},(proxy,method,values)->{
                    if(method.getName().equals("dispatchTouchEvent")&&values!=null&&values[0] instanceof MotionEvent){
                        MotionEvent e=(MotionEvent)values[0];try{if(delivered.length()<128)delivered.put(new JSONObject()
                            .put("action",e.getActionMasked()).put("count",e.getPointerCount()).put("source",e.getSource())
                            .put("event_time_ms",e.getEventTime()).put("down_time_ms",e.getDownTime()).put("action_index",e.getActionIndex())
                            .put("device_id",e.getDeviceId()).put("window_display_id",target.getDisplay().getDisplayId()));}catch(Exception ignored){}
                    }
                    try{return method.invoke(delegate,values);}catch(java.lang.reflect.InvocationTargetException failure){throw failure.getCause();}
                })));
            final Throwable[] problem={null};int oldGeneration=target.generation;
            runOnMainSync(()->{try{
                prepareUi(target,login.getString("username"),login.getString("password"));
                android.view.ViewGroup decor=(android.view.ViewGroup)target.getWindow().getDecorView();
                if(!clickStart(decor))throw new IllegalStateException("normal_UI_start_button_missing");
            }catch(Throwable e){problem[0]=e;}});
            final String username=login.getString("username"),password=login.getString("password");
            login.remove("password");login.remove("username");
            if(problem[0]!=null)throw new IllegalStateException("normal_UI_start",problem[0]);
            long deadline=SystemClock.elapsedRealtime()+25000;
            while((target.generation<=oldGeneration||target.receivedFrames.get()<15||target.presentedFrames.get()<10)&&SystemClock.elapsedRealtime()<deadline)Thread.sleep(100);
            if(target.generation<=oldGeneration||target.receivedFrames.get()<15||target.presentedFrames.get()<10)throw new IllegalStateException("no_authenticated_media");
            report.put("normal_UI_login_received_media",true);Thread.sleep(3000);
            try(FileOutputStream out=new FileOutputStream(new File(getTargetContext().getFilesDir(),"udp-ui-phase-steady-media"))){out.write(1);}
            Thread.sleep(22000);
            report.put("before_touch_received_frames",target.receivedFrames.get()).put("before_touch_callback_count",target.presentedFrames.get());
            if(!mediaOnly()){
            File phase=new File(getTargetContext().getFilesDir(),"udp-ui-phase-ready-touch");
            try(FileOutputStream out=new FileOutputStream(phase)){out.write(1);}
            File ready=new File(getTargetContext().getFilesDir(),"udp-ui-phase-touch-ready");deadline=SystemClock.elapsedRealtime()+15000;
            while(!ready.exists()&&SystemClock.elapsedRealtime()<deadline)Thread.sleep(100);
            if(!ready.exists())throw new IllegalStateException("touch_receipt_source_not_ready");
            ready.delete();phase.delete();
            // OS delivery is a separate layer. The phone vendor's intercepting
            // gesture panel has made repeated OS injection fail; do not silently
            // count direct View dispatch as OS or physical finger acceptance.
            String touchMode=arguments.getString("touch_mode","direct");
            boolean osAttempt=touchMode.equals("os"),osSuccess=false;
            if(touchMode.equals("adb")||touchMode.equals("kernel")){
                final int[] centre=new int[2];runOnMainSync(()->{target.screen.getLocationOnScreen(centre);
                    centre[0]+=target.screen.getWidth()/2;centre[1]+=target.screen.getHeight()/2;});
                try(FileOutputStream out=new FileOutputStream(new File(getTargetContext().getFilesDir(),"udp-ui-phase-adb-tap"))){
                    out.write(new JSONArray().put(centre[0]).put(centre[1]).toString().getBytes(StandardCharsets.US_ASCII));}
                File tapped=new File(getTargetContext().getFilesDir(),"udp-ui-phase-adb-tap-done");deadline=SystemClock.elapsedRealtime()+10000;
                while(!tapped.exists()&&SystemClock.elapsedRealtime()<deadline)Thread.sleep(100);
                if(!tapped.exists())throw new IllegalStateException("adb_OS_tap_driver_not_ready");tapped.delete();Thread.sleep(700);
                report.put("OS_shell_single_tap_attempted",touchMode.equals("adb"))
                    .put("OS_kernel_test_touch_attempted",touchMode.equals("kernel"));
            }
            if(osAttempt){try{
                pointers(target,1,true,true);Thread.sleep(300);pointers(target,2,true,true);Thread.sleep(400);
                osSuccess=osCleanupFailures==0;
            }catch(Exception failure){report.put("OS_failure_class",failure.getClass().getSimpleName())
                .put("OS_bounded_failure_label",failure.getMessage()!=null&&failure.getMessage().matches("[a-zA-Z_]+")?failure.getMessage():"unclassified");}}
            pointers(target,2,false,true);Thread.sleep(400);
            pointers(target,10,false,true);Thread.sleep(2000);
            cornerTaps(target);
            final String[] callbackCopy={null};runOnMainSync(()->callbackCopy[0]=delivered.toString());
            report.put("OS_inject_API_accepted_two_contacts_cancel_and_fresh_down",osSuccess)
                .put("OS_to_App_to_guest_acceptance_requires_correlated_receipt_review",true)
                .put("OS_injection_attempted_this_round",osAttempt)
                .put("OS_shell_single_tap_attempted",touchMode.equals("adb"))
                .put("OS_cleanup_failures",osCleanupFailures)
                .put("OS_injection_events",injected).put("phone_Window_Callback_deliveries",new JSONArray(callbackCopy[0]))
                .put("four_video_corners_one_percent_inset_direct_View_dispatch",true)
                .put("App_dispatched_two_and_ten_native_MotionEvent_contacts_cancel_and_fresh_down",true)
                .put("ten_contacts_delivered_through_phone_OS",false);
            }else report.put("media_only_no_touch_exercised",true);
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
                prepareUi(target,username,password);
                if(!clickStart(target.getWindow().getDecorView()))throw new IllegalStateException("reconnect_UI_button_missing");
            }catch(Throwable e){problem[0]=e;}});
            if(problem[0]!=null)throw new IllegalStateException("normal_UI_reconnect",problem[0]);
            deadline=SystemClock.elapsedRealtime()+25000;
            while((target.generation<=oldGeneration||target.receivedFrames.get()<15||target.presentedFrames.get()<10)&&SystemClock.elapsedRealtime()<deadline)Thread.sleep(100);
            if(target.generation<=oldGeneration||target.receivedFrames.get()<15||target.presentedFrames.get()<10)throw new IllegalStateException("reconnect_no_authenticated_media");
            Thread.sleep(2500);report.put("normal_UI_reconnected_received_media",true);
            if(!mediaOnly()){pointers(target,2,false,false);Thread.sleep(400);}
            runOnMainSync(()->target.handleBack());waitReport(first);
            report.put("disconnect_with_two_contacts_still_down",!mediaOnly()).put("running_after_second_leave",target.running);
        }catch(Throwable failure){try{report.put("failure_class",failure.getClass().getSimpleName());if(failure.getMessage()!=null&&failure.getMessage().matches("[a-zA-Z_]+"))report.put("bounded_failure_label",failure.getMessage());if(failure.getCause()!=null)report.put("failure_cause_class",failure.getCause().getClass().getSimpleName());}catch(Exception ignored){}}
        finally{credential.delete();if(a!=null){MainActivity target=a;Window.Callback restore=original;runOnMainSync(()->{if(restore!=null)target.getWindow().setCallback(restore);if(target.lanUdpEntry!=null)target.lanUdpEntry.cancel(true);});}}
        result.putString("numeric_result",report.toString());finish(report.has("failure_class")?Activity.RESULT_CANCELED:Activity.RESULT_OK,result);
    }
    private static boolean clickStart(android.view.View view){
        if(view instanceof Button&&((Button)view).getText().toString().equals("启动认证 UDP 测试")){view.performClick();return true;}
        if(view instanceof android.view.ViewGroup){android.view.ViewGroup group=(android.view.ViewGroup)view;for(int i=0;i<group.getChildCount();i++)if(clickStart(group.getChildAt(i)))return true;}
        return false;
    }
}
