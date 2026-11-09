package www.walle.ai.net;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;

import org.eclipse.paho.client.mqttv3.IMqttActionListener;
import org.eclipse.paho.client.mqttv3.IMqttDeliveryToken;
import org.eclipse.paho.client.mqttv3.IMqttToken;
import org.eclipse.paho.client.mqttv3.MqttAsyncClient;
import org.eclipse.paho.client.mqttv3.MqttCallbackExtended;
import org.eclipse.paho.client.mqttv3.MqttConnectOptions;
import org.eclipse.paho.client.mqttv3.MqttException;
import org.eclipse.paho.client.mqttv3.MqttMessage;
import org.eclipse.paho.client.mqttv3.persist.MemoryPersistence;

import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * MQTT 传输层：只负责「连接 / 重连 / 订阅 / 发布」这些与协议语义无关的事。
 *
 * <p>与业务无关（不知道 topic 的含义、不知道 DeviceInfo），所有事件通过 {@link Listener} 抛给上层
 * {@link MqttManager}。这样连接逻辑可以独立演进（将来换 MQTT5 客户端也只改这里）。</p>
 *
 * <p>线程模型：</p>
 * <ul>
 *   <li>{@code mqtt-conn}：连接/断开（Paho 的 connect/close 可能阻塞，不能放主线程）；</li>
 *   <li>{@code mqtt-publish}：发布（Paho 的 publish 会持内部锁，且单线程保证发送顺序）；</li>
 *   <li>回调统一 post 到主线程后再通知监听者。</li>
 * </ul>
 */
public class MqttClient {

    private static final String TAG = "MqttClient";
    /** 待发送上限：断网时避免无限堆积。 */
    private static final int MAX_PENDING_PUBLISH = 200;

    /** 连接与报文事件，全部在主线程回调。 */
    public interface Listener {
        void onState(ConnState state, String info);

        /** 连接建立（含重连）。上层在这里订阅主题、发起设备发现。 */
        void onConnected(boolean reconnect);

        void onConnectionLost(Throwable cause);

        /** 收到报文（payload 已按 UTF-8 解码）。 */
        void onMessage(String topic, String payload);

        /** 发布失败；kind 见 {@link TopicNames#KIND_STATE} 等常量。 */
        void onDeliveryError(String kind, String error);
    }

    private final Context appCtx;
    private final Listener listener;
    private final Handler main = new Handler(Looper.getMainLooper());

    private final ExecutorService connExecutor = newSingleThread("mqtt-conn");
    private final ExecutorService publishExecutor = newSingleThread("mqtt-publish");
    private final AtomicInteger pendingPublish = new AtomicInteger();

    /** 当前配置（由上层在 connect/reconnect 时传入）。 */
    private volatile MqttConfig config;
    private MqttAsyncClient client;

    private volatile ConnState state = ConnState.DISCONNECTED;
    private volatile String stateInfo = "未连接";

    public MqttClient(Context ctx, Listener listener) {
        this.appCtx = ctx.getApplicationContext();
        this.listener = listener;
    }

    private static ExecutorService newSingleThread(String name) {
        return Executors.newSingleThreadExecutor(r -> {
            Thread t = new Thread(r, name);
            t.setDaemon(true);
            return t;
        });
    }

    // ---------------------------------------------------------------- 状态

    public ConnState getState() {
        return state;
    }

    public String getStateInfo() {
        return stateInfo;
    }

    public boolean isConnected() {
        return state == ConnState.CONNECTED;
    }

    public boolean isConnectedOrConnecting() {
        return state == ConnState.CONNECTED || state == ConnState.CONNECTING;
    }

    private void setState(ConnState s, String info) {
        state = s;
        stateInfo = info;
        main.post(() -> listener.onState(s, info));
    }

    // ---------------------------------------------------------------- 连接

    /** 连接入口：实际建连在 mqtt-conn 线程执行。 */
    public void connect(MqttConfig cfg) {
        this.config = cfg;
        connExecutor.execute(this::connectInternal);
    }

    /** 配置变更后重连（断开 + 重连都在连接线程里做）。 */
    public void reconnect(MqttConfig cfg) {
        this.config = cfg;
        connExecutor.execute(() -> {
            synchronized (MqttClient.this) {
                disconnectInternal();
                connectInternal();
            }
        });
    }

    public void disconnect() {
        connExecutor.execute(() -> {
            synchronized (MqttClient.this) {
                disconnectInternal();
                setState(ConnState.DISCONNECTED, "已断开");
            }
        });
    }

