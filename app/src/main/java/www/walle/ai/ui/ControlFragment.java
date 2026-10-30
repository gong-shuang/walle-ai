package www.walle.ai.ui;

import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.AdapterView;
import android.widget.ArrayAdapter;
import android.widget.ImageView;
import android.widget.ProgressBar;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.fragment.app.Fragment;

import org.json.JSONArray;
import org.json.JSONObject;
import org.webrtc.SurfaceViewRenderer;

import java.util.ArrayList;
import java.util.List;

import www.walle.ai.BuildConfig;
import www.walle.ai.R;
import www.walle.ai.net.MqttManager;

/**
 * 控制页：摄像头能力查询 + 视频推流（页面内嵌 WebRTC 拉流）。
 *
 * <p>交互链路：
 * <ol>
 *   <li>页面显示（首次进入 / 从状态页切回）→ 发 {@code camera/capability/query} 拿摄像头列表；</li>
 *   <li>点击视频区域 → 生成 6 位 room_number（由 MqttManager 全局保存）→ 发 {@code video/start}；</li>
 *   <li>收到 {@code video/start/result} 且 result=0 → 按 room_number 拼 WHEP 地址，页面内开始拉流；</li>
 *   <li>离开控制页 / 切设备 / App 退到后台 → 释放拉流并发 {@code video/stop/send}。</li>
 * </ol>
 */
public class ControlFragment extends Fragment implements MqttManager.Listener {

    private MqttManager mgr;

    private Spinner spinnerCamera;
    private TextView tvVideoStatus;
    private ImageView btnPlay;
    private ImageView ivFullscreen;
    private ProgressBar pbVideo;
    private SurfaceViewRenderer videoRenderer;

    private CameraSpinnerAdapter cameraAdapter;
    private final List<CameraSpinnerAdapter.Camera> adapterItems = new ArrayList<>();
    private final List<Camera> cameras = new ArrayList<>();

    private String capUuid = "";      // 本次能力查询对应的设备 uuid
    private String activeUuid = "";   // 正在推流的设备 uuid
    private String activeRoom = "";   // 正在推流的房间号
    private String activeCamera = ""; // 正在推流的摄像头设备 node
    /** 播放中切换摄像头时暂存请求的新 node；result 回来前 pendingCamera != null。 */
    private String pendingCamera;
    private boolean starting;         // 已发 request，等待 result
    /** 各类请求的 request_id：结果回来时按它配对，避免「猜」哪条结果对应哪次点击。 */
    private String pendingStartRid = "";
    private String pendingSwitchRid = "";
    private String pendingStopRid = "";
    private String pendingCapRid = "";
    /** 请求超时兜底：设备没回 result 时复位 UI，避免一直卡在「正在启动/切换」。 */
    private final android.os.Handler timeoutHandler =
            new android.os.Handler(android.os.Looper.getMainLooper());
    private final Runnable videoTimeout = () -> {
        if (pendingStartRid.isEmpty() && pendingSwitchRid.isEmpty()) return;
        android.util.Log.w("ControlFragment", "推流请求超时未回包 start=" + pendingStartRid
                + " switch=" + pendingSwitchRid);
        pendingStartRid = "";
        pendingSwitchRid = "";
        pendingCamera = null;
        starting = false;
        if (pbVideo != null) pbVideo.setVisibility(View.GONE);
        if (btnPlay != null) {
            btnPlay.setVisibility(View.VISIBLE);
            btnPlay.setEnabled(selectedCamera() != null);
        }
        setStatus("设备未响应（8s 超时），请检查连接后重试");
    };

    private void armVideoTimeout() {
        timeoutHandler.removeCallbacks(videoTimeout);
        timeoutHandler.postDelayed(videoTimeout, 8000L);
    }

    private void clearVideoTimeout() {
        timeoutHandler.removeCallbacks(videoTimeout);
    }
    private boolean handoff;          // 已切换到全屏播放页：本次 onStop 不下发 stop
    private WhepPlayer player;
    /** 当前播放器实际拉取的房间号：用于幂等判断，防止重复结果把播放器初始化两次。 */
    private String playingRoom = "";

    @Nullable
    @Override
    public View onCreateView(@NonNull LayoutInflater inflater,
                             @Nullable ViewGroup container,
                             @Nullable Bundle savedInstanceState) {
        return inflater.inflate(R.layout.fragment_control, container, false);
    }

