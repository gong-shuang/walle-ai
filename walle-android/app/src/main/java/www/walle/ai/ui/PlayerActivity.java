package www.walle.ai.ui;

import android.Manifest;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.view.Window;
import android.view.WindowManager;
import android.widget.AdapterView;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsCompat;
import androidx.core.view.WindowInsetsControllerCompat;
import androidx.lifecycle.ViewModelProvider;

import org.webrtc.PeerConnection;

import www.walle.ai.BuildConfig;
import www.walle.ai.R;
import www.walle.ai.databinding.ActivityPlayerBinding;
import www.walle.ai.model.CameraCapability;
import www.walle.ai.net.MqttManager;
import www.walle.ai.net.WhipAudioSession;

import java.util.ArrayList;
import java.util.List;

/**
 * WHEP 拉流播放页（纯 Java 实现，横屏全屏）。
 *
 * <p>主要职责：
 * <ol>
 *   <li>从控制页接手一路推流（房间号 + 当前摄像头），通过 WHEP / WebRTC 拉流上屏；</li>
 *   <li>支持在播放过程中切换设备端摄像头（发 video/start 重启 ffmpeg），
 *       房间号不变 → PeerConnection 不动 → 只换设备端视频源；</li>
 *   <li>退出时释放会话并通知设备端停流。</li>
 * </ol>
 *
 * <p>「该选哪个摄像头 / 请求是否在途 / 状态条写什么」全部由 {@link PlayerViewModel} 决定，
 * 本类只做两件事：把 {@code UiState} 画到屏幕上，以及管理 WebRTC 相关的原生资源。</p>
 */
public class PlayerActivity extends AppCompatActivity {

    // —— Intent extras ——
    /** 控制页「放大」进入时携带的房间号；为空表示使用固定演示地址。 */
    public static final String EXTRA_ROOM = "extra_room";
    /** 设备真实 uuid（用于校验 result 归属，不参与主题）。 */
    public static final String EXTRA_UUID = "extra_uuid";
    /** 设备主题键（device_id/hostname/uuid 归一化后的值），发 MQTT 必须用这个。 */
    public static final String EXTRA_DEVICE_KEY = "extra_device_key";
    /** 控制页携带的「当前推流摄像头」节点，如 /dev/video0。 */
    public static final String EXTRA_CAMERA = "extra_camera";
    /** 控制页携带的完整摄像头列表，JSON 字符串：[{"node":"/dev/video0","name":"PSI USB CAMERA"}, ...] */
    public static final String EXTRA_CAMERAS_JSON = "extra_cameras_json";

    // —— 会话 / 播放相关 ——
    // 进入本页必定携带房间号（见 ControlFragment.openFullscreen 的空值守卫），
    // onCreate 会用 BuildConfig.WHEP_URL_TEMPLATE 按房间号填入实际拉流地址
    private String whepUrl = "";
    private String roomNumber = "";

    /** spinner 的数据源（View 层自有：VM 只给不可变快照）。 */
    private final List<CameraSpinnerAdapter.Camera> cameraItems = new ArrayList<>();
    private CameraSpinnerAdapter cameraAdapter;

    private ActivityPlayerBinding binding;
    private PlayerViewModel viewModel;

    /** 正在按 state 纠正 spinner 选中，此时回调不应被当成用户操作。 */
    private boolean rendering;

    // —— 拉流会话（与控制页共用同一份实现）——
    private WhepSession session;

    /** 页面是否已销毁（onDestroy 后置 true，用于丢弃迟到的回调）。 */
    private volatile boolean released;

    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    // —— 反向音频（手机麦克风 → 设备端：WebRTC WHIP 上行）——
    private static final int REQ_RECORD_AUDIO = 2001;
    private WhipAudioSession audioSession;
    /** 麦克风是否已成功开启（WHIP 已连上媒体面）。 */
    private boolean micOn;
    /** 已点击开启、正在建立 WHIP 连接（此期间再点一次表示取消）。 */
    private boolean micStarting;
    /** 发 phone/audio/* 的目标设备（真实 uuid 优先，回退主题键）。 */
    private String micTarget = "";
    private MqttManager mgr;

