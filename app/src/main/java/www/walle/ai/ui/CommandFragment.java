package www.walle.ai.ui;

import android.os.Bundle;
import android.text.TextUtils;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.fragment.app.Fragment;
import androidx.lifecycle.ViewModelProvider;

import com.google.android.material.button.MaterialButton;
import com.google.android.material.chip.Chip;
import com.google.android.material.chip.ChipGroup;
import com.google.android.material.textfield.TextInputEditText;

import java.util.Arrays;
import java.util.List;

import www.walle.ai.R;
import www.walle.ai.net.DeviceInfo;

/**
 * 远程命令页：只渲染 {@link CommandViewModel.UiState}。
 *
 * <p>命令下发、request_id 配对、超时兜底、历史记录都在 ViewModel 里；
 * 页面不再持有 pendingRid/Handler。</p>
 */
public class CommandFragment extends Fragment {

    private static final List<String> QUICK = Arrays.asList(
            "uptime", "df -h", "free -m", "vcgencmd measure_temp",
            "ls -la", "ifconfig", "uname -a", "top -bn1 | head -15");

    private CommandViewModel viewModel;
    private TextInputEditText etCmd, etTimeout;
    private MaterialButton btnRun;
    private TextView tvResult, tvResultMeta, tvHistory, tvCurrentDevice;

    @Nullable
    @Override
    public View onCreateView(@NonNull LayoutInflater inflater, @Nullable ViewGroup container,
                             @Nullable Bundle savedInstanceState) {
        return inflater.inflate(R.layout.fragment_command, container, false);
    }

    @Override
    public void onViewCreated(@NonNull View view, @Nullable Bundle savedInstanceState) {
        viewModel = new ViewModelProvider(this).get(CommandViewModel.class);

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

        btnRun.setOnClickListener(v -> submit());
        viewModel.state().observe(getViewLifecycleOwner(), this::render);
        viewModel.toast().observe(getViewLifecycleOwner(), msg -> {
            if (msg != null && !msg.isEmpty()) {
                Toast.makeText(requireContext(), msg, Toast.LENGTH_SHORT).show();
                viewModel.clearToast();
            }
        });
    }

    private void submit() {
        String cmd = etCmd.getText() == null ? "" : etCmd.getText().toString().trim();
        if (TextUtils.isEmpty(cmd)) {
            Toast.makeText(requireContext(), "请输入命令", Toast.LENGTH_SHORT).show();
            return;
        }
        int timeout = 30;
        try {
            timeout = Integer.parseInt(etTimeout.getText().toString().trim());
        } catch (Exception ignore) {
        }
        timeout = Math.max(1, Math.min(timeout, 300));
        etTimeout.setText(String.valueOf(timeout));
        viewModel.run(cmd, timeout);
    }

    private void render(CommandViewModel.UiState ui) {
        renderDeviceLabel(ui.device);
        btnRun.setEnabled(!ui.running);
        btnRun.setText(ui.running ? R.string.running : R.string.run);
        tvResultMeta.setText(ui.meta);
        tvResult.setText(ui.result);
        tvHistory.setText(ui.history.isEmpty()
                ? getString(R.string.history) + "：—"
                : getString(R.string.history) + "：\n" + String.join("\n", ui.history));
    }

    private void renderDeviceLabel(DeviceInfo d) {
        if (tvCurrentDevice == null) return;
        if (d == null) {
            tvCurrentDevice.setText(R.string.no_device_selected);
        } else {
            tvCurrentDevice.setText(getString(R.string.current_device, d.displayName())
                    + "（uuid " + d.shortUuid() + "）");
        }
    }
}
