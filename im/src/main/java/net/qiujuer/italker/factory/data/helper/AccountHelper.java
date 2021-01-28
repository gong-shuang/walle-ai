package net.qiujuer.italker.factory.data.helper;

import android.text.TextUtils;

import com.walle.base.utils.LogUtils;
import com.walle.push.OpenPush;

import net.qiujuer.italker.factory.model.api.account.BindModel;
import net.qiujuer.italker.factory.model.api.account.CheckTokenRspModel;
import net.qiujuer.italker.push.R;
import net.qiujuer.italker.push.R2;

import net.qiujuer.italker.factory.Factory;
import net.qiujuer.italker.factory.data.DataSource;
import net.qiujuer.italker.factory.model.api.RspModel;
import net.qiujuer.italker.factory.model.api.account.AccountRspModel;
import net.qiujuer.italker.factory.model.api.account.LoginModel;
import net.qiujuer.italker.factory.model.api.account.RegisterModel;
import net.qiujuer.italker.factory.model.db.User;
import net.qiujuer.italker.factory.net.Network;
import net.qiujuer.italker.factory.net.RemoteService;
import net.qiujuer.italker.factory.persistence.Account;

import retrofit2.Call;
import retrofit2.Callback;
import retrofit2.Response;

/**
 * @author qiujuer Email:qiujuer@live.cn
 * @version 1.0.0
 */
public class AccountHelper {

    /**
     * 注册的接口，异步的调用
     *
     * @param model    传递一个注册的Model进来
     * @param callback 成功与失败的接口回送
     */
    public static void register(final RegisterModel model, final DataSource.Callback<User> callback) {
        // 调用Retrofit对我们的网络请求接口做代理
        RemoteService service = Network.remote();
        // 得到一个Call
        Call<RspModel<AccountRspModel>> call = service.accountRegister(model);
        // 异步的请求
        call.enqueue(new AccountRspCallback(callback));
    }

    /**
     * 登录的调用
     *
     * @param model    登录的Model
     * @param callback 成功与失败的接口回送
     */
    public static void login(final LoginModel model, final DataSource.Callback<User> callback) {
        // 调用Retrofit对我们的网络请求接口做代理
        RemoteService service = Network.remote();
        // 得到一个Call
        Call<RspModel<AccountRspModel>> call = service.accountLogin(model);
        // 异步的请求
        call.enqueue(new AccountRspCallback(callback));
    }

    /**
     * 对设备Id进行绑定的操作  (现在修改为，只有推送连接到服务器后，就已经是绑定啦)
     *
     */
    public static void bindPush() {
        // 检查pushId为空
        String pushId = Account.getPushId();
        if (TextUtils.isEmpty(pushId))
            return;

        //检测厂商token是否为空
        if(TextUtils.isEmpty(OpenPush.getProducerToken())){
            return;
        }

        //检测用户是否为空
        if(TextUtils.isEmpty(Account.getUserId())){
            return;
        }

        // 调用Retrofit对我们的网络请求接口做代理
        RemoteService service = Network.remote();
        BindModel model = new BindModel(pushId, Account.getUserId());
        Call<RspModel<String>> call = service.accountBind(model);
        call.enqueue(new Callback<RspModel<String>>() {
            @Override
            public void onResponse(Call<RspModel<String>> call, Response<RspModel<String>> response) {
                RspModel<String> rspModel = response.body();
                if(rspModel!=null && rspModel.success()){
                    LogUtils.d("绑定成功！");
                }else {
                    LogUtils.d("绑定失败！");
                }
            }

            @Override
            public void onFailure(Call<RspModel<String>> call, Throwable t) {
                LogUtils.d("绑定失败！");
            }
        });
    }

    /**
     * 检查token
     *
     */
    public static void checkToken(final DataSource.Callback<Boolean> callback) {
        // 检查pushId为空
        String token = Account.getToken();
        if (TextUtils.isEmpty(token))
            return;

        // 调用Retrofit对我们的网络请求接口做代理
        RemoteService service = Network.remote();
        Call<RspModel<Boolean>> call = service.checkToken(token);
        call.enqueue(new Callback<RspModel<Boolean>>() {
            @Override
            public void onResponse(Call<RspModel<Boolean>> call, Response<RspModel<Boolean>> response) {
                RspModel<Boolean> rspModel = response.body();
                if(rspModel!=null && rspModel.success() && rspModel.getResult() != null){
                    LogUtils.d("检查成功！");
                    if(callback != null){
                        callback.onDataLoaded(rspModel.getResult());
                    }
                }else {
                    LogUtils.d("检查失败！");
                }
            }

            @Override
            public void onFailure(Call<RspModel<Boolean>> call, Throwable t) {
                LogUtils.d("检查失败！");
            }
        });
    }


    /**
     * 请求的回调部分封装
     */
    private static class AccountRspCallback implements Callback<RspModel<AccountRspModel>> {

        final DataSource.Callback<User> callback;

        AccountRspCallback(DataSource.Callback<User> callback) {
            this.callback = callback;
        }

        @Override
        public void onResponse(Call<RspModel<AccountRspModel>> call,
                               Response<RspModel<AccountRspModel>> response) {
            // 请求成功返回
            // 从返回中得到我们的全局Model，内部是使用的Gson进行解析
            RspModel<AccountRspModel> rspModel = response.body();
            if (rspModel.success()) {
                // 拿到实体
                AccountRspModel accountRspModel = rspModel.getResult();
                // 获取我的信息
                User user = accountRspModel.getUser();
                DbHelper.save(User.class, user);

                // 第一种，之间保存
                // user.save();
                /*
                // 第二种通过ModelAdapter
                FlowManager.getModelAdapter(User.class)
                        .save(user);

                // 第三种，事务中
                DatabaseDefinition definition = FlowManager.getDatabase(AppDatabase.class);
                definition.beginTransactionAsync(new ITransaction() {
                    @Override
                    public void execute(DatabaseWrapper databaseWrapper) {
                        FlowManager.getModelAdapter(User.class)
                                .save(user);
                    }
                }).build().execute();
                */
                // 同步到XML持久化中
                Account.login(accountRspModel);

                // 判断绑定状态，是否绑定设备
                if (accountRspModel.isBind()) {
                    // 设置绑定状态为True
                    Account.setBind(true);
                    // 然后返回
                    if (callback != null)
                        callback.onDataLoaded(user);
                } else {
                    // 进行绑定的唤起
//                    bindPush(callback);
                    bindPush();
                }
            } else {
                // 错误解析
                Factory.decodeRspCode(rspModel, callback);
            }
        }

        @Override
        public void onFailure(Call<RspModel<AccountRspModel>> call, Throwable t) {
            // 网络请求失败
            if (callback != null)
                callback.onDataNotAvailable(R.string.data_network_error);
        }
    }

}
