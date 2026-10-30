package www.walle.ai.ui;

import android.app.Application;
import android.os.Handler;
import android.os.Looper;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.lifecycle.AndroidViewModel;
import androidx.lifecycle.LiveData;
import androidx.lifecycle.MutableLiveData;

import org.json.JSONObject;

import www.walle.ai.model.StatusSnapshot;
import www.walle.ai.net.ConnState;
import www.walle.ai.net.DeviceInfo;
import www.walle.ai.net.MqttManager;

/**
 * 状态页的 ViewModel：把 MQTT 回调与会话级逻辑从 Fragment 里搬出来。
 *
 * <p>单一数据源：Fragment 只观察一个不可变的 {@link UiState}，不再自己维护 waiting/rid/缓存，
 * 也不在回调里碰 View（旋转、切页导致的「View 已销毁」问题从根上消失）。
 *
 * <p>说明：本项目是纯 Java，因此用 {@link LiveData} 而不是 Kotlin 的 {@code StateFlow}；
 * 两者语义等价（生命周期感知 + 单数据源），等迁 Kotlin 时再换（见技术债 I1）。</p>
 */
public class StatusViewModel extends AndroidViewModel implements MqttManager.Listener {

    /** 状态页完整 UI 状态。 */
    public static final class UiState {
        public final DeviceInfo device;
        public final StatusSnapshot snapshot;
        public final boolean connected;
        public final boolean refreshing;
        public final boolean autoRefresh;

        UiState(@Nullable DeviceInfo device, @Nullable StatusSnapshot snapshot,
                boolean connected, boolean refreshing, boolean autoRefresh) {
            this.device = device;
            this.snapshot = snapshot;
            this.connected = connected;
            this.refreshing = refreshing;
            this.autoRefresh = autoRefresh;
        }

        public boolean canQuery() {
            return connected && device != null;
        }
    }

    private static final long QUERY_TIMEOUT_MS = 6000L;

    private final MqttManager mgr;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final MutableLiveData<UiState> state = new MutableLiveData<>();
    private final MutableLiveData<String> toast = new MutableLiveData<>();

    private boolean refreshing;
    private boolean autoRefresh;
    /** 本次查询的 request_id：只有匹配的结果才结束「查询中」，迟到结果照样渲染。 */
    private String waitingRid = "";

    private final Runnable queryTimeout = () -> {
        if (!refreshing) return;
        refreshing = false;
        waitingRid = "";
        publish();
    };

    private boolean onMainThread() {
        return Looper.myLooper() == Looper.getMainLooper();
    }

    /** 单次事件安全 setValue：主线程立即生效，其它线程切主线程（避免 set/post 交错）。 */
    private void emit(MutableLiveData<String> live, String value) {
        if (onMainThread()) live.setValue(value);
        else handler.post(() -> live.setValue(value));
    }

    private void runOnMain(Runnable action) {
        if (onMainThread()) action.run();
        else handler.post(action);
    }

    private final Runnable autoTask = new Runnable() {
        @Override
        public void run() {
            if (!autoRefresh) return;
            if (mgr.getState() == ConnState.CONNECTED) query();
            handler.postDelayed(this, Math.max(2, mgr.getConfig().statusIntervalSec) * 1000L);
        }
    };

    public StatusViewModel(@NonNull Application app) {
        super(app);
        mgr = MqttManager.get(app);
        mgr.addListener(this);
        // 进页面先渲染缓存，避免「白屏 → 等查询」
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

    public boolean isAutoRefresh() {
        return autoRefresh;
    }

    /** 手动/自动查询入口。 */
    public void query() {
        if (mgr.getState() != ConnState.CONNECTED) {
            return;
        }
        if (mgr.getSelectedKey().isEmpty()) {
            emit(toast, "请先在设备页选择一台设备");
            return;
        }
        waitingRid = mgr.requestStatus();
        refreshing = !waitingRid.isEmpty();
        handler.removeCallbacks(queryTimeout);
        if (refreshing) handler.postDelayed(queryTimeout, QUERY_TIMEOUT_MS);
        publish();
    }

    public void setAutoRefresh(boolean enabled) {
        if (autoRefresh == enabled) return;
        autoRefresh = enabled;
        handler.removeCallbacks(autoTask);
        if (enabled) handler.postDelayed(autoTask, Math.max(2, mgr.getConfig().statusIntervalSec) * 1000L);
        publish();
    }

    @Override
    protected void onCleared() {
        mgr.removeListener(this);
        handler.removeCallbacksAndMessages(null);
    }

    // ---------------------------------------------------------------- MqttManager.Listener

    @Override
    public void onMachineStatus(JSONObject payload) {
        String uuid = payload.optString("uuid", "");
        String selected = mgr.getSelectedKey();
        if (!uuid.isEmpty() && !selected.isEmpty() && !uuid.equals(selected)) {
            return;      // 别的设备的快照
        }
        String rid = payload.optString("request_id", "");
        if (waitingRid.isEmpty() || rid.isEmpty() || rid.equals(waitingRid)) {
            refreshing = false;
            waitingRid = "";
            handler.removeCallbacks(queryTimeout);
        }
        // 即使 rid 不匹配（迟到结果）也渲染：数据本身仍然有效
        cache = StatusSnapshot.parse(payload);
        publish();
    }

    @Override
    public void onConnState(ConnState connState, String info) {
        if (connState == ConnState.CONNECTED) autoQueryIfIdle();
        publish();
    }

    @Override
    public void onDevicesChanged() {
        // 切设备后缓存/快照都要重新取
        cache = null;
        autoQueryIfIdle();
        publish();
    }

    /** discovery 结果只更新设备标题（snapshot 不变）。 */
    @Override
    public void onDeviceInfo(DeviceInfo device) {
        publish();
    }

    // ---------------------------------------------------------------- 内部

    /** 最近一次渲染的快照（用于状态变化时重发同一个 UiState）。 */
    private StatusSnapshot cache;

    private void autoQueryIfIdle() {
        if (refreshing) return;
        if (mgr.getState() != ConnState.CONNECTED) return;
        if (mgr.getSelectedKey().isEmpty()) return;
        if (mgr.getStatus(mgr.getSelectedKey()) != null) return;
        query();
    }

    private void publish() {
        if (!onMainThread()) {          // WebRTC 回调可能在 signaling 线程
            runOnMain(this::publish);
            return;
        }
        StatusSnapshot snapshot = cache;
        if (snapshot == null) {
            JSONObject cached = mgr.getStatus(mgr.getSelectedKey());
            if (cached != null) snapshot = StatusSnapshot.parse(cached);
        }
        cache = snapshot;
        state.setValue(new UiState(mgr.getSelectedDevice(), snapshot,
                mgr.getState() == ConnState.CONNECTED, refreshing, autoRefresh));
    }
}
