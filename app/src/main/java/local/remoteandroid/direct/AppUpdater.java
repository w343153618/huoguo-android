package local.remoteandroid.direct;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.pm.Signature;
import android.net.Uri;
import android.provider.Settings;
import android.widget.Toast;
import org.json.JSONObject;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import javax.net.ssl.*;
import java.security.KeyStore;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import java.util.concurrent.atomic.AtomicBoolean;
import java.lang.ref.WeakReference;

/** HTTPS releases with digest, package, version and signing identity verification. */
final class AppUpdater {
    private final Activity activity;
    private final AtomicBoolean busy = new AtomicBoolean();
    private static final UpdateOperationGate OPERATIONS=new UpdateOperationGate();
    private static volatile WeakReference<Activity> operationActivity=new WeakReference<>(null);
    private volatile long operation;
    private volatile boolean closed;
    private boolean waitingForPermission;
    private boolean installerLaunched;
    static final String DEFAULT_UPDATE_URL = "https://146.56.249.175:15556/updates/update.json";
    static String getUpdateUrl() {
        String u = BuildConfig.UPDATE_MANIFEST_URL;
        return (u != null && !u.trim().isEmpty()) ? u.trim() : DEFAULT_UPDATE_URL;
    }
    AppUpdater(Activity activity) { this.activity = activity; }
    private long begin(int phase){
        if(closed)return 0;long token=OPERATIONS.acquire(phase);if(token==0)return 0;
        if(closed){OPERATIONS.release(token);return 0;}
        operation=token;operationActivity=new WeakReference<>(activity);busy.set(true);return token;
    }
    private boolean active(long token){return !closed&&OPERATIONS.current(token);}
    private void end(long token){if(OPERATIONS.release(token)){busy.set(false);if(operation==token)operation=0;}}
    private boolean move(long token,int before,int after){return active(token)&&OPERATIONS.transition(token,before,after);}
    /** Active I/O releases only from its worker; dialog ownership may end immediately. */
    void close(){
        closed=true;int phase=OPERATIONS.phase(operation);
        if(phase==UpdateOperationGate.SELECTING||phase==UpdateOperationGate.DIALOG)end(operation);
    }
    boolean isBusy(){return OPERATIONS.phase()!=UpdateOperationGate.IDLE;}
    void check(boolean manual) {
        if(manual){
            long token=begin(UpdateOperationGate.SELECTING);if(token==0)return;
            activity.runOnUiThread(()->chooseChannel(token));return;
        }
        try{checkChannel(UpdateChannelPolicy.channelForPackage(activity.getPackageName()),false,0);}
        catch(Exception error){/* Unknown application identity never acquires an operation. */}
    }
    private void chooseChannel(long token){
        if(!active(token)||activity.isFinishing()||activity.isDestroyed()){end(token);return;}
        AtomicBoolean selected=new AtomicBoolean();
        AlertDialog dialog=new AlertDialog.Builder(activity).setTitle("选择更新通道")
            .setItems(new String[]{"稳定版 · 日常使用","测试版 · UDP 机主试用"},(ignored,which)->{
                if(!move(token,UpdateOperationGate.SELECTING,UpdateOperationGate.CHECKING))return;
                selected.set(true);
                checkChannel(which==0?UpdateChannelPolicy.STABLE:UpdateChannelPolicy.TEST,true,token);
            }).setNegativeButton("取消",null).create();
        dialog.setOnDismissListener(ignored->{if(!selected.get())end(token);});dialog.show();
    }
    private static final class Installed {
        final long code;final String name;
        Installed(long code,String name){this.code=code;this.name=name;}
    }
    private Installed installed(String target)throws Exception{
        try{
            PackageInfo info=activity.getPackageManager().getPackageInfo(target,PackageManager.GET_SIGNING_CERTIFICATES);
            if(info.signingInfo==null||info.signingInfo.getApkContentsSigners().length!=1
                ||!Collections.singleton(UpdateChannelPolicy.SIGNER_SHA256).equals(certificates(info)))throw new IOException("已安装的所选应用签名不匹配");
            return new Installed(info.getLongVersionCode(),info.versionName==null?"未知":info.versionName);
        }catch(PackageManager.NameNotFoundException absent){return new Installed(0,"未安装");}
    }
    private void checkChannel(String channel,boolean manual,long suppliedToken) {
        final String url,target;
        try{target=UpdateChannelPolicy.packageName(channel);url=manual?UpdateChannelPolicy.manifestUrl(channel):getUpdateUrl();}
        catch(Exception error){if(manual)toast("更新通道无效");end(suppliedToken);return;}
        if (url.isEmpty()) { if (manual) toast("更新入口尚未配置");end(suppliedToken);return; }
        android.content.SharedPreferences prefs = activity.getSharedPreferences("updates", 0);
        long now = System.currentTimeMillis();
        if (!manual && now - prefs.getLong("last_check_"+channel,prefs.getLong("last_check",0)) < 86400000L) return;
        final long token=suppliedToken==0?begin(UpdateOperationGate.CHECKING):suppliedToken;
        if(!active(token)||OPERATIONS.phase(token)!=UpdateOperationGate.CHECKING){end(token);return;}
        prefs.edit().putLong("last_check_"+channel, now).apply();
        if (manual) toast("正在检查更新…");
        new Thread(() -> {
            boolean posted=false;
            try {
                JSONObject info = new JSONObject(new String(read(url, 65536), StandardCharsets.UTF_8));
                if(!active(token))throw new IOException("更新检查已取消");
                Object codeValue=info.get("version_code"),sizeValue=info.get("apk_size");
                if(!(codeValue instanceof Integer||codeValue instanceof Long)||!(sizeValue instanceof Integer||sizeValue instanceof Long))throw new IOException("更新版本码或大小必须为整数");
                long version = info.getLong("version_code");
                String name=info.getString("version_name");
                String notes=info.optString("changelog","暂无更新说明");
                String apkUrl=info.optString("apk_url");
                String digest=info.optString("sha256");
                long size=info.optLong("apk_size");
                if(info.has("channel")&&!channel.equals(info.getString("channel")))throw new IOException("服务器通道说明与所选通道不匹配");
                UpdateChannelPolicy.manifest(channel,info.has("application_id")?info.getString("application_id"):null,
                    info.has("signing_certificate_sha256")?info.getString("signing_certificate_sha256"):null,
                    name,version,apkUrl,digest,size);
                Installed chosen=installed(target);
                int action=UpdateChannelPolicy.action(activity.getPackageName(),target,version,chosen.code);
                if(action!=UpdateChannelPolicy.INSTALL&&!manual)return;
                if(!move(token,UpdateOperationGate.CHECKING,UpdateOperationGate.DIALOG))throw new IOException("更新检查已取消");
                posted=true;
                activity.runOnUiThread(()->presentChannelUpdate(token,channel,target,chosen,name,notes,action,
                    ()->download(token,apkUrl,digest,size,version,target,channel)));
            }catch(Exception error){if(manual)toast("检查更新失败："+MainActivity.message(error));}
            finally{if(!posted)end(token);}
        }, "update-check").start();
    }
    private AlertDialog presentChannelUpdate(long token,String channel,String target,Installed installed,String name,String notes,int action,Runnable confirmed){
        if(!active(token)||activity.isFinishing()||activity.isDestroyed()){end(token);return null;}
        busy.set(true);
        final String label;try{label=UpdateChannelPolicy.label(channel);}catch(Exception invalid){end(token);return null;}
        boolean other=!activity.getPackageName().equals(target),install=action==UpdateChannelPolicy.INSTALL;
        android.widget.TextView content=new android.widget.TextView(activity);
        int pad=Math.round(20*activity.getResources().getDisplayMetrics().density);content.setPadding(pad,pad,pad,pad);content.setTextSize(15);
        content.setText("所选通道："+label+"\n当前打开：v"+BuildConfig.VERSION_NAME+"（版本码 "+BuildConfig.VERSION_CODE+"）\n"
            +label+"已安装："+(installed.code==0?"未安装":"v"+installed.name+"（版本码 "+installed.code+"）")
            +"\n服务器版本：v"+name+"\n\n更新内容\n\n"+notes
            +(other?"\n\n稳定版和测试版是独立应用，安装另一版不会移除当前版，设置分别保存。":"")
            +(UpdateChannelPolicy.TEST.equals(channel)?"\n\n测试版仅机主试用，可能不稳定；朋友暂用稳定版。":"")
            +(install?"\n\n确认后才下载；安装仍由 Android 确认。":action==UpdateChannelPolicy.OPEN_OTHER?"\n\n所选应用已是当前或更新版本，可直接打开。":"\n\n当前应用无需更新。"));
        android.widget.ScrollView scroll=new android.widget.ScrollView(activity);scroll.addView(content);
        AtomicBoolean handedOff=new AtomicBoolean();
        AlertDialog.Builder builder=new AlertDialog.Builder(activity).setTitle(label+" · v"+name).setView(scroll);
        if(install)builder.setNegativeButton("取消更新",null).setPositiveButton(installed.code==0?"安装"+label:"确认更新",(dialog,which)->{
            if(!move(token,UpdateOperationGate.DIALOG,UpdateOperationGate.DOWNLOADING))return;
            handedOff.set(true);confirmed.run();
        });
        else if(action==UpdateChannelPolicy.OPEN_OTHER)builder.setNegativeButton("取消",null).setPositiveButton("打开"+label,(dialog,which)->{
            if(!active(token))return;handedOff.set(true);end(token);
            try{installed(target);Intent launch=activity.getPackageManager().getLaunchIntentForPackage(target);
                if(launch==null)throw new IOException("所选应用没有启动入口");activity.startActivity(launch);
            }catch(Exception failure){toast("无法打开"+label+"，请重新检查更新");}
        });
        else builder.setPositiveButton("知道了",null);
        AlertDialog dialog=builder.create();dialog.setOnDismissListener(ignored->{if(!handedOff.get())end(token);});dialog.show();return dialog;
    }
    AlertDialog presentUpdate(String name,String notes,boolean newer,Runnable confirmed){
        if(activity.isFinishing()||activity.isDestroyed()){busy.set(false);return null;}
        busy.set(true);
        android.widget.TextView content=new android.widget.TextView(activity);
        int pad=Math.round(20*activity.getResources().getDisplayMetrics().density);
        content.setPadding(pad,pad,pad,pad);content.setTextSize(15);
        content.setText("已安装：v"+BuildConfig.VERSION_NAME+"（版本码 "+BuildConfig.VERSION_CODE+"）\n"+(newer?"可升级：v"+name:"服务器最新版：v"+name)+"\n\n更新内容\n\n"+notes+(newer?"\n\n确认后才下载；安装仍由 Android 确认，保留现有设置。":"\n\n当前安装版本 v"+BuildConfig.VERSION_NAME+"，无需更新。"));
        android.widget.ScrollView scroll=new android.widget.ScrollView(activity);scroll.addView(content);
        AtomicBoolean handedOff=new AtomicBoolean();
        AlertDialog.Builder builder=new AlertDialog.Builder(activity)
            .setTitle(newer?"发现新版本 v"+name:"已是最新版本 · 更新说明 v"+name).setView(scroll);
        if(newer)builder.setNegativeButton("取消更新",null).setPositiveButton("确认更新",(dialog,which)->{
            handedOff.set(true);busy.set(false);confirmed.run();
        });
        else builder.setPositiveButton("知道了",null);
        AlertDialog dialog=builder.create();dialog.setOnDismissListener(ignored->{if(!handedOff.get())busy.set(false);});dialog.show();return dialog;
    }
    private void download(long token,String url, String hash, long expectedSize, long version,String target,String channel) {
        if(!active(token)||OPERATIONS.phase(token)!=UpdateOperationGate.DOWNLOADING){end(token);return;}
        toast("正在下载更新…");
        new Thread(() -> {
            File part = new File(activity.getCacheDir(), "update.part");
            File apk = new File(activity.getCacheDir(), "update.apk");
            HttpURLConnection connection = null;
            boolean installationHandedOff=false;
            try {
                UpdateChannelPolicy.https(url);
                connection = open(url);
                MessageDigest digest = MessageDigest.getInstance("SHA-256");
                long count = 0;
                try (InputStream in = connection.getInputStream(); FileOutputStream out = new FileOutputStream(part)) {
                    byte[] buffer = new byte[32768]; int n;
                    while ((n = in.read(buffer)) != -1) {
                        if(!active(token))throw new IOException("更新下载已取消");
                        count += n;
                        if (count > expectedSize) throw new IOException("更新包大小不匹配");
                        out.write(buffer, 0, n); digest.update(buffer, 0, n);
                    }
                    out.getFD().sync();
                }
                if (count != expectedSize || !hex(digest.digest()).equalsIgnoreCase(hash)) throw new IOException("更新包校验失败");
                if(!active(token))throw new IOException("更新下载已取消");
                validate(part, version,target);
                if (!part.renameTo(apk)) throw new IOException("无法保存更新包");
                if(!activity.getSharedPreferences("updates",0).edit().putLong("pending_version",version).putString("pending_hash",hash).putLong("pending_size",expectedSize)
                    .putString("pending_package",target).putString("pending_channel",channel).putString("pending_apk_url",url)
                    .putBoolean("waiting_permission",false).putBoolean("installer_launched",false).commit())throw new IOException("无法保存待安装信息");
                if(!move(token,UpdateOperationGate.DOWNLOADING,UpdateOperationGate.VERIFYING))throw new IOException("更新下载已取消");
                installationHandedOff=true;
                activity.runOnUiThread(()->install(token));
            } catch (Exception error) { part.delete(); toast("更新失败：" + MainActivity.message(error)); }
            finally { if (connection != null) connection.disconnect(); if(!installationHandedOff)end(token); }
        }, "update-download").start();
    }
    private void validate(File file, long expectedVersion,String target) throws Exception {
        PackageManager manager = activity.getPackageManager();
        PackageInfo current = manager.getPackageInfo(activity.getPackageName(), PackageManager.GET_SIGNING_CERTIFICATES);
        PackageInfo next = manager.getPackageArchiveInfo(file.getAbsolutePath(), PackageManager.GET_SIGNING_CERTIFICATES);
        if(next==null||next.signingInfo==null||current.signingInfo==null||current.signingInfo.getApkContentsSigners().length!=1)throw new IOException("安装包签名信息缺失");
        Installed chosen=installed(target);
        UpdateChannelPolicy.archive(activity.getPackageName(),target,expectedVersion,chosen.code,next.packageName,
            next.getLongVersionCode(),certificates(current),certificates(next),next.signingInfo.getApkContentsSigners().length);
    }
    private static Set<String> certificates(PackageInfo info) throws Exception {
        Set<String> result = new HashSet<>();
        for (Signature signature : info.signingInfo.getApkContentsSigners())
            result.add(hex(MessageDigest.getInstance("SHA-256").digest(signature.toByteArray())));
        return result;
    }
    private void install(long token) {
        if(!active(token)||OPERATIONS.phase(token)!=UpdateOperationGate.VERIFYING){end(token);return;}
        busy.set(true);
        new Thread(()->{
        try {
            if(!active(token))throw new IOException("更新安装已取消");
            android.content.SharedPreferences prefs=activity.getSharedPreferences("updates",0);
            File file=new File(activity.getCacheDir(),"update.apk");
            long expected=prefs.getLong("pending_version",0),size=prefs.getLong("pending_size",0);
            String hash=prefs.getString("pending_hash","");
            String target=prefs.getString("pending_package",activity.getPackageName()),channel=prefs.getString("pending_channel","");
            if(!UpdateChannelPolicy.packageName(channel).equals(target))throw new IOException("待安装通道与包名不匹配");
            UpdateChannelPolicy.https(prefs.getString("pending_apk_url",""));
            if(!file.isFile()||size<1||size>UpdateChannelPolicy.MAX_APK_SIZE||file.length()!=size||!hash.matches("[0-9a-fA-F]{64}"))throw new IOException("待安装包信息无效，请重新检查更新");
            MessageDigest digest=MessageDigest.getInstance("SHA-256");
            try(InputStream input=new FileInputStream(file)){byte[] buffer=new byte[32768];int n;while((n=input.read(buffer))!=-1)digest.update(buffer,0,n);}
            if(!hex(digest.digest()).equalsIgnoreCase(hash))throw new IOException("待安装包校验失败，请重新下载");
            validate(file,expected,target);
            if(!active(token))throw new IOException("更新安装已取消");
            activity.runOnUiThread(()->launchInstaller(token));
        }catch(Exception error){if(OPERATIONS.current(token))activity.getSharedPreferences("updates",0).edit().putBoolean("waiting_permission",false).apply();end(token);toast("安装更新已停止："+MainActivity.message(error));}
        },"update-install-verify").start();
    }
    private void launchInstaller(long token) {
        if(!active(token)||activity.isFinishing()||activity.isDestroyed()){end(token);return;}
        try {
            if (!activity.getPackageManager().canRequestPackageInstalls()) {
                if(!move(token,UpdateOperationGate.VERIFYING,UpdateOperationGate.WAIT_PERMISSION)){end(token);return;}
                waitingForPermission = true;
                activity.getSharedPreferences("updates",0).edit().putBoolean("waiting_permission",true).apply();
                toast("请允许此 App 安装更新，再返回 App");
                activity.startActivity(new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                    Uri.parse("package:" + activity.getPackageName())));
                return;
            }
            activity.getSharedPreferences("updates",0).edit().putBoolean("waiting_permission",false).apply();
            Intent intent = new Intent(Intent.ACTION_VIEW);
            intent.setDataAndType(Uri.parse("content://" + activity.getPackageName() + ".updates/update.apk"),
                "application/vnd.android.package-archive");
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            if(!move(token,UpdateOperationGate.VERIFYING,UpdateOperationGate.INSTALLER)){end(token);return;}
            installerLaunched=true;activity.getSharedPreferences("updates",0).edit().putBoolean("installer_launched",true).apply();
            activity.startActivity(intent);
        } catch (Exception error) { installerLaunched=false;if(OPERATIONS.current(token))activity.getSharedPreferences("updates",0).edit().putBoolean("installer_launched",false).apply();end(token);toast("无法打开安装界面：" + MainActivity.message(error)); }
    }
    void resumeInstall() {
        if(closed)return;
        Activity owner=operationActivity.get();int phase=OPERATIONS.phase();
        boolean mayResume=owner==activity||owner==null||owner.isDestroyed()||owner.isFinishing();
        android.content.SharedPreferences prefs=activity.getSharedPreferences("updates",0);
        if((installerLaunched||prefs.getBoolean("installer_launched",false))&&mayResume
                &&(phase==UpdateOperationGate.INSTALLER||phase==UpdateOperationGate.IDLE)){
            if(phase==UpdateOperationGate.INSTALLER&&OPERATIONS.resume(UpdateOperationGate.INSTALLER,UpdateOperationGate.IDLE)==0)return;
            installerLaunched=false;prefs.edit().putBoolean("installer_launched",false).apply();busy.set(false);operation=0;return;
        }
        if((waitingForPermission||prefs.getBoolean("waiting_permission",false))&&mayResume
                &&(phase==UpdateOperationGate.WAIT_PERMISSION||phase==UpdateOperationGate.IDLE)){
            waitingForPermission=false;
            if(!activity.getPackageManager().canRequestPackageInstalls()){
                if(phase==UpdateOperationGate.WAIT_PERMISSION&&OPERATIONS.resume(UpdateOperationGate.WAIT_PERMISSION,UpdateOperationGate.IDLE)==0)return;
                prefs.edit().putBoolean("waiting_permission",false).apply();busy.set(false);operation=0;return;
            }
            long token=phase==UpdateOperationGate.WAIT_PERMISSION?OPERATIONS.resume(UpdateOperationGate.WAIT_PERMISSION,UpdateOperationGate.VERIFYING):begin(UpdateOperationGate.VERIFYING);
            if(token==0)return;operation=token;operationActivity=new WeakReference<>(activity);busy.set(true);install(token);
        }
    }
    private void toast(String text) { activity.runOnUiThread(() -> {
        if (!activity.isFinishing() && !activity.isDestroyed()) Toast.makeText(activity, text, Toast.LENGTH_LONG).show();
    }); }
    private static URL https(String url) throws Exception {
        return UpdateChannelPolicy.https(url);
    }
    private HttpURLConnection open(String url) throws Exception {
        URL target = https(url);
        for (int redirects = 0; redirects < 6; redirects++) {
            HttpURLConnection connection = (HttpURLConnection) target.openConnection();
            URL source=null;
            try{source=https(getUpdateUrl());}catch(Exception invalidConfiguredSource){/* Fixed manual channels still work. */}
            boolean fixedPrivateHost=target.getHost().equals("146.56.249.175")&&target.getPort()==15556;
            boolean configuredPrivateHost=source!=null&&source.getPort()==15556&&target.getHost().equals(source.getHost())&&target.getPort()==source.getPort();
            if (fixedPrivateHost||configuredPrivateHost) {
                HttpsURLConnection secured = (HttpsURLConnection) connection;
                KeyStore store = KeyStore.getInstance(KeyStore.getDefaultType()); store.load(null, null);
                List<byte[]> pins = new ArrayList<>();
                int[] certificates = {R.raw.server_cert, R.raw.server_cert_m5};
                for (int i = 0; i < certificates.length; i++) {
                    try (InputStream in = activity.getResources().openRawResource(certificates[i])) {
                        X509Certificate cert = (X509Certificate) CertificateFactory.getInstance("X.509").generateCertificate(in);
                        pins.add(MessageDigest.getInstance("SHA-256").digest(cert.getEncoded()));
                        store.setCertificateEntry("server" + i, cert);
                    }
                }
                // The existing private streaming host uses its pinned self-signed certificate.
                // GitHub release endpoints retain the platform's normal public CA validation.
                    TrustManagerFactory tm = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm()); tm.init(store);
                    SSLContext tls = SSLContext.getInstance("TLS"); tls.init(null, tm.getTrustManagers(), null);
                    secured.setSSLSocketFactory(tls.getSocketFactory());
                    secured.setHostnameVerifier((host, session) -> {
                        try {
                            byte[] actual = MessageDigest.getInstance("SHA-256").digest(session.getPeerCertificates()[0].getEncoded());
                            for (byte[] pin : pins) if (MessageDigest.isEqual(actual, pin)) return true;
                        } catch (Exception ignored) { }
                        return false;
                    });
            }
            connection.setUseCaches(false);connection.setRequestProperty("Cache-Control","no-cache, no-store");
            connection.setConnectTimeout(15000); connection.setReadTimeout(30000);
            connection.setInstanceFollowRedirects(false);
            connection.setRequestProperty("User-Agent", "HuoguoAndroid/" + BuildConfig.VERSION_NAME);
            int status = connection.getResponseCode();
            if (status == 200) return connection;
            String location = connection.getHeaderField("Location"); connection.disconnect();
            if (status >= 300 && status <= 399 && location != null) target = https(new URL(target, location).toString());
            else throw new IOException("更新服务器返回 " + status);
        }
        throw new IOException("更新地址重定向过多");
    }
    private byte[] read(String url, int limit) throws Exception {
        HttpURLConnection connection = open(url);
        try (InputStream in = connection.getInputStream(); ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[4096]; int n;
            while ((n = in.read(buffer)) != -1) {
                if (out.size() + n > limit) throw new IOException("更新信息过大");
                out.write(buffer, 0, n);
            }
            return out.toByteArray();
        } finally { connection.disconnect(); }
    }
    private static String hex(byte[] data) {
        StringBuilder out = new StringBuilder(); for (byte b : data) out.append(String.format(Locale.ROOT, "%02x", b & 255));
        return out.toString();
    }
}
