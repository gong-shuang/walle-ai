package net.qiujuer.italker.factory.model.api.account;

import com.walle.base.utils.DeviceInfo;
import com.walle.push.OpenPush;

public class BindModel {
    private String pushId;
    private String userId;
    private String phoneProducer ;  //手机厂家
    private String phoneModel ; // 手机型号
    private String systemVersion ;  //系统版本
    private int operatingSystem ; // 操作系统，1：android， 2：IOS
    private String token;  //厂商token

    public BindModel(String pushId, String userId) {
        this.pushId = pushId;
        this.userId = userId;
        this.phoneProducer = DeviceInfo.getPhoneProducer();
        this.phoneModel = DeviceInfo.getPhoneModel();
        this.systemVersion = DeviceInfo.getSystemVersion();
        this.operatingSystem = 1;
        this.token = OpenPush.getProducerToken();
    }

    public String getPushId() {
        return pushId;
    }

    public void setPushId(String pushId) {
        this.pushId = pushId;
    }

    public String getUserId() {
        return userId;
    }

    public void setUserId(String userId) {
        this.userId = userId;
    }

    public String getPhoneProducer() {
        return phoneProducer;
    }

    public void setPhoneProducer(String phoneProducer) {
        this.phoneProducer = phoneProducer;
    }

    public String getPhoneModel() {
        return phoneModel;
    }

    public void setPhoneModel(String phoneModel) {
        this.phoneModel = phoneModel;
    }

    public String getSystemVersion() {
        return systemVersion;
    }

    public void setSystemVersion(String systemVersion) {
        this.systemVersion = systemVersion;
    }

    public int getOperatingSystem() {
        return operatingSystem;
    }

    public void setOperatingSystem(int operatingSystem) {
        this.operatingSystem = operatingSystem;
    }

    public String getToken() {
        return token;
    }

    public void setToken(String token) {
        this.token = token;
    }
}
