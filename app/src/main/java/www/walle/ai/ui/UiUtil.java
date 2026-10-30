package www.walle.ai.ui;

import android.app.Activity;
import android.content.pm.ActivityInfo;
import android.graphics.Color;
import android.view.View;
import android.view.Window;

import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsCompat;
import androidx.core.view.WindowInsetsControllerCompat;

import org.json.JSONArray;
import org.json.JSONObject;

/** UI 展示用的格式化工具。 */
public final class UiUtil {
    private UiUtil() {}

    /**
     * 全屏（edge-to-edge）布局：内容铺到状态栏 / 导航栏下方，再用 insets 把「可点内容」推回安全区。
     *
     * <p>为什么不再用 {@code setDecorFitsSystemWindows(true)} + 给状态栏染色：
     * <ul>
     *   <li>API 35（Android 15）起，targetSdk >= 35 的应用被<b>强制</b> edge-to-edge；</li>
     *   <li>API 36（Android 16）起，连 {@code windowOptOutEdgeToEdgeEnforcement} 退路都没了；</li>
     *   <li>{@code setStatusBarColor} / {@code setNavigationBarColor} 在 35+ 变成空操作。</li>
     * </ul>
     * 所以旧写法在新系统上会「背景铺不上去 + 标题被状态栏压住」。这里统一改成：
     * 窗口透明化 → 绘制延伸到系统栏下 → 把 insets 作为 padding 加到 content 上，
     * 既保留全屏观感，又不会让顶部标题/底部导航被系统栏遮挡。</p>
     *
     * @param content 通常是 {@code findViewById(android.R.id.content)}；传 null 时自动取窗口根容器
     */
    public static void applyEdgeToEdge(Activity activity, View content) {
        if (activity == null) return;
        Window window = activity.getWindow();
        // 1) 允许内容绘制到系统栏区域（35+ 默认如此，这里对低版本也统一）
        WindowCompat.setDecorFitsSystemWindows(window, false);
        WindowInsetsControllerCompat controller =
                WindowCompat.getInsetsController(window, window.getDecorView());
        if (controller != null) {
            // 深色背景配浅色图标，与整体深空蓝主题一致
            controller.setAppearanceLightStatusBars(false);
            controller.setAppearanceLightNavigationBars(false);
        }

        View target = content;
        if (target == null) {
            View decor = window.getDecorView();
            target = decor instanceof View ? decor.findViewById(android.R.id.content) : null;
        }
        if (target == null) return;

        final View view = target;
        // 记录原始 padding，避免旋屏/重复调用时反复叠加
        final int baseLeft = view.getPaddingLeft();
        final int baseTop = view.getPaddingTop();
        final int baseRight = view.getPaddingRight();
        final int baseBottom = view.getPaddingBottom();
        ViewCompat.setOnApplyWindowInsetsListener(view, (v, insets) -> {
            Insets bars = insets.getInsets(
                    WindowInsetsCompat.Type.systemBars() | WindowInsetsCompat.Type.displayCutout());
            // edge-to-edge 后窗口不再因软键盘自动缩小（adjustResize 失效），
            // 这里把 IME 高度并进底部 padding，设置页的输入框才不会被键盘挡住。
            Insets ime = insets.getInsets(WindowInsetsCompat.Type.ime());
            int bottom = Math.max(bars.bottom, ime.bottom);
            v.setPadding(baseLeft + bars.left, baseTop + bars.top,
                    baseRight + bars.right, baseBottom + bottom);
            return WindowInsetsCompat.CONSUMED;
        });
        ViewCompat.requestApplyInsets(view);
    }

    /**
     * 全 App 竖屏锁定：手机横放时界面保持竖屏布局，不跟随重力感应旋转。
     * <p>Manifest 里每个 Activity 已声明 {@code screenOrientation="portrait"}；
     * 这里再在运行时锁一次，覆盖分屏 / 自由窗口 / 大屏设备等可能忽略 Manifest 属性的场景，
     * 保证 App 运行期间始终是竖屏。</p>
     */
    public static void lockPortrait(Activity activity) {
        if (activity == null) return;
        activity.setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_PORTRAIT);
    }

    /** 字节（1024 进制）转人类可读，与设备端 human_size 一致。 */
    public static String humanBytes(Double num) {
        if (num == null) return "—";
        double n = num;
        String[] units = {"B", "KB", "MB", "GB", "TB"};
        int u = 0;
        while (Math.abs(n) >= 1024 && u < units.length - 1) {
            n /= 1024.0;
            u++;
        }
        return String.format(java.util.Locale.US, "%.1f %s", n, units[u]);
    }

    public static String humanBytes(long num) { return humanBytes((double) num); }

    /** 秒转 “X天 Y小时 Z分”。 */
    public static String uptime(long sec) {
        if (sec <= 0) return "—";
        long d = sec / 86400;
        long h = (sec % 86400) / 3600;
        long m = (sec % 3600) / 60;
        StringBuilder sb = new StringBuilder();
        if (d > 0) sb.append(d).append("天 ");
        if (h > 0 || d > 0) sb.append(h).append("小时 ");
        sb.append(m).append("分");
        return sb.toString().trim();
    }

    public static String pct(Double v) {
        return v == null ? "—" : String.format(java.util.Locale.US, "%.1f%%", v);
    }

    /** 使用率颜色（深色底适配）：<60 绿，<85 橙，否则红。 */
    public static int usageColor(Double v) {
        if (v == null) return Color.GRAY;
        if (v >= 85) return Color.parseColor("#F87171");
        if (v >= 60) return Color.parseColor("#FBBF24");
        return Color.parseColor("#34D399");
    }

    public static int levelColor(String level, String event) {
        if ("recover".equals(event)) return Color.parseColor("#34D399");
        if (level == null) return Color.GRAY;
        switch (level) {
            case "critical": return Color.parseColor("#F87171");
            case "warning":  return Color.parseColor("#FBBF24");
            case "info":     return Color.parseColor("#38BDF8");
            default:         return Color.GRAY;
        }
    }

    public static String eventLabel(String event) {
        if (event == null) return "事件";
        switch (event) {
            case "alarm": return "告警";
            case "recover": return "恢复";
            case "usb_connect": return "USB 接入";
            case "usb_disconnect": return "USB 拔出";
            default: return event;
        }
    }

    public static String statusLabel(String status) {
        if (status == null) return "未知";
        switch (status) {
            case "success": return "成功";
            case "failed":  return "失败(退出码非0)";
            case "timeout": return "执行超时";
            case "denied":  return "被白名单拒绝";
            case "error":   return "错误";
            default: return status;
        }
    }

    public static int statusColor(String status) {
        if (status == null) return Color.GRAY;
        switch (status) {
            case "success": return Color.parseColor("#34D399");
            case "denied":  return Color.parseColor("#C084FC");
            default:        return Color.parseColor("#F87171");
        }
    }

    public static String joinJsonStrings(JSONArray arr) {
        if (arr == null || arr.length() == 0) return "";
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < arr.length(); i++) {
            if (i > 0) sb.append(", ");
            sb.append(arr.optString(i));
        }
        return sb.toString();
    }

    /** 从 JSONObject 安全取 double，键不存在或 null 返回 null。 */
    public static Double optDouble(JSONObject o, String key) {
        if (o == null || !o.has(key) || o.isNull(key)) return null;
        return o.optDouble(key, Double.NaN);
    }
}
