package www.walle.ai.net;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.List;
import java.util.Locale;
import java.util.Random;
import java.util.UUID;
import java.util.concurrent.CopyOnWriteArrayList;

import www.walle.ai.data.LocalDb;

/**
 * MQTT 门面（facade）：对 UI 暴露「设备 / 状态 / 视频 / 连接状态」这些业务 API，
 * 自身只做编排，具体职责拆给四个组件：
 *
 * <table>
 *   <tr><td>{@link MqttClient}</td><td>连接、重连、订阅、异步发布（Paho 细节）</td></tr>
 *   <tr><td>{@link MessageRouter}</td><td>主题 → 领域事件、QoS1 去重</td></tr>
 *   <tr><td>{@link DeviceRegistry}</td><td>设备清单、状态缓存、选中设备</td></tr>
 *   <tr><td>{@link TopicNames}</td><td>主题命名与解析</td></tr>
 * </table>
 *
 * <p><b>协议</b>：多租户主题 {@code walle/{tenant}/{device}/v1/{resource}}。
 * 请求按语义命名（查询 {@code query}、动作 {@code request}），
 * 应答一律 {@code result} 且只在收到请求后发布；主动上报只有 {@code online} 与 {@code alarm}。
 * 租户 = MQTT 用户名（清洗后）。</p>
 */
public class MqttManager implements MqttClient.Listener, MessageRouter.Sink {

    private static final String TAG = "MqttManager";

    /** 监听业务事件，所有方法均在主线程回调。 */
    public interface Listener {
        default void onConnState(ConnState state, String info) {}

        default void onOnline(JSONObject payload) {}

        default void onDeviceInfo(DeviceInfo device) {}

        /** 由 online 报文新增进列表的设备（尚未拿到 discovery/result 详情） */
        default void onDeviceAdded(DeviceInfo device) {}

        default void onDevicesChanged() {}

        default void onMachineStatus(JSONObject payload) {}

        default void onAlarm(JSONObject payload) {}

        /** capability/result：设备能力（摄像头/麦克风/音响等外设枚举结果） */
        default void onCapability(JSONObject payload) {}

        /** video/start/result：推流启动结果 */
        default void onVideoRequestResult(JSONObject payload) {}

        /** video/stop/result：停止推流结果 */
        default void onVideoStopResult(JSONObject payload) {}

        /** car/motor/result：车控持续命令回执 */
        default void onCarMotorResult(JSONObject payload) {}

        /** car/servo/status/result：舵机状态查询回执 */
        default void onCarServoStatusResult(JSONObject payload) {}

        /** 发布失败；kind 取值见 {@link TopicNames#KIND_STATE} 等常量 */
        default void onDeliveryError(String kind, String error) {}
    }

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
    private final DeviceRegistry registry = new DeviceRegistry();
    private final MessageRouter router = new MessageRouter(this);

    private final MqttClient client;

    private volatile MqttConfig config;
    /** 租户标识 = MQTT 用户名（清洗后）。 */
    private volatile String tenant = "";

    /** 全局保存的推流房间号（6 位数字），控制页每次发起推流时生成，后续拉流复用。 */
    private volatile String videoRoomNumber = "";
    private final Random roomRandom = new Random();

    private MqttManager(Context ctx) {
        this.appCtx = ctx;
        this.store = new MqttConfigStore(ctx);
        this.config = store.load();
        this.tenant = TopicNames.tenantOf(this.config.username);
        this.client = new MqttClient(ctx, this);
        // 本地持久化（N11）：只存告警收件箱；设备状态是实时查询的，不落盘。
        // 放在这里是因为它一定有 Application Context，且早于任何 MQTT 报文到达。
        AlarmStore.get().attach(LocalDb.init(ctx));
    }

    // ---------------------------------------------------------------- 监听

    public void addListener(Listener l) {
        if (!listeners.contains(l)) listeners.add(l);
    }

    public void removeListener(Listener l) {
        listeners.remove(l);
    }

    // ---------------------------------------------------------------- 配置

    public MqttConfig getConfig() {
        return config;
    }

    public boolean hasCredentials() {
        return store.hasCredentials();
    }

    public synchronized void updateConfig(MqttConfig c) {
        boolean needReconnect = !c.brokerUri().equals(config.brokerUri())
                || !str(c.username).equals(str(config.username))
                || !str(c.password).equals(str(config.password))
                || !str(c.clientId).equals(str(config.clientId));
        store.save(c);
        this.config = c;
        this.tenant = TopicNames.tenantOf(c.username);
        if (needReconnect && client.isConnectedOrConnecting()) {
            client.reconnect(c);
        }
    }

    // ---------------------------------------------------------------- 连接

    public void connect() {
        client.connect(config);
    }

