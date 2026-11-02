package www.walle.ai.model;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.json.JSONObject;
import org.junit.Test;

/** 状态快照解析的单元测试：协议字段 → 强类型模型，只在这一层做 optXxx。 */
public class StatusSnapshotTest {

    private static final String FULL = "{"
            + "\"uuid\":\"u1\",\"device_id\":\"d1\","
            + "\"datetime\":\"2026-10-30 12:00:00\",\"uptime_seconds\":12345,"
            + "\"hostname\":\"RaspberryPi\",\"ip\":\"192.168.1.5\","
            + "\"system\":{\"model\":\"Pi 5\",\"os\":\"Debian 13\",\"machine\":\"aarch64\",\"kernel\":\"6.18\"},"
            + "\"cpu\":{\"percent\":42.5,\"temperature_c\":47.4,"
            + "  \"load_avg_1m\":0.5,\"load_avg_5m\":0.4,\"load_avg_15m\":0.3,"
            + "  \"frequency_mhz\":{\"current\":1500,\"min\":1500,\"max\":2400},"
            + "  \"count_logical\":4,\"count_physical\":4,"
            + "  \"per_core_percent\":[10,20.5,30,40]},"
            + "\"memory\":{\"percent\":31.2,\"used_human\":\"2.5 GB\",\"total_human\":\"7.9 GB\","
            + "  \"available_human\":\"5.4 GB\",\"swap\":{\"used_human\":\"0 B\",\"total_human\":\"1 GB\",\"percent\":0}},"
            + "\"disk\":{\"mounts\":[{\"mount\":\"/\",\"fstype\":\"ext4\",\"used_human\":\"40 GB\","
            + "  \"total_human\":\"263 GB\",\"percent\":15.2,\"device\":\"/dev/mmcblk0p2\"}]},"
            + "\"network\":{\"total_rx_rate\":\"1.2 MB/s\",\"total_tx_rate\":\"300 KB/s\","
            + "  \"interfaces\":{\"wlan0\":{\"rx_rate\":\"1 MB/s\",\"tx_rate\":\"200 KB/s\"},"
            + "                   \"eth0\":{\"rx_rate\":\"200 KB/s\",\"tx_rate\":\"100 KB/s\"}}},"
            + "\"usb\":{\"count\":1,\"devices\":[{\"product\":\"USB Camera\",\"manufacturer\":\"ACME\","
            + "  \"vendor_id\":\"1d6b\",\"product_id\":\"0002\",\"block_nodes\":[\"sda1\"]}]},"
            + "\"alarms\":{\"active\":[\"CPU 温度过高\"]}"
            + "}";

    @Test
    public void parsesFullPayload() {
        StatusSnapshot s = StatusSnapshot.parse(json(FULL));
        assertNotNull(s);

        assertEquals("u1", s.uuid);
        assertEquals("2026-10-30 12:00:00", s.datetime);
        assertEquals(12345L, s.uptimeSeconds);
        assertEquals("RaspberryPi", s.hostname);
        assertEquals("192.168.1.5", s.ip);

        assertEquals("Pi 5", s.system.model);
        assertEquals("aarch64", s.system.machine);

        assertEquals(42.5, s.cpu.percent, 0.001);
        assertEquals(47.4, s.cpu.tempC, 0.001);
        assertEquals("0.5", s.cpu.load1);
        assertEquals("1500", s.cpu.freqCurrent);
        assertEquals(4, s.cpu.coresLogical);
        assertArrayEquals(new double[]{10, 20.5, 30, 40}, s.cpu.perCore, 0.001);

        assertEquals(31.2, s.memory.percent, 0.001);
        assertEquals("7.9 GB", s.memory.totalHuman);
        assertEquals("1 GB", s.memory.swapTotalHuman);
        assertEquals(0.0, s.memory.swapPercent, 0.001);

        assertEquals(1, s.disks.size());
        assertEquals("/", s.disks.get(0).mount);
        assertEquals("/dev/mmcblk0p2", s.disks.get(0).device);

        assertEquals("1.2 MB/s", s.network.totalRxRate);
        assertEquals(2, s.network.interfaces.size());
        // 网卡按名称排序，保证渲染稳定
        assertEquals("eth0", s.network.interfaces.get(0).name);
        assertEquals("wlan0", s.network.interfaces.get(1).name);

        assertEquals(1, s.usb.count);
        assertEquals("USB Camera", s.usb.devices.get(0).title);
        assertEquals("ACME", s.usb.devices.get(0).manufacturer);
        assertEquals("sda1", s.usb.devices.get(0).blockNodes.get(0));

        assertEquals(1, s.activeAlarms.size());
        assertEquals("CPU 温度过高", s.activeAlarms.get(0));
    }

