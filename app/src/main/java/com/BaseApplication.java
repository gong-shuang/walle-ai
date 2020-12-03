package com;

import android.app.Activity;
import android.os.Bundle;

import com.igexin.sdk.PushManager;
import com.walle.push.InitPush;

import net.qiujuer.italker.common.app.Application;
import net.qiujuer.italker.factory.Factory;
import net.qiujuer.italker.push.AppMessageReceiverService;
import net.qiujuer.italker.push.AppPushService;

/**
 * create by ld
 * date: 2020/11/23
 * des:
 */
public class BaseApplication extends Application {

    @Override
    public void onCreate() {
        super.onCreate();
        InitPush.initPush(this);
        // 调用Factory进行初始化
        Factory.setup();

        // 注册生命周期
        registerActivityLifecycleCallbacks(new PushInitializeLifecycle());
    }


    /**
     * 个推服务在部分手机上极易容易回收，可放Resumed中唤起
     */
    private class PushInitializeLifecycle implements ActivityLifecycleCallbacks {

        @Override
        public void onActivityCreated(Activity activity, Bundle savedInstanceState) {

        }

        @Override
        public void onActivityStarted(Activity activity) {

        }

        @Override
        public void onActivityResumed(Activity activity) {
            // 推送进行初始化
            PushManager.getInstance().initialize(BaseApplication.this, AppPushService.class);
            // 推送注册消息接收服务
            PushManager.getInstance().registerPushIntentService(BaseApplication.this, AppMessageReceiverService.class);
        }

        @Override
        public void onActivityPaused(Activity activity) {

        }

        @Override
        public void onActivityStopped(Activity activity) {

        }

        @Override
        public void onActivitySaveInstanceState(Activity activity, Bundle outState) {

        }

        @Override
        public void onActivityDestroyed(Activity activity) {

        }
    }
}
