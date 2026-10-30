package www.walle.ai.net;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.json.JSONObject;
import org.junit.Test;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** 设备清单 / 状态缓存 / 选中设备 的单元测试。 */
public class DeviceRegistryTest {

    private static JSONObject json(String raw) {
        try {
            return new JSONObject(raw);
        } catch (Exception e) {
            throw new IllegalArgumentException(e);
        }
    }

    @Test
    public void onlineAddsNewDeviceAndSelectsIt() {
        DeviceRegistry r = new DeviceRegistry();
        DeviceInfo added = r.applyOnline(json(
                "{\"uuid\":\"u1\",\"device_id\":\"d1\",\"hostname\":\"rpi\",\"online\":true}"));

        assertNotNull(added);
        assertEquals(1, r.count());
        assertEquals(1, r.onlineCount());
        assertEquals("u1", r.selectedKey());
        assertEquals("rpi", r.selected().displayName());
    }

    @Test
    public void offlineUnknownDeviceIsIgnored() {
        DeviceRegistry r = new DeviceRegistry();
        assertNull(r.applyOnline(json("{\"uuid\":\"u1\",\"online\":false}")));
        assertEquals(0, r.count());
    }

    @Test
    public void onlineMergesIntoExistingDevice() {
        DeviceRegistry r = new DeviceRegistry();
        r.applyOnline(json("{\"uuid\":\"u1\",\"hostname\":\"rpi\",\"online\":true}"));
        // 只带部分字段的后续报文不应把已有字段清空
        r.applyOnline(json("{\"uuid\":\"u1\",\"ip\":\"192.168.1.9\",\"online\":true}"));
        r.applyOnline(json("{\"uuid\":\"u1\",\"online\":false}"));

        assertEquals(1, r.count());
        DeviceInfo d = r.get("u1");
        assertEquals("rpi", d.displayName());
        assertEquals("192.168.1.9", d.ip);
        assertTrue(!d.online);
    }

    @Test
    public void discoveryResultReplacesStaleUuidOfSameDeviceId() {
        DeviceRegistry r = new DeviceRegistry();
        r.applyOnline(json("{\"uuid\":\"old\",\"device_id\":\"d1\",\"online\":true}"));
        DeviceInfo d = r.applyInfo(json(
                "{\"uuid\":\"new\",\"device_id\":\"d1\",\"hostname\":\"rpi\",\"model\":\"Pi 5\"}"));

        assertNotNull(d);
        assertEquals(1, r.count());          // 旧 uuid 条目已被清理
        assertNull(r.get("old"));
        assertNotNull(r.get("new"));
        assertTrue(r.get("new").detailLoaded);
        assertTrue(r.get("new").online);
    }

    @Test
    public void discoveryResultWithoutIdentityIsIgnored() {
        DeviceRegistry r = new DeviceRegistry();
        assertNull(r.applyInfo(json("{\"model\":\"Pi 5\"}")));
        assertEquals(0, r.count());
    }

    @Test
    public void selectionFallsBackWhenDeviceDisappears() {
        DeviceRegistry r = new DeviceRegistry();
        r.applyOnline(json("{\"uuid\":\"u1\",\"hostname\":\"a\",\"online\":true}"));
        r.applyOnline(json("{\"uuid\":\"u2\",\"hostname\":\"b\",\"online\":true}"));
        r.select("u2");
        assertEquals("u2", r.selectedKey());

        // 选中设备被 device_id 归并替换后，仍要有可用选中项
        r.applyInfo(json("{\"uuid\":\"u3\",\"device_id\":\"d2\",\"hostname\":\"b\"}"));
        assertTrue(!r.selectedKey().isEmpty());
    }

    @Test
    public void statusIsCachedPerDevice() {
        DeviceRegistry r = new DeviceRegistry();
        JSONObject s1 = json("{\"uuid\":\"u1\",\"cpu\":{}}");
        JSONObject s2 = json("{\"uuid\":\"u2\",\"cpu\":{}}");

        r.putStatus("u1", s1);
        r.putStatus("u2", s2);

        assertEquals(s1, r.status("u1"));
        assertEquals(s2, r.latestStatus());
        assertNull(r.status("nope"));
        assertNull(r.status(null));
    }

