package com.walle.push;

public class PushConstant {

    // push server
    public static final String allocServer = "http://81.69.253.141:9999";
//    public static final String allocServer = "http://172.30.66.171:9999";

    //小米的厂商配置，更换包名后，需要重新配置这两个
    // user your appid the key.
    public static final String APP_ID = "2882303761518762170";
    // user your appid the key.
    public static final String APP_KEY = "5991876274170";


    // IM
    public static final int MSG_NORMAL = 1;
    public static final int MSG_BIND = 2;

    // notification
    public static final int MSG_NOTIFY = 3;

    //厂商注册成功
    public static final int PRODUCER_REGISTER = 4;
}
