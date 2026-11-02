package www.walle.ai.ui;

import androidx.lifecycle.LiveData;

import org.json.JSONObject;

import java.util.List;

import www.walle.ai.R;

/** 「今日」：今天（App 本地日历日）收到的全部告警与事件，新的在前。 */
public class TodayAlarmFragment extends AlarmListFragment {

    @Override
    protected LiveData<List<JSONObject>> observeAlarms(AlarmViewModel vm) {
        return vm.today();
    }

    @Override
    protected int emptyTextRes() {
        return R.string.no_today_alarms;
    }
}
