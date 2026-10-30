package www.walle.ai.ui;

import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.view.View;
import android.view.Window;
import android.view.WindowManager;

import androidx.appcompat.app.AppCompatActivity;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsCompat;
import androidx.core.view.WindowInsetsControllerCompat;

import www.walle.ai.BuildConfig;
import www.walle.ai.databinding.ActivityPlayerBinding;

import org.json.JSONArray;
import org.json.JSONObject;
import org.webrtc.PeerConnection;

import java.util.ArrayList;
import java.util.List;

import www.walle.ai.net.MqttManager;

/**
 * WHEP 拉流播放页（纯 Java 实现，横屏全屏）。
 *
 * <p>主要职责：
 * <ol>
 *   <li>从 MqttManager 拿到设备端当前正在推的视频流（通过 WHEP / WebRTC 协议拉流上屏）；</li>
 *   <li>支持在播放过程中切换设备端摄像头（发 video/start 重启 ffmpeg），
 *       App 本地重新走 WHEP 协商即可接入新的媒体源；</li>
 *   <li>退出时通过 MQTT 通知设备端停流，并 DELETE WHEP 会话释放服务器资源。</li>
 * </ol>
 */
public class PlayerActivity extends AppCompatActivity {

    private static final String TAG = "WhepDemo";

    // —— Intent extras ——
    /** 控制页「放大」进入时携带的房间号；为空表示使用固定演示地址。 */
    public static final String EXTRA_ROOM = "extra_room";
    /** 与房间号对应的设备 uuid，退出播放页时下发 video/stop/send。 */
    public static final String EXTRA_UUID = "extra_uuid";
    /** 控制页携带的「当前推流摄像头」节点，如 /dev/video0。 */
    public static final String EXTRA_CAMERA = "extra_camera";
    /** 控制页携带的完整摄像头列表，JSON 字符串：[{"node":"/dev/video0","name":"PSI USB CAMERA"}, ...] */
    public static final String EXTRA_CAMERAS_JSON = "extra_cameras_json";

    // —— 会话 / 播放相关 ——
    private static final String WHEP_URL = BuildConfig.WHEP_URL;
    private String whepUrl = WHEP_URL;
    private String roomNumber = "";
    private String deviceUuid = "";
    /** 当前正在推流的摄像头节点，如 /dev/video0。 */
    private String currentCamera = "";

    /** 控制页传过来的完整摄像头列表（node + name），用于右上角 Spinner。 */
    private final List<CameraSpinnerAdapter.Camera> cameraItems = new ArrayList<>();
    private CameraSpinnerAdapter cameraAdapter;
    /** 切换摄像头期间暂存旧 node，result!=0 时回滚 spinner 选中。 */
    private String pendingCameraName;
    private volatile boolean waitingVideoResult;
    /** 本次摄像头切换请求的 request_id，用于与结果配对。 */
    private volatile String waitingVideoRid = "";

    private ActivityPlayerBinding binding;

    // —— 拉流会话（与控制页共用同一份实现）——
    private WhepSession session;

    /** 页面是否已销毁（onDestroy 后置 true，用于丢弃迟到的回调）。 */
    private volatile boolean released;

    /**
     * 极简 MQTT 监听：只关心 video/start/result 来更新切换摄像头的状态条。
     * 房间号不变 → PeerConnection/WHEP 会话都不动 → 只改 UI 文案。
     */
    private final MqttManager.Listener mqttListener = new MqttManager.Listener() {
        @Override
        public void onVideoRequestResult(org.json.JSONObject payload) {
            if (!waitingVideoResult) return;
            // 匹配本页发出的那一条：request_id 优先，其次 room_number + uuid
            String rid = payload.optString("request_id", "");
            if (!waitingVideoRid.isEmpty() && !waitingVideoRid.equals(rid)) return;
            String resultRoom = payload.optString("room_number", "");
            if (!roomNumber.isEmpty() && !roomNumber.equals(resultRoom)) return;
            if (!deviceUuid.isEmpty() && !deviceUuid.equals(payload.optString("uuid"))) return;

            waitingVideoResult = false;
            waitingVideoRid = "";
            int result = payload.optInt("result", -99);
            if (result == 0) {
                int idx = findCameraIndex(currentCamera);
                if (idx >= 0) cameraAdapter.setSelectedPosition(idx);
                setStatus("● 直播中");
            } else {
                String cause = payload.optString("error",
                        payload.optString("cause", "摄像头切换失败（result=" + result + "）"));
                // 回滚 UI：恢复旧 node + spinner 选中
                if (pendingCameraName != null && !pendingCameraName.equals(currentCamera)) {
                    currentCamera = pendingCameraName;
                    int idx = findCameraIndex(currentCamera);
                    if (idx >= 0) {
                        binding.spinnerCamera.setSelection(idx);
                        cameraAdapter.setSelectedPosition(idx);
                    }
                }
                pendingCameraName = null;
                setStatus("切换失败：" + cause);
            }
            pendingCameraName = null;
        }
    };

    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    // region 生命周期

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        binding = ActivityPlayerBinding.inflate(getLayoutInflater());
        setContentView(binding.getRoot());

