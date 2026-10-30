package www.walle.ai.net;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;

/**
 * 全局告警收件箱：跨页面共享告警列表与未读数。
 * 所有方法都在主线程调用（MqttManager 已把回调切到主线程）。
 */
public class AlarmStore {

    public interface Observer {
        void onAlarmsChanged();
    }

    private static final AlarmStore INSTANCE = new AlarmStore();
    private static final int MAX_ITEMS = 200;

    public static AlarmStore get() { return INSTANCE; }

    private final List<JSONObject> items = new ArrayList<>();
    private final CopyOnWriteArrayList<Observer> observers = new CopyOnWriteArrayList<>();
    private int unread;

    private AlarmStore() {}

    public void addObserver(Observer o) { if (o != null && !observers.contains(o)) observers.add(o); }

    public void removeObserver(Observer o) { observers.remove(o); }

    public synchronized void add(JSONObject alarm) {
        if (alarm == null) return;
        items.add(0, alarm);
        while (items.size() > MAX_ITEMS) items.remove(items.size() - 1);
        unread++;
        notifyObservers();
    }

    public synchronized List<JSONObject> items() { return new ArrayList<>(items); }

    public synchronized int unread() { return unread; }

    /** 进入告警页时调用：把未读数清零。 */
    public synchronized void markAllRead() {
        if (unread == 0) return;
        unread = 0;
        notifyObservers();
    }

    public synchronized void clear() {
        items.clear();
        unread = 0;
        notifyObservers();
    }

    private void notifyObservers() {
        for (Observer o : observers) o.onAlarmsChanged();
    }
}
