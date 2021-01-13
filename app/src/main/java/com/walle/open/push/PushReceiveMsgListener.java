package com.walle.open.push;

import com.walle.push.OpenPush;
import com.walle.push.PushConstant;

//接受的
public class PushReceiveMsgListener implements OpenPush.PushMsgListener {

    @Override
    public void onReceive(int type, String msg) {
        if(type == PushConstant.MSG_NOTIFY){
            //业务层的 通知

        }
    }
}
