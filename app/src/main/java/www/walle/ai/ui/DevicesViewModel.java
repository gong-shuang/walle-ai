package www.walle.ai.ui;

import android.app.Application;
import android.os.Handler;
import android.os.Looper;

import androidx.annotation.NonNull;
import androidx.lifecycle.LiveData;
import androidx.lifecycle.MutableLiveData;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

import www.walle.ai.net.ConnState;
import www.walle.ai.net.DeviceInfo;
import www.walle.ai.net.TopicNames;

/**
 * 设备列表页 ViewModel：负责「3 秒扫描窗口」与可见列表的结算。
 *
 * <p>规则（与改造前一致）：</p>
 * <ul>
 *   <li>进入页面 / 手动刷新 → 在租户内广播 discovery，开一个 3 秒窗口；</li>
 *   <li>窗口内收到 {@code discovery/result} 的设备才会出现在列表里；</li>
 *   <li>窗口结束时整体替换列表；原选中设备不在列表里则自动切到第一台可见设备；</li>
 *   <li>{@code online} 只用于更新在线状态，不新增卡片（新上线的设备由 onDeviceAdded 进入）。</li>
 * </ul>
 */
public class DevicesViewModel extends MqttViewModel {

    public static final class UiState {
        public final List<DeviceInfo> visible;
        public final String selectedKey;
        public final boolean scanning;
        public final boolean connected;

        UiState(List<DeviceInfo> visible, String selectedKey, boolean scanning, boolean connected) {
            this.visible = visible;
            this.selectedKey = selectedKey;
            this.scanning = scanning;
            this.connected = connected;
        }

        public int onlineCount() {
            int n = 0;
            for (DeviceInfo d : visible) if (d.online) n++;
            return n;
        }
    }

    private static final long SCAN_TIMEOUT_MS = 3000L;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private final MutableLiveData<UiState> state = new MutableLiveData<>();
    private final MutableLiveData<String> toast = new MutableLiveData<>();

    /** 当前列表展示的设备键（上一次扫描窗口结算的结果） */
    private final Set<String> shownKeys = new HashSet<>();
    /** 本次窗口内已回包的设备键 */
    private final Set<String> pendingKeys = new HashSet<>();
    private boolean scanning;
    private boolean connected;

    private final Runnable finishScan = () -> {
        scanning = false;
        shownKeys.clear();
        shownKeys.addAll(pendingKeys);
        pendingKeys.clear();
        // 原选中设备这次没回包 → 自动切到第一台可见设备，没有则清空
        if (!shownKeys.contains(mgr.getSelectedKey())) {
            mgr.selectDevice(firstVisibleKey());
        }
        publish();
    };

    public DevicesViewModel(@NonNull Application app) {
        super(app);
        connected = mgr.isConnected();
        publish();
    }

    public LiveData<UiState> state() {
        return state;
    }

    /** 一次性提示（Toast）；消费后调用 {@link #clearToast()}。 */
    public LiveData<String> toast() {
        return toast;
    }

    public void clearToast() {
        emit(toast, null);
    }

    /** 扫描一次；manual=true 时在未连接的情况下提示用户。 */
    public void scan(boolean manual) {
        if (!mgr.isConnected()) {
            if (manual) emit(toast, "尚未连接 MQTT 服务器");
            publish();
            return;
        }
        scanning = true;
        pendingKeys.clear();
        mgr.requestDeviceInfo();
        handler.removeCallbacks(finishScan);
        handler.postDelayed(finishScan, SCAN_TIMEOUT_MS);
        publish();
    }

    public void select(String key) {
        mgr.selectDevice(key);
        publish();
    }

    @Override
    public void onDeviceInfo(DeviceInfo device) {
        // 只有窗口内的回包才计入本轮结果；窗口外到达的不新增卡片
        if (scanning) pendingKeys.add(device.key());
        publish();
    }

    @Override
    public void onDeviceAdded(DeviceInfo device) {
        shownKeys.add(device.key());
        if (scanning) pendingKeys.add(device.key());
        publish();
    }

    @Override
    public void onDevicesChanged() {
        // 选中项 / 在线状态可能变了
        if (!mgr.isConnected()) connected = false;
        publish();
    }

    @Override
    public void onOnline(JSONObject payload) {
        publish();
    }

    @Override
    public void onConnState(ConnState connState, String info) {
        connected = connState == ConnState.CONNECTED;
        if (connected) {
            scan(false);       // 连上即扫描一次
        } else {
            publish();
        }
    }

    @Override
    public void onDeliveryError(String kind, String error) {
        if (TopicNames.KIND_DISCOVERY.equals(kind)) {
            handler.removeCallbacks(finishScan);
            scanning = false;
            pendingKeys.clear();
            emit(toast, error);
            publish();
        }
    }

    @Override
    protected void onCleared() {
        handler.removeCallbacksAndMessages(null);
        super.onCleared();
    }

    private String firstVisibleKey() {
        for (DeviceInfo d : mgr.getDevices()) {
            if (shownKeys.contains(d.key())) return d.key();
        }
        return "";
    }

    private void publish() {
        if (!onMainThread()) {          // WebRTC 回调可能在 signaling 线程
            runOnMain(this::publish);
            return;
        }
        List<DeviceInfo> visible = new ArrayList<>();
        for (DeviceInfo d : mgr.getDevices()) {
            if (shownKeys.contains(d.key())) visible.add(d);
        }
        state.setValue(new UiState(visible, mgr.getSelectedKey(), scanning, connected));
    }
}
