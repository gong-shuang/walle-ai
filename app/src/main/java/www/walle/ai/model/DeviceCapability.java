package www.walle.ai.model;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * 设备能力（capability/result）的强类型模型：摄像头 + 音频外设（麦克风/音响）。
 *
 * <p>对应设备端 {@code build_capability()}：主题已从 v2 早期的
 * {@code camera/capability/query} 升级为通用的 {@code capability/query}，
 * 报文新增 {@code audio{capture, playback}} 与条件化的 {@code capabilities} 清单。</p>
 *
 * <p>解析原则与 {@link StatusSnapshot} 一致：缺分组归空、异常值归一为默认值，
 * 任何字段缺失都不能让页面崩。</p>
 */
public final class DeviceCapability {

    /** 音频端点（录音 capture / 播放 playback）。 */
    public static final class AudioEnd {
        public final boolean available;
        /** 默认设备名（pulse source/sink），未知为 "" */
        public final String device;
        /** 不可用原因（pactl 缺失 / 无设备等），可用时为 "" */
        public final String reason;
        /** 仅 playback 有意义：音量 0-150+，未知为 -1 */
        public final int volumePercent;
        /** 仅 playback 有意义：是否静音 */
        public final boolean muted;

        AudioEnd(boolean available, String device, String reason, int volumePercent, boolean muted) {
            this.available = available;
            this.device = device == null ? "" : device;
            this.reason = reason == null ? "" : reason;
            this.volumePercent = volumePercent;
            this.muted = muted;
        }

        /** 通用「不可用」占位：ViewModel 初始态/无设备时复用同一实例。 */
        public static final AudioEnd UNAVAILABLE = new AudioEnd(false, "", "", -1, false);

        static AudioEnd parse(JSONObject o) {
            if (o == null) return UNAVAILABLE;
            int volume = o.has("volume_percent") && !o.isNull("volume_percent")
                    ? o.optInt("volume_percent", -1) : -1;
            return new AudioEnd(
                    o.optBoolean("available", false),
                    o.optString("device", ""),
                    o.optString("reason", ""),
                    volume,
                    o.optBoolean("muted", false));
        }
    }

    public final List<CameraCapability.Camera> cameras;
    /** 设备能力标签，如 camera_video_capture / audio_capture / audio_playback / rtmp_push … */
    public final List<String> capabilities;
    /** 麦克风能力，不可用时 available=false 且 reason 给出原因 */
    public final AudioEnd audioCapture;
    /** 音响/扬声器能力 */
    public final AudioEnd audioPlayback;
    /** v4l2 探测失败原因（无摄像头时展示给用户）。 */
    public final String probeError;

    private DeviceCapability(List<CameraCapability.Camera> cameras, List<String> capabilities,
                             AudioEnd audioCapture, AudioEnd audioPlayback, String probeError) {
        this.cameras = cameras;
        this.capabilities = capabilities;
        this.audioCapture = audioCapture;
        this.audioPlayback = audioPlayback;
        this.probeError = probeError;
    }

    public static DeviceCapability parse(JSONObject payload) {
        if (payload == null) {
            return new DeviceCapability(Collections.emptyList(), Collections.emptyList(),
                    AudioEnd.UNAVAILABLE, AudioEnd.UNAVAILABLE, "");
        }
        // 摄像头与 probeError 的解析规则与旧版一致，直接复用 CameraCapability
        CameraCapability cam = CameraCapability.parse(payload);

        List<String> caps = new ArrayList<>();
        JSONArray arr = payload.optJSONArray("capabilities");
        if (arr != null) {
            for (int i = 0; i < arr.length(); i++) {
                String c = arr.optString(i, "");
                if (!c.isEmpty()) caps.add(c);
            }
        }

        JSONObject audio = payload.optJSONObject("audio");
        AudioEnd capture = AudioEnd.parse(audio == null ? null : audio.optJSONObject("capture"));
        AudioEnd playback = AudioEnd.parse(audio == null ? null : audio.optJSONObject("playback"));

        return new DeviceCapability(cam.cameras, Collections.unmodifiableList(caps),
                capture, playback, cam.probeError);
    }

    /** 是否有某能力标签。 */
    public boolean has(String capability) {
        return capabilities.contains(capability);
    }
}