    @Test
    public void missingSectionsBecomeNullOrDefaults() {
        StatusSnapshot s = StatusSnapshot.parse(json("{\"datetime\":\"t\"}"));
        assertNotNull(s);
        assertNull(s.system);
        assertNull(s.cpu);
        assertNull(s.memory);
        assertNull(s.network);
        assertNull(s.usb);
        assertTrue(s.disks.isEmpty());
        assertTrue(s.activeAlarms.isEmpty());
        assertEquals("", s.hostname);
        assertEquals(0L, s.uptimeSeconds);
    }

    @Test
    public void nullPayloadYieldsNull() {
        assertNull(StatusSnapshot.parse(null));
    }

    @Test
    public void weirdNumbersAreNormalizedToNullNotNaN() {
        StatusSnapshot s = StatusSnapshot.parse(json(
                "{\"cpu\":{\"percent\":null,\"temperature_c\":\"abc\",\"load_avg_1m\":0},"
                + "\"memory\":{\"percent\":\"12.5\"}}"));
        assertNull(s.cpu.percent);          // JSON null
        assertNull(s.cpu.tempC);            // 非数字
        assertEquals("0", s.cpu.load1);
        assertEquals(12.5, s.memory.percent, 0.001);
        assertEquals(0, s.cpu.perCore.length);
    }

    @Test
    public void usbDeviceWithoutProductFallsBackToSysfs() {
        StatusSnapshot s = StatusSnapshot.parse(json(
                "{\"usb\":{\"count\":1,\"devices\":[{\"sysfs\":\"1-1.2\",\"vendor_id\":\"1d6b\",\"product_id\":\"0002\"}]}}"));
        assertEquals("1-1.2", s.usb.devices.get(0).title);
        assertTrue(s.usb.devices.get(0).manufacturer.isEmpty());
        assertTrue(s.usb.devices.get(0).blockNodes.isEmpty());
    }

    @Test
    public void parsesStreamingVideoGroup() {
        StatusSnapshot s = StatusSnapshot.parse(json(
                "{\"video\":{\"streaming\":true,\"room_number\":\"545754\","
                + "\"video_device\":\"/dev/video0\",\"pid\":12345,"
                + "\"started_at\":\"2026-10-30 18:47:42\",\"uptime_sec\":128.5,"
                + "\"audio\":{\"available\":true,\"device\":\"default\"}}}"));
        assertNotNull(s.video);
        assertTrue(s.video.streaming);
        assertTrue(s.video.takeoverReady());
        assertEquals("545754", s.video.roomNumber);
        assertEquals("/dev/video0", s.video.videoDevice);
        assertEquals(12345, s.video.pid);
        assertEquals("2026-10-30 18:47:42", s.video.startedAt);
        assertEquals(128.5, s.video.uptimeSec, 0.001);
        assertTrue(s.video.audioAvailable);
    }

    @Test
    public void idleVideoGroupIsNotTakeoverReady() {
        StatusSnapshot s = StatusSnapshot.parse(json(
                "{\"video\":{\"streaming\":false,\"room_number\":null,\"video_device\":null,"
                + "\"audio\":null,\"pid\":null,\"started_at\":null,\"uptime_sec\":null}}"));
        assertNotNull(s.video);
        assertTrue(!s.video.streaming);
        assertTrue(!s.video.takeoverReady());
        assertEquals("", s.video.roomNumber);
        assertEquals("", s.video.videoDevice);
        assertEquals(-1, s.video.pid);
        assertEquals(-1, s.video.uptimeSec, 0.001);
        assertTrue(!s.video.audioAvailable);
    }

    @Test
    public void missingVideoGroupYieldsNull() {
        // 旧设备端没有 video 分组：按「未推流」处理，不能崩
        StatusSnapshot s = StatusSnapshot.parse(json("{\"datetime\":\"t\"}"));
        assertNull(s.video);
    }

    private static JSONObject json(String raw) {
        try {
            return new JSONObject(raw);
        } catch (Exception e) {
            throw new IllegalArgumentException(e);
        }
    }
}
