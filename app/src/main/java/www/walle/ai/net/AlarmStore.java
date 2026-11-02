package www.walle.ai.net;

import org.json.JSONException;
import org.json.JSONObject;

import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CopyOnWriteArrayList;

/**
 * 全局告警收件箱：跨页面共享「今日」「历史」两个视图，以及未读数。
 *
 * <p>所有方法都在主线程调用（MqttManager 已把回调切到主线程）。</p>
 *
 * <p>两个页面视图：</p>
 * <ul>
 *   <li>{@link #todayAlarms()}：<b>今天收到的全部条目</b>（按本地日历日筛选），供「今日」页；</li>
 *   <li>{@link #historyAlarms()}：<b>今天之前的条目</b>，供「历史」页（两页互不重复）。</li>
 * </ul>
 *
 * <p>时间来源是 {@link #add} 时补上的本地接收时间 {@code ts}；{@link #items()} 仍是内存里的
 * 全量流水（落盘/去重/重建都用它），只是不再直接喂给「历史」页。</p>
 *
 * <p>{@link #activeAlarms()} 保留「尚未恢复的告警」这一领域状态，只用于红点判定
 * （按设备 + 指标 + 类型去重，recover 移出；USB 插拔不算告警）。</p>
 *
 * <p>未读语义：红点只统计「当前告警」里用户还没看过的条目。recover 会让对应的未读一起消失
 * （问题已经不存在了，不需要再提醒）。</p>
 *
 * <p>持久化（技术债 N11）：内存是权威数据源，{@link Persistence} 只是旁路落盘。
 * 「当前告警」不单独落盘，进程被杀后再次启动时由 {@link #attach} 从历史流水里重建
 * （从新到旧扫描，某个 key 第一次出现的 event 决定它是「仍告警」还是「已恢复」，
 * 语义等价于「最近一条 alarm 且其后无 recover」）。</p>
 */
public class AlarmStore {

    public interface Observer {
        void onAlarmsChanged();
    }

    /** 落盘端口：实现见 {@code data/LocalDb}；所有写操作必须是异步的。 */
    public interface Persistence {
        /** 追加一条告警；read=true 表示历史遗留的已读记录。 */
        void insert(JSONObject alarm, boolean read);

        void markAllRead();

        void clear();

        /** 按时间倒序返回最多 {@code limit} 条。 */
        List<JSONObject> load(int limit);

        /** 按时间倒序返回最多 {@code limit} 条尚未读过的记录（read=0），用于重启后恢复红点。 */
        default List<JSONObject> loadUnread(int limit) { return new ArrayList<>(); }
    }

    /** 内存里保留的最大告警条数。 */
    public static final int MAX_ITEMS = 200;
    /** 当前未恢复告警的内存上限（防止异常设备刷出无限多个不同 key）。 */
    private static final int MAX_ACTIVE = MAX_ITEMS;

    private static final String EVENT_ALARM = "alarm";
    private static final String EVENT_RECOVER = "recover";

    private static final AlarmStore INSTANCE = new AlarmStore();

    public static AlarmStore get() { return INSTANCE; }

    /** 全量流水（历史页）。 */
    private final List<JSONObject> items = new ArrayList<>();
    /** 当前告警：key -> 该 key 最新的一条 alarm 报文，新的在前。 */
    private final LinkedHashMap<String, JSONObject> active = new LinkedHashMap<>();
    /** 用户还没看过的「当前告警」key，红点数 = size。 */
    private final LinkedHashSet<String> unreadActive = new LinkedHashSet<>();
    private final CopyOnWriteArrayList<Observer> observers = new CopyOnWriteArrayList<>();
    private Persistence persistence;

    /** 包内可见：单测需要干净实例，生产统一走 {@link #get()}。 */
    AlarmStore() {}

    public void addObserver(Observer o) { if (o != null && !observers.contains(o)) observers.add(o); }

    public void removeObserver(Observer o) { observers.remove(o); }

    /**
     * 挂上持久化（幂等）：立刻把历史告警读回内存，重建「当前告警」，并恢复未读数。
     *
     * @param p 持久化实现；null 表示纯内存模式
     */
    public synchronized void attach(Persistence p) {
        if (p == null || persistence != null) return;
        persistence = p;
        try {
            List<JSONObject> stored = p.load(MAX_ITEMS);
            if (!stored.isEmpty()) {
                // 本次运行已收到的排在前面（理论上 attach 早于任何报文，这里只是兜底）
                List<JSONObject> merged = new ArrayList<>(items);
                for (JSONObject o : stored) {
                    if (!containsPayload(merged, o)) merged.add(o);
                }
                items.clear();
                items.addAll(merged);
                trim();
            }
            rebuildActive();
            // 未读只恢复「仍在当前告警里」的条目：已恢复、USB 等过时记录不该再点亮红点
            for (JSONObject o : p.loadUnread(MAX_ITEMS)) {
                String key = activeKey(o);
                if (!key.isEmpty() && active.containsKey(key)) unreadActive.add(key);
            }
        } catch (Throwable t) {
            // 持久化坏了不能影响告警功能本身：退回纯内存模式
            persistence = null;
            return;
        }
        notifyObservers();
    }

