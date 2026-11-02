package www.walle.ai.net;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.json.JSONException;
import org.json.JSONObject;
import org.junit.Test;

import java.util.ArrayList;
import java.util.List;

/**
 * 告警收件箱测试：当前告警 / 历史流水两条数据线 + 落盘端口契约。
 *
 * <p>用假的 {@link AlarmStore.Persistence} 驱动，不需要 Android/SQLite，
 * 因此在纯 JVM 单测里就能锁住「重启后历史还在、当前告警能重建、未读数还准」这条规则。</p>
 */
public class AlarmStoreTest {

    /** 内存版持久化，模拟「进程被杀、App 重启」。 */
    private static final class FakePersistence implements AlarmStore.Persistence {
        final List<JSONObject> rows = new ArrayList<>();
        final List<JSONObject> unreadRows = new ArrayList<>();
        boolean cleared;
        int markAllReadCalls;

        @Override
        public void insert(JSONObject alarm, boolean read) {
            rows.add(0, alarm);
            if (!read) unreadRows.add(0, alarm);
        }

        @Override
        public void markAllRead() {
            markAllReadCalls++;
            unreadRows.clear();
        }

        @Override
        public void clear() {
            rows.clear();
            unreadRows.clear();
            cleared = true;
        }

        @Override
        public List<JSONObject> load(int limit) {
            return new ArrayList<>(rows.subList(0, Math.min(limit, rows.size())));
        }

        @Override
        public List<JSONObject> loadUnread(int limit) {
            return new ArrayList<>(unreadRows.subList(0, Math.min(limit, unreadRows.size())));
        }
    }

    /** 设备端 _event() 的报文形状：event / level / metric / alarm_type / device_id / ts。 */
    private static JSONObject event(String event, String device, String metric, String type, int ts) {
        try {
            return new JSONObject()
                    .put("ts", ts)
                    .put("event", event)
                    .put("level", "warning")
                    .put("metric", metric)
                    .put("alarm_type", type)
                    .put("device_id", device)
                    .put("hostname", device)
                    .put("message", event + "-" + metric + "-" + ts);
        } catch (JSONException e) {
            throw new IllegalArgumentException(e);
        }
    }

    private static JSONObject alarm(int ts) {
        return event("alarm", "d1", "cpu", "cpu_high", ts);
    }

    private static JSONObject alarm(String device, String metric, int ts) {
        return event("alarm", device, metric, metric + "_high", ts);
    }

    private static JSONObject recover(int ts) {
        return event("recover", "d1", "cpu", "cpu_high", ts);
    }

    /** 覆盖 ts 为指定值（毫秒或秒都可以，isToday 会按数量级判断）。 */
    private static JSONObject withTs(JSONObject base, long ts) {
        try {
            base.put("ts", ts);
            return base;
        } catch (JSONException e) {
            throw new IllegalArgumentException(e);
        }
    }

    /** 设备原始报文形状：没有 ts，只有 datetime 字符串。 */
    private static JSONObject withoutTs(String datetime) {
        try {
            return new JSONObject()
                    .put("event", "alarm")
                    .put("level", "warning")
                    .put("metric", "cpu")
                    .put("alarm_type", "cpu_high")
                    .put("device_id", "d1")
                    .put("datetime", datetime);
        } catch (JSONException e) {
            throw new IllegalArgumentException(e);
        }
    }

    @Test
    public void alarmAddsToActiveAndHistoryAndCountsUnread() {
        FakePersistence p = new FakePersistence();
        AlarmStore store = new AlarmStore();
        store.attach(p);

        store.add(alarm(1));
        store.add(alarm("d1", "memory", 2));

        assertEquals("历史保留全部事件", 2, store.items().size());
        assertEquals("当前告警两张卡片", 2, store.activeAlarms().size());
        assertEquals(2, store.activeCount());
        assertEquals("红点只统计当前告警", 2, store.unread());
        assertEquals(2, p.rows.size());
    }

    @Test
    public void sameKeyAlarmUpdatesCardWithoutDuplicating() {
        AlarmStore store = new AlarmStore();

        store.add(alarm(1));
        store.add(alarm(2));

        assertEquals("同一设备同一指标同一类型只保留一张卡片", 1, store.activeAlarms().size());
        assertEquals("卡片取最新一条", 2, store.activeAlarms().get(0).optInt("ts"));
        assertEquals("卡片更新不再重复计未读", 1, store.unread());
        assertEquals("历史仍保留两条", 2, store.items().size());
    }

    @Test
    public void recoverRemovesFromActiveButKeepsHistory() {
        AlarmStore store = new AlarmStore();

        store.add(alarm(1));
        store.add(recover(2));

        assertTrue(store.activeAlarms().isEmpty());
        assertEquals("恢复后红点一起消失", 0, store.unread());
        assertEquals("历史里 alarm 与 recover 都在", 2, store.items().size());
        assertEquals(2, store.items().get(0).optInt("ts"));
    }

