package net.qiujuer.italker.factory.data.helper;

import android.os.SystemClock;
import android.text.TextUtils;
import android.util.Log;

import com.bumptech.glide.Glide;
import com.bumptech.glide.request.target.Target;
import com.raizlabs.android.dbflow.sql.language.OperatorGroup;
import com.raizlabs.android.dbflow.sql.language.SQLite;
import com.walle.base.utils.LogUtils;

import net.qiujuer.italker.common.Common;
import net.qiujuer.italker.common.app.Application;
import net.qiujuer.italker.factory.Factory;
import net.qiujuer.italker.factory.model.api.RspModel;
import net.qiujuer.italker.factory.model.api.message.GroupMissedMsgModel;
import net.qiujuer.italker.factory.model.api.message.MsgCreateModel;
import net.qiujuer.italker.factory.model.api.message.MsgReceivedModel;
import net.qiujuer.italker.factory.model.card.GroupMemberCard;
import net.qiujuer.italker.factory.model.card.MessageCard;
import net.qiujuer.italker.factory.model.db.Message;
import net.qiujuer.italker.factory.model.db.Message_Table;
import net.qiujuer.italker.factory.model.db.Session;
import net.qiujuer.italker.factory.net.Network;
import net.qiujuer.italker.factory.net.RemoteService;
import net.qiujuer.italker.factory.net.UploadHelper;
import net.qiujuer.italker.factory.persistence.Account;
import net.qiujuer.italker.utils.PicturesCompressor;
import net.qiujuer.italker.utils.StreamUtil;

import java.io.File;
import java.util.ArrayList;
import java.util.List;

import retrofit2.Call;
import retrofit2.Callback;
import retrofit2.Response;

/**
 * 消息工具类
 *
 * @author qiujuer Email:qiujuer@live.cn
 * @version 1.0.0
 */
public class MessageHelper {
    // 从本地找消息
    public static Message findFromLocal(String id) {
        return SQLite.select()
                .from(Message.class)
                .where(Message_Table.id.eq(id))
                .querySingle();
    }

    // 发送是异步进行的
    public static void push(final MsgCreateModel model) {
        model.setSenderId(Account.getUserId()); // gs add 2021-1-12
        Factory.runOnAsync(new Runnable() {
            @Override
            public void run() {
                // 成功状态：如果是一个已经发送过的消息，则不能重新发送
                // 正在发送状态：如果是一个消息正在发送，则不能重新发送
                Message message = findFromLocal(model.getId());
                if (message != null && message.getStatus() != Message.STATUS_FAILED)
                    return;


                // 我们在发送的时候需要通知界面更新状态，Card;
                final MessageCard card = model.buildCard();
                Factory.getMessageCenter().dispatch(card);


                // 发送文件消息分两部：上传到云服务器，消息Push到我们自己的服务器

                // 如果是文件类型的（语音，图片，文件），需要先上传后才发送
                if (card.getType() != Message.TYPE_STR) {
                    // 不是文字类型
                    if (!card.getContent().startsWith(UploadHelper.ENDPOINT)) {
                        // 没有上传到云服务器的，还是本地手机文件
                        String content;

                        switch (card.getType()) {
                            case Message.TYPE_PIC:
                                content = uploadPicture(card.getContent());
                                break;
                            case Message.TYPE_AUDIO:
                                content = uploadAudio(card.getContent());
                                break;
                            default:
                                content = "";
                                break;
                        }

                        if (TextUtils.isEmpty(content)) {
                            // 失败
                            card.setStatus(Message.STATUS_FAILED);
                            Factory.getMessageCenter().dispatch(card);
                            // 直接返回
                            return;
                        }


                        // 成功则把网络路径进行替换
                        card.setContent(content);
                        Factory.getMessageCenter().dispatch(card);
                        // 因为卡片的内容改变了，而我们上传到服务器是使用的model，
                        // 所以model也需要跟着更改
                        model.refreshByCard();
                    }
                }


                // 直接发送, 进行网络调度
                RemoteService service = Network.remote();
                service.msgPush(model).enqueue(new Callback<RspModel<MessageCard>>() {
                    @Override
                    public void onResponse(Call<RspModel<MessageCard>> call, Response<RspModel<MessageCard>> response) {
                        RspModel<MessageCard> rspModel = response.body();
                        if (rspModel != null && rspModel.success()) {
                            MessageCard rspCard = rspModel.getResult();
                            if (rspCard != null) {
                                // 成功的调度
                                Factory.getMessageCenter().dispatch(rspCard);
                            }
                        } else {
                            // 检查是否是账户异常
                            Factory.decodeRspCode(rspModel, null);
                            // 走失败流程
                            onFailure(call, null);
                        }
                    }

                    @Override
                    public void onFailure(Call<RspModel<MessageCard>> call, Throwable t) {
                        // 通知失败
                        card.setStatus(Message.STATUS_FAILED);
                        Factory.getMessageCenter().dispatch(card);
                    }
                });
            }
        });
    }

