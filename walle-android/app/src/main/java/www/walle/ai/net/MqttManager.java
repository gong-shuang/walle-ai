package www.walle.ai.net;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;

import www.walle.ai.data.LocalDb;
import www.walle.ai.model.ControlHolder;

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
 * <p><b>协议</b>：多租户主题 {@code walle/{tenant}/device/{device}/{resource}}。
 * 请求-应答对的请求以 {@code .../request} 结尾、应答以 {@code .../response} 结尾；
 * 持续设置且不需要应答的以 {@code .../cmd} 结尾；主动上报为 {@code online} 与 {@code alarm/report}。
 * 租户 = MQTT 用户名（清洗后）。</p>
 */
public class MqttManager implements MqttClient.Listener, MessageRouter.Sink {

    private static final String TAG = "MqttManager";

    /** 监听业务事件，所有方法均在主线程回调。 */
    public interface Listener {
        default void onConnState(ConnState state, String info) {}

        default void onOnline(JSONObject payload) {}

        default void onDeviceInfo(DeviceInfo device) {}

        /** 由 online 报文新增进列表的设备（尚未拿到 discovery/query/response 详情） */
        default void onDeviceAdded(DeviceInfo device) {}

        default void onDevicesChanged() {}

        default void onMachineStatus(JSONObject payload) {}

        default void onAlarm(JSONObject payload) {}

        /** runState/query/response：设备能力（摄像头/麦克风/音响等外设枚举结果） */
        default void onCapability(JSONObject payload) {}

        /** video/start/response：推流启动结果 */
        default void onVideoRequestResult(JSONObject payload) {}

        /** video/stop/response：停止推流结果 */
        default void onVideoStopResult(JSONObject payload) {}

        /** car/servo/status/query/response：舵机状态查询回执 */
        default void onCarServoStatusResult(JSONObject payload) {}

        /** control/acquire/response：获取/续租控制权回执 */
        default void onControlAcquireResult(JSONObject payload) {}

        /** control/release/response：释放控制权回执 */
        default void onControlReleaseResult(JSONObject payload) {}

        /** 发布失败；kind 取值见 {@link TopicNames#KIND_STATE} 等常量 */
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
    private final DeviceRegistry registry = new DeviceRegistry();
    private final MessageRouter router = new MessageRouter(this);
    /** 每台设备当前的控制权 holder；key 为设备主题段。null/缺失表示空闲。 */
    private final Map<String, ControlHolder> controlHolders = new ConcurrentHashMap<>();

    private final MqttClient client;

    private volatile MqttConfig config;
    /** 租户标识 = MQTT 用户名（清洗后）。 */
    private volatile String tenant = "";

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

    /** 开始推流：设备端自动使用默认摄像头并生成 8 位房间号。返回本次 request_id。 */
    public String startVideoStream(String deviceKey) {
        if (deviceKey == null || deviceKey.isEmpty()) {
            notifyDeliveryError(TopicNames.KIND_VIDEO_START, "请先在设备页选择设备");
            return "";
        }
        final String rid = newRequestId();
        JSONObject body = new JSONObject();
        try {
            putRequestMeta(body, rid);
        } catch (JSONException ignore) {
        }
        client.publish(TopicNames.KIND_VIDEO_START,
                TopicNames.videoStartRequest(tenant, deviceSegment(deviceKey)), body.toString());
        return rid;
    }

    /** 查询指定设备的运行状态（含音频能力）。返回本次 request_id。 */
    public String requestCapability(String deviceKey) {
        if (deviceKey == null || deviceKey.isEmpty()) {
            notifyDeliveryError(TopicNames.KIND_CAPABILITY, "请先在设备页选择设备");
            return "";
        }
        final String rid = newRequestId();
        JSONObject body = new JSONObject();
        try {
            putRequestMeta(body, rid);
        } catch (JSONException ignore) {
        }
        client.publish(TopicNames.KIND_CAPABILITY,
                TopicNames.capabilityQuery(tenant, deviceSegment(deviceKey)), body.toString());
        return rid;
    }

