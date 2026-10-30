# 多租户 MQTT 主题（协议 v2）

> 状态：**已完成并上线使用**。v1 的全局主题（`device/online`、`cmd/exec` 等）已从 Android 与设备端全部删除。
> 相关实现：App `net/TopicNames.java`、`net/MqttManager.java`；设备 `device/rpi_monitor.py`。

---

## 1. 为什么改成主题路径带租户

v1 把所有设备与用户放在同一批全局主题上，只靠 payload 里的 `uuid` 过滤：报文已经投递给所有订阅者，Broker 无法拒绝，也就无法按用户隔离。

v2 把「租户 + 稳定设备 ID」编进主题：

```
walle/{tenant}/{device}/{resource}
```

- `tenant` = App 登录的用户名（清洗后）→ Broker 可直接用 `walle/${username}/#` 做 ACL；
- `device` = 设备稳定的 `device_id`（MAC + CPU 核心数 + 内存容量的 md5 前 16 位，重启/重装不变）；
- 两者都只允许 `[A-Za-z0-9_-]`，`.` `:` `@` 空格转 `_`，其余丢弃（App 与设备端实现一致，`selftest_topics.py` 做交叉校验）。

---

## 2. 主题契约

| 用途 | 请求 `…/query` | 应答 `…/result` | 说明 |
|---|---|---|---|
| 设备发现 | `walle/{t}/discovery/query` | `walle/{t}/{d}/discovery/result` | 租户级广播；应答是设备身份详情 |
| 状态 | `.../state/query` | `.../state/result` | 完整状态快照 |
| 命令 | `.../cmd/exec` | `.../cmd/result` | 请求带 `request_id`、`timeout` |
| 摄像头 | `.../camera/capability/query` | `.../camera/capability/result` | v4l2 枚举结果 |
| 推流 | `.../video/start/request` | `.../video/start/result` | 带 `room_number` |
| 停止推流 | `.../video/stop/request` | `.../video/stop/result` | 带 `room_number` |
| 上线/下线（主动） | — | `.../online` | retained + LWT |
| 告警（主动） | — | `.../alarm` | 告警 / 恢复 / USB |

> **命名约定**：请求按语义划分（查询 `…/query`、命令 `…/exec`、动作 `…/request`），应答一律 `…/result`；
> `/result` 主题只在收到对应请求后发布，主动上报只有 `online` 与 `alarm`。
>
> **配对（N4）**：所有请求都带 `request_id`，设备在所有 `…/result` 里原样回显。

### 2.1 request_id 配对语义

| 资源 | 请求带 | 应答回显 | App 侧行为 |
|---|---|---|---|
| `cmd/exec` → `cmd/result` | ✅ | ✅ | 严格配对：mismatch 丢弃（`CommandFragment` 已有） |
| `video/start/request` → `video/start/result` | ✅ | ✅ | 严格配对 + 8s 超时复位；区分「首次启动」与「切换摄像头」不再靠布尔位 |
| `video/stop/request` → `video/stop/result` | ✅ | ✅ | 严格配对，忽略迟到结果 |
| `camera/capability/query` → `.../result` | ✅ | ✅ | 严格配对：旧查询的迟到结果不覆盖当前列表 |
| `state/query` → `state/result` | ✅ | ✅ | 宽松：数据照常渲染，只有 `request_id` 匹配时才结束「查询中」 |
| `discovery/query` → `discovery/result` | ✅ | ✅ | 宽松：一次广播多台设备用同一个 rid 各自回包，全部接收（含型号/序列号等详情） |

`request_id` 与 `uuid` 的区别：`uuid` 标识「哪台设备实例」（进程级），`request_id` 标识「哪一次请求」（每请求新生成）。

约定：

1. 除 `discovery/query` 外，请求必须带匹配的 `uuid`，否则设备静默忽略；
2. 设备只订阅自己子树下的**请求类**主题，不订通配子树（避免收到自己发布的上报）；
3. 上线报文 retained + 遗嘱，App 打开即可看到在线状态；
4. QoS 1，Keep Alive 60s。

---

## 3. App 侧实现