    @Test
    public void usbEventsOnlyGoToHistory() {
        AlarmStore store = new AlarmStore();

        store.add(event("usb_connect", "d1", "usb", "usb_connect", 1));
        store.add(event("usb_disconnect", "d1", "usb", "usb_disconnect", 2));

        assertTrue("USB 插拔不算当前告警", store.activeAlarms().isEmpty());
        assertEquals(0, store.unread());
        assertEquals(2, store.items().size());
    }

    @Test
    public void markAllReadClearsBadgeButKeepsActiveAlarms() {
        FakePersistence p = new FakePersistence();
        AlarmStore store = new AlarmStore();
        store.attach(p);
        store.add(alarm(1));

        store.markAllRead();

        assertEquals(0, store.unread());
        assertEquals("只是标记已读，告警本身还在", 1, store.activeAlarms().size());
        assertEquals(1, p.markAllReadCalls);
        // 重复调用不再写盘
        store.markAllRead();
        assertEquals(1, p.markAllReadCalls);
    }

    @Test
    public void clearEmptiesBothListsAndDisk() {
        FakePersistence p = new FakePersistence();
        AlarmStore store = new AlarmStore();
        store.attach(p);
        store.add(alarm(1));
        store.add(recover(2));

        store.clear();

        assertTrue(store.items().isEmpty());
        assertTrue(store.activeAlarms().isEmpty());
        assertEquals(0, store.unread());
        assertTrue(p.cleared);
        assertTrue(p.rows.isEmpty());
    }

    @Test
    public void memoryIsCappedAtMaxItems() {
        AlarmStore store = new AlarmStore();
        for (int i = 0; i < AlarmStore.MAX_ITEMS + 20; i++) store.add(alarm(i));

        assertEquals(AlarmStore.MAX_ITEMS, store.items().size());
        // 最旧的那批已被挤掉，队首还是最新的
        assertEquals(AlarmStore.MAX_ITEMS + 19, store.items().get(0).optInt("ts"));
        // 反复刷同一个 key 不会撑大活动集合
        assertEquals(1, store.activeCount());
    }

    @Test
    public void attachRestoresHistoryAcrossRestart() {
        FakePersistence p = new FakePersistence();
        AlarmStore first = new AlarmStore();
        first.attach(p);
        first.add(alarm(1));
        first.add(recover(2));

        // —— 进程被杀，App 重启：内存清空，只剩磁盘 ——
        AlarmStore second = new AlarmStore();
        second.attach(p);

        assertEquals(2, second.items().size());
        assertEquals(2, second.items().get(0).optInt("ts"));
        assertTrue("已恢复的告警不应再出现在当前", second.activeAlarms().isEmpty());
    }

    @Test
    public void restartRebuildsActiveFromHistory() {
        FakePersistence p = new FakePersistence();
        AlarmStore first = new AlarmStore();
        first.attach(p);
        // 先恢复一条旧告警，再有一条新的未恢复告警（历史里新的在前）
        first.add(recover(1));
        first.add(alarm(2));

        AlarmStore second = new AlarmStore();
        second.attach(p);

        assertEquals("重启后仍能重建当前告警", 1, second.activeAlarms().size());
        assertEquals(2, second.activeAlarms().get(0).optInt("ts"));
        assertEquals(2, second.items().size());
    }

    @Test
    public void unreadRestoredOnlyForStillActiveAlarms() {
        FakePersistence p = new FakePersistence();
        AlarmStore first = new AlarmStore();
        first.attach(p);
        first.add(alarm(1));        // cpu 告警
        first.add(recover(2));      // cpu 恢复
        first.add(alarm("d1", "memory", 3)); // memory 仍告警
        first.add(event("usb_connect", "d1", "usb", "usb_connect", 4)); // 只进历史

        AlarmStore second = new AlarmStore();
        second.attach(p);

        assertEquals("只有仍未恢复且未读的告警点亮红点", 1, second.unread());
        assertEquals("当前只剩 memory", "memory", second.activeAlarms().get(0).optString("metric"));
    }

    @Test
    public void multiDeviceSameMetricDoesNotCollide() {
        AlarmStore store = new AlarmStore();

        store.add(alarm("d1", "cpu", 1));
        store.add(alarm("d2", "cpu", 2));

        assertEquals("不同设备的同名指标各自一张卡片", 2, store.activeAlarms().size());
    }

