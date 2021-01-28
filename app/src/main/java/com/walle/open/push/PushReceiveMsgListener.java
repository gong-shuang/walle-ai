package com.walle.open.push;

import com.walle.push.OpenPush;
import com.walle.push.PushConstant;

import net.qiujuer.italker.factory.data.helper.AccountHelper;

//接受的
public class PushReceiveMsgListener implements OpenPush.PushMsgListener {

    @Override
    public void onReceive(int type, String msg) {
        if(type == PushConstant.MSG_NOTIFY){
            //业务层的 通知

        }else if(type == PushConstant.PRODUCER_REGISTER){
            //厂商注册
            AccountHelper.bindPush();
        }
    }
}
