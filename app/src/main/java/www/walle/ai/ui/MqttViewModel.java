package www.walle.ai.ui;

import android.app.Application;

import androidx.annotation.NonNull;
import androidx.lifecycle.AndroidViewModel;

import www.walle.ai.net.MqttManager;

/**
 * 所有页面 ViewModel 的基类：统一负责「注册/注销 MQTT 监听」。
 *
 * <p>页面（Fragment/Activity）不再实现 {@link MqttManager.Listener}：
 * 监听只存在于 ViewModel 生命周期内，回调里不接触 View，因此不会出现
 * 「回包到达时 View 已销毁」这类问题；旋转时 ViewModel 保留状态，也不会重复查询。</p>
 */
public abstract class MqttViewModel extends AndroidViewModel implements MqttManager.Listener {

    protected final MqttManager mgr;

    protected MqttViewModel(@NonNull Application app) {
        super(app);
        mgr = MqttManager.get(app);
        mgr.addListener(this);
    }

    @Override
    protected void onCleared() {
        mgr.removeListener(this);
    }
}
