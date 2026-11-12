package www.walle.ai.ui;

import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.TextView;

import androidx.annotation.NonNull;
import androidx.recyclerview.widget.RecyclerView;

import www.walle.ai.R;
import www.walle.ai.net.DeviceInfo;

import java.util.ArrayList;
import java.util.List;

/** 设备卡片列表适配器：一台设备一张卡片，点击切换当前设备。 */
public class DeviceAdapter extends RecyclerView.Adapter<DeviceAdapter.VH> {

    public interface OnDeviceClick { void onDeviceClick(DeviceInfo device); }

    private final List<DeviceInfo> data = new ArrayList<>();
    private String selectedKey = "";
    private final OnDeviceClick onClick;

    public DeviceAdapter(OnDeviceClick onClick) { this.onClick = onClick; }

    public void submit(List<DeviceInfo> list, String selectedKey) {
        data.clear();
        if (list != null) data.addAll(list);
        this.selectedKey = selectedKey == null ? "" : selectedKey;
        notifyDataSetChanged();
    }

    @NonNull
    @Override
    public VH onCreateViewHolder(@NonNull ViewGroup parent, int viewType) {
        View v = LayoutInflater.from(parent.getContext())
                .inflate(R.layout.item_device, parent, false);
        return new VH(v);
    }

    @Override
    public void onBindViewHolder(@NonNull VH h, int position) {
        DeviceInfo d = data.get(position);
        boolean selected = d.key().equals(selectedKey);

        h.dot.setBackgroundResource(d.online ? R.drawable.dot_online : R.drawable.dot_offline);
        h.tvName.setText(d.displayName());
        h.tvType.setText(d.typeLabel());
        h.tvCurrent.setVisibility(selected ? View.VISIBLE : View.GONE);
        h.tvModel.setText(d.modelLabel());

        String ip = d.ip == null || d.ip.isEmpty() ? "IP 未知" : d.ip;
        h.tvLine1.setText((d.online ? "在线" : "离线") + " · " + ip);

        String uptime = d.uptimeLabel();
        h.tvLine2.setText(d.osLabel() + " · " + (uptime.isEmpty() ? "" : "运行 " + uptime));

        h.tvVersion.setText("Version  " + (d.versionName == null || d.versionName.isEmpty() ? "—" : d.versionName));

        h.itemView.setOnClickListener(v -> {
            if (onClick != null) onClick.onDeviceClick(d);
        });
    }

    @Override
    public int getItemCount() { return data.size(); }

    static class VH extends RecyclerView.ViewHolder {
        final View dot;
        final TextView tvName, tvType, tvCurrent, tvModel, tvLine1, tvLine2, tvVersion;
        VH(@NonNull View v) {
            super(v);
            dot = v.findViewById(R.id.dotOnline);
            tvName = v.findViewById(R.id.tvName);
            tvType = v.findViewById(R.id.tvType);
            tvCurrent = v.findViewById(R.id.tvCurrent);
            tvModel = v.findViewById(R.id.tvModel);
            tvLine1 = v.findViewById(R.id.tvLine1);
            tvLine2 = v.findViewById(R.id.tvLine2);
            tvVersion = v.findViewById(R.id.tvVersion);
        }
    }
}
