package www.walle.ai.data;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;
import android.util.Log;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.atomic.AtomicBoolean;

import www.walle.ai.net.AlarmStore;
import www.walle.ai.net.DeviceRegistry;

/**
 * 本地持久化：告警收件箱 + 每台设备最近一次状态快照。
 *
 * <p>为什么直接用 SQLite 而不是 Room：本项目是纯 Java 且只有两张表、没有任何关联查询，
 * Room 会额外引入 annotation processor + schema.json + 增量构建依赖；用
 * {@link SQLiteOpenHelper} 三个方法就能覆盖，且不增加一行业务代码的编译期魔法。
 * 等迁 Kotlin（技术债 I1）时再评估 Room/KSP。</p>
 *
 * <p>线程模型：<b>读同步、写异步</b>。启动时读一遍缓存（几毫秒，换来界面不白屏），
 * 运行期所有写入都丢给单线程 {@code walle-db} 执行器，绝不阻塞主线程。</p>
 *
 * <p>任何数据库异常（磁盘满、库损坏、加解密失败）都在这里被吞掉并降级为「仅内存」，
 * 告警功能本身不受影响——持久化是增强，不是依赖。</p>
 */
public class LocalDb implements AlarmStore.Persistence, DeviceRegistry.StatusCache {

    private static final String TAG = "LocalDb";
    private static final String DB_NAME = "walle.db";
    /** 表结构变更时 +1，并在 {@link Helper#onUpgrade} 里补迁移。 */
    private static final int DB_VERSION = 1;

    /** 告警最多保留条数（与 {@link AlarmStore#MAX_ITEMS} 对齐）。 */
    private static final int MAX_ALARMS = AlarmStore.MAX_ITEMS;
    /** 状态快照按设备各存一条，再多就是历史设备了。 */
    private static final int MAX_STATUSES = 50;

    private static volatile LocalDb instance;

    /**
     * 初始化（幂等）。由 {@code MqttManager} 构造时调用：那里一定有 Application Context，
     * 且是全 App 最早拿到 Context 的入口。
     *
     * @return 已就绪的实例
     */
    public static LocalDb init(Context ctx) {
        if (instance == null) {
            synchronized (LocalDb.class) {
                if (instance == null) {
                    instance = new LocalDb(ctx.getApplicationContext());
                }
            }
        }
        return instance;
    }

    /** 未初始化时返回 null（单测、极早调用）；调用方必须容忍 null。 */
    public static LocalDb get() {
        return instance;
    }

    private final Helper helper;
    private final ExecutorService writer;
    /** 状态写入合并：同一设备只保留最新一条，避免每收一条报文就落一次盘。 */
    private final Map<String, JSONObject> pendingStatus = new ConcurrentHashMap<>();
    private final AtomicBoolean drainScheduled = new AtomicBoolean();

    private LocalDb(Context ctx) {
        helper = new Helper(ctx);
        writer = Executors.newSingleThreadExecutor(r -> {
            Thread t = new Thread(r, "walle-db");
            t.setDaemon(true);
            return t;
        });
    }

    // ---------------------------------------------------------------- 告警

    @Override
    public void insert(JSONObject alarm, boolean read) {
        if (alarm == null) return;
        final String payload = alarm.toString();
        final long ts = alarm.optLong("ts", System.currentTimeMillis());
        final String device = firstNonEmpty(alarm.optString("uuid", ""),
                alarm.optString("device_id", ""));
        submit(() -> {
            SQLiteDatabase db = writable();
            if (db == null) return;
            try {
                ContentValues cv = new ContentValues();
                cv.put("ts", ts);
                cv.put("device", device);
                cv.put("read", read ? 1 : 0);
                cv.put("payload", payload);
                db.insert("alarm", null, cv);
                db.execSQL("DELETE FROM alarm WHERE _id NOT IN "
                        + "(SELECT _id FROM alarm ORDER BY _id DESC LIMIT " + MAX_ALARMS + ")");
            } catch (Throwable t) {
                Log.w(TAG, "写入告警失败（降级为仅内存）: " + t);
            }
        });
    }

    @Override
    public void markAllRead() {
        submit(() -> {
            SQLiteDatabase db = writable();
            if (db == null) return;
            try {
                db.execSQL("UPDATE alarm SET read = 1 WHERE read = 0");
            } catch (Throwable t) {
                Log.w(TAG, "标记已读失败: " + t);
            }
        });
    }

    @Override
    public void clear() {
        submit(() -> {
            SQLiteDatabase db = writable();
            if (db == null) return;
            try {
                db.delete("alarm", null, null);
            } catch (Throwable t) {
                Log.w(TAG, "清空告警失败: " + t);
            }
        });
    }

    @Override
    public List<JSONObject> load(int limit) {
        List<JSONObject> out = new ArrayList<>();
        SQLiteDatabase db = readable();
        if (db == null) return out;
        Cursor c = null;
        try {
            c = db.query("alarm", new String[]{"payload"}, null, null, null, null,
                    "_id DESC", String.valueOf(Math.max(1, limit)));
            while (c.moveToNext()) {
                JSONObject o = parse(c.getString(0));
                if (o != null) out.add(o);
            }
        } catch (Throwable t) {
            Log.w(TAG, "读取告警失败: " + t);
        } finally {
            close(c);
        }
        return out;
    }

