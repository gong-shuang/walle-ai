package www.walle.ai.net;

/**
 * 多租户 MQTT 主题（协议 v1）的命名规则。
 *
 * <p>把租户与设备编码进主题路径，Broker 就能按 {@code walle/${username}/#} 做 ACL：</p>
 * <pre>
 * walle/{tenant}/{device}/v1/{资源}
 * </pre>
 * <ul>
 *   <li>{@code tenant}：租户标识，默认取 MQTT 用户名（同一账号 = 同一租户），
 *       这样 Broker 端可以直接用 {@code walle/${username}/#} 做 ACL；</li>
 *   <li>{@code device}：<b>稳定</b>的设备标识（{@code device_id}，回退 hostname、最后 uuid），
 *       不能用每次重启都会变的 uuid；</li>
 *   <li>{@code 资源}：请求按语义命名 —— 查询用 {@code .../query}、
 *       动作用 {@code .../request}（推流）、持续控制用 {@code .../set}（车控）；
 *       应答一律 {@code .../result}，且只在收到对应请求后发布；
 *       主动上报只有 {@code online} 与 {@code alarm}。</li>
 * </ul>
 *
 * <p>示例：{@code walle/home/666feb85d43410b2/state/query} →
 * {@code walle/home/666feb85d43410b2/state/result}。</p>
 *
 * <p>本类只做字符串拼装与解析，不依赖 Android，方便单测。任何一段都必须满足
 * {@code [A-Za-z0-9_-]{1,64}}，否则会被 {@link #sanitize(String)} 清洗或替换成哈希，
 * 避免主题出现 {@code +}、{@code #}、{@code /} 这类通配/分隔符导致 ACL 失效。</p>
 */
public final class TopicNames {

    /** 所有主题的根前缀。 */
    public static final String ROOT = "walle";

    /**
     * 主题协议版本段：{@code walle/{tenant}/{device}/v1/{resource}}。
     * <p>报文结构升级时新增 v2 段，旧设备继续用 v1，两套主题可以并存。</p>
     */
    public static final String PROTOCOL_VERSION = "v1";

    private static final int MAX_SEGMENT = 64;

    private TopicNames() {}

    // ---------------------------------------------------------------- 基础

    /** 该 topic 是否为本协议的命名空间主题。 */
    public static boolean isNamespaced(String topic) {
        return topic != null && topic.startsWith(ROOT + "/");
    }

    // ---------------------------------------------------------------- 事件种类（用于错误回调）

    /** 设备发现请求失败 */
    public static final String KIND_DISCOVERY = "discovery";
    /** 状态查询失败 */
    public static final String KIND_STATE = "state/query";
    /** 设备能力查询失败 */
    public static final String KIND_CAPABILITY = "capability/query";
    /** 推流启动失败 */
    public static final String KIND_VIDEO_START = "video/start";
    /** 停止推流失败 */
    public static final String KIND_VIDEO_STOP = "video/stop";
    /** 开启手机麦克风音频上行失败 */
    public static final String KIND_PHONE_AUDIO_SEND = "phone/audio/send";
    /** 关闭手机麦克风音频上行失败 */
    public static final String KIND_PHONE_AUDIO_STOP = "phone/audio/stop";
    /** 车控持续命令失败 */
    public static final String KIND_CAR_MOTOR = "car/motor/set";
    /** 舵机状态查询失败 */
    public static final String KIND_CAR_SERVO_STATUS = "car/servo/status/query";
    /** 舵机位置设置失败 */
    public static final String KIND_CAR_SERVO_SET = "car/servo/set";

    // ---------------------------------------------------------------- 清洗