    @Test
    public void todayOnlyKeepsItemsReceivedToday() {
        AlarmStore store = new AlarmStore();
        long now = System.currentTimeMillis();
        store.add(withTs(alarm(0), now));
        store.add(withTs(alarm("d1", "memory", 0), now - 3L * 24 * 3600 * 1000));

        List<JSONObject> today = store.todayAlarms();
        assertEquals("跨天的记录只进历史", 1, today.size());
        assertEquals("cpu", today.get(0).optString("metric"));
        assertEquals("历史仍保留两条", 2, store.items().size());
    }

    @Test
    public void historyExcludesTodayRecords() {
        AlarmStore store = new AlarmStore();
        long now = System.currentTimeMillis();
        store.add(withTs(alarm(0), now));
        store.add(withTs(alarm("d1", "memory", 0), now - 3L * 24 * 3600 * 1000));

        assertEquals("今日只留今天", 1, store.todayAlarms().size());
        assertEquals("历史不含当天", 1, store.historyAlarms().size());
        assertEquals("memory", store.historyAlarms().get(0).optString("metric"));
        assertEquals("内存流水仍保留两条（落盘与重建用）", 2, store.items().size());
    }

    @Test
    public void todayAcceptsSecondLevelEpochAndAllEventTypes() {
        AlarmStore store = new AlarmStore();
        long nowSec = System.currentTimeMillis() / 1000;
        store.add(withTs(alarm(0), nowSec));                 // 秒级 epoch
        store.add(withTs(recover(0), nowSec));               // 恢复事件也算今日记录
        store.add(withTs(event("usb_connect", "d1", "usb", "usb_connect", 0), nowSec));

        assertEquals(3, store.todayAlarms().size());
    }

    @Test
    public void addStampsLocalReceiptTimeWhenDeviceOmitsIt() {
        AlarmStore store = new AlarmStore();
        store.add(withoutTs(java.time.LocalDate.now() + " 09:00:00"));

        JSONObject item = store.items().get(0);
        assertTrue("没有 ts 时应补本地接收时间", item.has("ts"));
        assertTrue(item.optLong("ts", 0) > 0);
        assertEquals("补上 ts 后能进入今日页", 1, store.todayAlarms().size());
    }

    @Test
    public void attachIsIdempotentAndNotifiesObservers() {
        FakePersistence p = new FakePersistence();
        p.rows.add(alarm(9));
        AlarmStore store = new AlarmStore();
        final int[] notified = {0};
        store.addObserver(() -> notified[0]++);

        store.attach(p);
        store.attach(p);   // 第二次应无副作用

        assertEquals(1, store.items().size());
        assertEquals(1, notified[0]);
    }

    @Test
    public void brokenPersistenceDegradesToMemoryOnly() {
        AlarmStore store = new AlarmStore();
        store.attach(new AlarmStore.Persistence() {
            @Override public void insert(JSONObject alarm, boolean read) {}
            @Override public void markAllRead() {}
            @Override public void clear() {}
            @Override public List<JSONObject> load(int limit) { throw new IllegalStateException("库坏了"); }
        });

        store.add(alarm(1));

        assertEquals(1, store.items().size());
        assertEquals(1, store.activeAlarms().size());
        assertEquals(1, store.unread());
    }

    @Test
    public void withoutPersistenceBehaviourIsUnchanged() {
        AlarmStore store = new AlarmStore();
        store.add(alarm(1));
        assertEquals(1, store.items().size());
        assertEquals(1, store.unread());
        store.markAllRead();
        assertEquals(0, store.unread());
        store.clear();
        assertTrue(store.items().isEmpty());
        assertFalse(store.items().iterator().hasNext());
        assertTrue(store.activeAlarms().isEmpty());
    }

    @Test
    public void duplicatedStoredAlarmIsNotInsertedTwice() {
        FakePersistence p = new FakePersistence();
        AlarmStore store = new AlarmStore();
        store.add(alarm(1));      // 内存里已有一条
        p.rows.add(alarm(1));     // 磁盘上同一条（attach 之后又落盘的场景）
        store.attach(p);

        assertEquals(1, store.items().size());
    }

    @Test
    public void attachTrimsOversizedHistory() {
        // 即使实现方不老实（无视 limit 返回全部），attach 后内存也不会超过上限
        AlarmStore store = new AlarmStore();
        final List<JSONObject> tooMany = new ArrayList<>();
        for (int i = 0; i < AlarmStore.MAX_ITEMS + 5; i++) tooMany.add(alarm(i));
        store.attach(new AlarmStore.Persistence() {
            @Override public void insert(JSONObject alarm, boolean read) {}
            @Override public void markAllRead() {}
            @Override public void clear() {}
            @Override public List<JSONObject> load(int limit) { return new ArrayList<>(tooMany); }
        });

        assertEquals(AlarmStore.MAX_ITEMS, store.items().size());
        // 全部是同一个 key，重建后仍只有一张卡片
        assertEquals(1, store.activeCount());
    }
}