    public void disconnect() {
        client.disconnect();
    }

    public ConnState getState() {
        return client.getState();
    }

    public String getStateInfo() {
        return client.getStateInfo();
    }

    public boolean isConnectedOrConnecting() {
        return client.isConnectedOrConnecting();
    }

    public boolean isConnected() {
        return client.isConnected();
    }

    // ---------------------------------------------------------------- 设备清单（委托 DeviceRegistry）

    public List<DeviceInfo> getDevices() {
        return registry.devices();
    }

    public int deviceCount() {
        return registry.count();
    }

    public int onlineCount() {
        return registry.onlineCount();
    }

    public DeviceInfo getDevice(String key) {
        return registry.get(key);
    }

    public String getSelectedKey() {
        return registry.selectedKey();
    }

    public DeviceInfo getSelectedDevice() {
        return registry.selected();
    }

    public void selectDevice(String key) {
        String before = registry.selectedKey();
        registry.select(key);
        if (!str(before).equals(str(registry.selectedKey()))) {
            notifyDevicesChanged();
        }
    }

    public JSONObject getStatus(String key) {
        return registry.status(key);
    }

    public JSONObject getLatestStatus() {
        return registry.latestStatus();
    }

    public boolean isDeviceOnline() {
        return registry.onlineCount() > 0;
    }

    public String getDeviceHostname() {
        DeviceInfo d = registry.selected();
        return d == null ? "" : d.displayName();
    }

    // ---------------------------------------------------------------- 请求（App → 设备）