    // —— 车控摇杆（左下角，car/motor/set）——
    /** 目标设备：真实 uuid 优先，回退主题键。 */
    private String carTarget = "";
    /** 摇杆的线速度，与 Web 测试页保持一致（协议要求 0.5–1.0 m/s）。 */
    private static final double CAR_SPEED = 0.6;
    /** 按住期间的重发周期；设备端 500ms 收不到命令会自动停车。 */
    private static final long CAR_SEND_INTERVAL_MS = 200L;
    /** 死区：归一化行程小于它视为未操作（内圈可自由晃动但不发指令）。 */
    private static final float CAR_DEAD_ZONE = 0.18f;
    /** sin(22.5°)：某个轴的分量占比超过它才参与方向判定，用于区分四向与斜向。 */
    private static final float CAR_DIAGONAL_SIN = 0.38268f;

    /** 当前摇杆方向（同时只有一个平移、最多一个旋转）。 */
    private boolean carForward;
    private boolean carBack;
    private boolean carRotateLeft;
    private boolean carRotateRight;
    /** 是否已有生效方向（用于「松手/回死区才发停止」）。 */
    private boolean carActive;

    private final Runnable carSendTick = new Runnable() {
        @Override
        public void run() {
            if (!carActive) return;
            sendCarCommand();
            mainHandler.postDelayed(this, CAR_SEND_INTERVAL_MS);
        }
    };

    // region 生命周期

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        binding = ActivityPlayerBinding.inflate(getLayoutInflater());
        setContentView(binding.getRoot());

        hideStatusBarAndKeepScreenOn();

        // 解析 intent
        roomNumber = orEmpty(getIntent().getStringExtra(EXTRA_ROOM));
        if (!roomNumber.isEmpty()) {
            whepUrl = BuildConfig.WHEP_URL_TEMPLATE.replace("{room}", Uri.encode(roomNumber));
        }

        viewModel = new ViewModelProvider(this).get(PlayerViewModel.class);
        viewModel.load(getIntent().getStringExtra(EXTRA_DEVICE_KEY),
                getIntent().getStringExtra(EXTRA_UUID),
                roomNumber,
                getIntent().getStringExtra(EXTRA_CAMERA),
                getIntent().getStringExtra(EXTRA_CAMERAS_JSON));

        // 摄像头 Spinner：数据源在本类，选中态由 VM 决定
        cameraAdapter = new CameraSpinnerAdapter(this, cameraItems,
                R.layout.item_spinner_camera_selected_dark);
        binding.spinnerCamera.setAdapter(cameraAdapter);
        binding.spinnerCamera.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            @Override
            public void onItemSelected(AdapterView<?> parent, View v, int position, long id) {
                if (rendering) {
                    cameraAdapter.setSelectedPosition(position);
                    return;
                }
                // 用户手选：交给 VM 判断是否发请求；render() 会把选中态纠回真实状态
                viewModel.selectCamera(position);
            }