| 位置 | 职责 |
|---|---|
| `net/TopicNames.java` | 主题拼装/解析、段清洗、资源名与事件种类常量 |
| `net/DeviceInfo.java` | `topicDeviceId()`：device_id → hostname → uuid 的稳定设备段 |
| `net/MqttManager.java` | `tenant = TopicNames.tenantOf(username)`；订阅 8 个「设备 → App」主题（`walle/{tenant}/+/{online,alarm,discovery/result,state/result,cmd/result,camera/capability/result,video/start/result,video/stop/result}`）；按资源名分发；2 秒窗口去重（挡 QoS1 重复投递）；发布失败回调带「事件种类」而非主题名 |

> **升级遗留订阅的坑**：App 用 `cleanSession=false`（会话持久化），broker 会把该 clientId 的订阅一直保留。
> 于是从旧版本升级后会出现「新旧订阅并存」：旧版订过 `walle/{tenant}/#` 与 v1 全局主题，新版又订 8 个精确主题
> → 同一条 `identity` 被投递两次、自发 `discovery/query` 也被 `#` 收回来。
> 修复方式：每次连接先 `unsubscribe` 一组「旧过滤器黑名单」（`TopicNames.staleSubscriptionFilters()`，
> 只用于清理、不参与收发），再订阅本版本需要的主题。日志会打印 `cleared N stale subscriptions`。

> 为什么不是订阅 `walle/{tenant}/#`：MQTT 3.1.1 没有 no-local 标志，broker 会把客户端自己发布的
> 报文也投递回来。App 会在同一个租户空间里发布 `state/query`、`cmd`、`video/start`、`discovery/query`，
> 订通配子树就会收到自己的请求（日志表现为 `← RECV walle/home/discovery/query`）。改为按响应主题精确订阅后没有这个问题。

App 的租户由「设置页里的 MQTT 用户名」推导，**没有单独的租户输入框**：家庭多人共用账号时，所有人填同一个账号即可看到同一批设备。

---

## 4. EMQX 侧（6.x / 5.x，ACL File 模式）

设备与手机的账号分开，ACL 按账号授权：

```erlang
%% 手机端：读写本租户整棵子树
{allow, {username, {re, "^home$"}}, all, ["walle/home/#"]}.

%% 设备端：只读写自己的子树 + 收租户内发现广播
{allow, {username, {re, "^RaspPi5$"}}, publish,   ["walle/home/${clientid}/#"]}.
{allow, {username, {re, "^RaspPi5$"}}, subscribe, ["walle/home/${clientid}/#",
                                                   "walle/home/discovery/query"]}.
```

完整文件见 [`device/emqx/acl.conf.example`](../device/emqx/acl.conf.example)。

> 占位符是 EMQX 5/6 的写法：`${username}`、`${clientid}`；`%u` 是 Mosquitto / EMQX 4 的写法，在 6.x 上不生效。
> 若版本不支持 `${clientid}`，把设备规则里的 `${clientid}` 换成 `+`。

---

## 5. 验收

```bash
# 手机账号：能看到本租户的全部报文（含 retained 的 online）
mosquitto_sub -h <broker> -p 8883 -u home -P '<口令>' -t 'walle/home/#' -v

# 触发一次状态查询
mosquitto_pub -h <broker> -p 8883 -u home -P '<口令>' \
  -t 'walle/home/<DEVICE_ID>/state/query' -m '{"uuid":"<设备uuid>"}'

# 越权检查：换账号订阅本租户，应「连得上但收不到」
mosquitto_sub -h <broker> -p 8883 -u other -P '<口令>' -t 'walle/home/#' -v

# 设备账号不能读别的租户
mosquitto_sub -h <broker> -p 8883 -u RaspPi5 -P '<口令>' -t 'walle/other/#' -v
```

App 侧回归：设备列表 / 状态 / 命令 / 摄像头 / 推流 / 告警全部正常。

---

## 6. 已知限制

- **遗嘱只有一条**：MQTT 3.1.1 一条连接只能设一个 LWT，因此下线报文只在 `walle/{t}/{d}/online` 上；跨租户共享设备的方案（一个设备属于多个租户）需要额外的心跳判活，目前不支持。
- **视频不在 MQTT 隔离范围**：WHEP 拉流只校验 `room_number`，知道房间号即可观看；分享给外人后请及时停止推流。
- **家庭共享账号**：无法按人审计、无法单独踢人，成员离开需改一次口令。要细化时再升级为「一人一号 + 组租户」。
