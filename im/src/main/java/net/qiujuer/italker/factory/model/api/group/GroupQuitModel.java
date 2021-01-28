package net.qiujuer.italker.factory.model.api.group;

public class GroupQuitModel {
    private String groupId;
    private String userId;

    public GroupQuitModel(String groupId, String userId) {
        this.groupId = groupId;
        this.userId = userId;
    }

    public String getGroupId() {
        return groupId;
    }

    public void setGroupId(String groupId) {
        this.groupId = groupId;
    }

    public String getUserId() {
        return userId;
    }

    public void setUserId(String userId) {
        this.userId = userId;
    }
}