    /** 最近一次生成并下发给设备的 room_number，未发起过推流时为空串。 */
    public String getVideoRoomNumber() {
        return videoRoomNumber;
    }

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
        } catch (JSONException ignore) {
        }
        client.publish(TopicNames.KIND_CAPABILITY,
                TopicNames.capabilityQuery(tenant, deviceSegment(uuid)), body.toString());
        return rid;
    }

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
        } catch (JSONException ignore) {
        }
        client.publish(TopicNames.KIND_VIDEO_START,
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
        } catch (JSONException ignore) {
        }
        client.publish(TopicNames.KIND_VIDEO_STOP,
                TopicNames.videoStopRequest(tenant, deviceSegment(uuid)), body.toString());
        return rid;
    }

    /**
     * 开启手机麦克风上行：下发 {@code phone/audio/send}，设备收到后从
     * {@code {room}_audio} 的 RTMP 流拉音频播放。
     *
     * <p>单向动作（用户交互即时反馈，不等设备回执）：设备端回执只写日志，
     * App 不订阅 result，避免「点了按钮却没声音」时把等待感放大。</p>
     *
     * @return 本次 request_id；参数不合法时返回空串
     */
    public String sendPhoneAudio(String uuid, String roomNumber) {
        if (uuid == null || uuid.isEmpty()) {
            notifyDeliveryError(TopicNames.KIND_PHONE_AUDIO_SEND, "请先在设备页选择设备");
            return "";
        }
        String room = roomNumber == null ? "" : roomNumber.trim();
        if (!room.matches("[A-Za-z0-9_-]{1,64}")) {
            notifyDeliveryError(TopicNames.KIND_PHONE_AUDIO_SEND, "没有可用的推流房间号");
            return "";
        }
        final String rid = newRequestId();
        JSONObject body = new JSONObject();
        try {
            body.put("uuid", uuid);
            body.put("room_number", room);
            body.put("request_id", rid);
        } catch (JSONException ignore) {
        }
        client.publish(TopicNames.KIND_PHONE_AUDIO_SEND,
                TopicNames.phoneAudioSend(tenant, deviceSegment(uuid)), body.toString());
        return rid;
    }

    /** 关闭手机麦克风上行：下发 {@code phone/audio/stop}，设备停止拉音频流播放。 */
    public String stopPhoneAudio(String uuid, String roomNumber) {
        if (uuid == null || uuid.isEmpty()) {
            notifyDeliveryError(TopicNames.KIND_PHONE_AUDIO_STOP, "请先在设备页选择设备");
            return "";
        }
        final String rid = newRequestId();
        JSONObject body = new JSONObject();
        try {
            body.put("uuid", uuid);
            if (roomNumber != null && !roomNumber.trim().isEmpty()) {
                body.put("room_number", roomNumber.trim());
            }
            body.put("request_id", rid);
        } catch (JSONException ignore) {
        }
        client.publish(TopicNames.KIND_PHONE_AUDIO_STOP,
                TopicNames.phoneAudioStop(tenant, deviceSegment(uuid)), body.toString());
        return rid;
    }

    /**
     * 车控持续命令：{@code car/motor/set}。
     *
     * <p>move 四个方向只允许一个非 0，取值 0 或 [0.5,1.0] m/s；
     * rotate 的 left/right 互斥，都为 false 表示停止旋转。
     * 设备端保存最新状态并以 10Hz 重发 /cmd_vel，500ms 没有有效命令会自动停车。</p>
     *
     * @return 本次 request_id；参数不合法或未选设备时返回空串
     */
    public String sendCarMotorDrive(String uuid, double forward, double back,
                                    double left, double right,
                                    boolean rotateLeft, boolean rotateRight) {
        if (uuid == null || uuid.isEmpty()) {
            notifyDeliveryError(TopicNames.KIND_CAR_MOTOR, "请先在设备页选择设备");
            return "";
        }
        if (!isValidCarDrive(forward, back, left, right, rotateLeft, rotateRight)) {
            notifyDeliveryError(TopicNames.KIND_CAR_MOTOR, "车控参数不合法");
            return "";
        }
        final String rid = newRequestId();
        JSONObject move = new JSONObject();
        JSONObject rotate = new JSONObject();
        JSONObject body = new JSONObject();
        try {
            move.put("forward", forward);
            move.put("back", back);
            move.put("left", left);
            move.put("right", right);
            rotate.put("left", rotateLeft);
            rotate.put("right", rotateRight);
            body.put("uuid", uuid);
            body.put("request_id", rid);
            body.put("move", move);
            body.put("rotate", rotate);
        } catch (JSONException ignore) {
        }
        client.publish(TopicNames.KIND_CAR_MOTOR,
                TopicNames.carMotorSet(tenant, deviceSegment(uuid)), body.toString());
        return rid;
    }

    /** 停止车控：move 全 0 + rotate 都为 false。 */
    public String stopCarMotorDrive(String uuid) {
        return sendCarMotorDrive(uuid, 0, 0, 0, 0, false, false);
    }

    /** 查询默认舵机 ID 1、2 的位置，不读 offset。 */
    public String requestCarServoStatus(String uuid) {
        return requestCarServoStatus(uuid, new int[]{1, 2}, false);
    }

    /**
     * 查询 PWM 舵机状态：{@code car/servo/status/query}。
     *
     * <p>设备端调用 get_state，每个舵机最多阻塞 2 秒；UI 超时应至少 6 秒。
     * 默认不读 offset，避免 2 个舵机最坏等待约 8 秒。</p>
     *
     * @param ids           要查询的舵机 ID，允许 1–4；为空时使用 [1,2]
     * @param includeOffset 是否同时读取 offset
     * @return 本次 request_id；参数不合法或未选设备时返回空串
     */
    public String requestCarServoStatus(String uuid, int[] ids, boolean includeOffset) {
        if (uuid == null || uuid.isEmpty()) {
            notifyDeliveryError(TopicNames.KIND_CAR_SERVO_STATUS, "请先在设备页选择设备");
            return "";
        }
        int[] queryIds = (ids == null || ids.length == 0) ? new int[]{1, 2} : ids;
        boolean[] seen = new boolean[5];
        JSONArray idArray = new JSONArray();
        for (int id : queryIds) {
            if (id < 1 || id > 4 || seen[id]) {
                notifyDeliveryError(TopicNames.KIND_CAR_SERVO_STATUS, "舵机 ID 不合法");
                return "";
            }
            seen[id] = true;
            idArray.put(id);
        }
        final String rid = newRequestId();
        JSONObject body = new JSONObject();
        try {
            body.put("uuid", uuid);
            body.put("request_id", rid);
            body.put("ids", idArray);
            body.put("include_offset", includeOffset);
        } catch (JSONException ignore) {
        }
        client.publish(TopicNames.KIND_CAR_SERVO_STATUS,
                TopicNames.carServoStatusQuery(tenant, deviceSegment(uuid)), body.toString());
        return rid;
    }

    /** App 侧先做一次轻量校验，避免明显非法的报文占用 MQTT。 */
    private static boolean isValidCarDrive(double forward, double back,
                                           double left, double right,
                                           boolean rotateLeft, boolean rotateRight) {
        if (rotateLeft && rotateRight) return false;
        double[] values = {forward, back, left, right};
        int moving = 0;
        for (double value : values) {
            if (Double.isNaN(value) || Double.isInfinite(value)) return false;
            if (value != 0.0) {
                if (value < 0.5 || value > 1.0) return false;
                moving++;
            }
        }
        return moving <= 1;
    }

    /** 在租户内广播设备发现：{@code walle/{tenant}/discovery/query}。返回本次 request_id。 */
    public String requestDeviceInfo() {
        final String rid = newRequestId();
        JSONObject body = new JSONObject();
        try {
            body.put("request_id", rid);
        } catch (JSONException ignore) {
        }
        client.publish(TopicNames.KIND_DISCOVERY, TopicNames.discoveryQuery(tenant), body.toString());
        return rid;
    }

    /** 向当前选中设备查询状态。返回本次 request_id。 */
    public String requestStatus() {
        String key = registry.selectedKey();
        if (key == null || key.isEmpty()) {
            notifyDeliveryError(TopicNames.KIND_STATE, "请先在设备页选择设备");
            return "";
        }
        final String rid = newRequestId();
        JSONObject body = new JSONObject();
        try {
            body.put("uuid", key);
            body.put("request_id", rid);
        } catch (JSONException ignore) {
        }
        client.publish(TopicNames.KIND_STATE,
                TopicNames.stateQuery(tenant, deviceSegment(key)), body.toString());
        return rid;
    }

    /** 主题里的设备段：优先 device_id（稳定），回退 hostname / uuid。 */
    private String deviceSegment(String deviceKey) {
        DeviceInfo d = registry.get(deviceKey);
        return d == null ? TopicNames.sanitize(deviceKey) : d.topicDeviceId();
    }

    private static String newRequestId() {
        return UUID.randomUUID().toString().replace("-", "").substring(0, 8);
    }

    // ---------------------------------------------------------------- MqttClient.Listener

    @Override
    public void onState(ConnState state, String info) {
        for (Listener l : listeners) main.post(() -> l.onConnState(state, info));
    }

    @Override
    public void onConnected(boolean reconnect) {
        // 每次连上（含自动重连）都要重新订阅：cleanSession=true 会话不保留订阅。
        // 这里**不再**广播 discovery —— 设备上线时发布的 retained online 已带完整身份，
        // broker 回放一次就能建好设备列表（N7）。discovery 改为按需（手动刷新/兜底），
        // 由 DevicesViewModel 决定何时发。
        client.subscribe(TopicNames.responseSubscriptions(tenant), Topics.QOS);
    }

    @Override
    public void onConnectionLost(Throwable cause) {
        Log.w(TAG, "connection lost", cause);
        // 状态已由 MqttClient 推送（连接中 / 错误），这里只做记录
    }

    @Override
    public void onMessage(String topic, String payload) {
        router.route(topic, payload);
    }

    @Override
    public void onDeliveryError(String kind, String error) {
        notifyDeliveryError(kind, error);
    }

    // ---------------------------------------------------------------- MessageRouter.Sink

    @Override
    public void onOnline(JSONObject payload) {
        DeviceInfo added = registry.applyOnline(payload);
        for (Listener l : listeners) main.post(() -> l.onOnline(payload));
        if (added != null) {
            for (Listener l : listeners) main.post(() -> l.onDeviceAdded(added));
        }
        notifyDevicesChanged();
    }

    @Override
    public void onDiscoveryResult(JSONObject payload) {
        DeviceInfo d = registry.applyInfo(payload);
        if (d == null) return;
        for (Listener l : listeners) main.post(() -> l.onDeviceInfo(d));
        notifyDevicesChanged();
    }

    @Override
    public void onStateResult(JSONObject payload) {
        registry.putStatus(payload.optString("uuid", ""), payload);
        for (Listener l : listeners) main.post(() -> l.onMachineStatus(payload));
    }

    @Override
    public void onAlarm(JSONObject payload) {
        // 先落到全局告警收件箱（跨页面共享未读数），再分发给页面
        main.post(() -> {
            AlarmStore.get().add(payload);
            for (Listener l : listeners) l.onAlarm(payload);
        });
    }

    @Override
    public void onCapabilityResult(JSONObject payload) {
        for (Listener l : listeners) main.post(() -> l.onCapability(payload));
    }

    @Override
    public void onVideoStartResult(JSONObject payload) {
        for (Listener l : listeners) main.post(() -> l.onVideoRequestResult(payload));
    }

    @Override
    public void onVideoStopResult(JSONObject payload) {
        for (Listener l : listeners) main.post(() -> l.onVideoStopResult(payload));
    }

    @Override
    public void onCarMotorResult(JSONObject payload) {
        for (Listener l : listeners) main.post(() -> l.onCarMotorResult(payload));
    }

    @Override
    public void onCarServoStatusResult(JSONObject payload) {
        for (Listener l : listeners) main.post(() -> l.onCarServoStatusResult(payload));
    }

    @Override
    public void onIgnored(String topic, String reason) {
        Log.w(TAG, "忽略报文 " + topic + "（" + reason + "）");
    }

    // ---------------------------------------------------------------- 工具

    private void notifyDevicesChanged() {
        for (Listener l : listeners) main.post(l::onDevicesChanged);
    }

    private void notifyDeliveryError(String kind, String err) {
        for (Listener l : listeners) main.post(() -> l.onDeliveryError(kind, err));
    }

    private static String str(String s) {
        return s == null ? "" : s;
    }
}
