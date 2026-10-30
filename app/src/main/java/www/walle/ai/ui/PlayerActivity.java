package www.walle.ai.ui;

import android.app.AlertDialog;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.view.View;
import android.view.Window;
import android.view.WindowManager;
import android.widget.ArrayAdapter;

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
import org.webrtc.AudioTrack;
import org.webrtc.EglBase;
import org.webrtc.IceCandidate;
import org.webrtc.MediaConstraints;
import org.webrtc.MediaStreamTrack;
import org.webrtc.PeerConnection;
import org.webrtc.PeerConnectionFactory;
import org.webrtc.RendererCommon;
import org.webrtc.RtpReceiver;
import org.webrtc.RtpTransceiver;
import org.webrtc.SdpObserver;
import org.webrtc.SessionDescription;
import org.webrtc.SurfaceViewRenderer;
import org.webrtc.VideoTrack;
import org.webrtc.audio.JavaAudioDeviceModule;
import org.webrtc.DefaultVideoDecoderFactory;
import org.webrtc.DefaultVideoEncoderFactory;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicReference;

import www.walle.ai.net.MqttManager;
import www.walle.ai.net.WhepClient;

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

    // —— WebRTC ——
    private EglBase eglBase;
    private SurfaceViewRenderer renderer;
    private PeerConnectionFactory factory;
    private JavaAudioDeviceModule audioDeviceModule;
    private PeerConnection peerConnection;
    private final AtomicReference<String> sessionLocation = new AtomicReference<>();

    /** 完整资源是否已释放（onDestroy 调用 release() 后置 true）。 */
    private volatile boolean released;
    /** 当前是否处于「会话重建」过程中（切换摄像头时为 true，防止并发 startPlayback）。 */
    private volatile boolean reconnecting;
    /** 是否已进入直播中（PeerConnection = CONNECTED）。 */
    private volatile boolean isLive;

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
    private final ExecutorService networkExecutor = Executors.newSingleThreadExecutor();

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

        // 初始化 WebRTC（只做一次）并开始拉流
        initWebRtc();
        startPlayback();

        // 兜底提示：10 秒后仍未进入直播状态，提示可能没有主播推流
        mainHandler.postDelayed(() -> {
            if (!released && !isLive) {
                setStatus("等待主播推流中…");
            }
        }, 10_000);
    }

    @Override
    protected void onDestroy() {
        // 移除 MQTT 监听
        waitingVideoResult = false;
        MqttManager.get(getApplicationContext()).removeListener(mqttListener);

        // 完整释放（factory / eglBase / audioDeviceModule 也一起释放）
        release();
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

    // region WebRTC 初始化

    /**
     * 只做一次：创建 EGL / Factory / ADM，初始化 SurfaceViewRenderer。
     * 后续切换摄像头只重新协商会话（PeerConnection），不重走这里。
     */
    private void initWebRtc() {
        EglBase egl = EglBase.create();
        eglBase = egl;

        SurfaceViewRenderer r = binding.remoteRenderer;
        r.init(egl.getEglBaseContext(), new RendererCommon.RendererEvents() {
            @Override
            public void onFirstFrameRendered() {
                mainHandler.postDelayed(() -> binding.firstFrameMask.animate()
                        .alpha(0f).setDuration(200)
                        .withEndAction(() -> binding.firstFrameMask.setVisibility(View.GONE))
                        .start(), 400);
            }

            @Override
            public void onFrameResolutionChanged(int videoWidth, int videoHeight, int rotation) {
            }
        });
        r.setScalingType(RendererCommon.ScalingType.SCALE_ASPECT_FIT);
        renderer = r;

        PeerConnectionFactory.initialize(
                PeerConnectionFactory.InitializationOptions
                        .builder(getApplicationContext())
                        .createInitializationOptions()
        );

        JavaAudioDeviceModule adm = JavaAudioDeviceModule.builder(getApplicationContext())
                .createAudioDeviceModule();
        audioDeviceModule = adm;

        factory = PeerConnectionFactory.builder()
                .setAudioDeviceModule(adm)
                .setVideoEncoderFactory(new DefaultVideoEncoderFactory(egl.getEglBaseContext(), true, true))
                .setVideoDecoderFactory(new DefaultVideoDecoderFactory(egl.getEglBaseContext()))
                .createPeerConnectionFactory();
    }

    private PeerConnection createPeerConnection() {
        if (factory == null) return null;
        PeerConnection.RTCConfiguration config =
                new PeerConnection.RTCConfiguration(Collections.<PeerConnection.IceServer>emptyList());
        config.sdpSemantics = PeerConnection.SdpSemantics.UNIFIED_PLAN;
        config.continualGatheringPolicy = PeerConnection.ContinualGatheringPolicy.GATHER_ONCE;
        config.bundlePolicy = PeerConnection.BundlePolicy.MAXBUNDLE;
        config.rtcpMuxPolicy = PeerConnection.RtcpMuxPolicy.REQUIRE;
        config.tcpCandidatePolicy = PeerConnection.TcpCandidatePolicy.ENABLED;
        config.keyType = PeerConnection.KeyType.ECDSA;
        return factory.createPeerConnection(config, peerConnectionObserver);
    }

    // endregion

    // region 播放流程

    /**
     * 开始一次 WHEP 协商。初次播放和切换摄像头都会调用。
     */
    private void startPlayback() {
        if (factory == null || released) return;

        PeerConnection pc = createPeerConnection();
        if (pc == null) {
            onError("PeerConnection 创建失败");
            return;
        }
        peerConnection = pc;

        try {
            pc.addTransceiver(MediaStreamTrack.MediaType.MEDIA_TYPE_AUDIO,
                    new RtpTransceiver.RtpTransceiverInit(RtpTransceiver.RtpTransceiverDirection.RECV_ONLY));
            pc.addTransceiver(MediaStreamTrack.MediaType.MEDIA_TYPE_VIDEO,
                    new RtpTransceiver.RtpTransceiverInit(RtpTransceiver.RtpTransceiverDirection.RECV_ONLY));
        } catch (Exception e) {
            onError("添加收发通道失败: " + e.getMessage());
            return;
        }

        // 首帧遮罩恢复，切换摄像头时也复用同一个遮罩动画
        binding.firstFrameMask.setAlpha(1f);
        binding.firstFrameMask.setVisibility(View.VISIBLE);

        setStatus("正在创建连接…");
        pc.createOffer(new SimpleSdpObserver() {
            @Override
            public void onCreateSuccess(SessionDescription offer) {
                PeerConnection connection = peerConnection;
                if (connection == null) return;
                connection.setLocalDescription(new SimpleSdpObserver() {
                    @Override
                    public void onSetSuccess() {
                        waitForIceGatheringThenSend();
                    }

                    @Override
                    public void onSetFailure(String error) {
                        onError("设置本地 SDP 失败: " + error);
                    }
                }, offer);
            }

            @Override
            public void onCreateFailure(String error) {
                onError("创建 Offer 失败: " + error);
            }
        }, new MediaConstraints());
    }

    private void waitForIceGatheringThenSend() {
        mainHandler.postDelayed(new Runnable() {
            private int tries;
            @Override
            public void run() {
                PeerConnection pc = peerConnection;
                if (pc == null || released) return;
                tries++;
                boolean complete = pc.iceGatheringState() == PeerConnection.IceGatheringState.COMPLETE;
                if (complete || tries > 60) {
                    Log.i(TAG, "ICE gathering=" + pc.iceGatheringState() + "，开始发送 Offer SDP");
                    sendOffer(pc);
                } else {
                    mainHandler.postDelayed(this, 100);
                }
            }
        }, 100);
    }

    private void sendOffer(PeerConnection pc) {
        SessionDescription localDescription = pc.getLocalDescription();
        String offerSdp = localDescription == null ? null : localDescription.description;
        if (offerSdp == null) {
            onError("本地 SDP 为空");
            return;
        }
        setStatus("正在与服务器协商…");
        networkExecutor.execute(() -> {
            try {
                WhepClient.WhepResponse response = WhepClient.postOffer(whepUrl, offerSdp);
                sessionLocation.set(response.location);
                mainHandler.post(() -> applyAnswer(response.sdpAnswer));
            } catch (Exception e) {
                String message = e.getMessage() == null ? "WHEP 请求失败" : e.getMessage();
                mainHandler.post(() -> onError(message));
            }
        });
    }

    private void applyAnswer(String sdpAnswer) {
        PeerConnection pc = peerConnection;
        if (pc == null) return;
        SessionDescription answer = new SessionDescription(SessionDescription.Type.ANSWER, sdpAnswer);
        pc.setRemoteDescription(new SimpleSdpObserver() {
            @Override
            public void onSetSuccess() {
                setStatus("已建立信令，等待媒体流…");
                reconnecting = false;
            }

            @Override
            public void onSetFailure(String error) {
                onError("设置远端 SDP 失败: " + error);
                reconnecting = false;
            }
        }, answer);
    }

    // endregion

    // region WebRTC 事件

    private final PeerConnection.Observer peerConnectionObserver = new PeerConnection.Observer() {
        @Override public void onSignalingChange(PeerConnection.SignalingState state) {}
        @Override public void onIceConnectionChange(PeerConnection.IceConnectionState state) {}
        @Override public void onIceCandidatesRemoved(IceCandidate[] candidates) {}
        @Override public void onAddStream(org.webrtc.MediaStream stream) {}
        @Override public void onRemoveStream(org.webrtc.MediaStream stream) {}
        @Override public void onDataChannel(org.webrtc.DataChannel dataChannel) {}
        @Override public void onRenegotiationNeeded() {}
        @Override public void onIceConnectionReceivingChange(boolean receiving) {}

        @Override
        public void onConnectionChange(PeerConnection.PeerConnectionState newState) {
            Log.i(TAG, "PeerConnection state = " + newState);
            if (newState == PeerConnection.PeerConnectionState.CONNECTED) {
                isLive = true;
                setStatus("● 直播中");
            } else if (newState == PeerConnection.PeerConnectionState.DISCONNECTED) {
                setStatus("连接中断，尝试恢复…");
            } else if (newState == PeerConnection.PeerConnectionState.FAILED) {
                if (!released) setStatus("连接失败");
            } else if (newState == PeerConnection.PeerConnectionState.CLOSED) {
                if (!released) setStatus("连接已关闭");
            }
        }

        @Override public void onIceGatheringChange(PeerConnection.IceGatheringState state) {}
        @Override public void onIceCandidate(IceCandidate candidate) {}
        @Override public void onAddTrack(RtpReceiver receiver, org.webrtc.MediaStream[] streams) {}

        @Override
        public void onTrack(RtpTransceiver transceiver) {
            if (transceiver == null || transceiver.getReceiver() == null) return;
            org.webrtc.MediaStreamTrack track = transceiver.getReceiver().track();
            if (track == null) return;
            if (track instanceof VideoTrack) {
                VideoTrack videoTrack = (VideoTrack) track;
                videoTrack.setEnabled(true);
                if (renderer != null) videoTrack.addSink(renderer);
            } else if (track instanceof AudioTrack) {
                AudioTrack audioTrack = (AudioTrack) track;
                audioTrack.setEnabled(true);
                try { audioTrack.setVolume(1.0); } catch (Exception ignored) {}
            }
        }
    };

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

    /**
     * 只释放当前 WHEP 会话：DELETE 服务端会话 + dispose PeerConnection。
     * 保留 factory / eglBase / renderer / audioDeviceModule，以便切换摄像头后快速重建连接。
     */
    private void releaseSession() {
        mainHandler.removeCallbacksAndMessages(null);
        final String location = sessionLocation.getAndSet(null);
        networkExecutor.execute(() -> WhepClient.deleteSession(location));

        if (peerConnection != null) {
            try { peerConnection.dispose(); } catch (Exception ignored) {}
            peerConnection = null;
        }
        // 渲染器先清空，避免旧画面残留到新会话建立完成
        if (renderer != null) {
            try { renderer.clearImage(); } catch (Exception ignored) {}
        }
    }

    // endregion

    // region 完整释放

    /**
     * 完整释放：onDestroy 调用，不可逆。
     */
    private void release() {
        if (released) return;
        released = true;

        mainHandler.removeCallbacksAndMessages(null);

        final String location = sessionLocation.getAndSet(null);
        networkExecutor.execute(() -> WhepClient.deleteSession(location));
        networkExecutor.shutdown();

        if (peerConnection != null) {
            try { peerConnection.dispose(); } catch (Exception ignored) {}
            peerConnection = null;
        }
        if (factory != null) {
            try { factory.dispose(); } catch (Exception ignored) {}
            factory = null;
        }
        if (renderer != null) {
            try { renderer.release(); } catch (Exception ignored) {}
            renderer = null;
        }
        if (eglBase != null) {
            try { eglBase.release(); } catch (Exception ignored) {}
            eglBase = null;
        }
        if (audioDeviceModule != null) {
            try { audioDeviceModule.release(); } catch (Exception ignored) {}
            audioDeviceModule = null;
        }
    }

    // endregion

    // region helpers

    private void setStatus(final String text) {
        if (released) return;
        runOnUiThread(() -> {
            if (!isFinishing()) binding.statusText.setText(text);
        });
    }

    private void onError(String message) {
        if (released) return;
        Log.e(TAG, "播放错误: " + message);
        setStatus("错误：" + message);
    }

    private static String orEmpty(String s) {
        return s == null ? "" : s;
    }

    private abstract static class SimpleSdpObserver implements SdpObserver {
        @Override public void onCreateSuccess(SessionDescription sessionDescription) {}
        @Override public void onSetSuccess() {}
        @Override public void onCreateFailure(String error) {}
        @Override public void onSetFailure(String error) {}
    }

    // endregion
}
