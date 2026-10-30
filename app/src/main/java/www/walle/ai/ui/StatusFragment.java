package www.walle.ai.ui;

import android.graphics.Typeface;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.core.content.ContextCompat;
import androidx.fragment.app.Fragment;
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout;

import www.walle.ai.R;
import www.walle.ai.net.DeviceInfo;
import www.walle.ai.net.ConnState;
import www.walle.ai.net.MqttManager;
import com.google.android.material.button.MaterialButton;
import androidx.appcompat.widget.SwitchCompat;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.Locale;

public class StatusFragment extends Fragment implements MqttManager.Listener {

    private MqttManager mgr;
    private LinearLayout container;
    private TextView tvEmpty, tvUpdated, tvCurrentDevice;
    private MaterialButton btnQuery;
    private SwitchCompat swAuto;
    private SwipeRefreshLayout swipe;
    private final Handler handler = new Handler(Looper.getMainLooper());

    private boolean waiting = false;
    /** 本次状态查询的 request_id，用于过滤迟到的旧结果。 */
    private String waitingRid = "";
    private final Runnable autoTask = new Runnable() {
        @Override public void run() {
            if (swAuto != null && swAuto.isChecked() && mgr.getState() == ConnState.CONNECTED) {
                doQuery();
            }
            int sec = Math.max(2, mgr.getConfig().statusIntervalSec);
            handler.postDelayed(this, sec * 1000L);
        }
    };

    @Nullable
    @Override
    public View onCreateView(@NonNull LayoutInflater inflater, @Nullable ViewGroup container,
                             @Nullable Bundle savedInstanceState) {
        return inflater.inflate(R.layout.fragment_status, container, false);
    }

    @Override
    public void onViewCreated(@NonNull View view, @Nullable Bundle savedInstanceState) {
        mgr = MqttManager.get(requireContext());
        container = view.findViewById(R.id.statusContainer);
        tvEmpty = view.findViewById(R.id.tvEmpty);
        tvUpdated = view.findViewById(R.id.tvUpdated);
        tvCurrentDevice = view.findViewById(R.id.tvCurrentDevice);
        btnQuery = view.findViewById(R.id.btnQuery);
        swAuto = view.findViewById(R.id.swAuto);
        swipe = view.findViewById(R.id.swipe);

        btnQuery.setOnClickListener(v -> doQuery());
        swipe.setOnRefreshListener(this::doQuery);

        updateDeviceLabel();
        renderCached();
        updateQueryEnabled();
    }

    /** 设备页切换了当前设备：刷新标题、清空旧数据并立即查询一次。 */
    public void onDeviceSwitched() {
        if (!isAdded()) return;
        updateDeviceLabel();
        container.removeAllViews();
        tvEmpty.setVisibility(View.VISIBLE);
        tvUpdated.setText("");
        renderCached();
        updateQueryEnabled();
        doQuery();
    }

    private void renderCached() {
        JSONObject cached = mgr.getStatus(mgr.getSelectedKey());
        if (cached != null) render(cached);
    }

    private void updateDeviceLabel() {
        if (tvCurrentDevice == null) return;
        DeviceInfo d = mgr.getSelectedDevice();
        if (d == null) {
            tvCurrentDevice.setText(R.string.no_device_selected);
        } else {
            tvCurrentDevice.setText(getString(R.string.current_device, d.displayName())
                    + "（" + d.typeLabel() + " · uuid " + d.shortUuid() + "）");
        }
    }

    private void doQuery() {
        if (mgr.getState() != ConnState.CONNECTED) {
            swipe.setRefreshing(false);
            return;
        }
        if (mgr.getSelectedKey().isEmpty()) {
            swipe.setRefreshing(false);
            Toast.makeText(requireContext(), R.string.no_device_selected, Toast.LENGTH_SHORT).show();
            return;
        }
        waitingRid = mgr.requestStatus();
        waiting = !waitingRid.isEmpty();
        updateQueryEnabled();
        swipe.setRefreshing(true);
        handler.postDelayed(() -> {
            if (waiting) {
                waiting = false;
                waitingRid = "";
                swipe.setRefreshing(false);
                updateQueryEnabled();
            }
        }, 6000);
    }

    private void updateQueryEnabled() {
        if (btnQuery == null) return;
        boolean ok = mgr.getState() == ConnState.CONNECTED
                && !mgr.getSelectedKey().isEmpty();
        btnQuery.setEnabled(ok);
        btnQuery.setText(waiting ? R.string.refreshing : R.string.query_status);
    }

    /** 连接状态变化：按钮可用性跟着变；连上后若当前设备还没有数据，自动查一次。 */
    @Override
    public void onConnState(ConnState state, String info) {
        if (getActivity() == null) return;
        requireActivity().runOnUiThread(() -> {
            updateQueryEnabled();
            if (state == ConnState.CONNECTED) autoQueryIfIdle();
        });
    }

