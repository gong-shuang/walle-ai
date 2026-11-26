package www.walle.ai.ui;

import android.app.Application;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;

import androidx.annotation.NonNull;
import androidx.lifecycle.LiveData;
import androidx.lifecycle.MutableLiveData;

import org.json.JSONObject;

import java.util.Collections;
import java.util.List;

import www.walle.ai.R;
import www.walle.ai.model.CameraCapability;
import www.walle.ai.model.ControlHolder;
import www.walle.ai.model.DeviceCapability;
import www.walle.ai.net.ConnState;
import www.walle.ai.net.MqttManager;
import www.walle.ai.net.TopicNames;

/**
 * 控制页 ViewModel：摄像头能力 + 推流状态机 + request_id 配对 + 超时兜底。
 *
 * <p>播放本身（{@link WhepSession}、渲染器、全屏跳转）仍属于 View 层，留在 Fragment；
 * 这里只维护「此刻应该处于什么状态、应该显示什么文案、要不要开/停播放」。</p>
 */
public class ControlViewModel extends MqttViewModel {

    private static final String TAG = "ControlViewModel";
    private static final long VIDEO_TIMEOUT_MS = 8000L;

    /** 推流阶段。 */
    public enum Phase {
        /** 空闲：显示「点击开始推流」，或正在查询能力 */
        IDLE,
        /** 已下发开始请求，等 result */
        STARTING,
        /** 播放中切换摄像头，等 result */
        SWITCHING,
        /** 播放中 */
        LIVE
    }

    public static final class UiState {
        public final List<CameraCapability.Camera> cameras;
        public final String selectedNode;
        public final boolean connected;
        public final boolean hasDevice;
        public final boolean capabilityLoading;
        public final Phase phase;
        public final String status;
        /** LIVE 时非空：Fragment 据此建立 WhepSession */
        public final String room;
        /** LIVE 时正在推流的摄像头 node */
        public final String activeCamera;
        /** 当前设备的主题键：全屏页下发 video 命令必须用同一个键。 */
        public final String deviceKey;
        /** 麦克风能力（runState/query/response 的 audio.capture） */
        public final DeviceCapability.AudioEnd audioCapture;
        /** 音响/扬声器能力（runState/query/response 的 audio.playback） */
        public final DeviceCapability.AudioEnd audioPlayback;
        /** 当前控制权 holder；null 表示空闲（fail-open，可直接控制）。 */
        public final ControlHolder controlHolder;

        UiState(List<CameraCapability.Camera> cameras, String selectedNode, boolean connected,
                boolean hasDevice, boolean capabilityLoading, Phase phase, String status,
                String room, String activeCamera, String deviceKey,
                DeviceCapability.AudioEnd audioCapture, DeviceCapability.AudioEnd audioPlayback,
                ControlHolder controlHolder) {
            this.cameras = cameras;
            this.selectedNode = selectedNode;
            this.connected = connected;
            this.hasDevice = hasDevice;
            this.capabilityLoading = capabilityLoading;
            this.phase = phase;
            this.status = status;
            this.room = room;
            this.activeCamera = activeCamera;
            this.deviceKey = deviceKey;
            this.audioCapture = audioCapture;
            this.audioPlayback = audioPlayback;
            this.controlHolder = controlHolder;
        }

        public boolean canPlay() {
            return connected && hasDevice;
        }

        /** 当前是否允许控制设备：无 holder（fail-open）或 holder 是自己。 */
        public boolean canControl() {
            return controlHolder == null || controlHolder.self;
        }
    }

    private final Handler handler = new Handler(Looper.getMainLooper());
    private final MutableLiveData<UiState> state = new MutableLiveData<>();
    private final MutableLiveData<String> toast = new MutableLiveData<>();

