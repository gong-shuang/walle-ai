package www.walle.ai.ui;

import android.graphics.Typeface;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.TextUtils;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.fragment.app.Fragment;

import www.walle.ai.R;
import www.walle.ai.net.DeviceInfo;
import www.walle.ai.net.ConnState;
import www.walle.ai.net.MqttManager;
import com.google.android.material.button.MaterialButton;
import com.google.android.material.chip.Chip;
import com.google.android.material.chip.ChipGroup;
import com.google.android.material.textfield.TextInputEditText;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

public class CommandFragment extends Fragment implements MqttManager.Listener {

    private static final List<String> QUICK = Arrays.asList(
            "uptime", "df -h", "free -m", "vcgencmd measure_temp",
            "ls -la", "ifconfig", "uname -a", "top -bn1 | head -15");

    private MqttManager mgr;
    private TextInputEditText etCmd, etTimeout;
    private MaterialButton btnRun;
    private TextView tvResult, tvResultMeta, tvHistory, tvCurrentDevice;
    private final Handler handler = new Handler(Looper.getMainLooper());

    private String pendingRid;
    private String pendingCmd;
    private final Set<String> history = new LinkedHashSet<>();

    @Nullable
    @Override
    public View onCreateView(@NonNull LayoutInflater inflater, @Nullable ViewGroup container,
                             @Nullable Bundle savedInstanceState) {
        return inflater.inflate(R.layout.fragment_command, container, false);
    }

    @Override
    public void onViewCreated(@NonNull View view, @Nullable Bundle savedInstanceState) {
        mgr = MqttManager.get(requireContext());
        etCmd = view.findViewById(R.id.etCmd);
        etTimeout = view.findViewById(R.id.etTimeout);
        btnRun = view.findViewById(R.id.btnRun);
        tvResult = view.findViewById(R.id.tvResult);
        tvResultMeta = view.findViewById(R.id.tvResultMeta);
        tvHistory = view.findViewById(R.id.tvHistory);
        tvCurrentDevice = view.findViewById(R.id.tvCurrentDevice);
        ChipGroup chips = view.findViewById(R.id.chipGroup);

        for (String c : QUICK) {
            Chip chip = new Chip(requireContext());
            chip.setText(c);
            chip.setOnClickListener(v -> etCmd.setText(c));
            chips.addView(chip);
        }

        btnRun.setOnClickListener(v -> run());
        renderHistory();
        updateDeviceLabel();
    }

    private void updateDeviceLabel() {
        if (tvCurrentDevice == null) return;
        DeviceInfo d = mgr.getSelectedDevice();
        if (d == null) {
            tvCurrentDevice.setText(R.string.no_device_selected);
        } else {
            tvCurrentDevice.setText(getString(R.string.current_device, d.displayName())
                    + "（uuid " + d.shortUuid() + "）");
        }
    }

    private void run() {
        String cmd = etCmd.getText() == null ? "" : etCmd.getText().toString().trim();
        if (TextUtils.isEmpty(cmd)) {
            Toast.makeText(requireContext(), "请输入命令", Toast.LENGTH_SHORT).show();
            return;
        }
        if (mgr.getState() != ConnState.CONNECTED) {
            Toast.makeText(requireContext(), "MQTT 未连接", Toast.LENGTH_SHORT).show();
            return;
        }
        if (mgr.getSelectedKey().isEmpty()) {
            Toast.makeText(requireContext(), R.string.no_device_selected, Toast.LENGTH_SHORT).show();
            return;
        }
        int timeout = 30;
        try {
            timeout = Integer.parseInt(etTimeout.getText().toString().trim());
        } catch (Exception ignore) { }
        timeout = Math.max(1, Math.min(timeout, 300));
        etTimeout.setText(String.valueOf(timeout));

        pendingRid = mgr.execCommand(cmd, timeout);
        pendingCmd = cmd;
        history.add(cmd);
        if (history.size() > 20) {
            List<String> tmp = new ArrayList<>(history);
            history.clear();
            history.addAll(tmp.subList(tmp.size() - 20, tmp.size()));
        }
        renderHistory();

        btnRun.setEnabled(false);
        btnRun.setText(R.string.running);
        tvResultMeta.setText("request_id=" + pendingRid + "，已下发，等待结果…");
        tvResult.setText("");
        // 设备执行最长 timeout，再加网络余量
        long waitMs = (timeout + 8) * 1000L;
        handler.removeCallbacks(waitTimeout);
        handler.postDelayed(waitTimeout, waitMs);
    }

