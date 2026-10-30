package www.walle.ai.ui;

import android.os.Bundle;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.fragment.app.Fragment;
import androidx.lifecycle.ViewModelProvider;
import androidx.recyclerview.widget.LinearLayoutManager;
import androidx.recyclerview.widget.RecyclerView;
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout;

import java.util.List;

import www.walle.ai.R;
import www.walle.ai.net.DeviceInfo;

/**
 * 设备列表页：只渲染 {@link DevicesViewModel.UiState}。
 *
 * <p>扫描窗口（3 秒收敛）、选中项兜底、回包收集等逻辑都在 ViewModel 里；
 * 页面旋转/切 tab 不会再打断扫描或重复结算。</p>
 */
public class DevicesFragment extends Fragment {

    public interface OnDevicePicked {
        void onDevicePicked(String key);
    }

    private DevicesViewModel viewModel;
    private DeviceAdapter adapter;
    private SwipeRefreshLayout swipe;
    private TextView tvSummary, tvEmpty;
    private OnDevicePicked picked;

    public void setOnDevicePicked(OnDevicePicked l) {
        this.picked = l;
    }

    @Nullable
    @Override
    public View onCreateView(@NonNull LayoutInflater inflater, @Nullable ViewGroup container,
                             @Nullable Bundle savedInstanceState) {
        return inflater.inflate(R.layout.fragment_devices, container, false);
    }

    @Override
    public void onViewCreated(@NonNull View v, @Nullable Bundle savedInstanceState) {
        super.onViewCreated(v, savedInstanceState);
        viewModel = new ViewModelProvider(this).get(DevicesViewModel.class);

        tvSummary = v.findViewById(R.id.tvSummary);
        tvEmpty = v.findViewById(R.id.tvEmpty);
        swipe = v.findViewById(R.id.swipeDevices);
        RecyclerView recycler = v.findViewById(R.id.recyclerDevice);

        adapter = new DeviceAdapter(this::onCardClick);
        recycler.setLayoutManager(new LinearLayoutManager(requireContext()));
        recycler.setAdapter(adapter);

        swipe.setColorSchemeResources(R.color.brand_primary);
        // 深色主题：下拉刷新圆环的底色改成卡片深色，避免白圆盘突兀
        swipe.setProgressBackgroundColorSchemeResource(R.color.card_bg);
        swipe.setOnRefreshListener(() -> viewModel.scan(true));
        v.findViewById(R.id.btnScan).setOnClickListener(x -> viewModel.scan(true));

        viewModel.state().observe(getViewLifecycleOwner(), this::render);
        viewModel.toast().observe(getViewLifecycleOwner(), msg -> {
            if (msg != null && !msg.isEmpty()) {
                Toast.makeText(requireContext(), msg, Toast.LENGTH_SHORT).show();
                viewModel.clearToast();
            }
        });
    }

    @Override
    public void onStart() {
        super.onStart();
        // 打开 App / 回到本页即扫描一次（ViewModel 会判断是否已连接）
        viewModel.scan(false);
    }

    private void onCardClick(DeviceInfo d) {
        viewModel.select(d.key());
        if (picked != null) picked.onDevicePicked(d.key());
    }

    private void render(DevicesViewModel.UiState ui) {
        if (adapter == null) return;
        adapter.submit(ui.visible, ui.selectedKey);

        swipe.setRefreshing(ui.scanning);
        tvEmpty.setVisibility(ui.visible.isEmpty() ? View.VISIBLE : View.GONE);
        if (ui.visible.isEmpty()) {
            tvSummary.setText(ui.scanning && ui.connected
                    ? R.string.scanning : R.string.device_unknown);
        } else {
            tvSummary.setText(getString(R.string.devices_found, ui.visible.size(), ui.onlineCount()));
        }
    }

    /** 供宿主页面（MainActivity）在切 tab 后刷新用。 */
    public List<DeviceInfo> currentDevices() {
        DevicesViewModel.UiState ui = viewModel == null ? null : viewModel.state().getValue();
        return ui == null ? java.util.Collections.emptyList() : ui.visible;
    }
}
