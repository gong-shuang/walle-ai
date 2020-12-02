package net.qiujuer.italker.push.frags.main;

import android.os.Bundle;
import android.view.View;
import android.widget.TextView;

import com.bumptech.glide.Glide;

import net.qiujuer.italker.common.app.Application;
import net.qiujuer.italker.common.app.PresenterFragment;
import net.qiujuer.italker.common.widget.PortraitView;
import net.qiujuer.italker.factory.model.db.User;
import net.qiujuer.italker.factory.persistence.Account;
import net.qiujuer.italker.factory.presenter.me.MeContract;
import net.qiujuer.italker.factory.presenter.me.MePresenter;
import net.qiujuer.italker.push.R;
import net.qiujuer.italker.push.activities.AccountActivity;

import butterknife.BindView;
import butterknife.OnClick;

/**
 * 这个是我自己添加的。
 */
public class MeFragment extends PresenterFragment<MeContract.Presenter>
        implements MeContract.View {

    @BindView(R.id.im_portrait)
    PortraitView mPortrait;

    @BindView(R.id.txt_name)
    TextView mName;

    @Override
    protected void initArgs(Bundle bundle) {
        super.initArgs(bundle);
    }

    @Override
    protected MeContract.Presenter initPresenter() {
        return new MePresenter(this);
    }

    @Override
    protected int getContentLayoutId() {
        return R.layout.me_active;
    }

    @Override
    protected void initWidget(View root) {
        super.initWidget(root);
    }

    @Override
    protected void initData() {
        super.initData();

        User user = Account.getUser();

        // 初始化头像加载
        mPortrait.setup(Glide.with(this), user);
        mName.setText(user.getName());
    }

    @Override
    protected void onFirstInit() {
        super.onFirstInit();
    }


    @OnClick(R.id.btn_quit)
    void onSubmitClick() {
        // 调用P层，进行退出
        mPresenter.quit();
        AccountActivity.show(getActivity());
 //       getActivity().finish();
        Application.getInstance().finishAll();
        //注意，退出登陆时，应该给个推发送一个注销的消息。
    }
}