    @Override
    public void onViewCreated(@NonNull View view, @Nullable Bundle savedInstanceState) {
        super.onViewCreated(view, savedInstanceState);
        mgr = MqttManager.get(requireContext());

        spinnerCamera = view.findViewById(R.id.spinnerCamera);
        tvVideoStatus = view.findViewById(R.id.tvVideoStatus);
        btnPlay = view.findViewById(R.id.btnPlay);
        ivFullscreen = view.findViewById(R.id.ivFullscreen);
        pbVideo = view.findViewById(R.id.pbVideo);
        videoRenderer = view.findViewById(R.id.videoRenderer);

        cameraAdapter = new CameraSpinnerAdapter(requireContext(), adapterItems);
        spinnerCamera.setAdapter(cameraAdapter);
        spinnerCamera.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            @Override
            public void onItemSelected(AdapterView<?> parent, View v, int position, long id) {
                if (pendingCamera != null) {
                    // 切换请求已发出、等待 result 期间，禁止再选
                    int oldIdx = findCameraIndex(activeCamera);
                    if (oldIdx >= 0) {
                        spinnerCamera.setSelection(oldIdx);
                        cameraAdapter.setSelectedPosition(oldIdx);
                    }
                    return;
                }
                Camera selected = cameras.get(position);
                // 正在播放（player != null + activeRoom 非空）且用户选了不同的摄像头 → 发切换请求
                if (player != null && !activeRoom.isEmpty() && !selected.node.equals(activeCamera)) {
                    MqttManager.VideoRequest req =
                            mgr.startVideoStream(activeUuid, selected.node, activeRoom);
                    if (!req.isSent()) {
                        // MQTT 未发出，回滚 spinner 到当前
                        int idx = findCameraIndex(activeCamera);
                        if (idx >= 0) spinnerCamera.setSelection(idx);
                        cameraAdapter.setSelectedPosition(idx);
                        toast("摄像头切换请求未发出");
                        return;
                    }
                    pendingCamera = selected.node;
                    pendingSwitchRid = req.requestId;
                    armVideoTimeout();
                    setStatus("正在切换摄像头…");
                } else {
                    cameraAdapter.setSelectedPosition(position);
                    updateCameraInfo();
                }
            }

            @Override
            public void onNothingSelected(AdapterView<?> parent) {
                updateCameraInfo();
            }
        });

        view.findViewById(R.id.flVideo).setOnClickListener(v -> onVideoAreaClick());
        btnPlay.setOnClickListener(v -> onVideoAreaClick());
        ivFullscreen.setOnClickListener(v -> openFullscreen());

        renderIdleState();
    }

    // ---------------- 生命周期：显示即查询，隐藏即停止 ----------------

    @Override
    public void onStart() {
        super.onStart();
        mgr.addListener(this);
        // 首次进入时若本页就是当前显示的子页，立即查询
        if (!isHidden()) queryCapability();
    }

    @Override
    public void onStop() {
        mgr.removeListener(this);
        if (handoff) {
            // 已跳转到全屏播放页：推流所有权交给 PlayerActivity，由它在退出时下发 stop
            handoff = false;
        } else {
            stopStream(true, null);
        }
        super.onStop();
    }

    @Override
    public void onHiddenChanged(boolean hidden) {
        super.onHiddenChanged(hidden);
        if (hidden) {
            // 离开控制页：停止设备端推流并释放本地拉流
            stopStream(true, null);
        } else {
            queryCapability();
        }
    }

    @Override
    public void onDestroyView() {
        stopStream(true, null);
        super.onDestroyView();
    }

    // ---------------- 能力查询 ----------------

    private void queryCapability() {
        String uuid = mgr.getSelectedKey();
        capUuid = uuid;

        cameras.clear();
        // 后续 setPlaceholder / onCapability 会 syncAdapterItems，这里不用单独 notify

        if (uuid.isEmpty()) {
            setPlaceholder(getString(R.string.video_no_device));
            spinnerCamera.setEnabled(false);
            btnPlay.setEnabled(false);
            setStatus(getString(R.string.video_no_device));
            return;
        }

        setPlaceholder(getString(R.string.video_querying_capability));
        spinnerCamera.setEnabled(false);
        btnPlay.setEnabled(false);
        setStatus(getString(R.string.video_querying_capability));
        pendingCapRid = mgr.requestCapability(uuid);
    }

    @Override
    public void onCapability(@Nullable JSONObject payload) {
        if (payload == null) {
            return;
        }
        String uuid = payload.optString("uuid", "");
        // 设备端报文都带 uuid：不是当前设备的回包直接丢弃
        if (!uuid.isEmpty() && !uuid.equals(capUuid)) {
            return;
        }
        // 按 request_id 配对：旧查询的迟到结果不覆盖当前列表
        String rid = payload.optString("request_id", "");
        if (!pendingCapRid.isEmpty() && !pendingCapRid.equals(rid)) {
            android.util.Log.w("ControlFragment", "忽略不匹配的能力结果 request_id=" + rid
                    + "（等待 " + pendingCapRid + "）");
            return;
        }
        pendingCapRid = "";

        cameras.clear();

        JSONArray arr = payload.optJSONArray("cameras");
        if (arr != null) {
            for (int i = 0; i < arr.length(); i++) {
                JSONObject c = arr.optJSONObject(i);
                if (c == null) continue;
                String node = c.optString("node", "");
                if (node.isEmpty()) continue;
                cameras.add(new Camera(node,
                        c.optString("name", ""),
                        c.optString("detail", ""),
                        c.optString("max_resolution", "")));
            }
        }

        if (cameras.isEmpty()) {
            String probeError = "";
            JSONObject probe = payload.optJSONObject("probe");
            if (probe != null) probeError = probe.optString("error", "");
            setPlaceholder(getString(R.string.video_no_camera));
            spinnerCamera.setEnabled(false);
            btnPlay.setEnabled(false);
            setStatus(getString(R.string.video_no_camera));
            return;
        }

        // Adapter 直接读 cameras 列表，不需要单独 add；syncAdapterItems 会刷新。
        syncAdapterItems();
        spinnerCamera.setEnabled(true);
        spinnerCamera.setSelection(0);
        cameraAdapter.setSelectedPosition(0);
        btnPlay.setEnabled(true);
        updateCameraInfo();
        setStatus(getString(R.string.video_cameras_found, cameras.size()));
    }

    private void updateCameraInfo() {
        Camera c = selectedCamera();
        if (c == null) {
            return;
        }
        StringBuilder sb = new StringBuilder();
        if (!c.name.isEmpty()) sb.append(c.name);
        if (!c.maxRes.isEmpty()) {
            if (sb.length() > 0) sb.append(" · ");
            sb.append(getString(R.string.video_max_res, c.maxRes));
        }
    }

    @Nullable
    private Camera selectedCamera() {
        int pos = spinnerCamera == null ? -1 : spinnerCamera.getSelectedItemPosition();
        return pos >= 0 && pos < cameras.size() ? cameras.get(pos) : null;
    }

    // ---------------- 推流 ----------------

    private void onVideoAreaClick() {
        if (!isAdded() || starting || player != null) {
            return;
        }
        String uuid = mgr.getSelectedKey();
        if (uuid.isEmpty()) {
            toast(getString(R.string.video_no_device));
            return;
        }
        Camera cam = selectedCamera();
        if (cam == null) {
            toast(getString(R.string.video_no_camera));
            return;
        }

        activeUuid = uuid;
        MqttManager.VideoRequest req = mgr.startVideoStream(uuid, cam.node);
        if (!req.isSent()) {
            activeUuid = "";
            return;
        }
        activeRoom = req.room;
        pendingStartRid = req.requestId;
        activeCamera = cam.node;
        starting = true;
        armVideoTimeout();
        pbVideo.setVisibility(View.VISIBLE);
        btnPlay.setVisibility(View.GONE);
        setStatus(getString(R.string.video_starting, activeRoom));
    }

    @Override
    public void onVideoRequestResult(@Nullable JSONObject payload) {
        if (payload == null) {
            return;
        }
        String uuid = payload.optString("uuid", "");
        if (!uuid.isEmpty() && !uuid.equals(activeUuid)) {
            return;
        }
        String rid = payload.optString("request_id", "");
        boolean switchResp = !pendingSwitchRid.isEmpty() && pendingSwitchRid.equals(rid);
        boolean startResp = !pendingStartRid.isEmpty() && pendingStartRid.equals(rid);
        if (!switchResp && !startResp) {
            // 迟到的旧结果 / 别处发起的结果：直接丢，避免把当前状态改乱
            android.util.Log.w("ControlFragment", "忽略不匹配的推流结果 request_id=" + rid
                    + "（等待 start=" + pendingStartRid + " switch=" + pendingSwitchRid + "）");
            return;
        }

        int result = payload.optInt("result", -2);

        // 分支 1：播放中切换摄像头
        if (switchResp) {
            pendingSwitchRid = "";
            clearVideoTimeout();
            if (result == 0) {
                activeCamera = pendingCamera != null ? pendingCamera : activeCamera;
                pendingCamera = null;
                int idx = findCameraIndex(activeCamera);
                if (idx >= 0) cameraAdapter.setSelectedPosition(idx);
                setStatus("● 直播中");
            } else {
                String cause = payload.optString("error",
                        payload.optString("cause", "摄像头切换失败（result=" + result + "）"));
                int idx = findCameraIndex(activeCamera);
                if (idx >= 0) {
                    spinnerCamera.setSelection(idx);
                    cameraAdapter.setSelectedPosition(idx);
                }
                pendingCamera = null;
                setStatus(getString(R.string.video_start_failed, cause));
            }
            return;
        }

        // 分支 2：首次启动推流
        pendingStartRid = "";
        clearVideoTimeout();
        starting = false;
        pbVideo.setVisibility(View.GONE);
        if (result == 0) {
            String room = payload.optString("room_number", activeRoom);
            if (room.isEmpty()) room = activeRoom;
            beginPlayback(room);
        } else {
            String cause = payload.optString("error", payload.optString("cause", "推流启动失败"));
            activeUuid = "";
            activeRoom = "";
            activeCamera = "";
            btnPlay.setVisibility(View.VISIBLE);
            btnPlay.setEnabled(true);
            setStatus(getString(R.string.video_start_failed, cause));
        }
    }

    @Override
    public void onVideoStopResult(@Nullable JSONObject payload) {
        // 停止结果只做记录，UI 已在本端先行复位；仍按 request_id 配对，忽略迟到/无关结果
        if (payload == null || !isAdded()) {
            return;
        }
        String rid = payload.optString("request_id", "");
        if (!pendingStopRid.isEmpty() && !pendingStopRid.equals(rid)) {
            android.util.Log.w("ControlFragment", "忽略不匹配的停止结果 request_id=" + rid);
            return;
        }
        pendingStopRid = "";
        int result = payload.optInt("result", 0);
        if (result != 0) {
            toast(getString(R.string.video_stop_failed, payload.optString("error", "")));
        }
    }

    @Override
    public void onDeliveryError(String kind, String error) {
        if (!isAdded()) {
            return;
        }
        if (starting && kind != null
                && (kind.equals(www.walle.ai.net.TopicNames.KIND_VIDEO_START)
                || kind.equals(www.walle.ai.net.TopicNames.KIND_CAPABILITY))) {
            starting = false;
            pbVideo.setVisibility(View.GONE);
            btnPlay.setVisibility(View.VISIBLE);
            activeUuid = "";
            activeRoom = "";
            setStatus(error);
        }
    }

    @Override
    public void onDevicesChanged() {
        if (!isAdded() || isHidden()) {
            return;
        }
        // 当前设备被切换：先停掉旧设备的推流，再重新查询新设备的摄像头能力
        String uuid = mgr.getSelectedKey();
        if (!uuid.equals(capUuid)) {
            stopStream(true, null);
            queryCapability();
        }
    }

    // ---------------- 页面内拉流 ----------------

    private void beginPlayback(String room) {
        if (!isAdded() || room.isEmpty()) {
            return;
        }
        String url = BuildConfig.WHEP_URL_TEMPLATE.replace("{room}", Uri.encode(room));
        // 同一个房间的重复回调（QoS1 重投）直接返回，
        // 避免在同一个 SurfaceViewRenderer 上第二次 init 触发 videoRendererAlready initialized
        if (player != null && !player.isReleased()) {
            if (room.equals(playingRoom)) {
                ivFullscreen.setVisibility(View.VISIBLE);
                return;
            }
            // 换房间：先释放旧会话，再按新地址重建
            player.release();
            player = null;
        }
        playingRoom = room;
        player = new WhepPlayer(requireContext().getApplicationContext(), videoRenderer,
                new WhepPlayer.Callback() {
                    @Override
                    public void onStatus(String text) {
                        if (isAdded()) setStatus(text);
                    }

                    @Override
                    public void onFirstFrame() {
                        if (btnPlay != null) btnPlay.setVisibility(View.GONE);
                    }

                    @Override
                    public void onError(final String message) {
                        if (!isAdded()) return;
                        stopStream(true, getString(R.string.video_play_error, message));
                    }
                });
        player.start(url);
        ivFullscreen.setVisibility(View.VISIBLE);
    }

    /** 放大观看：跳转横屏全屏播放页（同一房间号），推流所有权交给 PlayerActivity。 */
    private void openFullscreen() {
        if (!isAdded() || activeRoom.isEmpty()) {
            return;
        }
        Intent intent = new Intent(requireContext(), PlayerActivity.class);
        intent.putExtra(PlayerActivity.EXTRA_ROOM, activeRoom);
        intent.putExtra(PlayerActivity.EXTRA_UUID, activeUuid);
        intent.putExtra(PlayerActivity.EXTRA_CAMERA, activeCamera);
        // 把摄像头列表序列化成 JSON 字符串传递
        if (!cameras.isEmpty()) {
            StringBuilder sb = new StringBuilder("[");
            for (int i = 0; i < cameras.size(); i++) {
                Camera c = cameras.get(i);
                if (i > 0) sb.append(',');
                sb.append("{\"node\":\"").append(c.node).append('\"')
                  .append(",\"name\":\"").append(c.name.replace("\"", "\\\"")).append("\"}");
            }
            sb.append(']');
            intent.putExtra(PlayerActivity.EXTRA_CAMERAS_JSON, sb.toString());
        }

        handoff = true;
        if (player != null) {
            player.release();
            player = null;
        }
        activeUuid = "";
        activeRoom = "";
        activeCamera = "";
        starting = false;
        pbVideo.setVisibility(View.GONE);
        btnPlay.setVisibility(View.VISIBLE);
        btnPlay.setEnabled(false);
        ivFullscreen.setVisibility(View.GONE);
        setStatus(getString(R.string.video_fullscreen_playing));
        startActivity(intent);
    }

    /**
     * 停止推流：释放本地拉流 → 通知设备停流 → 复位 UI。
     *
     * @param sendStop    是否下发 video/stop/send
     * @param finalStatus 复位后显示的文案，null 表示使用默认“点击开始推流”
     */
    private void stopStream(boolean sendStop, @Nullable String finalStatus) {
        if (player != null) {
            player.release();
            player = null;
        }
        playingRoom = "";
        pendingStartRid = "";
        pendingSwitchRid = "";
        pendingCamera = null;
        clearVideoTimeout();
        if (sendStop && !activeUuid.isEmpty() && !activeRoom.isEmpty()) {
            pendingStopRid = mgr.stopVideoStream(activeUuid, activeRoom);
        }
        activeUuid = "";
        activeRoom = "";
        activeCamera = "";
        starting = false;
        if (pbVideo != null) pbVideo.setVisibility(View.GONE);
        if (ivFullscreen != null) ivFullscreen.setVisibility(View.GONE);
        if (btnPlay != null) {
            btnPlay.setVisibility(View.VISIBLE);
            btnPlay.setEnabled(selectedCamera() != null);
        }
        setStatus(finalStatus != null ? finalStatus : getString(R.string.video_tap_to_play));
    }

    private void renderIdleState() {
        pbVideo.setVisibility(View.GONE);
        btnPlay.setVisibility(View.VISIBLE);
        btnPlay.setEnabled(false);
        ivFullscreen.setVisibility(View.GONE);
        setPlaceholder(getString(R.string.video_querying_capability));
        spinnerCamera.setEnabled(false);
        setStatus(getString(R.string.video_tap_to_play));
    }

    private void setPlaceholder(String text) {
        cameras.clear();
        cameras.add(new Camera(text, "", "", ""));
        syncAdapterItems();
    }

    /** 把内部 cameras 同步成 Adapter 需要的 node+name 列表。 */
    private void syncAdapterItems() {
        adapterItems.clear();
        for (Camera c : cameras) {
            adapterItems.add(new CameraSpinnerAdapter.Camera(c.node, c.name));
        }
        cameraAdapter.notifyDataSetChanged();
    }

    private void setStatus(String text) {
        if (tvVideoStatus != null) tvVideoStatus.setText(text);
    }

    private void toast(String text) {
        if (isAdded()) Toast.makeText(requireContext(), text, Toast.LENGTH_SHORT).show();
    }

    /** 给定 node，返回在 cameras 里的下标；找不到返回 -1。 */
    private int findCameraIndex(String node) {
        if (node == null) return -1;
        for (int i = 0; i < cameras.size(); i++) {
            if (node.equals(cameras.get(i).node)) return i;
        }
        return -1;
    }

    /** 能力查询结果里的单个摄像头节点。 */
    private static final class Camera {
        final String node;
        final String name;
        final String detail;
        final String maxRes;

        Camera(String node, String name, String detail, String maxRes) {
            this.node = node;
            this.name = name;
            this.detail = detail;
            this.maxRes = maxRes;
        }
    }
}
