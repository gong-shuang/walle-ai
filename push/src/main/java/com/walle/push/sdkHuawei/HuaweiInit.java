package com.walle.push.sdkHuawei;

import android.content.Context;
import android.text.TextUtils;
import android.util.Log;

import com.huawei.agconnect.config.AGConnectServicesConfig;
import com.huawei.hms.aaid.HmsInstanceId;
import com.huawei.hms.common.ApiException;
import com.walle.push.OpenPush;

import static com.walle.push.PushConstant.PRODUCER_REGISTER;

public class HuaweiInit {

    public static String TOKEN;

    public static void init(Context context){
        new Thread() {
            @Override
            public void run() {
                try {
                    // read from agconnect-services.json
                    String appId = AGConnectServicesConfig.fromContext(context).getString("client/app_id");
                    String token = HmsInstanceId.getInstance(context).getToken(appId, "HCM");
                    Log.i(OpenPush.TAG, "get token:" + token);
                    TOKEN = token;
                    if(!TextUtils.isEmpty(token)) {
                        if(OpenPush.mNotificationListener != null){
                            OpenPush.mNotificationListener.onReceive(PRODUCER_REGISTER, "");
                        }
                    }

                } catch (ApiException e) {
                    Log.e(OpenPush.TAG, "get token failed, " + e);
                }
            }
        }.start();
    }
}
