package www.walle.ai.ui;

import android.content.Context;
import android.graphics.Typeface;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.BaseAdapter;
import android.widget.TextView;

import androidx.core.content.ContextCompat;

import www.walle.ai.R;

/**
 * 摄像头 Spinner 通用 Adapter。
 *
 * <p>双行下拉（node + name），当前选中项品牌色 + 加粗高亮，让用户一眼看出正在用哪个。
 * 选中态触发器是单行 node（Spinner 的 getView），干净无多余图标。</p>
 */
public class CameraSpinnerAdapter extends BaseAdapter {

    /** 摄像头描述。node 必填，name 可选。 */
    public static final class Camera {
        public final String node;
        public final String name;

        public Camera(String node, String name) {
            this.node = node == null ? "" : node;
            this.name = name == null ? "" : name;
        }
    }

    private final Context ctx;
    private final java.util.List<Camera> items;
    /** Spinner 触发器（getView）的布局资源 id，不同页面可传不同外观。 */
    private final int selectedViewRes;
    private int selectedPosition = -1;

    public CameraSpinnerAdapter(Context ctx, java.util.List<Camera> items) {
        this(ctx, items, R.layout.item_spinner_camera_selected);
    }

    public CameraSpinnerAdapter(Context ctx, java.util.List<Camera> items, int selectedViewRes) {
        this.ctx = ctx;
        this.items = items;
        this.selectedViewRes = selectedViewRes;
    }

    public void setSelectedPosition(int pos) {
        if (selectedPosition != pos) {
            selectedPosition = pos;
            notifyDataSetChanged();
        }
    }

    @Override
    public int getCount() { return items.size(); }
    @Override
    public Object getItem(int position) { return items.get(position); }
    @Override
    public long getItemId(int position) { return position; }

    /** Spinner 关闭时显示的那行（触发器）：单行 node。 */
    @Override
    public View getView(int position, View convertView, ViewGroup parent) {
        ViewHolder vh;
        if (convertView == null) {
            convertView = LayoutInflater.from(ctx).inflate(selectedViewRes, parent, false);
            vh = new ViewHolder(convertView);
            convertView.setTag(vh);
        } else {
            vh = (ViewHolder) convertView.getTag();
        }
        vh.node.setText(items.get(position).node);
        return convertView;
    }

    /** 下拉弹出时的条目：双行 node + name，选中项品牌色 + 加粗。 */
    @Override
    public View getDropDownView(int position, View convertView, ViewGroup parent) {
        DropViewHolder vh;
        if (convertView == null) {
            convertView = LayoutInflater.from(ctx)
                    .inflate(R.layout.item_spinner_camera, parent, false);
            vh = new DropViewHolder(convertView);
            convertView.setTag(vh);
        } else {
            vh = (DropViewHolder) convertView.getTag();
        }
        Camera c = items.get(position);
        vh.node.setText(c.node);
        String sub = c.name;
        vh.name.setVisibility((sub == null || sub.isEmpty()) ? View.GONE : View.VISIBLE);
        vh.name.setText(sub);

        // 选中项高亮：品牌色 + 加粗；其它项必须显式复位（BaseAdapter 复用 convertView）
        if (position == selectedPosition) {
            vh.node.setTextColor(ContextCompat.getColor(ctx, R.color.brand_primary));
            vh.node.setTypeface(null, Typeface.BOLD);
        } else {
            vh.node.setTextColor(ContextCompat.getColor(ctx, R.color.text_primary));
            vh.node.setTypeface(null, Typeface.NORMAL);
        }
        return convertView;
    }

    private static final class ViewHolder {
        final TextView node;
        ViewHolder(View v) { node = v.findViewById(R.id.tvCameraNode); }
    }
    private static final class DropViewHolder {
        final TextView node;
        final TextView name;
        DropViewHolder(View v) {
            node = v.findViewById(R.id.tvCameraNode);
            name = v.findViewById(R.id.tvCameraName);
        }
    }
}
