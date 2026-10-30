package www.walle.ai.ui;

import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.view.Window;
import android.view.WindowManager;
import android.widget.AdapterView;

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
    private static final String WHEP_URL = BuildConfig.WHEP_URL;
    private String whepUrl = WHEP_URL;
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

    // region 渲染

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
            Insets navInsets = insets.getInsets(WindowInsetsCompat.Type.navigationBars());
            binding.backButton.setTranslationY(topInsets.top);
            binding.spinnerCamera.setTranslationY(topInsets.top);
            binding.statusText.setTranslationY(-navInsets.bottom);
            return WindowInsetsCompat.CONSUMED;
        });
    }

    // endregion

    private static String orEmpty(String s) {
        return s == null ? "" : s;
    }
}
