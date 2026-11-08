package www.walle.ai.ui;

import android.content.Intent;
import android.graphics.PorterDuff;
import android.os.Bundle;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.TextView;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.core.content.ContextCompat;
import androidx.fragment.app.Fragment;
import androidx.lifecycle.ViewModelProvider;

import www.walle.ai.BuildConfig;
import www.walle.ai.R;
import www.walle.ai.net.AlarmStore;
import www.walle.ai.net.ConnState;
import www.walle.ai.net.MqttManager;

/**
 * 设置页：两个入口 —— 服务器连接（含当前状态）、告警（含未查看条数）。
 * 连接状态条不再常驻所有 tab，只在这里和服务器设置页里出现。
 */
public class SettingsFragment extends Fragment {

    private MqttManager mgr;
    private View dotServer;
    private TextView tvServerTitle, tvServerSub, tvAlarmBadge, tvAppVersion;

    @Nullable
    @Override
    public View onCreateView(@NonNull LayoutInflater inflater, @Nullable ViewGroup container,
                             @Nullable Bundle savedInstanceState) {
        return inflater.inflate(R.layout.fragment_settings, container, false);
    }

    @Override
    public void onViewCreated(@NonNull View view, @Nullable Bundle savedInstanceState) {
        mgr = MqttManager.get(requireContext());
        dotServer = view.findViewById(R.id.dotServer);
        tvServerTitle = view.findViewById(R.id.tvServerTitle);
        tvServerSub = view.findViewById(R.id.tvServerSub);
        tvAlarmBadge = view.findViewById(R.id.tvAlarmBadge);
        tvAppVersion = view.findViewById(R.id.tvAppVersion);

        tvAppVersion.setText("V" + BuildConfig.VERSION_NAME);

        view.findViewById(R.id.rowServer).setOnClickListener(v ->
                startActivity(new Intent(requireContext(), SettingsActivity.class)));
        view.findViewById(R.id.rowAlarm).setOnClickListener(v ->
                startActivity(new Intent(requireContext(), AlarmActivity.class)));
        view.findViewById(R.id.rowOnboarding).setOnClickListener(v -> {
            Intent intent = new Intent(requireContext(), OnboardingActivity.class);
            intent.putExtra(OnboardingActivity.EXTRA_REPLAY, true);
            startActivity(intent);
        });

        ConnectionViewModel vm = new ViewModelProvider(requireActivity()).get(ConnectionViewModel.class);
        vm.state().observe(getViewLifecycleOwner(), this::renderConnection);
    }

    // 连接状态与未读告警都来自 ConnectionViewModel（唯一监听 MQTT / AlarmStore 的地方）
    private void renderConnection(ConnectionViewModel.UiState ui) {
        renderServer(ui.connState, ui.connInfo);
        renderAlarm(ui.unreadAlarms);
    }

    private void renderServer(ConnState state, String info) {
        if (dotServer == null || tvServerTitle == null) return;
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
        dotServer.getBackground().setColorFilter(
                ContextCompat.getColor(requireContext(), color), PorterDuff.Mode.SRC_IN);
        tvServerTitle.setText(getString(R.string.server_row_fmt, label));
        if (state == ConnState.CONNECTED) {
            tvServerSub.setText(mgr.getConfig().brokerUri());
        } else if (info != null && !info.isEmpty()) {
            tvServerSub.setText(info);
        } else {
            tvServerSub.setText(mgr.getConfig().brokerUri());
        }
    }

    private void renderAlarm(int n) {
        if (tvAlarmBadge == null) return;
        tvAlarmBadge.setVisibility(n > 0 ? View.VISIBLE : View.GONE);
        tvAlarmBadge.setText(n > 99 ? "99+" : String.valueOf(n));
    }
}
