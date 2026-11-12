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
    public void driftedDeviceIdMergesByHostnameInsteadOfCreatingGhostCard() {
        // 真实 bug：同一台机器两次启动算出两个 device_id（MemTotal 浮动几 KiB 进了哈希），
        // 旧主题上的 retained online:false 会和新主题的 online:true 一起回放。
        // 身份报文已不含 MAC，按 hostname 归并；更旧的 offline 必须被丢弃。
        DeviceRegistry r = new DeviceRegistry();
        r.applyOnline(json("{\"uuid\":\"new-uuid\",\"device_id\":\"d-new\","
                + "\"hostname\":\"aigo\",\"online\":true,\"timestamp\":200}"));
        r.applyOnline(json("{\"uuid\":\"old-uuid\",\"device_id\":\"d-old\","
                + "\"hostname\":\"aigo\",\"online\":false,\"timestamp\":100}"));

        assertEquals(1, r.count());
        DeviceInfo d = r.selected();
        assertNotNull(d);
        assertTrue(d.online);                       // 旧 offline 被丢弃，仍是在线
        assertEquals("d-new", d.deviceId);          // 卡片跟着新的稳定标识走
        assertEquals("new-uuid", d.key());
    }

    @Test
    public void staleOfflineArrivingFirstIsMergedAwayByLiveOnline() {
        // 回放顺序反过来（旧 offline 先到）：先建卡片，随后被在线报文归并，不能多出一台
        DeviceRegistry r = new DeviceRegistry();
        r.applyOnline(json("{\"uuid\":\"old-uuid\",\"device_id\":\"d-old\","
                + "\"hostname\":\"aigo\",\"online\":false,\"timestamp\":100}"));
        assertEquals(1, r.count());
        assertEquals(0, r.onlineCount());

        r.applyOnline(json("{\"uuid\":\"new-uuid\",\"device_id\":\"d-new\","
                + "\"hostname\":\"aigo\",\"online\":true,\"timestamp\":200}"));

        assertEquals(1, r.count());
        assertEquals(1, r.onlineCount());
        assertEquals("new-uuid", r.selectedKey());  // 列表键已迁移到新 uuid
        assertEquals("d-new", r.selected().deviceId);
    }

    @Test
    public void offlineOfSameDeviceIdStillApplies() {
        // 同一次开机的遗嘱（LWT）用的就是当前 device_id，时间戳与 online 相同，必须生效
        DeviceRegistry r = new DeviceRegistry();
        r.applyOnline(json("{\"uuid\":\"u1\",\"device_id\":\"d1\","
                + "\"online\":true,\"timestamp\":200}"));
        r.applyOnline(json("{\"uuid\":\"u1\",\"device_id\":\"d1\","
                + "\"online\":false,\"timestamp\":200}"));

        assertEquals(1, r.count());
        assertTrue(!r.selected().online);
    }

    @Test
    public void devicesWithDifferentHostnamesStaySeparate() {
        DeviceRegistry r = new DeviceRegistry();
        r.applyOnline(json("{\"uuid\":\"u1\",\"device_id\":\"d1\",\"hostname\":\"walle-a\",\"online\":true}"));
        r.applyOnline(json("{\"uuid\":\"u2\",\"device_id\":\"d2\",\"hostname\":\"walle-b\",\"online\":true}"));
        assertEquals(2, r.count());
        assertEquals(2, r.onlineCount());
    }

    @Test
    public void discoveryResultReplacesStaleEntryOfSameHostnameWhenDeviceIdDrifted() {
        DeviceRegistry r = new DeviceRegistry();
        r.applyOnline(json("{\"uuid\":\"old\",\"device_id\":\"d-old\",\"hostname\":\"aigo\",\"online\":false}"));
        DeviceInfo d = r.applyInfo(json("{\"uuid\":\"new\",\"device_id\":\"d-new\","
                + "\"hostname\":\"aigo\"}"));

        assertNotNull(d);
        assertEquals(1, r.count());
        assertNull(r.get("old"));
        assertNotNull(r.get("new"));
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
        // 未知设备的离线报文同样会入列（见 offlineUnknownDeviceIsKept），
        // 这里先让 zeta 上线再离线，验证「在线优先、其次按名称」的排序
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
