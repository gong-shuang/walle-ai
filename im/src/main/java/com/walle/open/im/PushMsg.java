package com.walle.open.im;

public class PushMsg {
    public static final int NOTIFICATION = 1;  //会在通知栏显示
    public static final int MESSAGE = 2;  //不会在通知栏显示,业务自定义消息
    public static final int NOTIFICATION_AND_MESSAGE = 3;  //1+2

    private int msgType; //type

    private String msgId; // alloc 会自增，返回的值为 msg_173

    private String content; //content

    public int getMsgType() {
        return msgType;
    }

    public void setMsgType(int msgType) {
        this.msgType = msgType;
    }

    public String getMsgId() {
        return msgId;
    }

    public void setMsgId(String msgId) {
        this.msgId = msgId;
    }

    public String getContent() {
        return content;
    }

    public void setContent(String content) {
        this.content = content;
    }
}
