package www.walle.ai.net;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * 单台设备的身份信息，来源是设备端 {@code identity} 报文。
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
    public String vendor = "";
    public String model = "";
    public String serial = "";
    public String hostname = "";
    public String ip = "";
    public String mac = "";

    public String osName = "";
    public String kernel = "";
    public String arch = "";

    public String cpuModel = "";
    public int coresLogical = -1;
    public int coresPhysical = -1;
    public Double freqMhz = null;
    public Double tempC = null;

    public String memTotalHuman = "";
    public String diskTotalHuman = "";
    public String uptimeHuman = "";
    public String bootTime = "";

    public String datetime = "";

    /**
     * 报文时间（报文里的 {@code timestamp}，Unix 秒）；缺失为 0。
     * <p>用于识别「更旧」的报文：device_id 漂移后，旧主题上遗留的 retained online 会被
     * 反复回放，必须按时间丢弃，否则会把在线设备刷成离线（见 DeviceRegistry.applyOnline）。</p>
     */
    public long timestamp = 0L;

    /** 是否在线（online 报文维护） */
    public boolean online = false;
    /** 是否已收到过 identity 详情 */
    public boolean detailLoaded = false;
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
        if (cpuModel != null && !cpuModel.isEmpty()) return cpuModel;
        return "型号未知";
    }

    public String osLabel() {
        StringBuilder sb = new StringBuilder();
        if (osName != null && !osName.isEmpty()) sb.append(osName);
        if (arch != null && !arch.isEmpty()) {
            if (sb.length() > 0) sb.append(" · ");
            sb.append(arch);
        }
        return sb.length() == 0 ? "—" : sb.toString();
    }

    public String memLabel() {
        if (memTotalHuman != null && !memTotalHuman.isEmpty()) return memTotalHuman;
        return "—";
    }

    public static DeviceInfo from(JSONObject j) {
        DeviceInfo d = new DeviceInfo();
        if (j == null) return d;
        d.uuid = j.optString("uuid", "");
        d.deviceId = j.optString("device_id", "");
        d.versionCode = j.optString("version_code", "");
        d.versionName = j.optString("version_name", "");
        d.deviceType = j.optString("device_type", "");
        d.vendor = j.optString("vendor", "");
        d.model = j.optString("model", "");
        d.serial = j.optString("serial", "");
        d.hostname = j.optString("hostname", "");
        d.ip = j.optString("ip", "");
        d.mac = j.optString("mac", "");
        d.datetime = j.optString("datetime", "");
        d.timestamp = j.optLong("timestamp", 0L);

        JSONObject os = j.optJSONObject("os");
        if (os != null) {
            d.osName = os.optString("name", "");
            d.kernel = os.optString("kernel", "");
            d.arch = os.optString("arch", "");
        }

        JSONObject cpu = j.optJSONObject("cpu");
        if (cpu != null) {
            d.cpuModel = cpu.optString("model", "");
            d.coresLogical = cpu.optInt("cores_logical", -1);
            d.coresPhysical = cpu.optInt("cores_physical", -1);
            d.freqMhz = optDouble(cpu, "frequency_mhz");
            d.tempC = optDouble(cpu, "temperature_c");
        }

        d.memTotalHuman = j.optString("memory_total_human", "");
        d.diskTotalHuman = j.optString("disk_total_human", "");
        d.uptimeHuman = j.optString("uptime_human", "");
        d.bootTime = j.optString("boot_time", "");
        d.detailLoaded = true;
        d.lastSeenMs = System.currentTimeMillis();
        return d;
    }

    private static Double optDouble(JSONObject o, String key) {
        if (o == null || !o.has(key) || o.isNull(key)) return null;
        double v = o.optDouble(key, Double.NaN);
        return Double.isNaN(v) ? null : v;
    }

    /** 网卡列表拼接（identity.interfaces[]） */
    public static String interfacesText(JSONObject j) {
        JSONArray arr = j == null ? null : j.optJSONArray("interfaces");
        if (arr == null || arr.length() == 0) return "";
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < arr.length(); i++) {
            JSONObject it = arr.optJSONObject(i);
            if (it == null) continue;
            String name = it.optString("name", "");
            String addr = it.optString("ip", it.optString("address", ""));
            if (name.isEmpty() && addr.isEmpty()) continue;
            if (sb.length() > 0) sb.append("，");
            sb.append(name).append(addr.isEmpty() ? "" : " " + addr);
        }
        return sb.toString();
    }
}
