package com.walle.open.im;

import com.walle.base.utils.LogUtils;
import com.walle.push.OpenPush;
import com.walle.push.PushConstant;

import net.qiujuer.italker.factory.Factory;
import net.qiujuer.italker.factory.model.card.MessageCard;
import net.qiujuer.italker.factory.persistence.Account;

import static net.qiujuer.italker.factory.Factory.getGson;

public class ReceiveMsgListener implements OpenPush.PushMsgListener {

    @Override
    public void onReceive(int type, String msg) {
        if(type == PushConstant.MSG_NORMAL){
            // 聊天的消息

            LogUtils.d("收到新的通知:" + msg);

            PushMsg pushMsg = getGson().fromJson(msg, PushMsg.class);

            if(pushMsg.getMsgType() == PushMsg.NOTIFICATION_AND_MESSAGE){
                onMessageArrived(pushMsg.getContent());
            }

        }else if (type == PushConstant.MSG_BIND){
            // 设置设备Id
            Account.setPushId(msg);

            //此时，推送的这个 已经绑定成功 。
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