    private synchronized void connectInternal() {
        MqttConfig cfg = config;
        if (cfg == null) {
            setState(ConnState.ERROR, "缺少连接配置");
            return;
        }
        if (client != null && (state == ConnState.CONNECTED || state == ConnState.CONNECTING)) {
            return;
        }
        // 先彻底关闭可能残留的旧客户端，避免同一 clientId 出现两条 TCP 连接被 broker 互相顶掉
        if (client != null) {
            Log.w(TAG, "connect(): 清理残留旧客户端 state=" + state);
            disconnectInternal();
        }
        try {
            client = new MqttAsyncClient(cfg.brokerUri(),
                    cfg.clientId == null || cfg.clientId.isEmpty()
                            ? MqttAsyncClient.generateClientId() : cfg.clientId,
                    new MemoryPersistence());

            MqttConnectOptions opts = new MqttConnectOptions();
            if (cfg.username != null && !cfg.username.isEmpty()) {
                opts.setUserName(cfg.username);
            }
            if (cfg.password != null) {
                opts.setPassword(cfg.password.toCharArray());
            }
            // 洁净会话：本地不持久化任何会话状态；每次连上都会重新订阅并广播发现。
            opts.setCleanSession(true);
            opts.setKeepAliveInterval(Topics.KEEP_ALIVE);
            opts.setConnectionTimeout(15);
            opts.setAutomaticReconnect(cfg.autoReconnect);
            opts.setMqttVersion(MqttConnectOptions.MQTT_VERSION_3_1_1);
            if (cfg.tls) {
                // ssl:// 下 Paho 用系统 CA + 证书链校验；主机名必须与证书 SAN 一致
                opts.setHttpsHostnameVerificationEnabled(true);
            }

            client.setCallback(new MqttCallbackExtended() {
                @Override
                public void connectComplete(boolean reconnect, String serverURI) {
                    setState(ConnState.CONNECTED, "已连接 " + serverURI);
                    main.post(() -> listener.onConnected(reconnect));
                }

                @Override
                public void connectionLost(Throwable cause) {
                    int rc = reasonCode(cause);
                    Log.w(TAG, "connectionLost rc=" + rc + " clientId=" + clientIdNow(), cause);
                    String msg;
                    if (rc == 32109) {
                        msg = "连接被服务端断开（同 clientId 冲突或网络中断），正在重连…";
                    } else if (cause == null) {
                        msg = "连接断开";
                    } else {
                        msg = "连接断开: " + cause.getMessage();
                    }
                    setState(cfg.autoReconnect ? ConnState.CONNECTING : ConnState.ERROR, msg);
                    main.post(() -> listener.onConnectionLost(cause));
                }

                @Override
                public void messageArrived(String topic, MqttMessage message) {
                    String payload;
                    try {
                        payload = new String(message.getPayload(), StandardCharsets.UTF_8);
                    } catch (Exception e) {
                        payload = "";
                    }
                    final String p = payload;
                    main.post(() -> listener.onMessage(topic, p));
                }

                @Override
                public void deliveryComplete(IMqttDeliveryToken token) {
                }
            });

            setState(ConnState.CONNECTING, "正在连接 " + cfg.brokerUri() + " …");
            Log.i(TAG, "connect: broker=" + cfg.brokerUri() + " clientId=" + clientIdNow());
            client.connect(opts, null, new IMqttActionListener() {
                @Override
                public void onSuccess(IMqttToken asyncActionToken) {
                    // 成功处理在 connectComplete
                }

                @Override
                public void onFailure(IMqttToken asyncActionToken, Throwable exception) {
                    int rc = reasonCode(exception);
                    Log.e(TAG, "connect onFailure rc=" + rc, exception);
                    setState(ConnState.ERROR, cfg.tls && isTlsProblem(exception)
                            ? tlsFailText(exception) : connectFailText(rc, exception));
                }
            });
        } catch (MqttException e) {
            Log.e(TAG, "connect failed uri=" + cfg.brokerUri(), e);
            setState(ConnState.ERROR, cfg.tls ? tlsFailText(e) : "连接失败: " + e.getMessage());
        }
    }

    private synchronized void disconnectInternal() {
        if (client != null) {
            try {
                client.setCallback(null);
                if (client.isConnected()) client.disconnect();
            } catch (MqttException ignore) {
            }
            try {
                client.close();
            } catch (MqttException ignore) {
            }
            client = null;
        }
    }

    public String currentClientId() {
        return client == null ? "" : client.getClientId();
    }

    // ---------------------------------------------------------------- 订阅 / 发布