    public synchronized void add(JSONObject alarm) {
        if (alarm == null) return;
        // 本地补接收时间：设备报文只有 timestamp(秒)/datetime，没有 ts。
        // 「今日」页按本地日历日筛选要靠它，落盘排序（LocalDb.ts 列）也用它。
        if (!alarm.has("ts")) {
            try {
                alarm.put("ts", System.currentTimeMillis());
            } catch (JSONException ignored) {
            }
        }
        items.add(0, alarm);
        trim();
        String key = activeKey(alarm);
        if (!key.isEmpty()) {
            String event = alarm.optString("event", "");
            if (EVENT_ALARM.equals(event)) {
                // 同一 key 重复告警只更新卡片（值与时间），不新增也不重复计数
                boolean isNew = !active.containsKey(key);
                active.remove(key);          // remove + put 把最新一条移到队首
                active.put(key, alarm);
                if (isNew) unreadActive.add(key);
            } else if (EVENT_RECOVER.equals(event)) {
                active.remove(key);
                unreadActive.remove(key);
            }
        }
        if (persistence != null) persistence.insert(alarm, false);
        notifyObservers();
    }

    /** 全量流水（历史页），新的在前。 */
    public synchronized List<JSONObject> items() { return new ArrayList<>(items); }

    /** 当前未恢复的告警（红点判定用），按最近更新倒序。 */
    public synchronized List<JSONObject> activeAlarms() {
        return new ArrayList<>(active.values());
    }

    /** 今天（App 本地日历日）收到的全部告警/事件，按接收时间倒序（「今日」页）。 */
    public synchronized List<JSONObject> todayAlarms() {
        List<JSONObject> out = new ArrayList<>();
        for (JSONObject o : items) {
            if (isToday(o)) out.add(o);
        }
        return out;
    }

    /** 今天之前的记录（「历史」页）：今日的记录只留在「今日」页，两页不重复。 */
    public synchronized List<JSONObject> historyAlarms() {
        List<JSONObject> out = new ArrayList<>();
        for (JSONObject o : items) {
            if (!isToday(o)) out.add(o);
        }
        return out;
    }

    private static boolean isToday(JSONObject o) {
        LocalDate today = LocalDate.now();
        long ts = millis(o);
        if (ts > 0) {
            return Instant.ofEpochMilli(ts).atZone(ZoneId.systemDefault()).toLocalDate().equals(today);
        }
        // 升级前落盘的老记录没有 ts：退回设备的 datetime（"YYYY-MM-DD HH:MM:SS"）日期部分
        String datetime = o.optString("datetime", "");
        return datetime.length() >= 10 && datetime.startsWith(today.toString());
    }

    private static long millis(JSONObject o) {
        long ts = o.optLong("ts", 0);
        if (ts <= 0) return 0;
        // 兼容秒级 epoch（10 位）与本地毫秒（13 位）
        return ts < 1_000_000_000_000L ? ts * 1000 : ts;
    }

    public synchronized int activeCount() { return active.size(); }

    /** 红点数：当前告警中用户还没看过的条数。 */
    public synchronized int unread() { return unreadActive.size(); }

    /** 进入告警页时调用：把未读数清零（当前告警本身保留）。 */
    public synchronized void markAllRead() {
        if (unreadActive.isEmpty()) return;
        unreadActive.clear();
        if (persistence != null) persistence.markAllRead();
        notifyObservers();
    }

    public synchronized void clear() {
        items.clear();
        active.clear();
        unreadActive.clear();
        if (persistence != null) persistence.clear();
        notifyObservers();
    }

    /** 活动告警的身份：设备 + metric + alarm_type。 */
    static String activeKey(JSONObject o) {
        if (o == null) return "";
        String metric = o.optString("metric", "").trim();
        String type = o.optString("alarm_type", "").trim();
        if (metric.isEmpty() && type.isEmpty()) return "";
        // 设备段必须带上，否则多台设备上同名的指标会互相覆盖
        String device = firstNonEmpty(o.optString("device_id", ""),
                o.optString("uuid", ""), o.optString("hostname", ""));
        return device + "|" + metric + "|" + type;
    }

    private void trim() {
        while (items.size() > MAX_ITEMS) items.remove(items.size() - 1);
        // 历史被裁剪不影响仍在告警的卡片，但也不能让异常数据把活动集合撑爆
        while (active.size() > MAX_ACTIVE) {
            Iterator<Map.Entry<String, JSONObject>> it = active.entrySet().iterator();
            if (!it.hasNext()) break;
            String key = it.next().getKey();
            it.remove();
            unreadActive.remove(key);
        }
    }

    /**
     * 从全量流水重建「当前告警」：从新到旧扫描，某个 key 第一次出现的 event 决定它的状态，
     * 后续同 key 的记录一律跳过（等价于「最近一条 alarm 且其后无 recover」）。
     */
    private void rebuildActive() {
        active.clear();
        Set<String> decided = new HashSet<>();
        for (JSONObject o : items) {
            String event = o.optString("event", "");
            if (!EVENT_ALARM.equals(event) && !EVENT_RECOVER.equals(event)) continue;
            String key = activeKey(o);
            if (key.isEmpty() || !decided.add(key)) continue;
            if (EVENT_ALARM.equals(event)) active.put(key, o);
        }
    }

    private static boolean containsPayload(List<JSONObject> list, JSONObject o) {
        if (o == null) return true;
        String payload = o.toString();
        for (JSONObject item : list) {
            if (payload.equals(item.toString())) return true;
        }
        return false;
    }

    private static String firstNonEmpty(String a, String b, String c) {
        if (a != null && !a.isEmpty()) return a;
        if (b != null && !b.isEmpty()) return b;
        return c == null ? "" : c;
    }

    private void notifyObservers() {
        for (Observer o : observers) o.onAlarmsChanged();
    }
}
