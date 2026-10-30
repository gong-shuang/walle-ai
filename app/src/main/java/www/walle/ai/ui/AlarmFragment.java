package www.walle.ai.ui;

import android.os.Bundle;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.TextView;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.fragment.app.Fragment;
import androidx.recyclerview.widget.LinearLayoutManager;
import androidx.recyclerview.widget.RecyclerView;

import www.walle.ai.R;
import www.walle.ai.net.AlarmStore;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/** 告警列表：数据来自全局 AlarmStore，进入页面即把未读清零。 */
public class AlarmFragment extends Fragment implements AlarmStore.Observer {

    private final List<JSONObject> items = new ArrayList<>();
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
        RecyclerView rv = view.findViewById(R.id.recyclerAlarm);
        tvEmpty = view.findViewById(R.id.tvAlarmEmpty);
        rv.setLayoutManager(new LinearLayoutManager(requireContext()));
        adapter = new AlarmAdapter(items);
        rv.setAdapter(adapter);
        reload();
    }

    @Override
    public void onStart() {
        super.onStart();
        AlarmStore.get().addObserver(this);
        reload();
        // 已进入告警页，未读清零（设置页上的条数随之消失）
        AlarmStore.get().markAllRead();
    }

    @Override
    public void onStop() {
        super.onStop();
        AlarmStore.get().removeObserver(this);
    }

    public void clearAlarms() {
        AlarmStore.get().clear();
    }

    @Override
    public void onAlarmsChanged() {
        if (getActivity() == null) return;
        requireActivity().runOnUiThread(() -> {
            reload();
            // 正停留在告警页时新到的告警直接算已读，避免设置 tab 一直亮着红点
            if (isResumed()) AlarmStore.get().markAllRead();
        });
    }

    private void reload() {
        if (adapter == null) return;
        items.clear();
        items.addAll(AlarmStore.get().items());
        adapter.notifyDataSetChanged();
        updateEmpty();
    }

    private void updateEmpty() {
        if (tvEmpty != null) tvEmpty.setVisibility(items.isEmpty() ? View.VISIBLE : View.GONE);
    }
}