    @Override
    public int unreadCount() {
        SQLiteDatabase db = readable();
        if (db == null) return 0;
        Cursor c = null;
        try {
            c = db.rawQuery("SELECT COUNT(*) FROM alarm WHERE read = 0", null);
            return c.moveToFirst() ? c.getInt(0) : 0;
        } catch (Throwable t) {
            Log.w(TAG, "统计未读失败: " + t);
            return 0;
        } finally {
            close(c);
        }
    }

    // ---------------------------------------------------------------- 状态

    @Override
    public void save(String key, JSONObject status) {
        if (key == null || key.isEmpty() || status == null) return;
        pendingStatus.put(key, status);
        scheduleDrain();
    }

    @Override
    public Map<String, JSONObject> loadAll() {
        Map<String, JSONObject> out = new LinkedHashMap<>();
        SQLiteDatabase db = readable();
        if (db == null) return out;
        Cursor c = null;
        try {
            c = db.query("status", new String[]{"_key", "payload"}, null, null, null, null,
                    "ts DESC");
            while (c.moveToNext()) {
                JSONObject o = parse(c.getString(1));
                if (o != null) out.put(c.getString(0), o);
            }
        } catch (Throwable t) {
            Log.w(TAG, "读取状态缓存失败: " + t);
        } finally {
            close(c);
        }
        return out;
    }

    /** 把合并后的状态批量落盘；期间再来新值就再跑一轮。 */
    private void scheduleDrain() {
        if (!drainScheduled.compareAndSet(false, true)) return;
        submit(() -> {
            drainScheduled.set(false);
            SQLiteDatabase db = writable();
            if (db == null) return;
            Map<String, JSONObject> batch = new LinkedHashMap<>(pendingStatus);
            pendingStatus.clear();
            try {
                db.beginTransaction();
                for (Map.Entry<String, JSONObject> e : batch.entrySet()) {
                    ContentValues cv = new ContentValues();
                    cv.put("_key", e.getKey());
                    cv.put("ts", e.getValue().optLong("ts", System.currentTimeMillis()));
                    cv.put("payload", e.getValue().toString());
                    db.insertWithOnConflict("status", null, cv,
                            SQLiteDatabase.CONFLICT_REPLACE);
                }
                db.execSQL("DELETE FROM status WHERE _key NOT IN "
                        + "(SELECT _key FROM status ORDER BY ts DESC LIMIT " + MAX_STATUSES + ")");
                db.setTransactionSuccessful();
            } catch (Throwable t) {
                Log.w(TAG, "写入状态缓存失败（降级为仅内存）: " + t);
            } finally {
                try {
                    db.endTransaction();
                } catch (Throwable ignored) {
                }
            }
        });
    }

    // ---------------------------------------------------------------- 内部

    private void submit(Runnable task) {
        try {
            writer.execute(task);
        } catch (RejectedExecutionException e) {
            Log.w(TAG, "写库队列已关闭，丢弃一次写入");
        }
    }

    private SQLiteDatabase writable() {
        try {
            return helper.getWritableDatabase();
        } catch (Throwable t) {
            Log.w(TAG, "打不开数据库（降级为仅内存）: " + t);
            return null;
        }
    }

    private SQLiteDatabase readable() {
        try {
            return helper.getReadableDatabase();
        } catch (Throwable t) {
            Log.w(TAG, "打不开数据库（降级为仅内存）: " + t);
            return null;
        }
    }

    private static JSONObject parse(String json) {
        if (json == null || json.isEmpty()) return null;
        try {
            return new JSONObject(json);
        } catch (Exception e) {
            return null;
        }
    }

    private static void close(Cursor c) {
        if (c != null) {
            try {
                c.close();
            } catch (Throwable ignored) {
            }
        }
    }

    private static String firstNonEmpty(String a, String b) {
        return a != null && !a.isEmpty() ? a : (b == null ? "" : b);
    }

    /** 建表 / 升级。所有 DDL 都写成幂等的形式，避免升级路径上二次建表失败。 */
    private static final class Helper extends SQLiteOpenHelper {

        Helper(Context ctx) {
            super(ctx, DB_NAME, null, DB_VERSION);
        }

        @Override
        public void onCreate(SQLiteDatabase db) {
            db.execSQL("CREATE TABLE IF NOT EXISTS alarm ("
                    + "_id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    + "ts INTEGER NOT NULL DEFAULT 0,"
                    + "device TEXT NOT NULL DEFAULT '',"
                    + "read INTEGER NOT NULL DEFAULT 0,"
                    + "payload TEXT NOT NULL)");
            db.execSQL("CREATE TABLE IF NOT EXISTS status ("
                    + "_key TEXT PRIMARY KEY,"
                    + "ts INTEGER NOT NULL DEFAULT 0,"
                    + "payload TEXT NOT NULL)");
            db.execSQL("CREATE INDEX IF NOT EXISTS idx_alarm_read ON alarm(read)");
        }

        @Override
        public void onUpgrade(SQLiteDatabase db, int oldVersion, int newVersion) {
            // v1 是首版。后续加字段时在这里按版本号 ALTER TABLE，不要 DROP 用户数据。
            Log.i(TAG, "数据库升级 " + oldVersion + " -> " + newVersion);
        }
    }
}
