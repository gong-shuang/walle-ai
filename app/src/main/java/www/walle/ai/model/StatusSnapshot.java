package www.walle.ai.model;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * 设备状态快照（{@code walle/{tenant}/{device}/state/result} 的 payload）。
 *
 * <p>协议解析只在这里做一次：UI 层不再直接 {@code optXxx}，字段缺省/类型异常都由 {@link #parse}
 * 归一成「null 或空列表」，界面只负责展示。这样字段改名只会影响这一个文件，也便于写单测。</p>
 */
public final class StatusSnapshot {

    public final String uuid;
    public final String datetime;
    public final long uptimeSeconds;
    public final String hostname;
    public final String ip;

    /** 以下分组可能为 null（设备端未采集/字段缺失）。 */
    public final SystemInfo system;
    public final Cpu cpu;
    public final Memory memory;
    public final List<DiskMount> disks;
    public final Network network;
    public final Usb usb;
    public final List<String> activeAlarms;

    private StatusSnapshot(JSONObject s) {
        uuid = optString(s, "uuid", "");
        datetime = optString(s, "datetime", "");
        uptimeSeconds = s.optLong("uptime_seconds", 0L);
        hostname = optString(s, "hostname", "");
        ip = optString(s, "ip", "");
        system = SystemInfo.parse(s.optJSONObject("system"));
        cpu = Cpu.parse(s.optJSONObject("cpu"));
        memory = Memory.parse(s.optJSONObject("memory"));
        disks = DiskMount.parseList(s.optJSONObject("disk"));
        network = Network.parse(s.optJSONObject("network"));
        usb = Usb.parse(s.optJSONObject("usb"));
        activeAlarms = parseStringArray(s.optJSONObject("alarms"), "active");
    }

    /** 解析状态快照；传入 null 返回 null（UI 直接展示「暂无数据」）。 */
    public static StatusSnapshot parse(JSONObject s) {
        return s == null ? null : new StatusSnapshot(s);
    }

    // ---------------------------------------------------------------- 分组模型

    public static final class SystemInfo {
        public final String model;
        public final String os;
        public final String machine;
        public final String kernel;

        private SystemInfo(JSONObject o) {
            model = optString(o, "model", "");
            os = optString(o, "os", "");
            machine = optString(o, "machine", "");
            kernel = optString(o, "kernel", "");
        }

        static SystemInfo parse(JSONObject o) {
            return o == null ? null : new SystemInfo(o);
        }
    }

    public static final class Cpu {
        public final Double percent;
        public final Double tempC;
        public final String load1;
        public final String load5;
        public final String load15;
        public final String freqCurrent;
        public final String freqMin;
        public final String freqMax;
        public final int coresLogical;
        public final int coresPhysical;
        /** 各核占用率；无数据时为空列表 */
        public final double[] perCore;

        private Cpu(JSONObject o) {
            percent = optDouble(o, "percent");
            tempC = optDouble(o, "temperature_c");
            load1 = optString(o, "load_avg_1m", "");
            load5 = optString(o, "load_avg_5m", "");
            load15 = optString(o, "load_avg_15m", "");
            JSONObject freq = o.optJSONObject("frequency_mhz");
            freqCurrent = optString(freq, "current", "");
            freqMin = optString(freq, "min", "");
            freqMax = optString(freq, "max", "");
            coresLogical = o.optInt("count_logical", -1);
            coresPhysical = o.optInt("count_physical", -1);
            JSONArray cores = o.optJSONArray("per_core_percent");
            if (cores == null || cores.length() == 0) {
                perCore = new double[0];
            } else {
                perCore = new double[cores.length()];
                for (int i = 0; i < cores.length(); i++) {
                    perCore[i] = cores.optDouble(i, 0);
                }
            }
        }

        static Cpu parse(JSONObject o) {
            return o == null ? null : new Cpu(o);
        }
    }

    public static final class Memory {
        public final Double percent;
        public final String usedHuman;
        public final String totalHuman;
        public final String availableHuman;
        public final String swapUsedHuman;
        public final String swapTotalHuman;
        public final Double swapPercent;

        private Memory(JSONObject o) {
            percent = optDouble(o, "percent");
            usedHuman = optString(o, "used_human", "");
            totalHuman = optString(o, "total_human", "");
            availableHuman = optString(o, "available_human", "");
            JSONObject swap = o.optJSONObject("swap");
            swapUsedHuman = optString(swap, "used_human", "");
            swapTotalHuman = optString(swap, "total_human", "");
            swapPercent = optDouble(swap, "percent");
        }

        static Memory parse(JSONObject o) {
            return o == null ? null : new Memory(o);
        }
    }

    public static final class DiskMount {
        public final String mount;
        public final String fstype;
        public final String usedHuman;
        public final String totalHuman;
        public final String device;
        public final Double percent;

        private DiskMount(JSONObject o) {
            mount = optString(o, "mount", "");
            fstype = optString(o, "fstype", "");
            usedHuman = optString(o, "used_human", "");
            totalHuman = optString(o, "total_human", "");
            device = optString(o, "device", "");
            percent = optDouble(o, "percent");
        }

        static List<DiskMount> parseList(JSONObject disk) {
            JSONArray mounts = disk == null ? null : disk.optJSONArray("mounts");
            if (mounts == null || mounts.length() == 0) return Collections.emptyList();
            List<DiskMount> list = new ArrayList<>(mounts.length());
            for (int i = 0; i < mounts.length(); i++) {
                JSONObject m = mounts.optJSONObject(i);
                if (m != null) list.add(new DiskMount(m));
            }
            return list;
        }
    }

    public static final class Network {
        public final String totalRxRate;
        public final String totalTxRate;
        public final List<Interface> interfaces;

        private Network(JSONObject o) {
            totalRxRate = optString(o, "total_rx_rate", "");
            totalTxRate = optString(o, "total_tx_rate", "");
            JSONObject ifaces = o.optJSONObject("interfaces");
            List<Interface> list = new ArrayList<>();
            if (ifaces != null) {
                for (String name : optionalNames(ifaces)) {
                    JSONObject it = ifaces.optJSONObject(name);
                    if (it != null) list.add(new Interface(name, it));
                }
            }
            interfaces = list;
        }

        /** 网卡顺序不保证，这里按名称排序，保证同一份数据渲染稳定。 */
        private static List<String> optionalNames(JSONObject obj) {
            JSONArray names = obj.names();
            List<String> list = new ArrayList<>();
            if (names != null) {
                for (int i = 0; i < names.length(); i++) list.add(names.optString(i));
            }
            Collections.sort(list);
            return list;
        }

        static Network parse(JSONObject o) {
            return o == null ? null : new Network(o);
        }

        public static final class Interface {
            public final String name;
            public final String rxRate;
            public final String txRate;

            private Interface(String name, JSONObject o) {
                this.name = name;
                this.rxRate = optString(o, "rx_rate", "");
                this.txRate = optString(o, "tx_rate", "");
            }
        }
    }

    public static final class Usb {
        public final int count;
        public final List<Device> devices;

        private Usb(JSONObject o) {
            count = o.optInt("count", 0);
            JSONArray arr = o.optJSONArray("devices");
            List<Device> list = new ArrayList<>();
            if (arr != null) {
                for (int i = 0; i < arr.length(); i++) {
                    JSONObject d = arr.optJSONObject(i);
                    if (d != null) list.add(new Device(d));
                }
            }
            devices = list;
        }

        static Usb parse(JSONObject o) {
            return o == null ? null : new Usb(o);
        }

        public static final class Device {
            public final String title;
            public final String manufacturer;
            public final String vendorId;
            public final String productId;
            public final List<String> blockNodes;

            private Device(JSONObject o) {
                String product = optString(o, "product", "");
                title = product.isEmpty() ? optString(o, "sysfs", "未知设备") : product;
                manufacturer = optString(o, "manufacturer", "");
                vendorId = optString(o, "vendor_id", "");
                productId = optString(o, "product_id", "");
                JSONArray blocks = o.optJSONArray("block_nodes");
                List<String> nodes = new ArrayList<>();
                if (blocks != null) {
                    for (int i = 0; i < blocks.length(); i++) {
                        String n = blocks.optString(i);
                        if (!n.isEmpty()) nodes.add(n);
                    }
                }
                blockNodes = nodes;
            }
        }
    }

    // ---------------------------------------------------------------- 解析辅助

    private static String optString(JSONObject o, String key, String fallback) {
        if (o == null) return fallback;
        Object v = o.opt(key);
        if (v == null || v == JSONObject.NULL) return fallback;
        return String.valueOf(v);
    }

    /** null / 非数字 / NaN 都归成 null，避免 UI 层反复判空。 */
    private static Double optDouble(JSONObject o, String key) {
        if (o == null || !o.has(key) || o.isNull(key)) return null;
        double v = o.optDouble(key, Double.NaN);
        return Double.isNaN(v) ? null : v;
    }

    private static List<String> parseStringArray(JSONObject o, String key) {
        JSONArray arr = o == null ? null : o.optJSONArray(key);
        if (arr == null || arr.length() == 0) return Collections.emptyList();
        List<String> list = new ArrayList<>(arr.length());
        for (int i = 0; i < arr.length(); i++) {
            String s = arr.optString(i, "");
            if (!s.isEmpty()) list.add(s);
        }
        return list;
    }
}