    /** 停止推流：带 room_number 时设备端会校验，不一致不会误停。返回 request_id。 */
    public String stopVideoStream(String deviceKey, String roomNumber) {
        if (deviceKey == null || deviceKey.isEmpty()) {
            notifyDeliveryError(TopicNames.KIND_VIDEO_STOP, "请先在设备页选择设备");
            return "";
        }
        final String rid = newRequestId();
        JSONObject body = new JSONObject();
        try {
            if (roomNumber != null && !roomNumber.isEmpty()) body.put("room_number", roomNumber);
            putRequestMeta(body, rid);
        } catch (JSONException ignore) {
        }
        client.publish(TopicNames.KIND_VIDEO_STOP,
                TopicNames.videoStopRequest(tenant, deviceSegment(deviceKey)), body.toString());
        return rid;
    }

    /**
     * 开启手机麦克风上行：下发 {@code phone/audio/send/request}，设备收到后从
     * {@code {room}_audio} 的 WHEP 流拉音频播放。
     *
     * <p>单向动作（用户交互即时反馈，不等设备回执）：设备端回执只写日志，
     * App 不订阅 response，避免「点了按钮却没声音」时把等待感放大。</p>
     *
     * @return 本次 request_id；参数不合法时返回空串
     */
    public String sendPhoneAudio(String deviceKey, String roomNumber) {
        if (deviceKey == null || deviceKey.isEmpty()) {
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
            body.put("room_number", room);
            putRequestMeta(body, rid);
        } catch (JSONException ignore) {
        }
        client.publish(TopicNames.KIND_PHONE_AUDIO_SEND,
                TopicNames.phoneAudioSend(tenant, deviceSegment(deviceKey)), body.toString());
        return rid;
    }

    /** 关闭手机麦克风上行：下发 {@code phone/audio/stop/request}，设备停止拉音频流播放。 */
    public String stopPhoneAudio(String deviceKey, String roomNumber) {
        if (deviceKey == null || deviceKey.isEmpty()) {
            notifyDeliveryError(TopicNames.KIND_PHONE_AUDIO_STOP, "请先在设备页选择设备");
            return "";
        }
        final String rid = newRequestId();
        JSONObject body = new JSONObject();
        try {
            if (roomNumber != null && !roomNumber.trim().isEmpty()) {
                body.put("room_number", roomNumber.trim());
            }
            putRequestMeta(body, rid);
        } catch (JSONException ignore) {
        }
        client.publish(TopicNames.KIND_PHONE_AUDIO_STOP,
                TopicNames.phoneAudioStop(tenant, deviceSegment(deviceKey)), body.toString());
        return rid;
    }

    /**
     * 车控持续命令：{@code car/motor/set/cmd}（无应答）。
     *
     * <p>move 四个方向只允许一个非 0，取值 0 或 [0.5,1.0] m/s；
     * rotate 的 left/right 互斥，都为 false 表示停止旋转。
     * 设备端保存最新状态并以 10Hz 重发 /cmd_vel，500ms 没有有效命令会自动停车。</p>
     *
     * @return 本次 request_id；参数不合法或未选设备时返回空串
     */
    public String sendCarMotorDrive(String deviceKey, double forward, double back,
                                    double left, double right,
                                    boolean rotateLeft, boolean rotateRight) {
        if (deviceKey == null || deviceKey.isEmpty()) {
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
            putRequestMeta(body, rid);
            body.put("move", move);
            body.put("rotate", rotate);
        } catch (JSONException ignore) {
        }
        client.publish(TopicNames.KIND_CAR_MOTOR,
                TopicNames.carMotorSet(tenant, deviceSegment(deviceKey)), body.toString());
        return rid;
    }

    /** 停止车控：move 全 0 + rotate 都为 false。 */
    public String stopCarMotorDrive(String deviceKey) {
        return sendCarMotorDrive(deviceKey, 0, 0, 0, 0, false, false);
    }

    /** 查询默认舵机 ID 1、2 的位置，不读 offset。 */
    public String requestCarServoStatus(String deviceKey) {
        return requestCarServoStatus(deviceKey, new int[]{1, 2}, false);
    }

    /**
     * 查询 PWM 舵机状态：{@code car/servo/status/query/request}。
     *
     * <p>设备端调用 get_state，每个舵机最多阻塞 2 秒；UI 超时应至少 6 秒。
     * 默认不读 offset，避免 2 个舵机最坏等待约 8 秒。</p>
     *
     * @param ids           要查询的舵机 ID，允许 1–4；为空时使用 [1,2]
     * @param includeOffset 是否同时读取 offset
     * @return 本次 request_id；参数不合法或未选设备时返回空串
     */
    public String requestCarServoStatus(String deviceKey, int[] ids, boolean includeOffset) {
        if (deviceKey == null || deviceKey.isEmpty()) {
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
            putRequestMeta(body, rid);
            body.put("ids", idArray);
            body.put("include_offset", includeOffset);
        } catch (JSONException ignore) {
        }
        client.publish(TopicNames.KIND_CAR_SERVO_STATUS,
                TopicNames.carServoStatusQuery(tenant, deviceSegment(deviceKey)), body.toString());
        return rid;
    }

