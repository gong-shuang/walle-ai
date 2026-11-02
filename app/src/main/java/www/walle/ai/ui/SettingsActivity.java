package www.walle.ai.ui;

import android.graphics.PorterDuff;
import android.os.Bundle;
import android.text.TextUtils;
import android.view.View;
import android.widget.TextView;
import android.widget.Toast;

import androidx.appcompat.app.AppCompatActivity;
import androidx.lifecycle.ViewModelProvider;
import androidx.appcompat.widget.SwitchCompat;
import androidx.core.content.ContextCompat;

import www.walle.ai.R;
import www.walle.ai.net.MqttConfig;
import www.walle.ai.net.MqttConfigStore;
import www.walle.ai.net.ConnState;
import www.walle.ai.net.MqttManager;
import www.walle.ai.net.MqttService;
import com.google.android.material.button.MaterialButton;
import com.google.android.material.textfield.TextInputEditText;

/**
 * 服务器设置页：连接状态 + 连接/断开 + 服务器配置表单。
 * 从「设置」tab 的「服务器连接」入口进入。
 */
public class SettingsActivity extends AppCompatActivity {

    private TextInputEditText etHost, etPort, etUser, etPass, etClientId, etInterval;
    private SwitchCompat swReconnect, swAutoConnect, swTls;
    private View dotConn;
    private TextView tvConnState, tvConnDetail;
    private MaterialButton btnConnect;
    private MqttConfig editing;
    private MqttManager mgr;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        UiUtil.lockPortrait(this);
        setContentView(R.layout.activity_settings);
        // targetSdk 35+ 强制 edge-to-edge：内容铺满全屏，系统栏高度用 insets 加回 padding
        UiUtil.applyEdgeToEdge(this, findViewById(android.R.id.content));

        mgr = MqttManager.get(this);

        findViewById(R.id.btnBack).setOnClickListener(v -> finish());
        dotConn = findViewById(R.id.dotConn);
        tvConnState = findViewById(R.id.tvConnState);
        tvConnDetail = findViewById(R.id.tvConnDetail);
        btnConnect = findViewById(R.id.btnConnect);

        etHost = findViewById(R.id.etHost);
        etPort = findViewById(R.id.etPort);
        etUser = findViewById(R.id.etUser);
        etPass = findViewById(R.id.etPass);
        etClientId = findViewById(R.id.etClientId);
        etInterval = findViewById(R.id.etInterval);
        swReconnect = findViewById(R.id.swAutoReconnect);
        swAutoConnect = findViewById(R.id.swAutoConnect);
        swTls = findViewById(R.id.swTls);

        loadConfig();

        findViewById(R.id.btnSave).setOnClickListener(v -> save());

        btnConnect.setOnClickListener(v -> {
            if (mgr.isConnectedOrConnecting()) {
                MqttService.stop(this);
                return;
            }
            // 本地还没有保存过凭据时，先让用户填写并保存，避免用空账号去连 broker
            if (!mgr.hasCredentials()) {
                Toast.makeText(this, R.string.need_credentials_save_tip, Toast.LENGTH_SHORT).show();
                etUser.requestFocus();
                return;
            }
            MqttService.start(this);
        });

