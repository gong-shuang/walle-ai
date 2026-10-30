package www.walle.ai.ui;

import android.graphics.Typeface;
import android.os.Bundle;
import android.view.Gravity;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.appcompat.widget.SwitchCompat;
import androidx.core.content.ContextCompat;
import androidx.fragment.app.Fragment;
import androidx.lifecycle.ViewModelProvider;
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout;

import com.google.android.material.button.MaterialButton;

import java.util.List;
import java.util.Locale;

import www.walle.ai.R;
import www.walle.ai.model.StatusSnapshot;
import www.walle.ai.net.DeviceInfo;

/**
 * 设备状态页：只负责渲染 {@link StatusViewModel.UiState}，不再自己解析 JSON / 维护请求状态。
 *
 * <p>数据流：MQTT 回调 → ViewModel（解析成 {@link StatusSnapshot}）→ LiveData → 本页渲染。
 * Activity/Fragment 重建时状态由 ViewModel 保留，不会出现「旋转后重新查一遍」或
 * 「回调里碰已销毁的 View」。</p>
 */
public class StatusFragment extends Fragment {

    private StatusViewModel viewModel;
    private LinearLayout container;
    private TextView tvEmpty, tvUpdated, tvCurrentDevice;
    private MaterialButton btnQuery;
    private SwitchCompat swAuto;
    private SwipeRefreshLayout swipe;
    /** 避免 setChecked 触发监听器造成回环。 */
    private boolean bindingSwitch;

    @Nullable
    @Override
    public View onCreateView(@NonNull LayoutInflater inflater, @Nullable ViewGroup container,
                             @Nullable Bundle savedInstanceState) {
        return inflater.inflate(R.layout.fragment_status, container, false);
    }

    @Override
    public void onViewCreated(@NonNull View view, @Nullable Bundle savedInstanceState) {
        viewModel = new ViewModelProvider(this).get(StatusViewModel.class);

        container = view.findViewById(R.id.statusContainer);
        tvEmpty = view.findViewById(R.id.tvEmpty);
        tvUpdated = view.findViewById(R.id.tvUpdated);
        tvCurrentDevice = view.findViewById(R.id.tvCurrentDevice);
        btnQuery = view.findViewById(R.id.btnQuery);
        swAuto = view.findViewById(R.id.swAuto);
        swipe = view.findViewById(R.id.swipe);

        btnQuery.setOnClickListener(v -> viewModel.query());
        swipe.setOnRefreshListener(() -> {
            viewModel.query();
            swipe.setRefreshing(false);   // 刷新指示由 UiState.refreshing 驱动
        });
        swAuto.setOnCheckedChangeListener((buttonView, isChecked) -> {
            if (bindingSwitch) return;
            viewModel.setAutoRefresh(isChecked);
        });

        viewModel.state().observe(getViewLifecycleOwner(), this::render);
        viewModel.toast().observe(getViewLifecycleOwner(), msg -> {
            if (msg != null && !msg.isEmpty()) {
                Toast.makeText(requireContext(), msg, Toast.LENGTH_SHORT).show();
                viewModel.clearToast();
            }
        });
    }

    /** 设备页切换了当前设备：Dialog 已由设备页负责，这里让 ViewModel 重新取一次。 */
    public void onDeviceSwitched() {
        if (!isAdded() || viewModel == null) return;
        viewModel.query();
    }

    // ---------------------------------------------------------------- 渲染

    private void render(StatusViewModel.UiState ui) {
        renderDeviceLabel(ui.device);
        renderToolbar(ui);

        StatusSnapshot s = ui.snapshot;
        if (s == null) {
            container.removeAllViews();
            tvEmpty.setVisibility(View.VISIBLE);
            tvUpdated.setText("");
            return;
        }
        tvEmpty.setVisibility(View.GONE);
        tvUpdated.setText("最近更新：" + s.datetime);
        renderSnapshot(s);
    }

    private void renderDeviceLabel(DeviceInfo d) {
        if (tvCurrentDevice == null) return;
        if (d == null) {
            tvCurrentDevice.setText(R.string.no_device_selected);
        } else {
            tvCurrentDevice.setText(getString(R.string.current_device, d.displayName())
                    + "（" + d.typeLabel() + " · uuid " + d.shortUuid() + "）");
        }
    }

