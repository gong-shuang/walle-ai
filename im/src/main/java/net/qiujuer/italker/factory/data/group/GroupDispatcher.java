package net.qiujuer.italker.factory.data.group;

import net.qiujuer.italker.factory.data.helper.DbHelper;
import net.qiujuer.italker.factory.data.helper.GroupHelper;
import net.qiujuer.italker.factory.data.helper.UserHelper;
import net.qiujuer.italker.factory.model.card.GroupCard;
import net.qiujuer.italker.factory.model.card.GroupMemberCard;
import net.qiujuer.italker.factory.model.db.Group;
import net.qiujuer.italker.factory.model.db.GroupMember;
import net.qiujuer.italker.factory.model.db.User;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Executor;
import java.util.concurrent.Executors;

/**
 * 群／群成员卡片中心的实现类
 * @author qiujuer Email:qiujuer@live.cn
 * @version 1.0.0
 */
public class GroupDispatcher implements GroupCenter {
    private static GroupCenter instance;
    private Executor executor = Executors.newSingleThreadExecutor();

    public static GroupCenter instance() {
        if (instance == null) {
            synchronized (GroupDispatcher.class) {
                if (instance == null)
                    instance = new GroupDispatcher();
            }
        }
        return instance;
    }

    @Override
    public void dispatch(GroupCard... cards) {
        if (cards == null || cards.length == 0)
            return;
        executor.execute(new GroupHandler(cards));
    }

    @Override
    public void dispatchDel(GroupCard... cards) {
        if (cards == null || cards.length == 0)
            return;
        executor.execute(new GroupDeleteHandler(cards));
    }

    @Override
    public void dispatchSync(GroupCard... cards) {
        if (cards == null || cards.length == 0)
            return;
        executor.execute(new GroupSyncHandler(cards));
    }

    @Override
    public void dispatch(GroupMemberCard... cards) {
        if (cards == null || cards.length == 0)
            return;
        executor.execute(new GroupMemberRspHandler(cards));
    }

    @Override
    public void dispatchSync(GroupMemberCard... cards) {
        if (cards == null || cards.length == 0)
            return;
        executor.execute(new GroupMemberRspSyncHandler(cards));
    }

    @Override
    public void dispatchDel(GroupMemberCard... cards) {
        if (cards == null || cards.length == 0)
            return;
        executor.execute(new GroupMemberRspDelHandler(cards));
    }

    private class GroupMemberRspHandler implements Runnable {
        private final GroupMemberCard[] cards;

        GroupMemberRspHandler(GroupMemberCard[] cards) {
            this.cards = cards;
        }

        @Override
        public void run() {
            List<GroupMember> members = new ArrayList<>();
            for (GroupMemberCard model : cards) {
                // 成员对应的人的信息
                User user = UserHelper.search(model.getUserId());
                // 成员对应的群的信息
                Group group = GroupHelper.find(model.getGroupId());
                if (user != null && group != null) {
                    GroupMember member = model.build(group, user);
                    members.add(member);
                }
            }
            if (members.size() > 0)
                DbHelper.save(GroupMember.class, members.toArray(new GroupMember[0]));
        }
    }

    // 群成员的 同步
    private class GroupMemberRspSyncHandler implements Runnable {
        private final GroupMemberCard[] cards;

        GroupMemberRspSyncHandler(GroupMemberCard[] cards) {
            this.cards = cards;
        }

        @Override
        public void run() {

            // 添加
            List<GroupMember> members = new ArrayList<>();
            for (GroupMemberCard model : cards) {
                // 成员对应的人的信息
                User user = UserHelper.search(model.getUserId());
                // 成员对应的群的信息
                Group group = GroupHelper.find(model.getGroupId());
                if (user != null && group != null) {
                    GroupMember member = model.build(group, user);
                    members.add(member);
                }
            }

            //不能删除，因为删除后，会再次网络请求
//            for(GroupMember groupMember: members){
//                //先删除所有的
//                List<GroupMember> oldGroupMembers = GroupHelper.findAllGroupMembers(groupMember.getGroup().getId());
//                if (oldGroupMembers.size() > 0)
//                    DbHelper.delete(GroupMember.class, oldGroupMembers.toArray(new GroupMember[0]));
//            }

            //添加新的
            if (members.size() > 0){
                DbHelper.save(GroupMember.class, members.toArray(new GroupMember[0]));
            }

        }
    }

