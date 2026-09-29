package local.remoteandroid.direct;

import org.json.JSONObject;
import javax.net.ssl.SSLSocket;
import java.io.IOException;
import java.nio.charset.StandardCharsets;

/** Small authenticated JSON requests on the existing certificate-pinned gateway. */
final class DiagnosticHttp {
    final MainActivity activity;
    final String credential, host;
    DiagnosticHttp(MainActivity activity, String credential, String host) {
        this.activity = activity;
        this.credential = credential;
        this.host = host;
    }
    JSONObject request(String method, String path, JSONObject body) throws Exception {
        SSLSocket socket = activity.connect(host);
        try {
            socket.setSoTimeout(30000);
            byte[] bytes = body == null ? null : body.toString().getBytes(StandardCharsets.UTF_8);
            String request = method + " " + path + " HTTP/1.1\r\nHost: " + host
                    + "\r\nAuthorization: " + credential + "\r\nConnection: close\r\n"
                    + (bytes == null ? "" : "Content-Type: application/json\r\nContent-Length: " + bytes.length + "\r\n")
                    + "\r\n";
            socket.getOutputStream().write(request.getBytes(StandardCharsets.US_ASCII));
            if (bytes != null) socket.getOutputStream().write(bytes);
            socket.getOutputStream().flush();
            MediaTransfer.Response header = MediaTransfer.readHeader(socket.getInputStream());
            if (header.code < 200 || header.code >= 300) {
                throw new IOException(header.code == 401 ? "账号或密码不正确"
                        : header.code == 404 ? "服务器尚未提供检测功能"
                        : header.code == 409 ? "已有其他检测在进行，或报告编号冲突"
                        : header.code == 429 ? "请求过多，请稍后重试"
                        : header.code == 503 ? "测试画面暂不可用，请稍后重试"
                        : "服务器返回 " + header.code);
            }
            return MediaTransfer.readJson(socket.getInputStream(), header.length);
        } finally {
            activity.sockets.remove(socket);
            try { socket.close(); } catch (IOException ignored) {}
        }
    }
}
