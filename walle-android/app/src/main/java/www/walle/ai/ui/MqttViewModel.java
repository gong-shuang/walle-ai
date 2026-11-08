package www.walle.ai.ui;

import android.app.Application;
import android.os.Handler;
import android.os.Looper;

import androidx.annotation.NonNull;
import androidx.lifecycle.AndroidViewModel;
import androidx.lifecycle.MutableLiveData;

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
    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    protected MqttViewModel(@NonNull Application app) {
        super(app);
        mgr = MqttManager.get(app);
        mgr.addListener(this);
    }

    @Override
    protected void onCleared() {
        mgr.removeListener(this);
        mainHandler.removeCallbacksAndMessages(null);
    }

    protected final boolean onMainThread() {
        return Looper.myLooper() == Looper.getMainLooper();
    }

    /**
     * 保证在主线程执行。
     *
     * <p>MQTT 回调由 {@code MqttManager} 统一 post 到主线程，但 <b>WebRTC 的回调不是</b>：
     * {@code PeerConnection.Observer} 来自 native 的 signaling 线程。ViewModel 里的状态字段
     * 与 LiveData 都不是线程安全的，所以入口统一走这里。</p>
     */
    protected final void runOnMain(@NonNull Runnable action) {
        if (onMainThread()) action.run();
        else mainHandler.post(action);
    }

    /**
     * 单次事件（toast 之类）的安全 setValue：主线程立即生效，其它线程切主线程。
     *
     * <p>不用 {@code postValue} 是为了避免「post 的旧值覆盖 set 的新值」这类交错。</p>
     */
    protected final <T> void emit(@NonNull MutableLiveData<T> live, T value) {
        if (onMainThread()) live.setValue(value);
        else mainHandler.post(() -> live.setValue(value));
    }
}
