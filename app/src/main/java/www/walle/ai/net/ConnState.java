package www.walle.ai.net;

/** MQTT 连接状态。UI 与设置页都基于它渲染状态文案。 */
public enum ConnState {
    DISCONNECTED,
    CONNECTING,
    CONNECTED,
    ERROR
}