    @Override
    public void onStart() {
        super.onStart();
        mgr.addListener(this);
        updateDeviceLabel();
        renderCached();
        handler.postDelayed(autoTask, Math.max(2, mgr.getConfig().statusIntervalSec) * 1000L);
    }

    @Override
    public void onStop() {
        super.onStop();
        mgr.removeListener(this);
        handler.removeCallbacks(autoTask);
    }

    // ---------------- Listener ----------------
    @Override public void onDevicesChanged() {
        if (getActivity() == null) return;
        requireActivity().runOnUiThread(() -> {
            updateDeviceLabel();
            updateQueryEnabled();
            autoQueryIfIdle();
        });
    }

    /** 已连接 + 已选设备 + 还没有数据时自动查一次，避免一直停在“暂无数据”。 */
    private void autoQueryIfIdle() {
        if (waiting) return;
        if (mgr.getState() != ConnState.CONNECTED) return;
        if (mgr.getSelectedKey().isEmpty()) return;
        if (mgr.getStatus(mgr.getSelectedKey()) != null) return;
        doQuery();
    }
    @Override public void onMachineStatus(JSONObject s) {
        if (getActivity() == null) return;
        String uuid = s.optString("uuid", "");
        String sel = mgr.getSelectedKey();
        if (!uuid.isEmpty() && !sel.isEmpty() && !uuid.equals(sel)) return;
        requireActivity().runOnUiThread(() -> {
            String rid = s.optString("request_id", "");
            // 只有与本次查询匹配（或报文没带 rid）才结束「查询中」；迟到的旧结果只渲染不改状态
            if (waitingRid.isEmpty() || rid.isEmpty() || rid.equals(waitingRid)) {
                waiting = false;
                waitingRid = "";
                swipe.setRefreshing(false);
                updateQueryEnabled();
            }
            render(s);
        });
    }

