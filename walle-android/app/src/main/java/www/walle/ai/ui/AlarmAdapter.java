package www.walle.ai.ui;

import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.TextView;

import androidx.annotation.NonNull;
import androidx.recyclerview.widget.RecyclerView;

import www.walle.ai.R;

import org.json.JSONObject;

import java.util.List;

public class AlarmAdapter extends RecyclerView.Adapter<AlarmAdapter.VH> {

    private final List<JSONObject> data;

    AlarmAdapter(List<JSONObject> data) { this.data = data; }

    /** 用新列表整体替换（ViewModel 每次发布的是不可变快照）。 */
    void setItems(List<JSONObject> items) {
        data.clear();
        if (items != null) data.addAll(items);
        notifyDataSetChanged();
    }

    @NonNull
    @Override
    public VH onCreateViewHolder(@NonNull ViewGroup parent, int viewType) {
        View v = LayoutInflater.from(parent.getContext()).inflate(R.layout.item_alarm, parent, false);
        return new VH(v);
    }

    @Override
    public void onBindViewHolder(@NonNull VH h, int position) {
        JSONObject a = data.get(position);
        String event = a.optString("event", "");
        String level = a.optString("level", "info");
        int color = UiUtil.levelColor(level, event);

        h.bar.setBackgroundColor(color);
        h.tvEvent.setText(UiUtil.eventLabel(event) + " · " + a.optString("metric", ""));
        h.tvEvent.setTextColor(color);
        String sourceType = a.optString("alarm_source_type", "device");
        String sourceName = a.optString("alarm_source_name", "");
        if (sourceName.isEmpty()) sourceName = a.optString("hostname", "");
        if (sourceName.isEmpty()) {
            String id = a.optString("alarm_source_id",
                    a.optString("device_id", ""));
            sourceName = id.length() >= 8 ? id.substring(0, 8) : id;
        }
        String type = a.optString("alarm_type", "");
        String sourceLabel = ("agent".equals(sourceType) ? "Agent" : "设备");
        h.tvType.setText(sourceName.isEmpty()
                ? sourceLabel + (type.isEmpty() ? "" : " · " + type)
                : sourceLabel + " · " + sourceName
                        + (type.isEmpty() ? "" : " · " + type));
        h.tvMessage.setText(a.optString("message", ""));
        h.tvTime.setText(a.optString("datetime", ""));
    }

    @Override
    public int getItemCount() { return data.size(); }

    static class VH extends RecyclerView.ViewHolder {
        final View bar;
        final TextView tvEvent, tvType, tvMessage, tvTime;
        VH(@NonNull View v) {
            super(v);
            bar = v.findViewById(R.id.barLevel);
            tvEvent = v.findViewById(R.id.tvEvent);
            tvType = v.findViewById(R.id.tvType);
            tvMessage = v.findViewById(R.id.tvMessage);
            tvTime = v.findViewById(R.id.tvTime);
        }
    }
}
