package www.walle.ai.ui;

import android.app.Application;
import android.os.Handler;
import android.os.Looper;

import androidx.annotation.NonNull;
import androidx.lifecycle.LiveData;
import androidx.lifecycle.MutableLiveData;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

import www.walle.ai.net.ConnState;
import www.walle.ai.net.DeviceInfo;

/**
 * 命令页 ViewModel：命令下发、request_id 配对、超时兜底与历史记录。
 *
 * <p>改造前这些都在 Fragment 里（pendingRid/pendingCmd/history/Handler），
 * 现在只暴露一个 UiState，Fragment 只管渲染。</p>
 */
public class CommandViewModel extends MqttViewModel {

    /** 本次会话的历史最多保留多少条 */
    private static final int MAX_HISTORY = 20;

    public static final class UiState {
        public final DeviceInfo device;
        public final boolean connected;
        public final boolean running;
        public final String meta;
        public final String result;
        public final List<String> history;

        UiState(DeviceInfo device, boolean connected, boolean running,
                String meta, String result, List<String> history) {
            this.device = device;
            this.connected = connected;
            this.running = running;
            this.meta = meta;
            this.result = result;
            this.history = history;
        }
    }

    private final Handler handler = new Handler(Looper.getMainLooper());
    private final MutableLiveData<UiState> state = new MutableLiveData<>();
    private final MutableLiveData<String> toast = new MutableLiveData<>();

    private final Set<String> history = new LinkedHashSet<>();
    private String pendingRid;
    private String meta = "";
    private String result = "";
    private boolean running;
    private boolean connected;

    public CommandViewModel(@NonNull Application app) {
        super(app);
        connected = mgr.isConnected();
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

    /** 下发命令；参数已由页面做过空值/范围收敛。 */
    public void run(String cmd, int timeoutSec) {
        if (cmd == null || cmd.trim().isEmpty()) {
            emit(toast, "请输入命令");
            return;
        }
        if (mgr.getState() != ConnState.CONNECTED) {
            emit(toast, "MQTT 未连接");
            return;
        }
        if (mgr.getSelectedKey().isEmpty()) {
            emit(toast, "请先在设备页选择一台设备");
            return;
        }
        final int timeout = Math.max(1, Math.min(timeoutSec, 300));
        pendingRid = mgr.execCommand(cmd.trim(), timeout);
        running = true;
        meta = "request_id=" + pendingRid + "，已下发，等待结果…";
        result = "";
        history.add(cmd.trim());
        while (history.size() > MAX_HISTORY) history.remove(history.iterator().next());

        // 设备执行最长 timeout，再加网络余量
        handler.removeCallbacks(waitTimeout);
        handler.postDelayed(waitTimeout, (timeout + 8) * 1000L);
        publish();
    }

    private final Runnable waitTimeout = () -> {
        if (pendingRid == null) return;
        pendingRid = null;
        running = false;
        meta = "等待结果超时（设备未回包）";
        publish();
    };

    @Override
    public void onCmdResult(JSONObject payload) {
        String rid = payload.optString("request_id", "");
        if (pendingRid == null || (!rid.isEmpty() && !rid.equals(pendingRid))) {
            return;      // 迟到的旧结果 / 不是本页发的
        }
        handler.removeCallbacks(waitTimeout);
        pendingRid = null;
        running = false;

        String status = payload.optString("status", "");
        String statusText = UiUtil.statusLabel(status);
        String cmd = payload.optString("cmd", "");
        meta = String.format(Locale.US, "%s · exit=%s · %dms",
                statusText, payload.optString("exit_code", "—"), payload.optLong("duration_ms", 0));
        if (!cmd.isEmpty()) meta = cmd + "\n" + meta;

        String out = payload.optString("stdout", "");
        String err = payload.optString("stderr", "");
        StringBuilder body = new StringBuilder();
        if (!out.isEmpty()) body.append(out);
        if (!err.isEmpty()) {
            if (body.length() > 0) body.append("\n");
            body.append("[stderr]\n").append(err);
        }
        result = body.length() == 0 ? "（无输出）" : body.toString();
        publish();
    }

    @Override
    public void onConnState(ConnState connState, String info) {
        connected = connState == ConnState.CONNECTED;
        if (!connected && running) {
            handler.removeCallbacks(waitTimeout);
            pendingRid = null;
            running = false;
            meta = "连接已断开，命令结果未知";
        }
        publish();
    }

    @Override
    public void onDevicesChanged() {
        publish();
    }

    @Override
    public void onDeliveryError(String kind, String error) {
        if (!www.walle.ai.net.TopicNames.KIND_CMD.equals(kind)) return;
        if (!running) return;
        handler.removeCallbacks(waitTimeout);
        pendingRid = null;
        running = false;
        meta = "下发失败：" + error;
        publish();
    }

    @Override
    protected void onCleared() {
        handler.removeCallbacksAndMessages(null);
        super.onCleared();
    }

    private void publish() {
        if (!onMainThread()) {          // WebRTC 回调可能在 signaling 线程
            runOnMain(this::publish);
            return;
        }
        state.setValue(new UiState(mgr.getSelectedDevice(), connected, running,
                meta, result, new ArrayList<>(history)));
    }
}
