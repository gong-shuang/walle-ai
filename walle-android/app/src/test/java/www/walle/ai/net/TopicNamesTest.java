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
    private static final String BASE = "walle/home/device/666feb85d43410b2";

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
        assertEquals(BASE + "/baseState/query/request", TopicNames.baseStateQuery(TENANT, DEVICE));
        assertEquals(BASE + "/runState/query/request", TopicNames.capabilityQuery(TENANT, DEVICE));
        assertEquals(BASE + "/video/start/request", TopicNames.videoStartRequest(TENANT, DEVICE));
        assertEquals(BASE + "/video/stop/request", TopicNames.videoStopRequest(TENANT, DEVICE));
        assertEquals("walle/home/discovery/query/request", TopicNames.discoveryQuery(TENANT));
        assertEquals(BASE + "/car/motor/set/request", TopicNames.carMotorSetRequest(TENANT, DEVICE));
        assertEquals(BASE + "/car/servo/status/query/request",
                TopicNames.carServoStatusQuery(TENANT, DEVICE));
        assertEquals(BASE + "/car/servo/set/cmd", TopicNames.carServoSet(TENANT, DEVICE));
    }

    @Test
    public void clientOnlineTopic() {
        assertEquals("walle/home/client/web-abc12345/online",
                TopicNames.clientOnline(TENANT, "web-abc12345"));
    }

    @Test
    public void responseTopics() {
        assertEquals(BASE + "/online", TopicNames.online(TENANT, DEVICE));
        assertEquals(BASE + "/alarm/report", TopicNames.alarm(TENANT, DEVICE));
        assertEquals(BASE + "/discovery/query/response",
                TopicNames.discoveryResult(TENANT, DEVICE));
        assertEquals(BASE + "/baseState/query/response", TopicNames.baseStateResult(TENANT, DEVICE));
        assertEquals(BASE + "/runState/query/response",
                TopicNames.capabilityResult(TENANT, DEVICE));
        assertEquals(BASE + "/video/start/response",
                TopicNames.videoStartResult(TENANT, DEVICE));
        assertEquals(BASE + "/video/stop/response",
                TopicNames.videoStopResult(TENANT, DEVICE));
        assertEquals(BASE + "/car/motor/set/response",
                TopicNames.carMotorSetResult(TENANT, DEVICE));
        assertEquals(BASE + "/car/servo/status/query/response",
                TopicNames.carServoStatusResult(TENANT, DEVICE));
    }

    @Test
    public void namingConventionIsEnforced() {
        // 请求-应答对：请求 /request、应答 /response
        assertTrue(TopicNames.baseStateQuery(TENANT, DEVICE).endsWith("/request"));
        assertTrue(TopicNames.capabilityQuery(TENANT, DEVICE).endsWith("/request"));
        assertTrue(TopicNames.discoveryQuery(TENANT).endsWith("/request"));
        assertTrue(TopicNames.videoStartRequest(TENANT, DEVICE).endsWith("/request"));
        assertTrue(TopicNames.videoStopRequest(TENANT, DEVICE).endsWith("/request"));
        assertTrue(TopicNames.carMotorSetRequest(TENANT, DEVICE).endsWith("/request"));
        assertTrue(TopicNames.baseStateResult(TENANT, DEVICE).endsWith("/response"));
        assertTrue(TopicNames.capabilityResult(TENANT, DEVICE).endsWith("/response"));
        assertTrue(TopicNames.carMotorSetResult(TENANT, DEVICE).endsWith("/response"));

        // 持续设置且无应答：/cmd
        assertTrue(TopicNames.carServoSet(TENANT, DEVICE).endsWith("/cmd"));

        // 主动上报：online 单独；alarm 用 /report
        assertTrue(TopicNames.alarm(TENANT, DEVICE).endsWith("/report"));
        assertFalse(TopicNames.online(TENANT, DEVICE).endsWith("/report"));
    }

    @Test
    public void subscriptionsCoverAllResponsesAndAvoidWildcards() {
        String[] subs = TopicNames.responseSubscriptions(TENANT);
        assertEquals(11, subs.length);
        for (String s : subs) {
            assertTrue("不应订通配子树: " + s, !s.endsWith("/#"));
            assertTrue("订阅必须以 walle/home/device/+ 开头: " + s,
                    s.startsWith("walle/home/device/+"));
        }
        String joined = String.join(",", subs);
        for (String expected : new String[]{
                "/online", "/alarm/report", "/discovery/query/response",
                "/baseState/query/response", "/runState/query/response",
                "/video/start/response", "/video/stop/response",
                "/car/motor/set/response", "/car/servo/status/query/response",
                "/control/acquire/response", "/control/release/response"}) {
            assertTrue("缺少订阅: " + expected, joined.contains(expected));
        }
        // 请求类/持续控制主题不能被订阅，否则会收到自己发出的报文
        for (String s : subs) {
            assertFalse("不应订阅请求主题: " + s, s.endsWith("/request"));
            assertFalse("不应订阅持续控制主题: " + s, s.endsWith("/cmd"));
        }
    }

    @Test
    public void parseHandlesDeviceAndTenantLevelTopics() {
        String[] p = TopicNames.parse(BASE + "/baseState/query/response");
        assertEquals("home", p[0]);
        assertEquals(DEVICE, p[1]);
        assertEquals("baseState/query/response", p[2]);

        // 租户级发现主题没有 device 段，不应被当成设备主题解析
        assertNull(TopicNames.parse("walle/home/discovery/query/request"));
        // 旧版带 v1 版本段的主题已废弃，不兼容
        assertNull(TopicNames.parse("walle/home/666feb85d43410b2/v1/baseState/query"));
        assertNull(TopicNames.parse("device/online"));
        assertNull(TopicNames.parse(null));

        // 设备名恰好是 discovery 时，带资源的主题仍应正常解析
        String[] devDiscovery = TopicNames.parse("walle/home/device/discovery/baseState/query/request");
        assertEquals("discovery", devDiscovery[1]);
        assertEquals("baseState/query/request", devDiscovery[2]);

        assertEquals("baseState/query/response",
                TopicNames.resourceOf(BASE + "/baseState/query/response"));
        assertEquals(DEVICE, TopicNames.deviceOf(BASE + "/baseState/query/response"));
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
