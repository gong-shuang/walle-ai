/*
 * (C) Copyright 2015-2016 the original author or authors.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 *
 * Contributors:
 *     ohun@live.cn (夜色)
 */


package com.mpush.android;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.os.Build;
import android.os.IBinder;

import androidx.core.app.NotificationCompat;

import com.huawei.gspush.R;

/**
 * 双Service提高进程优先级,降低被系统杀死机率
 * <p>
 * Created by yxx on 2016/2/15.
 *
 * @author ohun@live.cn
 */
public final class MPushFakeService extends Service {
    public static final int NOTIFICATION_ID = 1001;

    public static void startForeground(Service service) {
        Intent intent = new Intent(service, MPushFakeService.class);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            //android8.0以上通过startForegroundService启动service
            service.startForegroundService(intent);
        } else {
            service.startService(intent);
        }
    }

    @Override
    public void onCreate() {
        super.onCreate();

        startNotificationForeground();
    }

    private void startNotificationForeground() {

        /**
         * Oreo不用Priority了，用importance
         * IMPORTANCE_NONE 关闭通知
         * IMPORTANCE_MIN 开启通知，不会弹出，但没有提示音，状态栏中无显示
         * IMPORTANCE_LOW 开启通知，不会弹出，不发出提示音，状态栏中显示
         * IMPORTANCE_DEFAULT 开启通知，不会弹出，发出提示音，状态栏中显示
         * IMPORTANCE_HIGH 开启通知，会弹出，发出提示音，状态栏中显示
         */
        NotificationManager manager = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        NotificationCompat.Builder builder ;
        String CHANNEL_ID = "my_channel";
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O){        //Android 8.0适配
            NotificationChannel channel = new NotificationChannel(CHANNEL_ID,
                    "Channel human readable title",
                    NotificationManager.IMPORTANCE_DEFAULT);//如果这里用IMPORTANCE_NOENE就需要在系统的设置里面开启渠道， //通知才能正常弹出
            channel.enableLights(true);//设置提示灯
            channel.setLightColor(Color.RED);//设置提示灯颜色
            channel.setShowBadge(true);//显示logo
            channel.setDescription("bottombar notification");//设置描述
            manager.createNotificationChannel(channel);
            builder = new NotificationCompat.Builder(this,String.valueOf(CHANNEL_ID));
        }else{
            builder = new NotificationCompat.Builder(this);
        }
        builder.setContentTitle("this is content title")            //指定通知栏的标题内容
                .setContentText("this is content text")             //通知的正文内容
                .setWhen(System.currentTimeMillis())                //通知创建的时间
                .setSmallIcon(R.mipmap.ic_launcher)    //通知显示的小图标，只能用alpha图层的图片进行设置
                .setLargeIcon(BitmapFactory.decodeResource(getResources(),R.drawable.ic_launcher_background));

        Notification notification = builder.build() ;
        startForeground(NOTIFICATION_ID, notification);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
//        startForeground(NOTIFICATION_ID, new Notification());
        stopSelf();
        return super.onStartCommand(intent, flags, startId);
    }

    @Override
    public void onDestroy() {
        stopForeground(true);
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
