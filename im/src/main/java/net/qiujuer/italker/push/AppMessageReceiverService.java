package net.qiujuer.italker.push;

import android.content.Context;
import android.util.Log;

import com.igexin.sdk.GTIntentService;
import com.igexin.sdk.message.GTCmdMessage;
import com.igexin.sdk.message.GTNotificationMessage;
import com.igexin.sdk.message.GTTransmitMessage;

import net.qiujuer.italker.factory.Factory;
import net.qiujuer.italker.factory.data.helper.AccountHelper;
import net.qiujuer.italker.factory.persistence.Account;

/**
 * 个推接收消息的IntentService，用以接收具体的消息信息
 * 替换之前老版本的消息广播
 */
public class AppMessageReceiverService extends GTIntentService {
    @Override
    public void onReceiveServicePid(Context context, int i) {
        // 返回消息接收进程id，当前APP中就是AppPushService进程id
        Log.i(TAG, "onReceiveServicePid() called with: context = [" + context + "], i = [" + i + "]");
    }

    /**
     * 这个clientid的值，会变化，每次打开APP都不一样，（APP重新安装后，打开APP时CID不一样，
     * 但是，仅仅只是退出登陆，没有个推相关处理时，打开APP时，CID是一样的。）
     * 这个CID在设备还没有登陆时，仅仅是打开APP时，由个推服务器返回的
     * @param context
     * @param s
     */
    @Override
    public void onReceiveClientId(Context context, String s) {
        // 当设备成功在个推注册时返回个推唯一设备码
        Log.i(TAG, "onReceiveClientId() called with: context = [" + context + "], s = [" + s + "]");
        // 当Id初始化的时候
        // 获取设备Id
        onClientInit(s);
    }

    /**
     * 透传消息，就是聊天发送的消息，包括文字信息，头像，语言。
     * @param context
     * @param gtTransmitMessage
     */
    @Override
    public void onReceiveMessageData(Context context, GTTransmitMessage gtTransmitMessage) {
        // 当接收到透传消息时回调，跟之前广播接收消息一样
        Log.i(TAG, "onReceiveMessageData() called with: context = [" + context + "], gtTransmitMessage = [" + gtTransmitMessage + "]");

        // 拿到透传消息的实体信息转换为字符串
        byte[] payload = gtTransmitMessage.getPayload();
        if (payload != null) {
            String message = new String(payload);
            onMessageArrived(message);
        }
    }

    @Override
    public void onReceiveOnlineState(Context context, boolean b) {
        // 当设备状态变化时回调，是否在线
        Log.i(TAG, "onReceiveOnlineState() called with: context = [" + context + "], b = [" + b + "]");
    }

    @Override
    public void onReceiveCommandResult(Context context, GTCmdMessage gtCmdMessage) {
        // 当个推Command命名返回时回调
        Log.i(TAG, "onReceiveCommandResult() called with: context = [" + context + "], gtCmdMessage = [" + gtCmdMessage + "]");
    }

    @Override
    public void onNotificationMessageArrived(Context context, GTNotificationMessage gtNotificationMessage) {
        // 当通知栏消息达到时回调
        Log.i(TAG, "onNotificationMessageArrived() called with: context = [" + context + "], gtNotificationMessage = [" + gtNotificationMessage + "]");
    }

    @Override
    public void onNotificationMessageClicked(Context context, GTNotificationMessage gtNotificationMessage) {
        // 当通知栏消息点击时回调
        Log.i(TAG, "onNotificationMessageClicked() called with: context = [" + context + "], gtNotificationMessage = [" + gtNotificationMessage + "]");
    }


    /**
     * 当Id初始化的试试
     *
     * @param cid 设备Id
     */
    private void onClientInit(String cid) {
        // 设置设备Id
        Account.setPushId(cid);
        if (Account.isLogin()) {
            // 账户登录状态，进行一次PushId绑定
            // 没有登录是不能绑定PushId的
            AccountHelper.bindPush(null);
        }
    }

    /**
     * 消息达到时
     *
     * @param message 新消息
     */
    private void onMessageArrived(String message) {
        // 交给Factory处理
        Factory.dispatchPush(message);
    }
}