    /** 设置单个舵机到绝对位置，duration 默认 1.0 秒。 */
    public String setCarServo(String deviceKey, int id, int position) {
        return setCarServos(deviceKey, new int[]{id}, new int[]{position}, 1.0);
    }

    /**
     * 设置一个或多个 PWM 舵机的绝对位置：{@code car/servo/set/cmd}（无应答）。
     *
     * <p>position 必须在 800–2200；设备端规则：一个越界、另一个合法时只执行合法项，
     * 两个都越界则整条命令不执行。App 侧只做结构和 duration 校验，越界交给设备端。</p>
     *
     * @return 本次 request_id；参数不合法或未选设备时返回空串
     */
    public String setCarServos(String deviceKey, int[] ids, int[] positions, double duration) {
        if (deviceKey == null || deviceKey.isEmpty()) {
            notifyDeliveryError(TopicNames.KIND_CAR_SERVO_SET, "请先在设备页选择设备");
            return "";
        }
        if (ids == null || positions == null || ids.length == 0
                || ids.length != positions.length || ids.length > 4) {
            notifyDeliveryError(TopicNames.KIND_CAR_SERVO_SET, "舵机参数不合法");
            return "";
        }
        if (Double.isNaN(duration) || duration < 0.1 || duration > 5.0) {
            notifyDeliveryError(TopicNames.KIND_CAR_SERVO_SET, "duration 必须在 0.1–5.0 秒");
            return "";
        }
        boolean[] seen = new boolean[5];
        JSONArray servos = new JSONArray();
        for (int i = 0; i < ids.length; i++) {
            int id = ids[i];
            if (id < 1 || id > 4 || seen[id]) {
                notifyDeliveryError(TopicNames.KIND_CAR_SERVO_SET, "舵机 ID 不合法");
                return "";
            }
            seen[id] = true;
            JSONObject item = new JSONObject();
            try {
                item.put("id", id);
                item.put("position", positions[i]);
            } catch (JSONException ignore) {
            }
            servos.put(item);
        }
        final String rid = newRequestId();
        JSONObject body = new JSONObject();
        try {
            putRequestMeta(body, rid);
            body.put("servos", servos);
            body.put("duration", duration);
        } catch (JSONException ignore) {
        }
        client.publish(TopicNames.KIND_CAR_SERVO_SET,
                TopicNames.carServoSet(tenant, deviceSegment(deviceKey)), body.toString());
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

    /** 在租户内广播设备发现：{@code walle/{tenant}/discovery/query/request}。返回本次 request_id。 */
    public String requestDeviceInfo() {
        final String rid = newRequestId();
        JSONObject body = new JSONObject();
        try {
            putRequestMeta(body, rid);
        } catch (JSONException ignore) {
        }
        client.publish(TopicNames.KIND_DISCOVERY, TopicNames.discoveryQuery(tenant), body.toString());
        return rid;
    }

    // ---------------------------------------------------------------- 控制权租约

    /**
     * 获取/续租设备控制权：{@code control/acquire/request}。
     *
     * <p>设备端 fail-open：没有 holder 时所有人可以直接控制；有人 acquire 后才形成独占锁。
     * 同一 client_id 重复 acquire 会续租（result=0, action=renewed）。</p>
     */
    public String acquireControl(String deviceKey) {
        if (deviceKey == null || deviceKey.isEmpty()) {
            notifyDeliveryError(TopicNames.KIND_CONTROL_ACQUIRE, "请先在设备页选择设备");
            return "";
        }
        final String rid = newRequestId();
        JSONObject body = new JSONObject();
        try {
            putRequestMeta(body, rid);
        } catch (JSONException ignore) {
        }
        client.publish(TopicNames.KIND_CONTROL_ACQUIRE,
                TopicNames.controlAcquireRequest(tenant, deviceSegment(deviceKey)), body.toString());
        return rid;
    }

    /** 释放设备控制权：{@code control/release/request}（只有 holder 本人能释放）。 */
    public String releaseControl(String deviceKey) {
        if (deviceKey == null || deviceKey.isEmpty()) {
            notifyDeliveryError(TopicNames.KIND_CONTROL_RELEASE, "请先在设备页选择设备");
            return "";
        }
        final String rid = newRequestId();
        JSONObject body = new JSONObject();
        try {
            body.put("request_id", rid);
            body.put("client_id", selfClientId());
        } catch (JSONException ignore) {
        }
        client.publish(TopicNames.KIND_CONTROL_RELEASE,
                TopicNames.controlReleaseRequest(tenant, deviceSegment(deviceKey)), body.toString());
        return rid;
    }

    /** 最近一次从 runState / control 应答里看到的 holder；null 表示空闲。 */
    public ControlHolder getControlHolder(String deviceKey) {
        if (deviceKey == null || deviceKey.isEmpty()) return null;
        return controlHolders.get(deviceSegment(deviceKey));
    }

    /** 当前设备是否可控制：无 holder（fail-open）或 holder 是自己。 */
    public boolean canControl(String deviceKey) {
        ControlHolder holder = getControlHolder(deviceKey);
        return holder == null || holder.self;
    }

    /** 设备拒绝控制：应答里带 error_code=control_denied。 */
    public static boolean isControlDenied(JSONObject payload) {
        return payload != null && "control_denied".equals(payload.optString("error_code", ""));
    }

    /** 当前 App 的 client_id（与请求报文里的一致）。 */
    private String selfClientId() {
        return TopicNames.sanitize(config == null ? "" : config.clientId);
    }

    /** 从报文里更新 holder 缓存（报文没有 control_holder 字段时保持原值）。 */
    private void applyControlHolder(JSONObject payload) {
        if (payload == null || !payload.has("control_holder")) return;
        String key = TopicNames.sanitize(payload.optString("device_id", ""));
        if (key.isEmpty() || "unknown".equals(key)) return;
        ControlHolder holder = ControlHolder.parse(
                payload.optJSONObject("control_holder"), selfClientId());
        if (holder == null) {
            controlHolders.remove(key);
        } else {
            controlHolders.put(key, holder);
        }
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
            putRequestMeta(body, rid);
        } catch (JSONException ignore) {
        }
        client.publish(TopicNames.KIND_STATE,
                TopicNames.baseStateQuery(tenant, deviceSegment(key)), body.toString());
        return rid;
    }

