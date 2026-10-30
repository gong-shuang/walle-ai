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
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Random;
import java.util.TreeMap;
import java.util.UUID;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.atomic.AtomicInteger;

import www.walle.ai.BuildConfig;

/**
 * Paho MQTT 单例管理器：连接、自动重连、订阅、发布，并把所有回调切到主线程分发给 UI。
 *
 * <p><b>协议</b>：多租户主题 {@code walle/{tenant}/{device}/{resource}}，规则见 {@link TopicNames}。
 * 命名约定：请求按语义划分 —— 查询 {@code .../query}、命令 {@code .../exec}、动作 {@code .../request}；
 * 应答一律 {@code .../result}，且只在收到对应请求后发布，只有 {@code online} 与 {@code alarm} 是主动上报。
 * 租户 = MQTT 用户名（清洗后），设备段 = 设备上报的稳定 {@code device_id}。</p>
 *
 * <p><b>设备发现</b>：App 在租户内广播 {@code walle/{tenant}/discovery/query}，
 * 各设备回复 {@code walle/{tenant}/{device}/identity} 并维护 retained 的 {@code .../online}。</p>
 */
public class MqttManager {

    public enum ConnState { DISCONNECTED, CONNECTING, CONNECTED, ERROR }

    /** 监听 MQTT 事件，所有方法均在主线程回调。 */
    public interface Listener {
        default void onConnState(ConnState state, String info) {}
        default void onOnline(JSONObject payload) {}
        default void onDeviceInfo(DeviceInfo device) {}
        /** 由 online 报文新增进列表的设备（尚未拿到 identity 详情） */
        default void onDeviceAdded(DeviceInfo device) {}
        default void onDevicesChanged() {}
        default void onMachineStatus(JSONObject payload) {}
        default void onAlarm(JSONObject payload) {}
        default void onCmdResult(JSONObject payload) {}
        /** camera/capability：摄像头能力（v4l2 枚举结果） */
        default void onCapability(JSONObject payload) {}
        /** video/start/result：推流启动结果 */
        default void onVideoRequestResult(JSONObject payload) {}
        /** video/stop/result：停止推流结果 */
        default void onVideoStopResult(JSONObject payload) {}
        /** 发布失败；kind 取值见 {@link TopicNames#KIND_CMD} 等常量 */
        default void onDeliveryError(String kind, String error) {}
    }

    private static volatile MqttManager instance;

    public static MqttManager get(Context ctx) {
        if (instance == null) {
            synchronized (MqttManager.class) {
                if (instance == null) instance = new MqttManager(ctx.getApplicationContext());
            }
        }
        return instance;
    }

    private final Context appCtx;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final CopyOnWriteArrayList<Listener> listeners = new CopyOnWriteArrayList<>();
    private final MqttConfigStore store;
    /**
     * 连接/断开专用单线程。Paho 的 close()/disconnect() 在 broker 无响应时可能阻塞，
     * 因此所有可能阻塞的连接管理操作都不在调用线程（可能是主线程）执行。
     */
    private final ExecutorService connExecutor = Executors.newSingleThreadExecutor();
    /**
     * 发布线程：Paho 的 publish 会持有客户端内部锁，网络慢或正在重连时可能阻塞，
     * 因此所有 publish 都投递到这个单线程执行器，绝不在 UI 线程上直接调用。
     * 用单线程是为了保持发送顺序（QoS1 的顺序性依赖发布先后）。
     */
    private final ExecutorService publishExecutor = Executors.newSingleThreadExecutor(r -> {
        Thread t = new Thread(r, "mqtt-publish");
        t.setDaemon(true);
        return t;
    });
    /** 待发送上限：断网时避免无限堆积；超限直接报错给 UI。 */
    private static final int MAX_PENDING_PUBLISH = 200;
    private final AtomicInteger pendingPublish = new AtomicInteger();

    private MqttConfig config;
    private MqttAsyncClient client;
    /** 连接状态可能在连接线程被改写、在主线程被读取，因此声明为 volatile。 */
    private volatile ConnState state = ConnState.DISCONNECTED;
    private volatile String stateInfo = "未连接";

    /** 已发现的设备，键为 DeviceInfo.key()（uuid 优先） */
    private final Map<String, DeviceInfo> devices = new LinkedHashMap<>();
    /** 每台设备最近一次 device/detail/result，键同上 */
    private final Map<String, JSONObject> statusByKey = new LinkedHashMap<>();
    /** 当前选中设备键 */
    private volatile String selectedKey = "";
    /**
     * 租户标识 = MQTT 用户名（清洗后）。
     * <p>同一账号即同一租户，Broker 端用 {@code walle/${username}/#} 做 ACL。
     * 家庭多人共用一个账号时，大家看到的是同一批设备。</p>
     */
    private volatile String tenant = "";

    private volatile JSONObject latestStatus = null;

