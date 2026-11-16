package www.walle.ai.net;

import org.json.JSONObject;

/**
 * 单台设备的最小身份信息，来源是设备端 {@code online} / {@code discovery/result} 报文。
 * <p>
 * 设备端自 2026-11-10 起身份报文只含最小身份（device_id/uuid/版本/系统描述/类型/型号/
 * 主机名/IP/运行时长/时间戳）；MAC、序列号、CPU、内存/磁盘、启动时间等不再通过 MQTT 下发，
 * 设备详情统一走 {@code state/query}，由 {@link www.walle.ai.model.StatusSnapshot} 解析。
 * <p>
 * 设备端每次重启都会重新生成 uuid（uuid5(MAC|IP|启动时间)），因此同一台机器
 * 在不同启动周期里 uuid 可能变化；{@code device_id} 与 {@code hostname} 相对稳定，
 * 用来把“重启后换了 uuid 的同一台设备”归并成一条。
 */
public class DeviceInfo {

    public String uuid = "";
    public String deviceId = "";
    public String versionCode = "";
    public String versionName = "";
    public String deviceType = "";
    public String model = "";
    public String hostname = "";
    public String ip = "";
    /** 操作系统描述（os-release 的 PRETTY_NAME，如 “Ubuntu 24.04.5 LTS”）。 */
    public String osDescription = "";
    /** 已运行秒数（uptime_seconds），缺失为 0。 */
    public long uptimeSeconds = 0L;

    public String datetime = "";

    /**
     * 报文时间（报文里的 {@code timestamp}，Unix 秒）；缺失为 0。
     * <p>用于识别「更旧」的报文：device_id 漂移后，旧主题上遗留的 retained online 会被
     * 反复回放，必须按时间丢弃，否则会把在线设备刷成离线（见 DeviceRegistry.applyOnline）。</p>
     */
    public long timestamp = 0L;

    /** 是否在线（online 报文维护） */
    public boolean online = false;
    /** 最近一次收到任何该设备报文的时间 */
    public long lastSeenMs = 0L;
    /**
     * 主题里使用的稳定设备标识：device_id 优先（跨重启不变），其次 hostname，最后 uuid。
     * <p>不能直接用 uuid：设备每次重启 uuid 会变，主题就跟着变，retained 消息和 ACL 都会失效。</p>
     */
    public String topicDeviceId() {
        if (deviceId != null && !deviceId.isEmpty()) return TopicNames.sanitize(deviceId);
        if (hostname != null && !hostname.isEmpty()) return TopicNames.sanitize(hostname);
        if (uuid != null && !uuid.isEmpty()) return TopicNames.sanitize(uuid);
        return "unknown";
    }

    /** 设备唯一键：优先 uuid，缺失时退化为 device_id */
    public String key() {
        if (uuid != null && !uuid.isEmpty()) return uuid;
        return deviceId == null ? "" : deviceId;
    }

    /** 展示名：主机名 > device_id > uuid 前 8 位 */
    public String displayName() {
        if (hostname != null && !hostname.isEmpty()) return hostname;
        if (deviceId != null && !deviceId.isEmpty()) return deviceId;
        return shortUuid();
    }

    public String shortUuid() {
        if (uuid == null || uuid.isEmpty()) return "—";
        return uuid.length() <= 8 ? uuid : uuid.substring(0, 8);
    }

    public String typeLabel() {
        if ("raspberrypi".equals(deviceType)) return "树莓派";
        if ("embedded-linux".equals(deviceType)) return "嵌入式 Linux";
        if (deviceType == null || deviceType.isEmpty()) return "未知类型";
        return deviceType;
    }

    public String modelLabel() {
        if (model != null && !model.isEmpty() && !model.equals("null")) return model;
        return "型号未知";
    }

    /** 系统描述，缺失时显示占位符。 */
    public String osLabel() {
        if (osDescription != null && !osDescription.isEmpty() && !osDescription.equals("null")) {
            return osDescription;
        }
        return "—";
    }

    /** 运行时长（uptime_seconds）转成简短中文；缺失返回空串。 */
    public String uptimeLabel() {
        if (uptimeSeconds <= 0) return "";
        long days = uptimeSeconds / 86400;
        long hours = (uptimeSeconds % 86400) / 3600;
        long minutes = (uptimeSeconds % 3600) / 60;
        if (days > 0) return days + "天" + hours + "小时";
        if (hours > 0) return hours + "小时" + minutes + "分";
        if (minutes > 0) return minutes + "分";
        return uptimeSeconds + "秒";
    }

    public static DeviceInfo from(JSONObject j) {
        DeviceInfo d = new DeviceInfo();
        if (j == null) return d;
        d.uuid = j.optString("uuid", "");
        d.deviceId = j.optString("device_id", "");
        d.versionCode = j.optString("version_code", "");
        d.versionName = j.optString("version_name", "");
        d.deviceType = j.optString("device_type", "");
        d.model = j.optString("model", "");
        d.hostname = j.optString("hostname", "");
        d.ip = j.optString("ip", "");
        d.osDescription = j.optString("os_description", "");
        d.uptimeSeconds = j.optLong("uptime_seconds", 0L);
        d.datetime = j.optString("datetime", "");
        d.timestamp = j.optLong("timestamp", 0L);
        d.lastSeenMs = System.currentTimeMillis();
        return d;
    }
}
