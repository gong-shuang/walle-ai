package www.walle.ai.ui;

import android.content.Context;
import android.content.SharedPreferences;

/**
 * 「首次启动引导页是否已看过」的本地标记。
 * <p>应用第一次启动时展示 3 页全屏导览；用户看完 / 跳过 / 从第三次左滑进入主页面后标记为已看过，
 * 之后启动直接进入主页面。设置页的「功能导览」可以随时重新打开，不影响该标记。</p>
 */
final class OnboardingPrefs {

    private static final String PREFS = "onboarding";
    private static final String KEY_DONE = "done_v1";

    private OnboardingPrefs() {}

    static boolean isDone(Context context) {
        return prefs(context).getBoolean(KEY_DONE, false);
    }

    static void markDone(Context context) {
        prefs(context).edit().putBoolean(KEY_DONE, true).apply();
    }

    private static SharedPreferences prefs(Context context) {
        return context.getApplicationContext()
                .getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }
}
