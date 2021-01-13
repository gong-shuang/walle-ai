package com.walle.push.mpush;

import android.app.Activity;
import android.content.Context;
import android.graphics.BitmapFactory;
import android.telephony.TelephonyManager;
import android.text.TextUtils;
import android.widget.EditText;

import com.huawei.agconnect.core.BuildConfig;
import com.mpush.android.Notifications;
import com.mpush.client.ClientConfig;
import com.walle.base.utils.DeviceInfo;
import com.walle.base.utils.LogUtils;
import com.walle.push.OpenPush;
import com.walle.push.PushConstant;
import com.walle.push.R;

import java.util.UUID;

/**
 * 暂时命名这个吧 MPushInit，后面再修改
 */
public class MPushInit {



    private static MPushInit mInstance = new MPushInit();

    public static MPushInit getInstance(){
        return mInstance;
    }

    private void initNotifications(){
        Notifications.I.init(OpenPush.getContext());
        Notifications.I.setSmallIcon(R.mipmap.ic_launcher);
        Notifications.I.setLargeIcon(BitmapFactory.decodeResource(OpenPush.getContext().getResources(), R.mipmap.ic_launcher));
    }

    private void initPush(String userId) {
        //公钥有服务端提供和私钥对应
        String publicKey = "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCghPCWCobG8nTD24juwSVataW7iViRxcTkey/B792VZEhuHjQvA3cAJgx2Lv8GnX8NIoShZtoCg3Cx6ecs+VEPD2fBcg2L4JK7xldGpOJ3ONEAyVsLOttXZtNXvyDZRijiErQALMTorcgi79M5uVX9/jMv2Ggb2XAeZhlLD28fHwIDAQAB";

        ClientConfig cc = ClientConfig.build()
                .setPublicKey(publicKey)
                .setAllotServer(PushConstant.allocServer)
                .setDeviceId(getDeviceId())
                .setClientVersion(BuildConfig.VERSION_NAME)
                .setLogger(new MyLog(null, null))
                .setLogEnabled(BuildConfig.DEBUG)
                .setEnableHttpProxy(true)
                .setUserId(userId);
        MPush.I.checkInit(OpenPush.getContext()).setClientConfig(cc);
    }

    private String getDeviceId() {
        TelephonyManager tm = (TelephonyManager) OpenPush.getContext().getSystemService(Activity.TELEPHONY_SERVICE);
//        String deviceId = tm.getDeviceId(0);
        String deviceId = "1111111112";
        if (TextUtils.isEmpty(deviceId)) {
            String time = Long.toString((System.currentTimeMillis() / (1000 * 60 * 60)));
            deviceId = time + time;
        }
        return deviceId;
    }


    public void start(String userId){
        if(TextUtils.isEmpty(userId)){
            userId = getPushId();
        }

        LogUtils.d("userId:"+userId);

        initNotifications();

        initPush(userId);

        MPush.I.checkInit(OpenPush.getContext()).startPush();
    }

    /**
     * 已经初始化后，需要变更user，则调用这个函数
     * @param user
     */
    public void bindUser(String user){

        if (!TextUtils.isEmpty(user)) {
            MPush.I.bindAccount(user, "mpush:" + (int) (Math.random() * 10));
        }
    }

    public String getPushId(){
        String androidID = DeviceInfo.getAndroidID(OpenPush.getContext());
        String fingerPrint = DeviceInfo.getFingerPrint();
        String serialNumber = DeviceInfo.getSerialNumber();
//        phoneProducer = AndroidDeviceInfo.getPhoneProducer();
//        phoneModel = AndroidDeviceInfo.getPhoneModel();
//        systemVersion = AndroidDeviceInfo.getSystemVersion();
//        sdkVersion = AndroidDeviceInfo.getSDKVersion();
        String mac = DeviceInfo.getMacAddress(OpenPush.getContext());
        String applicationId = OpenPush.getContext().getApplicationInfo().processName;
        UUID deviceUuid = new UUID(((long)androidID.hashCode()<< 32 | fingerPrint.hashCode()),
                ((long)applicationId.hashCode() << 32) | mac.hashCode());
        return deviceUuid.toString();
    }

}
