package www.walle.ai.ui;

import android.app.Application;
import android.os.Handler;
import android.os.Looper;

import androidx.annotation.NonNull;
import androidx.lifecycle.LiveData;
import androidx.lifecycle.MutableLiveData;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.Collections;
import java.util.List;

import www.walle.ai.model.CameraCapability;
import www.walle.ai.net.MqttManager;

/**
 * 全屏播放页 ViewModel：摄像头切换状态机 + 状态条文案 + 退出去重。
 *
 * <p>播放链路本身（{@link WhepSession}、EGL、WebRTC 渲染器、首帧遮罩）留在
 * {@link PlayerActivity}，因为那是真正属于 View 的资源；这里只维护
 * 「此刻该显示哪个摄像头、切换请求是否在途、状态条写什么」。</p>
 *
 * <p>状态机与 {@link ControlViewModel} 的 SWITCHING 分支保持一致：</p>
 * <ul>
 *   <li>请求发出<b>前</b>不改 currentCamera，发出成功才切换，避免 UI 先变后被回滚；</li>
 *   <li>result 按 request_id 严格配对（设备端已保证每条 result 都回显）；</li>
 *   <li>result 失败或 8 秒内没有 result → 回滚到旧 node + 提示。</li>
 * </ul>
 *
 * <p>放在 ViewModel 里还有一个实际好处：页面重建（进程内配置变化）时切换中的状态不丢，
 * 不会出现「重建后 spinner 显示新摄像头、设备其实还在旧摄像头推流」的错位。</p>
 */
public class PlayerViewModel extends MqttViewModel {

    private static final String TAG = "PlayerViewModel";

    /** 切换请求的兜底超时：设备端 ffmpeg 重启一般 1~3 秒，8 秒足够。 */
    private static final long SWITCH_TIMEOUT_MS = 8000L;

    /** 舵机状态查询的兜底超时：协议建议默认 6 秒（只读位置、不读 offset）。 */
    private static final long SERVO_QUERY_TIMEOUT_MS = 6000L;

    /** 舵机绝对位置范围（协议 4.12）。 */
    private static final int SERVO_MIN = 800;
    private static final int SERVO_MAX = 2200;
    private static final int SERVO_DEF = 1500;
    /** 舵机动作时长：每步 1 秒走完，与按住时的 1 秒重发周期对齐。 */
    private static final double SERVO_DURATION_SEC = 1.0;

    /** 渲染所需的最小状态快照（不可变）。 */
    public static final class UiState {
        /** 设备端上报的摄像头列表（可能为空：老固件不返回或探测失败）。 */
        public final List<CameraCapability.Camera> cameras;
        /** spinner 应选中的下标；-1 表示列表里找不到当前 node。 */
        public final int selectedIndex;
        /** 当前正在推流的 node。 */
        public final String currentCamera;
        /** true = 已发切换请求、等 result，期间忽略新的选择。 */
        public final boolean switching;
        /** 状态条文案。 */
        public final String status;
        /** 房间号：非空才代表这次播放有真实的 MQTT 会话。 */
        public final String room;
        /** 设备主题键（用于 video/stop/send）。 */
        public final String deviceKey;
        /** 已收到舵机状态回包并缓存到至少一个位置 → 显示右下角舵机摇杆。 */
        public final boolean servoReady;

        UiState(List<CameraCapability.Camera> cameras, int selectedIndex, String currentCamera,
                boolean switching, String status, String room, String deviceKey,
                boolean servoReady) {
            this.cameras = cameras;
            this.selectedIndex = selectedIndex;
            this.currentCamera = currentCamera;
            this.switching = switching;
            this.status = status;
            this.room = room;
            this.deviceKey = deviceKey;
            this.servoReady = servoReady;
        }

        /** 有摄像头且有当前 node 才显示右上角切换下拉。 */
        public boolean showSpinner() {
            return !cameras.isEmpty() && !currentCamera.isEmpty();
        }

