package com;

import android.app.Application;

import com.walle.push.InitPush;

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
    }
}
