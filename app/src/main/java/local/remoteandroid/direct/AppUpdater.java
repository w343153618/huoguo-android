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

/** HTTPS releases with digest, package, version and signing identity verification. */
final class AppUpdater {
    private final Activity activity;
    private final AtomicBoolean busy = new AtomicBoolean();
    private boolean waitingForPermission;
    AppUpdater(Activity activity) { this.activity = activity; }
    void check(boolean manual) {
        String url = BuildConfig.UPDATE_MANIFEST_URL;
        if (url.isEmpty()) { if (manual) toast("更新入口尚未配置"); return; }
        android.content.SharedPreferences prefs = activity.getSharedPreferences("updates", 0);
        long now = System.currentTimeMillis();
        if (!manual && now - prefs.getLong("last_check", 0) < 86400000L) return;
        if (!busy.compareAndSet(false, true)) return;
        prefs.edit().putLong("last_check", now).apply();
        if (manual) toast("正在检查更新…");
        new Thread(() -> {
            boolean posted=false;
            try {
                JSONObject info = new JSONObject(new String(read(url, 65536), StandardCharsets.UTF_8));
                long version = info.getLong("version_code");
                boolean newer=version>BuildConfig.VERSION_CODE;
                if(!newer&&!manual)return;
                String name=info.getString("version_name");
                String notes=info.optString("changelog","暂无更新说明");
                String apkUrl=info.optString("apk_url");
                String digest=info.optString("sha256");
                long size=info.optLong("apk_size");
                if(newer){
                    https(apkUrl);
                    if(!digest.matches("[0-9a-fA-F]{64}"))throw new IOException("更新校验信息无效");
                    if(size<=0||size>67108864)throw new IOException("更新包大小无效");
                }
                posted=true;
                activity.runOnUiThread(()->presentUpdate(name,notes,newer,()->download(apkUrl,digest,size,version)));
            }catch(Exception error){if(manual)toast("检查更新失败："+MainActivity.message(error));}
            finally{if(!posted)busy.set(false);}
        }, "update-check").start();
    }
    AlertDialog presentUpdate(String name,String notes,boolean newer,Runnable confirmed){
        if(activity.isFinishing()||activity.isDestroyed()){busy.set(false);return null;}
        busy.set(true);
        android.widget.TextView content=new android.widget.TextView(activity);
        int pad=Math.round(20*activity.getResources().getDisplayMetrics().density);
        content.setPadding(pad,pad,pad,pad);content.setTextSize(15);
        content.setText("更新内容\n\n"+notes+(newer?"\n\n确认后才下载；安装仍由 Android 确认，保留现有设置。":"\n\n当前安装版本 v"+BuildConfig.VERSION_NAME+"，无需更新。"));
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
    private void download(String url, String hash, long expectedSize, long version) {
        if (!busy.compareAndSet(false, true)) return;
        toast("正在下载更新…");
        new Thread(() -> {
            File part = new File(activity.getCacheDir(), "update.part");
            File apk = new File(activity.getCacheDir(), "update.apk");
            HttpURLConnection connection = null;
            try {
                connection = open(url);
                MessageDigest digest = MessageDigest.getInstance("SHA-256");
                long count = 0;
                try (InputStream in = connection.getInputStream(); FileOutputStream out = new FileOutputStream(part)) {
                    byte[] buffer = new byte[32768]; int n;
                    while ((n = in.read(buffer)) != -1) {
                        count += n;
                        if (count > expectedSize) throw new IOException("更新包大小不匹配");
                        out.write(buffer, 0, n); digest.update(buffer, 0, n);
                    }
                    out.getFD().sync();
                }
                if (count != expectedSize || !hex(digest.digest()).equalsIgnoreCase(hash)) throw new IOException("更新包校验失败");
                validate(part, version);
                if (!part.renameTo(apk)) throw new IOException("无法保存更新包");
                activity.runOnUiThread(this::install);
            } catch (Exception error) { part.delete(); toast("更新失败：" + MainActivity.message(error)); }
            finally { if (connection != null) connection.disconnect(); busy.set(false); }
        }, "update-download").start();
    }
    private void validate(File file, long expectedVersion) throws Exception {
        PackageManager manager = activity.getPackageManager();
        PackageInfo current = manager.getPackageInfo(activity.getPackageName(), PackageManager.GET_SIGNING_CERTIFICATES);
        PackageInfo next = manager.getPackageArchiveInfo(file.getAbsolutePath(), PackageManager.GET_SIGNING_CERTIFICATES);
        if (next == null || !activity.getPackageName().equals(next.packageName)
                || next.getLongVersionCode() != expectedVersion || expectedVersion <= current.getLongVersionCode()
                || next.signingInfo == null || current.signingInfo == null
                || !certificates(current).equals(certificates(next))) throw new IOException("安装包身份或签名不匹配");
    }
    private static Set<String> certificates(PackageInfo info) throws Exception {
        Set<String> result = new HashSet<>();
        for (Signature signature : info.signingInfo.getApkContentsSigners())
            result.add(hex(MessageDigest.getInstance("SHA-256").digest(signature.toByteArray())));
        return result;
    }
    private void install() {
        try {
            if (!activity.getPackageManager().canRequestPackageInstalls()) {
                waitingForPermission = true;
                toast("请允许此 App 安装更新，再返回 App");
                activity.startActivity(new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                    Uri.parse("package:" + activity.getPackageName())));
                return;
            }
            Intent intent = new Intent(Intent.ACTION_VIEW);
            intent.setDataAndType(Uri.parse("content://" + activity.getPackageName() + ".updates/update.apk"),
                "application/vnd.android.package-archive");
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            activity.startActivity(intent);
        } catch (Exception error) { toast("无法打开安装界面：" + MainActivity.message(error)); }
    }
    void resumeInstall() {
        if (waitingForPermission) {
            waitingForPermission = false;
            if (activity.getPackageManager().canRequestPackageInstalls()) install();
        }
    }
    private void toast(String text) { activity.runOnUiThread(() -> {
        if (!activity.isFinishing() && !activity.isDestroyed()) Toast.makeText(activity, text, Toast.LENGTH_LONG).show();
    }); }
    private static URL https(String url) throws Exception {
        URL parsed = new URL(url);
        if (!"https".equals(parsed.getProtocol()) || parsed.getUserInfo() != null) throw new IOException("更新必须使用 HTTPS");
        return parsed;
    }
    private HttpURLConnection open(String url) throws Exception {
        URL target = https(url);
        for (int redirects = 0; redirects < 6; redirects++) {
            HttpURLConnection connection = (HttpURLConnection) target.openConnection();
            URL source = https(BuildConfig.UPDATE_MANIFEST_URL);
            if (target.getHost().equals(source.getHost()) && target.getPort() == source.getPort()) {
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
                if (source.getPort() == 15556) {
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
            }
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
