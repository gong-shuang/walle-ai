package www.walle.ai.ui;

import android.os.Bundle;

import androidx.appcompat.app.AppCompatActivity;

import www.walle.ai.R;
import www.walle.ai.net.AlarmStore;

/** 告警页：从设置页「告警」入口进入，展示实时告警 / 事件列表。 */
public class AlarmActivity extends AppCompatActivity {

    private static final String TAG_ALARM = "alarm_list";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        UiUtil.lockPortrait(this);
        setContentView(R.layout.activity_alarm);
        // targetSdk 35+ 强制 edge-to-edge：内容铺满全屏，系统栏高度用 insets 加回 padding
        UiUtil.applyEdgeToEdge(this, findViewById(android.R.id.content));

        findViewById(R.id.btnBack).setOnClickListener(v -> finish());
        findViewById(R.id.btnClearAlarm).setOnClickListener(v -> AlarmStore.get().clear());

        if (getSupportFragmentManager().findFragmentByTag(TAG_ALARM) == null) {
            getSupportFragmentManager().beginTransaction()
                    .replace(R.id.alarmContainer, new AlarmFragment(), TAG_ALARM)
                    .commit();
        }
    }
}
