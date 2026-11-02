package www.walle.ai.data;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;
import android.util.Log;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.RejectedExecutionException;

import www.walle.ai.net.AlarmStore;

/**
 * 本地告警持久化（技术债 N11）。
 *
 * <p>只存告警：设备状态（state/result）<b>不落盘</b>——它表示「当前时刻这台设备的状态」，
 * 只有在选中一台在线设备、设备回包之后才有意义；缓存下来的旧快照会变成「看起来在线、数据却是昨天的」
 * 假象。设备列表本来就只展示在线设备，状态页每次都是现查。</p>
 *
 * <p>为什么直接用 SQLite 而不是 Room：本项目是纯 Java 且只有一张表、没有任何关联查询，
 * Room 会额外引入 annotation processor + schema.json + 增量构建依赖；用
 * {@link SQLiteOpenHelper} 三个方法就能覆盖，且不增加一行业务代码的编译期魔法。
 * 等迁 Kotlin（技术债 I1）时再评估 Room/KSP。</p>
 *
 * <p>线程模型：<b>读同步、写异步</b>。启动时同步读回告警历史（几毫秒，换来红点与列表不空），
 * 运行期所有写入都丢给单线程 {@code walle-db} 执行器，绝不阻塞主线程。</p>
 *
 * <p>任何数据库异常（磁盘满、库损坏、加解密失败）都在这里被吞掉并降级为「仅内存」，
 * 告警功能本身不受影响——持久化是增强，不是依赖。</p>
 */
public class LocalDb implements AlarmStore.Persistence {

    private static final String TAG = "LocalDb";
    private static final String DB_NAME = "walle.db";
    /** 表结构变更时 +1，并在 {@link Helper#onUpgrade} 里补迁移。 */
    private static final int DB_VERSION = 2;

    /** 告警最多保留条数（与 {@link AlarmStore#MAX_ITEMS} 对齐）。 */
    private static final int MAX_ALARMS = AlarmStore.MAX_ITEMS;

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
            c = db.query("alarm", new String[]{"payload", "ts"}, null, null, null, null,
                    "_id DESC", String.valueOf(Math.max(1, limit)));
            while (c.moveToNext()) {
                JSONObject o = withTimestamp(c.getString(0), c.getLong(1));
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
    public List<JSONObject> loadUnread(int limit) {
        List<JSONObject> out = new ArrayList<>();
        SQLiteDatabase db = readable();
        if (db == null) return out;
        Cursor c = null;
        try {
            c = db.query("alarm", new String[]{"payload", "ts"}, "read = 0", null, null, null,
                    "_id DESC", String.valueOf(Math.max(1, limit)));
            while (c.moveToNext()) {
                JSONObject o = withTimestamp(c.getString(0), c.getLong(1));
                if (o != null) out.add(o);
            }
        } catch (Throwable t) {
            Log.w(TAG, "读取未读告警失败: " + t);
        } finally {
            close(c);
        }
        return out;
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

    /** 解析报文并补上落盘时的接收时间：老记录的 payload 里没有 ts，ts 只在列里。 */
    private static JSONObject withTimestamp(String json, long ts) {
        JSONObject o = parse(json);
        if (o != null && !o.has("ts") && ts > 0) {
            try {
                o.put("ts", ts);
            } catch (Exception ignored) {
            }
        }
        return o;
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
            db.execSQL("CREATE INDEX IF NOT EXISTS idx_alarm_read ON alarm(read)");
        }

        @Override
        public void onUpgrade(SQLiteDatabase db, int oldVersion, int newVersion) {
            Log.i(TAG, "数据库升级 " + oldVersion + " -> " + newVersion);
            if (oldVersion < 2) {
                // v1 曾短暂存过设备状态快照（state/result）。状态只对「当前在线设备」有意义，
                // 落盘的旧快照会让人误以为设备在线且数据新鲜，故整表删除。
                db.execSQL("DROP TABLE IF EXISTS status");
            }
        }
    }
}
