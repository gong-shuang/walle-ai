package www.walle.ai.net;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 设备清单 + 状态缓存 + 当前选中设备。
 *
 * <p>所有方法都加了同步：设备报文来自 paho 回调线程（经主线程转发），
 * 而 UI 会在主线程随时读取，两者必须互斥。</p>
 *
 * <p>设备键 {@link DeviceInfo#key()} 优先 uuid；uuid 每次重启都会变，所以设备的稳定身份
 * （用于主题段）由 {@link DeviceInfo#topicDeviceId()} 单独推导。</p>
 */
public class DeviceRegistry {

    /** 已发现的设备，键为 {@link DeviceInfo#key()} */
    private final Map<String, DeviceInfo> devices = new LinkedHashMap<>();
    /** 每台设备最近一次 state/result 快照，键同上 */
    private final Map<String, JSONObject> statusByKey = new LinkedHashMap<>();

    private volatile String selectedKey = "";
    private volatile JSONObject latestStatus;

    // ---------------------------------------------------------------- 查询

    /** 已发现设备（在线优先，其次按名称）。 */
    public synchronized List<DeviceInfo> devices() {
        List<DeviceInfo> list = new ArrayList<>(devices.values());
        Collections.sort(list, (a, b) -> {
            if (a.online != b.online) return a.online ? -1 : 1;
            return a.displayName().compareToIgnoreCase(b.displayName());
        });
        return list;
    }

    public synchronized int count() {
        return devices.size();
    }

    public synchronized int onlineCount() {
        int n = 0;
        for (DeviceInfo d : devices.values()) if (d.online) n++;
        return n;
    }

    public synchronized DeviceInfo get(String key) {
        return key == null ? null : devices.get(key);
    }

    public String selectedKey() {
        return selectedKey;
    }

    public synchronized DeviceInfo selected() {
        return devices.get(selectedKey);
    }

    public synchronized void select(String key) {
        if (key != null && !key.equals(selectedKey)) {
            selectedKey = key;
            ensureSelectedLocked();
        }
    }

    public synchronized JSONObject status(String key) {
        return key == null ? null : statusByKey.get(key);
    }

    public JSONObject latestStatus() {
        return latestStatus;
    }

    // ---------------------------------------------------------------- 写入

    /** 缓存一次 state/result 快照（键用报文里的 uuid）。 */
    public void putStatus(String key, JSONObject status) {
        if (key == null || key.isEmpty() || status == null) return;
        synchronized (this) {
            statusByKey.put(key, status);
        }
        latestStatus = status;
    }

    /**
     * 应用一条 online 报文（上线/下线，retained + LWT）。
     *
     * @return 本次新加入列表的设备；已有设备或忽略的报文返回 null
     */
    public synchronized DeviceInfo applyOnline(JSONObject j) {
        if (j == null) return null;
        boolean online = j.optBoolean("online", false);
        String uuid = j.optString("uuid", "");
        String did = j.optString("device_id", "");
        String hostname = j.optString("hostname", "");

        // online 报文自带完整身份信息（字段同 discovery/result），直接整体解析
        DeviceInfo fresh = DeviceInfo.from(j);
        fresh.online = online;

        DeviceInfo target;
        DeviceInfo added = null;
        String key = !uuid.isEmpty() ? uuid : findByDeviceIdLocked(did);
        if (key == null || key.isEmpty()) key = findByHostnameLocked(hostname);
        target = key == null || key.isEmpty() ? null : devices.get(key);

        if (target == null) {
            // 列表里没有这台设备：
            // - online=true  → 新增（报文已补全身份信息）
            // - online=false → 不处理（broker 残留的 retained 遗嘱不能变成幽灵卡片）
            if (online && !fresh.key().isEmpty()) {
                devices.put(fresh.key(), fresh);
                added = fresh;
            }
        } else {
            mergeDeviceInfo(target, fresh);
            target.online = online;
            String newKey = fresh.key();
            if (!newKey.isEmpty() && !newKey.equals(key)) {
                // uuid 每次重启都会变：列表键跟着换，保证后续按 uuid 匹配能命中
                devices.remove(key);
                devices.put(newKey, target);
                JSONObject st = statusByKey.remove(key);
                if (st != null) statusByKey.put(newKey, st);
                if (selectedKey.equals(key)) selectedKey = newKey;
            }
        }
        ensureSelectedLocked();
        return added;
    }

    /**
     * 应用一条 discovery/result（设备身份详情）。同一台设备重启换了 uuid 时按 device_id 替换旧条目。
     *
     * @return 解析后的设备；无有效键时返回 null
     */
    public synchronized DeviceInfo applyInfo(JSONObject j) {
        if (j == null) return null;
        DeviceInfo d = DeviceInfo.from(j);
        String key = d.key();
        if (key.isEmpty()) return null;

        if (!d.deviceId.isEmpty()) {
            List<String> stale = new ArrayList<>();
            for (Map.Entry<String, DeviceInfo> e : devices.entrySet()) {
                DeviceInfo old = e.getValue();
                if (!e.getKey().equals(key)
                        && !old.deviceId.isEmpty()
                        && old.deviceId.equals(d.deviceId)) {
                    stale.add(e.getKey());
                }
            }
            for (String s : stale) {
                devices.remove(s);
                statusByKey.remove(s);
                if (s.equals(selectedKey)) selectedKey = "";
            }
        }
        d.online = true;
        d.lastSeenMs = System.currentTimeMillis();
        devices.put(key, d);
        ensureSelectedLocked();
        return d;
    }

    // ---------------------------------------------------------------- 内部

    /** 选中设备被移除/不存在时：清空或自动选第一台 */
    private void ensureSelectedLocked() {
        if (!selectedKey.isEmpty() && devices.containsKey(selectedKey)) return;
        selectedKey = "";
        for (DeviceInfo d : devices.values()) {
            selectedKey = d.key();
            break;
        }
    }

    /** 用 online 的完整字段覆盖已有条目，报文里缺失（空）的字段保留原值。 */
    private static void mergeDeviceInfo(DeviceInfo dst, DeviceInfo src) {
        if (!src.uuid.isEmpty()) dst.uuid = src.uuid;
        if (!src.deviceId.isEmpty()) dst.deviceId = src.deviceId;
        if (!src.deviceType.isEmpty()) dst.deviceType = src.deviceType;
        if (!src.vendor.isEmpty()) dst.vendor = src.vendor;
        if (!src.model.isEmpty()) dst.model = src.model;
        if (!src.serial.isEmpty()) dst.serial = src.serial;
        if (!src.hostname.isEmpty()) dst.hostname = src.hostname;
        if (!src.ip.isEmpty()) dst.ip = src.ip;
        if (!src.mac.isEmpty()) dst.mac = src.mac;
        if (!src.osName.isEmpty()) dst.osName = src.osName;
        if (!src.kernel.isEmpty()) dst.kernel = src.kernel;
        if (!src.arch.isEmpty()) dst.arch = src.arch;
        if (!src.cpuModel.isEmpty()) dst.cpuModel = src.cpuModel;
        if (src.coresLogical > 0) dst.coresLogical = src.coresLogical;
        if (src.coresPhysical > 0) dst.coresPhysical = src.coresPhysical;
        if (src.freqMhz != null) dst.freqMhz = src.freqMhz;
        if (src.tempC != null) dst.tempC = src.tempC;
        if (!src.memTotalHuman.isEmpty()) dst.memTotalHuman = src.memTotalHuman;
        if (!src.diskTotalHuman.isEmpty()) dst.diskTotalHuman = src.diskTotalHuman;
        if (!src.uptimeHuman.isEmpty()) dst.uptimeHuman = src.uptimeHuman;
        if (!src.bootTime.isEmpty()) dst.bootTime = src.bootTime;
        if (!src.datetime.isEmpty()) dst.datetime = src.datetime;
        dst.detailLoaded |= src.detailLoaded;
        dst.lastSeenMs = src.lastSeenMs;
    }

    private String findByDeviceIdLocked(String deviceId) {
        if (deviceId == null || deviceId.isEmpty()) return "";
        for (Map.Entry<String, DeviceInfo> e : devices.entrySet()) {
            if (deviceId.equals(e.getValue().deviceId)) return e.getKey();
        }
        return "";
    }

    /** 按主机名兜底匹配已有设备（仅用于 online 无 uuid/device_id 可用时的归并）。 */
    private String findByHostnameLocked(String hostname) {
        if (hostname == null || hostname.isEmpty()) return "";
        for (Map.Entry<String, DeviceInfo> e : devices.entrySet()) {
            String h = e.getValue().hostname;
            if (h != null && hostname.equalsIgnoreCase(h)) return e.getKey();
        }
        return "";
    }
}
