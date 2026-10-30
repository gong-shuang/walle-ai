package www.walle.ai.ui;

import android.app.Application;

import androidx.annotation.NonNull;
import androidx.lifecycle.LiveData;
import androidx.lifecycle.MutableLiveData;

import org.json.JSONObject;

import www.walle.ai.net.AlarmStore;
import www.walle.ai.net.ConnState;
import www.walle.ai.net.DeviceInfo;

/**
 * 连接与设备摘要的 ViewModel：给「主界面红点 / 设置页连接状态 / 设备详情标题」共用。
 *
 * <p>改造前这几处各自实现 {@code MqttManager.Listener} + {@code AlarmStore.Observer}，
 * 到处写 {@code runOnUiThread}；现在统一观察一个 {@link UiState}。</p>
 */
public class ConnectionViewModel extends MqttViewModel implements AlarmStore.Observer {

    public static final class UiState {
        public final ConnState connState;
        public final String connInfo;
        public final int deviceCount;
        public final int onlineCount;
        public final DeviceInfo selected;
        public final int unreadAlarms;

        UiState(ConnState connState, String connInfo, int deviceCount, int onlineCount,
                DeviceInfo selected, int unreadAlarms) {
            this.connState = connState;
            this.connInfo = connInfo;
            this.deviceCount = deviceCount;
            this.onlineCount = onlineCount;
            this.selected = selected;
            this.unreadAlarms = unreadAlarms;
        }

        public boolean offline() {
            return connState == ConnState.DISCONNECTED || connState == ConnState.ERROR;
        }
    }

    private final MutableLiveData<UiState> state = new MutableLiveData<>();

    public ConnectionViewModel(@NonNull Application app) {
        super(app);
        AlarmStore.get().addObserver(this);
        publish();
    }

    public LiveData<UiState> state() {
        return state;
    }

    @Override
    public void onConnState(ConnState connState, String info) {
        publish();
    }

    @Override
    public void onDevicesChanged() {
        publish();
    }

    @Override
    public void onDeviceInfo(DeviceInfo device) {
        publish();
    }

    @Override
    public void onOnline(JSONObject payload) {
        publish();
    }

    @Override
    public void onAlarmsChanged() {
        publish();
    }

    @Override
    protected void onCleared() {
        AlarmStore.get().removeObserver(this);
        super.onCleared();
    }

    private void publish() {
        if (!onMainThread()) {          // WebRTC 回调可能在 signaling 线程
            runOnMain(this::publish);
            return;
        }
        state.setValue(new UiState(mgr.getState(), mgr.getStateInfo(),
                mgr.deviceCount(), mgr.onlineCount(), mgr.getSelectedDevice(),
                AlarmStore.get().unread()));
    }
}
