package www.walle.ai.ui;

import android.os.Bundle;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.TextView;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.fragment.app.Fragment;
import androidx.lifecycle.ViewModelProvider;
import androidx.fragment.app.FragmentManager;
import androidx.fragment.app.FragmentTransaction;

import www.walle.ai.R;
import www.walle.ai.net.DeviceInfo;
import www.walle.ai.net.MqttManager;
import com.google.android.material.tabs.TabLayout;


/**
 * 设备详情容器页：顶部「当前设备」信息条 + 状态 / 控制 / 命令 三个子页。
 * 底部导航第二项对应本页。
 */
public class DeviceDetailFragment extends Fragment {

    private static final String T_STATUS = "detail_status";
    private static final String T_CONTROL = "detail_control";
    private static final String T_COMMAND = "detail_command";

    private MqttManager mgr;
    private TextView tvCurrentDevice;
    private TabLayout tabs;

    private StatusFragment statusFragment;
    private ControlFragment controlFragment;
    private CommandFragment commandFragment;

    @Nullable
    @Override
    public View onCreateView(@NonNull LayoutInflater inflater, @Nullable ViewGroup container,
                             @Nullable Bundle savedInstanceState) {
        return inflater.inflate(R.layout.fragment_device_detail, container, false);
    }

    @Override
    public void onViewCreated(@NonNull View view, @Nullable Bundle savedInstanceState) {
        mgr = MqttManager.get(requireContext());
        tvCurrentDevice = view.findViewById(R.id.tvCurrentDevice);
        tabs = view.findViewById(R.id.detailTabs);

        FragmentManager fm = getChildFragmentManager();
        statusFragment = (StatusFragment) fm.findFragmentByTag(T_STATUS);
        controlFragment = (ControlFragment) fm.findFragmentByTag(T_CONTROL);
        commandFragment = (CommandFragment) fm.findFragmentByTag(T_COMMAND);

        FragmentTransaction ft = fm.beginTransaction();
        if (statusFragment == null) {
            statusFragment = new StatusFragment();
            ft.add(R.id.detailContainer, statusFragment, T_STATUS);
        }
        if (controlFragment == null) {
            controlFragment = new ControlFragment();
            ft.add(R.id.detailContainer, controlFragment, T_CONTROL);
            ft.hide(controlFragment);
        }
        if (commandFragment == null) {
            commandFragment = new CommandFragment();
            ft.add(R.id.detailContainer, commandFragment, T_COMMAND);
            ft.hide(commandFragment);
        }
        ft.commit();

        // 支持 intent 指定子页（0 状态 / 1 控制 / 2 命令），便于自动化验证
        int startSub = requireActivity().getIntent().getIntExtra("sub", -1);
        android.util.Log.i("DeviceDetail", "onViewCreated startSub=" + startSub
                + " tabs=" + tabs.getTabCount());
        int target = 0;
        if (startSub >= 0 && startSub < tabs.getTabCount()) {
            target = startSub;
            TabLayout.Tab t = tabs.getTabAt(startSub);
            if (t != null) t.select();
        }
        showTab(target);
        tabs.addOnTabSelectedListener(new TabLayout.OnTabSelectedListener() {
            @Override public void onTabSelected(TabLayout.Tab tab) { showTab(tab.getPosition()); }
            @Override public void onTabUnselected(TabLayout.Tab tab) { }
            @Override public void onTabReselected(TabLayout.Tab tab) { }
        });

        ConnectionViewModel vm = new ViewModelProvider(requireActivity()).get(ConnectionViewModel.class);
        vm.state().observe(getViewLifecycleOwner(), this::renderConnection);
    }

    private void showTab(int position) {
        Fragment target;
        if (position == 1) target = controlFragment;
        else if (position == 2) target = commandFragment;
        else target = statusFragment;
        if (target == null || !isAdded()) return;
        FragmentTransaction ft = getChildFragmentManager().beginTransaction();
        for (Fragment f : new Fragment[]{statusFragment, controlFragment, commandFragment}) {
            if (f != null && f != target && !f.isHidden()) ft.hide(f);
        }
        ft.show(target);
        ft.commitAllowingStateLoss();
    }

    /** 设备列表里点了某台设备：切到本页并回到「状态」子页，立即查询该设备。 */
    public void onDeviceSwitched() {
        if (!isAdded()) return;
        if (tabs != null && tabs.getTabCount() > 0) {
            TabLayout.Tab t = tabs.getTabAt(0);
            if (t != null) t.select();
        }
        // 标题由 ConnectionViewModel 观察自动刷新，这里只需重置子页并查询
        if (statusFragment != null) statusFragment.onDeviceSwitched();
    }

    private void updateDeviceLabel(@Nullable DeviceInfo d) {
        if (tvCurrentDevice == null) return;
        if (d == null) {
            tvCurrentDevice.setText(R.string.no_device_selected);
        } else {
            tvCurrentDevice.setText(getString(R.string.current_device_detail,
                    d.displayName(), d.typeLabel(), d.shortUuid()));
        }
    }

    /** 当前设备标题由 ConnectionViewModel 驱动。 */
    private void renderConnection(ConnectionViewModel.UiState ui) {
        updateDeviceLabel(ui.selected);
    }
}