        hideStatusBarAndKeepScreenOn();

        // 解析 intent
        String room = getIntent().getStringExtra(EXTRA_ROOM);
        roomNumber = room == null ? "" : room;
        String uuid = getIntent().getStringExtra(EXTRA_UUID);
        deviceUuid = uuid == null ? "" : uuid;
        currentCamera = orEmpty(getIntent().getStringExtra(EXTRA_CAMERA));
        if (!roomNumber.isEmpty()) {
            whepUrl = BuildConfig.WHEP_URL_TEMPLATE.replace("{room}", Uri.encode(roomNumber));
        }
        // 初始化摄像头 Spinner（必须在 parseCamerasJson 之前，否则 NPE）
        cameraAdapter = new CameraSpinnerAdapter(this, cameraItems,
                www.walle.ai.R.layout.item_spinner_camera_selected_dark);
        binding.spinnerCamera.setAdapter(cameraAdapter);
        binding.spinnerCamera.setOnItemSelectedListener(new android.widget.AdapterView.OnItemSelectedListener() {
            @Override
            public void onItemSelected(android.widget.AdapterView<?> parent, android.view.View v,
                                       int position, long id) {
                CameraSpinnerAdapter.Camera selected = cameraItems.get(position);
                if (waitingVideoResult) {
                    // 切换请求已发出、等待 result 期间，禁止再选
                    int oldIdx = findCameraIndex(currentCamera);
                    if (oldIdx >= 0) {
                        binding.spinnerCamera.setSelection(oldIdx);
                        cameraAdapter.setSelectedPosition(oldIdx);
                    }
                    return;
                }
                if (selected.node.equals(currentCamera)) {
                    cameraAdapter.setSelectedPosition(position);
                    return;
                }
                // 发切换请求：沿用同房间号，PeerConnection 不动，设备端换摄像头后 App 自然收到新帧
                MqttManager.VideoRequest req = MqttManager.get(getApplicationContext())
                        .startVideoStream(deviceUuid, selected.node, roomNumber);
                if (!req.isSent()) {
                    int idx = findCameraIndex(currentCamera);
                    if (idx >= 0) binding.spinnerCamera.setSelection(idx);
                    cameraAdapter.setSelectedPosition(idx);
                    setStatus("摄像头切换请求未发出");
                    return;
                }
                pendingCameraName = currentCamera;
                currentCamera = selected.node;
                waitingVideoRid = req.requestId;
                waitingVideoResult = true;
                cameraAdapter.setSelectedPosition(position);
                setStatus("正在切换摄像头…");
            }

            @Override public void onNothingSelected(android.widget.AdapterView<?> parent) {}
        });

        // 先把摄像头列表解析出来（adapter 已有，parseCamerasJson 内部要调 notifyDataSetChanged）
        parseCamerasJson(getIntent().getStringExtra(EXTRA_CAMERAS_JSON));

        // 绑定 UI
        binding.backButton.setOnClickListener(v -> finish());

        // 注册 MQTT 监听：video/start/result 用来更新切换摄像头的状态条
        MqttManager.get(getApplicationContext()).addListener(mqttListener);

        // 初始渲染状态
        updateCameraUi();

        // 创建拉流会话（内部负责 EGL / Factory / 渲染器初始化）并开始协商
        binding.firstFrameMask.setAlpha(1f);
        binding.firstFrameMask.setVisibility(View.VISIBLE);
        session = new WhepSession(getApplicationContext(), binding.remoteRenderer,
                new WhepSession.Callback() {
                    @Override
                    public void onStatus(String text) {
                        setStatus(text);
                    }

                    @Override
                    public void onFirstFrame() {
                        if (released) return;
                        // 首帧上屏后淡出遮罩
                        mainHandler.postDelayed(() -> binding.firstFrameMask.animate()
                                .alpha(0f).setDuration(200)
                                .withEndAction(() -> binding.firstFrameMask.setVisibility(View.GONE))
                                .start(), 400);
                    }

                    @Override
                    public void onError(String message) {
                        setStatus("错误：" + message);
                    }

                    @Override
                    public void onConnectionState(PeerConnection.PeerConnectionState state) {
                        if (released) return;
                        switch (state) {
                            case CONNECTED:
                                setStatus("● 直播中");
                                break;
                            case DISCONNECTED:
                                setStatus("连接中断，尝试恢复…");
                                break;
                            case FAILED:
                                setStatus("连接失败");
                                break;
                            case CLOSED:
                                setStatus("连接已关闭");
                                break;
                            default:
                                break;
                        }
                    }
                });
        session.start(whepUrl);

