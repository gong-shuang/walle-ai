package com.walle.push.mpush;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.text.TextUtils;
import android.util.Log;
import android.widget.Toast;

import com.google.gson.Gson;
import com.mpush.android.NotificationDO;
import com.mpush.api.Constants;
import com.walle.base.utils.LogUtils;
import com.walle.push.OpenPush;
import com.walle.push.PushConstant;

import org.json.JSONObject;

import static com.walle.push.mpush.PushMsg.MESSAGE;
import static com.walle.push.mpush.PushMsg.NOTIFICATION;

/**
 * 接受广播
 */
public class MyReceiver extends BroadcastReceiver {


    @Override
    public void onReceive(Context context, Intent intent) {
        LogUtils.d("Mpush getAction:" + intent.getAction());
        if (MPushService.ACTION_MESSAGE_RECEIVED.equals(intent.getAction())) {
            byte[] bytes = intent.getByteArrayExtra(MPushService.EXTRA_PUSH_MESSAGE);
            int messageId = intent.getIntExtra(MPushService.EXTRA_PUSH_MESSAGE_ID, 0);
            String message = new String(bytes, Constants.UTF_8);

            LogUtils.d( "Mpush 收到新的通知：" + message);

            PushMsg pushMsg = new Gson().fromJson(message, PushMsg.class);
            if(pushMsg.getMsgType() == NOTIFICATION ){
                //业务层
                if(OpenPush.mNotificationListener != null){
                    //消息
                    OpenPush.mNotificationListener.onReceive( PushConstant.MSG_NOTIFY, pushMsg.getContent());
                }
            }else if(pushMsg.getMsgType() == MESSAGE ){
                //IM
                if(OpenPush.mIMListener != null){
                    //消息
                    OpenPush.mIMListener.onReceive( PushConstant.MSG_NORMAL, pushMsg.getContent());
                }
            }

            if (messageId > 0) MPush.I.ack(messageId);

            if (TextUtils.isEmpty(message)) return;

//            NotificationDO ndo = fromJson(message);
//
//            if (ndo != null) {
//                Intent it = new Intent(context, MyReceiver.class);
//                it.setAction(MPushService.ACTION_NOTIFICATION_OPENED);
//                if (ndo.getExtras() != null) it.putExtra("my_extra", ndo.getExtras().toString());
//                if (TextUtils.isEmpty(ndo.getTitle())) ndo.setTitle("MPush");
//                if (TextUtils.isEmpty(ndo.getTicker())) ndo.setTicker(ndo.getTitle());
//                if (TextUtils.isEmpty(ndo.getContent())) ndo.setContent(ndo.getTitle());
//                Notifications.I.notify(ndo, it);
//            }
        } else if (MPushService.ACTION_NOTIFICATION_OPENED.equals(intent.getAction())) {
//            Notifications.I.clean(intent);
            String extras = intent.getStringExtra("my_extra");
            Toast.makeText(context, "通知被点击了， extras=" + extras, Toast.LENGTH_SHORT).show();
        } else if (MPushService.ACTION_KICK_USER.equals(intent.getAction())) {
            Toast.makeText(context, "用户被踢下线了", Toast.LENGTH_SHORT).show();
        } else if (MPushService.ACTION_BIND_USER.equals(intent.getAction())) {
            Toast.makeText(context, "绑定用户:"
                            + intent.getStringExtra(MPushService.EXTRA_USER_ID)
                            + (intent.getBooleanExtra(MPushService.EXTRA_BIND_RET, false) ? "成功" : "失败")
                    , Toast.LENGTH_SHORT).show();
            //用户绑定，表示推送通道已经建立
            if(OpenPush.mIMListener != null){
                OpenPush.mIMListener.onReceive(PushConstant.MSG_BIND, intent.getStringExtra(MPushService.EXTRA_USER_ID));
            }
            LogUtils.d("userId:" + intent.getStringExtra(MPushService.EXTRA_USER_ID));
        } else if (MPushService.ACTION_UNBIND_USER.equals(intent.getAction())) {
            Toast.makeText(context, "解绑用户:"
                            + (intent.getBooleanExtra(MPushService.EXTRA_BIND_RET, false)
                            ? "成功"
                            : "失败")
                    , Toast.LENGTH_SHORT).show();
        } else if (MPushService.ACTION_CONNECTIVITY_CHANGE.equals(intent.getAction())) {
            Toast.makeText(context, intent.getBooleanExtra(MPushService.EXTRA_CONNECT_STATE, false)
                            ? "MPUSH连接建立成功"
                            : "MPUSH连接断开"
                    , Toast.LENGTH_SHORT).show();
        } else if (MPushService.ACTION_HANDSHAKE_OK.equals(intent.getAction())) {
            Toast.makeText(context, "MPUSH握手成功, 心跳:" + intent.getIntExtra(MPushService.EXTRA_HEARTBEAT, 0)
                    , Toast.LENGTH_SHORT).show();
        }
    }

    private NotificationDO fromJson(String message) {
        try {
            JSONObject messageDO = new JSONObject(message);
            if (messageDO != null) {
                JSONObject jo = new JSONObject(messageDO.optString("content"));
                NotificationDO ndo = new NotificationDO();
                ndo.setContent(jo.optString("content"));
                ndo.setTitle(jo.optString("title"));
                ndo.setTicker(jo.optString("ticker"));
                ndo.setNid(jo.optInt("nid", 1));
                ndo.setExtras(jo.optJSONObject("extras"));
                return ndo;
            }
        } catch (Exception e) {
            e.printStackTrace();
        }
        return null;
    }
}
