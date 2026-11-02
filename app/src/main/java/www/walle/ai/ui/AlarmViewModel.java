package www.walle.ai.ui;

import android.app.Application;

import androidx.annotation.NonNull;
import androidx.lifecycle.AndroidViewModel;
import androidx.lifecycle.LiveData;
import androidx.lifecycle.MutableLiveData;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

import www.walle.ai.net.AlarmStore;

/**
 * 告警页 ViewModel：把全局 AlarmStore 包装成两条可观察状态。
 *
 * <ul>
 *   <li>{@link #today()}：今天收到的全部告警/事件（「今日」页）；</li>
 *   <li>{@link #history()}：今天之前的记录（「历史」页，与「今日」不重复）。</li>
 * </ul>
 *
 * <p>页面不再自己注册 Observer；每次变化都重新取快照。</p>
 */
public class AlarmViewModel extends AndroidViewModel implements AlarmStore.Observer {

    private final MutableLiveData<List<JSONObject>> today = new MutableLiveData<>(new ArrayList<>());
    private final MutableLiveData<List<JSONObject>> history = new MutableLiveData<>(new ArrayList<>());

    public AlarmViewModel(@NonNull Application app) {
        super(app);
        AlarmStore.get().addObserver(this);
        reload();
    }

    public LiveData<List<JSONObject>> today() {
        return today;
    }

    public LiveData<List<JSONObject>> history() {
        return history;
    }

    /** 进入告警页：未读清零（设置 tab 的红点随之消失）。 */
    public void onScreenVisible() {
        AlarmStore.get().markAllRead();
    }

    @Override
    public void onAlarmsChanged() {
        reload();
    }

    @Override
    protected void onCleared() {
        AlarmStore.get().removeObserver(this);
    }

    /** 用 postValue：AlarmStore 可能被 MQTT 回调（主线程）之外的路径触发，且每次都重新取快照。 */
    private void reload() {
        today.postValue(new ArrayList<>(AlarmStore.get().todayAlarms()));
        history.postValue(new ArrayList<>(AlarmStore.get().historyAlarms()));
    }
}
