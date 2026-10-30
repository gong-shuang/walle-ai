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
import androidx.recyclerview.widget.LinearLayoutManager;
import androidx.recyclerview.widget.RecyclerView;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

import www.walle.ai.R;

/** 告警列表：数据来自 AlarmStore，经 {@link AlarmViewModel} 暴露成 LiveData。 */
public class AlarmFragment extends Fragment {

    private AlarmViewModel viewModel;
    private AlarmAdapter adapter;
    private TextView tvEmpty;

    @Nullable
    @Override
    public View onCreateView(@NonNull LayoutInflater inflater, @Nullable ViewGroup container,
                             @Nullable Bundle savedInstanceState) {
        return inflater.inflate(R.layout.fragment_alarm, container, false);
    }

    @Override
    public void onViewCreated(@NonNull View view, @Nullable Bundle savedInstanceState) {
        viewModel = new ViewModelProvider(this).get(AlarmViewModel.class);

        RecyclerView rv = view.findViewById(R.id.recyclerAlarm);
        tvEmpty = view.findViewById(R.id.tvAlarmEmpty);
        rv.setLayoutManager(new LinearLayoutManager(requireContext()));
        adapter = new AlarmAdapter(new ArrayList<>());
        rv.setAdapter(adapter);

        viewModel.alarms().observe(getViewLifecycleOwner(), this::render);
    }

    @Override
    public void onStart() {
        super.onStart();
        // 进入告警页即把未读清零（设置 tab 的红点随之消失）
        viewModel.onScreenVisible();
    }

    /** 由宿主页面（AlarmActivity）调用。 */
    public void clearAlarms() {
        if (viewModel != null) viewModel.clear();
    }

    private void render(List<JSONObject> items) {
        if (adapter == null) return;
        adapter.setItems(items);
        if (tvEmpty != null) {
            tvEmpty.setVisibility(items.isEmpty() ? View.VISIBLE : View.GONE);
        }
    }
}
