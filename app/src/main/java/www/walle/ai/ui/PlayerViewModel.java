package www.walle.ai.ui;

import android.app.Application;
import android.os.Handler;
import android.os.Looper;

import androidx.annotation.NonNull;
import androidx.lifecycle.LiveData;
import androidx.lifecycle.MutableLiveData;

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

        UiState(List<CameraCapability.Camera> cameras, int selectedIndex, String currentCamera,
                boolean switching, String status, String room, String deviceKey) {
            this.cameras = cameras;
            this.selectedIndex = selectedIndex;
            this.currentCamera = currentCamera;
            this.switching = switching;
            this.status = status;
            this.room = room;
            this.deviceKey = deviceKey;
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
                waiting, status, room, deviceKey));
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
