package com.walle.open.push;

import com.walle.base.utils.LogUtils;
import com.walle.open.eventBus.MessageEvent;
import com.walle.push.OpenPush;
import com.walle.push.PushConstant;

import org.greenrobot.eventbus.EventBus;

//接受的
public class PushReceiveMsgListener implements OpenPush.PushMsgListener {

    @Override
    public void onReceive(int type, String msg) {
        if(type == PushConstant.MSG_NOTIFY){
            //业务层的 通知
            LogUtils.d("通知：" + msg);

            EventBus.getDefault().post(new MessageEvent(msg));

        }else if(type == PushConstant.PRODUCER_REGISTER){
            //厂商注册
        }
    }
}
