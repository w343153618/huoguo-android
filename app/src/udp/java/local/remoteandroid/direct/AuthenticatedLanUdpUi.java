package local.remoteandroid.direct;

import android.os.Looper;
import android.util.AtomicFile;
import android.view.Gravity;
import android.widget.*;
import org.json.JSONObject;
import java.io.*;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Arrays;
import javax.net.ssl.SSLSocket;

/** Isolated, opt-in LAN entry. HTTPS carries authentication/description only;
 * AES-GCM UDP carries video, AAC and Android native touch. No media fallback. */
public final class AuthenticatedLanUdpUi implements LanUdpEntry {
    private final MainActivity activity;
    private final Object lock=new Object();
    private long generation;
    private Attempt current,retiring;
    private EditText address,user,password;
    private TextView status;
    private Spinner fps,quality,rate,buffer;
    private CheckBox sound,pcmQueue;
    private static final class Attempt {
        final long generation;final String endpoint,credential;
        final boolean boundedPcmQueueEnabled;
        volatile boolean cancelled;volatile SSLSocket https;volatile UdpVideoProbe receiver;
        volatile String sessionId;volatile boolean stopped;
        Attempt(long generation,String endpoint,String credential,boolean pcmQueue){this.generation=generation;this.endpoint=endpoint;this.credential=credential;boundedPcmQueueEnabled=pcmQueue;}
    }
    public AuthenticatedLanUdpUi(MainActivity activity){
        if(!BuildConfig.AUTHENTICATED_LAN_UDP||!BuildConfig.APPLICATION_ID.equals("local.remoteandroid.direct.experiment"))throw new IllegalStateException("isolated build required");
        this.activity=activity;
    }
    @Override public boolean active(){synchronized(lock){return current!=null&&!current.stopped;}}
    @Override public void showLogin(){
        if(Looper.myLooper()!=Looper.getMainLooper()){activity.ui.post(this::showLogin);return;}
        if(active())return;
        LinearLayout box=new LinearLayout(activity);box.setOrientation(LinearLayout.VERTICAL);box.setPadding(32,32,32,32);
        TextView title=new TextView(activity);title.setText("认证 LAN UDP · 隔离候选\nHTTPS 仅登录；视频、声音、多指触控均走 UDP\n最长 120 秒；断线不会切换成 TCP 媒体");box.addView(title);
        address=field(box,"M1 局域网 IP:端口","192.168.9.128:15560");
        user=field(box,"现有安卓账号",activity.getSharedPreferences("connection",0).getString("username","huoguo"));
        password=field(box,"现有账号密码（此次仅保存在内存）","");password.setInputType(129);
        quality=choice(box,"串流清晰度",new String[]{"540P · 960","720P · 1280","1080P · 1920"},2);
        rate=choice(box,"视频 VBR 目标码率",new String[]{"4 Mbps","8 Mbps","12 Mbps","16 Mbps","24 Mbps"},2);
        fps=choice(box,"串流上限（不代表实际内容帧率）",new String[]{"60 FPS","120 FPS"},0);
        buffer=choice(box,"播放缓冲",new String[]{"30 ms","50 ms","80 ms · 推荐","100 ms"},2);
        sound=new CheckBox(activity);sound.setText("UDP 音频");sound.setChecked(true);box.addView(sound);
        pcmQueue=new CheckBox(activity);pcmQueue.setText("实验：有界 PCM 输出队列（默认关闭）");pcmQueue.setChecked(false);box.addView(pcmQueue);
        Button start=new Button(activity);start.setText("启动认证 UDP 测试");box.addView(start);status=new TextView(activity);box.addView(status);
        start.setOnClickListener(v->start());
        ScrollView scroll=new ScrollView(activity);scroll.addView(box);activity.setContentView(scroll);
    }
    private EditText field(LinearLayout box,String hint,String value){EditText input=new EditText(activity);input.setSingleLine();input.setHint(hint);input.setText(value);box.addView(input);return input;}
    private Spinner choice(LinearLayout box,String label,String[] values,int selected){TextView text=new TextView(activity);text.setText(label);box.addView(text);Spinner input=new Spinner(activity);input.setAdapter(new ArrayAdapter<>(activity,android.R.layout.simple_spinner_dropdown_item,values));input.setSelection(selected);box.addView(input);return input;}
    private void start(){
        synchronized(lock){if(retiring!=null){status.setText("上一条 UDP 会话正在收尾，请稍后重新连接。");return;}}
        final String endpoint,credential;final JSONObject request=new JSONObject();
        try{
            endpoint=Endpoint.destination(address.getText().toString());Endpoint.Address parsed=Endpoint.parse(endpoint);
            if(parsed.port!=15560||!privateIpv4(parsed.host))throw new IOException("仅接受局域网 IPv4:15560");
            String name=user.getText().toString(),secret=password.getText().toString();
            if(name.isEmpty()||secret.isEmpty()||name.indexOf(':')>=0||name.length()>128||secret.length()>1024)throw new IOException("请填写现有账号及密码");
            credential="Basic "+android.util.Base64.encodeToString((name+":"+secret).getBytes(StandardCharsets.UTF_8),android.util.Base64.NO_WRAP);
            request.put("max_size",new int[]{960,1280,1920}[quality.getSelectedItemPosition()]);
            request.put("video_bit_rate",new int[]{4000000,8000000,12000000,16000000,24000000}[rate.getSelectedItemPosition()]);
            request.put("max_fps",fps.getSelectedItemPosition()==0?60:120).put("buffer_ms",new int[]{30,50,80,100}[buffer.getSelectedItemPosition()]);
            request.put("seconds",120).put("audio_enabled",sound.isChecked()).put("touch_enabled",true);
            activity.initTLS();
        }catch(Exception failure){status.setText("无法启动："+failure.getMessage());return;}
        Attempt attempt;
        synchronized(lock){if(current!=null&&!current.stopped)return;attempt=new Attempt(++generation,endpoint,credential,pcmQueue.isChecked());current=attempt;}
        password.setText("");LinearLayout wait=new LinearLayout(activity);wait.setOrientation(LinearLayout.VERTICAL);wait.setGravity(Gravity.CENTER);
        TextView text=new TextView(activity);text.setText("正在通过受信 HTTPS 登录…\n媒体不会回退 TCP");wait.addView(text);Button cancel=new Button(activity);cancel.setText("取消连接");cancel.setOnClickListener(v->cancel(true));wait.addView(cancel);activity.setContentView(wait);
        new Thread(()->authenticate(attempt,request),"udp-session-auth").start();
    }
    private void authenticate(Attempt attempt,JSONObject request){
        try{
            JSONObject descriptor=http(attempt,"POST","/udp/session",request,true);
            String id=descriptor.optString("session","");if(!id.matches("[0-9a-f]{32}"))throw new IOException("invalid_session_id");attempt.sessionId=id;
            validateDescriptor(descriptor,Endpoint.parse(attempt.endpoint).host);
            synchronized(lock){
                if(attempt.cancelled||current!=attempt||generation!=attempt.generation)throw new IOException("cancelled");
                attempt.receiver=UdpVideoProbe.startApp(activity,descriptor,attempt.boundedPcmQueueEnabled,(report,failed)->finished(attempt,report,failed));
            }
        }catch(Exception failure){
            finishRemote(attempt);
            synchronized(lock){if(retiring==attempt)retiring=null;}
            activity.ui.post(()->{synchronized(lock){if(current!=attempt||generation!=attempt.generation)return;attempt.stopped=true;current=null;}
                showLogin();status.setText(attempt.cancelled?"已取消连接":"UDP 连接失败（"+failure.getClass().getSimpleName()+"），未回退 TCP。");});
        }
    }
    static boolean privateIpv4(String host){return LanUdpContract.privateIpv4(host);}
    static void validateDescriptor(JSONObject json,String loginHost)throws Exception{
        java.util.Map<String,Object> fields=new java.util.HashMap<>();java.util.Iterator<String> keys=json.keys();
        while(keys.hasNext()){String key=keys.next();fields.put(key,json.get(key));}
        LanUdpContract.validate(fields,loginHost);
    }
    private JSONObject http(Attempt attempt,String method,String path,JSONObject data,boolean cancellable)throws Exception{
        if(cancellable&&attempt.cancelled)throw new IOException("cancelled");Endpoint.Address endpoint=Endpoint.parse(attempt.endpoint);
        SSLSocket socket=(SSLSocket)activity.tls.getSocketFactory().createSocket();
        if(cancellable){synchronized(lock){if(attempt.cancelled){socket.close();throw new IOException("cancelled");}attempt.https=socket;}}
        try{
            socket.connect(new InetSocketAddress(endpoint.host,endpoint.port),5000);socket.setSoTimeout(7000);socket.setTcpNoDelay(true);socket.startHandshake();
            byte[] leaf=MessageDigest.getInstance("SHA-256").digest(socket.getSession().getPeerCertificates()[0].getEncoded());boolean pinned=false;
            for(byte[] pin:activity.fingerprints)pinned|=MessageDigest.isEqual(pin,leaf);if(!pinned)throw new IOException("certificate_pin");
            byte[] body=data==null?new byte[0]:data.toString().getBytes(StandardCharsets.UTF_8);
            OutputStream out=socket.getOutputStream();out.write((method+" "+path+" HTTP/1.1\r\nHost: "+attempt.endpoint+"\r\nAuthorization: "+attempt.credential+"\r\nContent-Type: application/json\r\nContent-Length: "+body.length+"\r\nConnection: close\r\n\r\n").getBytes(StandardCharsets.US_ASCII));out.write(body);out.flush();
            InputStream in=socket.getInputStream();int status=activity.header(in);if(status!=200&&!(method.equals("POST")&&status==201)&&!(method.equals("DELETE")&&status==204))throw new IOException("https_status_"+status);
            ByteArrayOutputStream bytes=new ByteArrayOutputStream();byte[] chunk=new byte[1024];int n;
            while((n=in.read(chunk))!=-1){if(bytes.size()+n>32768)throw new IOException("descriptor_bound");bytes.write(chunk,0,n);}
            if(bytes.size()==0)return new JSONObject();byte[] encoded=bytes.toByteArray();try{return new JSONObject(new String(encoded,StandardCharsets.UTF_8));}finally{Arrays.fill(encoded,(byte)0);}
        }finally{socket.close();if(cancellable)attempt.https=null;}
    }
    private void finished(Attempt attempt,JSONObject report,boolean failed){
        boolean written=false;
        try{byte[] bytes=report.toString().getBytes(StandardCharsets.UTF_8);if(bytes.length>65536)throw new IOException("numeric_report_bound");
            AtomicFile file=new AtomicFile(new File(activity.getFilesDir(),"udp-app-last-report.json"));FileOutputStream out=null;
            try{out=file.startWrite();out.write(bytes);file.finishWrite(out);out=null;written=true;}finally{if(out!=null)file.failWrite(out);}
        }catch(Exception ignored){}
        final boolean reportWritten=written;
        finishRemote(attempt);
        if(report.optInt("audio_cleanup_confirmed",0)!=1){
            synchronized(lock){attempt.stopped=true;retiring=attempt;if(current==attempt){current=null;generation++;}}
            activity.ui.post(()->{showLogin();status.setText("UDP 已停止，但本机音频资源收尾未确认。此次候选需结束进程后再测，暂时禁止重连；未回退 TCP。");});
            return;
        }
        synchronized(lock){if(retiring==attempt)retiring=null;}
        activity.ui.post(()->{synchronized(lock){if(current!=attempt||generation!=attempt.generation)return;attempt.stopped=true;current=null;}
            showLogin();status.setText(!reportWritten?"UDP 已结束，但数值报告保存失败；未回退 TCP。":failed?"UDP 测试中断，数值报告已保存；未回退 TCP。":"UDP 测试结束，数值报告已保存在 App 私有目录。");});
    }
    private void finishRemote(Attempt attempt){
        String id=attempt.sessionId;if(id==null)return;
        try{http(attempt,"DELETE","/udp/session/"+id,null,false);}catch(Exception ignored){}
    }
    @Override public void failed(Exception failure){
        cancel(true);activity.ui.post(()->{if(!active()&&status!=null)status.setText("UDP 媒体中断（"+failure.getClass().getSimpleName()+"），未回退 TCP。");});
    }
    @Override public void cancel(boolean show){
        Attempt attempt; synchronized(lock){attempt=current;if(attempt==null)return;attempt.cancelled=true;attempt.stopped=true;current=null;retiring=attempt;generation++;}
        SSLSocket socket=attempt.https;if(socket!=null)try{socket.close();}catch(Exception ignored){}
        UdpVideoProbe receiver=attempt.receiver;if(receiver!=null)receiver.cancelApp();
        // Never stop a later generation here; receiver cleanup owns codec/audio/CANCEL/STOP.
        new Thread(()->finishRemote(attempt),"udp-session-delete").start();
        if(show)activity.ui.post(()->{if(!active()){showLogin();status.setText("已离开 UDP 测试。重新连接会建立新的短期会话。");}});
    }
}