    @Test
    public void devicesAreSortedOnlineFirstThenByName() {
        DeviceRegistry r = new DeviceRegistry();
        // 未知设备的 offline 报文会被忽略（避免 retained 遗嘱变成幽灵卡片），
        // 所以先让 zeta 上线、再令其离线。
        r.applyOnline(json("{\"uuid\":\"u1\",\"hostname\":\"zeta\",\"online\":true}"));
        r.applyOnline(json("{\"uuid\":\"u1\",\"online\":false}"));
        r.applyOnline(json("{\"uuid\":\"u2\",\"hostname\":\"beta\",\"online\":true}"));
        r.applyOnline(json("{\"uuid\":\"u3\",\"hostname\":\"alpha\",\"online\":true}"));

        List<DeviceInfo> list = r.devices();
        assertEquals("alpha", list.get(0).displayName());
        assertEquals("beta", list.get(1).displayName());
        assertEquals("zeta", list.get(2).displayName());
    }

    // ---------------------------------------------------------------- 状态快照持久化（N11）

    /** 内存版状态缓存：模拟进程重启后从磁盘读回。 */
    private static final class FakeStatusCache implements DeviceRegistry.StatusCache {
        final Map<String, JSONObject> rows = new LinkedHashMap<>();
        int saves;

        @Override
        public void save(String key, JSONObject status) {
            saves++;
            rows.put(key, status);
        }

        @Override
        public Map<String, JSONObject> loadAll() {
            return new LinkedHashMap<>(rows);
        }
    }

    @Test
    public void attachStatusCacheRestoresSnapshotsBeforeAnyReport() {
        FakeStatusCache cache = new FakeStatusCache();
        cache.rows.put("u1", json("{\"ts\":100,\"cpu\":{\"percent\":12}}"));
        cache.rows.put("u2", json("{\"ts\":200,\"cpu\":{\"percent\":35}}"));

        DeviceRegistry r = new DeviceRegistry();
        r.attachStatusCache(cache);

        assertNotNull("重启后应立刻有缓存状态可渲染", r.status("u1"));
        assertEquals(12, r.status("u1").getJSONObject("cpu").optInt("percent"));
        assertNotNull(r.latestStatus());
        assertEquals(200, r.latestStatus().optLong("ts"));
    }

    @Test
    public void putStatusPersistsThroughCache() {
        FakeStatusCache cache = new FakeStatusCache();
        DeviceRegistry r = new DeviceRegistry();
        r.attachStatusCache(cache);

        r.putStatus("u1", json("{\"ts\":300}"));

        assertEquals(1, cache.saves);
        assertEquals(300, cache.rows.get("u1").optLong("ts"));
    }

    @Test
    public void liveStatusWinsOverCachedOne() {
        FakeStatusCache cache = new FakeStatusCache();
        cache.rows.put("u1", json("{\"ts\":100,\"cpu\":{\"percent\":12}}"));
        DeviceRegistry r = new DeviceRegistry();
        r.putStatus("u1", json("{\"ts\":400,\"cpu\":{\"percent\":80}}"));
        r.attachStatusCache(cache);

        assertEquals(80, r.status("u1").getJSONObject("cpu").optInt("percent"));
    }

    @Test
    public void brokenCacheDoesNotBreakRegistry() {
        DeviceRegistry r = new DeviceRegistry();
        r.attachStatusCache(new DeviceRegistry.StatusCache() {
            @Override public void save(String key, JSONObject status) { throw new IllegalStateException("磁盘满"); }
            @Override public Map<String, JSONObject> loadAll() { throw new IllegalStateException("库损坏"); }
        });

        r.putStatus("u1", json("{\"ts\":1}"));

        assertNotNull("落盘失败不能影响内存状态", r.status("u1"));
    }
}