    private class GroupMemberRspDelHandler implements Runnable {
        private final GroupMemberCard[] cards;

        GroupMemberRspDelHandler(GroupMemberCard[] cards) {
            this.cards = cards;
        }

        @Override
        public void run() {
            List<GroupMember> members = new ArrayList<>();
            for (GroupMemberCard model : cards) {
                // 成员对应的人的信息
                User user = UserHelper.search(model.getUserId());
                // 成员对应的群的信息
                Group group = GroupHelper.find(model.getGroupId());
                if (user != null && group != null) {
                    GroupMember member = model.build(group, user);
                    members.add(member);
                }
            }
            if (members.size() > 0)
                DbHelper.delete(GroupMember.class, members.toArray(new GroupMember[0]));
        }
    }

    /**
     * 把群Card处理为群DB类
     */
    private class GroupHandler implements Runnable {
        private final GroupCard[] cards;

        GroupHandler(GroupCard[] cards) {
            this.cards = cards;
        }

        @Override
        public void run() {
            List<Group> groups = new ArrayList<>();
            for (GroupCard card : cards) {
                // 搜索管理员
                User owner = UserHelper.search(card.getOwnerId());
                if (owner != null) {
                    Group group = card.build(owner);
                    groups.add(group);
                }
            }
            if (groups.size() > 0)
                DbHelper.save(Group.class, groups.toArray(new Group[0]));
        }
    }


    /**
     * 要被删除的群
     */
    private class GroupDeleteHandler implements Runnable {
        private final GroupCard[] cards;

        GroupDeleteHandler(GroupCard[] cards) {
            this.cards = cards;
        }

        @Override
        public void run() {
            List<Group> groups = new ArrayList<>();
            for (GroupCard card : cards) {
                // 搜索管理员
                User owner = UserHelper.search(card.getOwnerId());
                if (owner != null) {
                    Group group = card.build(owner);
                    groups.add(group);
                }
            }
            //删除
//            //先删除组成员
//            for(Group group: groups){
//                //先删除所有的
//                List<GroupMember> oldGroupMembers = GroupHelper.findAllGroupMembers(group.getId());
//                if (oldGroupMembers.size() > 0)
//                    DbHelper.delete(GroupMember.class, oldGroupMembers.toArray(new GroupMember[0]));
//            }

            if (groups.size() > 0)
                DbHelper.delete(Group.class, groups.toArray(new Group[0]));

        }
    }

    /**
     * 把群Card处理为群DB类
     */
    private class GroupSyncHandler implements Runnable {
        private final GroupCard[] cards;

        GroupSyncHandler(GroupCard[] cards) {
            this.cards = cards;
        }

        @Override
        public void run() {
            //先删除多余的，后添加新的

            //老的（本地）， 放在单独的函数中
//            List<Group> oldGroups = GroupHelper.findAllGroup();
//            if (oldGroups.size() > 0)
//                DbHelper.delete(Group.class, oldGroups.toArray(new Group[0]));
//            for(Group group: oldGroups){
//                //先删除所有的
//                List<GroupMember> oldGroupMembers = GroupHelper.findAllGroupMembers(group.getId());
//                if (oldGroupMembers.size() > 0)
//                    DbHelper.delete(GroupMember.class, oldGroupMembers.toArray(new GroupMember[0]));
//            }

            //新的（网络）
            List<Group> newGroups = new ArrayList<>();
            for (GroupCard card : cards) {
                // 搜索管理员
                User owner = UserHelper.search(card.getOwnerId());
                if (owner != null) {
                    Group group = card.build(owner);
                    newGroups.add(group);
                }
            }

            if (newGroups.size() > 0){
                DbHelper.save(Group.class, newGroups.toArray(new Group[0]));
            }

            //同步成员
//            for(Group group : newGroups){
//                GroupHelper.refreshGroupMember(group);
//            }

        }
    }

    @Override
    public void deleteAllGroupAndMembers(){
        List<Group> oldGroups = GroupHelper.findAllGroup();
        for(Group group: oldGroups){
            //先删除所有的组成员
            List<GroupMember> oldGroupMembers = GroupHelper.findAllGroupMembers(group.getId());
            if (oldGroupMembers.size() > 0)
                DbHelper.delete(GroupMember.class, oldGroupMembers.toArray(new GroupMember[0]));
        }

        //再删除组
        if (oldGroups.size() > 0)
            DbHelper.delete(Group.class, oldGroups.toArray(new Group[0]));

    }
}
