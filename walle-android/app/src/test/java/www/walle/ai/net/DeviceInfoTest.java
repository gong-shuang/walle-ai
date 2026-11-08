package www.walle.ai.net;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

/** 设备标识与主题段推导的单元测试。 */
public class DeviceInfoTest {

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
        try {
            assertEquals(1794099244L,
                    DeviceInfo.from(new org.json.JSONObject("{\"timestamp\":1794099244}")).timestamp);
            assertEquals(0L, DeviceInfo.from(new org.json.JSONObject("{}")).timestamp);
        } catch (org.json.JSONException e) {
            throw new IllegalArgumentException(e);
        }
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
