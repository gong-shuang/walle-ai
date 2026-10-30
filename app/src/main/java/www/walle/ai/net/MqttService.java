package www.walle.ai.net;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;
import android.util.Log;

import androidx.annotation.Nullable;
import androidx.core.app.NotificationCompat;

import www.walle.ai.R;

/**
 * 前台服务：持有 MQTT 连接的生命周期，常驻通知降低后台被回收概率。
 * 实际连接逻辑在 MqttManager 单例中，UI 直接监听该单例。
 */
public class MqttService extends Service {

    private static final String TAG = "MqttService";
    private static final String CH_ID = "mqtt_service";
    private static final int NOTIF_ID = 1001;
    public static final String ACTION_START = "www.walle.ai.START";
    public static final String ACTION_STOP = "www.walle.ai.STOP";

    public static void start(Context ctx) {
        Intent i = new Intent(ctx.getApplicationContext(), MqttService.class)
                .setAction(ACTION_START);
        ctx.getApplicationContext().startForegroundService(i);
    }

    public static void stop(Context ctx) {
        Intent i = new Intent(ctx.getApplicationContext(), MqttService.class)
                .setAction(ACTION_STOP);
        ctx.getApplicationContext().startService(i);
    }

    @Override
    public void onCreate() {
        super.onCreate();
        createChannel();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? null : intent.getAction();
        Log.i(TAG, "onStartCommand action=" + action + " startId=" + startId);
        if (ACTION_STOP.equals(action)) {
            Log.i(TAG, "收到 STOP，断开 MQTT 并停止前台服务");
            MqttManager.get(this).disconnect();
            stopForeground(true);
            stopSelf();
            return START_NOT_STICKY;
        }

        MqttManager mgr = MqttManager.get(this);
        // 本地还没有用户名/密码时不建立连接（也不驻留前台通知），由设置页引导用户输入
        if (!mgr.hasCredentials()) {
            Log.i(TAG, "本地无 MQTT 凭据，跳过前台服务（由设置页引导用户输入）");
            stopForeground(true);
            stopSelf();
            return START_NOT_STICKY;
        }
        startAsForeground();
        if (!mgr.isConnectedOrConnecting()) {
            mgr.connect();
        }
        return START_STICKY;
    }

    private void startAsForeground() {
        Notification n = new NotificationCompat.Builder(this, CH_ID)
                .setContentTitle(getString(R.string.app_name))
                .setContentText("MQTT 连接保活中")
                .setSmallIcon(R.drawable.ic_stat_link)
                .setOngoing(true)
                .setPriority(NotificationCompat.PRIORITY_LOW)
                .build();
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            // API 26–28 还没有前台服务类型
            startForeground(NOTIF_ID, n);
            return;
        }
        // connectedDevice 语义最贴合「与外部设备保持长连接」，且不受 Android 15 起
        // dataSync 的 6 小时/天 配额限制（超限会被系统 onTimeout 掐掉）。
        try {
            startForeground(NOTIF_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE);
            Log.i(TAG, "前台服务已启动：type=connectedDevice（不受 dataSync 6h/天配额限制）");
            return;
        } catch (Exception e) {
            Log.w(TAG, "connectedDevice 前台服务被拒绝，回退 dataSync", e);
        }
        try {
            startForeground(NOTIF_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
            Log.w(TAG, "前台服务已回退为 type=dataSync（Android 15+ 有 6 小时/天配额）");
            return;
        } catch (Exception e) {
            Log.e(TAG, "dataSync 也被拒绝，退化为无类型前台服务", e);
        }
        startForeground(NOTIF_ID, n);
        Log.w(TAG, "前台服务已退化为无类型（API<29 或系统拒绝指定类型）");
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel ch = new NotificationChannel(
                    CH_ID, "MQTT 服务", NotificationManager.IMPORTANCE_LOW);
            ch.setDescription("保持与树莓派的 MQTT 连接");
            NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
            if (nm != null) nm.createNotificationChannel(ch);
        }
    }

    @Override
    public void onDestroy() {
        super.onDestroy();
    }

    @Nullable
    @Override
    public IBinder onBind(Intent intent) { return null; }
}