        /** 本页是否接管了一路真实推流（退出时需要通知设备端停流）。 */
        public boolean hasStream() {
            return !room.isEmpty() && !deviceKey.isEmpty();
        }
    }

    private final MutableLiveData<UiState> state = new MutableLiveData<>();
    private final Handler handler = new Handler(Looper.getMainLooper());

    private List<CameraCapability.Camera> cameras = Collections.emptyList();
    private String deviceKey = "";
    /** 设备真实 uuid：只用于和 result 里的 uuid 做宽松校验，不用于主题。 */
    private String deviceUuid = "";
    private String room = "";
    private String currentCamera = "";

    /** 切换前的旧 node，result 失败时回滚到这里。 */
    private String pendingCamera;
    private boolean waiting;
    private String waitingRid = "";
    private String status = "";

    private boolean loaded;
    /** 退出时是否已经下发过 stop（避免 onCleared 重复发布）。 */
    private boolean stopped;

    // —— 控制相机（舵机状态查询）——
    /** 开关状态：配置变化后由 Activity 恢复到界面上。 */
    private boolean cameraControlEnabled;
    /** 最近一次查询到的舵机位置，下标 1–4；-1 表示尚未取得。 */
    private final int[] servoPositions = {-1, -1, -1, -1, -1};
    /** 在途查询的 request_id；空串表示没有在途查询。 */
    private String servoQueryRid = "";

    private final Runnable servoQueryTimeout = new Runnable() {
        @Override
        public void run() {
            if (servoQueryRid.isEmpty()) return;
            servoQueryRid = "";
            status = "查询舵机位置超时";
            publish();
        }
    };

    private final Runnable switchTimeout = new Runnable() {
        @Override
        public void run() {
            if (!waiting) return;
            rollback("切换超时，已恢复原摄像头");
            publish();
        }
    };

    public PlayerViewModel(@NonNull Application app) {
        super(app);
    }

    public LiveData<UiState> state() {
        return state;
    }

    /**
     * 页面打开时装载 intent 参数（幂等：ViewModel 存活期间只生效一次）。
     *
     * @param deviceKey 设备主题键（{@code MqttManager.getSelectedKey()}），发 MQTT 用
     * @param deviceUuid 设备真实 uuid，仅用于校验 result
     * @param room      房间号，空表示使用固定演示地址
     * @param camera    控制页当前正在推流的 node
     * @param camerasJson 摄像头列表裸数组 JSON
     */
    public void load(String deviceKey, String deviceUuid, String room, String camera,
                     String camerasJson) {
        if (loaded) return;
        loaded = true;
        this.deviceKey = orEmpty(deviceKey);
        this.deviceUuid = orEmpty(deviceUuid);
        this.room = orEmpty(room);
        this.currentCamera = orEmpty(camera);
        this.cameras = CameraCapability.parseArray(camerasJson);
        publish();
    }

    /** 状态条文案（WHEP 回调、首帧兜底等）。可从任意线程调用。 */
    public void postStatus(final String text) {
        handler.post(new Runnable() {
            @Override
            public void run() {
                status = orEmpty(text);
                publish();
            }
        });
    }

    /**
     * 用户在右下角 spinner 里选了第 {@code position} 个摄像头。
     *
     * <p>注意：真正「选中」以设备端 result 为准，所以这里发请求成功后才改 currentCamera；
     * 渲染层每次都会把 spinner 纠回到 state 里的 selectedIndex，用户乱点会自动弹回。</p>
     */
    public void selectCamera(int position) {
        if (position < 0 || position >= cameras.size()) return;
        String node = cameras.get(position).node;
        if (node.isEmpty()) return;
        if (waiting) {
            // 切换在途：忽略新选择，仅重发一次状态让 spinner 弹回
            publish();
            return;
        }
        if (node.equals(currentCamera)) {
            publish();
            return;
        }
        MqttManager.VideoRequest req = mgr.startVideoStream(deviceKey, node, room);
        if (req == null || !req.isSent()) {
            status = "摄像头切换请求未发出";
            publish();
            return;
        }
        pendingCamera = currentCamera;
        currentCamera = node;
        waitingRid = orEmpty(req.requestId);
        waiting = true;
        status = "正在切换摄像头…";
        handler.postDelayed(switchTimeout, SWITCH_TIMEOUT_MS);
        publish();
    }

