package www.walle.ai.net;

/** MQTT 连接配置（可变模型）。 */
public class MqttConfig {
    public String host = Topics.DEFAULT_HOST;
    public int port = Topics.DEFAULT_PORT;
    /** 不设默认账号：首次为空，由用户在设置页输入后持久化到本地 */
    public String username = "";
    public String password = "";
    public String clientId = Topics.DEFAULT_CLIENT_ID;
    public boolean autoReconnect = true;
    public boolean autoConnectOnLaunch = true;
    /**
     * true = 走 TLS（ssl://），false = 明文（tcp://）。
     * <p>默认值与默认端口绑定：默认端口是 8883（EMQX ssl 监听）即默认开启 TLS，
     * 只有显式把端口改成 1883 明文端口时才会关闭。</p>
     */
    public boolean tls = isTlsPort(Topics.DEFAULT_PORT);
    /** 状态自动刷新间隔（秒），<=0 表示不自动刷新 */
    public int statusIntervalSec = 5;

    public String brokerUri() {
        return (tls ? "ssl://" : "tcp://") + host + ":" + port;
    }

    /** 该端口是否为 MQTT 的 TLS 标准端口（EMQX 默认 8883，WebSocket-TLS 8084）。 */
    public static boolean isTlsPort(int port) {
        return port == 8883 || port == 8084 || port == 8884;
    }

    /** 该端口是否为 MQTT 的明文标准端口（EMQX 默认 1883，WebSocket 8083）。 */
    public static boolean isPlainPort(int port) {
        return port == 1883 || port == 8083;
    }

    public MqttConfig copy() {
        MqttConfig c = new MqttConfig();
        c.host = host;
        c.port = port;
        c.username = username;
        c.password = password;
        c.clientId = clientId;
        c.autoReconnect = autoReconnect;
        c.autoConnectOnLaunch = autoConnectOnLaunch;
        c.tls = tls;
        c.statusIntervalSec = statusIntervalSec;
        return c;
    }
}
