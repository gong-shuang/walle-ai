package www.walle.ai.model;

import org.json.JSONObject;

/**
 * 控制权租约持有者（{@code runState/query/response} 与 {@code control/*} 应答里的
 * {@code control_holder}）。
 *
 * <p>没有 holder 时设备 fail-open：所有人可以直接控制；有人 acquire 后才形成独占锁。
 * 因此 {@code null} 表示「空闲，可直接控制」。</p>
 */
public final class ControlHolder {

    public final String clientId;
    public final String clientName;
    /** 获取时间（Unix 秒）。 */
    public final long acquiredAt;
    /** 到期时间（Unix 秒）。 */
    public final long expiresAt;
    /** 剩余秒数（设备端计算）。 */
    public final int remainingSec;
    /** 是否为当前 App 自己（由 MqttManager 比较 client_id 后填充）。 */
    public final boolean self;

    private ControlHolder(String clientId, String clientName, long acquiredAt,
                          long expiresAt, int remainingSec, boolean self) {
        this.clientId = clientId;
        this.clientName = clientName;
        this.acquiredAt = acquiredAt;
        this.expiresAt = expiresAt;
        this.remainingSec = remainingSec;
        this.self = self;
    }

    /** 解析 control_holder；null/空对象/缺少 client_id 时返回 null（表示空闲）。 */
    public static ControlHolder parse(JSONObject o, String selfClientId) {
        if (o == null || o.isNull("client_id")) return null;
        String id = o.optString("client_id", "");
        if (id.isEmpty()) return null;
        return new ControlHolder(
                id,
                o.optString("client_name", ""),
                o.optLong("acquired_at", 0L),
                o.optLong("expires_at", 0L),
                o.optInt("remaining_sec", 0),
                id.equals(selfClientId == null ? "" : selfClientId));
    }

    /** 展示名：客户端名称优先，缺失时退回 client_id。 */
    public String displayName() {
        return clientName == null || clientName.isEmpty() ? clientId : clientName;
    }
}
