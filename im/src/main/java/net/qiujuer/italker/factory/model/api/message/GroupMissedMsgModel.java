package net.qiujuer.italker.factory.model.api.message;


import java.util.Date;

public class GroupMissedMsgModel {
    String id;
    Date date;

    public GroupMissedMsgModel(String id, Date date) {
        this.id = id;
        this.date = date;
    }
}
