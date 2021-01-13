package com.walle.open.im;

import android.content.Context;

import com.walle.push.OpenPush;

import net.qiujuer.italker.factory.Factory;

public class OpenIM {

    public static void init(Context context){
        // 调用Factory进行初始化
        Factory.setup();

        //绑定 推送
        OpenPush.setIMListener(new ReceiveMsgListener());
    }
}
