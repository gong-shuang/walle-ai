package com.walle.push;

import android.content.Context;

import com.xiaomi.mipush.sdk.MiPushClient;

//初始化
public class InitPush {

    // user your appid the key.
    private static final String APP_ID = "2882303761518762170";
    // user your appid the key.
    private static final String APP_KEY = "5991876274170";


    public static void initPush(Context context){
        MiPushClient.registerPush(context, APP_ID, APP_KEY);
    }
}
