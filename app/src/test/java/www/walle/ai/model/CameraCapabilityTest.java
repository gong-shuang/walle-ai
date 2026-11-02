package www.walle.ai.model;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.json.JSONObject;
import org.junit.Test;

import java.util.List;

/**
 * 摄像头能力模型测试：设备端应答（{@code {"cameras":[...]}}）与控制页透传给全屏页的
 * 裸数组（{@code [{"node":...}]}）两种形态都要能稳定解析。
 */
public class CameraCapabilityTest {

    @Test
    public void parseArray_readsNodeAndName() {
        List<CameraCapability.Camera> list = CameraCapability.parseArray(
                "[{\"node\":\"/dev/video0\",\"name\":\"PSI USB CAMERA\"},"
                        + "{\"node\":\"/dev/video2\",\"name\":\"IR CAMERA\"}]");
        assertEquals(2, list.size());
        assertEquals("/dev/video0", list.get(0).node);
        assertEquals("PSI USB CAMERA", list.get(0).name);
        assertEquals("/dev/video2", list.get(1).node);
    }

    @Test
    public void parseArray_skipsEntriesWithoutNode() {
        List<CameraCapability.Camera> list = CameraCapability.parseArray(
                "[{\"name\":\"无名\"},{\"node\":\"\"},{\"node\":\"/dev/video4\"}]");
        assertEquals(1, list.size());
        assertEquals("/dev/video4", list.get(0).node);
    }

    @Test
    public void parseArray_toleratesEmptyAndMalformed() {
        assertTrue(CameraCapability.parseArray(null).isEmpty());
        assertTrue(CameraCapability.parseArray("").isEmpty());
        assertTrue(CameraCapability.parseArray("not json").isEmpty());
        assertTrue(CameraCapability.parseArray("{}").isEmpty());
    }

    @Test
    public void parse_readsCamerasAndProbeError() throws Exception {
        JSONObject payload = new JSONObject("{\"cameras\":[{\"node\":\"/dev/video0\","
                + "\"name\":\"CAM\",\"detail\":\"USB 2.0\",\"max_resolution\":\"1920x1080\"}],"
                + "\"probe\":{\"error\":\"v4l2-ctl 不可用\"}}");
        CameraCapability cap = CameraCapability.parse(payload);
        assertEquals(1, cap.cameras.size());
        assertEquals("USB 2.0", cap.cameras.get(0).detail);
        assertEquals("1920x1080", cap.cameras.get(0).maxResolution);
        assertEquals("v4l2-ctl 不可用", cap.probeError);
    }

    @Test
    public void parse_missingFieldsFallBackToEmpty() throws Exception {
        CameraCapability cap = CameraCapability.parse(new JSONObject("{\"cameras\":[]}"));
        assertTrue(cap.cameras.isEmpty());
        assertEquals("", cap.probeError);
        assertTrue(CameraCapability.parse(null).cameras.isEmpty());
    }
}
