package www.walle.ai.net;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;

/**
 * 全局告警收件箱：跨页面共享告警列表与未读数。
 *
 * <p>所有方法都在主线程调用（MqttManager 已把回调切到主线程）。</p>
 *
 * <p>持久化（技术债 N11）：内存是权威数据源，{@link Persistence} 只是旁路落盘。
 * 进程被杀后再次启动时，{@link #attach} 会把历史告警读回来，所以「设置页红点」和
 * 「告警列表」不会因为杀进程而清零。没挂持久化（单测、初始化失败）时行为与原来一致。</p>
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

        /** 还未 markAllRead 的条数。 */
        int unreadCount();
    }

    /** 内存里保留的最大告警条数。 */
    public static final int MAX_ITEMS = 200;

    private static final AlarmStore INSTANCE = new AlarmStore();

    public static AlarmStore get() { return INSTANCE; }

    private final List<JSONObject> items = new ArrayList<>();
    private final CopyOnWriteArrayList<Observer> observers = new CopyOnWriteArrayList<>();
    private int unread;
    private Persistence persistence;

    /** 包内可见：单测需要干净实例，生产统一走 {@link #get()}。 */
    AlarmStore() {}

    public void addObserver(Observer o) { if (o != null && !observers.contains(o)) observers.add(o); }

    public void removeObserver(Observer o) { observers.remove(o); }

    /**
     * 挂上持久化（幂等）：立刻把历史告警读回内存，并叠加未读数。
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
            int storedUnread = p.unreadCount();
            if (storedUnread > 0) unread += storedUnread;
        } catch (Throwable t) {
            // 持久化坏了不能影响告警功能本身：退回纯内存模式
            persistence = null;
            return;
        }
        notifyObservers();
    }

    public synchronized void add(JSONObject alarm) {
        if (alarm == null) return;
        items.add(0, alarm);
        trim();
        unread++;
        if (persistence != null) persistence.insert(alarm, false);
        notifyObservers();
    }

    public synchronized List<JSONObject> items() { return new ArrayList<>(items); }

    public synchronized int unread() { return unread; }

    /** 进入告警页时调用：把未读数清零。 */
    public synchronized void markAllRead() {
        if (unread == 0) return;
        unread = 0;
        if (persistence != null) persistence.markAllRead();
        notifyObservers();
    }

    public synchronized void clear() {
        items.clear();
        unread = 0;
        if (persistence != null) persistence.clear();
        notifyObservers();
    }

    private void trim() {
        while (items.size() > MAX_ITEMS) items.remove(items.size() - 1);
    }

    private static boolean containsPayload(List<JSONObject> list, JSONObject o) {
        if (o == null) return true;
        String payload = o.toString();
        for (JSONObject item : list) {
            if (payload.equals(item.toString())) return true;
        }
        return false;
    }

    private void notifyObservers() {
        for (Observer o : observers) o.onAlarmsChanged();
    }
}
