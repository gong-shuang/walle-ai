package com.walle.push;

import android.content.Context;

import com.walle.base.utils.DeviceInfo;
import com.walle.base.utils.LogUtils;
import com.walle.push.mpush.MPushInit;
import com.walle.push.mpush.MyReceiver;
import com.xiaomi.mipush.sdk.MiPushClient;

public class OpenPush {


    public static Context mContext;



    public static PushMsgListener mNotificationListener;  // 通知的消息
    public static PushMsgListener mIMListener;  //  IM的消息

    public static void setNotificationListener(PushMsgListener mNotificationListener) {
        OpenPush.mNotificationListener = mNotificationListener;
    }

    public static void setIMListener(PushMsgListener mIMListener) {
        OpenPush.mIMListener = mIMListener;
    }

    public static void initPush(Context context){
        mContext = context;

        //初始化 mpush
        MPushInit.getInstance().start(null);

        String phoneProducer = DeviceInfo.getPhoneProducer();
        String producer = phoneProducer.toLowerCase();
        if("xiaomi".contains(producer) || "redmi".contains(producer)){
            //小米
            MiPushClient.registerPush(context, PushConstant.APP_ID, PushConstant.APP_KEY);
        }else if("huawei".contains(producer) || "honor".contains(producer)){
            //华为
        }else {

        }

        LogUtils.d("producer:"+producer);
    }


    public static Context getContext() {
        return mContext;
    }

    public interface PushMsgListener{
        void onReceive(int type, String msg);
    }
}
