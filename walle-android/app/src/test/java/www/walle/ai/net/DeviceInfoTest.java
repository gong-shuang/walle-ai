package www.walle.ai.net;

import static org.junit.Assert.assertEquals;

import org.json.JSONObject;
import org.junit.Test;

/** 设备标识与主题段推导的单元测试。 */
public class DeviceInfoTest {

    private static JSONObject json(String raw) {
        try {
            return new JSONObject(raw);
        } catch (Exception e) {
            throw new IllegalArgumentException(e);
        }
    }

    @Test
    public void topicDeviceIdPrefersStableDeviceId() {
        DeviceInfo d = new DeviceInfo();
        d.uuid = "c004cb13-0004-5252-aa84-4b09f6517c5e";
        d.deviceId = "666feb85d43410b2";
        d.hostname = "RaspberryPi";
        assertEquals("666feb85d43410b2", d.topicDeviceId());
    }

    @Test
    public void topicDeviceIdFallsBackToHostnameThenUuid() {
        DeviceInfo d = new DeviceInfo();
        d.hostname = "RaspberryPi";
        assertEquals("RaspberryPi", d.topicDeviceId());

        d.hostname = "";
        d.uuid = "c004cb13-0004-5252-aa84-4b09f6517c5e";
        // uuid 里的连字符保留（属于允许字符），仅做清洗
        assertEquals("c004cb13-0004-5252-aa84-4b09f6517c5e", d.topicDeviceId());
    }

    @Test
    public void topicDeviceIdSanitizesIllegalChars() {
        DeviceInfo d = new DeviceInfo();
        d.deviceId = "rpi 5/主#";
        assertEquals("rpi_5", d.topicDeviceId());
    }

    @Test
    public void topicDeviceIdNeverEmpty() {
        assertEquals("unknown", new DeviceInfo().topicDeviceId());
    }

    @Test
    public void parsesTimestampForStalenessChecks() {
        // 陈旧 retained 报文的判定依赖报文里的 timestamp，缺省必须是 0（表示未知）
        assertEquals(1794099244L, DeviceInfo.from(json("{\"timestamp\":1794099244}")).timestamp);
        assertEquals(0L, DeviceInfo.from(json("{}")).timestamp);
    }

    @Test
    public void parsesMinimalIdentityPayload() {
        DeviceInfo d = DeviceInfo.from(json("{\"uuid\":\"u1\",\"device_id\":\"d1\","
                + "\"version_code\":1,\"version_name\":\"0.0.2\","
                + "\"os_description\":\"Ubuntu 24.04.5 LTS\","
                + "\"device_type\":\"embedded-linux\",\"model\":\"Orange Pi 5 Plus\","
                + "\"hostname\":\"orangepi5plus\",\"ip\":\"192.168.31.38\","
                + "\"uptime_seconds\":3720,\"timestamp\":1794457514,"
                + "\"datetime\":\"2026-11-12 12:25:14\"}"));

        assertEquals("u1", d.uuid);
        assertEquals("d1", d.deviceId);
        assertEquals("1", d.versionCode);
        assertEquals("0.0.2", d.versionName);
        assertEquals("Ubuntu 24.04.5 LTS", d.osDescription);
        assertEquals("embedded-linux", d.deviceType);
        assertEquals("Orange Pi 5 Plus", d.model);
        assertEquals("orangepi5plus", d.hostname);
        assertEquals("192.168.31.38", d.ip);
        assertEquals(3720L, d.uptimeSeconds);
        assertEquals(1794457514L, d.timestamp);
        assertEquals("Ubuntu 24.04.5 LTS", d.osLabel());
        assertEquals("1小时2分", d.uptimeLabel());
    }

    @Test
    public void missingOptionalIdentityFieldsFallBack() {
        // 最小身份报文之外的字段（MAC/CPU/磁盘等）已不再下发，缺失时必须能正常展示
        DeviceInfo d = DeviceInfo.from(json("{\"uuid\":\"u1\"}"));

        assertEquals("", d.osDescription);
        assertEquals(0L, d.uptimeSeconds);
        assertEquals("—", d.osLabel());
        assertEquals("", d.uptimeLabel());
        assertEquals("型号未知", d.modelLabel());
    }

    @Test
    public void keyAndDisplayName() {
        DeviceInfo d = new DeviceInfo();
        d.uuid = "uuid-1";
        d.deviceId = "dev-1";
        d.hostname = "host-1";
        assertEquals("uuid-1", d.key());
        assertEquals("host-1", d.displayName());

        d.uuid = "";
        assertEquals("dev-1", d.key());

        d.hostname = "";
        assertEquals("dev-1", d.displayName());
    }
}