            @Override
            public void onNothingSelected(AdapterView<?> parent) {
            }
        });

        viewModel.state().observe(this, this::render);

        binding.backButton.setOnClickListener(v -> finish());

        // 反向音频：目标设备 + 麦克风按钮（默认关闭）
        String deviceKey = orEmpty(getIntent().getStringExtra(EXTRA_DEVICE_KEY));
        String deviceUuid = orEmpty(getIntent().getStringExtra(EXTRA_UUID));
        micTarget = !deviceUuid.isEmpty() ? deviceUuid : deviceKey;
        mgr = MqttManager.get(getApplicationContext());
        binding.micButton.setText(R.string.mic_off);
        binding.micButton.setOnClickListener(v -> toggleMic());

        // 车控摇杆：按住期间每 200ms 下发一次，松手立即下发停止
        carTarget = micTarget;
        binding.joystick.setListener(new JoystickView.Listener() {
            @Override
            public void onJoystickMove(float nx, float ny) {
                // 注意：必须显式限定外层，否则会递归调用到本方法自身
                PlayerActivity.this.handleJoystickMove(nx, ny);
            }

            @Override
            public void onJoystickRelease() {
                stopCarControl();
            }
        });

        // 创建拉流会话（内部负责 EGL / Factory / 渲染器初始化）并开始协商
        binding.firstFrameMask.setAlpha(1f);
        binding.firstFrameMask.setVisibility(View.VISIBLE);
        session = new WhepSession(getApplicationContext(), binding.remoteRenderer,
                new WhepSession.Callback() {
                    @Override
                    public void onStatus(String text) {
                        viewModel.postStatus(text);
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
                        viewModel.postStatus("错误：" + message);
                    }

                    @Override
                    public void onConnectionState(PeerConnection.PeerConnectionState state) {
                        if (released) return;
                        switch (state) {
                            case CONNECTED:
                                viewModel.postStatus("● 直播中");
                                break;
                            case DISCONNECTED:
                                viewModel.postStatus("连接中断，尝试恢复…");
                                break;
                            case FAILED:
                                viewModel.postStatus("连接失败");
                                break;
                            case CLOSED:
                                viewModel.postStatus("连接已关闭");
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
                viewModel.postStatus("等待主播推流中…");
            }
        }, 10_000);
    }

    @Override
    protected void onDestroy() {
        released = true;
        mainHandler.removeCallbacksAndMessages(null);

        // 退出页面先停车，避免摇杆还按着时车继续动
        stopCarControl();

        // 退出页面 = 退出这个播放会话：麦克风开着就停采集，并通知设备停止拉音频流
        stopMic(true);

        // 释放拉流会话（EGL / Factory / 渲染器一起释放）
        if (session != null) {
            session.release();
            session = null;
        }
        // 通知设备端停流（由 VM 保证只发一次；没有房间号/设备键时自然不发）
        if (viewModel != null) viewModel.stopStreamOnExit();

        super.onDestroy();
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) hideStatusBarAndKeepScreenOn();
    }

    // endregion

    // region 反向音频（手机麦克风 → 设备端）

    /** 麦克风按钮：关 →（建 WHIP 音频连接 + 通知设备）→ 开；再点一次 → 停采集 + 通知设备停止。 */
    private void toggleMic() {
        if (micStarting) {                       // 正在建立连接时再点 = 取消
            micStarting = false;
            if (audioSession != null) {
                audioSession.stop();
                audioSession = null;
            }
            updateMicUi();
            viewModel.postStatus("已取消开启麦克风");
            return;
        }
        if (micOn) {
            stopMic(true);
            viewModel.postStatus("麦克风已关闭，设备停止拉取音频");
            return;
        }
        if (roomNumber.isEmpty()) {
            viewModel.postStatus(getString(R.string.mic_no_room));
            return;
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, REQ_RECORD_AUDIO);
            return;
        }
        startMic();
    }

    private void startMic() {
        if (audioSession != null && audioSession.isRunning()) return;
        // 复用视频会话的 PeerConnectionFactory：一个进程只能有一个音频设备模块（ADM）
        WhepSession videoSession = session;
        if (videoSession == null || videoSession.isReleased() || videoSession.getFactory() == null) {
            viewModel.postStatus("视频会话未就绪，无法开启麦克风");
            return;
        }
        // 音频流名固定为 {room}_audio，避免和设备推的视频抢同一个 stream
        String url = BuildConfig.WHIP_AUDIO_URL_TEMPLATE.replace("{room}", Uri.encode(roomNumber));
        // 构建期没注入 Oryx 推流 secret 时直接给出可操作提示，避免只看到服务端 502
        if (!url.contains("secret=")) {
            viewModel.postStatus("未配置 Oryx 推流 secret，请用 -PoryxSecret=... 或环境变量 "
                    + "WHIP_SECRET 重新构建 App");
            return;
        }
        micStarting = true;
        updateMicUi();
        viewModel.postStatus("正在开启麦克风…");

        WhipAudioSession audio = new WhipAudioSession(videoSession.getFactory());
        audioSession = audio;
        audio.start(url, new WhipAudioSession.Callback() {
            @Override
            public void onConnected() {
                micStarting = false;
                if (released) return;
                micOn = true;
                updateMicUi();
                // 媒体通道已就绪，再通知设备来拉，避免设备端 WHEP 面对不存在的流
                if (mgr != null && !micTarget.isEmpty()) {
                    mgr.sendPhoneAudio(micTarget, roomNumber);
                }
                viewModel.postStatus("麦克风已开启，设备开始拉取音频");
            }

            @Override
            public void onError(String message) {
                micStarting = false;
                if (released) return;
                micOn = false;
                if (audioSession != null) {
                    audioSession.stop();
                    audioSession = null;
                }
                updateMicUi();
                viewModel.postStatus(getString(R.string.mic_error, message));
            }
        });
    }

    /** 停止采集；只有在「真的开过」时才通知设备，避免退出时误发 stop。 */
    private void stopMic(boolean notifyDevice) {
        boolean wasOn = micOn;
        micOn = false;
        micStarting = false;
        if (audioSession != null) {
            audioSession.stop();
            audioSession = null;
        }
        updateMicUi();
        if (wasOn && notifyDevice && mgr != null && !micTarget.isEmpty()) {
            mgr.stopPhoneAudio(micTarget, roomNumber);
        }
    }

    private void updateMicUi() {
        if (binding == null) return;
        binding.micButton.setText(micStarting ? R.string.mic_starting
                : (micOn ? R.string.mic_on : R.string.mic_off));
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, @NonNull String[] permissions,
                                           @NonNull int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode != REQ_RECORD_AUDIO) return;
        if (grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
            startMic();
        } else {
            viewModel.postStatus(getString(R.string.mic_permission_denied));
        }
    }

    // endregion

    // region 车控摇杆（car/motor/set）

    /**
     * 摇杆位置 → 方向：上下为前进/后退，左右为左旋/右旋，斜向同时下发。
     *
     * <p>用「单轴分量占比」划分 8 个扇区：占比低于 sin(22.5°) 的轴不参与，
     * 于是 ±22.5° 内是纯前进/后退/旋转，22.5°~67.5° 是斜向，其余是纯旋转。</p>
     */
    private void handleJoystickMove(float nx, float ny) {
        float magnitude = (float) Math.hypot(nx, ny);
        if (magnitude < CAR_DEAD_ZONE) {
            stopCarControl();
            return;
        }
        float ax = Math.abs(nx);
        float ay = Math.abs(ny);
        boolean forward = ay / magnitude >= CAR_DIAGONAL_SIN && ny < 0;
        boolean back = ay / magnitude >= CAR_DIAGONAL_SIN && ny > 0;
        boolean rotateLeft = ax / magnitude >= CAR_DIAGONAL_SIN && nx < 0;
        boolean rotateRight = ax / magnitude >= CAR_DIAGONAL_SIN && nx > 0;

        boolean changed = forward != carForward || back != carBack
                || rotateLeft != carRotateLeft || rotateRight != carRotateRight;
        carForward = forward;
        carBack = back;
        carRotateLeft = rotateLeft;
        carRotateRight = rotateRight;

        // 方向变化（含由静止进入操作）立即下发一次，然后按 200ms 周期重发
        if (!carActive || changed) {
            carActive = true;
            sendCarCommand();
            mainHandler.removeCallbacks(carSendTick);
            mainHandler.postDelayed(carSendTick, CAR_SEND_INTERVAL_MS);
        }
    }

    /** 松手 / 回到死区 / 退出页面：停发并下发一次停止命令。 */
    private void stopCarControl() {
        mainHandler.removeCallbacks(carSendTick);
        if (!carActive) {
            clearCarDirection();
            return;
        }
        carActive = false;
        clearCarDirection();
        if (mgr != null && !carTarget.isEmpty()) {
            mgr.stopCarMotorDrive(carTarget);
        }
    }

    private void sendCarCommand() {
        if (mgr == null || carTarget.isEmpty()) return;
        mgr.sendCarMotorDrive(carTarget,
                carForward ? CAR_SPEED : 0,
                carBack ? CAR_SPEED : 0,
                0, 0,
                carRotateLeft, carRotateRight);
    }

    private void clearCarDirection() {
        carForward = false;
        carBack = false;
        carRotateLeft = false;
        carRotateRight = false;
    }

    // endregion

    private void render(@Nullable PlayerViewModel.UiState ui) {
        if (ui == null || released || binding == null) return;

        syncCameraItems(ui.cameras);

        binding.spinnerCamera.setVisibility(ui.showSpinner() ? View.VISIBLE : View.GONE);

        int target = ui.selectedIndex;
        if (target >= 0) {
            cameraAdapter.setSelectedPosition(target);
            if (binding.spinnerCamera.getSelectedItemPosition() != target) {
                rendering = true;
                binding.spinnerCamera.setSelection(target);
                rendering = false;
            }
        }
        if (!ui.status.isEmpty()) binding.statusText.setText(ui.status);
    }

    /** 把 VM 的不可变列表同步到 adapter 数据源；列表内容变了才重建（避免无谓闪烁）。 */
    private void syncCameraItems(List<CameraCapability.Camera> cameras) {
        if (cameraItems.size() == cameras.size()) {
            boolean same = true;
            for (int i = 0; i < cameras.size(); i++) {
                if (!cameraItems.get(i).node.equals(cameras.get(i).node)) {
                    same = false;
                    break;
                }
            }
            if (same) return;
        }
        cameraItems.clear();
        for (CameraCapability.Camera c : cameras) {
            cameraItems.add(new CameraSpinnerAdapter.Camera(c.node, c.name));
        }
        cameraAdapter.notifyDataSetChanged();
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
            // 四个悬浮控件同属顶部一行，整体下移，避免各自偏移导致高低不齐
            binding.topBar.setTranslationY(topInsets.top);
            return WindowInsetsCompat.CONSUMED;
        });
    }

    // endregion

    private static String orEmpty(String s) {
        return s == null ? "" : s;
    }
}