    public static void getMissedMessage(){
        // 用户不能为空
        if(TextUtils.isEmpty(Account.getUserId())){
            return;
        }

        RemoteService service = Network.remote();
        service.getMissedMsg(Account.getUserId()).enqueue(new Callback<RspModel<List<MessageCard>>>() {
            @Override
            public void onResponse(Call<RspModel<List<MessageCard>>> call, Response<RspModel<List<MessageCard>>> response) {
                RspModel<List<MessageCard>> rspModel = response.body();
                if (rspModel != null && rspModel.success()) {
                    LogUtils.d("get Missed Message ok!");
                    List<MessageCard> rspCard = rspModel.getResult();
                    if (rspCard != null && rspCard.size() > 0) {
                        // 成功的调度
                        Factory.getMessageCenter().dispatch(rspCard.toArray(new MessageCard[0]));

                        //设置消息已经被接受
                        setMsgReceived(rspCard);
                    }
                } else {
                    // 检查是否是账户异常
                    Factory.decodeRspCode(rspModel, null);
                    // 走失败流程
                    onFailure(call, null);
                    LogUtils.e("rspModel.Message:" );
                }
            }

            @Override
            public void onFailure(Call<RspModel<List<MessageCard>>> call, Throwable t) {
                // 失败
                LogUtils.e("call:" + call);
            }
        });
    }


    /**
     * 拉取所有群的未接受的消息
     */
    public static void getGroupMissedMessage(){
        // 用户不能为空
        if(TextUtils.isEmpty(Account.getUserId())){
            return;
        }

        List<Session> sessionList =SessionHelper.getGroupList();

        if(sessionList.size() == 0){
            return;
        }

        List<GroupMissedMsgModel> missedList = new ArrayList<>();
        for(Session session: sessionList){
            missedList.add(new GroupMissedMsgModel(session.getId(), session.getModifyAt()));
        }

        RemoteService service = Network.remote();
        service.getGroupMissedMsg(missedList).enqueue(new Callback<RspModel<List<MessageCard>>>() {
            @Override
            public void onResponse(Call<RspModel<List<MessageCard>>> call, Response<RspModel<List<MessageCard>>> response) {
                RspModel<List<MessageCard>> rspModel = response.body();
                if (rspModel != null && rspModel.success()) {
                    LogUtils.d("get Missed Message ok!");
                    List<MessageCard> rspCard = rspModel.getResult();
                    if (rspCard != null && rspCard.size() > 0) {
                        // 成功的调度
                        Factory.getMessageCenter().dispatch(rspCard.toArray(new MessageCard[0]));
                    }
                } else {
                    // 检查是否是账户异常
                    Factory.decodeRspCode(rspModel, null);
                    // 走失败流程
                    onFailure(call, null);
                    LogUtils.e("rspModel.Message:" );
                }
            }

            @Override
            public void onFailure(Call<RspModel<List<MessageCard>>> call, Throwable t) {
                // 失败
                LogUtils.e("call:" + call);
            }
        });
    }

    /**
     * 设置消息已经被接受
     */
    public static void setMsgReceived(List<MessageCard> list){
        if(list != null && list.size() > 0){
            return;
        }
        RemoteService service = Network.remote();
        List<String> msgList = new ArrayList<>();
        for (MessageCard card : list){
            msgList.add(card.getId());
        }
        service.setMsgReceived(new MsgReceivedModel(msgList)).enqueue(new Callback<RspModel<String>>() {
            @Override
            public void onResponse(Call<RspModel<String>> call, Response<RspModel<String>> response) {
                RspModel<String> rspModel = response.body();
                if (rspModel != null && rspModel.success()) {
                    LogUtils.d("get Missed Message ok!");
                } else {
                    LogUtils.e("rspModel.Message:" );
                }
            }

            @Override
            public void onFailure(Call<RspModel<String>> call, Throwable t) {
                // 失败
                LogUtils.e("call:" + call);
            }
        });
    }

    // 上传图片
    private static String uploadPicture(String path) {
        File file = null;
        try {
            // 通过Glide的缓存区间解决了图片外部权限的问题
            file = Glide.with(Factory.app())
                    .load(path)
                    .downloadOnly(Target.SIZE_ORIGINAL, Target.SIZE_ORIGINAL)
                    .get();
        } catch (Exception e) {
            e.printStackTrace();
        }

        if (file != null) {
            // 进行压缩
            String cacheDir = Application.getCacheDirFile().getAbsolutePath();
            String tempFile = String.format("%s/image/Cache_%s.png", cacheDir, SystemClock.uptimeMillis());

            try {
                // 压缩工具类
                if (PicturesCompressor.compressImage(file.getAbsolutePath(), tempFile,
                        Common.Constance.MAX_UPLOAD_IMAGE_LENGTH)) {
                    // 上传
                    String ossPath = UploadHelper.uploadImage(tempFile);
                    // 清理缓存
                    StreamUtil.delete(tempFile);
                    return ossPath;
                }
            } catch (Exception e) {
                e.printStackTrace();
            }
        }

        return null;
    }

    // 上传语音
    private static String uploadAudio(String content) {
        // 上传语音
        File file = new File(content);
        if (!file.exists() || file.length() <= 0)
            return null;
        // 上传并返回
        return UploadHelper.uploadAudio(content);
    }

    /**
     * 查询一个消息，这个消息是一个群中的最后一条消息
     *
     * @param groupId 群Id
     * @return 群中聊天的最后一条消息
     */
    public static Message findLastWithGroup(String groupId) {
        return SQLite.select()
                .from(Message.class)
                .where(Message_Table.group_id.eq(groupId))
                .orderBy(Message_Table.createAt, false) // 倒序查询
                .querySingle();
    }

    /**
     * 查询一个消息，这个消息是和一个人的最后一条聊天消息
     *
     * @param userId UserId
     * @return 聊天的最后一条消息
     */
    public static Message findLastWithUser(String userId) {
        return SQLite.select()
                .from(Message.class)
                .where(OperatorGroup.clause()
                        .and(Message_Table.sender_id.eq(userId))
                        .and(Message_Table.group_id.isNull()))
                .or(Message_Table.receiver_id.eq(userId))
                .orderBy(Message_Table.createAt, false) // 倒序查询
                .querySingle();
    }
}