    private final Runnable waitTimeout = new Runnable() {
        @Override public void run() {
            if (pendingRid != null) {
                pendingRid = null;
                btnRun.setEnabled(true);
                btnRun.setText(R.string.run);
                tvResultMeta.setText("等待结果超时（设备可能离线或命令耗时过长）");
            }
        }
    };

    private void renderHistory() {
        if (tvHistory == null) return;
        if (history.isEmpty()) {
            tvHistory.setVisibility(View.GONE);
            return;
        }
        tvHistory.setVisibility(View.VISIBLE);
            StringBuilder sb = new StringBuilder("本次历史（点击重填）：\n");
        int i = 1;
        List<String> list = new ArrayList<>(history);
        for (int k = list.size() - 1; k >= 0; k--) {
            sb.append(i++).append(". ").append(list.get(k)).append('\n');
        }
        tvHistory.setText(sb.toString().trim());
        tvHistory.setOnClickListener(v -> {
            // 简单处理：点击填入最近一条
            if (!list.isEmpty()) etCmd.setText(list.get(list.size() - 1));
        });
    }

    @Override
    public void onStart() {
        super.onStart();
        mgr.addListener(this);
        updateDeviceLabel();
    }

    @Override
    public void onDevicesChanged() {
        if (getActivity() == null) return;
        requireActivity().runOnUiThread(this::updateDeviceLabel);
    }

    @Override
    public void onStop() {
        super.onStop();
        mgr.removeListener(this);
        handler.removeCallbacks(waitTimeout);
    }

    @Override
    public void onCmdResult(JSONObject r) {
        if (getActivity() == null) return;
        requireActivity().runOnUiThread(() -> {
            String rid = r.optString("request_id", "");
            // 多设备场景：非当前设备的回执直接忽略
            String uuid = r.optString("uuid", "");
            String sel = mgr.getSelectedKey();
            if (!uuid.isEmpty() && !sel.isEmpty() && !uuid.equals(sel)) return;
            // 仅展示自己请求的结果；若设备未回 rid（异常分支），也展示最新一条
            if (pendingRid != null && !pendingRid.isEmpty() && !rid.isEmpty()
                    && !rid.equals(pendingRid)) {
                return;
            }
            handler.removeCallbacks(waitTimeout);
            pendingRid = null;
            btnRun.setEnabled(true);
            btnRun.setText(R.string.run);

            String status = r.optString("status", "error");
            int color = UiUtil.statusColor(status);
            String meta = String.format(Locale.US, "%s    退出码=%s    耗时=%sms    %s",
                    UiUtil.statusLabel(status),
                    r.isNull("exit_code") ? "—" : r.optString("exit_code"),
                    r.optInt("duration_ms", 0),
                    r.optString("datetime", ""));
            tvResultMeta.setTextColor(color);
            tvResultMeta.setTypeface(Typeface.DEFAULT_BOLD);
            tvResultMeta.setText(meta);

            StringBuilder out = new StringBuilder();
            String stdout = r.optString("stdout", "");
            String stderr = r.optString("stderr", "");
            if (!stdout.isEmpty()) out.append("—— stdout ——\n").append(stdout);
            if (!stderr.isEmpty()) {
                if (out.length() > 0) out.append("\n\n");
                out.append("—— stderr ——\n").append(stderr);
            }
            if (out.length() == 0) out.append("（无输出）");
            tvResult.setText(out.toString());
        });
    }
}