    // region 控制相机（舵机状态查询）

    /** 控制相机开关：每次打开都查询一次舵机位置，结果缓存在内存中。 */
    public void setCameraControlEnabled(boolean enabled) {
        cameraControlEnabled = enabled;
        if (!enabled) return;
        queryServoStatus();
    }

    /** 开关状态，供 Activity 在重建后恢复界面。 */
    public boolean isCameraControlEnabled() {
        return cameraControlEnabled;
    }

    /** 最近一次查询到的舵机位置；尚未取得（或该舵机读取失败）时返回 -1。 */
    public int getServoPosition(int id) {
        return id >= 1 && id <= 4 ? servoPositions[id] : -1;
    }

    /**
     * 舵机摇杆步进：以缓存位置为基准，1/2 号舵机分别加上 {@code delta1/delta2}。
     *
     * <p>目标超出 800–2200 的舵机不下发（设备端同样只跳过越界项）；
     * 两个都越界或位置未知时整条命令不发。</p>
     *
     * @return true 表示已下发至少一个舵机
     */
    public boolean stepServos(int delta1, int delta2) {
        if (deviceUuid.isEmpty()) return false;
        int target1 = validServoTarget(1, delta1);
        int target2 = validServoTarget(2, delta2);
        if (target1 < 0 && target2 < 0) return false;

        int[] ids;
        int[] positions;
        if (target1 >= 0 && target2 >= 0) {
            ids = new int[]{1, 2};
            positions = new int[]{target1, target2};
        } else if (target1 >= 0) {
            ids = new int[]{1};
            positions = new int[]{target1};
        } else {
            ids = new int[]{2};
            positions = new int[]{target2};
        }
        String rid = mgr.setCarServos(deviceUuid, ids, positions, SERVO_DURATION_SEC);
        if (rid.isEmpty()) return false;
        // 本地先乐观更新，按住时下一秒在上一目标上继续步进
        if (target1 >= 0) servoPositions[1] = target1;
        if (target2 >= 0) servoPositions[2] = target2;
        status = describeServoPositions();
        publish();
        return true;
    }

    private int validServoTarget(int id, int delta) {
        if (delta == 0 || servoPositions[id] < 0) return -1;
        int target = servoPositions[id] + delta;
        return target >= SERVO_MIN && target <= SERVO_MAX ? target : -1;
    }

    private boolean hasServoPosition() {
        for (int id = 1; id <= 4; id++) {
            if (servoPositions[id] >= 0) return true;
        }
        return false;
    }

    private void queryServoStatus() {
        if (deviceUuid.isEmpty()) {
            status = "尚未取得设备 uuid，无法查询舵机位置";
            publish();
            return;
        }
        String rid = mgr.requestCarServoStatus(deviceUuid);
        if (rid.isEmpty()) return;      // MqttManager 已通过 delivery error 提示原因
        servoQueryRid = rid;
        handler.removeCallbacks(servoQueryTimeout);
        handler.postDelayed(servoQueryTimeout, SERVO_QUERY_TIMEOUT_MS);
        status = "正在查询舵机位置…";
        publish();
    }

