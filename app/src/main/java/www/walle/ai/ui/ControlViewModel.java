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
import www.walle.ai.net.ConnState;
import www.walle.ai.net.DeviceInfo;
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
        /** 当前设备的真实 uuid：只用于校验 result 归属。 */
        public final String deviceUuid;

        UiState(List<CameraCapability.Camera> cameras, String selectedNode, boolean connected,
                boolean hasDevice, boolean capabilityLoading, Phase phase, String status,
                String room, String activeCamera, String deviceKey, String deviceUuid) {
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
            this.deviceUuid = deviceUuid;
        }

        public boolean canPlay() {
            return !cameras.isEmpty() && connected && hasDevice;
        }
    }

    private final Handler handler = new Handler(Looper.getMainLooper());
    private final MutableLiveData<UiState> state = new MutableLiveData<>();
    private final MutableLiveData<String> toast = new MutableLiveData<>();

    private List<CameraCapability.Camera> cameras = Collections.emptyList();
    private String selectedNode = "";
    private String capUuid = "";
    private boolean capabilityLoading;

    private Phase phase = Phase.IDLE;
    private String status = "";
    private String activeUuid = "";
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
        activeUuid = "";
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
        if (phase == Phase.LIVE) return;      // 从全屏页返回时不打断
        queryCapability();
    }

    public void onScreenHidden() {
        stopStream(true);
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
        activeUuid = "";
        activeRoom = "";
        activeCamera = "";
        status = app().getString(R.string.video_fullscreen_playing);
        publish();
    }

    /** Fragment 在 onStop 时调用；handoff 期间不下发 stop。 */
    public void onScreenStopped() {
        if (handoff) {
            handoff = false;
            return;
        }
        stopStream(true);
    }

    // ---------------------------------------------------------------- 能力查询

    public void queryCapability() {
        String uuid = mgr.getSelectedKey();
        capUuid = uuid;
        selectedNode = "";
        if (uuid.isEmpty()) {
            cameras = Collections.emptyList();
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
        pendingCapRid = mgr.requestCapability(uuid);
        publish();
    }

    @Override
    public void onCapability(JSONObject payload) {
        if (payload == null) return;
        String uuid = payload.optString("uuid", "");
        if (!uuid.isEmpty() && !uuid.equals(capUuid)) return;
        String rid = payload.optString("request_id", "");
        if (!pendingCapRid.isEmpty() && !rid.isEmpty() && !pendingCapRid.equals(rid)) {
            Log.w(TAG, "忽略不匹配的能力结果 request_id=" + rid + "（等待 " + pendingCapRid + "）");
            return;
        }
        pendingCapRid = "";
        capabilityLoading = false;

        CameraCapability cap = CameraCapability.parse(payload);
        cameras = cap.cameras;
        if (cameras.isEmpty()) {
            selectedNode = "";
            status = app().getString(R.string.video_no_camera);
            if (!cap.probeError.isEmpty()) Log.w(TAG, "探测异常: " + cap.probeError);
        } else {
            selectedNode = cameras.get(0).node;
            status = app().getString(R.string.video_cameras_found, cameras.size());
        }
        publish();
    }

    // ---------------------------------------------------------------- 用户操作

    public void selectCamera(String node) {
        if (node == null || node.isEmpty() || node.equals(selectedNode)) return;
        if (pendingCamera != null) return;          // 切换中，忽略再次选择

        if (phase == Phase.LIVE && !activeRoom.isEmpty()) {
            MqttManager.VideoRequest req = mgr.startVideoStream(activeUuid, node, activeRoom);
            if (!req.isSent()) {
                emit(toast, "摄像头切换请求未发出");
                return;
            }
            pendingCamera = node;
            pendingSwitchRid = req.requestId;
            phase = Phase.SWITCHING;
            status = "正在切换摄像头…";
            armTimeout();
            publish();
            return;
        }
        selectedNode = node;
        publish();
    }

    /** 点击视频区域：空闲时开始推流，推流中则停止（与改造前一致：播放中点击不处理）。 */
    public void togglePlay() {
        if (phase == Phase.LIVE || phase == Phase.STARTING || phase == Phase.SWITCHING) {
            return;
        }
        String uuid = mgr.getSelectedKey();
        if (uuid.isEmpty()) {
            emit(toast, app().getString(R.string.video_no_device));
            return;
        }
        CameraCapability.Camera cam = selectedCamera();
        if (cam == null) {
            emit(toast, app().getString(R.string.video_no_camera));
            return;
        }
        activeUuid = uuid;
        MqttManager.VideoRequest req = mgr.startVideoStream(uuid, cam.node);
        if (!req.isSent()) {
            activeUuid = "";
            return;
        }
        activeRoom = req.room;
        activeCamera = cam.node;
        pendingStartRid = req.requestId;
        phase = Phase.STARTING;
        status = app().getString(R.string.video_starting, activeRoom);
        armTimeout();
        publish();
    }

    // ---------------------------------------------------------------- MQTT 回包

    @Override
    public void onVideoRequestResult(JSONObject payload) {
        if (payload == null) return;
        String uuid = payload.optString("uuid", "");
        if (!uuid.isEmpty() && !uuid.equals(activeUuid)) return;
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
            String room = payload.optString("room_number", activeRoom);
            if (room.isEmpty()) room = activeRoom;
            activeRoom = room;
            phase = Phase.LIVE;
            status = "● 推流中";
        } else {
            activeUuid = "";
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
            activeUuid = "";
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
        boolean changed = !mgr.getSelectedKey().equals(capUuid);
        connected = nowConnected;
        if (changed) {
            stopStream(true);
            queryCapability();
        } else {
            publish();
        }
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
        if (sendStop && !activeUuid.isEmpty() && !activeRoom.isEmpty()) {
            pendingStopRid = mgr.stopVideoStream(activeUuid, activeRoom);
        }
        activeUuid = "";
        activeRoom = "";
        activeCamera = "";
        phase = Phase.IDLE;
        status = app().getString(R.string.video_tap_to_play);
        publish();
    }

    public CameraCapability.Camera selectedCamera() {
        for (CameraCapability.Camera c : cameras) {
            if (c.node.equals(selectedNode)) return c;
        }
        return cameras.isEmpty() ? null : cameras.get(0);
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
        DeviceInfo dev = mgr.getSelectedDevice();
        state.setValue(new UiState(cameras, selectedNode, connected, hasDevice,
                capabilityLoading, phase, status, activeRoom, activeCamera,
                mgr.getSelectedKey(), dev == null ? "" : orEmpty(dev.uuid)));
    }
}
