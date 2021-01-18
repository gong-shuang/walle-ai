package com.walle.push;

import android.app.ActivityManager;
import android.content.Context;
import android.os.Process;
import android.util.Log;

import com.walle.base.utils.DeviceInfo;
import com.walle.base.utils.LogUtils;
import com.walle.push.mpush.MPushInit;
import com.walle.push.mpush.MyReceiver;
import com.walle.push.sdkXiaomi.XiaomiInit;
import com.xiaomi.channel.commonutils.logger.LoggerInterface;
import com.xiaomi.mipush.sdk.Logger;
import com.xiaomi.mipush.sdk.MiPushClient;

import java.util.List;

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
            XiaomiInit.init(mContext);

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
