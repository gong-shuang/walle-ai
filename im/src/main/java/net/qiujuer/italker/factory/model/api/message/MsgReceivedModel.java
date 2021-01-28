package net.qiujuer.italker.factory.model.api.message;

import java.util.List;

public class MsgReceivedModel {
    List<String> ids;

    public MsgReceivedModel(List<String> ids) {
        this.ids = ids;
    }

    public List<String> getIds() {
        return ids;
    }

    public void setIds(List<String> ids) {
        this.ids = ids;
    }
}