    private List<CameraCapability.Camera> cameras = Collections.emptyList();
    private DeviceCapability.AudioEnd audioCapture = DeviceCapability.AudioEnd.UNAVAILABLE;
    private DeviceCapability.AudioEnd audioPlayback = DeviceCapability.AudioEnd.UNAVAILABLE;
    private String selectedNode = "";
    private String capDeviceKey = "";
    private boolean capabilityLoading;
    /** 当前设备能力是否已拿到确定性应答：已加载则切回页面不再重查。 */
    private boolean capabilityLoaded;

    private Phase phase = Phase.IDLE;
    private String status = "";
    private String activeDeviceKey = "";
    private String activeRoom = "";
    private String activeCamera = "";
    private String pendingCamera;

    private String pendingCapRid = "";
    private String pendingStartRid = "";
    private String pendingSwitchRid = "";
    private String pendingStopRid = "";

    private boolean handoff;
    private boolean connected;
    private boolean hasDevice;

    private final Runnable videoTimeout = () -> {
        if (pendingStartRid.isEmpty() && pendingSwitchRid.isEmpty()) return;
        Log.w(TAG, "推流请求超时 start=" + pendingStartRid + " switch=" + pendingSwitchRid);
        pendingStartRid = "";
        pendingSwitchRid = "";
        pendingCamera = null;
        phase = Phase.IDLE;
        activeDeviceKey = "";
        activeRoom = "";
        activeCamera = "";
        status = "设备未响应（8s 超时），请检查连接后重试";
        publish();
    };

    public ControlViewModel(@NonNull Application app) {
        super(app);
        connected = mgr.isConnected();
        hasDevice = !mgr.getSelectedKey().isEmpty();
        status = app.getString(R.string.video_querying_capability);
        publish();
    }

    public LiveData<UiState> state() {
        return state;
    }

    public LiveData<String> toast() {
        return toast;
    }

    public void clearToast() {
        emit(toast, null);
    }

    // ---------------------------------------------------------------- 生命周期

    public void onScreenVisible() {
        if (phase == Phase.LIVE) {
            // 从全屏页返回 / 切回本 tab：推流未停，重发一次状态让 Fragment 重建播放器
            publish();
            return;
        }
        // 能力已加载或查询在飞、且设备未变：不重查（点选设备时已查过一次，见 onDeviceSwitched）
        if (capDeviceKey.equals(mgr.getSelectedKey()) && (capabilityLoaded || capabilityLoading)) return;
        queryCapability();
    }

    /**
     * 设备列表里显式点了某台设备：立即查一次能力（与状态页的 baseState/query/request 配对）。
     *
     * <p>只要用户点设备就发 —— 不做「能力已加载就跳过」的去重，保证每次点击都正好是
     * baseState/query/request + runState/query/request 两个主题。后台的设备上线 / 列表变化广播不会走到这里。</p>
     */
    public void onDeviceSwitched() {
        if (phase == Phase.LIVE && mgr.getSelectedKey().equals(activeDeviceKey)) return;  // 推流中的设备不打断
        queryCapability();
    }

    /** Tab 切走：保持推流，不下发 stream/stop；本地播放器由 Fragment 自行释放。 */
    public void onScreenHidden() {
    }

    public void onDestroyed() {
        stopStream(true);
    }

    /** 跳转全屏播放页：推流所有权交给 PlayerActivity，onStop 不再下发 stop。 */
    public void onHandoffToFullscreen() {
        handoff = true;
        pendingStartRid = "";
        pendingSwitchRid = "";
        pendingStopRid = "";
        pendingCamera = null;
        handler.removeCallbacks(videoTimeout);
        phase = Phase.IDLE;
        activeDeviceKey = "";
        activeRoom = "";
        activeCamera = "";
        status = app().getString(R.string.video_fullscreen_playing);
        publish();
    }

    /** Fragment 在 onStop 时调用；只清 handoff 标记，不下发 stop（离开页面不打断推流）。 */
    public void onScreenStopped() {
        handoff = false;
    }

    // ---------------------------------------------------------------- 能力查询