    private MqttManager(Context ctx) {
        this.appCtx = ctx;
        this.store = new MqttConfigStore(ctx);
        this.config = store.load();
        this.tenant = TopicNames.tenantOf(this.config.username);
    }

    // ---------------- Listener ----------------
    public void addListener(Listener l) { if (!listeners.contains(l)) listeners.add(l); }
    public void removeListener(Listener l) { listeners.remove(l); }

    // ---------------- 去重（QoS1 重复投递） ----------------
    /**
     * QoS1 允许 broker 重复投递同一条报文（例如重连后补发、客户端未及时 PUBACK），
     * 而「命令结果 / 视频结果 / 告警 / 能力查询」都是一次性事件，重复处理会直接出问题
     * （推流结果重复 → 播放器被初始化两次 → {@code videoRendererAlready initialized} 崩溃）。
     * 这里按「业务字段规范化后的签名」做短窗口去重。
     */
    private static final long DEDUPE_WINDOW_MS = 2000L;
    private static final int DEDUPE_CACHE_SIZE = 128;

    private final Map<String, Long> recentEvents = new LinkedHashMap<String, Long>(
            16, 0.75f, false) {
        @Override
        protected boolean removeEldestEntry(Map.Entry<String, Long> eldest) {
            return size() > DEDUPE_CACHE_SIZE;
        }
    };

    /** 事件签名：把 JSON 的键排序后拼接，保证同一份报文得到同一个签名。 */
    private static String eventSignature(String kind, JSONObject json) {
        if (json == null) return kind;
        TreeMap<String, String> normalized = new TreeMap<>();
        for (Iterator<String> it = json.keys(); it.hasNext(); ) {
            String key = it.next();
            normalized.put(key, String.valueOf(json.opt(key)));
        }
        return kind + "|" + normalized;
    }

    /** 返回 true 表示这是 2 秒内已经处理过的同一条事件，应当丢弃。 */
    private synchronized boolean isDuplicate(String kind, JSONObject json) {
        long now = android.os.SystemClock.elapsedRealtime();
        String sig = eventSignature(kind, json);
        Long last = recentEvents.get(sig);
        if (last != null && now - last < DEDUPE_WINDOW_MS) {
            return true;
        }
        recentEvents.put(sig, now);
        // 顺手清理过期项，避免长跑后占满
        if (recentEvents.size() > DEDUPE_CACHE_SIZE / 2) {
            Iterator<Map.Entry<String, Long>> it = recentEvents.entrySet().iterator();
            while (it.hasNext()) {
                if (now - it.next().getValue() > DEDUPE_WINDOW_MS * 5) it.remove();
            }
        }
        return false;
    }

    // ---------------- Config ----------------
    public MqttConfig getConfig() { return config; }

    /** 本地是否已保存用户名 + 密码（首次安装或清除数据后为 false）。 */
    public boolean hasCredentials() { return store.hasCredentials(); }

    public synchronized void updateConfig(MqttConfig c) {
        boolean needReconnect = !c.brokerUri().equals(config.brokerUri())
                || !str(c.username).equals(str(config.username))
                || !str(c.password).equals(str(config.password))
                || !str(c.clientId).equals(str(config.clientId));
        store.save(c);
        this.config = c;
        this.tenant = TopicNames.tenantOf(c.username);
        if (needReconnect && isConnectedOrConnecting()) {
            // 断开 + 重连包含可能阻塞的 close()/connect()，统一切到连接线程执行，
            // 避免在设置页保存配置时卡住主线程。
            connExecutor.execute(() -> {
                synchronized (MqttManager.this) {
                    disconnectInternal();
                    connectInternal();
                }
            });
        }
    }

    // ---------------- State getters ----------------
    public ConnState getState() { return state; }
    public String getStateInfo() { return stateInfo; }
    public boolean isConnectedOrConnecting() {
        return state == ConnState.CONNECTED || state == ConnState.CONNECTING;
    }
    public boolean isConnected() { return state == ConnState.CONNECTED; }

    // ---------------- 设备清单 ----------------
    /** 已发现设备（在线优先，其次按名称） */
    public synchronized List<DeviceInfo> getDevices() {
        List<DeviceInfo> list = new ArrayList<>(devices.values());
        Collections.sort(list, (a, b) -> {
            if (a.online != b.online) return a.online ? -1 : 1;
            return a.displayName().compareToIgnoreCase(b.displayName());
        });
        return list;
    }

    public synchronized int deviceCount() { return devices.size(); }

    public synchronized int onlineCount() {
        int n = 0;
        for (DeviceInfo d : devices.values()) if (d.online) n++;
        return n;
    }

    public synchronized DeviceInfo getDevice(String key) {
        return key == null ? null : devices.get(key);
    }

    public String getSelectedKey() { return selectedKey; }

    public synchronized DeviceInfo getSelectedDevice() {
        return devices.get(selectedKey);
    }

