package local.remoteandroid.direct;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.DialogInterface;
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
 * Default: reads one owner-only existing-account input, removes it before login.
 * Explicit saved-ui: uses normal App restore, never reads or modifies the store.
 * Never provisions a media key or exports any credential. */
public final class LanUiAcceptance extends Instrumentation {
    private Bundle arguments;
    private final JSONArray injected=new JSONArray(),delivered=new JSONArray();
    private int osCleanupFailures;private boolean osCleanup;
    private boolean lastV50ButtonClicked;private int v50ButtonClicks;
    public void onCreate(Bundle arguments){this.arguments=arguments;super.onCreate(arguments);start();}
    private int rateIndex(){int value=Integer.parseInt(arguments.getString("rate_index","2"));if(value<0||value>4)throw new IllegalArgumentException("rate_index_bound");return value;}
    private boolean v50Profile(){String value=arguments.getString("v50_profile","off");
        if(!value.equals("on")&&!value.equals("off"))throw new IllegalArgumentException("v50_profile_bound");return value.equals("on");}
    /** Select the actual label, so a 30/60 list or historical 60/120/30 list is safe. */
    private static void selectSpinnerLabel(Spinner spinner,String label){
        for(int index=0;index<spinner.getCount();index++)if(label.equals(String.valueOf(spinner.getItemAtPosition(index)))){
            spinner.setSelection(index);return;
        }throw new IllegalStateException("fps_label_missing");
    }
    private void applyVideoProfile(MainActivity target,Object ui)throws Exception{
        lastV50ButtonClicked=false;
        if(v50Profile()){
            if(!clickLabel(target.getWindow().getDecorView(),"真我 V50 · 一键均衡优化"))
                throw new IllegalStateException("v50_profile_button_missing");
            lastV50ButtonClicked=true;v50ButtonClicks++;
        }else{
            ((Spinner)field(ui,"rate")).setSelection(rateIndex());
            ((Spinner)field(ui,"quality")).setSelection(2);
            selectSpinnerLabel((Spinner)field(ui,"fps"),"60 FPS");
            ((Spinner)field(ui,"buffer")).setSelection(2);
        }
    }
    private boolean mediaOnly(){return arguments.getString("media_only","false").equals("true");}
    /** Local coordinator only. This helper selection is not a server/operator lease. */
    private boolean ownerNativeWindow(){
        String mode=arguments.getString("owner_native_window","off");
        if(!mode.equals("off")&&!mode.equals("single"))throw new IllegalArgumentException("native_window_mode");
        if(mode.equals("off"))return false;
        if(!networkScope().equals("lan")||!node().isEmpty()||!mediaOnly()||!v50Profile()
                ||!arguments.getString("credential_source","private-file").equals("saved-ui")
                ||credentialSave()||!arguments.getString("source_input","off").equals("off")
                ||!arguments.getString("pcm_queue","off").equals("off")||codecStartup()
                ||stageDiagnostics()||surfaceLeadMsUnchecked()!=0)
            throw new IllegalArgumentException("native_window_scope");
        nativeWindowSeconds();return true;
    }
    private int surfaceLeadMsUnchecked(){return Integer.parseInt(arguments.getString("surface_submit_lead_ms","0"));}
    private int nativeWindowSeconds(){int n=Integer.parseInt(arguments.getString("owner_native_window_seconds","5"));
        if(n<1||n>10)throw new IllegalArgumentException("native_window_seconds");return n;}
    /** Start-click precedes READY: deliberately earlier than the descriptor lease.
     * The server process ceiling may be earlier still; continuous checks can only
     * fail a shortened window, not establish a server-side ownership hold. */
    private static long nativeWindowBudgetEnd(long clickNs,long nowNs,int descriptorSeconds,int sampleSeconds){
        if(clickNs<=0||nowNs<clickNs||descriptorSeconds!=30||sampleSeconds<1||sampleSeconds>10
                ||clickNs>Long.MAX_VALUE-30_000_000_000L)
            throw new IllegalStateException("native_window_lease_unknown");
        long end=clickNs+30_000_000_000L;
        if(end-nowNs<(sampleSeconds+8L)*1_000_000_000L)
            throw new IllegalStateException("native_window_lease_insufficient");
        return end;
    }
    /** Helper-only opt-in; never enabled by the App, saved preferences or defaults. */
    private boolean sourceInput(){String value=arguments.getString("source_input","off");
        boolean enabled=OwnerSourceTap.enabled(value);
        if(enabled&&(!mediaOnly()||!networkScope().equals("nps_owner")
                ||!node().equals("m1")||!savedUiCredentials()))throw new IllegalArgumentException("source_input_owner_scope");
        return enabled;}
    private boolean sourcePauseOnly(){return OwnerSourceTap.pauseOnly(arguments.getString("source_input","off"));}
    private boolean sourceFrameOnly(){return OwnerSourceTap.frameOnly(arguments.getString("source_input","off"));}
    private String networkScope(){String value=arguments.getString("network_scope","lan");
        if(!value.equals("lan")&&!value.equals("tailnet")&&!value.equals("nps_owner"))throw new IllegalArgumentException("scope_bound");return value;}
    private String node(){String value=arguments.getString("node","");
        if(networkScope().equals("nps_owner")){
            if(!value.equals("m1")&&!value.equals("m5"))throw new IllegalArgumentException("node_bound");
        }else if(!value.isEmpty())throw new IllegalArgumentException("unexpected_node");return value;}
    private int scopeIndex(){return networkScope().equals("nps_owner")?(node().equals("m1")?2:3):networkScope().equals("tailnet")?1:0;}
    private String controlAddress()throws Exception{return networkScope().equals("nps_owner")?
        LanUdpContract.NPS_HOST+":"+LanUdpContract.npsHttpsPort(node()):
        (networkScope().equals("tailnet")?LanUdpContract.TAILNET_HOST:"192.168.9.128")+":"+LanUdpContract.HTTPS_PORT;}
    private boolean codecStartup(){String value=arguments.getString("codec_startup","off");
        if(!value.equals("on")&&!value.equals("off"))throw new IllegalArgumentException("codec_startup_bound");if(v50Profile()&&value.equals("on"))throw new IllegalArgumentException("v50_startup_conflict");return value.equals("on");}
    private boolean stageDiagnostics(){String value=arguments.getString("stage_diagnostics","on");
        if(!value.equals("on")&&!value.equals("off"))throw new IllegalArgumentException("stage_diagnostics_bound");return !v50Profile()&&value.equals("on");}
    private int steadySeconds(){int value=Integer.parseInt(arguments.getString("steady_seconds","20"));if(value<20||value>150)throw new IllegalArgumentException("steady_seconds_bound");return value;}
    private int surfaceLeadMs()throws Exception{
        String raw=arguments.getString("surface_submit_lead_ms","0");
        if(!raw.equals("0")&&!raw.equals("16"))throw new IllegalArgumentException("surface_submit_lead_bound");
        int value=Integer.parseInt(raw);if(v50Profile()&&value!=0)throw new IllegalArgumentException("v50_surface_lead_conflict");LanUdpContract.validateOwnerSurfaceLead(value);return value;
    }
    /** Probe-only liveness; worker/callback counts are not presented or unique-content FPS. */
    private void waitSteady(MainActivity target,JSONObject report,long steadyStart)throws Exception{
        long requiredEnd=steadyStart+(steadySeconds()+2)*1_000_000_000L;
        // Keep <=48 numeric rows spanning the complete bounded window; the
        //250ms running/progress monitor remains independent of sample density.
        long sampleIntervalNs=Math.max(1_000_000_000L,(steadySeconds()+2)*1_000_000_000L/46);
        long lastProgress=steadyStart,lastReceived=target.receivedFrames.get(),lastCallback=target.presentedFrames.get();
        long nextSample=steadyStart,maxIdle=0;boolean stalled=false;JSONArray rows=new JSONArray();
        File sampled=new File(getTargetContext().getFilesDir(),"udp-ui-phase-steady-sampled");
        while(true){long now=System.nanoTime(),received=target.receivedFrames.get(),callback=target.presentedFrames.get();
            if(received>lastReceived||callback>lastCallback)lastProgress=now;
            maxIdle=Math.max(maxIdle,now-lastProgress);if(!target.running||now-lastProgress>=3_000_000_000L)stalled=true;
            if(now>=nextSample&&rows.length()<48){rows.put(new JSONObject().put("phone_ns",now)
                .put("worker_received_frames",received).put("codec_callback_count",callback));nextSample=now+sampleIntervalNs;}
            lastReceived=received;lastCallback=callback;
            if(now>=requiredEnd&&sampled.exists())break;
            if(now>=requiredEnd+10_000_000_000L)throw new IllegalStateException("steady_sampler_completion_missing");
            Thread.sleep(250);
        }
        if(!sampled.delete())throw new IllegalStateException("steady_sampler_completion_cleanup");
        report.put("steady_progress_monitor_enabled",true).put("steady_media_progress_healthy",!stalled)
            .put("steady_progress_max_idle_ns",maxIdle).put("steady_progress_stall_threshold_ns",3_000_000_000L)
            .put("steady_progress_samples",rows).put("steady_progress_is_presented_fps",false);
    }
    private static boolean authorizedTrialAccount(String username){return username.equals("wyw")||username.equals("huoguo");}
    private boolean savedUiCredentials(){
        String value=arguments.getString("credential_source","private-file");
        if(!value.equals("private-file")&&!value.equals("saved-ui"))throw new IllegalArgumentException("credential_source_bound");
        if(value.equals("saved-ui")&&((!networkScope().equals("nps_owner")&&!ownerNativeWindow())||credentialSave()))
            throw new IllegalArgumentException("saved_UI_exact_public_read_only_required");
        return value.equals("saved-ui");
    }
    private static String savedUiValidationFailure(int actualScope,int expectedScope,String actualAddress,
            String expectedAddress,String username,int secretLength){
        if(actualScope!=expectedScope||!expectedAddress.equals(actualAddress))return "saved_UI_route_unverified";
        if(!authorizedTrialAccount(username))return "saved_UI_account_unavailable";
        if(secretLength<1||secretLength>1024)return "saved_UI_credential_unavailable";
        return "";
    }
    private void assertIdleUi(Object ui)throws Exception{
        synchronized(field(ui,"lock")){if(field(ui,"current")!=null||field(ui,"retiring")!=null)
            throw new IllegalStateException("existing_UI_attempt_busy");}
    }
    private String savedUiValidation(Object ui)throws Exception{
        if((Boolean)field(ui,"restoringFields"))return "saved_UI_route_unverified";
        return savedUiValidationFailure(((Spinner)field(ui,"scope")).getSelectedItemPosition(),scopeIndex(),
            ((EditText)field(ui,"address")).getText().toString(),controlAddress(),
            ((EditText)field(ui,"user")).getText().toString(),((EditText)field(ui,"password")).getText().length());
    }
    private void throwSavedUiFailure(Throwable failure)throws Exception{
        if(failure!=null){
            if("existing_UI_attempt_busy".equals(failure.getMessage()))throw new IllegalStateException("existing_UI_attempt_busy");
            throw new IllegalStateException("saved_UI_restore_unavailable",failure);
        }
    }
    private void prepareUi(MainActivity target,String username,String password)throws Exception{
        Object ui=target.lanUdpEntry;
        if(networkScope().equals("nps_owner")&&!authorizedTrialAccount(username))throw new IllegalArgumentException("nps_owner_account_required");
        ((Spinner)field(ui,"scope")).setSelection(scopeIndex());
        ((EditText)field(ui,"address")).setText(controlAddress());
        ((EditText)field(ui,"user")).setText(username);((EditText)field(ui,"password")).setText(password);
        applyMediaOptions(target,ui);
    }
    private void applyMediaOptions(MainActivity target,Object ui)throws Exception{
        applyVideoProfile(target,ui);
        ((CheckBox)field(ui,"sound")).setChecked(true);
        String pcm=arguments.getString("pcm_queue","off");if(!pcm.equals("on")&&!pcm.equals("off"))throw new IllegalArgumentException("pcm_choice_bound");
        if(v50Profile()&&pcm.equals("on"))throw new IllegalArgumentException("v50_pcm_conflict");
        ((CheckBox)field(ui,"pcmQueue")).setChecked(pcm.equals("on"));
        ((CheckBox)field(ui,"codecStartup")).setChecked(codecStartup());
        Field lead=ui.getClass().getDeclaredField("ownerSurfaceSubmitLeadMs");lead.setAccessible(true);lead.setInt(ui,surfaceLeadMs());
        Field stages=ui.getClass().getDeclaredField("ownerStageDiagnosticsEnabled");stages.setAccessible(true);stages.setBoolean(ui,stageDiagnostics());
    }
    /** Uses the App's spinner/text listeners and encrypted restore; no secret String snapshot. */
    private void prepareSavedUi(MainActivity target,JSONObject report,String stage)throws Exception{
        Object ui=target.lanUdpEntry;final Throwable[] problem={null};
        runOnMainSync(()->{try{assertIdleUi(ui);
            ((Spinner)field(ui,"scope")).setSelection(scopeIndex());
        }catch(Throwable failure){problem[0]=failure;}});
        throwSavedUiFailure(problem[0]);
        // The normal scope listener supplies its fixed public address and saved
        // username. Writing address before that posted callback would persist
        // the previous scope's username into the newly selected preference.
        waitForIdleSync(); // Selection return is not restore completion.
        final String[] validation={"saved_UI_route_unverified"};
        boolean ready=awaitUiCallback(()->{
            runOnMainSync(()->{try{assertIdleUi(ui);
                validation[0]=savedUiValidation(ui);
            }catch(Throwable failure){problem[0]=failure;}});
            throwSavedUiFailure(problem[0]);
            return validation[0].isEmpty();
        },SystemClock::elapsedRealtime,Thread::sleep);
        if(!ready)throw new IllegalStateException(validation[0]);
        runOnMainSync(()->{try{assertIdleUi(ui);applyMediaOptions(target,ui);}catch(Throwable failure){problem[0]=failure;}});
        throwSavedUiFailure(problem[0]);
        report.put(stage+"_saved_UI_route_verified",true).put(stage+"_saved_UI_allowed_account_verified",true)
            .put(stage+"_saved_UI_nonempty_credential_verified",true).put(stage+"_saved_UI_normal_restore_used",true);
    }
    private void throwUiStartFailure(Throwable failure,String fallback)throws Exception{
        if(failure!=null){
            if("existing_UI_attempt_busy".equals(failure.getMessage()))throw new IllegalStateException("existing_UI_attempt_busy");
            if("saved_UI_route_unverified".equals(failure.getMessage())||"saved_UI_account_unavailable".equals(failure.getMessage())
                    ||"saved_UI_credential_unavailable".equals(failure.getMessage()))throw new IllegalStateException(failure.getMessage());
            throw new IllegalStateException(fallback,failure);
        }
    }
    private boolean credentialSave(){String value=arguments.getString("credential_save","off");
        if(!value.equals("on")&&!value.equals("off"))throw new IllegalArgumentException("credential_save_bound");return value.equals("on");}
    private MainActivity reopenIdleActivity(MainActivity old)throws Exception{
        Object ui=old.lanUdpEntry;
        synchronized(field(ui,"lock")){if(field(ui,"current")!=null||field(ui,"retiring")!=null)throw new IllegalStateException("credential_UI_attempt_busy");}
        runOnMainSync(old::finish);waitForIdleSync();
        MainActivity fresh=(MainActivity)startActivitySync(new Intent().setClassName(getTargetContext().getPackageName(),"local.remoteandroid.direct.MainActivity").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        waitForIdleSync();return fresh;
    }
    /** Normal encrypted-save/clear UI; never report password, ciphertext or key material. */
    private MainActivity credentialUiAcceptance(MainActivity initial,String username,String password,JSONObject report)throws Exception{
        MainActivity target=initial;final Throwable[] problem={null};MainActivity first=target;
        runOnMainSync(()->{try{prepareUi(first,username,password);if(!clickLabel(first.getWindow().getDecorView(),"保存密码"))throw new IllegalStateException("credential_save_button_missing");}catch(Throwable failure){problem[0]=failure;}});
        if(problem[0]!=null)throw new IllegalStateException("credential_save_UI",problem[0]);
        target=reopenIdleActivity(target);MainActivity reopened=target;
        final boolean[] restored={false};runOnMainSync(()->{try{Object ui=reopened.lanUdpEntry;restored[0]=((EditText)field(ui,"user")).getText().toString().equals(username)
            &&((EditText)field(ui,"password")).getText().toString().equals(password);if(!restored[0])throw new IllegalStateException("credential_reopen_not_restored");
            if(!clickLabel(reopened.getWindow().getDecorView(),"清除已保存密码"))throw new IllegalStateException("credential_clear_button_missing");}catch(Throwable failure){problem[0]=failure;}});
        if(problem[0]!=null)throw new IllegalStateException("credential_restore_clear_UI",problem[0]);
        target=reopenIdleActivity(target);MainActivity cleared=target;final boolean[] empty={false};
        runOnMainSync(()->{try{empty[0]=((EditText)field(cleared.lanUdpEntry,"password")).getText().toString().isEmpty();if(!empty[0])throw new IllegalStateException("credential_clear_not_persistent");
            prepareUi(cleared,username,password);if(!clickLabel(cleared.getWindow().getDecorView(),"保存密码"))throw new IllegalStateException("credential_final_save_button_missing");}catch(Throwable failure){problem[0]=failure;}});
        if(problem[0]!=null)throw new IllegalStateException("credential_final_save_UI",problem[0]);
        target=reopenIdleActivity(target);MainActivity finalTarget=target;final boolean[] retained={false};
        runOnMainSync(()->{try{Object ui=finalTarget.lanUdpEntry;retained[0]=((EditText)field(ui,"password")).getText().toString().equals(password)
            &&((EditText)field(ui,"user")).getText().toString().equals(username);}catch(Throwable failure){problem[0]=failure;}});
        if(problem[0]!=null||!retained[0])throw new IllegalStateException("credential_final_reopen_not_restored");
        report.put("credential_save_used_actual_UI",true).put("credential_save_reopen_restored",restored[0])
            .put("credential_clear_reopen_empty",empty[0]).put("credential_final_save_reopen_retained",retained[0])
            .put("credential_secret_exported",false).put("credential_other_package_modified",false);
        return target;
    }
    private static volatile long sink;
    private static Object field(Object target,String name)throws Exception{
        Field field=target.getClass().getDeclaredField(name);field.setAccessible(true);return field.get(target);
    }
    private final java.util.HashSet<File> sourceMarkers=new java.util.HashSet<>();
    private File sourceMarker(String name){return new File(getTargetContext().getFilesDir(),"udp-ui-source-"+name);}
    private void writeSourceMarker(File file,String data)throws Exception{
        if(!file.createNewFile())throw new IllegalStateException("source_marker_preexisting");
        sourceMarkers.add(file);android.system.Os.chmod(file.getPath(),0600);
        try(FileOutputStream out=new FileOutputStream(file)){out.write(data.getBytes(StandardCharsets.US_ASCII));}
    }
    /** Bounded, no-follow actual descriptor read; exact inode checked before removal. */
    private byte[] readSourceMarker(File file,int bound)throws Exception{
        java.io.FileDescriptor fd=android.system.Os.open(file.getPath(),android.system.OsConstants.O_RDONLY
            |android.system.OsConstants.O_NOFOLLOW|android.system.OsConstants.O_NONBLOCK,0);
        try(FileInputStream in=new FileInputStream(fd)){
            android.system.StructStat stat=android.system.Os.fstat(fd);
            if(!android.system.OsConstants.S_ISREG(stat.st_mode)||stat.st_uid!=getTargetContext().getApplicationInfo().uid
                    ||(stat.st_mode&0777)!=0600||stat.st_nlink!=1||stat.st_size<1||stat.st_size>bound)
                throw new IllegalStateException("source_marker_descriptor_bound");
            byte[] raw=new byte[(int)stat.st_size];int used=0,count;
            while(used<raw.length&&(count=in.read(raw,used,raw.length-used))!=-1)used+=count;
            if(used!=raw.length||in.read()!=-1)throw new IllegalStateException("source_marker_changed_size");
            android.system.StructStat current=android.system.Os.lstat(file.getPath());
            if(current.st_dev!=stat.st_dev||current.st_ino!=stat.st_ino)
                throw new IllegalStateException("source_marker_changed_inode");
            return raw;
        }
    }
    private byte[] awaitSourceMarker(File file,int bound)throws Exception{
        long started=SystemClock.elapsedRealtime();
        for(int polls=0;polls<251;polls++){
            if(SystemClock.elapsedRealtime()-started>=10000)break;
            if(file.exists())return readSourceMarker(file,bound);
            Thread.sleep(40);
        }throw new IllegalStateException("source_marker_timeout");
    }
    /** One phase inside the already authenticated captured Attempt. The external
     * observer qualifies source identity/target and verifies actual playback;
     * no guest ADB command is sent here and no local return is called playback. */
    private void sourceInputPhase(MainActivity target,Object ownedAttempt,int phase,JSONObject report)throws Exception{
        Object ui=target.lanUdpEntry;final Object[] receiver={null},touch={null};
        final android.view.SurfaceView[] surface={null};final long[] uiGen={0};final int[] appGen={0};
        final OwnerSourceTap.Geometry[] geometry={null};final Throwable[] failure={null};
        runOnMainSync(()->{try{synchronized(field(ui,"lock")){
            if(field(ui,"current")!=ownedAttempt||ownedAttempt==null||(Boolean)field(ownedAttempt,"cancelled")
                    ||(Boolean)field(ownedAttempt,"stopped")||!target.running)throw new IllegalStateException("source_owned_attempt_missing");
            uiGen[0]=(Long)field(ui,"generation");appGen[0]=target.generation;
            receiver[0]=field(ownedAttempt,"receiver");if(receiver[0]==null)throw new IllegalStateException("source_receiver_missing");
            touch[0]=field(receiver[0],"touchControl");if(touch[0]==null)throw new IllegalStateException("source_native_touch_missing");
            synchronized(field(touch[0],"lock")){
                surface[0]=target.screen;
                if(field(touch[0],"surface")!=surface[0]||(Boolean)field(touch[0],"closed")
                        ||(Integer)field(touch[0],"rotation")!=0
                        ||!((java.util.Map<?,?>)field(touch[0],"contacts")).isEmpty())
                    throw new IllegalStateException("source_native_surface_or_contacts");
                geometry[0]=new OwnerSourceTap.Geometry(surface[0].getWidth(),surface[0].getHeight(),
                    (Integer)field(touch[0],"streamWidth"),(Integer)field(touch[0],"streamHeight"));
            }
        }}catch(Throwable error){failure[0]=error;}});
        if(failure[0]!=null)throw new IllegalStateException("source_input_capture",failure[0]);
        long nonce;do{nonce=new java.security.SecureRandom().nextLong()&Long.MAX_VALUE;}while(nonce==0);
        File ready=sourceMarker(phase+"-ready"),command=sourceMarker(phase+"-command"),verified=sourceMarker(phase+"-verified"),dispatched=sourceMarker(phase+"-dispatched");
        if(ready.exists()||command.exists()||verified.exists()||dispatched.exists())throw new IllegalStateException("source_phase_preexisting");
        writeSourceMarker(ready,new JSONObject().put("phase",phase).put("nonce",nonce).put("app_generation",appGen[0])
            .put("ui_generation",uiGen[0]).put("surface_width",geometry[0].surfaceWidth).put("surface_height",geometry[0].surfaceHeight)
            .put("image_width",geometry[0].imageWidth).put("image_height",geometry[0].imageHeight).toString());
        final long sourceNonce=nonce;OwnerSourceTap.Receipt receipt=new OwnerSourceTap.Receipt();
        class OwnedHooks implements OwnerSourceTap.Hooks{
            boolean check()throws Exception{
                return field(ui,"current")==ownedAttempt&&(Long)field(ui,"generation")==uiGen[0]
                    &&!(Boolean)field(ownedAttempt,"cancelled")&&!(Boolean)field(ownedAttempt,"stopped")
                    &&field(ownedAttempt,"receiver")==receiver[0]&&field(receiver[0],"touchControl")==touch[0]
                    &&target.running&&target.generation==appGen[0]&&target.screen==surface[0]
                    &&field(touch[0],"surface")==surface[0]&&!(Boolean)field(touch[0],"closed")
                    &&(Integer)field(touch[0],"rotation")==0
                    &&surface[0].getHolder().getSurface().isValid()
                    &&surface[0].getWidth()==geometry[0].surfaceWidth&&surface[0].getHeight()==geometry[0].surfaceHeight
                    &&(Integer)field(touch[0],"streamWidth")==geometry[0].imageWidth
                    &&(Integer)field(touch[0],"streamHeight")==geometry[0].imageHeight;
            }
            public boolean owns()throws Exception{
                final boolean[] result={false};final Throwable[] error={null};
                runOnMainSync(()->{try{synchronized(field(ui,"lock")){synchronized(field(touch[0],"lock")){result[0]=check();}}}
                    catch(Throwable e){error[0]=e;}});
                if(error[0]!=null)throw new IllegalStateException("source_input_ownership",error[0]);return result[0];
            }
            public long uptimeMillis(){return SystemClock.uptimeMillis();}
            public void pause(long millis)throws Exception{Thread.sleep(millis);}
            public void dispatch(int action,long downTime,float x,float y)throws Exception{
                final Throwable[] error={null};
                runOnMainSync(()->{try{synchronized(field(ui,"lock")){synchronized(field(touch[0],"lock")){
                    if(!check())throw new IllegalStateException("source_input_changed_before_dispatch");
                    if(action==OwnerSourceTap.DOWN&&!((java.util.Map<?,?>)field(touch[0],"contacts")).isEmpty())
                        throw new IllegalStateException("source_input_contacts_busy");
                    MotionEvent event=MotionEvent.obtain(downTime,SystemClock.uptimeMillis(),action,x,y,0);
                    event.setSource(android.view.InputDevice.SOURCE_TOUCHSCREEN);
                    long beforeEvents=(Long)field(touch[0],"motionEvents");
                    try{if(!surface[0].dispatchTouchEvent(event))throw new IllegalStateException("source_input_View_rejected");
                        if((Long)field(touch[0],"motionEvents")!=beforeEvents+1
                                ||((java.util.Map<?,?>)field(touch[0],"contacts")).size()!=(action==OwnerSourceTap.DOWN?1:0))
                            throw new IllegalStateException("source_input_native_contact_not_observed");}
                    finally{event.recycle();}
                }}}catch(Throwable e){error[0]=e;}});
                if(error[0]!=null)throw new IllegalStateException("source_input_dispatch",error[0]);
            }
        }
        if(sourceFrameOnly()){
            byte[] raw=awaitSourceMarker(command,32);
            if(!command.delete())throw new IllegalStateException("source_frame_command_cleanup");
            OwnerSourceFrame.Receipt observation=new OwnerSourceFrame.Receipt();
            OwnedHooks current=new OwnedHooks();
            try{OwnerSourceFrame.observe(raw,phase,sourceNonce,new OwnerSourceFrame.Hooks(){
                public boolean owns()throws Exception{return current.owns();}
                public void published(byte[] body)throws Exception{
                    writeSourceMarker(dispatched,new String(body,StandardCharsets.US_ASCII));
                }
                public byte[] confirmation()throws Exception{return awaitSourceMarker(verified,32);}
            },observation);}
            finally{report.put("source_frame_observation",new JSONObject()
                .put("owned_before",observation.ownedBefore).put("publication_attempted",observation.publicationAttempted)
                .put("publication_returned",observation.publicationReturned).put("confirmation_matched",observation.confirmationMatched)
                .put("owned_after",observation.ownedAfter).put("input_sent",false)
                .put("nonce",sourceNonce)
                .put("remote_pixels_verified_by_helper",false).put("source_identity_verified_by_helper",false)
                .put("server_lease_independently_verified_by_helper",false).put("atomic_hold",false));}
            if(!verified.delete())throw new IllegalStateException("source_frame_verified_cleanup");
            return;
        }
        byte[] raw=awaitSourceMarker(command,128);OwnerSourceTap.Command input=OwnerSourceTap.Command.parse(raw,phase,nonce);
        if(!command.delete())throw new IllegalStateException("source_command_cleanup");
        try{OwnerSourceTap.run(new OwnedHooks(),input,geometry[0],receipt);}
        finally{report.put("source_phase_"+phase+"_local_tap",new JSONObject().put("down_attempted",receipt.downAttempted)
            .put("down_returned",receipt.downReturned).put("up_attempted",receipt.upAttempted).put("up_returned",receipt.upReturned)
            .put("cancel_attempted",receipt.cancelAttempted).put("cancel_returned",receipt.cancelReturned)
            .put("cancel_skipped_for_changed_owner",receipt.cancelSkippedForChangedOwner)
            .put("remote_playback_verified_by_helper",false));}
        writeSourceMarker(dispatched,sourceNonce+"\n");
        byte[] confirmation=awaitSourceMarker(verified,32);
        if(!java.util.Arrays.equals(confirmation,(sourceNonce+"\n").getBytes(StandardCharsets.US_ASCII)))
            throw new IllegalStateException("source_observer_nonce_changed");
        if(!verified.delete())throw new IllegalStateException("source_verified_marker_cleanup");
        report.put("source_phase_"+phase+"_external_observer_confirmation",true)
            .put("source_phase_"+phase+"_external_observer_is_helper_independent_measurement",false);
    }
    /** Actual in-flight App attempt and parsed receiver tuple, not a request echo.
     * No credential, key, tag, session id or arbitrary server text is read/exported.
     * Release the UI monitor before any wait, Back, codec call or network work.
     */
    private void verifyNetworkReadback(MainActivity target,JSONObject report,String stage)throws Exception{
        Object ui=target.lanUdpEntry;String actualScope,actualNode,actualControl,peer;int port,actualFps,actualBuffer,actualWidth,actualHeight;
        NpsPhysicalNetwork controlNetwork=null,mediaNetwork=null;
        synchronized(field(ui,"lock")){
            Object current=field(ui,"current");if(current==null)throw new IllegalStateException("network_attempt_missing");
            actualScope=(String)field(current,"networkScope");actualControl=(String)field(current,"endpoint");
            // Existing alpha5 attempts have no node; only alpha6 public trials
            // require this new field. Preserve old LAN/Tailnet helper behavior.
            actualNode=networkScope().equals("nps_owner")?(String)field(current,"node"):"";
            Object receiver=field(current,"receiver");if(receiver==null)throw new IllegalStateException("network_receiver_missing");
            if(networkScope().equals("nps_owner")){
                controlNetwork=(NpsPhysicalNetwork)field(current,"physicalNetwork");
                mediaNetwork=(NpsPhysicalNetwork)field(receiver,"appPhysicalNetwork");
            }
            Object parsed=field(receiver,"appSession");if(parsed==null)throw new IllegalStateException("network_session_missing");
            peer=((java.net.InetAddress)field(parsed,"peer")).getHostAddress();port=(Integer)field(parsed,"peerPort");
            actualFps=(Integer)field(parsed,"fps");actualBuffer=(Integer)field(parsed,"buffer");
            // Receiver geometry comes from configured real video frames, not the quality spinner.
            actualWidth=(Integer)field(receiver,"width");actualHeight=(Integer)field(receiver,"height");
        }
        if(networkScope().equals("nps_owner")){
            if(controlNetwork==null||controlNetwork!=mediaNetwork||controlNetwork.httpsBindings()<1||controlNetwork.udpBindings()!=1)
                throw new IllegalStateException("physical_network_lease_binding");
            // Android API/lease evidence only; not packet capture or country evidence.
            controlNetwork.requireUsable();
            report.put(stage+"_physical_network_same_lease",true)
                .put(stage+"_physical_network_handle",controlNetwork.handle())
                .put(stage+"_physical_network_transport",controlNetwork.transport())
                .put(stage+"_physical_https_bind_calls",controlNetwork.httpsBindings())
                .put(stage+"_physical_udp_bind_calls",controlNetwork.udpBindings())
                .put(stage+"_physical_packet_route_verified",false)
                .put(stage+"_physical_domestic_country_verified",false);
        }
        if(actualFps!=(v50Profile()?30:60)||actualBuffer!=80||actualWidth<1||actualHeight<1)
            throw new IllegalStateException("video_profile_readback_mismatch");
        if(v50Profile()&&(!lastV50ButtonClicked||v50ButtonClicks<1||Math.max(actualWidth,actualHeight)>960))
            throw new IllegalStateException("v50_profile_readback_mismatch");
        report.put(stage+"_actual_fps_limit",actualFps).put(stage+"_actual_buffer_ms",actualBuffer)
            .put(stage+"_actual_video_width",actualWidth).put(stage+"_actual_video_height",actualHeight)
            .put(stage+"_v50_profile_button_clicked",lastV50ButtonClicked).put(stage+"_v50_profile_button_click_count",v50ButtonClicks)
            .put(stage+"_video_profile_readback_verified",true).put(stage+"_video_profile_is_presented_FPS",false);
        Endpoint.Address expected=Endpoint.parse(Endpoint.destination(controlAddress()));
        if(!actualScope.equals(networkScope())||!actualNode.equals(node())
                ||!actualControl.equals(Endpoint.destination(controlAddress())))throw new IllegalStateException("network_attempt_binding");
        if(networkScope().equals("nps_owner")){
            LanUdpContract.validateLogin(expected.host,expected.port,actualScope,actualNode);
            LanUdpContract.validateAppMediaPeer(peer,port,actualScope,actualNode);
        }else{
            LanUdpContract.validateLogin(expected.host,expected.port,actualScope);
            if(port!=LanUdpContract.UDP_PORT)throw new IllegalStateException("network_media_port_binding");
        }
        if(!peer.equals(expected.host))throw new IllegalStateException("network_peer_binding");
        report.put(stage+"_actual_network_scope",actualScope).put(stage+"_actual_node",actualNode)
            .put(stage+"_actual_control_host",expected.host).put(stage+"_actual_control_port",expected.port)
            .put(stage+"_actual_media_peer_host",peer).put(stage+"_actual_media_peer_port",port)
            .put(stage+"_actual_received_frames",target.receivedFrames.get())
            .put(stage+"_actual_codec_callback_count",target.presentedFrames.get())
            .put(stage+"_network_readback_verified",true).put(stage+"_media_transport_code",1)
            .put(stage+"_media_transport_is_App_UDP_not_NPC_outer_verification",true);
    }
    @FunctionalInterface private interface CallbackPause {void sleep(long millis)throws InterruptedException;}
    /** performClick posts AlertDialog's listener; completion must be observed separately.
     * Monotonic, <=1500ms/76 polls; never holds an attempt monitor while sleeping. */
    private static boolean awaitUiCallback(java.util.concurrent.Callable<Boolean> condition,
            java.util.function.LongSupplier clock,CallbackPause pause)throws Exception{
        long started=clock.getAsLong(),last=started;if(started<0)throw new IllegalStateException("UI_callback_clock_invalid");
        for(int polls=0;polls<76;polls++){
            long now=clock.getAsLong();if(now<last)throw new IllegalStateException("UI_callback_clock_invalid");
            if(now-started>1500)return false;
            boolean ready=Boolean.TRUE.equals(condition.call());long observed=clock.getAsLong();
            if(observed<now)throw new IllegalStateException("UI_callback_clock_invalid");
            if(observed-started>1500)return false;if(ready)return true;
            long remaining=1500-(observed-started);if(remaining<=0)return false;
            pause.sleep(Math.min(20,remaining));last=observed;
        }return false;
    }
    /** Actual Back dialog and button listeners; never bypass positive action with backend cancel. */
    private void leaveThroughConfirmation(MainActivity target,JSONObject report,String stage)throws Exception{
        leaveThroughConfirmation(target,report,stage,null);
    }
    private void leaveThroughConfirmation(MainActivity target,JSONObject report,String stage,Object requiredOwned)throws Exception{
        Object ui=target.lanUdpEntry;Object captured;long generation;
        synchronized(field(ui,"lock")){
            captured=field(ui,"current");generation=(Long)field(ui,"generation");
            if(captured==null||(Boolean)field(captured,"cancelled")||(requiredOwned!=null&&captured!=requiredOwned))
                throw new IllegalStateException("exit_attempt_missing");
        }
        final AlertDialog[] dialogs={null,null};final Throwable[] problem={null};
        runOnMainSync(()->{try{
            synchronized(field(ui,"lock")){
                if(field(ui,"current")!=captured||(Long)field(ui,"generation")!=generation||(Boolean)field(captured,"cancelled"))
                    throw new IllegalStateException("exit_initial_captured_attempt_changed");
            }
            target.handleBack();dialogs[0]=(AlertDialog)field(ui,"exitDialog");
            if(dialogs[0]==null||!dialogs[0].isShowing())throw new IllegalStateException("exit_dialog_missing");
            if(!dialogs[0].getButton(DialogInterface.BUTTON_NEGATIVE).getText().toString().equals("继续使用")
                    ||!dialogs[0].getButton(DialogInterface.BUTTON_POSITIVE).getText().toString().equals("退出连接"))
                throw new IllegalStateException("exit_dialog_buttons");
            target.handleBack();dialogs[1]=(AlertDialog)field(ui,"exitDialog");
            if(dialogs[0]!=dialogs[1])throw new IllegalStateException("exit_dialog_stacked");
            synchronized(field(ui,"lock")){
                if(field(ui,"current")!=captured||(Long)field(ui,"generation")!=generation||(Boolean)field(captured,"cancelled"))
                    throw new IllegalStateException("exit_dialog_changed_attempt");
            }
            dialogs[0].getButton(DialogInterface.BUTTON_NEGATIVE).performClick();
        }catch(Throwable failure){problem[0]=failure;}});
        if(problem[0]!=null)throw new IllegalStateException("exit_continue_UI",problem[0]);
        // ButtonHandler and dismissal are messages queued by performClick.
        boolean continued=awaitUiCallback(()->{
            final boolean[] complete={false};final Throwable[] failure={null};
            runOnMainSync(()->{try{
                synchronized(field(ui,"lock")){
                    if(field(ui,"current")!=captured||(Long)field(ui,"generation")!=generation||(Boolean)field(captured,"cancelled"))
                        throw new IllegalStateException("exit_continue_captured_attempt_changed");
                }
                complete[0]=!dialogs[0].isShowing()&&field(ui,"exitDialog")!=dialogs[0]
                    &&!((UdpExitConfirmationGate)field(ui,"exitGate")).pending();
            }catch(Throwable e){failure[0]=e;}});
            if(failure[0]!=null)throw new IllegalStateException("exit_continue_callback_state",failure[0]);
            return complete[0];
        },SystemClock::elapsedRealtime,Thread::sleep);
        if(!continued)throw new IllegalStateException("exit_continue_callback_timeout");
        long beforeReceived=target.receivedFrames.get(),beforeCallback=target.presentedFrames.get();
        long deadline=SystemClock.elapsedRealtime()+3000;
        while(target.running&&target.receivedFrames.get()<=beforeReceived&&target.presentedFrames.get()<=beforeCallback
                &&SystemClock.elapsedRealtime()<deadline)Thread.sleep(50);
        boolean progress=target.receivedFrames.get()>beforeReceived||target.presentedFrames.get()>beforeCallback;
        synchronized(field(ui,"lock")){
            if(field(ui,"current")!=captured||(Long)field(ui,"generation")!=generation||(Boolean)field(captured,"cancelled")||!target.running||!progress)
                throw new IllegalStateException("exit_continue_not_live");
        }
        runOnMainSync(()->{try{
            synchronized(field(ui,"lock")){
                if(field(ui,"current")!=captured||(Long)field(ui,"generation")!=generation||(Boolean)field(captured,"cancelled"))
                    throw new IllegalStateException("exit_positive_captured_attempt_changed");
            }
            target.handleBack();AlertDialog next=(AlertDialog)field(ui,"exitDialog");
            if(next==null||next==dialogs[0]||!next.isShowing())throw new IllegalStateException("exit_second_dialog_missing");
            synchronized(field(ui,"lock")){
                if(field(ui,"current")!=captured||(Long)field(ui,"generation")!=generation||(Boolean)field(captured,"cancelled"))
                    throw new IllegalStateException("exit_positive_captured_attempt_changed");
            }
            next.getButton(DialogInterface.BUTTON_POSITIVE).performClick();
        }catch(Throwable failure){problem[0]=failure;}});
        if(problem[0]!=null)throw new IllegalStateException("exit_positive_UI",problem[0]);
        boolean exited=awaitUiCallback(()->{
            synchronized(field(ui,"lock")){
                if((Boolean)field(captured,"cancelled")&&field(ui,"current")!=captured)return true;
                if(field(ui,"current")!=captured||(Long)field(ui,"generation")!=generation)
                    throw new IllegalStateException("exit_positive_callback_captured_attempt_changed");
                return false;
            }
        },SystemClock::elapsedRealtime,Thread::sleep);
        if(!exited)throw new IllegalStateException("exit_captured_attempt_not_cancelled");
        report.put(stage+"_exit_dialog_shown",true).put(stage+"_exit_repeated_back_same_dialog",true)
            .put(stage+"_exit_continue_preserved_attempt",true).put(stage+"_exit_continue_media_progress",progress)
            .put(stage+"_exit_positive_button_clicked",true).put(stage+"_exit_captured_attempt_cancelled",true)
            .put(stage+"_exit_used_actual_UI_buttons",true).put(stage+"_exit_UI_callbacks_observed",true);
    }
    private boolean ownsAttempt(Object ui,Object owned)throws Exception{
        if(owned==null)return false;
        synchronized(field(ui,"lock")){return field(ui,"current")==owned||field(ui,"retiring")==owned;}
    }
    /** Observe one captured Attempt. No inputs, accessibility session, SF sampler,
     * marker barrier or reconnect; counts are not presented/unique-content FPS. */
    private void observeNativeWindow(MainActivity target,Object owned,long clickNs,JSONObject report)throws Exception{
        Object ui=target.lanUdpEntry;final Object[] receiver={null},surface={null};
        final long[] uiGeneration={0};final int[] activityGeneration={0},descriptorSeconds={0};
        final Throwable[] failure={null};
        runOnMainSync(()->{try{synchronized(field(ui,"lock")){
            if(owned==null||field(ui,"current")!=owned||(Boolean)field(owned,"cancelled")
                    ||(Boolean)field(owned,"stopped")||!target.running)
                throw new IllegalStateException("native_window_attempt_changed");
            receiver[0]=field(owned,"receiver");if(receiver[0]==null)throw new IllegalStateException("native_window_attempt_changed");
            Object session=field(receiver[0],"appSession");
            descriptorSeconds[0]=(Integer)field(session,"seconds");
            if(!Boolean.TRUE.equals(field(session,"diagnosticEvents"))
                    ||!Boolean.TRUE.equals(field(receiver[0],"diagnosticEvents")))
                throw new IllegalStateException("native_window_events_unavailable");
            uiGeneration[0]=(Long)field(ui,"generation");activityGeneration[0]=target.generation;
            surface[0]=target.screen.getHolder().getSurface();
            if(surface[0]==null||!((android.view.Surface)surface[0]).isValid())
                throw new IllegalStateException("native_window_attempt_changed");
        }}catch(Throwable problem){failure[0]=problem;}});
        if(failure[0]!=null)throw new IllegalStateException("native_window_qualification",failure[0]);
        long started=System.nanoTime(),leaseEnd=nativeWindowBudgetEnd(clickNs,started,descriptorSeconds[0],nativeWindowSeconds());
        long end=started+nativeWindowSeconds()*1_000_000_000L,last=started;JSONArray samples=new JSONArray();
        while(true){
            failure[0]=null;
            runOnMainSync(()->{try{synchronized(field(ui,"lock")){
                if(field(ui,"current")!=owned||(Long)field(ui,"generation")!=uiGeneration[0]
                        ||(Boolean)field(owned,"cancelled")||(Boolean)field(owned,"stopped")
                        ||field(owned,"receiver")!=receiver[0]||target.generation!=activityGeneration[0]
                        ||!target.running||target.screen.getHolder().getSurface()!=surface[0]
                        ||!((android.view.Surface)surface[0]).isValid())
                    throw new IllegalStateException("native_window_attempt_changed");
            }}catch(Throwable problem){failure[0]=problem;}});
            if(failure[0]!=null)throw new IllegalStateException("native_window_attempt_changed",failure[0]);
            long now=System.nanoTime();
            if(now<last||now>leaseEnd-8_000_000_000L||samples.length()>=44)
                throw new IllegalStateException("native_window_coverage_unknown");
            if(samples.length()==0){started=now;end=started+nativeWindowSeconds()*1_000_000_000L;
                nativeWindowBudgetEnd(clickNs,started,descriptorSeconds[0],nativeWindowSeconds());}
            samples.put(new JSONObject().put("phone_ns",now).put("worker_received_frames",target.receivedFrames.get())
                .put("codec_callback_count",target.presentedFrames.get()));last=now;
            if(now>=end)break;Thread.sleep(250);
        }
        report.put("native_window_started_ns",started).put("native_window_finished_ns",last)
            .put("native_window_click_ns",clickNs).put("native_window_budget_end_ns",leaseEnd)
            .put("native_window_descriptor_seconds",descriptorSeconds[0]).put("native_window_close_margin_ns",8_000_000_000L)
            .put("native_window_current_attempt_observed",true).put("native_window_server_atomic_hold_verified",false)
            .put("native_window_diagnostic_events_observed",true).put("native_window_samples",samples)
            .put("native_window_is_presented_FPS",false).put("native_window_no_reconnect",true)
            .put("native_window_no_source_input",true).put("native_window_no_UiAutomation",true);
    }
    private void waitReport(File report)throws Exception{
        long deadline=SystemClock.elapsedRealtime()+20000;
        while((report.length()<1||field(field(getCurrentActivity(),"lanUdpEntry"),"retiring")!=null)&&SystemClock.elapsedRealtime()<deadline)Thread.sleep(100);
        if(report.length()<1||report.length()>65536||field(field(getCurrentActivity(),"lanUdpEntry"),"retiring")!=null)throw new IllegalStateException("report_unavailable_after_leave");
    }
    private void verifySurfaceReadback(File file,JSONObject result,String stage)throws Exception{
        if(file.length()<1||file.length()>65536)throw new IllegalStateException("surface_readback_file_bound");
        byte[] bytes=new byte[(int)file.length()];
        try(FileInputStream in=new FileInputStream(file)){
            int read=0,n;while(read<bytes.length&&(n=in.read(bytes,read,bytes.length-read))>0)read+=n;
            if(read!=bytes.length)throw new IllegalStateException("surface_readback_file_short");
        }
        JSONObject report;
        if(arguments.getString("owner_native_window","off").equals("single")){
            byte[] digest=java.security.MessageDigest.getInstance("SHA-256").digest(bytes);
            StringBuilder hex=new StringBuilder(64);for(byte part:digest)hex.append(String.format(java.util.Locale.ROOT,"%02x",part&255));
            result.put(stage+"_App_report_sha256",hex.toString());
        }
        try{report=new JSONObject(new String(bytes,StandardCharsets.UTF_8));}finally{java.util.Arrays.fill(bytes,(byte)0);}
        java.util.Map<String,Object> fields=new java.util.HashMap<>();
        for(String key:new String[]{"surface_submit_lead_ms","surface_submit_status_code","surface_submit_wait_count","surface_submit_applications"}){
            fields.put(key,report.get(key));result.put(stage+"_"+key,report.get(key));
        }
        LanUdpContract.validateSurfaceSubmissionReadback(fields,surfaceLeadMs());
        result.put(stage+"_surface_submit_execution_verified",true);
        Object enabled=report.get("stage_diagnostics_enabled");
        if(!(enabled instanceof Integer)||((Integer)enabled)!=(stageDiagnostics()?1:0)
                ||stageDiagnostics()!=report.has("decoder_stage_metrics"))throw new IllegalStateException("stage_diagnostics_readback");
        result.put(stage+"_stage_diagnostics_enabled",enabled).put(stage+"_stage_diagnostics_verified",true);
        Object startup=report.get("codec_startup_ready_enabled");
        if(!(startup instanceof Integer)||((Integer)startup)!=(codecStartup()?1:0))throw new IllegalStateException("codec_startup_readback");
        result.put(stage+"_codec_startup_ready_enabled",startup).put(stage+"_codec_startup_readback_verified",true);
        if(report.has("codec_startup_gate"))result.put(stage+"_codec_startup_gate",report.getJSONObject("codec_startup_gate"));
    }
    /** Independent test-process liveness check, outside the media window.
     * Never obtain stacks or export names; a full fixed snapshot is inconclusive.
     */
    private static void waitAudioThreadsGone(JSONObject report,String stage)throws Exception{
        ThreadGroup group=Thread.currentThread().getThreadGroup();
        int parents=0;
        while(group.getParent()!=null&&parents++<16)group=group.getParent();
        if(group.getParent()!=null)throw new IllegalStateException("audio_thread_group_bound");
        Thread[] snapshot=new Thread[1024];
        long started=SystemClock.elapsedRealtime(),deadline=started+500;
        int alive=-1,observations=0;
        do{
            java.util.Arrays.fill(snapshot,null);
            int count=group.enumerate(snapshot,true);observations++;
            if(count>=snapshot.length){
                report.put(stage+"_audio_threads_alive",-1).put(stage+"_audio_thread_snapshot_bound_hit",1);
                throw new IllegalStateException("audio_thread_snapshot_bound");
            }
            alive=0;
            for(int i=0;i<count;i++){
                Thread thread=snapshot[i];if(thread==null||!thread.isAlive())continue;
                String name=thread.getName();
                if(name.equals("udp-audio-input")||name.equals("udp-audio-output")||name.equals("udp-audio-pcm"))alive++;
            }
            report.put(stage+"_audio_threads_alive",alive)
                .put(stage+"_audio_thread_observations",observations)
                .put(stage+"_audio_thread_observation_ms",SystemClock.elapsedRealtime()-started);
            if(alive==0)return;
            long remaining=deadline-SystemClock.elapsedRealtime();
            if(remaining<=0)break;
            Thread.sleep(Math.min(20,remaining));
        }while(SystemClock.elapsedRealtime()<deadline);
        report.put(stage+"_audio_thread_observation_ms",SystemClock.elapsedRealtime()-started);
        throw new IllegalStateException("audio_threads_alive_after_leave");
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
        final Object[] ownedAttempt={null};
        File credential=null;Window.Callback original=null;
        try{
            final boolean nativeWindow=ownerNativeWindow(),savedMode=savedUiCredentials();
            report.put("requested_owner_native_window",nativeWindow)
                .put("requested_owner_native_window_seconds",nativeWindow?nativeWindowSeconds():JSONObject.NULL);
            report.put("requested_credential_source",savedMode?"saved-ui":"private-file")
                .put("helper_owned_attempt_started",false);
            if(savedMode)report.put("saved_UI_private_input_touched",false).put("saved_UI_secret_exported",false);
            report.put("requested_v50_profile",v50Profile());
            report.put("requested_surface_submit_lead_ms",surfaceLeadMs());
            report.put("requested_stage_diagnostics_enabled",stageDiagnostics());
            report.put("requested_codec_startup_ready_enabled",codecStartup());
            report.put("requested_steady_seconds",nativeWindow?JSONObject.NULL:steadySeconds());
            final boolean sourceWindow=sourceInput();report.put("requested_authenticated_source_input",sourceWindow);
            report.put("source_pause_only_recovery",sourcePauseOnly());
            report.put("requested_network_scope",networkScope()).put("requested_node",node())
                .put("requested_scope_index",scopeIndex()).put("physical_FPS_acceptance",false);
            if(!nativeWindow){bench(true,10000);bench(false,10000);JSONArray rows=new JSONArray();
            for(boolean enabled:new boolean[]{false,true,true,false})rows.put(new JSONObject()
                .put("diagnostics_enabled",enabled).put("iterations",100000).put("elapsed_ns",bench(enabled,100000)));
            report.put("audio_diagnostics_ART_microbench",rows).put("microbench_scope","Numeric histogram operations only; not real codec, concurrent snapshot or PCM scheduling overhead");}
            final JSONObject login;
            if(savedMode)login=null;
            else{
                credential=new File(getTargetContext().getFilesDir(),"udp-test-login.json");
                if(credential.length()<1||credential.length()>4096)throw new IllegalStateException("private_login_input_bound");
                byte[] bytes=new byte[(int)credential.length()];
                try(FileInputStream in=new FileInputStream(credential)){if(in.read(bytes)!=bytes.length)throw new IllegalStateException("input_read");}
                login=new JSONObject(new String(bytes,StandardCharsets.UTF_8));java.util.Arrays.fill(bytes,(byte)0);
                if(!credential.delete())throw new IllegalStateException("input_cleanup");
                if(networkScope().equals("nps_owner")&&!authorizedTrialAccount(login.getString("username")))throw new IllegalStateException("nps_owner_account_required");
            }
            a=(MainActivity)startActivitySync(new Intent().setClassName(getTargetContext().getPackageName(),"local.remoteandroid.direct.MainActivity").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            waitForIdleSync();
            Object initialUi=a.lanUdpEntry;
            synchronized(field(initialUi,"lock")){if(field(initialUi,"current")!=null||field(initialUi,"retiring")!=null)throw new IllegalStateException("existing_UI_attempt_busy");}
            report.put("requested_credential_save_acceptance",credentialSave());
            if(credentialSave())a=credentialUiAcceptance(a,login.getString("username"),login.getString("password"),report);
            MainActivity target=a;testActivity=a;
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
            final Throwable[] problem={null};final long[] ownedClickNs={0};int oldGeneration=target.generation;
            if(savedMode)prepareSavedUi(target,report,"first");
            runOnMainSync(()->{try{
                assertIdleUi(target.lanUdpEntry);
                if(!savedMode)prepareUi(target,login.getString("username"),login.getString("password"));
                else{String validation=savedUiValidation(target.lanUdpEntry);if(!validation.isEmpty())throw new IllegalStateException(validation);}
                android.view.ViewGroup decor=(android.view.ViewGroup)target.getWindow().getDecorView();
                if(nativeWindow)ownedClickNs[0]=System.nanoTime();
                if(!clickStart(decor))throw new IllegalStateException("normal_UI_start_button_missing");
                ownedAttempt[0]=field(target.lanUdpEntry,"current");
            }catch(Throwable e){problem[0]=e;}});
            final String username=savedMode?null:login.getString("username"),password=savedMode?null:login.getString("password");
            if(login!=null){login.remove("password");login.remove("username");}
            report.put("helper_owned_attempt_started",ownedAttempt[0]!=null);
            throwUiStartFailure(problem[0],"normal_UI_start");
            long deadline=SystemClock.elapsedRealtime()+(nativeWindow?8000:25000);
            while((target.generation<=oldGeneration||target.receivedFrames.get()<15||target.presentedFrames.get()<10)&&SystemClock.elapsedRealtime()<deadline)Thread.sleep(100);
            if(target.generation<=oldGeneration||target.receivedFrames.get()<15||target.presentedFrames.get()<10){
                try{report.put("connection_failure_code",(Integer)field(target.lanUdpEntry,"lastConnectionFailureCode"));}catch(NoSuchFieldException olderCandidate){}
                throw new IllegalStateException("no_authenticated_media");
            }
            report.put("normal_UI_login_received_media",true);verifyNetworkReadback(target,report,"first");
            if(!nativeWindow)Thread.sleep(3000);
            if(sourceWindow)sourceInputPhase(target,ownedAttempt[0],1,report);
            if(nativeWindow||sourcePauseOnly()||sourceFrameOnly()){
                if(nativeWindow)observeNativeWindow(target,ownedAttempt[0],ownedClickNs[0],report);
                // Pause-only needs the independently observed transition. The
                // separate frame-only mode performs no input or transition.
                // Both leave without a steady marker or reconnect.
                if(!nativeWindow)report.put("source_recovery_no_steady_window",true).put("source_recovery_no_reconnect",true);
                if(sourceFrameOnly())report.put("source_frame_only_observation",true)
                    .put("source_observation_input_sent",false);
                if(nativeWindow)leaveThroughConfirmation(target,report,"first",ownedAttempt[0]);
                else leaveThroughConfirmation(target,report,"first");
                File recoveryReport=new File(getTargetContext().getFilesDir(),"udp-app-last-report.json");
                waitReport(recoveryReport);
                if(nativeWindow){Object ui=target.lanUdpEntry;synchronized(field(ui,"lock")){
                    if(field(ui,"current")!=null||field(ui,"retiring")!=null
                            ||!(Boolean)field(ownedAttempt[0],"cancelled")
                            ||(Long)field(ui,"generation")!=(Long)field(ownedAttempt[0],"generation")+1)
                        throw new IllegalStateException("native_window_completed_attempt_changed");
                    // No later Attempt may enter its start lock while this bounded
                    // completed-report byte digest/readback is associated.
                    verifySurfaceReadback(recoveryReport,report,"first");
                }}else verifySurfaceReadback(recoveryReport,report,"first");
                waitAudioThreadsGone(report,"first_leave");
                report.put("left_through_App_back",true).put("activity_running_after_leave",target.running);
            }else{
            long steadyStart=System.nanoTime();report.put("steady_media_started_ns",steadyStart);
            try(FileOutputStream out=new FileOutputStream(new File(getTargetContext().getFilesDir(),"udp-ui-phase-steady-media"))){out.write(1);}
            waitSteady(target,report,steadyStart);
            long steadyEnd=System.nanoTime();report.put("steady_media_finished_ns",steadyEnd)
                .put("steady_media_wait_ms",(steadyEnd-steadyStart)/1e6)
                .put("steady_sampler_completion_observed",true);
            if(sourceWindow)sourceInputPhase(target,ownedAttempt[0],2,report);
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
            leaveThroughConfirmation(target,report,"first");
            File first=new File(getTargetContext().getFilesDir(),"udp-app-last-report.json");waitReport(first);
            verifySurfaceReadback(first,report,"first");
            waitAudioThreadsGone(report,"first_leave");
            report.put("left_through_App_back",true).put("activity_running_after_leave",target.running)
                .put("actual_optical_latency_measured",false).put("actual_acoustic_sync_measured",false);
            try(FileInputStream in=new FileInputStream(first);FileOutputStream out=new FileOutputStream(new File(getTargetContext().getFilesDir(),"udp-app-first-report.json"))){
                byte[] chunk=new byte[1024];int n;while((n=in.read(chunk))!=-1)out.write(chunk,0,n);
            }
            if(!first.delete())throw new IllegalStateException("first_report_checkpoint_cleanup");
            // New HTTPS account auth, fresh session key/receiver, normal start UI.
            oldGeneration=target.generation;
            if(savedMode)prepareSavedUi(target,report,"second");
            runOnMainSync(()->{try{
                assertIdleUi(target.lanUdpEntry);
                if(!savedMode)prepareUi(target,username,password);
                else{String validation=savedUiValidation(target.lanUdpEntry);if(!validation.isEmpty())throw new IllegalStateException(validation);}
                if(!clickStart(target.getWindow().getDecorView()))throw new IllegalStateException("reconnect_UI_button_missing");
                ownedAttempt[0]=field(target.lanUdpEntry,"current");
            }catch(Throwable e){problem[0]=e;}});
            throwUiStartFailure(problem[0],"normal_UI_reconnect");
            deadline=SystemClock.elapsedRealtime()+25000;
            while((target.generation<=oldGeneration||target.receivedFrames.get()<15||target.presentedFrames.get()<10)&&SystemClock.elapsedRealtime()<deadline)Thread.sleep(100);
            if(target.generation<=oldGeneration||target.receivedFrames.get()<15||target.presentedFrames.get()<10)throw new IllegalStateException("reconnect_no_authenticated_media");
            Thread.sleep(2500);report.put("normal_UI_reconnected_received_media",true);verifyNetworkReadback(target,report,"second");
            if(!mediaOnly()){pointers(target,2,false,false);Thread.sleep(400);}
            leaveThroughConfirmation(target,report,"second");waitReport(first);
            verifySurfaceReadback(first,report,"second");
            waitAudioThreadsGone(report,"second_leave");
            report.put("disconnect_with_two_contacts_still_down",!mediaOnly()).put("running_after_second_leave",target.running);
            }
        }catch(Throwable failure){try{report.put("failure_class",failure.getClass().getSimpleName());if(failure.getMessage()!=null&&failure.getMessage().matches("[a-zA-Z_]+"))report.put("bounded_failure_label",failure.getMessage());if(failure.getCause()!=null)report.put("failure_cause_class",failure.getCause().getClass().getSimpleName());}catch(Exception ignored){}}
        finally{for(File marker:sourceMarkers)if(marker.exists()&&!marker.delete())try{
                report.put("source_owned_marker_cleanup_failed",true);
                if(!report.has("failure_class"))report.put("failure_class","IllegalStateException").put("bounded_failure_label","source_owned_marker_cleanup");
            }catch(Exception ignored){}
            if(credential!=null)credential.delete();if(a!=null){MainActivity target=a;Window.Callback restore=original;runOnMainSync(()->{if(restore!=null)target.getWindow().setCallback(restore);
            if(target.lanUdpEntry!=null)try{
                if(arguments.getString("owner_native_window","off").equals("single")){
                    // current-only under its own lock: a retiring old owner must
                    // never turn this cleanup into cancelling a newer Attempt.
                    synchronized(field(target.lanUdpEntry,"lock")){
                        if(ownedAttempt[0]!=null&&field(target.lanUdpEntry,"current")==ownedAttempt[0])
                            target.lanUdpEntry.cancel(true);
                    }
                }else if(ownsAttempt(target.lanUdpEntry,ownedAttempt[0]))target.lanUdpEntry.cancel(true);
            }catch(Exception ignored){}
        });}}
        result.putString("numeric_result",report.toString());finish(report.has("failure_class")?Activity.RESULT_CANCELED:Activity.RESULT_OK,result);
    }
    private static boolean clickLabel(android.view.View view,String text){
        if(view instanceof Button&&((Button)view).getText().toString().equals(text)){view.performClick();return true;}
        if(view instanceof android.view.ViewGroup){android.view.ViewGroup group=(android.view.ViewGroup)view;for(int i=0;i<group.getChildCount();i++)if(clickLabel(group.getChildAt(i),text))return true;}
        return false;
    }
    private static boolean clickStart(android.view.View view){
        if(view instanceof Button&&((Button)view).getText().toString().equals("启动认证 UDP 测试")){view.performClick();return true;}
        if(view instanceof android.view.ViewGroup){android.view.ViewGroup group=(android.view.ViewGroup)view;for(int i=0;i<group.getChildCount();i++)if(clickStart(group.getChildAt(i)))return true;}
        return false;
    }
}
