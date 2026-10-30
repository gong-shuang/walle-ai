# 升级到 MQTT 5 的评估：工作量、收益与风险

> 评估日期：2026-10-30　对应技术债 `I4`（Paho 1.2.5 / MQTT 3.1.1）
> 结论先行：**现在不建议做**。若要做，拆成两步，第一步（换客户端库 + 保持协议语义不变）约 **5–8 人日**，第二步（真正吃到 MQTT5 收益：响应主题替换 `request_id`、去掉 O(N) 发现）再 **6–10 人日**。
> 触发条件见 [§7](#7-什么时候才值得做触发条件)。

---

## 1. 现状（代码级事实，不是猜的）

| 维度 | 现状 | 位置 |
|---|---|---|
| 客户端库 | Paho Java **1.2.5**，MQTT **3.1.1** | `app/build.gradle:114` |
| Paho 接触面 | **只在 1 个文件里**：`net/MqttClient.java`（370 行）；全项目 `org.eclipse.paho` 的 import 仅 9 行、API 调用点 103 处，全部集中在此 | `net/MqttClient.java` |
| 「协议无关」的上层 | `MqttManager`(484) / `MessageRouter`(162) / `TopicNames`(229) / `DeviceRegistry`(228) 都不认识 Paho，只处理主题与 JSON | `net/` |
| 订阅模型 | 固定 **8 个**响应主题（`cleanSession=true`，每次连上都重订） | `TopicNames.subscriptions()` |
| 请求配对 | 自制 `request_id`（8 位 hex）+ 按 id 严格配对；6 类请求 | `MqttManager` / 设备 `REQUEST_HANDLERS` |
| 去重 | 2 秒窗口去重（QoS1 至少一次语义下的重复投递） | `MessageRouter` |
| 设备端 | **paho-mqtt 2.x（Python）**，已用 `CallbackAPIVersion.VERSION2` 回调签名 | `linux/rpi_monitor.py:1927` |
| Broker | EMQX **5.x / 6.x**（原生支持 MQTT5，无需升级） | `linux/README.md:61` |
| ACL | File 规则，已用 `${clientid}` 占位符 | `linux/emqx/acl.conf.example` |
| 单测 | 50 例，**全部不依赖 Paho**（主题/路由/注册表/告警/快照/摄像头能力） | `app/src/test/` |

**这意味着换协议最大的好消息：传输层是隔离的**。换库 = 重写 `MqttClient.java` 一个文件 + 设备端 `connect/publish` 几处，业务层几乎不动。

---

## 2. 两条技术路线

| | A. Paho MQTT v5 客户端 | B. HiveMQ MQTT Client |
|---|---|---|
| 依赖 | `org.eclipse.paho:org.eclipse.paho.mqttv5.client`（**另一个 artifact，包名不同**） | `com.hivemq:hivemq-mqtt-client`（Reactive/异步，MQTT5 原生） |
| 改造量 | 中：同族 API，`MqttConnectOptions`→`MqttConnectionOptions`、回调签名变化 | 大：回调模型换成 `Mqtt5AsyncClient` + `CompletableFuture`，需重写发布队列（我们自己那一套可以删） |
| 维护活跃度 | **低**：1.2.5 是 2020 年的版本，之后基本无功能迭代，安全修复不保证 | **高**：持续发版，MQTT5 特性齐全 |
| Android | 纯 Java + 系统 TLS，无 native 依赖，风险最低 | 引入 Netty：需排除 native transport、加 R8 keep 规则、APK +1~3 MB；官方文档支持 Android（minSdk 需 ≥24，本项目 26 达标），但**必须实测 release 包** |
| 长期 | 换一次等于把债挪到 3 年后 | 一次到位 |
| 预估 | **4–6 人日** | **7–10 人日** |

> 我的建议：如果决定换，选 **B**。选 A 等于「用 3 天工作量换到一个 2020 年后没更新的库上」，而 `I4` 的原始动机之一恰恰是「客户端库无人维护」。
> 参考：[Paho MQTTv5 客户端说明](http://gitea.hyhsystem.cn/eclipse/paho.mqtt.java/src/commit/01926a23fb1b5f0f9a4da735af14b181089ff1b7/MQTTv5.md?display=source)、[HiveMQ 客户端 Android 安装说明](https://hivemq.github.io/hivemq-mqtt-client/docs/installation/android/)、[HiveMQ Android 支持说明](https://www.hivemq.com/blog/hivemq-mqtt-client-features-android-support/)（外部链接，请在联网环境复核版本号与要求）。

---

## 3. 工作量分解

### 第一步：换客户端 + 保持协议语义不变（低风险，可独立上线）

| # | 工作 | 人日 |
|:--:|---|:--:|
| 1 | 依赖切换（artifact / Netty 排除 / R8 keep / 打包体积与 release 冒烟） | 0.5–1 |
| 2 | 重写 `MqttClient.java`：连接选项（`cleanStart=true` + `SessionExpiryInterval=0` 等价于原 `cleanSession=true`）、TLS、自动重连、订阅、发布队列、错误码映射 | 2–3 |
| 3 | 错误文案改造：`reason code`（0x87 未授权 / 0x9C 主题无效 / 0x97 超配额…）映射到现有中文提示，替换 Paho 的 `32100/32104/32110` 魔法数字 | 0.5–1 |
| 4 | 设备端：`protocol=MQTTv5`、`reason_code` 处理、`publish` 返回值判定、`will_set` 的 `WillDelayInterval` | 1–2 |
| 5 | 回归：50 单测 + `selftest_topics.py` 扩展 v5 断言 + 真机/设备联调（含断网重连、TLS、ACL 拒绝路径） | 2–4 |
| | **合计** | **6–10** |

产出：全部 8 个主题、`request_id` 配对、去重、`online`/`alarm` 行为**完全不变**，只是底层换了协议版本。这一步单独做没有用户可感知价值，唯一收益是「客户端库换成有人维护的」。

### 第二步：吃 MQTT5 的收益（需要产品/架构决策）

| # | 工作 | 人日 |
|:--:|---|:--:|
| 6 | 响应主题 + `CorrelationData`：请求带 `ResponseTopic=walle/{t}/app/{myClientId}/result`，设备按它回包；App 只订 1 个主题；退役 8 个固定响应主题与 `request_id`（保留 2 秒去重） | 2–3 |
| 7 | ACL 放开：设备 publish 需要 `walle/{t}/app/+/#`（打破「设备只能写自己子树」的隔离，见 §6.1） | 0.5 |
| 8 | 设备发现改造：设备按请求里的 `ResponseTopic` 回发现结果 → **N7 的 O(N) 问题顺带解决** | 1–2 |
| 9 | 双栈/兼容期：v5 App ↔ 3.1.1 设备（线上旧设备）互操作验证 + 特性探测降级 | 2–3 |
| 10 | 文档重写：`linux/README.md` 排障表、协议文档、`doc/` 全部按 v5 术语更新 | 1 |
| | **合计（叠加第一步）** | **13–19** |

---

## 4. 收益（按对本项目的真实价值排序）

| 收益 | 说明 | 对本项目价值 |
|---|---|:--:|
| **① 请求/响应正名** | `ResponseTopic` + `CorrelationData` 替代自制的 `request_id` + 8 个固定响应主题；设备把结果直达请求方 | **高**：顺带解决 N7（租户内广播 O(N)），订阅/ACL 从「每条资源一个主题」收敛为「每个 App 一个主题」 |
| **② Reason Code** | 区分「ACL 拒绝(0x87)」「主题无效」「会话/配额超限」「设备真的执行失败」；现在全塞进 payload 的 `result`/`error`，排障靠 `linux/README` 的对照表 | **高**：现场支持成本直接下降 |
| **③ Will Delay** | 设备重启 3 秒内重连不再产生「离线→在线」抖动 | **中高**：直接减少告警噪音（`online` LWT 抖一次 = 一条告警 + 红点） |
| **④ Message Expiry** | retained `online` 过期自动失效，broker 里不再躺「幽灵在线」 | **中**：现在靠「未知设备 offline 忽略 + 2s 去重」兜底，属打补丁 |
| **⑤ Session Expiry + 离线队列** | 为「设备离线期间的命令/告警补发」铺路 | **低（当前无此需求）**；产品真做「离线告警补偿」时才变高 |
| **⑥ 主题别名 / 订阅标识 / 订阅选项** | 减少报文头开销、细化订阅 | **≈0**：8 个固定主题、流量极小 |
| **⑦ 生态一致性** | EMQX 5.x 默认全开 v5，Dashboard/日志里协议版本统一 | **低** |

一句话：**第 ①②④ 条是真收益，但都不是当前用户在抱怨的问题**。

---

## 5. 兼容性事实（决定灰度策略）

- MQTT 协议版本是**按连接**协商的：同一 broker 上 v5 客户端与 3.1.1 客户端可以互通。**服务端向 3.1.1 客户端投递时会丢弃 v5 属性**（报文格式不同），其余不变。
- 所以：**v5 App ↔ 老的 3.1.1 设备可以工作**，只是拿不到 `ResponseTopic`/reason code 这些 v5-only 能力 → 可以做特性探测（设备 `on_connect` 回报协议版本，App 按版本选主题方案）。
- 反之：**3.1.1 App ↔ v5 设备**同样能工作。**不需要一刀切强制升级**，这是最大的风险缓解点。
- QoS1 在 v5 里依旧是「至少一次」，**重复投递不会消失**，2 秒去重必须保留（别以为升 v5 就能删）。
- retained 消息跨版本可见，`Message Expiry` 只对 v5 发布者生效——混编期间过期策略不一致，需要接受。

---

## 6. 问题与风险（按严重度）

### 6.1 「响应主题」会打破现在的 ACL 隔离（架构级，必须先决策）

现在设备只被允许 publish 到 `walle/home/${clientid}/#`（自己的子树），这是多租户安全边界。用 `ResponseTopic` 让设备回到 `walle/home/app/{appClientId}/...` 时，设备规则必须放开到 `walle/home/app/+/#` —— 意味着**任何一台设备都能往任何 App 的响应主题发消息**（伪造别的设备的状态/发现结果）。

缓解方式（都要成本）：
- 用 `User Properties` 带 `device_id` 并在 App 端校验（防伪造，但防不了刷屏）；
- EMQX 用 `topic` 重写/规则引擎做隔离；
- 或者干脆**不用响应主题**，只在 payload 里加 `reply_to`（3.1.1 也能用），ACL 的放开程度一样。

> 关键洞察：**N7 的真正成本不是 MQTT5，而是 ACL 边界的让步**。这个问题无论用不用 MQTT5 都存在，只是 MQTT5 让它更顺手。

### 6.2 现场设备升级不可控

客户手里的设备装的是 `install.sh` 部署的旧脚本。协议不改（第一步）时无所谓；一旦走第二步的响应主题，就需要设备端也升级，而升级窗口不受我们控制 → 必须有双栈期与特性探测，工期 +2~3 天。

### 6.3 release 包风险（HiveMQ 路线特有）

项目已开 R8 + 资源压缩（`S5`）。HiveMQ 依赖 Netty，注解/反射面较大 → 需要完整 keep 规则，**且必须在 release 包上实测**（debug 包不裁剪，测不出问题）。这一步只能靠真机，沙箱里跑不了 Gradle。

### 6.4 Broker 版本假设

`linux/README` 写的是「EMQX 5.x / 6.x」，但如果现场有 4.x：`Session Expiry`、`Message Expiry`、部分 reason code 行为会退化/不一致 → 需要版本探测或在部署文档里硬性要求 5.x+。

### 6.5 共用账号（你们选的方案 A）与响应主题

家庭成员共用 `home` 账号、每台手机各自 clientId。响应主题 `walle/home/app/{clientId}/...` 在 ACL 里可用 `${clientid}` 区分，**技术上可行**，但要注意：`cleanStart=true` 下每台手机只收到自己发起请求的响应；一旦将来改成持久会话，就会收到别的手机的历史响应 —— 需要在文档里明确约束。

### 6.6 排障与文档成本翻倍

reason code、properties、session expiry、will delay 会让现场支持文档、日志格式、`selftest_topics.py` 全部更新。粗估 1 人日/次，且新人学习曲线变陡。

### 6.7 客户端库的「两难」

Paho v5 客户端（A 路线）**不解决「库无人维护」这个原始问题**；HiveMQ（B 路线）解决了，但要吃 Netty 的包体和 R8 风险。没有「零成本」选项。

### 6.8 收益延迟兑现

第 ①②④ 条收益要等第二步（+7 人日）才拿到；第一步只是换库。**如果只做第一步，用户侧零感知**——要提前跟收益预期对齐。

---

## 7. 什么时候才值得做（触发条件）

建议把 `I4` 保持「忽略」，但在文档里写死触发条件，避免将来临时救火：

1. **在线设备数 > 20**：发现报文变成 `N × 手机数` 的广播风暴，N7 开始影响体验 → 做第二步（响应主题）。
2. **产品要做「离线告警/命令补发」**：只有 `SessionExpiryInterval > 0` + QoS1 会话队列能干净实现 → 第一步 + ⑤。
3. **现场排障成本持续走高**（比如每月都有人因为「ACL 拒了」排查半天）→ 做第一步拿 reason code。
4. **Paho 1.2.5 出现无法绕过的 CVE** → 必须换库，直接走 B 路线。

## 8. 建议路线

```
现在：       不动。把触发条件写进 doc（本次已写）。
触发①②：    B 路线第一步（换 HiveMQ + 语义不变，6–10 人日）→ 稳定两周
             → 第二步（响应主题 + 发现改造 + ACL 决策，+7–9 人日）
只触发③：    B 路线第一步即可，第二步排队
只触发④：    立刻 B 路线第一步（安全例外，不做评估）
```

**不要做的两件事**：
1. 不要为了「版本新」而换（8 个主题、QoS1、几十台设备，MQTT 3.1.1 完全够用）。
2. 不要用 Paho v5 客户端做过渡（等于把债从「3.1.1 老库」换成「v5 老库」，还多付一次迁移成本）。

---

## 9. 一句话总结

> MQTT5 的收益是**架构层面的整洁与未来能力**（请求/响应语义、reason code、会话过期），不是当前痛点的解药；换库成本 6–10 人日、拿到收益再 +7–9 人日，且最大代价是**放弃「设备只能写自己子树」的 ACL 隔离**。建议等设备上量或出现离线补偿需求时再启动，并把这条路线的技术选型提前锁定为 **HiveMQ 客户端**。
