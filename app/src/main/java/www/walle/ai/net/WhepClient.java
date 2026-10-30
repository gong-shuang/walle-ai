package www.walle.ai.net;

import android.util.Log;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLException;

/**
 * WHEP (WebRTC-HTTP Egress Protocol) 信令客户端。
 *
 * <p>流程：
 * <ol>
 *   <li>POST SDP Offer 到 WHEP 端点；</li>
 *   <li>服务端返回 201 Created，body 为 SDP Answer，Location 头为本次会话地址；</li>
 *   <li>退出时 DELETE 该会话地址，通知服务端停止推流。</li>
 * </ol>
 *
 * <p>TLS 策略：使用 Android 系统信任库做证书链校验，并保留默认的主机名校验
 * （HttpsURLConnection 的默认行为），不提供「信任所有证书」的开关。
 * 如果服务端换成私有 CA / 自签证书，应在 res/xml/network_security_config.xml 里
 * 用 domain-config + trust-anchors 显式声明，而不是在代码里放宽校验。
 */
public final class WhepClient {

    private static final String TAG = "WhepDemo";

    private WhepClient() {
    }

    /** WHEP 协商结果：SDP Answer 与本次会话地址。 */
    public static final class WhepResponse {
        public final String sdpAnswer;
        public final String location;

        public WhepResponse(String sdpAnswer, String location) {
            this.sdpAnswer = sdpAnswer;
            this.location = location;
        }
    }

    /** WHEP 协商失败异常。 */
    public static class WhepException extends Exception {
        public WhepException(String message) {
            super(message);
        }
    }

    /** 向 WHEP 端点发送 SDP Offer，返回 Answer 与会话地址。必须在后台线程调用。 */
    public static WhepResponse postOffer(String whepUrl, String sdpOffer) throws Exception {
        HttpsURLConnection connection = (HttpsURLConnection) new URL(whepUrl).openConnection();
        // 不设置 SSLSocketFactory / HostnameVerifier：走系统 CA + 默认主机名校验
        connection.setRequestMethod("POST");
        connection.setConnectTimeout(10_000);
        connection.setReadTimeout(10_000);
        connection.setDoOutput(true);
        connection.setInstanceFollowRedirects(false);
        connection.setRequestProperty("Content-Type", "application/sdp");
        connection.setRequestProperty("Accept", "application/sdp");

        try {
            try (OutputStreamWriter writer =
                         new OutputStreamWriter(connection.getOutputStream(), StandardCharsets.UTF_8)) {
                writer.write(sdpOffer);
                writer.flush();
            }

            int status = connection.getResponseCode();
            String body = readBody(connection, status);
            String locationHeader = connection.getHeaderField("Location");
            Log.i(TAG, "POST WHEP -> HTTP " + status
                    + ", body=" + body.length() + " chars, Location=" + locationHeader);

            if (status != HttpURLConnection.HTTP_OK && status != HttpURLConnection.HTTP_CREATED) {
                throw new WhepException("WHEP 协商失败: HTTP " + status + ", " + body);
            }
            if (body.trim().isEmpty()) {
                throw new WhepException("WHEP 响应为空: HTTP " + status);
            }

            return new WhepResponse(
                    body,
                    locationHeader == null ? null : resolveLocation(whepUrl, locationHeader)
            );
        } catch (SSLException e) {
            throw new WhepException(tlsHint(whepUrl, e));
        } finally {
            connection.disconnect();
        }
    }

    /**
     * 把 TLS 握手失败翻译成用户能照做的提示。
     * <p>常见原因：证书由私有 CA 签发（系统中没有该根证书）、证书域名与地址不一致（例如用 IP 直连域名证书）、
     * 证书过期、或手机系统时间不正确。</p>
     */
    private static String tlsHint(String url, SSLException e) {
        String reason = e.getMessage() == null ? "" : e.getMessage();
        String host = "";
        try {
            host = new URL(url).getHost();
        } catch (Exception ignore) { }
        if (reason.contains("Certificate") || reason.contains("PKIX")
                || reason.contains("Trust") || reason.contains("certificate")) {
            return "WHEP 证书校验失败：系统不信任 " + host + " 的证书（自签证书请改用受信任证书，"
                    + "或在 network_security_config 中显式信任该 CA）";
        }
        return "WHEP TLS 握手失败（" + host + "）：请检查证书域名是否匹配、证书是否过期、手机时间是否正确"
                + (reason.isEmpty() ? "" : "（" + reason + "）");
    }

    /** 通知服务端结束本次 WHEP 会话。必须在后台线程调用。 */
    public static void deleteSession(String location) {
        if (location == null || location.trim().isEmpty()) {
            return;
        }
        // 网络抖动时单次 DELETE 很容易失败，而残留会话会持续占用服务端推流资源，
        // 因此失败后间隔 1 秒再重试一次（最多 2 次），仍失败才放弃。
        final int maxAttempts = 2;
        for (int attempt = 1; attempt <= maxAttempts; attempt++) {
            if (deleteSessionOnce(location)) {
                return;
            }
            if (attempt < maxAttempts) {
                try {
                    Thread.sleep(1000L);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    return;
                }
            }
        }
        Log.w(TAG, "DELETE WHEP 会话重试后仍失败: " + location);
    }

    /** 执行一次 DELETE 请求，HTTP 2xx 视为成功。 */
    private static boolean deleteSessionOnce(String location) {
        HttpsURLConnection connection = null;
        try {
            connection = (HttpsURLConnection) new URL(location).openConnection();
            connection.setRequestMethod("DELETE");
            connection.setConnectTimeout(5_000);
            connection.setReadTimeout(5_000);
            connection.setInstanceFollowRedirects(false);
            int status = connection.getResponseCode();
            return status >= 200 && status <= 299;
        } catch (Exception e) {
            Log.w(TAG, "DELETE WHEP 会话失败: " + e.getMessage());
            return false;
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
    }

    private static String readBody(HttpsURLConnection connection, int status) {
        InputStream stream;
        try {
            stream = (status >= 200 && status <= 299)
                    ? connection.getInputStream()
                    : connection.getErrorStream();
        } catch (Exception e) {
            return "";
        }
        if (stream == null) {
            return "";
        }
        try (BufferedReader reader =
                     new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8))) {
            StringBuilder builder = new StringBuilder();
            String line;
            while ((line = reader.readLine()) != null) {
                builder.append(line).append('\n');
            }
            return builder.toString();
        } catch (Exception e) {
            return "";
        }
    }

    /** 服务端可能返回相对路径的 Location（如 /rtc/v1/whep/xxx），需要拼成绝对地址。 */
    private static String resolveLocation(String baseUrl, String location) {
        try {
            String lower = location.toLowerCase();
            if (lower.startsWith("http://") || lower.startsWith("https://")) {
                return location;
            }
            return new URI(baseUrl).resolve(location).toString();
        } catch (Exception e) {
            return location;
        }
    }
}