    private void renderToolbar(StatusViewModel.UiState ui) {
        if (btnQuery != null) {
            btnQuery.setEnabled(ui.canQuery());
            btnQuery.setText(ui.refreshing ? R.string.refreshing : R.string.query_status);
        }
        if (swAuto != null) {
            bindingSwitch = true;
            swAuto.setChecked(ui.autoRefresh);
            bindingSwitch = false;
        }
    }

    private void renderSnapshot(StatusSnapshot s) {
        container.removeAllViews();

        // ---------- 系统 ----------
        StringBuilder sb = new StringBuilder();
        if (s.system != null && !s.system.model.isEmpty()) {
            sb.append("型号：").append(s.system.model).append('\n');
        }
        sb.append("主机名：").append(orDash(s.hostname)).append('\n');
        sb.append("IP：").append(orDash(s.ip)).append('\n');
        if (s.system != null) {
            sb.append("系统：").append(orDash(s.system.os))
                    .append("  ").append(s.system.machine).append('\n');
            sb.append("内核：").append(orDash(s.system.kernel)).append('\n');
        }
        sb.append("已运行：").append(UiUtil.uptime(s.uptimeSeconds));
        addCard("🖥 系统", sb.toString());

        // ---------- CPU ----------
        if (s.cpu != null) {
            LinearLayout card = newCardContainer("🧠 CPU");
            addBigMetric(card, "使用率", UiUtil.pct(s.cpu.percent), UiUtil.usageColor(s.cpu.percent));
            addRow(card, "温度", temp(s.cpu.tempC));
            addRow(card, "负载 1/5/15m",
                    fmt(s.cpu.load1) + " / " + fmt(s.cpu.load5) + " / " + fmt(s.cpu.load15));
            if (!s.cpu.freqCurrent.isEmpty() || !s.cpu.freqMin.isEmpty()) {
                addRow(card, "主频", fmt(s.cpu.freqCurrent) + " / "
                        + fmt(s.cpu.freqMin) + " ~ " + fmt(s.cpu.freqMax) + " MHz");
            }
            addRow(card, "核心数", "逻辑 " + s.cpu.coresLogical + "，物理 " + s.cpu.coresPhysical);
            if (s.cpu.perCore.length > 0) {
                StringBuilder cs = new StringBuilder();
                for (int i = 0; i < s.cpu.perCore.length; i++) {
                    cs.append(String.format(Locale.US, "核%d:%.0f%%  ", i, s.cpu.perCore[i]));
                }
                addRow(card, "各核", cs.toString().trim());
            }
            container.addView(card);
        }

        // ---------- 内存 ----------
        if (s.memory != null) {
            LinearLayout card = newCardContainer("💾 内存");
            addBigMetric(card, "使用率", UiUtil.pct(s.memory.percent), UiUtil.usageColor(s.memory.percent));
            addRow(card, "容量", orDash(s.memory.usedHuman) + " / " + orDash(s.memory.totalHuman));
            addRow(card, "可用", orDash(s.memory.availableHuman));
            if (!s.memory.swapTotalHuman.isEmpty()) {
                addRow(card, "Swap", orDash(s.memory.swapUsedHuman) + " / "
                        + s.memory.swapTotalHuman + "（" + UiUtil.pct(s.memory.swapPercent) + "）");
            }
            container.addView(card);
        }

        // ---------- 磁盘 ----------
        if (!s.disks.isEmpty()) {
            LinearLayout card = newCardContainer("💿 磁盘");
            for (StatusSnapshot.DiskMount m : s.disks) {
                TextView t = new TextView(requireContext());
                t.setText(String.format(Locale.US, "%s  %s\n   %s / %s (%s)  [%s]",
                        orDash(m.mount), m.fstype, orDash(m.usedHuman), orDash(m.totalHuman),
                        UiUtil.pct(m.percent), m.device));
                t.setTextSize(12);
                t.setTextColor(UiUtil.usageColor(m.percent));
                t.setPadding(0, 6, 0, 6);
                card.addView(t);
            }
            container.addView(card);
        }

        // ---------- 网络 ----------
        if (s.network != null) {
            LinearLayout card = newCardContainer("🌐 网络");
            addRow(card, "总入站", orDash(s.network.totalRxRate));
            addRow(card, "总出站", orDash(s.network.totalTxRate));
            for (StatusSnapshot.Network.Interface it : s.network.interfaces) {
                addRow(card, it.name, "↓" + orDash(it.rxRate) + "  ↑" + orDash(it.txRate));
            }
            container.addView(card);
        }

        // ---------- USB ----------
        if (s.usb != null) {
            LinearLayout card = newCardContainer("🔌 USB 设备（" + s.usb.count + "）");
            if (s.usb.devices.isEmpty()) {
                addRow(card, "无设备", "");
            } else {
                for (StatusSnapshot.Usb.Device d : s.usb.devices) {
                    String id = d.vendorId + ":" + d.productId;
                    String blocks = d.blockNodes.isEmpty() ? "" : "  块设备:" + String.join(",", d.blockNodes);
                    TextView t = new TextView(requireContext());
                    t.setText("• " + d.title
                            + (d.manufacturer.isEmpty() ? "" : "（" + d.manufacturer + "）")
                            + "\n   " + id + blocks);
                    t.setTextSize(12);
                    t.setTextColor(ContextCompat.getColor(requireContext(), R.color.text_primary));
                    t.setPadding(0, 6, 0, 6);
                    card.addView(t);
                }
            }
            container.addView(card);
        }

        // ---------- 活动告警 ----------
        if (!s.activeAlarms.isEmpty()) {
            addCard("🚨 活动告警", String.join("\n", s.activeAlarms));
        } else {
            addCard("🚨 活动告警", "无活动告警");
        }
    }

