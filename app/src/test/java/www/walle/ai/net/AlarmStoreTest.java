package www.walle.ai.net;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.json.JSONObject;
import org.junit.Test;

import java.util.ArrayList;
import java.util.List;

/**
 * 告警收件箱测试（N11 持久化）：内存行为 + 落盘端口契约。
 *
 * <p>用假的 {@link AlarmStore.Persistence} 驱动，不需要 Android/SQLite，
 * 因此在纯 JVM 单测里就能锁住「重启后告警还在、未读数还准」这条规则。</p>
 */
public class AlarmStoreTest {

    /** 内存版持久化，模拟「进程被杀、App 重启」。 */
    private static final class FakePersistence implements AlarmStore.Persistence {
        final List<JSONObject> rows = new ArrayList<>();
        boolean cleared;
        int markAllReadCalls;

        @Override
        public void insert(JSONObject alarm, boolean read) {
            rows.add(0, alarm);
        }

        @Override
        public void markAllRead() {
            markAllReadCalls++;
        }

        @Override
        public void clear() {
            rows.clear();
            cleared = true;
        }

        @Override
        public List<JSONObject> load(int limit) {
            return new ArrayList<>(rows.subList(0, Math.min(limit, rows.size())));
        }

        @Override
        public int unreadCount() {
            return rows.isEmpty() ? 0 : 1;
        }
    }

    private static JSONObject alarm(int i) {
        try {
            return new JSONObject("{\"ts\":" + i + ",\"level\":\"warn\",\"message\":\"m" + i + "\"}");
        } catch (Exception e) {
            throw new IllegalArgumentException(e);
        }
    }

    @Test
    public void addPrependsCountsUnreadAndPersists() {
        FakePersistence p = new FakePersistence();
        AlarmStore store = new AlarmStore();
        store.attach(p);

        store.add(alarm(1));
        store.add(alarm(2));

        assertEquals(2, store.items().size());
        assertEquals(2, store.unread());
        // 新的在前
        assertEquals(2, store.items().get(0).optInt("ts"));
        assertEquals(2, p.rows.size());
    }

    @Test
    public void memoryIsCappedAtMaxItems() {
        AlarmStore store = new AlarmStore();
        for (int i = 0; i < AlarmStore.MAX_ITEMS + 20; i++) store.add(alarm(i));

        assertEquals(AlarmStore.MAX_ITEMS, store.items().size());
        // 最旧的那批已被挤掉，队首还是最新的
        assertEquals(AlarmStore.MAX_ITEMS + 19, store.items().get(0).optInt("ts"));
    }

    @Test
    public void markAllReadClearsBadgeAndHitsDisk() {
        FakePersistence p = new FakePersistence();
        AlarmStore store = new AlarmStore();
        store.attach(p);
        store.add(alarm(1));

        store.markAllRead();

        assertEquals(0, store.unread());
        assertEquals(1, p.markAllReadCalls);
        // 重复调用不再写盘
        store.markAllRead();
        assertEquals(1, p.markAllReadCalls);
    }

    @Test
    public void clearEmptiesMemoryAndDisk() {
        FakePersistence p = new FakePersistence();
        AlarmStore store = new AlarmStore();
        store.attach(p);
        store.add(alarm(1));
        store.add(alarm(2));

        store.clear();

        assertTrue(store.items().isEmpty());
        assertEquals(0, store.unread());
        assertTrue(p.cleared);
        assertTrue(p.rows.isEmpty());
    }

    @Test
    public void attachRestoresHistoryAndUnreadAcrossRestart() {
        FakePersistence p = new FakePersistence();
        AlarmStore first = new AlarmStore();
        first.attach(p);
        first.add(alarm(1));
        first.add(alarm(2));

        // —— 进程被杀，App 重启：内存清空，只剩磁盘 ——
        AlarmStore second = new AlarmStore();
        second.attach(p);

        assertEquals(2, second.items().size());
        assertEquals(2, second.items().get(0).optInt("ts"));
        assertTrue("重启后未读数应从磁盘恢复", second.unread() > 0);
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
            @Override public int unreadCount() { return 0; }
        });

        store.add(alarm(1));

        assertEquals(1, store.items().size());
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
            @Override public int unreadCount() { return 0; }
        });

        assertEquals(AlarmStore.MAX_ITEMS, store.items().size());
    }
}