        ConnectionViewModel vm = new ViewModelProvider(this).get(ConnectionViewModel.class);
        vm.state().observe(this, this::renderConnection);
    }

    private void loadConfig() {
        editing = mgr.getConfig().copy();
        etHost.setText(editing.host);
        etPort.setText(String.valueOf(editing.port));
        etUser.setText(editing.username);
        etPass.setText(editing.password);
        etClientId.setText(editing.clientId);
        etInterval.setText(String.valueOf(editing.statusIntervalSec));
        swReconnect.setChecked(editing.autoReconnect);
        swAutoConnect.setChecked(editing.autoConnectOnLaunch);
        swTls.setChecked(editing.tls);
    }

    private void save() {
        String host = text(etHost);
        if (TextUtils.isEmpty(host)) {
            Toast.makeText(this, "请填写 Broker 地址", Toast.LENGTH_SHORT).show();
            return;
        }
        int port;
        try {
            port = Integer.parseInt(text(etPort));
            if (port <= 0 || port > 65535) throw new NumberFormatException();
        } catch (Exception e) {
            Toast.makeText(this, "端口需为 1-65535", Toast.LENGTH_SHORT).show();
            return;
        }
        int interval;
        try { interval = Math.max(0, Integer.parseInt(text(etInterval))); }
        catch (Exception e) { interval = 5; }

        // 用户名 / 密码没有代码默认值，必须由用户输入；用户名去首尾空格，密码原样保留
        String user = text(etUser);
        String pass = raw(etPass);
        if (TextUtils.isEmpty(user)) {
            Toast.makeText(this, R.string.credentials_required_user, Toast.LENGTH_SHORT).show();
            etUser.requestFocus();
            return;
        }
        if (TextUtils.isEmpty(pass)) {
            Toast.makeText(this, R.string.credentials_required_pass, Toast.LENGTH_SHORT).show();
            etPass.requestFocus();
            return;
        }

        editing.host = host;
        editing.port = port;
        editing.username = user;
        editing.password = pass;
        String cid = text(etClientId);
        // 不允许回退成固定 clientId（会与其他手机互踢），为空则重新生成唯一值
        editing.clientId = (TextUtils.isEmpty(cid) || "android-controller".equals(cid))
                ? MqttConfigStore.newClientId() : cid;
        etClientId.setText(editing.clientId);
        editing.statusIntervalSec = interval;
        editing.autoReconnect = swReconnect.isChecked();
        editing.autoConnectOnLaunch = swAutoConnect.isChecked();
        // 标准端口与协议一一对应，保存时按端口纠正开关，避免「端口/协议」配错：
        // 8883·8084·8884 必须 TLS（这是「改成 8883 还连不上」的典型原因），
        // 1883·8083 是明文端口，勾着 TLS 也连不上。非标准端口则尊重用户选择。
        boolean tls = swTls.isChecked();
        if (MqttConfig.isTlsPort(port) && !tls) {
            tls = true;
            swTls.setChecked(true);
            Toast.makeText(this, "端口 " + port + " 为 TLS 监听，已自动启用加密", Toast.LENGTH_SHORT).show();
        } else if (MqttConfig.isPlainPort(port) && tls) {
            tls = false;
            swTls.setChecked(false);
            Toast.makeText(this, "端口 " + port + " 为明文监听，已关闭 TLS", Toast.LENGTH_SHORT).show();
        }
        editing.tls = tls;

        mgr.updateConfig(editing);
        // 已连接时 updateConfig 内部会重连；未连接（首次填写凭据）时按按钮语义直接发起连接
        if (!mgr.isConnectedOrConnecting()) {
            MqttService.start(this);
        }
        Toast.makeText(this, "已保存并重连", Toast.LENGTH_SHORT).show();
    }

    private String text(TextInputEditText e) {
        return e.getText() == null ? "" : e.getText().toString().trim();
    }

    /** 密码不做 trim：MQTT 密码允许包含首尾空格。 */
    private String raw(TextInputEditText e) {
        return e.getText() == null ? "" : e.getText().toString();
    }

    private void renderConnection(ConnectionViewModel.UiState ui) {
        renderConn(ui.connState, ui.connInfo);
    }

    private void renderConn(ConnState state, String info) {
        int color;
        String label;
        switch (state) {
            case CONNECTED:
                color = R.color.state_connected;
                label = getString(R.string.state_connected);
                break;
            case CONNECTING:
                color = R.color.state_connecting;
                label = getString(R.string.state_connecting);
                break;
            case ERROR:
                color = R.color.state_error;
                label = getString(R.string.state_error);
                break;
            default:
                color = R.color.state_idle;
                label = getString(R.string.state_disconnected);
                break;
        }
        dotConn.getBackground().setColorFilter(
                ContextCompat.getColor(this, color), PorterDuff.Mode.SRC_IN);
        tvConnState.setText(label);
        tvConnDetail.setText(info == null || info.isEmpty() ? mgr.getConfig().brokerUri() : info);

        if (state == ConnState.CONNECTED) {
            btnConnect.setText(R.string.disconnect);
            btnConnect.setEnabled(true);
        } else if (state == ConnState.CONNECTING) {
            btnConnect.setText(R.string.connecting);
            btnConnect.setEnabled(true);
        } else {
            btnConnect.setText(R.string.connect);
            btnConnect.setEnabled(true);
        }
    }
}
