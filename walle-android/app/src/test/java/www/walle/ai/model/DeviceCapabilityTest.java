package www.walle.ai.model;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.json.JSONObject;
import org.junit.Test;

/** 设备能力（capability/result）解析契约：摄像头 + 麦克风 + 音响。 */
public class DeviceCapabilityTest {

    @Test
    public void parsesFullPayload() throws Exception {
        JSONObject p = new JSONObject("{"
                + "\"capabilities\":[\"system_status\",\"remote_cmd\",\"camera_video_capture\","
                + "\"rtmp_push\",\"audio_capture\",\"audio_playback\"],"
                + "\"cameras\":[{\"node\":\"/dev/video0\",\"name\":\"USB Camera\",\"max_resolution\":\"640x480\"}],"
                + "\"audio\":{"
                + "  \"capture\":{\"available\":true,\"device\":\"default\",\"sources\":[\"alsa_input.usb-1\"],\"reason\":null},"
                + "  \"playback\":{\"available\":true,\"device\":\"alsa_output.usb-1\",\"volume_percent\":70,\"muted\":false,\"sinks\":[\"alsa_output.usb-1\"]}"
                + "},"
                + "\"probe\":{\"tool\":\"v4l2-ctl + pactl\",\"error\":null,\"audio_error\":null}"
                + "}");
        DeviceCapability cap = DeviceCapability.parse(p);
        assertEquals(1, cap.cameras.size());
        assertEquals("/dev/video0", cap.cameras.get(0).node);
        assertTrue(cap.has("audio_capture"));
        assertTrue(cap.has("audio_playback"));
        assertTrue(cap.audioCapture.available);
        assertEquals("default", cap.audioCapture.device);
        assertTrue(cap.audioPlayback.available);
        assertEquals(70, cap.audioPlayback.volumePercent);
        assertFalse(cap.audioPlayback.muted);
        assertTrue(cap.probeError.isEmpty());
    }

    private static JSONObject payloadOf(String json) throws Exception {
        return new JSONObject(json);
    }

    @Test
    public void missingAudioGroupDegradesToUnavailable() throws Exception {
        // 旧设备端（只上报摄像头）或探测失败时，audio 分组缺失也不能崩
        DeviceCapability cap = DeviceCapability.parse(payloadOf("{\"cameras\":[]}"));
        assertTrue(cap.cameras.isEmpty());
        assertFalse(cap.audioCapture.available);
        assertFalse(cap.audioPlayback.available);
        assertEquals(-1, cap.audioPlayback.volumePercent);
    }

    @Test
    public void unavailableAudioCarriesReason() throws Exception {
        DeviceCapability cap = DeviceCapability.parse(payloadOf("{"
                + "\"audio\":{"
                + "  \"capture\":{\"available\":false,\"device\":null,\"reason\":\"未检测到录音源\"},"
                + "  \"playback\":{\"available\":false,\"reason\":\"未安装 pactl\",\"volume_percent\":null}"
                + "}}"));
        assertFalse(cap.audioCapture.available);
        assertEquals("未检测到录音源", cap.audioCapture.reason);
        assertFalse(cap.audioPlayback.available);
        assertEquals("未安装 pactl", cap.audioPlayback.reason);
        assertEquals(-1, cap.audioPlayback.volumePercent);   // null 归一为 -1
    }

    @Test
    public void nullAndGarbagePayloadNeverCrash() throws Exception {
        DeviceCapability cap = DeviceCapability.parse(null);
        assertTrue(cap.cameras.isEmpty());
        assertTrue(cap.capabilities.isEmpty());
        assertFalse(cap.audioCapture.available);

        DeviceCapability garbage = DeviceCapability.parse(payloadOf("{\"audio\":\"oops\"}"));
        assertFalse(garbage.audioCapture.available);
        assertFalse(garbage.audioPlayback.available);
    }
}
