package www.walle.ai.ui;

import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.fragment.app.Fragment;
import androidx.recyclerview.widget.LinearLayoutManager;
import androidx.recyclerview.widget.RecyclerView;
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout;

import www.walle.ai.R;
import www.walle.ai.net.DeviceInfo;
import www.walle.ai.net.ConnState;
import www.walle.ai.net.MqttManager;
import www.walle.ai.net.Topics;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * 设备发现页（App 主页）：进入即在租户内广播 {@code walle/{tenant}/discovery/query}，
 * 收集窗口期（3 秒）内的 {@code identity} 回包展示成设备卡片列表。
 * <p>列表只由回包建立：3 秒内没有回包的设备一条都不显示；
 * {@code online} 仅用于更新已展示设备的上线/离线状态，不会新增卡片。</p>
 */
public class DevicesFragment extends Fragment implements MqttManager.Listener {

    public interface OnDevicePicked { void onDevicePicked(String key); }

    private static final long SCAN_TIMEOUT_MS = 3000L;

    private final Handler handler = new Handler(Looper.getMainLooper());
    /** 扫描窗口结束时结算结果（不只是停止下拉刷新） */
    private final Runnable finishScanRunnable = this::finishScan;

    /** 当前列表展示的设备键：上一次扫描窗口内回过 identity 的设备 */
    private final Set<String> shownKeys = new HashSet<>();
    /** 本次扫描窗口内已回包的设备键，窗口结束后整体替换 shownKeys */
    private final Set<String> pendingKeys = new HashSet<>();
    /** 是否处于扫描窗口内 */
    private boolean scanning;

    private MqttManager mgr;
    private DeviceAdapter adapter;
    private SwipeRefreshLayout swipe;
    private TextView tvSummary, tvEmpty;
    private OnDevicePicked picked;

    public void setOnDevicePicked(OnDevicePicked l) { this.picked = l; }

    @Nullable
    @Override
    public View onCreateView(@NonNull LayoutInflater inflater, @Nullable ViewGroup container,
                             @Nullable Bundle savedInstanceState) {
        return inflater.inflate(R.layout.fragment_devices, container, false);
    }

    @Override
    public void onViewCreated(@NonNull View v, @Nullable Bundle savedInstanceState) {
        super.onViewCreated(v, savedInstanceState);
        mgr = MqttManager.get(requireContext());
        tvSummary = v.findViewById(R.id.tvSummary);
        tvEmpty = v.findViewById(R.id.tvEmpty);
        swipe = v.findViewById(R.id.swipeDevices);
        RecyclerView recycler = v.findViewById(R.id.recyclerDevice);
        v.findViewById(R.id.btnScan).setOnClickListener(x -> scan(true));

        adapter = new DeviceAdapter(this::onCardClick);
        recycler.setLayoutManager(new LinearLayoutManager(requireContext()));
        recycler.setAdapter(adapter);

        swipe.setColorSchemeResources(R.color.brand_primary);
        // 深色主题：下拉刷新圆环的底色改成卡片深色，避免白圆盘突兀
        swipe.setProgressBackgroundColorSchemeResource(R.color.card_bg);
        swipe.setOnRefreshListener(() -> scan(true));

        render();
    }

    @Override
    public void onStart() {
        super.onStart();
        mgr.addListener(this);
        render();
        // 打开 App / 回到本页即扫描一次
        scan(false);
    }

    @Override
    public void onStop() {
        super.onStop();
        mgr.removeListener(this);
        handler.removeCallbacks(finishScanRunnable);
        scanning = false;
        pendingKeys.clear();
        if (swipe != null) swipe.setRefreshing(false);
    }

    private void scan(boolean manual) {
        if (!mgr.isConnected()) {
            swipe.setRefreshing(false);
            tvSummary.setText(R.string.device_unknown);
            if (manual) toast("尚未连接 MQTT 服务器");
            return;
        }
        // 开启新的扫描窗口：窗口内只收集回包，不立刻改动列表；
        // 3 秒后以窗口内的回包整体替换列表，没回包的设备一台都不显示。
        scanning = true;
        pendingKeys.clear();
        mgr.requestDeviceInfo();
        swipe.setRefreshing(true);
        handler.removeCallbacks(finishScanRunnable);
        handler.postDelayed(finishScanRunnable, SCAN_TIMEOUT_MS);
        render();
    }

    /** 扫描窗口结算：只保留窗口内回过 identity 的设备。 */
    private void finishScan() {
        scanning = false;
        shownKeys.clear();
        shownKeys.addAll(pendingKeys);
        pendingKeys.clear();
        if (swipe != null) swipe.setRefreshing(false);
        // 原选中设备这次没回包（已从列表移除）时，自动切到第一台可见设备，没有则清空选择
        if (!shownKeys.contains(mgr.getSelectedKey())) {
            mgr.selectDevice(firstVisibleKey());
        }
        render();
    }

    private String firstVisibleKey() {
        for (DeviceInfo d : mgr.getDevices()) {
            if (shownKeys.contains(d.key())) return d.key();
        }
        return "";
    }

    private void onCardClick(DeviceInfo d) {
        mgr.selectDevice(d.key());
        render();
        if (picked != null) picked.onDevicePicked(d.key());
    }

    private void render() {
        if (!isAdded() || adapter == null) return;
        // 只展示本次（或上一次）扫描窗口内回过 identity 的设备
        List<DeviceInfo> visible = new ArrayList<>();
        for (DeviceInfo d : mgr.getDevices()) {
            if (shownKeys.contains(d.key())) visible.add(d);
        }
        int online = 0;
        for (DeviceInfo d : visible) if (d.online) online++;

        adapter.submit(visible, mgr.getSelectedKey());
        tvEmpty.setVisibility(visible.isEmpty() ? View.VISIBLE : View.GONE);
        if (visible.isEmpty()) {
            tvSummary.setText(scanning && mgr.isConnected()
                    ? R.string.scanning : R.string.device_unknown);
        } else {
            tvSummary.setText(getString(R.string.devices_found, visible.size(), online));
        }
    }

    @Override
    public void onDevicesChanged() {
        render();
    }

    @Override
    public void onDeviceInfo(DeviceInfo device) {
        // 扫描窗口内收到的回包决定列表内容；窗口外到达的回包不新增卡片
        if (scanning) pendingKeys.add(device.key());
        render();
    }

    @Override
    public void onOnline(org.json.JSONObject payload) {
        // online 只更新已展示设备的上线/离线状态，不新增卡片
        render();
    }

    @Override
    public void onDeviceAdded(DeviceInfo device) {
        // online(online=true) 新增的设备立即进入列表，不必等它的 identity 回包
        String key = device.key();
        shownKeys.add(key);
        if (scanning) pendingKeys.add(key);
        render();
    }

    @Override
    public void onConnState(ConnState state, String info) {
        if (state == ConnState.CONNECTED) {
            scan(false);
        } else {
            render();
        }
    }

    @Override
    public void onDeliveryError(String kind, String error) {
        if (www.walle.ai.net.TopicNames.KIND_DISCOVERY.equals(kind)) {
            handler.removeCallbacks(finishScanRunnable);
            scanning = false;
            pendingKeys.clear();
            swipe.setRefreshing(false);
            toast(error);
        }
    }

    private void toast(String msg) {
        if (isAdded()) Toast.makeText(requireContext(), msg, Toast.LENGTH_SHORT).show();
    }
}
