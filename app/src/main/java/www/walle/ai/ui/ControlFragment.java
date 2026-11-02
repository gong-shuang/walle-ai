package www.walle.ai.ui;

import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.AdapterView;
import android.widget.ImageView;
import android.widget.ProgressBar;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.fragment.app.Fragment;
import androidx.lifecycle.ViewModelProvider;

import org.webrtc.SurfaceViewRenderer;

import java.util.ArrayList;
import java.util.List;

import www.walle.ai.BuildConfig;
import www.walle.ai.R;
import www.walle.ai.model.CameraCapability;
import www.walle.ai.model.DeviceCapability;

/**
 * 摄像头/推流页：只负责渲染 {@link ControlViewModel.UiState} 与本地播放器（{@link WhepSession}）的生命周期。
 *
 * <p>能力查询、request_id 配对、推流状态机、8 秒超时都在 ViewModel 里；页面不再实现 MQTT 监听。</p>
 */
public class ControlFragment extends Fragment {

    private ControlViewModel viewModel;

    private Spinner spinnerCamera;
    private TextView tvVideoStatus;
    private TextView tvAudioInfo;
    private ImageView btnPlay;
    private ImageView ivFullscreen;
    private ProgressBar pbVideo;
    private SurfaceViewRenderer videoRenderer;

    private CameraSpinnerAdapter cameraAdapter;
    private final List<CameraSpinnerAdapter.Camera> adapterItems = new ArrayList<>();

    private WhepSession player;
    /** 当前播放器实际拉取的房间号（幂等：防止重复结果把播放器初始化两次）。 */
    private String playingRoom = "";
    /** 程序化修改 Spinner 选中项时，忽略回调。 */
    private boolean bindingSpinner;
    /** 详情页首次打开时点了设备、但 ViewModel 尚未就绪，待 onViewCreated 后补查能力。 */
    private boolean pendingDeviceSwitchQuery;

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
        viewModel = new ViewModelProvider(this).get(ControlViewModel.class);

        spinnerCamera = view.findViewById(R.id.spinnerCamera);
        tvVideoStatus = view.findViewById(R.id.tvVideoStatus);
        tvAudioInfo = view.findViewById(R.id.tvAudioInfo);
        btnPlay = view.findViewById(R.id.btnPlay);
        ivFullscreen = view.findViewById(R.id.ivFullscreen);
        pbVideo = view.findViewById(R.id.pbVideo);
        videoRenderer = view.findViewById(R.id.videoRenderer);

        cameraAdapter = new CameraSpinnerAdapter(requireContext(), adapterItems);
        spinnerCamera.setAdapter(cameraAdapter);
        spinnerCamera.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            @Override
            public void onItemSelected(AdapterView<?> parent, View v, int position, long id) {
                if (bindingSpinner) return;
                ControlViewModel.UiState ui = viewModel.state().getValue();
                if (ui == null || position < 0 || position >= ui.cameras.size()) return;
                viewModel.selectCamera(ui.cameras.get(position).node);
            }

