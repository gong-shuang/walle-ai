package net.qiujuer.italker.factory.data.group;

import net.qiujuer.italker.factory.model.card.GroupCard;
import net.qiujuer.italker.factory.model.card.GroupMemberCard;

/**
 * 群中心的接口定义
 *
 * @author qiujuer Email:qiujuer@live.cn
 * @version 1.0.0
 */
public interface GroupCenter {
    // 群卡片的处理（添加，修改，没有删除）
    void dispatch(GroupCard... cards);

    // 群卡片的处理（添加，修改，没有删除）
    void dispatchDel(GroupCard... cards);

    // 群卡片的处理同步（先删除多余的，在添加新的）
    void dispatchSync(GroupCard... cards);

    // 群成员的添加（修改）
    void dispatch(GroupMemberCard... cards);

    // 群成员的同步
    void dispatchSync(GroupMemberCard... cards);

    // 退出群（成员）的处理
    void dispatchDel(GroupMemberCard... cards);

    void deleteAllGroupAndMembers();
}