    public void queryCapability() {
        String deviceKey = mgr.getSelectedKey();
        capDeviceKey = deviceKey;
        selectedNode = "";
        capabilityLoaded = false;
        if (deviceKey.isEmpty()) {
            cameras = Collections.emptyList();
            audioCapture = DeviceCapability.AudioEnd.UNAVAILABLE;
            audioPlayback = DeviceCapability.AudioEnd.UNAVAILABLE;
            hasDevice = false;
            capabilityLoading = false;
            status = app().getString(R.string.video_no_device);
            publish();
            return;
        }
        hasDevice = true;
        capabilityLoading = true;
        cameras = Collections.emptyList();
        status = app().getString(R.string.video_querying_capability);
        pendingCapRid = mgr.requestCapability(deviceKey);
        publish();
    }

    @Override
    public void onCapability(JSONObject payload) {
        if (payload == null) return;
        if (!mgr.isFromDevice(capDeviceKey, payload)) return;
        String rid = payload.optString("request_id", "");
        if (!pendingCapRid.isEmpty() && !rid.isEmpty() && !pendingCapRid.equals(rid)) {
            Log.w(TAG, "忽略不匹配的能力结果 request_id=" + rid + "（等待 " + pendingCapRid + "）");
            return;
        }
        pendingCapRid = "";
        capabilityLoading = false;
        capabilityLoaded = true;      // 拿到确定性应答（无论有无摄像头），本设备不再重查

        DeviceCapability cap = DeviceCapability.parse(payload);
        cameras = cap.cameras;
        audioCapture = cap.audioCapture;
        audioPlayback = cap.audioPlayback;
        if (cameras.isEmpty()) {
            selectedNode = "";
            // V1.11：摄像头由设备端在收到 stream/start 时自动选择，这里不再要求能力里有摄像头
            status = app().getString(R.string.video_tap_to_play);
            if (!cap.probeError.isEmpty()) Log.w(TAG, "探测异常: " + cap.probeError);
        } else {
            selectedNode = cameras.get(0).node;
            status = app().getString(R.string.video_cameras_found, cameras.size());
        }
        publish();
    }

    // ---------------------------------------------------------------- 控制权租约

    /** 获取/续租控制权。 */
    public void acquireControl() {
        String deviceKey = mgr.getSelectedKey();
        if (deviceKey.isEmpty()) {
            emit(toast, app().getString(R.string.video_no_device));
            return;
        }
        String rid = mgr.acquireControl(deviceKey);
        if (rid.isEmpty()) return;
        status = "正在获取控制权…";
        publish();
    }

    /** 释放控制权（只有持有者本人能释放）。 */
    public void releaseControl() {
        String deviceKey = mgr.getSelectedKey();
        if (deviceKey.isEmpty()) {
            emit(toast, app().getString(R.string.video_no_device));
            return;
        }
        String rid = mgr.releaseControl(deviceKey);
        if (rid.isEmpty()) return;
        status = "正在释放控制权…";
        publish();
    }

    @Override
    public void onControlAcquireResult(JSONObject payload) {
        if (payload == null || !mgr.isFromDevice(mgr.getSelectedKey(), payload)) return;
        int result = payload.optInt("result", -1);
        status = result == 0
                ? "已获取控制权"
                : "获取控制权失败：" + payload.optString("cause", "result=" + result);
        publish();
    }

    @Override
    public void onControlReleaseResult(JSONObject payload) {
        if (payload == null || !mgr.isFromDevice(mgr.getSelectedKey(), payload)) return;
        int result = payload.optInt("result", -1);
        status = result == 0
                ? "已释放控制权"
                : "释放控制权失败：" + payload.optString("cause", "result=" + result);
        publish();
    }

    /** 控制被拒绝时的可读文案：优先展示 holder 名称。 */
    private String controlDeniedText(JSONObject payload) {
        ControlHolder holder = mgr.getControlHolder(mgr.getSelectedKey());
        String cause = payload == null ? "" : payload.optString("cause", "");
        if (cause.isEmpty()) cause = app().getString(R.string.lease_occupied);
        return holder == null ? cause : cause + "（" + holder.displayName() + "）";
    }