    // ---------------- Rendering ----------------
    private void render(JSONObject s) {
        container.removeAllViews();
        tvEmpty.setVisibility(View.GONE);
        tvUpdated.setText("最近更新：" + s.optString("datetime", ""));

        // 系统卡片
        JSONObject sys = s.optJSONObject("system");
        StringBuilder sy = new StringBuilder();
        if (sys != null) {
            String model = sys.optString("model", "");
            if (!model.isEmpty()) sy.append("型号：").append(model).append('\n');
        }
        sy.append("主机名：").append(s.optString("hostname", "—")).append('\n');
        sy.append("IP：").append(s.optString("ip", "—")).append('\n');
        if (sys != null) {
            sy.append("系统：").append(sys.optString("os", "—"))
              .append("  ").append(sys.optString("machine", "")).append('\n');
            sy.append("内核：").append(sys.optString("kernel", "—")).append('\n');
        }
        long up = s.optLong("uptime_seconds", 0);
        sy.append("已运行：").append(UiUtil.uptime(up));
        addCard("🖥 系统", sy.toString());

        // CPU 卡片
        JSONObject cpu = s.optJSONObject("cpu");
        if (cpu != null) {
            LinearLayout card = newCardContainer("🧠 CPU");
            Double pct = UiUtil.optDouble(cpu, "percent");
            addBigMetric(card, "使用率", UiUtil.pct(pct), UiUtil.usageColor(pct));
            addRow(card, "温度", temp(UiUtil.optDouble(cpu, "temperature_c")));
            addRow(card, "负载 1/5/15m",
                    nz(cpu, "load_avg_1m") + " / " + nz(cpu, "load_avg_5m") + " / " + nz(cpu, "load_avg_15m"));
            JSONObject freq = cpu.optJSONObject("frequency_mhz");
            if (freq != null) {
                addRow(card, "主频", nz(freq, "current") + " / " + nz(freq, "min") + " ~ " + nz(freq, "max") + " MHz");
            }
            addRow(card, "核心数", "逻辑 " + cpu.optInt("count_logical", -1)
                    + "，物理 " + cpu.optInt("count_physical", -1));
            JSONArray cores = cpu.optJSONArray("per_core_percent");
            if (cores != null && cores.length() > 0) {
                StringBuilder cs = new StringBuilder();
                for (int i = 0; i < cores.length(); i++) {
                    cs.append(String.format(Locale.US, "核%d:%.0f%%  ", i, cores.optDouble(i, 0)));
                }
                addRow(card, "各核", cs.toString().trim());
            }
            container.addView(card);
        }

        // 内存
        JSONObject mem = s.optJSONObject("memory");
        if (mem != null) {
            LinearLayout card = newCardContainer("💾 内存");
            Double mpct = UiUtil.optDouble(mem, "percent");
            addBigMetric(card, "使用率", UiUtil.pct(mpct), UiUtil.usageColor(mpct));
            addRow(card, "容量", mem.optString("used_human", "—") + " / " + mem.optString("total_human", "—"));
            addRow(card, "可用", mem.optString("available_human", "—"));
            JSONObject swap = mem.optJSONObject("swap");
            if (swap != null) {
                addRow(card, "Swap", swap.optString("used_human", "—") + " / "
                        + swap.optString("total_human", "—") + "（" + UiUtil.pct(UiUtil.optDouble(swap, "percent")) + "）");
            }
            container.addView(card);
        }

        // 磁盘
        JSONObject disk = s.optJSONObject("disk");
        if (disk != null) {
            JSONArray mounts = disk.optJSONArray("mounts");
            if (mounts != null && mounts.length() > 0) {
                LinearLayout card = newCardContainer("💿 磁盘");
                for (int i = 0; i < mounts.length(); i++) {
                    JSONObject m = mounts.optJSONObject(i);
                    if (m == null) continue;
                    Double dp = UiUtil.optDouble(m, "percent");
                    TextView t = new TextView(requireContext());
                    t.setText(String.format(Locale.US, "%s  %s\n   %s / %s (%s)  [%s]",
                            m.optString("mount", "—"),
                            m.optString("fstype", ""),
                            m.optString("used_human", "—"),
                            m.optString("total_human", "—"),
                            UiUtil.pct(dp),
                            m.optString("device", "")));
                    t.setTextSize(12);
                    t.setTextColor(UiUtil.usageColor(dp));
                    t.setPadding(0, 6, 0, 6);
                    card.addView(t);
                }
                container.addView(card);
            }
        }

        // 网络
        JSONObject net = s.optJSONObject("network");
        if (net != null) {
            LinearLayout card = newCardContainer("🌐 网络");
            addRow(card, "总入站", net.optString("total_rx_rate", "—"));
            addRow(card, "总出站", net.optString("total_tx_rate", "—"));
            JSONObject ifaces = net.optJSONObject("interfaces");
            if (ifaces != null) {
                JSONArray names = ifaces.names();
                if (names != null) {
                    for (int i = 0; i < names.length(); i++) {
                        String n = names.optString(i);
                        JSONObject it = ifaces.optJSONObject(n);
                        if (it == null) continue;
                        addRow(card, n, "↓" + it.optString("rx_rate", "—")
                                + "  ↑" + it.optString("tx_rate", "—"));
                    }
                }
            }
            container.addView(card);
        }

        // USB
        JSONObject usb = s.optJSONObject("usb");
        if (usb != null) {
            LinearLayout card = newCardContainer("🔌 USB 设备（" + usb.optInt("count", 0) + "）");
            JSONArray devs = usb.optJSONArray("devices");
            if (devs != null && devs.length() > 0) {
                for (int i = 0; i < devs.length(); i++) {
                    JSONObject d = devs.optJSONObject(i);
                    if (d == null) continue;
                    String title = d.optString("product", "");
                    if (title.isEmpty()) title = d.optString("sysfs", "未知设备");
                    String manuf = d.optString("manufacturer", "");
                    String vid = d.optString("vendor_id", ""), pid = d.optString("product_id", "");
                    JSONArray blocks = d.optJSONArray("block_nodes");
                    String blk = "";
                    if (blocks != null && blocks.length() > 0) {
                        StringBuilder sb = new StringBuilder("  块设备:");
                        for (int bi = 0; bi < blocks.length(); bi++) {
                            if (bi > 0) sb.append(",");
                            sb.append(blocks.optString(bi));
                        }
                        blk = sb.toString();
                    }
                    TextView t = new TextView(requireContext());
                    t.setText("• " + title + (manuf.isEmpty() ? "" : "（" + manuf + "）")
                            + "\n   " + vid + ":" + pid + blk);
                    t.setTextSize(12);
                    t.setTextColor(ContextCompat.getColor(requireContext(), R.color.text_primary));
                    t.setPadding(0, 6, 0, 6);
                    card.addView(t);
                }
            } else {
                addRow(card, "无设备", "");
            }
            container.addView(card);
        }

        // 活动告警
        JSONObject alarms = s.optJSONObject("alarms");
        if (alarms != null) {
            JSONArray active = alarms.optJSONArray("active");
            String a = active != null && active.length() > 0
                    ? UiUtil.joinJsonStrings(active) : "无活动告警";
            addCard("🚨 活动告警", a);
        }
    }

    private String temp(Double t) { return t == null ? "—" : String.format(Locale.US, "%.1f ℃", t); }
    private String nz(JSONObject o, String k) {
        if (!o.has(k) || o.isNull(k)) return "—";
        double v = o.optDouble(k, Double.NaN);
        if (Double.isNaN(v)) return "—";
        return (v == Math.floor(v)) ? String.valueOf((long) v) : String.format(Locale.US, "%.1f", v);
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