    /** 主题里的设备段：优先 device_id（稳定），回退 hostname。 */
    private String deviceSegment(String deviceKey) {
        DeviceInfo d = registry.get(deviceKey);
        return d == null ? TopicNames.sanitize(deviceKey) : d.topicDeviceId();
    }

    /**
     * 判断一条设备报文是否属于指定设备：比较 payload.device_id 与主题设备段。
     * <p>报文缺少 device_id 时不做过滤（兼容异常报文），由 request_id 兜底。</p>
     */
    public boolean isFromDevice(String deviceKey, JSONObject payload) {
        if (deviceKey == null || deviceKey.isEmpty() || payload == null) return true;
        String deviceId = payload.optString("device_id", "");
        if (deviceId.isEmpty()) return true;
        return TopicNames.sanitize(deviceId).equals(deviceSegment(deviceKey));
    }

    /** 所有 App → 设备请求/命令的公共字段：request_id + client_id + client_name。 */
    private void putRequestMeta(JSONObject body, String rid) throws JSONException {
        body.put("request_id", rid);
        body.put("client_id", TopicNames.sanitize(config == null ? "" : config.clientId));
        body.put("client_name", Topics.clientName());
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
        if (payload != null && !payload.optBoolean("online", true)) {
            // 设备掉线：租约缓存已无意义（设备侧会在 presence grace 后释放）
            String key = TopicNames.sanitize(payload.optString("device_id", ""));
            if (!key.isEmpty()) controlHolders.remove(key);
        }
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
        registry.putStatus(payload.optString("device_id", ""), payload);
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
        applyControlHolder(payload);
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
    public void onCarServoStatusResult(JSONObject payload) {
        applyControlHolder(payload);
        for (Listener l : listeners) main.post(() -> l.onCarServoStatusResult(payload));
    }

    @Override
    public void onControlAcquireResult(JSONObject payload) {
        applyControlHolder(payload);
        for (Listener l : listeners) main.post(() -> l.onControlAcquireResult(payload));
    }

    @Override
    public void onControlReleaseResult(JSONObject payload) {
        applyControlHolder(payload);
        for (Listener l : listeners) main.post(() -> l.onControlReleaseResult(payload));
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
