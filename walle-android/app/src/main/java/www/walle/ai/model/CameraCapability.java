package www.walle.ai.model;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/** 摄像头能力（capability/result 里的 cameras 分组）的强类型模型。 */
public final class CameraCapability {

    public static final class Camera {
        public final String node;
        public final String name;
        public final String detail;
        public final String maxResolution;

        public Camera(String node, String name, String detail, String maxResolution) {
            this.node = node == null ? "" : node;
            this.name = name == null ? "" : name;
            this.detail = detail == null ? "" : detail;
            this.maxResolution = maxResolution == null ? "" : maxResolution;
        }
    }

    public final List<Camera> cameras;
    /** v4l2 探测失败原因（无摄像头时展示给用户）。 */
    public final String probeError;

    private CameraCapability(List<Camera> cameras, String probeError) {
        this.cameras = cameras;
        this.probeError = probeError;
    }

    /**
     * 解析「裸数组」形态：[{"node":"/dev/video0","name":"..."}]。
     *
     * <p>控制页把能力列表塞进 Intent 传给播放页时用的就是这个形态，
     * 与设备端 {@code capability/result} 的 {@code {"cameras":[...]}} 不同。</p>
     */
    public static List<Camera> parseArray(String json) {
        if (json == null || json.isEmpty()) return Collections.emptyList();
        List<Camera> list = new ArrayList<>();
        try {
            JSONArray arr = new JSONArray(json);
            for (int i = 0; i < arr.length(); i++) {
                JSONObject c = arr.optJSONObject(i);
                if (c == null) continue;
                String node = c.optString("node", "");
                if (node.isEmpty()) continue;
                list.add(new Camera(node, c.optString("name", ""), "", ""));
            }
        } catch (Exception e) {
            // 结构非法时退化为空列表：播放页只是少一个切换下拉，不该崩
        }
        return Collections.unmodifiableList(list);
    }

    public static CameraCapability parse(JSONObject payload) {
        if (payload == null) return new CameraCapability(Collections.emptyList(), "");
        List<Camera> list = new ArrayList<>();
        JSONArray arr = payload.optJSONArray("cameras");
        if (arr != null) {
            for (int i = 0; i < arr.length(); i++) {
                JSONObject c = arr.optJSONObject(i);
                if (c == null) continue;
                String node = c.optString("node", "");
                if (node.isEmpty()) continue;
                list.add(new Camera(node, c.optString("name", ""),
                        c.optString("detail", ""), c.optString("max_resolution", "")));
            }
        }
        JSONObject probe = payload.optJSONObject("probe");
        String error = probe == null ? "" : probe.optString("error", "");
        return new CameraCapability(list, error);
    }
}
