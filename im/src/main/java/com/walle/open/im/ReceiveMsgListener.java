package com.walle.open.im;

import com.walle.base.utils.LogUtils;
import com.walle.push.OpenPush;
import com.walle.push.PushConstant;

import net.qiujuer.italker.factory.Factory;
import net.qiujuer.italker.factory.data.helper.AccountHelper;
import net.qiujuer.italker.factory.data.helper.MessageHelper;
import net.qiujuer.italker.factory.model.card.MessageCard;
import net.qiujuer.italker.factory.persistence.Account;

import static net.qiujuer.italker.factory.Factory.getGson;

public class ReceiveMsgListener implements OpenPush.PushMsgListener {

    @Override
    public void onReceive(int type, String msg) {
        Factory.runOnAsync(new Runnable() {
            @Override
            public void run() {
                if(type == PushConstant.MSG_NORMAL){
                    // 聊天的消息

                    LogUtils.d("IM-收到新的通知:" + msg);

                    onMessageArrived(msg);

                }else if (type == PushConstant.MSG_BIND){
                    // 设置设备Id
                    Account.setPushId(msg);

                    // MPush 已经绑定成功, 此时，需要绑定厂商token
                    AccountHelper.bindPush();

                    // MPush 建立成功，此时需要获取当前用户未接受到的消息
                    MessageHelper.getMissedMessage();
                }
            }
        });
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
