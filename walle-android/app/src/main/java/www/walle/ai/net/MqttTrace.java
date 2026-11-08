package www.walle.ai.net;

import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import www.walle.ai.BuildConfig;

/**
 * MQTT 收发报文日志。
 * <p>Debug 构建下把 JSON 格式化（缩进 2 空格）后分段打印，避免 logcat 单条 4000 字符上限截断：
 * {@code adb logcat -s MqttTrace}。Release 构建不输出。</p>
 */
final class MqttTrace {

    private static final String TAG = "MqttTrace";
    private static final int LOGCAT_CHUNK = 3500;

    private MqttTrace() {
    }

    static void log(String direction, String topic, String payload) {
        if (!BuildConfig.DEBUG) return;
        String raw = payload == null ? "" : payload;
        String pretty;
        try {
            String trimmed = raw.trim();
            if (trimmed.startsWith("{")) {
                pretty = new JSONObject(trimmed).toString(2);
            } else if (trimmed.startsWith("[")) {
                pretty = new JSONArray(trimmed).toString(2);
            } else {
                pretty = trimmed;
            }
        } catch (Exception e) {
            // 非法 JSON：原样输出，便于排查设备端格式问题
            pretty = raw;
        }
        Log.i(TAG, direction + " " + topic + " (" + raw.length() + " chars)");
        if (pretty.isEmpty()) {
            Log.i(TAG, "<empty payload>");
            return;
        }
        for (int start = 0; start < pretty.length(); start += LOGCAT_CHUNK) {
            Log.i(TAG, pretty.substring(start, Math.min(pretty.length(), start + LOGCAT_CHUNK)));
        }
    }
}