    @Override
    public void onCarServoStatusResult(JSONObject payload) {
        if (payload == null || servoQueryRid.isEmpty()) return;
        // 严格按 request_id 配对，避免把其它端/上次查询的回包当成自己的
        if (!servoQueryRid.equals(payload.optString("request_id", ""))) return;
        handler.removeCallbacks(servoQueryTimeout);
        servoQueryRid = "";

        JSONArray servos = payload.optJSONArray("servos");
        if (servos != null) {
            for (int i = 0; i < servos.length(); i++) {
                JSONObject servo = servos.optJSONObject(i);
                if (servo == null) continue;
                int id = servo.optInt("id", -1);
                if (id < 1 || id > 4) continue;
                // 只缓存读取成功的舵机；失败时保留上一次的值
                if (servo.optBoolean("available", false)) {
                    servoPositions[id] = servo.optInt("position", -1);
                }
            }
        }

        int result = payload.optInt("result", -1);
        status = result == 0
                ? describeServoPositions()
                : "舵机查询失败：" + payload.optString("cause", "result=" + result);
        publish();
    }

    private String describeServoPositions() {
        StringBuilder sb = new StringBuilder("相机位置：");
        for (int id = 1; id <= 4; id++) {
            if (servoPositions[id] < 0) continue;
            if (sb.length() > 5) sb.append('，');
            if (id == 1) {
                sb.append("垂直").append('=').append(SERVO_DEF - servoPositions[id]);
            }else {
                sb.append("水平").append('=').append(SERVO_DEF - servoPositions[id]);
            }
        }
        return sb.length() > 5 ? sb.toString() : "未读到舵机位置";
    }

    // endregion

    @Override
    public void onVideoRequestResult(JSONObject payload) {
        if (!waiting || payload == null) return;
        // 严格靠 request_id 配对；rid 缺失时才退化为「房间号 + uuid」宽松匹配
        String rid = payload.optString("request_id", "");
        if (!waitingRid.isEmpty() && !waitingRid.equals(rid)) return;
        if (waitingRid.isEmpty() && !rid.isEmpty()) return;
        String resultRoom = payload.optString("room_number", "");
        if (!room.isEmpty() && !resultRoom.isEmpty() && !room.equals(resultRoom)) return;
        String resultUuid = payload.optString("uuid", "");
        if (!deviceUuid.isEmpty() && !resultUuid.isEmpty() && !deviceUuid.equals(resultUuid)) return;

        int result = payload.optInt("result", -99);
        if (result == 0) {
            waiting = false;
            waitingRid = "";
            pendingCamera = null;
            handler.removeCallbacks(switchTimeout);
            status = "● 直播中";
        } else {
            String cause = payload.optString("error",
                    payload.optString("cause", "摄像头切换失败（result=" + result + "）"));
            rollback("切换失败：" + cause);
        }
        publish();
    }

    /** 页面退出：通知设备端停流（只在真正接管了推流时下发，且只发一次）。 */
    public void stopStreamOnExit() {
        if (stopped) return;
        stopped = true;
        handler.removeCallbacks(switchTimeout);
        waiting = false;
        if (!room.isEmpty() && !deviceKey.isEmpty()) {
            mgr.stopVideoStream(deviceKey, room);
        }
    }

    @Override
    protected void onCleared() {
        handler.removeCallbacksAndMessages(null);
        stopped = true; // onDestroy 已停流，onCleared 不再重复发
        super.onCleared();
    }

    // region 内部

    private void rollback(String reason) {
        waiting = false;
        waitingRid = "";
        if (pendingCamera != null && !pendingCamera.equals(currentCamera)) {
            currentCamera = pendingCamera;
        }
        pendingCamera = null;
        status = reason;
        handler.removeCallbacks(switchTimeout);
    }

    private void publish() {
        if (!onMainThread()) {          // WebRTC 回调可能在 signaling 线程
            runOnMain(this::publish);
            return;
        }
        state.setValue(new UiState(cameras, indexOf(currentCamera), currentCamera,
                waiting, status, room, deviceKey, hasServoPosition()));
    }

    private int indexOf(String node) {
        if (node == null || node.isEmpty()) return -1;
        for (int i = 0; i < cameras.size(); i++) {
            if (node.equals(cameras.get(i).node)) return i;
        }
        return -1;
    }

    private static String orEmpty(String s) {
        return s == null ? "" : s;
    }

    // endregion
}