    /**
     * 把任意字符串收敛成合法的主题片段：仅保留 {@code [A-Za-z0-9_-]}，长度 1–64。
     * <p>为空或清洗后为空时返回 {@code "unknown"}；超长时用哈希截断，保证同一个输入
     * 总是得到同一个片段（ACL 与设备端拼装结果必须一致）。</p>
     */
    public static String sanitize(String raw) {
        if (raw == null) return "unknown";
        StringBuilder sb = new StringBuilder(raw.length());
        for (int i = 0; i < raw.length() && sb.length() < MAX_SEGMENT; i++) {
            char c = raw.charAt(i);
            if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')
                    || (c >= '0' && c <= '9') || c == '_' || c == '-') {
                sb.append(c);
            } else if (c == '.' || c == ':' || c == '@' || c == ' ') {
                // 常见于邮箱/主机名/MAC，统一换成下划线，保持可读
                sb.append('_');
            }
        }
        if (sb.length() == 0) return "unknown";
        if (raw.length() > MAX_SEGMENT) {
            // 超长时保留前 40 位 + 短哈希，保证唯一且稳定
            String hash = Integer.toHexString(raw.hashCode());
            return sb.substring(0, Math.min(40, sb.length())) + "-" + hash;
        }
        return sb.toString();
    }

    /** 租户标识：默认就是 MQTT 用户名（经清洗）。 */
    public static String tenantOf(String username) {
        return sanitize(username == null || username.trim().isEmpty() ? "default" : username);
    }

    // ---------------------------------------------------------------- 拼装

    /** 设备子树前缀：{@code walle/{tenant}/{device}/v1} */
    public static String deviceBase(String tenant, String device) {
        return ROOT + "/" + sanitize(tenant) + "/" + sanitize(device) + "/" + PROTOCOL_VERSION;
    }

    /** 租户级广播：设备发现（App → 设备），{@code walle/{tenant}/v1/discovery/query}。 */
    public static String discoveryQuery(String tenant) {
        return ROOT + "/" + sanitize(tenant) + "/" + PROTOCOL_VERSION + "/discovery/query";
    }

    // ================= 设备 → App =================
    // 命名约定：请求用 .../query，应答用 .../result；以 /result 结尾的主题设备只在收到
    // 对应查询时才发布，绝不主动上报。主动上报只有 online 与 alarm（不带 /result）。

    /** 上线/下线（主动上报，retained + LWT）。 */
    public static String online(String tenant, String device) {
        return deviceBase(tenant, device) + "/online";
    }

    /** 设备发现应答（收到 walle/{tenant}/discovery/query 后发布，payload 为身份详情）。 */
    public static String discoveryResult(String tenant, String device) {
        return deviceBase(tenant, device) + "/discovery/result";
    }

    /** 状态快照（收到 state/query 后发布）。 */
    public static String stateResult(String tenant, String device) {
        return deviceBase(tenant, device) + "/state/result";
    }

    /** 告警 / 恢复 / USB 插拔（主动上报）。 */
    public static String alarm(String tenant, String device) {
        return deviceBase(tenant, device) + "/alarm";
    }

    /** 设备能力结果（收到 capability/query 后发布，含摄像头/麦克风/音响等外设）。 */
    public static String capabilityResult(String tenant, String device) {
        return deviceBase(tenant, device) + "/capability/result";
    }

    /** 推流启动结果（收到 video/start/request 后发布）。 */
    public static String videoStartResult(String tenant, String device) {
        return deviceBase(tenant, device) + "/video/start/result";
    }

    /** 停止推流结果（收到 video/stop/request 后发布）。 */
    public static String videoStopResult(String tenant, String device) {
        return deviceBase(tenant, device) + "/video/stop/result";
    }

    /** 车控持续命令结果（收到 car/motor/set 后发布）。 */
    public static String carMotorResult(String tenant, String device) {
        return deviceBase(tenant, device) + "/car/motor/result";
    }

    /** 舵机状态查询结果（收到 car/servo/status/query 后发布）。 */
    public static String carServoStatusResult(String tenant, String device) {
        return deviceBase(tenant, device) + "/car/servo/status/result";
    }

    /** 舵机位置设置结果（收到 car/servo/set 后发布）。 */
    public static String carServoResult(String tenant, String device) {
        return deviceBase(tenant, device) + "/car/servo/result";
    }

    /**
     * App 需要订阅的主题（只订「设备 → App」的响应类，不订请求类）。
     *
     * <p>为什么不直接订 {@code walle/{tenant}/#}：MQTT 3.1.1 没有 no-local 语义，
     * 客户端会收到自己发布的报文。App 会在租户内发布 state/query、video/start/request 等请求，
     * 订通配子树就会把自己的请求再收一遍（日志里表现为 {@code ← RECV walle/home/discovery/query}），
     * 白白浪费带宽并干扰排查。</p>
     */
    public static String[] responseSubscriptions(String tenant) {
        String base = ROOT + "/" + sanitize(tenant) + "/+/" + PROTOCOL_VERSION;
        return new String[]{
                base + "/online",                  // 主动上报
                base + "/alarm",                   // 主动上报
                base + "/discovery/result",        // 查询应答
                base + "/state/result",
                base + "/capability/result",
                base + "/video/start/result",
                base + "/video/stop/result",
                base + "/car/motor/result",
                base + "/car/servo/status/result",
                base + "/car/servo/result",
        };
    }

    // ================= App → 设备（按语义命名）=================
    /** 查询状态。 */
    public static String stateQuery(String tenant, String device) {
        return deviceBase(tenant, device) + "/state/query";
    }

    /** 查询设备能力（摄像头/麦克风/音响等外设）。 */
    public static String capabilityQuery(String tenant, String device) {
        return deviceBase(tenant, device) + "/capability/query";
    }

    /** 开始推流（动作类，用 /request）。 */
    public static String videoStartRequest(String tenant, String device) {
        return deviceBase(tenant, device) + "/video/start/request";
    }

    /** 停止推流（动作类，用 /request）。 */
    public static String videoStopRequest(String tenant, String device) {
        return deviceBase(tenant, device) + "/video/stop/request";
    }

    /**
     * 车控持续命令（App → 设备）：最新一条覆盖旧状态。
     * <p>应答为父资源 {@code car/motor/result}，不是 {@code car/motor/set/result}。</p>
     */
    public static String carMotorSet(String tenant, String device) {
        return deviceBase(tenant, device) + "/car/motor/set";
    }

    /** 舵机状态查询（App → 设备）。 */
    public static String carServoStatusQuery(String tenant, String device) {
        return deviceBase(tenant, device) + "/car/servo/status/query";
    }

    /** 舵机位置设置（App → 设备）。 */
    public static String carServoSet(String tenant, String device) {
        return deviceBase(tenant, device) + "/car/servo/set";
    }

    /**
     * 开启手机麦克风上行（App → 设备，单向动作）。
     * <p>{@code phone/audio/*} 描述的是「手机侧发起的动作」，不是设备的推流；
     * 设备收到后自行从 RTMP 拉 {@code {room}_audio} 播放。载荷含 room_number，设备据此拼拉流地址。
     * 设备端会发布 {@code .../send/result} 回执；但当前 {@link #responseSubscriptions}
     * 未订阅 phone/audio 应答，UI 收不到该回执（已知待修：应订阅并根据回执更新开关状态）。</p>
     */
    public static String phoneAudioSend(String tenant, String device) {
        return deviceBase(tenant, device) + "/phone/audio/send";
    }

    /** 关闭手机麦克风上行（App → 设备，单向动作）。 */
    public static String phoneAudioStop(String tenant, String device) {
        return deviceBase(tenant, device) + "/phone/audio/stop";
    }

    // ---------------------------------------------------------------- 解析

    /**
     * 拆解主题：{@code walle/{tenant}/{device}/v1/{resource...}}。
     *
     * @return 解析成功返回 {@code [tenant, device, resource]}（resource 可能含 {@code /}）；
     *         不是本协议主题、版本段不符或段数不足时返回 {@code null}
     */
    public static String[] parse(String topic) {
        if (!isNamespaced(topic)) return null;
        String[] parts = topic.split("/", 5);
        if (parts.length < 5 || parts[2].isEmpty() || parts[3].isEmpty() || parts[4].isEmpty()) {
            return null;
        }
        // 租户级发现主题 walle/{tenant}/v1/discovery/query 没有 device 段，
        // 不能解析成 device="v1"、resource="discovery/query"。
        if (PROTOCOL_VERSION.equals(parts[2]) && !PROTOCOL_VERSION.equals(parts[3])) return null;
        if (!PROTOCOL_VERSION.equals(parts[3])) return null;
        return new String[]{parts[1], parts[2], parts[4]};
    }

    /**
     * 从主题里取出资源段（不含租户与设备），用于分发：
     * {@code walle/t/d/v1/state/result} → {@code state/result}；{@code walle/t/d/v1/capability/result} → {@code capability/result}。
     */
    public static String resourceOf(String topic) {
        String[] p = parse(topic);
        return p == null ? null : p[2];
    }

    /** 从主题里取出设备段。 */
    public static String deviceOf(String topic) {
        String[] p = parse(topic);
        return p == null ? null : p[1];
    }
}
