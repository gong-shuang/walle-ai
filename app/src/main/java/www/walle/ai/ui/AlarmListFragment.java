package www.walle.ai.ui;

import android.os.Bundle;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.TextView;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.annotation.StringRes;
import androidx.fragment.app.Fragment;
import androidx.lifecycle.LiveData;
import androidx.lifecycle.ViewModelProvider;
import androidx.recyclerview.widget.LinearLayoutManager;
import androidx.recyclerview.widget.RecyclerView;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

import www.walle.ai.R;

/**
 * 「当前」「历史」两个告警列表的公共骨架：同一套列表布局与空态文案机制，
 * 子类只决定观察哪条数据、空态显示什么。
 */
public abstract class AlarmListFragment extends Fragment {

    private AlarmAdapter adapter;
    private TextView tvEmpty;

    /** 子类要观察的列表数据。 */
    protected abstract LiveData<List<JSONObject>> observeAlarms(AlarmViewModel vm);

    /** 列表为空时的提示文案。 */
    @StringRes
    protected abstract int emptyTextRes();

    @Nullable
    @Override
    public View onCreateView(@NonNull LayoutInflater inflater, @Nullable ViewGroup container,
                             @Nullable Bundle savedInstanceState) {
        return inflater.inflate(R.layout.fragment_alarm, container, false);
    }

    @Override
    public void onViewCreated(@NonNull View view, @Nullable Bundle savedInstanceState) {
        AlarmViewModel viewModel = new ViewModelProvider(this).get(AlarmViewModel.class);

        RecyclerView rv = view.findViewById(R.id.recyclerAlarm);
        tvEmpty = view.findViewById(R.id.tvAlarmEmpty);
        tvEmpty.setText(emptyTextRes());
        rv.setLayoutManager(new LinearLayoutManager(requireContext()));
        adapter = new AlarmAdapter(new ArrayList<>());
        rv.setAdapter(adapter);

        observeAlarms(viewModel).observe(getViewLifecycleOwner(), this::render);
    }

    private void render(List<JSONObject> items) {
        if (adapter == null) return;
        adapter.setItems(items);
        if (tvEmpty != null) {
            tvEmpty.setVisibility(items.isEmpty() ? View.VISIBLE : View.GONE);
        }
    }
}
