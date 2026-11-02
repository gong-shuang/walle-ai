package www.walle.ai.ui;

import androidx.lifecycle.LiveData;

import org.json.JSONObject;

import java.util.List;

import www.walle.ai.R;

/** 「历史」：已恢复的告警 + USB 插拔等全部事件流水，按时间倒序。 */
public class AlarmHistoryFragment extends AlarmListFragment {

    @Override
    protected LiveData<List<JSONObject>> observeAlarms(AlarmViewModel vm) {
        return vm.history();
    }

    @Override
    protected int emptyTextRes() {
        return R.string.no_alarm_history;
    }
}
