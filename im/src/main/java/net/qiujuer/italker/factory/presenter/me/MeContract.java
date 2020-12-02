package net.qiujuer.italker.factory.presenter.me;

import net.qiujuer.italker.factory.presenter.BaseContract;

public interface MeContract {

    interface View extends BaseContract.View<Presenter> {

    }

    interface Presenter extends BaseContract.Presenter {

        // 退出
        void quit();
    }
}