    /** 订阅（在连接建立后的回调里调用，已在 paho 线程，不会阻塞 UI）。 */
    public void subscribe(String[] topics, int qos) {
        MqttAsyncClient c = client;
        if (c == null || !c.isConnected()) return;
        try {
            for (String t : topics) c.subscribe(t, qos);
            Log.i(TAG, "subscribed " + topics.length + " topics");
        } catch (MqttException e) {
            Log.e(TAG, "subscribe failed", e);
        }
    }

    /**
     * 发布：只入队，实际发送在 mqtt-publish 线程执行。
     * <p>调用方多在 UI 线程，而 Paho 在重连/网络抖动时可能阻塞，因此必须异步。</p>
     */
    public void publish(String kind, String topic, String payload) {
        if (pendingPublish.incrementAndGet() > MAX_PENDING_PUBLISH) {
            pendingPublish.decrementAndGet();
            Log.w(TAG, "发布队列已满（" + MAX_PENDING_PUBLISH + "），丢弃: " + topic);
            main.post(() -> listener.onDeliveryError(kind, "发送队列已满，请检查网络后重试"));
            return;
        }
        try {
            publishExecutor.execute(() -> {
                try {
                    doPublish(kind, topic, payload);
                } finally {
                    pendingPublish.decrementAndGet();
                }
            });
        } catch (RejectedExecutionException e) {
            pendingPublish.decrementAndGet();
            main.post(() -> listener.onDeliveryError(kind, "发布线程不可用"));
        }
    }

    private void doPublish(String kind, String topic, String payload) {
        MqttAsyncClient c = client;
        if (c == null || !c.isConnected()) {
            main.post(() -> listener.onDeliveryError(kind, "未连接，无法发送"));
            return;
        }
        try {
            MqttMessage msg = new MqttMessage(payload.getBytes(StandardCharsets.UTF_8));
            msg.setQos(Topics.QOS);
            c.publish(topic, msg);
            // 发送成功后再打印，避免把被拒绝的报文误记为已发出
            MqttTrace.log("→ PUB", topic, payload);
        } catch (Exception e) {
            Log.e(TAG, "publish failed: " + topic, e);
            final String err = e.getMessage() == null ? "发送失败" : e.getMessage();
            main.post(() -> listener.onDeliveryError(kind, err));
        }
    }

    // ---------------------------------------------------------------- 错误文案

    private String clientIdNow() {
        return client == null ? "" : client.getClientId();
    }

    private static int reasonCode(Throwable t) {
        return t instanceof MqttException ? ((MqttException) t).getReasonCode() : -1;
    }

    /** 异常链里是否有 SSL/TLS 相关错误（握手失败、证书不受信任、主机名不匹配等）。 */
    private static boolean isTlsProblem(Throwable t) {
        for (Throwable c = t; c != null; c = c.getCause()) {
            if (c instanceof javax.net.ssl.SSLException) return true;
            String n = c.getClass().getSimpleName();
            String m = c.getMessage() == null ? "" : c.getMessage();
            if (n.contains("SSL") || n.contains("Cert") || n.contains("Trust")
                    || m.contains("SSL") || m.contains("handshake") || m.contains("certificate")
                    || m.contains("Certificate")) {
                return true;
            }
        }
        return false;
    }

    private static String tlsFailText(Throwable e) {
        String reason = e == null || e.getMessage() == null ? "" : e.getMessage();
        if (reason.contains("Certificate") || reason.contains("certificate")
                || reason.contains("Trust") || reason.contains("PKIX")) {
            return "TLS 握手失败：证书不受信任或证书链不完整，请确认 CA 证书已包含中间证书";
        }
        if (reason.contains("hostname") || reason.contains("Hostname")) {
            return "TLS 握手失败：证书域名与填写的地址不一致，请改用证书上的域名";
        }
        return "TLS 握手失败：请确认 8883 是 TLS 监听、地址用证书域名、系统时间正确"
                + (reason.isEmpty() ? "" : "（" + reason + "）");
    }

    private static String connectFailText(int rc, Throwable e) {
        switch (rc) {
            case 32104:
                return "连接超时：检查手机网络或 broker 地址";
            case 32100:
                return "未连接：MQTT 凭据（用户名/密码）被拒绝";
            case 32101:
                return "连接状态异常，请重试";
            case 32110:
                return "broker 拒绝连接，请检查端口与协议";
            case 32112:
                return "clientId 非法，请在设置里重新生成";
            default:
                return "连接失败" + (e == null || e.getMessage() == null ? "" : (": " + e.getMessage()));
        }
    }
}