            @Override
            public void onNothingSelected(AdapterView<?> parent) {
            }
        });

        view.findViewById(R.id.flVideo).setOnClickListener(v -> viewModel.togglePlay());
        btnPlay.setOnClickListener(v -> viewModel.togglePlay());
        ivFullscreen.setOnClickListener(v -> openFullscreen());

        viewModel.state().observe(getViewLifecycleOwner(), this::render);
        viewModel.toast().observe(getViewLifecycleOwner(), msg -> {
            if (msg != null && !msg.isEmpty()) {
                Toast.makeText(requireContext(), msg, Toast.LENGTH_SHORT).show();
                viewModel.clearToast();
            }
        });

        // 详情页首次打开就点了设备：这里补发能力查询，保证点设备正好两个主题
        if (pendingDeviceSwitchQuery) {
            pendingDeviceSwitchQuery = false;
            viewModel.onDeviceSwitched();
        }
    }

    // ---------------------------------------------------------------- 生命周期

    @Override
    public void onStart() {
        super.onStart();
        // 隐藏的子页也会走 onStart：此时不能查能力，否则启动/切页会多发 capability/query
        if (!isHidden()) viewModel.onScreenVisible();
    }

    @Override
    public void onStop() {
        viewModel.onScreenStopped();
        releasePlayer();            // 离开页面只释放本地播放器，推流在设备端继续
        super.onStop();
    }

    @Override
    public void onHiddenChanged(boolean hidden) {
        super.onHiddenChanged(hidden);
        if (viewModel == null) return;
        if (hidden) {
            viewModel.onScreenHidden();
            releasePlayer();        // 切走 tab 同样只释放播放器，不下发 video/stop
        } else {
            viewModel.onScreenVisible();
        }
    }

    @Override
    public void onDestroyView() {
        if (viewModel != null) viewModel.onDestroyed();
        releasePlayer();
        super.onDestroyView();
    }

    /**
     * 设备列表里显式点了某台设备：立即查一次能力（与状态页的 state/query 配对）。
     * <p>即使本页当前不可见也要查，这样用户点「控制」tab 时能力已经就绪、不需再发一次。</p>
     */
    public void onDeviceSwitched() {
        if (!isAdded()) return;
        if (viewModel == null) {
            // 详情页首次打开时本 Fragment 可能还没走 onViewCreated，先记下待查询
            pendingDeviceSwitchQuery = true;
            return;
        }
        pendingDeviceSwitchQuery = false;
        viewModel.onDeviceSwitched();
    }

    // ---------------------------------------------------------------- 渲染

    private void render(ControlViewModel.UiState ui) {
        if (cameraAdapter == null) return;

        syncAdapter(ui.cameras);
        bindingSpinner = true;
        int idx = indexOfNode(ui.cameras, ui.selectedNode);
        if (idx >= 0 && spinnerCamera.getSelectedItemPosition() != idx) {
            spinnerCamera.setSelection(idx);
            cameraAdapter.setSelectedPosition(idx);
        }
        bindingSpinner = false;
        spinnerCamera.setEnabled(!ui.capabilityLoading && !ui.cameras.isEmpty());

        tvVideoStatus.setText(ui.status);
        renderAudioInfo(ui);
        pbVideo.setVisibility(ui.phase == ControlViewModel.Phase.STARTING ? View.VISIBLE : View.GONE);

        boolean idle = ui.phase == ControlViewModel.Phase.IDLE;
        btnPlay.setVisibility(idle ? View.VISIBLE : View.GONE);
        btnPlay.setEnabled(ui.canPlay());
        ivFullscreen.setVisibility(ui.phase == ControlViewModel.Phase.LIVE ? View.VISIBLE : View.GONE);

        syncPlayer(ui);
    }

    private void syncAdapter(List<CameraCapability.Camera> cameras) {
        boolean changed = adapterItems.size() != cameras.size();
        if (!changed) {
            for (int i = 0; i < cameras.size(); i++) {
                CameraSpinnerAdapter.Camera item = adapterItems.get(i);
                CameraCapability.Camera c = cameras.get(i);
                if (!item.node.equals(c.node) || !item.name.equals(c.name)) {
                    changed = true;
                    break;
                }
            }
        }
        if (!changed) return;
        adapterItems.clear();
        for (CameraCapability.Camera c : cameras) {
            adapterItems.add(new CameraSpinnerAdapter.Camera(c.node, c.name));
        }
        cameraAdapter.notifyDataSetChanged();
    }

    private static int indexOfNode(List<CameraCapability.Camera> cameras, String node) {
        if (node == null || node.isEmpty()) return -1;
        for (int i = 0; i < cameras.size(); i++) {
            if (node.equals(cameras.get(i).node)) return i;
        }
        return -1;
    }

    /** 麦克风 / 音响能力一行展示：查询中或无设备时隐藏，避免闪屏噪音。 */
    private void renderAudioInfo(ControlViewModel.UiState ui) {
        if (ui.capabilityLoading || !ui.hasDevice) {
            tvAudioInfo.setVisibility(View.GONE);
            return;
        }
        StringBuilder sb = new StringBuilder();
        DeviceCapability.AudioEnd cap = ui.audioCapture;
        if (cap.available) {
            sb.append("麦克风：").append(cap.device.isEmpty() ? "default" : cap.device);
        } else {
            sb.append("麦克风：无");
        }
        DeviceCapability.AudioEnd play = ui.audioPlayback;
        if (play.available) {
            sb.append(" · 音响：");
            if (play.volumePercent >= 0) sb.append(play.volumePercent).append("%");
            else sb.append("有");
            if (play.muted) sb.append("（静音）");
        } else {
            sb.append(" · 音响：无");
        }
        tvAudioInfo.setText(sb);
        tvAudioInfo.setVisibility(View.VISIBLE);
    }

    // ---------------------------------------------------------------- 播放器

    /** 状态里的 room 驱动播放器：有房间就（按需）建会话，没房间就释放。 */
    private void syncPlayer(ControlViewModel.UiState ui) {
        String room = ui.room == null ? "" : ui.room;
        if (room.isEmpty()) {
            releasePlayer();
            return;
        }
        if (room.equals(playingRoom) && player != null && !player.isReleased()) {
            return;
        }
        releasePlayer();
        beginPlayback(room);
    }

    private void beginPlayback(String room) {
        if (!isAdded() || isHidden()) return;   // 隐藏时不建播放器；回到前台由 onScreenVisible 补建
        String url = BuildConfig.WHEP_URL_TEMPLATE.replace("{room}", Uri.encode(room));
        playingRoom = room;
        player = new WhepSession(requireContext().getApplicationContext(), videoRenderer,
                new WhepSession.Callback() {
                    @Override
                    public void onStatus(String text) {
                        viewModel.onPlayerStatus(text);
                    }

                    @Override
                    public void onFirstFrame() {
                        // 首帧上屏：状态已由 ViewModel 维护，这里不需要额外动作
                    }

                    @Override
                    public void onError(String message) {
                        viewModel.onPlayerError(message);
                    }

                    @Override
                    public void onConnectionState(org.webrtc.PeerConnection.PeerConnectionState state) {
                        viewModel.onPlayerConnectedState(state);
                    }
                });
        player.start(url);
    }

    private void releasePlayer() {
        if (player != null) {
            player.release();
            player = null;
        }
        playingRoom = "";
    }

    /** 放大观看：同一房间号交给全屏播放页，推流所有权转交 PlayerActivity。 */
    private void openFullscreen() {
        if (!isAdded()) return;
        ControlViewModel.UiState ui = viewModel.state().getValue();
        if (ui == null || ui.room == null || ui.room.isEmpty()) return;

        Intent intent = new Intent(requireContext(), PlayerActivity.class);
        intent.putExtra(PlayerActivity.EXTRA_ROOM, ui.room);
        if (!ui.cameras.isEmpty()) {
            StringBuilder sb = new StringBuilder("[");
            for (int i = 0; i < ui.cameras.size(); i++) {
                CameraCapability.Camera c = ui.cameras.get(i);
                if (i > 0) sb.append(',');
                sb.append("{\"node\":\"").append(c.node).append('"')
                        .append(",\"name\":\"").append(c.name.replace("\"", "\\\"")).append("\"}");
            }
            sb.append(']');
            intent.putExtra(PlayerActivity.EXTRA_CAMERAS_JSON, sb.toString());
        }
        intent.putExtra(PlayerActivity.EXTRA_CAMERA, ui.activeCamera);
        intent.putExtra(PlayerActivity.EXTRA_DEVICE_KEY, ui.deviceKey);
        intent.putExtra(PlayerActivity.EXTRA_UUID, ui.deviceUuid);

        releasePlayer();
        viewModel.onHandoffToFullscreen();
        startActivity(intent);
    }
}
