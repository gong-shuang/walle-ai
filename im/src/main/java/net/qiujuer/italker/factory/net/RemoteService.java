package net.qiujuer.italker.factory.net;

import net.qiujuer.italker.factory.model.api.RspModel;
import net.qiujuer.italker.factory.model.api.account.AccountRspModel;
import net.qiujuer.italker.factory.model.api.account.BindModel;
import net.qiujuer.italker.factory.model.api.account.CheckTokenRspModel;
import net.qiujuer.italker.factory.model.api.account.LoginModel;
import net.qiujuer.italker.factory.model.api.account.RegisterModel;
import net.qiujuer.italker.factory.model.api.group.GroupCreateModel;
import net.qiujuer.italker.factory.model.api.group.GroupMemberAddModel;
import net.qiujuer.italker.factory.model.api.group.GroupQuitModel;
import net.qiujuer.italker.factory.model.api.message.GroupMissedMsgModel;
import net.qiujuer.italker.factory.model.api.message.MsgCreateModel;
import net.qiujuer.italker.factory.model.api.message.MsgReceivedModel;
import net.qiujuer.italker.factory.model.api.user.UserUpdateModel;
import net.qiujuer.italker.factory.model.card.GroupCard;
import net.qiujuer.italker.factory.model.card.GroupMemberCard;
import net.qiujuer.italker.factory.model.card.MessageCard;
import net.qiujuer.italker.factory.model.card.UserCard;

import java.util.List;

import retrofit2.Call;
import retrofit2.http.Body;
import retrofit2.http.GET;
import retrofit2.http.Header;
import retrofit2.http.POST;
import retrofit2.http.PUT;
import retrofit2.http.Path;
import retrofit2.http.Query;

/**
 * 网络请求的所有的接口
 *
 * @author qiujuer Email:qiujuer@live.cn
 * @version 1.0.0
 */
public interface RemoteService {

    /**
     * 注册接口
     *
     * @param model 传入的是RegisterModel
     * @return 返回的是RspModel<AccountRspModel>
     */
    @POST("account/register")
    Call<RspModel<AccountRspModel>> accountRegister(@Body RegisterModel model);

    /**
     * 登录接口
     *
     * @param model LoginModel
     * @return RspModel<AccountRspModel>
     */
    @POST("user/login")
    Call<RspModel<AccountRspModel>> accountLogin(@Body LoginModel model);

    /**
     * 检测token
     *
     * @return RspModel<String>
     */
    @GET("user/checkToken")
    Call<RspModel<Boolean>> checkToken(@Query("token") String token);

    /**
     * 绑定设备Id
     * @return RspModel<String>
     */
    @POST("device/bind")
    Call<RspModel<String>> accountBind(@Body BindModel model);

    // 用户更新的接口
    @PUT("user")
    Call<RspModel<UserCard>> userUpdate(@Body UserUpdateModel model);

    // 用户搜索的接口
    @GET("user/search/{name}")
    Call<RspModel<List<UserCard>>> userSearch(@Path("name") String name);

    // 用户关注接口
    @PUT("user/follow/{userId}")
    Call<RspModel<UserCard>> userFollow(@Path("userId") String userId);

    // 获取联系人列表
    // ok
    @GET("user/contact")
    Call<RspModel<List<UserCard>>> userContacts(@Query("userId") String name);

    // 查询某人的信息
    @GET("user/{userId}")
    Call<RspModel<UserCard>> userFind(@Path("userId") String userId);

    // 发送消息的接口
    // ok
    @POST("message/send")
    Call<RspModel<MessageCard>> msgPush(@Body MsgCreateModel model);

    // 获取未接受到的消息 （单聊）
    @GET("user/missedMessage")
    Call<RspModel<List<MessageCard>>> getMissedMsg(@Query("userId") String userId);

    // 设置消息已经被接受
    @POST("message/received")
    Call<RspModel<String>> setMsgReceived(@Body MsgReceivedModel model);

    // 获取群未接受到的消息（群）
    @POST("message/GroupMissedMessage")
    Call<RspModel<List<MessageCard>>> getGroupMissedMsg(@Body List<GroupMissedMsgModel> list);

    // 创建群
    @POST("group/insert")
    Call<RspModel<GroupCard>> groupCreate(@Body GroupCreateModel model);

    // 拉取群信息
    @GET("group/{groupId}")
    // no
    Call<RspModel<GroupCard>> groupFind(@Path("groupId") String groupId);

    // 群搜索的接口
    // no
    @GET("group/search/{name}")
    Call<RspModel<List<GroupCard>>> groupSearch(@Path(value = "name", encoded = true) String name);

    // 我的群列表
    @GET("group/list")
    Call<RspModel<List<GroupCard>>> groups(@Query("userId") String userId);

    // 我的群的成员列表
    @GET("group/member")
    Call<RspModel<List<GroupMemberCard>>> groupMembers(@Query("groupId") String groupId);

    // 给群添加新成员
    @POST("group/AddMember")
    Call<RspModel<List<GroupMemberCard>>> groupMemberAdd(@Body GroupMemberAddModel model);

    // 删除群成员
    @POST("group/DelMember")
    Call<RspModel<List<GroupMemberCard>>> groupMemberDel(@Body GroupMemberAddModel model);

    // 退出群
    @POST("group/quit")
    Call<RspModel<GroupCard>> groupQuit(@Body GroupQuitModel model);
}
