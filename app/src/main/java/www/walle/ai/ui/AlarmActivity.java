package www.walle.ai.ui;

import android.app.Dialog;
import android.os.Bundle;
import android.view.Gravity;
import android.view.Window;
import android.view.WindowManager;

import androidx.appcompat.app.AppCompatActivity;
import androidx.fragment.app.Fragment;
import androidx.fragment.app.FragmentManager;
import androidx.fragment.app.FragmentTransaction;
import androidx.lifecycle.ViewModelProvider;

import com.google.android.material.dialog.MaterialAlertDialogBuilder;
import com.google.android.material.tabs.TabLayout;

import org.json.JSONObject;

import java.util.List;

import www.walle.ai.R;
import www.walle.ai.net.AlarmStore;

/** 告警页：从设置页「告警」入口进入，分「今日」「历史」两个 Tab。 */
public class AlarmActivity extends AppCompatActivity {

    private static final String TAG_TODAY = "alarm_today";
    private static final String TAG_HISTORY = "alarm_history";

    private TabLayout tabs;
    private AlarmViewModel viewModel;
    private Fragment todayFragment;
    private Fragment historyFragment;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        UiUtil.lockPortrait(this);
        setContentView(R.layout.activity_alarm);
        // targetSdk 35+ 强制 edge-to-edge：内容铺满全屏，系统栏高度用 insets 加回 padding
        UiUtil.applyEdgeToEdge(this, findViewById(android.R.id.content));

        findViewById(R.id.btnBack).setOnClickListener(v -> finish());
        findViewById(R.id.btnClearAlarm).setOnClickListener(v -> confirmClear());

        tabs = findViewById(R.id.tabAlarm);
        tabs.addTab(tabs.newTab().setText(R.string.alarm_tab_today));
        tabs.addTab(tabs.newTab().setText(R.string.alarm_tab_history));

        FragmentManager fm = getSupportFragmentManager();
        todayFragment = fm.findFragmentByTag(TAG_TODAY);
        historyFragment = fm.findFragmentByTag(TAG_HISTORY);
        if (todayFragment == null) {
            // 首次进入：两个页都 add，历史页隐藏，切 Tab 只做 show/hide 以保留滚动位置
            todayFragment = new TodayAlarmFragment();
            historyFragment = new AlarmHistoryFragment();
            fm.beginTransaction()
                    .add(R.id.alarmContainer, todayFragment, TAG_TODAY)
                    .add(R.id.alarmContainer, historyFragment, TAG_HISTORY)
                    .hide(historyFragment)
                    .commit();
        }
        tabs.addOnTabSelectedListener(new TabLayout.OnTabSelectedListener() {
            @Override public void onTabSelected(TabLayout.Tab tab) { showTab(tab.getPosition()); }
            @Override public void onTabUnselected(TabLayout.Tab tab) { }
            @Override public void onTabReselected(TabLayout.Tab tab) { }
        });

        viewModel = new ViewModelProvider(this).get(AlarmViewModel.class);
        viewModel.today().observe(this, this::renderTabTitles);
    }

    @Override
    protected void onStart() {
        super.onStart();
        // 进入告警页即把未读清零（设置 tab 的红点随之消失）
        viewModel.onScreenVisible();
    }

    /** 清空会同时删掉「今日」与「历史」两个页的数据，必须先让用户确认。 */
    private void confirmClear() {
        Dialog dialog = new MaterialAlertDialogBuilder(this)
                .setTitle(R.string.clear_alarms_confirm_title)
                .setMessage(R.string.clear_alarms_confirm_message)
                .setNegativeButton(R.string.cancel, null)
                .setPositiveButton(R.string.clear_alarms, (d, w) -> AlarmStore.get().clear())
                .create();
        dialog.show();
        // 高度随内容自适应（WRAP_CONTENT），并在屏幕水平/垂直居中显示
        Window window = dialog.getWindow();
        if (window != null) {
            window.setGravity(Gravity.CENTER);
            WindowManager.LayoutParams attrs = window.getAttributes();
            attrs.width = WindowManager.LayoutParams.WRAP_CONTENT;
            attrs.height = WindowManager.LayoutParams.WRAP_CONTENT;
            window.setAttributes(attrs);
        }
    }

    private void showTab(int position) {
        Fragment target = position == 1 ? historyFragment : todayFragment;
        Fragment other = position == 1 ? todayFragment : historyFragment;
        if (target == null || isFinishing()) return;
        FragmentTransaction ft = getSupportFragmentManager().beginTransaction();
        if (other != null && !other.isHidden()) ft.hide(other);
        if (target.isHidden()) ft.show(target);
        ft.commitAllowingStateLoss();
    }

    /** Tab 标题带上今日条数，一眼看出今天有没有新告警。 */
    private void renderTabTitles(List<JSONObject> today) {
        TabLayout.Tab tab = tabs.getTabAt(0);
        if (tab == null) return;
        int count = today == null ? 0 : today.size();
        tab.setText(count == 0 ? getString(R.string.alarm_tab_today)
                : getString(R.string.alarm_tab_today_count, count));
    }
}