    public synchronized void selectDevice(String key) {
        if (key != null && !key.equals(selectedKey)) {
            selectedKey = key;
            notifyDevicesChanged();
        }
    }

    /** 选中设备变化后，若原先没有设备则自动选第一台 */
    private void ensureSelectedLocked() {
        if (!selectedKey.isEmpty() && devices.containsKey(selectedKey)) return;
        selectedKey = "";
        for (DeviceInfo d : devices.values()) {
            selectedKey = d.key();
            break;
        }
    }

    public synchronized JSONObject getStatus(String key) {
        return key == null ? null : statusByKey.get(key);
    }

    public JSONObject getLatestStatus() { return latestStatus; }

    // 兼容旧接口：返回选中设备（或第一台）的信息
    public boolean isDeviceOnline() { return onlineCount() > 0; }
    public String getDeviceHostname() {
        DeviceInfo d = getSelectedDevice();
        return d == null ? "" : d.displayName();
    }

    // ---------------- Connect / Disconnect ----------------
    /**
     * 连接入口：实际建连放到连接线程执行。
     * <p>原实现在调用线程（可能是主线程）里同步创建客户端、清理旧客户端，
     * 而 Paho 的 close()/disconnect() 在 broker 无响应时可能阻塞数秒，会造成 UI 卡顿/ANR。</p>
     */
    public void connect() {
        connExecutor.execute(this::connectInternal);
    }

    private synchronized void connectInternal() {
        if (client != null && (state == ConnState.CONNECTED || state == ConnState.CONNECTING)) {
            return;
        }
        // 关键：先彻底关闭可能残留的旧客户端，避免同一 clientId 出现两条 TCP 连接，
        // 被 broker 互相顶掉（表现就是「已连接 / 连接断开」反复交替）。
        if (client != null) {
            Log.w("MqttManager", "connect(): 清理残留旧客户端 state=" + state);
            disconnectInternal();
        }
        try {
            client = new MqttAsyncClient(config.brokerUri(),
                    config.clientId == null || config.clientId.isEmpty()
                            ? MqttAsyncClient.generateClientId() : config.clientId,
                    new MemoryPersistence());

            MqttConnectOptions opts = new MqttConnectOptions();
            if (config.username != null && !config.username.isEmpty()) {
                opts.setUserName(config.username);
            }
            if (config.password != null) {
                opts.setPassword(config.password.toCharArray());
            }
            opts.setCleanSession(false);
            opts.setKeepAliveInterval(Topics.KEEP_ALIVE);
            opts.setConnectionTimeout(15);
            opts.setAutomaticReconnect(config.autoReconnect);
            opts.setMqttVersion(MqttConnectOptions.MQTT_VERSION_3_1_1);
            if (config.tls) {
                // ssl:// 下 Paho 用系统 CA + 证书链校验；主机名必须与证书 SAN 一致，
                // 否则会抛 SSLHandshakeException（用 IP 直连域名证书时最常见）。
                opts.setHttpsHostnameVerificationEnabled(true);
            }

            client.setCallback(new MqttCallbackExtended() {
                @Override public void connectComplete(boolean reconnect, String serverURI) {
                    setState(ConnState.CONNECTED, "已连接 " + serverURI);
                    subscribeAll();
                    // 打开 App / 重连成功后立即扫描设备
                    requestDeviceInfo();
                }
                @Override public void connectionLost(Throwable cause) {
                    int rc = reasonCode(cause);
                    Log.w("MqttManager", "connectionLost rc=" + rc + " clientId=" + clientIdNow(),
                            cause);
                    String msg;
                    if (rc == 32109) {
                        // 服务端主动断开：最常见原因是同 clientId 的另一条连接把本连接顶掉
                        msg = "连接被服务端断开（同 clientId 冲突或网络中断），正在重连…";
                    } else if (cause == null) {
                        msg = "连接断开";
                    } else {
                        msg = "连接断开: " + cause.getMessage();
                    }
                    setState(config.autoReconnect ? ConnState.CONNECTING : ConnState.ERROR, msg);
                }
                @Override public void messageArrived(String topic, MqttMessage message) {
                    dispatch(topic, message);
                }
                @Override public void deliveryComplete(IMqttDeliveryToken token) { }
            });

            setState(ConnState.CONNECTING, "正在连接 " + config.brokerUri() + " …");
            Log.i("MqttManager", "connect: broker=" + config.brokerUri() + " clientId=" + clientIdNow());
            client.connect(opts, null, new IMqttActionListener() {
                @Override public void onSuccess(IMqttToken asyncActionToken) {
                    // 真正的成功处理在 connectComplete 里
                }
                @Override public void onFailure(IMqttToken asyncActionToken, Throwable exception) {
                    int rc = reasonCode(exception);
                    Log.e("MqttManager", "connect onFailure rc=" + rc, exception);
                    setState(ConnState.ERROR, config.tls && isTlsProblem(exception)
                            ? tlsFailText(exception) : connectFailText(rc, exception));
                }
            });
        } catch (MqttException e) {
            Log.e("MqttManager", "connect failed uri=" + config.brokerUri(), e);
            setState(ConnState.ERROR, config.tls ? tlsFailText(e) : "连接失败: " + e.getMessage());
        }
    }

