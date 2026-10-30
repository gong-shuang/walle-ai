package www.walle.ai.ui;

import android.app.Application;
import android.os.Handler;
import android.os.Looper;

import androidx.annotation.NonNull;
import androidx.lifecycle.LiveData;
import androidx.lifecycle.MutableLiveData;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

import www.walle.ai.net.ConnState;
import www.walle.ai.net.DeviceInfo;
import www.walle.ai.net.TopicNames;

/**
 * 设备列表页 ViewModel。
 *
 * <p><b>列表 = 当前在线的设备</b>：直接取注册表里 {@code online == true} 的设备。
 * 数据来源是设备上线时发布的 <b>retained online</b>（内容就是完整身份）与掉线时的 LWT，
 * 订阅一次 broker 就会回放所有在线设备，不需要任何请求。</p>
 *
 * <p><b>为什么不再用 discovery 广播来结算列表</b>（技术债 N7）：设备发布的 retained
 * {@code online} 已经带全身份字段，App 每次连接只需 N 条报文就能建好列表；
 * 而「广播一次 + 每台设备答复 + 3 秒窗口结算」是 {@code 3N+1} 条报文，
 * 且每台手机<b>每次重连、每次切回本页</b>都会重放一遍 —— 设备数一多就是
 * {@code 手机数 × 重连次数 × 设备数} 的风暴，还会把每台设备从睡眠里叫醒来跑一遍
 * {@code psutil}/{@code v4l2}。所以 discovery 降级为<b>按需</b>动作：</p>
 * <ul>
 *   <li>下拉刷新 / 点「扫描」：用户显式要求刷新设备身份详情；</li>
 *   <li>兜底：连上后 {@link #FALLBACK_DISCOVERY_DELAY_MS} 毫秒内一台设备的 retained online
 *       都没回放出来（broker 无保留消息、或设备固件未发 retained），才广播一次。</li>
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

    /** 手动扫描的指示器最长展示时间（不参与列表结算）。 */
    private static final long SCAN_TIMEOUT_MS = 3000L;
    /** 连接后等 retained online 回放的宽限；过了还没设备才兜底广播 discovery。 */
    private static final long FALLBACK_DISCOVERY_DELAY_MS = 1500L;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private final MutableLiveData<UiState> state = new MutableLiveData<>();
    private final MutableLiveData<String> toast = new MutableLiveData<>();

    private boolean scanning;
    private boolean connected;
    /** 本次连接是否已做过兜底发现，避免反复触发。 */
    private boolean fallbackTried;

    private final Runnable finishScan = () -> {
        scanning = false;
        publish();
    };

    private final Runnable fallbackDiscovery = () -> {
        if (!connected || fallbackTried || mgr.onlineCount() > 0) return;
        fallbackTried = true;
        scan(false);
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

    /**
     * 按需发现：刷新设备身份详情。只在用户显式操作或兜底时调用，<b>不再</b>在每次连接/切页时自动调用。
     *
     * @param manual true = 用户点的（未连接时要给提示）
     */
    public void scan(boolean manual) {
        if (!mgr.isConnected()) {
            if (manual) emit(toast, "尚未连接 MQTT 服务器");
            publish();
            return;
        }
        scanning = true;
        fallbackTried = true;
        mgr.requestDeviceInfo();
        handler.removeCallbacks(finishScan);
        handler.postDelayed(finishScan, SCAN_TIMEOUT_MS);
        publish();
    }

    /** 页面可见：只刷新渲染，必要时安排一次兜底发现。 */
    public void onScreenVisible() {
        publish();
        if (connected && mgr.onlineCount() == 0) {
            handler.removeCallbacks(fallbackDiscovery);
            handler.postDelayed(fallbackDiscovery, FALLBACK_DISCOVERY_DELAY_MS);
        }
    }

    public void select(String key) {
        mgr.selectDevice(key);
        publish();
    }

    // ---------------------------------------------------------------- MqttManager.Listener

    @Override
    public void onDeviceInfo(DeviceInfo device) {
        // 身份详情刷新：注册表已更新，直接重渲染
        publish();
    }

    @Override
    public void onDeviceAdded(DeviceInfo device) {
        publish();
    }

    @Override
    public void onOnline(JSONObject payload) {
        publish();
    }

    @Override
    public void onDevicesChanged() {
        if (!mgr.isConnected()) connected = false;
        publish();
    }

    @Override
    public void onConnState(ConnState connState, String info) {
        connected = connState == ConnState.CONNECTED;
        if (connected) {
            // 不再广播：等 retained online 回放；万一没有回放，兜底再广播一次
            fallbackTried = false;
            handler.removeCallbacks(fallbackDiscovery);
            handler.postDelayed(fallbackDiscovery, FALLBACK_DISCOVERY_DELAY_MS);
        } else {
            handler.removeCallbacks(fallbackDiscovery);
        }
        publish();
    }

    @Override
    public void onDeliveryError(String kind, String error) {
        if (TopicNames.KIND_DISCOVERY.equals(kind)) {
            handler.removeCallbacks(finishScan);
            scanning = false;
            emit(toast, error);
            publish();
        }
    }

    @Override
    protected void onCleared() {
        handler.removeCallbacksAndMessages(null);
        super.onCleared();
    }

    // ---------------------------------------------------------------- 内部

    private void publish() {
        if (!onMainThread()) {          // WebRTC 回调可能在 signaling 线程
            runOnMain(this::publish);
            return;
        }
        List<DeviceInfo> visible = new ArrayList<>();
        for (DeviceInfo d : mgr.getDevices()) {
            if (d.online) visible.add(d);
        }
        // 选中设备下线后从列表消失 → 选中自动落到第一台在线设备；
        // 一台都不在线时保持原选中（不清空：selectDevice("") 会被注册表自动回填成
        // 第一台设备，导致「选中→下线→回填」反复通知的死循环）。
        String selected = mgr.getSelectedKey();
        if (!visible.isEmpty() && !containsKey(visible, selected)) {
            mgr.selectDevice(visible.get(0).key());
            selected = mgr.getSelectedKey();
        }
        state.setValue(new UiState(visible, selected, scanning, connected));
    }

    private static boolean containsKey(List<DeviceInfo> list, String key) {
        if (key == null || key.isEmpty()) return false;
        for (DeviceInfo d : list) {
            if (key.equals(d.key())) return true;
        }
        return false;
    }
}
