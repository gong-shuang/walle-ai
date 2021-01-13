package com.walle.base.utils;

import android.annotation.SuppressLint;
import android.content.Context;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.net.wifi.WifiInfo;
import android.net.wifi.WifiManager;
import android.telephony.TelephonyManager;
import android.text.TextUtils;

public class DeviceInfo {

    /**
     * 未加密
     * @return Android ID
     *
     * ANDROID_ID 是设备首次启动时由系统随机生成的一串64位的十六进制数字。
     */
    public static String getAndroidID(Context context){
        return android.provider.Settings.Secure.getString(context.getContentResolver(), android.provider.Settings.Secure.ANDROID_ID);
    }

    public static String getFingerPrint(){
        return android.os.Build.FINGERPRINT;
    }

    //获取SIM卡的序列号
    @SuppressLint("MissingPermission")
    public static String getSimSerial(Context context){
        final TelephonyManager tm = (TelephonyManager) context.getSystemService(Context.TELEPHONY_SERVICE);
        if (tm != null) {
            return tm.getSimSerialNumber();
        }
        return "";
    }

    //IMEI号（国际移动设备身份码）、IMSI号（国际移动设备识别码）这两个是有电话功能的移动设备才具有
    @SuppressLint("MissingPermission")
    public static String getIMEI(Context context){
        final TelephonyManager tm = (TelephonyManager) context.getSystemService(Context.TELEPHONY_SERVICE);
        if (tm != null) {
//            tm.getSubscriberId(); //获取IMSI号
            return tm.getDeviceId();  //获取IMEI号
        }
        return "";
    }

    @SuppressLint("MissingPermission")
    public static String getMacAddress(Context context) {
        String macAddress = null ;
        WifiManager wifiManager = (WifiManager)context.getSystemService(Context.WIFI_SERVICE);
        WifiInfo info = ( null == wifiManager ? null : wifiManager.getConnectionInfo());
        macAddress = info.getMacAddress();
        return macAddress;
    }

    public static String getSerialNumber (){
        return android.os.Build.SERIAL;
    }

    /**
     * 手机厂商
     * @return
     */
    public static String getPhoneProducer() {
        return android.os.Build.BRAND;
    }

    /**
     *   获取手机型号,例如：HUAWEI COR-AL10
     * @return
     */
    public  static  String  getPhoneModel()  {
        return  android.os.Build.MANUFACTURER + " " + android.os.Build.MODEL;
    }

    /**
     * 获取系统版本号，例如：6.0 7.0 8.0 9
     * @return
     */
    public static String getSystemVersion() {
        return  android.os.Build.VERSION.RELEASE;
    }

    /**
     * SDK 版本，例如：28
     * @return
     */
    public static String getSDKVersion() {
        return  String.valueOf(android.os.Build.VERSION.SDK_INT);
    }

    /**
     * 当前APP版本
     * @return
     */
    private  String getAppVersionName(Context context) {
        String versionName = "";
        try {
            PackageManager packageManager = context.getPackageManager();
//            context.getPackageName() 当前软件包名
            PackageInfo packageInfo = packageManager.getPackageInfo(context.getPackageName(), 0);
//            packageInfo.versionCode
            versionName = packageInfo.versionName;
            if (TextUtils.isEmpty(versionName)) {
                return "";
            }
        } catch (Exception e) {
            e.printStackTrace();
        }
        return versionName;
    }
}
