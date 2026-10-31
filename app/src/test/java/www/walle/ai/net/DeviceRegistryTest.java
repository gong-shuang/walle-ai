package www.walle.ai.net;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.json.JSONObject;
import org.junit.Test;

import java.util.List;

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
    public void offlineUnknownDeviceIsKept() {
        // 离线设备也要显示（灰点/红点），所以首次看到的离线报文同样入列
        DeviceRegistry r = new DeviceRegistry();
        DeviceInfo added = r.applyOnline(json("{\"uuid\":\"u1\",\"online\":false}"));
        assertNotNull(added);
        assertEquals(1, r.count());
        assertEquals(0, r.onlineCount());
        assertTrue(!added.online);
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
}
