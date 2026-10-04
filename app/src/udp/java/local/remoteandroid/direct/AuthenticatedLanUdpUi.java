package local.remoteandroid.direct;

import android.content.SharedPreferences;
import android.app.AlertDialog;
import android.os.Looper;
import android.text.Editable;
import android.text.TextWatcher;
import android.util.AtomicFile;
import android.view.Gravity;
import android.widget.*;
import org.json.JSONObject;
import java.io.*;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Arrays;
import javax.net.ssl.SSLSocket;

/** Isolated, opt-in LAN, registered Tailnet or fixed owner-only NPS trial entry.
 * HTTPS carries authentication/description only;
 * AES-GCM UDP carries video, AAC and Android native touch. No media fallback. */
public final class AuthenticatedLanUdpUi implements LanUdpEntry {
    private final MainActivity activity;
    private final PasswordStore passwordStore;
    private final Object lock=new Object();
    private long generation,loginRevision;
    private volatile int lastConnectionFailureCode;
    private Attempt current,retiring;
    private final UdpExitConfirmationGate exitGate=new UdpExitConfirmationGate();
    private AlertDialog exitDialog;
    private UdpExitConfirmationGate.Token exitToken;
    private EditText address,user,password;
    private TextView status;
    private Spinner scope,fps,quality,rate,buffer;
    private CheckBox sound,pcmQueue,codecStartup;
    private static final String SETTINGS="authenticated_udp_candidate";
    private static final String DEFAULT_LAN_ADDRESS="192.168.9.128:"+LanUdpContract.HTTPS_PORT;
    private String lastLanAddress;
    private boolean restoringFields;
    // Owner instrumentation only: no public widget, Intent extra or saved setting.
    // showLogin resets this one-attempt value; helper must explicitly opt in again.
    private int ownerSurfaceSubmitLeadMs;
    private boolean ownerStageDiagnosticsEnabled=true;
    private static final class Attempt {
        final long generation;final String endpoint,credential,networkScope,node;
        final NpsPhysicalNetwork physicalNetwork;
        final boolean boundedPcmQueueEnabled,stageDiagnosticsEnabled,codecStartupReadyEnabled;final int surfaceSubmitLeadMs;
        volatile boolean cancelled;volatile Socket https;volatile UdpVideoProbe receiver;
        volatile String sessionId;volatile boolean stopped;
        Attempt(long generation,String endpoint,String credential,String scope,String node,NpsPhysicalNetwork physicalNetwork,boolean pcmQueue,int surfaceLeadMs,boolean stages,boolean startup){this.generation=generation;this.endpoint=endpoint;this.credential=credential;networkScope=scope;this.node=node;this.physicalNetwork=physicalNetwork;boundedPcmQueueEnabled=pcmQueue;surfaceSubmitLeadMs=surfaceLeadMs;stageDiagnosticsEnabled=stages;codecStartupReadyEnabled=startup;}
    }
    public AuthenticatedLanUdpUi(MainActivity activity){
        if(!BuildConfig.AUTHENTICATED_LAN_UDP||!BuildConfig.APPLICATION_ID.equals("local.remoteandroid.direct.experiment"))throw new IllegalStateException("isolated build required");
        this.activity=activity;passwordStore=new PasswordStore(activity);
    }
    @Override public boolean active(){synchronized(lock){return current!=null&&!current.stopped;}}
    @Override public void showLogin(){
        if(Looper.myLooper()!=Looper.getMainLooper()){activity.ui.post(this::showLogin);return;}
        if(active())return;
        dismissExitConfirmation();
        final long pageRevision=++loginRevision;
        ownerSurfaceSubmitLeadMs=0;
        SharedPreferences saved=activity.getSharedPreferences(SETTINGS,0);
        boolean lowLoad=UdpDeviceCapabilities.lowLoadDefault();
        ownerStageDiagnosticsEnabled=!savedLowLoad(saved,lowLoad);
        // Preserve explicitly saved selections. New installs use M1; M5 remains
        // selectable as the user's backup/test host.
        final int savedScope=savedSelection(saved,"scope",3,2);
        lastLanAddress=savedAddress(saved,"lan_address",LanUdpContract.LAN_SCOPE,DEFAULT_LAN_ADDRESS);
        String restoredAddress=savedScope>=2?publicAddress(savedScope):savedAddress(saved,"address",selectedScope(savedScope),
            savedScope==0?lastLanAddress:LanUdpContract.TAILNET_HOST+":"+LanUdpContract.HTTPS_PORT);
        LinearLayout box=new LinearLayout(activity);box.setOrientation(LinearLayout.VERTICAL);box.setPadding(32,32,32,32);
        box.addView(loginHeader(pageRevision));
        TextView summary=new TextView(activity);summary.setText("公网 M1/M5 认证 UDP · 机主体验（公网单次 1 小时，届时提醒休息）\n可选局域网或 Tailnet；断线不会改用 TCP 媒体");box.addView(summary);
        TextView installed=new TextView(activity);installed.setText("已安装版本 v"+BuildConfig.VERSION_NAME+" · 版本码 "+BuildConfig.VERSION_CODE+"\n更新通道：实验版（独立于正式版）");box.addView(installed);
        scope=choice(box,"连接范围（请手动选择）",new String[]{"物理局域网 · 手填 M1 IP","Tailnet · M1 100.65.0.2", "公网 UDP · M1 · 默认", "公网 UDP · M5 · 备用测试"},savedScope);
        address=field(box,"HTTPS 控制地址；公网节点使用固定地址",restoredAddress);address.setEnabled(savedScope<2);
        user=field(box,"现有安卓账号",savedText(saved,usernamePreference(savedScope),defaultUsername(savedScope),128));
        password=field(box,"现有账号密码（可在本机加密保存）","");password.setInputType(129);
        LinearLayout passwordActions=new LinearLayout(activity);Button remember=new Button(activity);remember.setText("保存密码");passwordActions.addView(remember,new LinearLayout.LayoutParams(0,-2,1));Button forget=new Button(activity);forget.setText("清除已保存密码");passwordActions.addView(forget,new LinearLayout.LayoutParams(0,-2,1));box.addView(passwordActions);
        remember.setOnClickListener(v->savePassword());forget.setOnClickListener(v->{try{passwordStore.clear();password.setText("");status.setText("已清除本机保存的密码。");}catch(Exception failure){status.setText("清除失败，请重试。");}});
        quality=choice(box,"串流清晰度",new String[]{"540P · 540×960 · 流畅","720P · 720×1280 · 高清","1080P · 1080×1920 · 清晰"},savedSelection(saved,"quality",2,lowLoad?0:2));
        rate=choice(box,"视频 VBR 目标码率",new String[]{"4 Mbps","8 Mbps","12 Mbps","16 Mbps","24 Mbps"},savedSelection(saved,"rate",4,lowLoad?0:2));
        fps=choice(box,"串流上限（不代表实际内容帧率）",new String[]{"30 FPS · 稳定优先","60 FPS"},savedFpsSelection(saved));
        buffer=choice(box,"播放缓冲",new String[]{"30 ms","50 ms","80 ms · 推荐","100 ms"},savedSelection(saved,"buffer",3,2));
        Button optimize=new Button(activity);optimize.setText("真我 V50 · 一键均衡优化");box.addView(optimize);
        Button capabilities=new Button(activity);capabilities.setText("查看本机硬解能力");box.addView(capabilities);
        sound=new CheckBox(activity);sound.setText("UDP 音频");sound.setChecked(savedSound(saved));box.addView(sound);
        pcmQueue=new CheckBox(activity);pcmQueue.setText("实验：有界 PCM 输出队列（默认关闭）");pcmQueue.setChecked(false);box.addView(pcmQueue);
        codecStartup=new CheckBox(activity);codecStartup.setText("实验：解码器准备后接收新关键帧（默认关闭）");codecStartup.setChecked(false);box.addView(codecStartup);
        TextView remembered=new TextView(activity);remembered.setText("连接地址、账号及串流参数自动记住；点击保存密码才会加密保存一组服务器和账号。本次实验开关不会保存。");box.addView(remembered);
        Button start=new Button(activity);start.setText("启动认证 UDP 测试");box.addView(start);status=new TextView(activity);box.addView(status);
        scope.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener(){
            private int previous=savedScope;
            public void onItemSelected(AdapterView<?> parent,android.view.View view,int position,long id){
                if(position==previous)return;previous=position;
                // Programmatic address changes must not save the old scope's
                // username into the newly selected public preference.
                restoringFields=true;
                try{
                    address.setEnabled(position<2);
                    address.setText(position>=2?publicAddress(position):position==1?LanUdpContract.TAILNET_HOST+":"+LanUdpContract.HTTPS_PORT:lastLanAddress);
                    user.setText(savedText(activity.getSharedPreferences(SETTINGS,0),usernamePreference(position),defaultUsername(position),128));
                }finally{restoringFields=false;}
                saveSettings();restorePassword();
            }
            public void onNothingSelected(AdapterView<?> parent){}
        });
        for(Spinner input:new Spinner[]{quality,rate,fps,buffer})input.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener(){
            public void onItemSelected(AdapterView<?> parent,android.view.View view,int position,long id){saveSettings();}
            public void onNothingSelected(AdapterView<?> parent){}
        });
        TextWatcher watcher=new TextWatcher(){
            public void beforeTextChanged(CharSequence value,int start,int count,int after){}
            public void onTextChanged(CharSequence value,int start,int before,int count){if(restoringFields)return;saveSettings();restorePassword();}
            public void afterTextChanged(Editable value){}
        };
        address.addTextChangedListener(watcher);user.addTextChangedListener(watcher);
        sound.setOnCheckedChangeListener((button,checked)->saveSettings());
        optimize.setOnClickListener(v->{
            restoringFields=true;
            try{quality.setSelection(0);rate.setSelection(0);fps.setSelection(0);buffer.setSelection(2);
                pcmQueue.setChecked(false);codecStartup.setChecked(false);ownerStageDiagnosticsEnabled=false;
            }finally{restoringFields=false;}
            activity.getSharedPreferences(SETTINGS,0).edit().putBoolean("low_load_profile",true).apply();saveSettings();
            status.setText("已应用 V50 均衡起点：540P · 4 Mbps VBR · 30 FPS 上限 · 80 ms。硬解优先；参数可修改并保留。实际效果需火锅真机验证。");
        });
        capabilities.setOnClickListener(v->{capabilities.setEnabled(false);new Thread(()->{
            String result=UdpDeviceCapabilities.summary();activity.ui.post(()->{
                if(activity.isFinishing()||activity.isDestroyed()||active()||loginRevision!=pageRevision)return;
                capabilities.setEnabled(true);
                new AlertDialog.Builder(activity).setTitle("本机解码能力").setMessage(result).setPositiveButton("知道了",null).show();});
        },"udp-codec-capabilities").start();});
        start.setOnClickListener(v->start());
        restorePassword();ScrollView scroll=new ScrollView(activity);scroll.addView(box);
        // API37 edge-to-edge: reserve system bars only on the login scroll root.
        // The existing inner spacing and remote video/touch coordinates are unchanged.
        scroll.setOnApplyWindowInsetsListener((view,insets)->{
            android.graphics.Insets bars=insets.getInsets(android.view.WindowInsets.Type.systemBars());
            view.setPadding(bars.left,bars.top,bars.right,bars.bottom);return insets;
        });
        activity.setContentView(scroll);scroll.requestApplyInsets();
    }
    /** Login-only compact action; title wraps before the update button shrinks. */
    private LinearLayout loginHeader(final long pageRevision){
        LinearLayout header=new LinearLayout(activity);header.setOrientation(LinearLayout.HORIZONTAL);header.setGravity(Gravity.CENTER_VERTICAL);
        TextView title=new TextView(activity);title.setText("给火锅的安卓 · 测试版");title.setTextSize(18);
        header.addView(title,new LinearLayout.LayoutParams(0,-2,1));
        Button update=new Button(activity);update.setText("检查更新");update.setTextSize(14);update.setAllCaps(false);
        float density=activity.getResources().getDisplayMetrics().density;
        update.setMinWidth(0);update.setMinimumWidth(0);update.setMinHeight(Math.round(48*density));update.setMinimumHeight(Math.round(48*density));
        update.setPadding(Math.round(12*density),0,Math.round(12*density),0);
        update.setOnClickListener(v->{
            // A retained view or queued click cannot begin installation after
            // this login page was replaced by another page or a media session.
            if(activity.isFinishing()||activity.isDestroyed()||active()||loginRevision!=pageRevision)return;
            activity.updater.check(true);
        });
        header.addView(update,new LinearLayout.LayoutParams(-2,-2));return header;
    }
    private EditText field(LinearLayout box,String hint,String value){EditText input=new EditText(activity);input.setSingleLine();input.setHint(hint);input.setText(value);box.addView(input);return input;}
    private Spinner choice(LinearLayout box,String label,String[] values,int selected){TextView text=new TextView(activity);text.setText(label);box.addView(text);Spinner input=new Spinner(activity);input.setAdapter(new ArrayAdapter<>(activity,android.R.layout.simple_spinner_dropdown_item,values));input.setSelection(selected);box.addView(input);return input;}
    private static int savedSelection(SharedPreferences saved,String key,int max,int fallback){try{int value=saved.getInt(key,fallback);return value>=0&&value<=max?value:fallback;}catch(ClassCastException invalid){return fallback;}}
    private static String savedText(SharedPreferences saved,String key,String fallback,int max){try{String value=saved.getString(key,fallback);return value!=null&&!value.isEmpty()&&value.length()<=max?value:fallback;}catch(ClassCastException invalid){return fallback;}}
    private static int savedFpsSelection(SharedPreferences saved){
        Integer value=null,legacy=null;
        try{if(saved.contains("fps_value"))value=saved.getInt("fps_value",30);}catch(ClassCastException invalid){}
        try{if(saved.contains("fps"))legacy=saved.getInt("fps",-1);}catch(ClassCastException invalid){}
        return UdpLowLoadProfile.indexForSaved(value,legacy);
    }
    private static boolean savedSound(SharedPreferences saved){try{return saved.getBoolean("sound",true);}catch(ClassCastException invalid){return true;}}
    private static boolean savedLowLoad(SharedPreferences saved,boolean fallback){try{return saved.getBoolean("low_load_profile",fallback);}catch(ClassCastException invalid){return fallback;}}
    private static String selectedScope(int selection){return selection>=2?LanUdpContract.NPS_SCOPE:selection==1?LanUdpContract.TAILNET_SCOPE:LanUdpContract.LAN_SCOPE;}
    private static String selectedNode(int selection){return selection==2?LanUdpContract.M1_NODE:selection==3?LanUdpContract.M5_NODE:"";}
    private static String usernamePreference(int selection){return selection>=2?"nps_username":"username";}
    private static String defaultUsername(int selection){return "huoguo";}
    private static String publicAddress(int selection){
        if(selection==2)return LanUdpContract.NPS_HOST+":"+LanUdpContract.NPS_M1_HTTPS_PORT;
        if(selection==3)return LanUdpContract.NPS_HOST+":"+LanUdpContract.NPS_M5_HTTPS_PORT;
        throw new IllegalArgumentException("public_selection_invalid");
    }
    private static String savedAddress(SharedPreferences saved,String key,String scope,String fallback){
        try{String value=savedText(saved,key,fallback,256);Endpoint.Address parsed=Endpoint.parse(Endpoint.destination(value));
            LanUdpContract.validateLogin(parsed.host,parsed.port,scope);return value;}catch(Exception invalid){return fallback;}
    }
    private void saveSettings(){
        if(restoringFields)return;
        int selected=scope.getSelectedItemPosition();String endpoint=address.getText().toString();
        SharedPreferences.Editor edit=activity.getSharedPreferences(SETTINGS,0).edit()
            .putInt("scope",selected).putString("address",endpoint).putString(usernamePreference(selected),user.getText().toString())
            .putInt("quality",quality.getSelectedItemPosition()).putInt("rate",rate.getSelectedItemPosition())
            .putInt("fps_value",UdpLowLoadProfile.fpsForIndex(fps.getSelectedItemPosition())).putInt("buffer",buffer.getSelectedItemPosition()).putBoolean("sound",sound.isChecked());
        if(selected==0)try{Endpoint.Address parsed=Endpoint.parse(Endpoint.destination(endpoint));LanUdpContract.validateLogin(parsed.host,parsed.port,LanUdpContract.LAN_SCOPE);
            lastLanAddress=endpoint;edit.putString("lan_address",endpoint);}catch(Exception invalid){}
        edit.apply();
    }
    private void savePassword(){
        try{int selected=scope.getSelectedItemPosition();String destination=Endpoint.destination(address.getText().toString());Endpoint.Address parsed=Endpoint.parse(destination);
            LanUdpContract.validateLogin(parsed.host,parsed.port,selectedScope(selected),selectedNode(selected));
            String name=user.getText().toString(),secret=password.getText().toString();
            if(name.isEmpty()||secret.isEmpty()||name.indexOf(':')>=0||name.length()>128||secret.length()>1024)throw new IOException("请先填写有效账号及密码");
            passwordStore.save(Endpoint.identity(destination),name,secret);saveSettings();status.setText("密码已在本机加密保存，下次匹配此服务器和账号时自动填入。");
        }catch(Exception failure){status.setText("保存失败，请核对服务器、账号和密码后重试。");}
    }
    private void restorePassword(){
        if(restoringFields||password==null)return;
        try{String destination=Endpoint.destination(address.getText().toString());Endpoint.Address parsed=Endpoint.parse(destination);int selected=scope.getSelectedItemPosition();
            LanUdpContract.validateLogin(parsed.host,parsed.port,selectedScope(selected),selectedNode(selected));
            password.setText(passwordStore.load(Endpoint.identity(destination),user.getText().toString()));
        }catch(IllegalArgumentException invalid){password.setText("");}
        catch(Exception failure){password.setText("");if(status!=null)status.setText("已保存的密码无法读取，请重新输入并保存。");}
    }
    private void start(){
        if(activity.updater.isBusy()){status.setText("更新检查或安装正在进行，请完成或取消后再连接。");return;}
        synchronized(lock){if(retiring!=null){status.setText("上一条 UDP 会话正在收尾，请稍后重新连接。");return;}}
        lastConnectionFailureCode=0;
        final String endpoint,credential,networkScope,node;final NpsPhysicalNetwork physicalNetwork;final int requestedSurfaceLeadMs;final JSONObject request=new JSONObject();
        try{
            endpoint=Endpoint.destination(address.getText().toString());Endpoint.Address parsed=Endpoint.parse(endpoint);
            int selection=scope.getSelectedItemPosition();if(selection<0||selection>3)throw new IOException("请手动选择测试节点");
            networkScope=selectedScope(selection);node=selectedNode(selection);
            try{LanUdpContract.validateLogin(parsed.host,parsed.port,networkScope,node);}
            catch(IOException invalid){throw new IOException(networkScope.equals(LanUdpContract.NPS_SCOPE)?"公网试用只接受所选节点的固定 HTTPS 控制地址":networkScope.equals(LanUdpContract.TAILNET_SCOPE)?"Tailnet 实验仅接受 M1 100.65.0.2:"+LanUdpContract.HTTPS_PORT:"局域网范围仅接受私有 IPv4:"+LanUdpContract.HTTPS_PORT);}
            String name=user.getText().toString(),secret=password.getText().toString();
            if(name.isEmpty()||secret.isEmpty()||name.indexOf(':')>=0||name.length()>128||secret.length()>1024)throw new IOException("请填写现有账号及密码");
            credential="Basic "+android.util.Base64.encodeToString((name+":"+secret).getBytes(StandardCharsets.UTF_8),android.util.Base64.NO_WRAP);
            request.put("max_size",new int[]{960,1280,1920}[quality.getSelectedItemPosition()]);
            request.put("video_bit_rate",new int[]{4000000,8000000,12000000,16000000,24000000}[rate.getSelectedItemPosition()]);
            request.put("max_fps",UdpLowLoadProfile.fpsForIndex(fps.getSelectedItemPosition())).put("buffer_ms",new int[]{30,50,80,100}[buffer.getSelectedItemPosition()]);
            request.put("seconds",LanUdpContract.NPS_SCOPE.equals(networkScope)?3600:120).put("audio_enabled",sound.isChecked()).put("touch_enabled",true).put("network_scope",networkScope);
            if(LanUdpContract.NPS_SCOPE.equals(networkScope))request.put("node",node);
            requestedSurfaceLeadMs=ownerSurfaceSubmitLeadMs;LanUdpContract.validateOwnerSurfaceLead(requestedSurfaceLeadMs);
            request.put("surface_submit_lead_ms",requestedSurfaceLeadMs);
            activity.initTLS();
            physicalNetwork=LanUdpContract.NPS_SCOPE.equals(networkScope)?NpsPhysicalNetwork.select(activity):null;
        }catch(Exception failure){status.setText("无法启动："+failure.getMessage());return;}
        Attempt attempt;
        synchronized(lock){if(current!=null&&!current.stopped)return;attempt=new Attempt(++generation,endpoint,credential,networkScope,node,physicalNetwork,pcmQueue.isChecked(),requestedSurfaceLeadMs,ownerStageDiagnosticsEnabled,codecStartup.isChecked());current=attempt;}
        password.setText("");LinearLayout wait=new LinearLayout(activity);wait.setOrientation(LinearLayout.VERTICAL);wait.setGravity(Gravity.CENTER);
        TextView text=new TextView(activity);text.setText("正在通过受信 HTTPS 登录…\n媒体不会回退 TCP");wait.addView(text);Button cancel=new Button(activity);cancel.setText("取消连接");cancel.setOnClickListener(v->cancel(true));wait.addView(cancel);activity.setContentView(wait);
        new Thread(()->authenticate(attempt,request),"udp-session-auth").start();
    }
    private void authenticate(Attempt attempt,JSONObject request){
        try{
            JSONObject descriptor=http(attempt,"POST","/udp/session",request,true);
            String id=descriptor.optString("session","");if(!id.matches("[0-9a-f]{32}"))throw new IOException("invalid_session_id");attempt.sessionId=id;
            Endpoint.Address login=Endpoint.parse(attempt.endpoint);
            validateDescriptor(descriptor,login.host,login.port,attempt.networkScope,attempt.node,attempt.surfaceSubmitLeadMs);
            synchronized(lock){
                if(attempt.cancelled||current!=attempt||generation!=attempt.generation)throw new IOException("cancelled");
                attempt.receiver=UdpVideoProbe.startApp(activity,descriptor,attempt.boundedPcmQueueEnabled,attempt.stageDiagnosticsEnabled,attempt.codecStartupReadyEnabled,attempt.physicalNetwork,(report,failed,completion)->finished(attempt,report,failed,completion));
            }
        }catch(Exception failure){
            String failureLabel=failure.getMessage();
            if(failureLabel!=null&&failureLabel.matches("https_status_[0-9]{3}"))lastConnectionFailureCode=Integer.parseInt(failureLabel.substring(13));
            else if(failureLabel!=null&&failureLabel.startsWith("descriptor_"))lastConnectionFailureCode=1;
            else lastConnectionFailureCode=2;
            finishRemote(attempt);
            synchronized(lock){if(retiring==attempt)retiring=null;}
            activity.ui.post(()->{synchronized(lock){if(current!=attempt||generation!=attempt.generation)return;attempt.stopped=true;current=null;}
                showLogin();status.setText(attempt.cancelled?"已取消连接":connectionFailureMessage(lastConnectionFailureCode));});
        }
    }
    private static String connectionFailureMessage(int code){
        if(code==401||code==403)return "登录被拒绝（"+code+"），请核对账号、密码及节点权限。";
        if(code==409)return "此节点正在使用或维护（409），请稍后连接。";
        if(code==400)return "服务器拒绝本次串流参数（400），请检查 App 和服务版本。";
        if(code==503)return "远端 UDP 服务暂未就绪（503），请稍后连接。";
        if(code==429)return "尝试登录过于频繁（429），请稍后重试。";
        return "UDP 连接失败（"+(code==1?"会话校验":code==2?"网络或 TLS":String.valueOf(code))+"），未回退 TCP。";
    }
    static boolean privateIpv4(String host){return LanUdpContract.privateIpv4(host);}
    static void validateDescriptor(JSONObject json,String loginHost)throws Exception{
        validateDescriptor(json,loginHost,LanUdpContract.LAN_SCOPE);
    }
    static void validateDescriptor(JSONObject json,String loginHost,String expectedScope)throws Exception{
        validateDescriptor(json,loginHost,expectedScope,0);
    }
    static void validateDescriptor(JSONObject json,String loginHost,String expectedScope,int expectedLeadMs)throws Exception{
        validateDescriptor(json,loginHost,LanUdpContract.HTTPS_PORT,expectedScope,"",expectedLeadMs);
    }
    static void validateDescriptor(JSONObject json,String loginHost,int loginPort,String expectedScope,String expectedNode,int expectedLeadMs)throws Exception{
        java.util.Map<String,Object> fields=new java.util.HashMap<>();java.util.Iterator<String> keys=json.keys();
        while(keys.hasNext()){String key=keys.next();fields.put(key,json.get(key));}
        LanUdpContract.validate(fields,loginHost,loginPort,expectedScope,expectedNode,expectedLeadMs);
    }
    private JSONObject http(Attempt attempt,String method,String path,JSONObject data,boolean cancellable)throws Exception{
        if(cancellable&&attempt.cancelled)throw new IOException("cancelled");Endpoint.Address endpoint=Endpoint.parse(attempt.endpoint);
        LanUdpContract.validateLogin(endpoint.host,endpoint.port,attempt.networkScope,attempt.node);
        NpsPhysicalNetwork.validateScope(attempt.networkScope,attempt.physicalNetwork);
        Socket raw=null;SSLSocket socket=null;
        try{
            if(attempt.physicalNetwork!=null){
                raw=new Socket();attempt.physicalNetwork.bind(raw);
                if(cancellable){synchronized(lock){if(attempt.cancelled)throw new IOException("cancelled");attempt.https=raw;}}
                raw.connect(new InetSocketAddress(endpoint.host,endpoint.port),5000);
                attempt.physicalNetwork.requireUsable();
                socket=(SSLSocket)activity.tls.getSocketFactory().createSocket(raw,endpoint.host,endpoint.port,true);
            }else{
                socket=(SSLSocket)activity.tls.getSocketFactory().createSocket();
                if(cancellable){synchronized(lock){if(attempt.cancelled)throw new IOException("cancelled");attempt.https=socket;}}
                socket.connect(new InetSocketAddress(endpoint.host,endpoint.port),5000);
            }
            if(cancellable&&attempt.cancelled)throw new IOException("cancelled");
            socket.setSoTimeout(7000);socket.setTcpNoDelay(true);socket.startHandshake();
            byte[] leaf=MessageDigest.getInstance("SHA-256").digest(socket.getSession().getPeerCertificates()[0].getEncoded());boolean pinned=false;
            if(LanUdpContract.NPS_SCOPE.equals(attempt.networkScope)){
                String hex=LanUdpContract.npsCertificateSha256(attempt.node);byte[] nodePin=new byte[32];
                for(int i=0;i<nodePin.length;i++)nodePin[i]=(byte)Integer.parseInt(hex.substring(i*2,i*2+2),16);
                pinned=MessageDigest.isEqual(nodePin,leaf);
            }else for(byte[] pin:activity.fingerprints)pinned|=MessageDigest.isEqual(pin,leaf);
            if(!pinned)throw new IOException("certificate_pin");
            byte[] body=data==null?new byte[0]:data.toString().getBytes(StandardCharsets.UTF_8);
            OutputStream out=socket.getOutputStream();out.write((method+" "+path+" HTTP/1.1\r\nHost: "+attempt.endpoint+"\r\nAuthorization: "+attempt.credential+"\r\nContent-Type: application/json\r\nContent-Length: "+body.length+"\r\nConnection: close\r\n\r\n").getBytes(StandardCharsets.US_ASCII));out.write(body);out.flush();
            InputStream in=socket.getInputStream();int status=activity.header(in);if(status!=200&&!(method.equals("POST")&&status==201)&&!(method.equals("DELETE")&&status==204))throw new IOException("https_status_"+status);
            ByteArrayOutputStream bytes=new ByteArrayOutputStream();byte[] chunk=new byte[1024];int n;
            while((n=in.read(chunk))!=-1){if(bytes.size()+n>32768)throw new IOException("descriptor_bound");bytes.write(chunk,0,n);}
            if(bytes.size()==0)return new JSONObject();byte[] encoded=bytes.toByteArray();try{return new JSONObject(new String(encoded,StandardCharsets.UTF_8));}finally{Arrays.fill(encoded,(byte)0);}
        }finally{try{if(socket!=null)socket.close();}finally{try{if(raw!=null)raw.close();}finally{if(cancellable)attempt.https=null;}}}
    }
    private static boolean cleanupAllowsReconnect(UdpVideoProbe.CompletionReceipt completion){
        return completion!=null&&completion.audioCleanupConfirmed();
    }
    private static JSONObject completedReportPayload(JSONObject report,UdpVideoProbe.CompletionReceipt completion)throws Exception{
        return completion!=null&&completion.statisticsAccepted()?report
            :new JSONObject().put("completion_receipt",completion==null?new JSONObject():completion.numeric());
    }
    private void finished(Attempt attempt,JSONObject report,boolean failed,UdpVideoProbe.CompletionReceipt completion){
        boolean written=false;
        try{
            // A rejected report is never trimmed or presented as valid stats.
            // Replace an older report with this small, explicitly typed receipt.
            JSONObject stored=completedReportPayload(report,completion);
            byte[] bytes=stored.toString().getBytes(StandardCharsets.UTF_8);if(bytes.length>65536)throw new IOException("numeric_report_bound");
            AtomicFile file=new AtomicFile(new File(activity.getFilesDir(),"udp-app-last-report.json"));FileOutputStream out=null;
            try{out=file.startWrite();out.write(bytes);file.finishWrite(out);out=null;written=true;}finally{if(out!=null)file.failWrite(out);}
        }catch(Exception ignored){}
        final boolean reportWritten=written;
        finishRemote(attempt);
        if(!cleanupAllowsReconnect(completion)){
            synchronized(lock){attempt.stopped=true;retiring=attempt;if(current==attempt){current=null;generation++;}}
            activity.ui.post(()->{showLogin();status.setText("UDP 已停止，但本机音频资源收尾未确认。此次候选需结束进程后再测，暂时禁止重连；未回退 TCP。");});
            return;
        }
        final long completedGeneration;
        synchronized(lock){
            if(retiring==attempt)retiring=null;
            if(current!=attempt||generation!=attempt.generation)return;
            // Publish completion atomically. A cancel arriving before the UI
            // runnable must not put this already-finished attempt back into
            // retiring, since the Probe will not issue another callback.
            attempt.stopped=true;current=null;completedGeneration=generation;
        }
        activity.ui.post(()->{synchronized(lock){if(current!=null||generation!=completedGeneration)return;}
            if(activity.isFinishing()||activity.isDestroyed())return;
            showLogin();
            if(completion.shouldRemindAfterHour()){
                status.setText("已连接一小时，休息一下。需要时可以重新连接。");
                if(!activity.isFinishing()&&!activity.isDestroyed())new AlertDialog.Builder(activity).setTitle("休息一下吧").setMessage("本次已连接一小时，远程会话已结束。休息后可重新连接。").setPositiveButton("知道了",null).show();
                return;
            }
            if(completion.statisticsStatus==UdpVideoProbe.CompletionReceipt.REPORT_REJECTED_LIMIT){
                status.setText("UDP 已结束，统计报告超过 64 KiB，已明确拒绝；音频收尾已确认，可以重新连接。未回退 TCP。");return;
            }
            if(!completion.statisticsAccepted()){
                status.setText("UDP 已结束，统计报告未能生成；音频收尾已确认，可以重新连接。未回退 TCP。");return;
            }
            if(report.optInt("session_end_reason_code",0)==2){status.setText("UDP 首帧等待超时，请检查节点是否在线后重新连接；未回退 TCP。");return;}
            if(report.optInt("session_end_reason_code",0)==3){status.setText("与远端的 UDP 连接已中断，请检查网络后重新连接；未回退 TCP。");return;}
            status.setText(!reportWritten?"UDP 已结束，但数值报告保存失败；未回退 TCP。":failed?"UDP 测试中断，数值报告已保存；未回退 TCP。":"UDP 测试结束，数值报告已保存在 App 私有目录。");});
    }
    private void finishRemote(Attempt attempt){
        String id=attempt.sessionId;if(id==null)return;
        try{http(attempt,"DELETE","/udp/session/"+id,null,false);}catch(Exception ignored){}
    }
    @Override public void failed(Exception failure){
        cancel(true);activity.ui.post(()->{if(!active()&&status!=null)status.setText("UDP 媒体中断（"+failure.getClass().getSimpleName()+"），未回退 TCP。");});
    }
    private void dismissExitConfirmation(){
        AlertDialog dialog=exitDialog;UdpExitConfirmationGate.Token token=exitToken;exitDialog=null;exitToken=null;
        exitGate.dismiss(token);if(dialog!=null)dialog.dismiss();
    }
    @Override public void requestBack(){
        if(Looper.myLooper()!=Looper.getMainLooper()){activity.ui.post(this::requestBack);return;}
        final Attempt captured;final UdpExitConfirmationGate.Token token;
        synchronized(lock){if(current==null||current.stopped)return;captured=current;token=exitGate.open(generation,captured);}
        if(token==null)return;
        if(activity.isFinishing()||activity.isDestroyed()){exitGate.dismiss(token);return;}
        AlertDialog dialog=new AlertDialog.Builder(activity).setTitle("要退出远程连接吗？")
            .setNegativeButton("继续使用",(ignored,which)->{synchronized(lock){exitGate.resolve(token,false,generation,current);}})
            .setPositiveButton("退出连接",(ignored,which)->{
                boolean exit; synchronized(lock){exit=exitGate.resolve(token,true,generation,current)==UdpExitConfirmationGate.EXIT_CURRENT;}
                if(exit)cancelOwned(captured,true);
            }).create();
        dialog.setOnDismissListener(ignored->{exitGate.dismiss(token);if(exitToken==token){exitToken=null;exitDialog=null;}});
        synchronized(lock){if(current!=captured||generation!=captured.generation||captured.stopped){exitGate.dismiss(token);return;}}
        exitToken=token;exitDialog=dialog;dialog.show();
    }
    @Override public void cancel(boolean show){
        cancelOwned(null,show);
    }
    private void cancelOwned(Attempt expected,boolean show){
        Attempt attempt; synchronized(lock){attempt=current;if(attempt==null||(expected!=null&&(attempt!=expected||generation!=expected.generation)))return;attempt.cancelled=true;attempt.stopped=true;current=null;retiring=attempt;generation++;}
        Socket socket=attempt.https;if(socket!=null)try{socket.close();}catch(Exception ignored){}
        UdpVideoProbe receiver=attempt.receiver;if(receiver!=null)receiver.cancelApp();
        // Never stop a later generation here; receiver cleanup owns codec/audio/CANCEL/STOP.
        new Thread(()->finishRemote(attempt),"udp-session-delete").start();
        if(show)activity.ui.post(()->{if(!active()){showLogin();status.setText("已离开 UDP 测试。重新连接会建立新的短期会话。");}});
    }
}
