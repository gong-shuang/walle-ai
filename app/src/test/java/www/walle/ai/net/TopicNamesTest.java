package www.walle.ai.net;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

/**
 * 主题命名契约的单元测试。
 *
 * <p>这些断言与设备端 {@code device/selftest_topics.py} 的清洗用例保持一致：
 * 任何一端改了命名规则，两边都必须同步改，否则会在运行时「各说各话」——表现为设备收不到请求。</p>
 */
public class TopicNamesTest {

    private static final String TENANT = "home";
    private static final String DEVICE = "666feb85d43410b2";
    private static final String BASE = "walle/home/666feb85d43410b2/v1";

    @Test
    public void sanitizeKeepsAllowedChars() {
        assertEquals("home", TopicNames.sanitize("home"));
        assertEquals("RaspPi5", TopicNames.sanitize("RaspPi5"));
        assertEquals("a_b_c_d_e", TopicNames.sanitize("a.b:c@d e"));
        assertEquals("RPi_5", TopicNames.sanitize("RPi 5"));
        assertEquals("user_example_com", TopicNames.sanitize("user@example.com"));
    }

    @Test
    public void sanitizeDropsWildcardsAndSeparators() {
        // 通配符/分隔符必须被丢弃，否则可以被用来订阅别人的子树
        assertEquals("xyz", TopicNames.sanitize("x/y+z#"));
        assertEquals("unknown", TopicNames.sanitize(""));
        assertEquals("unknown", TopicNames.sanitize(null));
        assertEquals("unknown", TopicNames.sanitize("中文租户"));
    }

    @Test
    public void tenantOfFallsBackToDefault() {
        assertEquals("home", TopicNames.tenantOf("home"));
        assertEquals("default", TopicNames.tenantOf(""));
        assertEquals("default", TopicNames.tenantOf(null));
    }

    @Test
    public void requestTopics() {
        assertEquals(BASE + "/state/query", TopicNames.stateQuery(TENANT, DEVICE));
        assertEquals(BASE + "/capability/query", TopicNames.capabilityQuery(TENANT, DEVICE));
        assertEquals(BASE + "/cmd/exec", TopicNames.cmdExec(TENANT, DEVICE));
        assertEquals(BASE + "/video/start/request", TopicNames.videoStartRequest(TENANT, DEVICE));
        assertEquals(BASE + "/video/stop/request", TopicNames.videoStopRequest(TENANT, DEVICE));
        assertEquals("walle/home/v1/discovery/query", TopicNames.discoveryQuery(TENANT));
    }

    @Test
    public void responseTopics() {
        assertEquals(BASE + "/online", TopicNames.online(TENANT, DEVICE));
        assertEquals(BASE + "/alarm", TopicNames.alarm(TENANT, DEVICE));
        assertEquals(BASE + "/discovery/result", TopicNames.discoveryResult(TENANT, DEVICE));
        assertEquals(BASE + "/state/result", TopicNames.stateResult(TENANT, DEVICE));
        assertEquals(BASE + "/cmd/result", TopicNames.cmdResult(TENANT, DEVICE));
        assertEquals(BASE + "/capability/result",
                TopicNames.capabilityResult(TENANT, DEVICE));
        assertEquals(BASE + "/video/start/result", TopicNames.videoStartResult(TENANT, DEVICE));
        assertEquals(BASE + "/video/stop/result", TopicNames.videoStopResult(TENANT, DEVICE));
    }

    @Test
    public void namingConventionIsEnforced() {
        // 请求：查询 /query、命令 /exec、动作 /request
        assertTrue(TopicNames.stateQuery(TENANT, DEVICE).endsWith("/query"));
        assertTrue(TopicNames.capabilityQuery(TENANT, DEVICE).endsWith("/query"));
        assertTrue(TopicNames.discoveryQuery(TENANT).endsWith("/query"));
        assertTrue(TopicNames.cmdExec(TENANT, DEVICE).endsWith("/exec"));
        assertTrue(TopicNames.videoStartRequest(TENANT, DEVICE).endsWith("/request"));
        assertTrue(TopicNames.videoStopRequest(TENANT, DEVICE).endsWith("/request"));

        // 应答：一律 /result
        assertTrue(TopicNames.stateResult(TENANT, DEVICE).endsWith("/result"));
        assertTrue(TopicNames.cmdResult(TENANT, DEVICE).endsWith("/result"));
        assertTrue(TopicNames.capabilityResult(TENANT, DEVICE).endsWith("/result"));

        // 主动上报：不以 /result 结尾
        assertFalse(TopicNames.online(TENANT, DEVICE).endsWith("/result"));
        assertFalse(TopicNames.alarm(TENANT, DEVICE).endsWith("/result"));
    }

    @Test
    public void subscriptionsCoverAllResponsesAndAvoidWildcards() {
        String[] subs = TopicNames.responseSubscriptions(TENANT);
        assertEquals(8, subs.length);
        for (String s : subs) {
            assertTrue("不应订通配子树: " + s, !s.endsWith("/#"));
            assertTrue("订阅必须以 walle/home/+/v1 开头: " + s, s.startsWith("walle/home/+/v1"));
        }
        String joined = String.join(",", subs);
        for (String expected : new String[]{
                "/online", "/alarm", "/discovery/result", "/state/result",
                "/cmd/result", "/capability/result",
                "/video/start/result", "/video/stop/result"}) {
            assertTrue("缺少订阅: " + expected, joined.contains(expected));
        }
        // 请求类主题不能被订阅，否则会收到自己发出的报文
        assertFalse(joined.contains("/query"));
        assertFalse(joined.contains("/cmd/exec"));
    }

    @Test
    public void parseHandlesDeviceAndTenantLevelTopics() {
        String[] p = TopicNames.parse(BASE + "/state/result");
        assertEquals("home", p[0]);
        assertEquals(DEVICE, p[1]);
        assertEquals("state/result", p[2]);

        // 租户级发现主题没有 device 段，不应被当成设备主题解析
        assertNull(TopicNames.parse("walle/home/v1/discovery/query"));
        assertNull(TopicNames.parse("device/online"));
        assertNull(TopicNames.parse(null));

        // 设备名恰好是 discovery 时，带资源的主题仍应正常解析
        String[] devDiscovery = TopicNames.parse("walle/home/discovery/v1/state/query");
        assertEquals("discovery", devDiscovery[1]);
        assertEquals("state/query", devDiscovery[2]);

        assertEquals("state/result", TopicNames.resourceOf(BASE + "/state/result"));
        assertEquals(DEVICE, TopicNames.deviceOf(BASE + "/state/result"));
    }

    @Test
    public void namespacedDetection() {
        assertTrue(TopicNames.isNamespaced(BASE + "/online"));
        assertFalse(TopicNames.isNamespaced("device/online"));
        assertFalse(TopicNames.isNamespaced(null));
    }

    @Test
    public void sanitizeKeepsSegmentShortAndStable() {
        String longTenant = "a".repeat(100);
        String seg = TopicNames.sanitize(longTenant);
        assertEquals(seg, TopicNames.sanitize(longTenant));   // 同一输入必须稳定
        assertTrue(seg.length() <= 64);
        assertTrue(seg.startsWith("a".repeat(40) + "-"));
    }
}
