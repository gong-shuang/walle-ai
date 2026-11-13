package www.walle.ai.net;

import android.util.Log;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.TreeMap;

/**
 * 把收到的报文按主题路由成领域事件，并过滤 QoS1 的重复投递。
 *
 * <p>主题形如 {@code walle/{tenant}/{device}/v1/{resource}}，resource 决定事件类型；
 * 不认识的资源、非本协议主题都会被忽略（只打日志）。</p>
 *
 * <p>去重：MQTT QoS1 允许 broker 重复投递同一条报文（重连补发、未及时 PUBACK），
 * 而「视频结果 / 告警 / 能力查询」都是一次性事件，重复处理会直接出问题
 * （例如推流结果重复 → 播放器被初始化两次）。这里用「键排序后的 JSON 签名 + 2 秒窗口」去重。
 * 状态快照与在线报文是幂等的，不去重。</p>
 */
public class MessageRouter {

    private static final String TAG = "MessageRouter";
    private static final long DEDUPE_WINDOW_MS = 2000L;
    private static final int DEDUPE_CACHE_SIZE = 128;

    /** 路由结果：每种资源一个回调，全部在主线程执行。 */
    public interface Sink {
        void onOnline(JSONObject payload);

        void onDiscoveryResult(JSONObject payload);

        void onStateResult(JSONObject payload);

        void onAlarm(JSONObject payload);

        void onCapabilityResult(JSONObject payload);

        void onVideoStartResult(JSONObject payload);

        void onVideoStopResult(JSONObject payload);

        /** car/motor/result：车控持续命令回执。 */
        void onCarMotorResult(JSONObject payload);

        /** 无法识别的主题/资源（默认只打日志）。 */
        default void onIgnored(String topic, String reason) {
        }
    }

    private final Sink sink;

    /** 签名 → 上次处理时间（毫秒，单调时钟）。 */
    private final Map<String, Long> recent = new LinkedHashMap<String, Long>(16, 0.75f, false) {
        @Override
        protected boolean removeEldestEntry(Map.Entry<String, Long> eldest) {
            return size() > DEDUPE_CACHE_SIZE;
        }
    };

    public MessageRouter(Sink sink) {
        this.sink = sink;
    }

    /** 路由一条报文。payload 为 UTF-8 解码后的原文。 */
    public void route(String topic, String payload) {
        MqttTrace.log("← RECV", topic, payload);

        if (!TopicNames.isNamespaced(topic)) {
            sink.onIgnored(topic, "非本协议主题");
            return;
        }
        String[] parts = TopicNames.parse(topic);
        if (parts == null) {
            // 租户级发现主题 walle/{tenant}/v1/discovery/query 没有 device 段，不是报文入口
            sink.onIgnored(topic, "主题层级不足");
            return;
        }
        String device = parts[1];
        String resource = parts[2];

        JSONObject json;
        try {
            json = payload == null || payload.trim().isEmpty() ? new JSONObject() : new JSONObject(payload);
        } catch (JSONException e) {
            Log.w(TAG, "非 JSON 报文 on " + topic + ": " + payload);
            sink.onIgnored(topic, "非法 JSON");
            return;
        }
        // 设备端可能省略 device_id，用主题里的设备段兜底，保证归并不会因为缺字段而退化
        try {
            if (json.optString("device_id", "").isEmpty()) {
                json.put("device_id", device);
            }
        } catch (JSONException ignore) {
        }

        switch (resource) {
            // 主动上报
            case "online":
                sink.onOnline(json);
                break;
            case "alarm":
                if (!isDuplicate("alarm", json)) sink.onAlarm(json);
                break;
            // 查询应答（.../result）
            case "discovery/result":
                sink.onDiscoveryResult(json);
                break;
            case "state/result":
                sink.onStateResult(json);
                break;
            case "capability/result":
                if (!isDuplicate("capability", json)) sink.onCapabilityResult(json);
                break;
            case "video/start/result":
                if (!isDuplicate("video/start", json)) sink.onVideoStartResult(json);
                break;
            case "video/stop/result":
                if (!isDuplicate("video/stop", json)) sink.onVideoStopResult(json);
                break;
            case "car/motor/result":
                if (!isDuplicate("car/motor", json)) sink.onCarMotorResult(json);
                break;
            default:
                sink.onIgnored(topic, "未知资源 " + resource);
                break;
        }
    }

    /** 事件签名：把 JSON 的键排序后拼接，保证同一份报文得到同一个签名。 */
    private static String signature(String kind, JSONObject json) {
        if (json == null) return kind;
        TreeMap<String, String> normalized = new TreeMap<>();
        for (Iterator<String> it = json.keys(); it.hasNext(); ) {
            String key = it.next();
            normalized.put(key, String.valueOf(json.opt(key)));
        }
        return kind + "|" + normalized;
    }

    /** 返回 true 表示 2 秒内已处理过同一条事件，应当丢弃。 */
    private synchronized boolean isDuplicate(String kind, JSONObject json) {
        long now = System.nanoTime() / 1_000_000L;
        String sig = signature(kind, json);
        Long last = recent.get(sig);
        if (last != null && now - last < DEDUPE_WINDOW_MS) {
            return true;
        }
        recent.put(sig, now);
        // 顺手清理过期项，避免长跑后占满
        if (recent.size() > DEDUPE_CACHE_SIZE / 2) {
            Iterator<Map.Entry<String, Long>> it = recent.entrySet().iterator();
            while (it.hasNext()) {
                if (now - it.next().getValue() > DEDUPE_WINDOW_MS * 5) it.remove();
            }
        }
        return false;
    }
}
