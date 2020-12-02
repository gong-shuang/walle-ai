package net.qiujuer.italker.factory.presenter.me;

import net.qiujuer.italker.factory.Factory;
import net.qiujuer.italker.factory.persistence.Account;
import net.qiujuer.italker.factory.presenter.BasePresenter;

public class MePresenter extends BasePresenter<MeContract.View>
        implements MeContract.Presenter {

    public MePresenter(MeContract.View view) {
        super(view);
    }

    @Override
    public void quit() {
        // 清除 xml 文件
        Account.clear(Factory.app());
    }
}