    // ---------------------------------------------------------------- 用户操作

    public void selectCamera(String node) {
        if (node == null || node.isEmpty() || node.equals(selectedNode)) return;
        // V1.11：设备端固定使用默认摄像头，不再支持 App 端切换
        selectedNode = node;
        publish();
    }

    /** 点击视频区域：空闲时开始推流，推流中则停止（与改造前一致：播放中点击不处理）。 */
    public void togglePlay() {
        if (phase == Phase.LIVE || phase == Phase.STARTING || phase == Phase.SWITCHING) {
            return;
        }
        String deviceKey = mgr.getSelectedKey();
        if (deviceKey.isEmpty()) {
            emit(toast, app().getString(R.string.video_no_device));
            return;
        }
        // V1.11：请求只带 request_id，摄像头与房间号由设备端决定
        if (!mgr.canControl(deviceKey)) {
            emit(toast, controlDeniedText(null));
            return;
        }
        String rid = mgr.startVideoStream(deviceKey);
        if (rid.isEmpty()) {
            return;
        }
        activeDeviceKey = deviceKey;
        activeRoom = "";
        activeCamera = "";
        pendingStartRid = rid;
        phase = Phase.STARTING;
        status = app().getString(R.string.video_starting);
        armTimeout();
        publish();
    }

    // ---------------------------------------------------------------- MQTT 回包

    @Override
    public void onVideoRequestResult(JSONObject payload) {
        if (payload == null) return;
        if (!mgr.isFromDevice(activeDeviceKey, payload)) return;
        String rid = payload.optString("request_id", "");
        int result = payload.optInt("result", -2);

        boolean switchResp = !pendingSwitchRid.isEmpty() && pendingSwitchRid.equals(rid);
        boolean startResp = !pendingStartRid.isEmpty() && pendingStartRid.equals(rid);
        if (!switchResp && !startResp) {
            Log.w(TAG, "忽略不匹配的推流结果 request_id=" + rid
                    + "（等待 start=" + pendingStartRid + " switch=" + pendingSwitchRid + "）");
            return;
        }
        handler.removeCallbacks(videoTimeout);

        if (MqttManager.isControlDenied(payload)) {
            pendingStartRid = "";
            pendingSwitchRid = "";
            pendingCamera = null;
            activeDeviceKey = "";
            activeRoom = "";
            activeCamera = "";
            phase = Phase.IDLE;
            status = controlDeniedText(payload);
            publish();
            return;
        }

        if (switchResp) {
            pendingSwitchRid = "";
            if (result == 0) {
                activeCamera = pendingCamera != null ? pendingCamera : activeCamera;
                selectedNode = activeCamera;
                phase = Phase.LIVE;
                status = "● 直播中";
            } else {
                pendingCamera = null;
                selectedNode = activeCamera;
                phase = Phase.LIVE;
                status = app().getString(R.string.video_start_failed,
                        payload.optString("error", payload.optString("cause", "摄像头切换失败")));
            }
            publish();
            return;
        }

        pendingStartRid = "";
        if (result == 0) {
            // V1.11：房间号由设备端生成，必须从应答里取
            String room = payload.optString("room_number", "");
            if (room.isEmpty()) {
                activeDeviceKey = "";
                activeRoom = "";
                activeCamera = "";
                phase = Phase.IDLE;
                status = app().getString(R.string.video_start_failed, "设备未返回 room_number");
            } else {
                activeRoom = room;
                String camera = payload.optString("camera_device", "");
                if (!camera.isEmpty()) activeCamera = camera;
                phase = Phase.LIVE;
                status = "● 推流中";
            }
        } else {
            activeDeviceKey = "";
            activeRoom = "";
            activeCamera = "";
            phase = Phase.IDLE;
            status = app().getString(R.string.video_start_failed,
                    payload.optString("error", payload.optString("cause", "推流启动失败")));
        }
        publish();
    }

