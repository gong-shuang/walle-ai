package www.walle.ai.net;

import www.walle.ai.BuildConfig;

/**
 * MQTT 连接常量（协议 v2）。
 *
 * <p>主题命名统一由 {@link TopicNames} 生成：{@code walle/{tenant}/{device}/{resource}}。
 * 默认连接参数由 {@code app/build.gradle} 的 buildConfigField 注入，只包含主机与端口；
 * 用户名 / 密码由用户在 App 内输入，经 Android Keystore 加密后保存在本地。</p>
 */
public final class Topics {
    private Topics() {}

    // ---- 默认连接参数 ----
    public static final String DEFAULT_HOST = BuildConfig.MQTT_DEFAULT_HOST;
    public static final int    DEFAULT_PORT = BuildConfig.MQTT_DEFAULT_PORT;
    /** 旧版本固定 clientId，仅用于升级时识别并替换（会与其它手机互踢） */
    public static final String DEFAULT_CLIENT_ID = "android-controller";
    /** 每次安装唯一的 clientId 前缀：android-controller-xxxxxx */
    public static final String CLIENT_ID_PREFIX = "android-controller-";

    public static final int KEEP_ALIVE = 60;
    public static final int QOS = 1;
}
