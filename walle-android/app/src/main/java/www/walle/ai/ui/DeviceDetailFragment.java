package www.walle.ai.ui;

import android.os.Bundle;
import android.text.SpannableStringBuilder;
import android.text.Spanned;
import android.text.style.ForegroundColorSpan;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.TextView;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.core.content.ContextCompat;
import androidx.fragment.app.Fragment;
import androidx.lifecycle.ViewModelProvider;
import androidx.fragment.app.FragmentManager;
import androidx.fragment.app.FragmentTransaction;

import www.walle.ai.R;
import www.walle.ai.net.DeviceInfo;
import www.walle.ai.net.MqttManager;
import com.google.android.material.tabs.TabLayout;


/**
 * 设备详情容器页：顶部「当前设备」信息条 + 状态 / 控制 两个子页。
 * 底部导航第二项对应本页。
 */
public class DeviceDetailFragment extends Fragment {

    private static final String T_STATUS = "detail_status";
    private static final String T_CONTROL = "detail_control";

    private MqttManager mgr;
    private View vDot;
    private TextView tvCurrentDevice;
    private TabLayout tabs;

    private StatusFragment statusFragment;
    private ControlFragment controlFragment;

    @Nullable
    @Override
    public View onCreateView(@NonNull LayoutInflater inflater, @Nullable ViewGroup container,
                             @Nullable Bundle savedInstanceState) {
        return inflater.inflate(R.layout.fragment_device_detail, container, false);
    }

    @Override
    public void onViewCreated(@NonNull View view, @Nullable Bundle savedInstanceState) {
        mgr = MqttManager.get(requireContext());
        vDot = view.findViewById(R.id.dotOnline);
        tvCurrentDevice = view.findViewById(R.id.tvCurrentDevice);
        tabs = view.findViewById(R.id.detailTabs);

        FragmentManager fm = getChildFragmentManager();
        statusFragment = (StatusFragment) fm.findFragmentByTag(T_STATUS);
        controlFragment = (ControlFragment) fm.findFragmentByTag(T_CONTROL);

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
        ft.commit();

        // 支持 intent 指定子页（0 状态 / 1 控制），便于自动化验证
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
        else target = statusFragment;
        if (target == null || !isAdded()) return;
        FragmentTransaction ft = getChildFragmentManager().beginTransaction();
        for (Fragment f : new Fragment[]{statusFragment, controlFragment}) {
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
        // 点设备只发两个主题：状态页的 state/query + 控制页的 capability/query。
        // 控制页此时即使不可见也要查，这样点「控制」tab 时能力已就绪，不必再发一次。
        if (statusFragment != null) statusFragment.onDeviceSwitched();
        if (controlFragment != null) controlFragment.onDeviceSwitched();
    }

    /** 顶部信息条：「当前设备：名称」+ 在线绿点 / 离线红点（离线附带换设备提示）。 */
    private void updateDeviceLabel(@Nullable DeviceInfo d) {
        if (tvCurrentDevice == null) return;
        if (d == null) {
            vDot.setBackgroundResource(R.drawable.dot_warning);
            tvCurrentDevice.setText(R.string.no_device_selected);
            return;
        }

        boolean online = d.online;
        SpannableStringBuilder sb = new SpannableStringBuilder(
                getString(R.string.current_device, d.displayName()));

        if (!online) {
            sb.append(' ').append(getString(R.string.current_device_offline_hint));
            sb.setSpan(new ForegroundColorSpan(
                            ContextCompat.getColor(requireContext(), R.color.state_error)),
                    sb.length() - getString(R.string.current_device_offline_hint).length(),
                    sb.length(), Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
        }
        vDot.setBackgroundResource(d.online ? R.drawable.dot_online : R.drawable.dot_offline);
        tvCurrentDevice.setText(sb);
    }

    /** 当前设备标题由 ConnectionViewModel 驱动。 */
    private void renderConnection(ConnectionViewModel.UiState ui) {
        updateDeviceLabel(ui.selected);
    }
}