    @Override
    public void onVideoStopResult(JSONObject payload) {
        if (payload == null) return;
        String rid = payload.optString("request_id", "");
        if (!pendingStopRid.isEmpty() && !rid.isEmpty() && !pendingStopRid.equals(rid)) return;
        pendingStopRid = "";
        int result = payload.optInt("result", 0);
        if (result != 0) {
            emit(toast, app().getString(R.string.video_stop_failed,
                    payload.optString("error", "")));
        }
    }

    @Override
    public void onDeliveryError(String kind, String error) {
        if (TopicNames.KIND_VIDEO_START.equals(kind) && phase == Phase.STARTING) {
            phase = Phase.IDLE;
            activeDeviceKey = "";
            activeRoom = "";
            pendingStartRid = "";
            status = error;
            publish();
        } else if (TopicNames.KIND_CAPABILITY.equals(kind)) {
            capabilityLoading = false;
            status = error;
            publish();
        }
    }

    @Override
    public void onDevicesChanged() {
        boolean nowConnected = mgr.isConnected();
        hasDevice = !mgr.getSelectedKey().isEmpty();
        connected = nowConnected;
        if (!mgr.getSelectedKey().equals(capDeviceKey)) {
            // 选中设备变了：清掉旧能力并停掉旧推流，
            // 但**不在这里发查询** —— 查询只由用户显式切换触发（onDeviceSwitched）。
            stopStream(true);
            cameras = Collections.emptyList();
            selectedNode = "";
            capabilityLoaded = false;
            capabilityLoading = false;
            status = app().getString(R.string.video_querying_capability);
        }
        publish();
    }

    @Override
    public void onConnState(ConnState connState, String info) {
        connected = connState == ConnState.CONNECTED;
        publish();
    }

    // ---------------------------------------------------------------- 播放回调（来自 WhepSession）

    public void onPlayerStatus(String text) {
        status = text;
        publish();
    }

    public void onPlayerError(String message) {
        stopStream(true);
        status = app().getString(R.string.video_play_error, message);
        publish();
    }

    public void onPlayerConnectedState(org.webrtc.PeerConnection.PeerConnectionState s) {
        switch (s) {
            case CONNECTED:
                status = "● 推流中";
                break;
            case DISCONNECTED:
                status = "连接中断，尝试恢复…";
                break;
            case FAILED:
                status = "连接失败";
                break;
            case CLOSED:
                status = "连接已关闭";
                break;
            default:
                return;
        }
        publish();
    }

    // ---------------------------------------------------------------- 内部

    private void stopStream(boolean sendStop) {
        pendingStartRid = "";
        pendingSwitchRid = "";
        pendingCamera = null;
        handler.removeCallbacks(videoTimeout);
        if (sendStop && !activeDeviceKey.isEmpty() && !activeRoom.isEmpty()) {
            pendingStopRid = mgr.stopVideoStream(activeDeviceKey, activeRoom);
        }
        activeDeviceKey = "";
        activeRoom = "";
        activeCamera = "";
        phase = Phase.IDLE;
        status = app().getString(R.string.video_tap_to_play);
        publish();
    }

    private void armTimeout() {
        handler.removeCallbacks(videoTimeout);
        handler.postDelayed(videoTimeout, VIDEO_TIMEOUT_MS);
    }

    @Override
    protected void onCleared() {
        handler.removeCallbacksAndMessages(null);
        super.onCleared();
    }

    private Application app() {
        return getApplication();
    }

    private static String orEmpty(String s) {
        return s == null ? "" : s;
    }

    private void publish() {
        if (!onMainThread()) {          // WebRTC 回调可能在 signaling 线程
            runOnMain(this::publish);
            return;
        }
        state.setValue(new UiState(cameras, selectedNode, connected, hasDevice,
                capabilityLoading, phase, status, activeRoom, activeCamera,
                mgr.getSelectedKey(), audioCapture, audioPlayback,
                mgr.getSelectedKey().isEmpty() ? null : mgr.getControlHolder(mgr.getSelectedKey())));
    }
}
