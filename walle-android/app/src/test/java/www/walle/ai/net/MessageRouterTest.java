package www.walle.ai.net;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.json.JSONObject;
import org.junit.Test;

import java.util.ArrayList;
import java.util.List;

/** 主题路由与 QoS1 去重的单元测试（纯 JVM，不依赖 Android）。 */
public class MessageRouterTest {

    private static final String BASE = "walle/home/666feb85d43410b2/v1";

    /** 记录 Sink 收到的回调。 */
    private static final class RecordingSink implements MessageRouter.Sink {
        final List<String> events = new ArrayList<>();
        final List<String> ignored = new ArrayList<>();
        JSONObject lastPayload;

        private void record(String kind, JSONObject payload) {
            events.add(kind);
            lastPayload = payload;
        }

        @Override public void onOnline(JSONObject p) { record("online", p); }
        @Override public void onDiscoveryResult(JSONObject p) { record("discovery", p); }
        @Override public void onStateResult(JSONObject p) { record("state", p); }
        @Override public void onAlarm(JSONObject p) { record("alarm", p); }
        @Override public void onCapabilityResult(JSONObject p) { record("capability", p); }
        @Override public void onVideoStartResult(JSONObject p) { record("video-start", p); }
        @Override public void onVideoStopResult(JSONObject p) { record("video-stop", p); }
        @Override public void onIgnored(String topic, String reason) { ignored.add(topic); }
    }

    @Test
    public void routesEachResourceToItsCallback() {
        RecordingSink sink = new RecordingSink();
        MessageRouter router = new MessageRouter(sink);

        router.route(BASE + "/online", "{\"online\":true}");
        router.route(BASE + "/discovery/result", "{\"model\":\"Pi 5\"}");
        router.route(BASE + "/state/result", "{\"cpu\":{}}");
        router.route(BASE + "/alarm", "{\"event\":\"alarm\"}");
        router.route(BASE + "/capability/result", "{\"cameras\":[]}");
        router.route(BASE + "/video/start/result", "{\"result\":0}");
        router.route(BASE + "/video/stop/result", "{\"result\":0}");

        assertEquals(List.of("online", "discovery", "state", "alarm",
                "capability", "video-start", "video-stop"), sink.events);
    }

    @Test
    public void injectsDeviceIdFromTopicWhenMissing() {
        RecordingSink sink = new RecordingSink();
        MessageRouter router = new MessageRouter(sink);

        router.route(BASE + "/state/result", "{\"cpu\":{}}");

        assertEquals("666feb85d43410b2", sink.lastPayload.optString("device_id"));
    }

    @Test
    public void dedupesOneShotEventsWithinWindow() {
        RecordingSink sink = new RecordingSink();
        MessageRouter router = new MessageRouter(sink);

        // 告警 / 视频结果：2 秒内重复投递只处理一次
        router.route(BASE + "/alarm", "{\"event\":\"alarm\",\"timestamp\":1}");
        router.route(BASE + "/alarm", "{\"event\":\"alarm\",\"timestamp\":1}");
        router.route(BASE + "/video/start/result", "{\"request_id\":\"v1\"}");
        router.route(BASE + "/video/start/result", "{\"request_id\":\"v1\"}");

        assertEquals(List.of("alarm", "video-start"), sink.events);
    }

    @Test
    public void doesNotDedupeIdempotentSnapshots() {
        RecordingSink sink = new RecordingSink();
        MessageRouter router = new MessageRouter(sink);

        router.route(BASE + "/state/result", "{\"datetime\":\"2026-10-30 12:00:00\"}");
        router.route(BASE + "/state/result", "{\"datetime\":\"2026-10-30 12:00:00\"}");

        assertEquals(List.of("state", "state"), sink.events);
    }

    @Test
    public void differentPayloadIsNotDeduped() {
        RecordingSink sink = new RecordingSink();
        MessageRouter router = new MessageRouter(sink);

        router.route(BASE + "/alarm", "{\"event\":\"alarm\",\"timestamp\":1}");
        router.route(BASE + "/alarm", "{\"event\":\"alarm\",\"timestamp\":2}");

        assertEquals(List.of("alarm", "alarm"), sink.events);
    }

    @Test
    public void ignoresUnknownResourcesAndMalformedTopics() {
        RecordingSink sink = new RecordingSink();
        MessageRouter router = new MessageRouter(sink);

        router.route(BASE + "/unknown/resource", "{}");        // 未知资源
        router.route(BASE + "/cmd/result", "{}");              // 已下线的远程命令应答
        router.route("walle/home/v1/discovery/query", "{}");    // 租户级发现主题（没有 device 段）
        router.route("device/online", "{}");                   // 旧协议主题
        router.route(BASE + "/state/result", "{not json");     // 非法 JSON

        assertEquals(5, sink.ignored.size());
        assertTrue(sink.events.isEmpty());
    }

    @Test
    public void emptyPayloadIsTreatedAsEmptyObject() {
        RecordingSink sink = new RecordingSink();
        MessageRouter router = new MessageRouter(sink);

        router.route(BASE + "/online", "");

        assertEquals(List.of("online"), sink.events);
        assertEquals("666feb85d43410b2", sink.lastPayload.optString("device_id"));
    }
}