        // 兜底提示：10 秒后仍未进入直播状态，提示可能没有主播推流
        mainHandler.postDelayed(() -> {
            WhepSession s = session;
            if (!released && (s == null || !s.isLive())) {
                setStatus("等待主播推流中…");
            }
        }, 10_000);
    }

    @Override
    protected void onDestroy() {
        // 移除 MQTT 监听
        waitingVideoResult = false;
        MqttManager.get(getApplicationContext()).removeListener(mqttListener);

        // 释放拉流会话（EGL / Factory / 渲染器一起释放）
        released = true;
        if (session != null) {
            session.release();
            session = null;
        }
        // 通知设备端停流
        if (!roomNumber.isEmpty() && !deviceUuid.isEmpty()) {
            MqttManager.get(getApplicationContext()).stopVideoStream(deviceUuid, roomNumber);
        }
        super.onDestroy();
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) hideStatusBarAndKeepScreenOn();
    }

    // endregion

    // region 窗口设置

    private void hideStatusBarAndKeepScreenOn() {
        Window window = getWindow();
        WindowCompat.setDecorFitsSystemWindows(window, false);
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        // 系统栏颜色由主题统一置透明（API 35+ 上这两个 API 本身已是空操作）
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            window.getAttributes().layoutInDisplayCutoutMode =
                    WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES;
        }
        WindowInsetsControllerCompat controller =
                new WindowInsetsControllerCompat(window, window.getDecorView());
        controller.hide(WindowInsetsCompat.Type.statusBars());
        controller.setSystemBarsBehavior(
                WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
        controller.setAppearanceLightStatusBars(false);
        controller.setAppearanceLightNavigationBars(false);

        ViewCompat.setOnApplyWindowInsetsListener(binding.getRoot(), (view, insets) -> {
            Insets topInsets = insets.getInsets(
                    WindowInsetsCompat.Type.statusBars() | WindowInsetsCompat.Type.displayCutout());
            Insets navInsets = insets.getInsets(WindowInsetsCompat.Type.navigationBars());
            binding.backButton.setTranslationY(topInsets.top);
            binding.spinnerCamera.setTranslationY(topInsets.top);
            binding.statusText.setTranslationY(-navInsets.bottom);
            return WindowInsetsCompat.CONSUMED;
        });
    }

    // endregion

    // region 切换摄像头

    /** 解析控制页传递的摄像头列表 JSON，并同步 Spinner。 */
    private void parseCamerasJson(String json) {
        cameraItems.clear();
        if (cameraAdapter != null) cameraAdapter.notifyDataSetChanged();
        if (json == null || json.isEmpty()) return;
        int selectedPos = -1;
        try {
            JSONArray arr = new JSONArray(json);
            for (int i = 0; i < arr.length(); i++) {
                JSONObject o = arr.getJSONObject(i);
                String node = orEmpty(o.optString("node"));
                String name = orEmpty(o.optString("name"));
                cameraItems.add(new CameraSpinnerAdapter.Camera(node, name));
                if (node.equals(currentCamera)) selectedPos = i;
            }
        } catch (Exception e) {
            Log.w(TAG, "解析摄像头列表失败: " + e.getMessage());
        }
        if (cameraAdapter != null) cameraAdapter.notifyDataSetChanged();
        updateCameraUi();
        if (selectedPos >= 0) {
            binding.spinnerCamera.setSelection(selectedPos);
            cameraAdapter.setSelectedPosition(selectedPos);
        }
    }

    /** 根据摄像头列表是否为空决定 spinner 可见性。 */
    private void updateCameraUi() {
        binding.spinnerCamera.setVisibility(
                (!cameraItems.isEmpty() && !currentCamera.isEmpty())
                        ? View.VISIBLE : View.GONE);
    }

    /** 给定摄像头 node，返回在 cameraItems 列表中的下标；找不到返回 -1。 */
    private int findCameraIndex(String node) {
        if (node == null) return -1;
        for (int i = 0; i < cameraItems.size(); i++) {
            if (node.equals(cameraItems.get(i).node)) return i;
        }
        return -1;
    }

    // endregion

    // region helpers

    private void setStatus(final String text) {
        if (released) return;
        runOnUiThread(() -> {
            if (!isFinishing()) binding.statusText.setText(text);
        });
    }

    private static String orEmpty(String s) {
        return s == null ? "" : s;
    }

    // endregion
}