    /** 断开入口：同样切到连接线程，避免 close()/disconnect() 阻塞调用线程。 */
    public void disconnect() {
        connExecutor.execute(() -> {
            synchronized (MqttManager.this) {
                disconnectInternal();
                setState(ConnState.DISCONNECTED, "已断开");
            }
        });
    }

    private void disconnectInternal() {
        if (client != null) {
            try {
                client.setCallback(null);
                if (client.isConnected()) client.disconnect();
            } catch (MqttException ignore) { }
            try { client.close(); } catch (MqttException ignore) { }
            client = null;
        }
    }

    private String clientIdNow() {
        return client == null ? "" : client.getClientId();
    }

    private static int reasonCode(Throwable t) {
        if (t instanceof MqttException) return ((MqttException) t).getReasonCode();
        return -1;
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

    /** TLS 连接失败的统一提示，把常见坑直接写进状态栏。 */
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
            case 32104: return "连接超时：检查手机网络或 broker 地址";
            case 32100: return "未连接：MQTT 凭据（用户名/密码）被拒绝";
            case 32101: return "连接状态异常，请重试";
            case 32110: return "broker 拒绝连接，请检查端口与协议";
            case 32112: return "clientId 非法，请在设置里重新生成";
            default:
                return "连接失败" + (e == null || e.getMessage() == null ? "" : (": " + e.getMessage()));
        }
    }

    /**
     * 订阅本租户下「设备 → App」的响应主题（见 {@link TopicNames#responseSubscriptions}）。
     * <p>不订阅 {@code walle/{tenant}/#}：MQTT 3.1.1 没有 no-local，订通配子树会把
     * App 自己发布的请求也收回来。</p>
     */
    private void subscribeAll() {
        if (client == null || !client.isConnected()) return;

        // ① 先退订旧版本遗留在会话里的过滤器（cleanSession=false，broker 会一直保留）。
        //    不清理的话：旧版订过 walle/{tenant}/# 与 v1 主题，新版又订精确主题，
        //    同一条 identity 会被投递两次，自发请求也会被 # 收回来。
        String[] stale = TopicNames.staleSubscriptionFilters(tenant);
        try {
            client.unsubscribe(stale);
            Log.i("MqttManager", "cleared " + stale.length + " stale subscriptions");
        } catch (MqttException e) {
            Log.w("MqttManager", "clear stale subscriptions failed", e);
        }

        // ② 再订阅本版本需要的「设备 → App」响应主题
        String[] topics = TopicNames.responseSubscriptions(tenant);
        try {
            for (String t : topics) client.subscribe(t, Topics.QOS);
            Log.i("MqttManager", "subscribed " + topics.length + " response topics under "
                    + TopicNames.ROOT + "/" + TopicNames.sanitize(tenant) + "/+/");
        } catch (MqttException e) {
            Log.e("MqttManager", "subscribe failed", e);
        }
    }

    /** 主题里的设备段：优先 device_id（稳定），回退 hostname / uuid。 */
    private String deviceSegment(String deviceKey) {
        DeviceInfo d = getDevice(deviceKey);
        return d == null ? TopicNames.sanitize(deviceKey) : d.topicDeviceId();
    }

    // ---------------- Publish ----------------
    /** 全局保存的推流房间号（6 位数字），ControlFragment 每次发起推流时生成，后续拉流复用。 */
    private volatile String videoRoomNumber = "";
    private final Random roomRandom = new Random();

    /** 一次视频请求：request_id 用于结果配对，room 为空表示请求未发出。 */
    public static final class VideoRequest {
        public final String requestId;
        public final String room;

        VideoRequest(String requestId, String room) {
            this.requestId = requestId;
            this.room = room;
        }

        public boolean isSent() {
            return !room.isEmpty();
        }
    }

    /** 生成一次请求的 request_id（8 位十六进制），用于与应答配对。 */
    private static String newRequestId() {
        return UUID.randomUUID().toString().replace("-", "").substring(0, 8);
    }

    /** 最近一次生成并下发给设备的 room_number，未发起过推流时为空串。 */
    public String getVideoRoomNumber() { return videoRoomNumber; }

    /** 查询指定设备的摄像头能力。返回本次 request_id。 */
    public String requestCapability(String uuid) {
        if (uuid == null || uuid.isEmpty()) {
            notifyDeliveryError(TopicNames.KIND_CAPABILITY, "请先在设备页选择设备");
            return "";
        }
        final String rid = newRequestId();
        JSONObject body = new JSONObject();
        try {
            body.put("uuid", uuid);
            body.put("request_id", rid);
        } catch (JSONException ignore) { }
        publish(TopicNames.KIND_CAPABILITY,
                TopicNames.capabilityQuery(tenant, deviceSegment(uuid)), body.toString());
        return rid;
    }

    /**
     * 发起视频推流：随机生成 6 位 room_number 并全局保存，随后下发 video/start。
     *
     * @return 本次请求的 request_id 与 room_number；请求未发出时 room 为空串。
     */
    public VideoRequest startVideoStream(String uuid, String videoDevice) {
        return startVideoStream(uuid, videoDevice, null);
    }

    /**
     * 开始推流，可指定房间号。
     * <p>切换摄像头等场景需要沿用原房间号：设备换摄像头后仍推到同一个 room，
     * App 侧继续用同一 WHEP 地址拉流，避免两端房间号不一致导致黑屏。</p>
     *
     * @param roomNumber 非空则复用；为空/非法则重新随机生成
     * @return request_id + 实际使用的房间号；uuid 或视频设备为空时 room 为 ""
     */
    public VideoRequest startVideoStream(String uuid, String videoDevice, String roomNumber) {
        if (uuid == null || uuid.isEmpty()) {
            notifyDeliveryError(TopicNames.KIND_VIDEO_START, "请先在设备页选择设备");
            return new VideoRequest("", "");
        }
        if (videoDevice == null || videoDevice.isEmpty()) {
            notifyDeliveryError(TopicNames.KIND_VIDEO_START, "未选择摄像头设备");
            return new VideoRequest("", "");
        }
        String room = roomNumber == null ? "" : roomNumber.trim();
        if (!room.matches("[A-Za-z0-9_-]{1,64}")) {
            room = String.format(Locale.US, "%06d", roomRandom.nextInt(1_000_000));
        }
        videoRoomNumber = room;
        final String rid = newRequestId();
        JSONObject body = new JSONObject();
        try {
            body.put("uuid", uuid);
            body.put("video_device", videoDevice);
            body.put("room_number", room);
            body.put("request_id", rid);
        } catch (JSONException ignore) { }
        publish(TopicNames.KIND_VIDEO_START,
                TopicNames.videoStartRequest(tenant, deviceSegment(uuid)), body.toString());
        return new VideoRequest(rid, room);
    }

    /** 停止推流：带 room_number 时设备端会校验，不一致不会误停。返回 request_id。 */
    public String stopVideoStream(String uuid, String roomNumber) {
        if (uuid == null || uuid.isEmpty()) {
            notifyDeliveryError(TopicNames.KIND_VIDEO_STOP, "请先在设备页选择设备");
            return "";
        }
        final String rid = newRequestId();
        JSONObject body = new JSONObject();
        try {
            body.put("uuid", uuid);
            if (roomNumber != null && !roomNumber.isEmpty()) body.put("room_number", roomNumber);
            body.put("request_id", rid);
        } catch (JSONException ignore) { }
        publish(TopicNames.KIND_VIDEO_STOP,
                TopicNames.videoStopRequest(tenant, deviceSegment(uuid)), body.toString());
        return rid;
    }

    /** 在租户内广播设备发现：{@code walle/{tenant}/discovery/query}。返回本次 request_id。 */
    public String requestDeviceInfo() {
        final String rid = newRequestId();
        JSONObject body = new JSONObject();
        try {
            body.put("request_id", rid);
        } catch (JSONException ignore) { }
        publish(TopicNames.KIND_DISCOVERY, TopicNames.discoveryQuery(tenant), body.toString());
        return rid;
    }

    /** 向当前选中设备查询状态。 */
    public String requestStatus() {
        String key = selectedKey;
        if (key == null || key.isEmpty()) {
            notifyDeliveryError(TopicNames.KIND_STATE, "请先在设备页选择设备");
            return "";
        }
        final String rid = newRequestId();
        JSONObject body = new JSONObject();
        try {
            body.put("uuid", key);
            body.put("request_id", rid);
        } catch (JSONException ignore) { }
        publish(TopicNames.KIND_STATE,
                TopicNames.stateQuery(tenant, deviceSegment(key)), body.toString());
        return rid;
    }

    /** 向选中设备下发远程命令，返回生成的 request_id（用于结果配对）。 */
    public String execCommand(String cmd, int timeoutSec) {
        String key = selectedKey;
        if (key == null || key.isEmpty()) {
            notifyDeliveryError(TopicNames.KIND_CMD, "请先在设备页选择设备");
            return "";
        }
        String rid = newRequestId();
        JSONObject body = new JSONObject();
        try {
            body.put("uuid", key);
            body.put("cmd", cmd);
            body.put("request_id", rid);
            body.put("timeout", timeoutSec);
        } catch (JSONException ignore) { }
        publish(TopicNames.KIND_CMD,
                TopicNames.cmdExec(tenant, deviceSegment(key)), body.toString());
        return rid;
    }

    /**
     * 发布入口：只做入队，实际发送在 {@link #publishExecutor} 的单线程里执行。
     * <p>调用方多在 UI 线程（按钮点击、查询按钮），而 Paho 在重连/网络抖动时可能阻塞，
     * 所以这里必须异步。队列有上限，断网时不会无限堆积。</p>
     */
    private void publish(String kind, String topic, String payload) {
        if (pendingPublish.incrementAndGet() > MAX_PENDING_PUBLISH) {
            pendingPublish.decrementAndGet();
            Log.w("MqttManager", "发布队列已满（" + MAX_PENDING_PUBLISH + "），丢弃: " + topic);
            notifyDeliveryError(kind, "发送队列已满，请检查网络后重试");
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
            notifyDeliveryError(kind, "发布线程不可用");
        }
    }

    /** 真正的发送（在发布线程执行）。 */
    private void doPublish(String kind, String topic, String payload) {
        MqttAsyncClient c = client;
        if (c == null || !c.isConnected()) {
            notifyDeliveryError(kind, "未连接，无法发送");
            return;
        }
        try {
            MqttMessage msg = new MqttMessage(payload.getBytes("UTF-8"));
            msg.setQos(Topics.QOS);
            c.publish(topic, msg);
            // 发送成功后再打印，避免把被拒绝的报文误记为已发出
            logJson("→ PUB", topic, payload);
        } catch (Exception e) {
            Log.e("MqttManager", "publish failed: " + topic, e);
            notifyDeliveryError(kind, e.getMessage() == null ? "发送失败" : e.getMessage());
        }
    }

    // ---------------- JSON 日志 ----------------
    /** 收发报文日志 TAG，过滤命令：adb logcat -s MqttTrace */
    private static final String TAG_TRACE = "MqttTrace";
    /** Logcat 单条日志上限约 4000 字符，超长 JSON 分段打印避免被截断 */
    private static final int LOGCAT_CHUNK = 3500;

    /**
     * 以格式化（缩进 2 空格）方式打印 MQTT 收发报文。
     * <p>非 JSON 报文按原始文本打印；仅在 Debug 构建输出，Release 不刷屏。</p>
     */
    private static void logJson(String direction, String topic, String payload) {
        if (!BuildConfig.DEBUG) return;
        String raw = payload == null ? "" : payload;
        String pretty;
        try {
            String trimmed = raw.trim();
            if (trimmed.startsWith("{")) {
                pretty = new JSONObject(trimmed).toString(2);
            } else if (trimmed.startsWith("[")) {
                pretty = new JSONArray(trimmed).toString(2);
            } else {
                pretty = trimmed;
            }
        } catch (Exception e) {
            // 非法 JSON：原样输出，便于排查设备端格式问题
            pretty = raw;
        }
        Log.i(TAG_TRACE, direction + " " + topic + " (" + raw.length() + " chars)");
        if (pretty.isEmpty()) {
            Log.i(TAG_TRACE, "<empty payload>");
            return;
        }
        for (int start = 0; start < pretty.length(); start += LOGCAT_CHUNK) {
            Log.i(TAG_TRACE, pretty.substring(start,
                    Math.min(pretty.length(), start + LOGCAT_CHUNK)));
        }
    }

    // ---------------- Dispatch ----------------
    private void dispatch(String topic, MqttMessage message) {
        String payload;
        try {
            payload = new String(message.getPayload(), "UTF-8");
        } catch (Exception e) {
            payload = "";
        }
        logJson("← RECV", topic, payload);
        JSONObject json = null;
        try {
            if (!payload.trim().isEmpty()) json = new JSONObject(payload);
        } catch (JSONException e) {
            Log.w("MqttManager", "non-json on " + topic + ": " + payload);
        }
        // 只处理本协议主题：walle/{tenant}/{device}/{resource}
        if (!TopicNames.isNamespaced(topic)) {
            Log.w("MqttManager", "忽略非本协议主题: " + topic);
            return;
        }
        dispatchNamespaced(topic, json);
    }

    /**
     * 分发 {@code walle/{tenant}/{device}/{resource}}。
     * <p>设备端可能在 payload 里省略 {@code device_id}，这里用主题里的设备段兜底，
     * 保证设备归并不会因为缺字段而退化。</p>
     */
    private void dispatchNamespaced(String topic, JSONObject payload) {
        String[] parts = TopicNames.parse(topic);
        if (parts == null) return;
        String device = parts[1];
        String resource = parts[2];

        JSONObject json = payload == null ? new JSONObject() : payload;
        try {
            if (json.optString("device_id", "").isEmpty()) json.put("device_id", device);
        } catch (JSONException ignore) { }

        switch (resource) {
            // 主动上报
            case "online":
                handleOnline(json);
                break;
            case "alarm":
                handleAlarm(json);
                break;
            // 查询应答（.../result）
            case "discovery/result":
                handleDeviceInfo(json);
                break;
            case "state/result":
                handleMachineStatus(json);
                break;
            case "cmd/result":
                handleCmdResult(json);
                break;
            case "camera/capability/result":
                handleCapability(json);
                break;
            case "video/start/result":
                handleVideoStartResult(json);
                break;
            case "video/stop/result":
                handleVideoStopResult(json);
                break;
            default:
                Log.w("MqttManager", "忽略未知资源: " + resource);
                break;
        }
    }

    // ---- 各资源的处理（v1 / v2 共用，避免两套分发逻辑各写一遍）----

    private void handleMachineStatus(JSONObject json) {
        if (json == null) return;
        final JSONObject s = json;
        String key = json.optString("uuid", "");
        if (!key.isEmpty()) {
            synchronized (this) { statusByKey.put(key, json); }
        }
        latestStatus = json;
        for (Listener l : listeners) main.post(() -> l.onMachineStatus(s));
    }

    private void handleAlarm(JSONObject json) {
        if (json == null || isDuplicate("alarm", json)) return;
        final JSONObject a = json;
        // 先落到全局告警收件箱（跨页面共享未读数），再分发给页面
        main.post(() -> {
            AlarmStore.get().add(a);
            for (Listener l : listeners) l.onAlarm(a);
        });
    }

    private void handleCmdResult(JSONObject json) {
        if (json == null || isDuplicate("cmd", json)) return;
        final JSONObject r = json;
        for (Listener l : listeners) main.post(() -> l.onCmdResult(r));
    }

    private void handleCapability(JSONObject json) {
        if (json == null || isDuplicate("capability", json)) return;
        final JSONObject c = json;
        for (Listener l : listeners) main.post(() -> l.onCapability(c));
    }

    private void handleVideoStartResult(JSONObject json) {
        if (json == null || isDuplicate("video/start", json)) return;
        final JSONObject v = json;
        for (Listener l : listeners) main.post(() -> l.onVideoRequestResult(v));
    }

    private void handleVideoStopResult(JSONObject json) {
        if (json == null || isDuplicate("video/stop", json)) return;
        final JSONObject s = json;
        for (Listener l : listeners) main.post(() -> l.onVideoStopResult(s));
    }

    /** online：上线报文带 uuid，遗嘱/下线报文可能只有 device_id，需要按 device_id 兜底匹配。 */
    private void handleOnline(JSONObject j) {
        if (j == null) return;
        boolean online = j.optBoolean("online", false);
        String uuid = j.optString("uuid", "");
        String did = j.optString("device_id", "");
        String hostname = j.optString("hostname", "");

        // online 已携带完整身份信息，字段与 identity 一致，直接整体解析
        DeviceInfo fresh = DeviceInfo.from(j);
        fresh.online = online;

        DeviceInfo target;
        DeviceInfo added = null;         // 本次新加入列表的设备（online=true 且列表中没有）
        synchronized (this) {
            String key = !uuid.isEmpty() ? uuid : findByDeviceIdLocked(did);
            if (key == null || key.isEmpty()) key = findByHostnameLocked(hostname);
            target = key == null || key.isEmpty() ? null : devices.get(key);

            if (target == null) {
                // 列表里没有这台设备：
                // - online=true  → 新增一台设备（报文已补全 json，直接作为完整身份信息）
                // - online=false → 不处理（broker 残留的 retained 遗嘱不能变成幽灵卡片）
                if (!online) {
                    Log.i("MqttManager", "忽略未匹配的离线报文 hostname=" + hostname
                            + " device_id=" + did + " uuid=" + uuid);
                } else if (fresh.key().isEmpty()) {
                    Log.i("MqttManager", "忽略无身份的 online 上线报文");
                } else {
                    devices.put(fresh.key(), fresh);
                    added = fresh;
                    ensureSelectedLocked();
                }
            } else {
                // 列表里已有这台设备：合并完整字段，只更新状态，不删除设备
                mergeDeviceInfo(target, fresh);
                target.online = online;
                String newKey = fresh.key();
                if (!newKey.isEmpty() && !newKey.equals(key)) {
                    // uuid 每次重启都会变：列表键跟着换，保证后续按 uuid 匹配能命中
                    devices.remove(key);
                    devices.put(newKey, target);
                    JSONObject st = statusByKey.remove(key);
                    if (st != null) statusByKey.put(newKey, st);
                    if (selectedKey.equals(key)) selectedKey = newKey;
                }
            }
        }
        // online 只做状态/列表更新，不发送任何主题（设备清单由 discovery/query 扫描获取）
        final JSONObject payload = j;
        for (Listener l : listeners) main.post(() -> l.onOnline(payload));
        if (added != null) {
            final DeviceInfo a = added;
            for (Listener l : listeners) main.post(() -> l.onDeviceAdded(a));
        }
        notifyDevicesChanged();
    }

    /** 用 online 的完整字段覆盖已有条目，报文里缺失（空）的字段保留原值。 */
    private static void mergeDeviceInfo(DeviceInfo dst, DeviceInfo src) {
        if (!src.uuid.isEmpty()) dst.uuid = src.uuid;
        if (!src.deviceId.isEmpty()) dst.deviceId = src.deviceId;
        if (!src.deviceType.isEmpty()) dst.deviceType = src.deviceType;
        if (!src.vendor.isEmpty()) dst.vendor = src.vendor;
        if (!src.model.isEmpty()) dst.model = src.model;
        if (!src.serial.isEmpty()) dst.serial = src.serial;
        if (!src.hostname.isEmpty()) dst.hostname = src.hostname;
        if (!src.ip.isEmpty()) dst.ip = src.ip;
        if (!src.mac.isEmpty()) dst.mac = src.mac;
        if (!src.osName.isEmpty()) dst.osName = src.osName;
        if (!src.kernel.isEmpty()) dst.kernel = src.kernel;
        if (!src.arch.isEmpty()) dst.arch = src.arch;
        if (!src.cpuModel.isEmpty()) dst.cpuModel = src.cpuModel;
        if (src.coresLogical > 0) dst.coresLogical = src.coresLogical;
        if (src.coresPhysical > 0) dst.coresPhysical = src.coresPhysical;
        if (src.freqMhz != null) dst.freqMhz = src.freqMhz;
        if (src.tempC != null) dst.tempC = src.tempC;
        if (!src.memTotalHuman.isEmpty()) dst.memTotalHuman = src.memTotalHuman;
        if (!src.diskTotalHuman.isEmpty()) dst.diskTotalHuman = src.diskTotalHuman;
        if (!src.uptimeHuman.isEmpty()) dst.uptimeHuman = src.uptimeHuman;
        if (!src.bootTime.isEmpty()) dst.bootTime = src.bootTime;
        if (!src.datetime.isEmpty()) dst.datetime = src.datetime;
        dst.detailLoaded |= src.detailLoaded;
        dst.lastSeenMs = src.lastSeenMs;
    }

    /** identity：设备身份详情，按 uuid 归并（重启换了 uuid 的同一台机器按 device_id 替换）。 */
    private void handleDeviceInfo(JSONObject j) {
        if (j == null) return;
        DeviceInfo d = DeviceInfo.from(j);
        String key = d.key();
        if (key.isEmpty()) return;

        synchronized (this) {
            // 同一台设备重启后 uuid 会变：清掉旧 uuid 的残留条目
            if (!d.deviceId.isEmpty()) {
                List<String> stale = new ArrayList<>();
                for (Map.Entry<String, DeviceInfo> e : devices.entrySet()) {
                    DeviceInfo old = e.getValue();
                    if (!e.getKey().equals(key)
                            && !old.deviceId.isEmpty()
                            && old.deviceId.equals(d.deviceId)) {
                        stale.add(e.getKey());
                    }
                }
                for (String s : stale) {
                    devices.remove(s);
                    statusByKey.remove(s);
                    if (s.equals(selectedKey)) selectedKey = "";
                }
            }
            d.online = true;
            d.lastSeenMs = System.currentTimeMillis();
            devices.put(key, d);
            ensureSelectedLocked();
        }

        final DeviceInfo info = d;
        for (Listener l : listeners) main.post(() -> l.onDeviceInfo(info));
        notifyDevicesChanged();
    }

    private String findByDeviceIdLocked(String deviceId) {
        if (deviceId == null || deviceId.isEmpty()) return "";
        for (Map.Entry<String, DeviceInfo> e : devices.entrySet()) {
            if (deviceId.equals(e.getValue().deviceId)) return e.getKey();
        }
        return "";
    }

    /** 按主机名兜底匹配已有设备（仅用于 online 无 uuid/device_id 可用时的归并）。 */
    private String findByHostnameLocked(String hostname) {
        if (hostname == null || hostname.isEmpty()) return "";
        for (Map.Entry<String, DeviceInfo> e : devices.entrySet()) {
            String h = e.getValue().hostname;
            if (h != null && hostname.equalsIgnoreCase(h)) return e.getKey();
        }
        return "";
    }

    private void notifyDevicesChanged() {
        for (Listener l : listeners) main.post(l::onDevicesChanged);
    }

    // ---------------- Helpers ----------------
    private void setState(ConnState s, String info) {
        state = s;
        stateInfo = info;
        for (Listener l : listeners) main.post(() -> l.onConnState(s, info));
    }

    private void notifyDeliveryError(String kind, String err) {
        for (Listener l : listeners) main.post(() -> l.onDeliveryError(kind, err));
    }

    private static String str(String s) { return s == null ? "" : s; }
}