    // ---------------------------------------------------------------- 小组件

    private static String orDash(String s) {
        return s == null || s.isEmpty() ? "—" : s;
    }

    private String temp(Double t) {
        return t == null ? "—" : String.format(Locale.US, "%.1f ℃", t);
    }

    /** 数值文本：设备端可能给 "1500"、"1500.0"、""；统一按「整数不带小数」展示。 */
    private String fmt(String raw) {
        if (raw == null || raw.isEmpty()) return "—";
        try {
            double v = Double.parseDouble(raw);
            return (v == Math.floor(v)) ? String.valueOf((long) v) : String.format(Locale.US, "%.1f", v);
        } catch (NumberFormatException e) {
            return raw;
        }
    }

    private void addCard(String title, String body) {
        LinearLayout card = newCardContainer(title);
        TextView b = new TextView(requireContext());
        b.setText(body);
        b.setTextSize(13);
        b.setTextColor(ContextCompat.getColor(requireContext(), R.color.text_primary));
        card.addView(b);
        container.addView(card);
    }

    private LinearLayout newCardContainer(String title) {
        LinearLayout card = new LinearLayout(requireContext());
        card.setOrientation(LinearLayout.VERTICAL);
        card.setBackgroundResource(R.drawable.bg_card);
        LinearLayout.LayoutParams cp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        cp.topMargin = dp(10);
        card.setLayoutParams(cp);
        card.setPadding(dp(14), dp(12), dp(14), dp(12));

        TextView t = new TextView(requireContext());
        t.setText(title);
        t.setTextSize(15);
        t.setTypeface(Typeface.DEFAULT_BOLD);
        t.setTextColor(ContextCompat.getColor(requireContext(), R.color.brand_primary));
        t.setPadding(0, 0, 0, dp(6));
        card.addView(t);
        return card;
    }

    private void addRow(LinearLayout card, String label, String value) {
        LinearLayout row = new LinearLayout(requireContext());
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setPadding(0, dp(3), 0, dp(3));

        TextView l = new TextView(requireContext());
        l.setText(label);
        l.setTextSize(12);
        l.setTextColor(ContextCompat.getColor(requireContext(), R.color.text_secondary));
        l.setLayoutParams(new LinearLayout.LayoutParams(dp(110), ViewGroup.LayoutParams.WRAP_CONTENT));

        TextView v = new TextView(requireContext());
        v.setText(value == null || value.isEmpty() ? "—" : value);
        v.setTextSize(12);
        v.setTextColor(ContextCompat.getColor(requireContext(), R.color.text_primary));

        row.addView(l);
        row.addView(v);
        card.addView(row);
    }

    private void addBigMetric(LinearLayout card, String label, String value, int color) {
        LinearLayout row = new LinearLayout(requireContext());
        row.setGravity(Gravity.CENTER_VERTICAL);
        TextView v = new TextView(requireContext());
        v.setText(value);
        v.setTextSize(30);
        v.setTypeface(Typeface.DEFAULT_BOLD);
        v.setTextColor(color);
        TextView l = new TextView(requireContext());
        l.setText("  " + label);
        l.setTextSize(13);
        l.setTextColor(ContextCompat.getColor(requireContext(), R.color.text_secondary));
        row.addView(v);
        row.addView(l);
        card.addView(row);
    }

    private int dp(int v) {
        return (int) (v * getResources().getDisplayMetrics().density + 0.5f);
    }
}
