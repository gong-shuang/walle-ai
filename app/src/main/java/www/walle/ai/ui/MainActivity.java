package www.walle.ai.ui;

import android.Manifest;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Bundle;
import android.widget.Toast;

import androidx.appcompat.app.AppCompatActivity;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;
import androidx.fragment.app.Fragment;

import www.walle.ai.R;
import www.walle.ai.net.AlarmStore;
import www.walle.ai.net.ConnState;
import www.walle.ai.net.MqttManager;
import www.walle.ai.net.MqttService;
import com.google.android.material.badge.BadgeDrawable;
import com.google.android.material.bottomnavigation.BottomNavigationView;

/**
 * 主界面：全屏无标题栏，底部三个 tab —— 设备列表 / 设备详情 / 设置。
 * 连接状态不再常驻顶部，统一收进「设置」页；
 * 服务器断开、或存在未查看告警时，设置 tab 显示红点。
 */
public class MainActivity extends AppCompatActivity implements MqttManager.Listener {

    private static final int REQ_NOTIF = 1001;

    private MqttManager mgr;
    private BottomNavigationView nav;

    private final DevicesFragment devicesFragment = new DevicesFragment();
    private final DeviceDetailFragment detailFragment = new DeviceDetailFragment();
    private final SettingsFragment settingsFragment = new SettingsFragment();
    private Fragment active = devicesFragment;

    /** 告警收件箱变化时刷新 tab 红点（未读告警也让设置 tab 亮红点）。 */
    private final AlarmStore.Observer alarmObserver = this::refreshSettingsBadge;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // 首次启动先展示引导页，看完后再进入主界面
        if (!OnboardingPrefs.isDone(this)) {
            startActivity(new Intent(this, OnboardingActivity.class));
            finish();
            return;
        }

        UiUtil.lockPortrait(this);
        setContentView(R.layout.activity_main);
        // targetSdk 35+ 强制 edge-to-edge：内容铺满全屏，系统栏高度用 insets 加回 padding
        UiUtil.applyEdgeToEdge(this, findViewById(android.R.id.content));

        mgr = MqttManager.get(this);
        nav = findViewById(R.id.bottomNav);

        getSupportFragmentManager().beginTransaction()
                .add(R.id.fragmentContainer, settingsFragment, "settings").hide(settingsFragment)
                .add(R.id.fragmentContainer, detailFragment, "detail").hide(detailFragment)
                .add(R.id.fragmentContainer, devicesFragment, "devices")
                .commit();

        devicesFragment.setOnDevicePicked(this::onDevicePicked);

        nav.inflateMenu(R.menu.bottom_nav);
        nav.setOnItemSelectedListener(item -> {
            int id = item.getItemId();
            if (id == R.id.nav_devices) switchTo(devicesFragment);
            else if (id == R.id.nav_detail) switchTo(detailFragment);
            else if (id == R.id.nav_settings) switchTo(settingsFragment);
            return true;
        });
        // 支持通过 intent 指定初始 tab（便于自动化验证 / 快捷入口）
        int startTab = getIntent().getIntExtra("tab", -1);
        if (startTab == 1) nav.setSelectedItemId(R.id.nav_detail);
        else if (startTab == 2) nav.setSelectedItemId(R.id.nav_settings);
        else nav.setSelectedItemId(R.id.nav_devices);

        refreshSettingsBadge();

        ensureNotificationPermission();
        // 进入自动连接：本地已保存用户名/密码才连；
        // 首次使用（未填凭据）不发起连接，直接引导到「设置 → 服务器连接」填写
        if (mgr.getConfig().autoConnectOnLaunch && !mgr.isConnectedOrConnecting()) {
            if (mgr.hasCredentials()) {
                MqttService.start(this);
            } else {
                nav.setSelectedItemId(R.id.nav_settings);
                Toast.makeText(this, R.string.need_credentials_tip, Toast.LENGTH_LONG).show();
            }
        }
    }

    private void switchTo(Fragment target) {
        if (target == active) return;
        getSupportFragmentManager().beginTransaction()
                .hide(active).show(target).commit();
        active = target;
    }

    /** 设备卡片被点击：切到「设备详情」并回到状态子页 */
    private void onDevicePicked(String key) {
        nav.setSelectedItemId(R.id.nav_detail);
        switchTo(detailFragment);
        detailFragment.onDeviceSwitched();
    }

    private void ensureNotificationPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                    != PackageManager.PERMISSION_GRANTED) {
                ActivityCompat.requestPermissions(this,
                        new String[]{Manifest.permission.POST_NOTIFICATIONS}, REQ_NOTIF);
            }
        }
    }

    @Override
    protected void onStart() {
        super.onStart();
        mgr.addListener(this);
        AlarmStore.get().addObserver(alarmObserver);
        refreshSettingsBadge();
    }

    @Override
    protected void onStop() {
        super.onStop();
        mgr.removeListener(this);
        AlarmStore.get().removeObserver(alarmObserver);
    }

    // ---------------- MqttManager.Listener ----------------
    @Override public void onConnState(ConnState state, String info) {
        runOnUiThread(this::refreshSettingsBadge);
    }

    /**
     * 「设置」tab 的红点：服务器断开 / 连接失败，或存在未查看告警时显示。
     */
    private void refreshSettingsBadge() {
        if (nav == null) return;
        BadgeDrawable badge = nav.getOrCreateBadge(R.id.nav_settings);
        badge.setBackgroundColor(ContextCompat.getColor(this, R.color.state_error));
        ConnState state = mgr == null ? null : mgr.getState();
        boolean offline = state == ConnState.DISCONNECTED
                || state == ConnState.ERROR;
        boolean hasUnreadAlarm = AlarmStore.get().unread() > 0;
        badge.setVisible(offline || hasUnreadAlarm);
    }
}
