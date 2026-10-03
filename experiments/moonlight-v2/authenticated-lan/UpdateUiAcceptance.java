package local.remoteandroid.direct;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Intent;
import android.os.Bundle;
import android.os.SystemClock;
import android.view.View;
import android.view.ViewGroup;
import android.view.accessibility.AccessibilityNodeInfo;
import android.widget.Button;
import java.io.File;
import java.io.FileOutputStream;
import java.lang.reflect.Field;
import org.json.JSONObject;

/** Read only gate state; clicks update UI only. Never inspects login fields or media. */
public final class UpdateUiAcceptance extends Instrumentation {
    private static final String REPORT="udp-update-ui-check.json";
    private static final String TEST_LABEL="测试版 · UDP 机主试用";
    public void onCreate(Bundle args){super.onCreate(args);start();}
    private static Object field(Object object,String name)throws Exception{
        Class<?> owner=object instanceof Class?(Class<?>)object:object.getClass();
        Field f=owner.getDeclaredField(name);f.setAccessible(true);return f.get(object instanceof Class?null:object);
    }
    private static Button findButton(View view,String label){
        if(view instanceof Button&&label.contentEquals(((Button)view).getText()))return (Button)view;
        if(view instanceof ViewGroup){ViewGroup group=(ViewGroup)view;
            for(int i=0;i<group.getChildCount();i++){Button found=findButton(group.getChildAt(i),label);if(found!=null)return found;}}
        return null;
    }
    private static boolean privateInput(AccessibilityNodeInfo node){
        CharSequence name=node.getClassName();return node.isPassword()||name!=null&&name.toString().contains("EditText");
    }
    private static AccessibilityNodeInfo findLabel(AccessibilityNodeInfo node,String label){
        if(node==null)return null;
        if(!privateInput(node)){CharSequence text=node.getText();if(text!=null&&label.contentEquals(text))return node;}
        for(int i=0;i<node.getChildCount();i++){AccessibilityNodeInfo child=node.getChild(i);
            AccessibilityNodeInfo found=findLabel(child,label);if(found!=null)return found;}
        return null;
    }
    private boolean clickAccessible(String label){
        AccessibilityNodeInfo node=findLabel(getUiAutomation().getRootInActiveWindow(),label);
        if(node==null)return false;
        for(int depth=0;depth<4&&node!=null;depth++,node=node.getParent())
            if(node.isClickable()&&node.performAction(AccessibilityNodeInfo.ACTION_CLICK))return true;
        return false;
    }
    /** Called only after a modal update dialog appears; exclude every input node. */
    private static void modalText(AccessibilityNodeInfo node,StringBuilder output){
        if(node==null||output.length()>32768)return;
        if(!privateInput(node)){CharSequence text=node.getText();if(text!=null)output.append(text).append('\n');}
        for(int i=0;i<node.getChildCount();i++)modalText(node.getChild(i),output);
    }
    private static void write(File file,JSONObject value)throws Exception{
        byte[] bytes=value.toString().getBytes("UTF-8");if(bytes.length>4096)throw new IllegalStateException("report_bound");
        try(FileOutputStream out=new FileOutputStream(file)){out.write(bytes);out.flush();}
    }
    public void onStart(){
        JSONObject report=new JSONObject();Bundle result=new Bundle();MainActivity target=null;boolean success=false;
        try{
            long code=getTargetContext().getPackageManager().getPackageInfo(getTargetContext().getPackageName(),0).getLongVersionCode();
            report.put("installed_version39",code==39).put("no_credentials_read",true).put("no_media_requested",true);
            if(code!=39)throw new IllegalStateException("target_version_mismatch");
            target=(MainActivity)startActivitySync(new Intent().setClassName(getTargetContext().getPackageName(),"local.remoteandroid.direct.MainActivity").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            waitForIdleSync();MainActivity activity=target;final Throwable[] problem={null};final boolean[] clicked={false};
            runOnMainSync(()->{try{
                Object ui=activity.lanUdpEntry;if(ui==null)throw new IllegalStateException("UDP_UI_missing");
                Object lock=field(ui,"lock");boolean current,retiring;long revision;
                synchronized(lock){current=field(ui,"current")!=null;retiring=field(ui,"retiring")!=null;revision=(Long)field(ui,"loginRevision");}
                Object gate=field(AppUpdater.class,"OPERATIONS");
                int phase=(Integer)gate.getClass().getMethod("phase").invoke(gate);
                boolean active=activity.lanUdpEntry.active(),busy=activity.updater.isBusy();
                report.put("active_before",active).put("current_before",current).put("retiring_before",retiring)
                    .put("updater_busy_before",busy).put("updater_gate_idle_before",phase==0).put("updater_gate_phase_before",phase)
                    .put("login_revision_present",revision>0).put("login_revision",revision);
                if(active||current||retiring||busy)throw new IllegalStateException("non_idle_UI_skip_update");
                Button update=findButton(activity.getWindow().getDecorView(),"检查更新");
                report.put("update_button_present",update!=null);if(update==null)throw new IllegalStateException("update_button_missing");
                clicked[0]=update.performClick();report.put("actual_update_perform_click",clicked[0]);
            }catch(Throwable e){problem[0]=e;}});
            if(problem[0]!=null)throw new IllegalStateException("update_UI_click_failed",problem[0]);
            if(!clicked[0])throw new IllegalStateException("update_UI_click_rejected");
            long deadline=SystemClock.elapsedRealtime()+8000;boolean menuClicked=false;
            while(SystemClock.elapsedRealtime()<deadline){if(clickAccessible(TEST_LABEL)){menuClicked=true;break;}SystemClock.sleep(100);}
            report.put("experimental_menu_action_clicked",menuClicked);if(!menuClicked)throw new IllegalStateException("experimental_menu_missing");
            deadline=SystemClock.elapsedRealtime()+30000;boolean appeared=false;String content="";
            while(SystemClock.elapsedRealtime()<deadline){
                AccessibilityNodeInfo root=getUiAutomation().getRootInActiveWindow();
                if(findLabel(root,"知道了")!=null||findLabel(root,"确认更新")!=null){StringBuilder text=new StringBuilder();modalText(root,text);content=text.toString();
                    if(content.contains("更新内容")&&content.contains("服务器版本")){appeared=true;break;}}
                SystemClock.sleep(150);
            }
            report.put("manifest_update_dialog_visible",appeared).put("dialog_version39",content.contains("版本码 39")||content.contains("版本码39"))
                .put("server_alpha8_visible",content.contains("服务器版本：v1.31-alpha.8"))
                .put("changelog_visible",content.contains("更新内容"))
                .put("hour_limit_changelog_visible",content.contains("1 小时")||content.contains("1小时")||content.contains("一小时")||content.contains("3600"))
                .put("current_latest_visible",content.contains("当前应用无需更新。"));
            if(!appeared)throw new IllegalStateException("manifest_dialog_missing");
            boolean closed=clickAccessible("知道了");report.put("dialog_closed_with_ack",closed);
            // Never click confirm/download/install; cancel only if server unexpectedly offers an upgrade.
            if(!closed){report.put("unexpected_upgrade_cancelled",clickAccessible("取消更新"));throw new IllegalStateException("expected_current_dialog_missing");}
            success=report.optBoolean("installed_version39")&&report.optBoolean("dialog_version39")&&report.optBoolean("server_alpha8_visible")
                &&report.optBoolean("changelog_visible")&&report.optBoolean("hour_limit_changelog_visible")&&report.optBoolean("current_latest_visible");
        }catch(Throwable failure){try{report.put("failure_class",failure.getClass().getSimpleName());
            Throwable cause=failure.getCause();if(cause!=null)report.put("failure_cause_class",cause.getClass().getSimpleName());}catch(Exception ignored){}
            // Whitelisted dismissal only; no Back that could touch a remote Android session.
            try{clickAccessible("取消");clickAccessible("知道了");clickAccessible("取消更新");}catch(Exception ignored){}
        }finally{
            try{report.put("acceptance_passed",success);write(new File(getTargetContext().getFilesDir(),REPORT),report);
                result.putString("report_file",REPORT);result.putString("numeric_boolean_report",report.toString());}
            catch(Exception failure){result.putString("report_write_failure",failure.getClass().getSimpleName());}
        }
        finish(success?Activity.RESULT_OK:Activity.RESULT_CANCELED,result);
    }
}
