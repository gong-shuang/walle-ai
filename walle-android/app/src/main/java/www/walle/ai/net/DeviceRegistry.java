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
        DeviceInfo fresh = DeviceInfo.from(j);
        fresh.online = j.optBoolean("online", false);

        String existingKey = findKeyLocked(fresh);
        if (existingKey.isEmpty() && fresh.key().isEmpty()) return null;

        DeviceInfo target = upsertLocked(fresh, existingKey);
        ensureSelectedLocked();
        return existingKey.isEmpty() ? target : null;
    }

    /**
     * 应用一条 discovery/result。报文与 online 同构（最小身份、online 固定 true），
     * 归并规则完全一致，只是需要把解析后的设备回给调用方。
     */
    public synchronized DeviceInfo applyInfo(JSONObject j) {
        if (j == null) return null;
        DeviceInfo fresh = DeviceInfo.from(j);
        fresh.online = true;

        String existingKey = findKeyLocked(fresh);
        if (existingKey.isEmpty() && fresh.key().isEmpty()) return null;

        DeviceInfo target = upsertLocked(fresh, existingKey);
        ensureSelectedLocked();
        return target;
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

    /**
     * 按 device_id → uuid → hostname 的顺序定位已有条目。
     * <p>device_id 跨重启稳定，所以先按它找；只看 uuid 会漏掉「重启后换了 uuid」的同一台设备。
     * device_id 本身也可能漂移，最后用 hostname 兜住，避免同一台机器冒出两张卡片。</p>
     */
    private String findKeyLocked(DeviceInfo fresh) {
        String key = findByDeviceIdLocked(fresh.deviceId);
        if (key.isEmpty()) key = findByUuidLocked(fresh.uuid);
        if (key.isEmpty()) key = findByHostnameLocked(fresh.hostname);
        return key;
    }

    /**
     * 合并到已有条目，找不到就新建。
     *
     * @param existingKey 已匹配到的列表键；空表示新设备
     * @return 落库后的条目（新建时就是 fresh）
     */
    private DeviceInfo upsertLocked(DeviceInfo fresh, String existingKey) {
        DeviceInfo target = existingKey.isEmpty() ? null : devices.get(existingKey);
        if (target == null) {
            devices.put(fresh.key(), fresh);
            return fresh;
        }
        if (isStaleOffline(fresh, target)) return target;

        mergeDeviceInfo(target, fresh);
        target.online = fresh.online;

        String newKey = fresh.key();
        if (!newKey.isEmpty() && !newKey.equals(existingKey)) {
            // uuid 每次重启都会变：列表键跟着换，保证后续按 uuid 匹配能命中
            devices.remove(existingKey);
            devices.put(newKey, target);
            JSONObject st = statusByKey.remove(existingKey);
            if (st != null) statusByKey.put(newKey, st);
            if (selectedKey.equals(existingKey)) selectedKey = newKey;
        }
        return target;
    }

    /** 用新报文的非空字段覆盖已有条目，报文里缺失（空）的字段保留原值。 */
    private static void mergeDeviceInfo(DeviceInfo dst, DeviceInfo src) {
        if (!src.uuid.isEmpty()) dst.uuid = src.uuid;
        if (!src.deviceId.isEmpty()) dst.deviceId = src.deviceId;
        if (!src.versionCode.isEmpty()) dst.versionCode = src.versionCode;
        if (!src.versionName.isEmpty()) dst.versionName = src.versionName;
        if (!src.deviceType.isEmpty()) dst.deviceType = src.deviceType;
        if (!src.model.isEmpty()) dst.model = src.model;
        if (!src.hostname.isEmpty()) dst.hostname = src.hostname;
        if (!src.ip.isEmpty()) dst.ip = src.ip;
        if (!src.osDescription.isEmpty()) dst.osDescription = src.osDescription;
        if (src.uptimeSeconds > 0) dst.uptimeSeconds = src.uptimeSeconds;
        if (!src.datetime.isEmpty()) dst.datetime = src.datetime;
        if (src.timestamp > 0) dst.timestamp = src.timestamp;
        dst.lastSeenMs = src.lastSeenMs;
    }

    private String findByDeviceIdLocked(String deviceId) {
        if (deviceId == null || deviceId.isEmpty()) return "";
        for (Map.Entry<String, DeviceInfo> e : devices.entrySet()) {
            if (deviceId.equals(e.getValue().deviceId)) return e.getKey();
        }
        return "";
    }

    /** 按列表键（通常是 uuid）精确查找。 */
    private String findByUuidLocked(String uuid) {
        if (uuid == null || uuid.isEmpty() || !devices.containsKey(uuid)) return "";
        return uuid;
    }

    /** 按主机名兜底匹配已有设备（device_id 漂移时把同一台机器归并成一条）。 */
    private String findByHostnameLocked(String hostname) {
        if (hostname == null || hostname.isEmpty()) return "";
        for (Map.Entry<String, DeviceInfo> e : devices.entrySet()) {
            String h = e.getValue().hostname;
            if (h != null && hostname.equalsIgnoreCase(h)) return e.getKey();
        }
        return "";
    }

    /**
     * 这条报文是否是「同一台设备上一轮开机」留下的陈旧 offline。
     * <p>device_id 漂移后靠 hostname 归并时，旧主题上的 retained online:false 比已知信息还旧，
     * 必须整条丢弃，否则会把正在在线的设备刷成离线。同一次开机的遗嘱（device_id 相同）始终有效。</p>
     */
    private static boolean isStaleOffline(DeviceInfo fresh, DeviceInfo known) {
        if (fresh.online) return false;
        if (fresh.deviceId.equals(known.deviceId)) return false;
        return fresh.timestamp > 0 && known.timestamp > 0 && fresh.timestamp < known.timestamp;
    }
}
