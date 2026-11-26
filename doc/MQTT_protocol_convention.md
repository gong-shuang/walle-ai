# MQTT 协议约定（文档版本 v1.24）

> 本文是 控制端（App/Web）、代理端（agent）、设备端（ros2设备和非ros2设备）之间 MQTT 报文协议的契约
> **唯一权威文档**，所有 MQTT 主题定义统一收敛到本文。
> 改主题、改字段、改配对逻辑之前先读完本文，并按文末「修改协议的流程」同步各端与测试。
>
> - 本文档的版本：v1.25 （记录每次文档变更，本文档的内容可新增，可修改，但是不能删除，对于不需要的笔记废弃即可）
> - MQTT协议本身的版本：MQTT 3.1.1 （即，服务器部署的MQTT的协议版本）。
> 

---

## 1. 总览

```
┌──────────┐                     .../device/{device}/{resource}   ┌──────────────┐
│  web/App │ ◄───────── MQTT Broker ──────────────────────────►   │ Linux 设备端  │
└──────────┘         （walle-server）                              └──────────────┘
                            |
                            |  .../agent/{agent}/{resource}
                      ┌──────────┐
                      │  agent   │
                      └──────────┘
```

主题结构：

```
# 设备端格式
walle/{tenant}/device/{device}/{resource}

# agent端格式
walle/{tenant}/agent/{agent}/{resource}
```

- `tenant`：租户标识 = **App 登录用的 MQTT 用户名**（经清洗）。同一账号 = 同一租户。
- `device`（第 3 段）：固定字面量 `device`，用来把设备级主题与租户级、agent级广播区分开。
- `agent`（第 3 段）：固定字面量 `agent`，用来把agent级主题与租户级、设备级广播区分开。
- `{device}`（第 4 段）：**稳定**的设备标识 `device_id`，其值就是MQTT中的 client_id。首次启动由设备端按 `md5(MAC | CPU 物理核数 | 内存 GiB)` 取前 16 位，
  随后**落盘复用**（`main.resolve_device_id`），因此重启、重装、换 IP 都不会变。
  注意内存必须按 GiB 取整：`MemTotal` 在两次启动之间会浮动几 KiB，直接进哈希会让同一台硬件换 identity。
- `{agent}`（第 4 段）：**稳定**的agent标识 `agent_id`，其值就是MQTT中的 client_id，来自 `etc/.run/agent-id` | 形如`agent-b851fdde`。
- `resource`：按语义命名（见 §2.3），可能含 `/`（如 `stream/start/response`）。

特例：设备发现是**租户级**广播，没有 `device` 段（第 3 段是固定的 `discovery`）：`walle/{tenant}/discovery/query/request`。

### 主题总表

命名规则：
1. `.../online` 单独，因为比较特殊；
2. 所有的请求-应答对，请求主题都以 `.../request` 结尾，应答主题则以 `.../response` 结尾；
3. 所有只有下发，没有应答的主题，都以 `.../cmd` 结尾；
4. 所有的主动上报主题，都以 `.../report` 结尾；

基础功能：
| # | 协议 | App → 设备（请求） | 设备 → App（应答/上报） | 类型 | 版本 | 备注 |
|---|---|---|---|---|---|---|
| 1 | 上线/下线 | — | `walle/{t}/device/{d}/online` | 主动上报（retained + LWT） | v1.1 | v1.1最小化json报文 |
| 2 | 设备发现 | `walle/{t}/discovery/query/request` | `walle/{t}/device/{d}/discovery/query/response` | 请求-应答（按需、限频） | v1.1 | v1.1最小化json报文 |
| 3 | 基础状态查询 | `walle/{t}/device/{d}/baseState/query/request` | `walle/{t}/device/{d}/baseState/query/response` | 请求-应答 | v1.4 | v1.4精简json报文；v1.7增补电池/声呐，移除alarms；v1.10精简json报文 |
| 4 | 运行状态查询 | `walle/{t}/device/{d}/runState/query/request` | `walle/{t}/device/{d}/runState/query/response` | 请求-应答 | v1.10 | v1.10将capability改为运行状态查询并精简json报文 |
| 5 | 告警上报 | — | `walle/{t}/device/{d}/alarm/report` | 主动上报 | v1.0 | v1.6 增补电池低压/前方障碍告警 |
| 6 | 远程命令执行 | `walle/{t}/device/{d}/cmd/exec/request` | `walle/{t}/device/{d}/cmd/exec/response` | 请求-应答 | v1.1 | v1.1已删除 |
| 7 | 客户端主动上报（上线/下线） | - | `walle/{t}/client/{client_id}/online` | 主动上报（retained + LWT） | v1.13 |  |

控制权（独占使用）：
| # | 协议 | App → 设备（请求） | 设备 → App（应答/上报） | 类型 | 版本 | 备注 |
|---|---|---|---|---|---|---|
| 1 | 获取控制权 | `walle/{t}/device/{d}/control/acquire/request` | `walle/{t}/device/{d}/control/acquire/response` | 请求-应答 | v1.15 | 无 holder 时 fail-open；同一 client 重复 acquire 会续租 |
| 2 | 释放控制权 | `walle/{t}/device/{d}/control/release/request` | `walle/{t}/device/{d}/control/release/response` | 请求-应答 | v1.15 | 只有当前 holder 能释放 |

agent功能：
| # | 协议 | Web → agent（请求） | agent → Web（应答/上报） | 类型 | 版本 | 备注 |
|---|---|---|---|---|---|---|
| 1 | 自然对话 | `walle/{t}/agent/{a}/chat/request` | `walle/{t}/agent/{a}/chat/response` | 请求-应答（QoS1） | v1.16 |  |
| 2 | agent 在线 | — | `walle/{t}/agent/{a}/online` | 主动上报（retained + LWT） | v1.16 |  |
| 3 | 基本状态查询 | `walle/{t}/agent/{a}/baseState/query/request` | `walle/{t}/agent/{a}/baseState/query/response` | 请求-应答 | v1.20 | 返回 agent 与所在机器的运行状态 |
| 4 | 配置修改 | `walle/{t}/agent/{a}/config/modify/request` | `walle/{t}/agent/{a}/config/modify/response` | 请求-应答 | v1.21 | 当前仅支持修改 `agent_name`（§4.23） |
| 5 | 会话停止 | `walle/{t}/agent/{a}/chat/stop/request` | `walle/{t}/agent/{a}/chat/stop/response` | 请求-应答 | v1.22 | 优雅停止当前会话并释放控制权（§4.24） |
| 6 | 会话进度上报 | - | `walle/{t}/agent/{a}/chat/progress/report` | 主动上报 | v1.23 | 长会话关键进度节点（§4.25） |

`{a}` = agent clientid。

视频语音功能：
| # | 协议 | App → 设备（请求） | 设备 → App（应答/上报） | 类型 | 版本 | 备注 |
|---|---|---|---|---|---|---|
| 1 | 开始推流 | `walle/{t}/device/{d}/stream/start/request` | `walle/{t}/device/{d}/stream/start/response` | 请求-应答 | v1.11 | v1.11 请求去掉 video_device/room_number；v1.24 主题 video→stream，字段 video_device→camera_device |
| 2 | 停止推流 | `walle/{t}/device/{d}/stream/stop/request` | `walle/{t}/device/{d}/stream/stop/response` | 请求-应答 | v1.0 | v1.24 主题 video→stream，字段 video_device→camera_device |
| 3 | 手机音频上行·开 | `walle/{t}/device/{d}/phone/audio/send/request` | `walle/{t}/device/{d}/phone/audio/send/response` | 单向动作（App 不订阅应答） | v1.0 |  |
| 4 | 手机音频上行·关 | `walle/{t}/device/{d}/phone/audio/stop/request` | `walle/{t}/device/{d}/phone/audio/stop/response` | 单向动作（App 不订阅应答） | v1.0 |  |
| 5 | 开始录像 | `walle/{t}/device/{d}/record/start/request` | `walle/{t}/device/{d}/record/start/response` | 请求-应答 | v1.3 | 本地 fMP4 落盘 |
| 6 | 停止录像 | `walle/{t}/device/{d}/record/stop/request` | `walle/{t}/device/{d}/record/stop/response` | 请求-应答 | v1.3 | 停止当前录像会话 |
| 7 | 录像列表 | `walle/{t}/device/{d}/record/list/query/request` | `walle/{t}/device/{d}/record/list/query/response` | 请求-应答 | v1.3 | 返回历史录像与播放地址 |
| 8 | 删除录像 | `walle/{t}/device/{d}/record/delete/request` | `walle/{t}/device/{d}/record/delete/response` | 请求-应答 | v1.3 | 删除指定录像文件 |
| 9 | 播放录像 | `walle/{t}/device/{d}/record/play/start/request` | `walle/{t}/device/{d}/record/play/start/response` | 请求-应答 | v1.5 | ffmpeg 推 fmp4 → SRS，App 拉流 |
| 10 | 停止播放 | `walle/{t}/device/{d}/record/play/stop/request` | `walle/{t}/device/{d}/record/play/stop/response` | 请求-应答 | v1.5 | 停止录像点播推流 |

外部设备功能：
| # | 协议 | App → 设备（请求） | 设备 → App（应答/上报） | 类型 | 版本 | 备注 |
|---|---|---|---|---|---|---|
| 1 | 电机持续控制 | `walle/{t}/device/{d}/car/motor/set/request` | `walle/{t}/device/{d}/car/motor/set/response` | 请求-应答（最新状态覆盖） | v1.2 | 500ms 无有效命令自动停车；v1.7 前方障碍禁前进；v1.17 改为 request/response 并恢复应答 |
| 2 | 舵机状态查询 | `walle/{t}/device/{d}/car/servo/status/query/request` | `walle/{t}/device/{d}/car/servo/status/query/response` | 请求-应答 | v1.2 | 默认只读位置，不读 offset |
| 3 | 舵机位置设置 | `walle/{t}/device/{d}/car/servo/set/request` | `walle/{t}/device/{d}/car/servo/set/response` | 请求-应答（最新状态覆盖） | v1.2 | 绝对位置；单舵机越界只跳过该舵机；v1.18 改为 request/response 并恢复应答 |


> `{t}` = tenant，`{d}` = device_id。
> 手机音频上行·开/关 只承载「开/关」控制，音频本身走独立 RTMP（§4.8）。
> 录像 start/stop/list/delete 由 `record_video_node` 订阅 `/image_raw` 编码为 fMP4 落盘。
> 录像点播（record/play/start|stop）：设备端 ffmpeg 用 `-c copy` 把 fMP4 转封装为 FLV 推到 SRS，App 通过返回的 `flv_url` / `whep_url` 拉流；支持 `offset_sec` 从指定时间点开始。

---

## 2. 设计原则（为什么这么设计）

修改协议前务必理解这些约束——它们都是踩过坑后定下来的。

### 2.1 主题即权限边界

把租户编码进主题路径，Broker 就能直接用 `walle/${username}/#` 做 ACL：
用户 A 的 App 和设备只能看到 `walle/A/#`，天然隔离多用户，无需服务端代码。
**因此主题段里绝不允许出现 `+ # /` 等通配/分隔符**（见 §3.3 清洗规则），否则 ACL 会失效。

### 2.2 设备ID必须稳定，禁止用 uuid（v1.12 已删除 uuid）

> **v1.12 起 uuid 已彻底删除**：设备端不再生成 `uuid5(MAC|IP|启动时间)`，报文不再携带 `uuid`，
> 请求也不再校验 uuid。设备身份只用落盘复用的 `device_id`（主题第 4 段）。
> 下面保留历史说明，解释为什么当初放弃 uuid。

历史上的 `uuid` 由设备端按 `uuid5(MAC|IP|启动时间)` 生成，**每次重启都会变**，
导致设备列表、状态缓存和请求校验反复出错，因此改用 `device_id` 并最终在 v1.12 删除 uuid。

**`device_id` 是设备的唯一标识，只要不更换硬件，就不会变**。
`md5(MAC | CPU 核数 | 内存字节数)` 取 16 位，但 `MemTotal` 在两次启动之间会浮动几 KiB
（内核保留页、启动参数、内存训练），于是同一台机器算出了两个 `device_id` —— 表现为
App 里同一台设备出现两张卡片、旧主题上的 retained `online:false` 一直残留。
现在的规则：

- 首次启动按 `md5(MAC | CPU 物理核数 | 内存 **GiB**)` 生成，写入 `<安装目录>/.device-id`
  （可用 `DEVICE_ID_FILE` 覆盖）；
- 之后**只读文件复用**，硬件指纹再怎么漂移都不影响；

> 代价：`device_id` 一旦变化（换网卡），旧主题上的 retained `online` 无法由设备自己清理
> ——Broker ACL 只允许它写 `walle/{tenant}/device/${clientid}/#`。这类残留由 App 侧按 MAC 归并消化。

### 2.3 资源按语义命名

| 后缀 | 语义 | 例子 |
|---|---|---|
| `…/request` | 请求（查询或动作，请求-应答对的发起方） | `baseState/query/request`、`stream/start/request` |
| `…/response` | 应答，**只在收到对应请求后发布**，绝不主动上报 | `baseState/query/response`、`stream/start/response` |
| `…/cmd` | 持续设置命令（有副作用；最新一条覆盖旧状态，不排队，**无应答**） | 当前无现网主题，保留后缀语义 |
| `…/report` | 主动上报 | `alarm/report` |
| 无后缀 | 仅 `online`（上线/下线） | `online` |

这套约定由设备端 `net_mqtt_bridge/test/test_protocol.py` 与 `linux-demo/test/test_protocol.py` 强制检查：
每个 `REQ_*`（`.../request`）必须在 `REQUEST_TO_RESULT` 里登记对应的 `RES_*`（`.../response`）；
`.../cmd` 主题不登记应答；不允许存在「没人请求的 /response 主题」。

### 2.4 请求-应答严格配对：request_id

‘request_id‘用于标识当前的请求，每次请求的`request_id`都不同。

- `request_id`：客户端生成的请求标识，每次请求都不同。
  - App：8 位十六进制随机串（`UUID.randomUUID()` 前 8 位）。
  - Web：以 `web-` 为前缀；对话用 `web-` + 8 位十六进制，急停/中止用 `web-estop-` / `web-abort-` 前缀。
  - agent 不兜底生成；`chat/request` 缺失 `request_id` 时按 §4.20 返回 `invalid`。
  - 消费方按字符串原样匹配，不要假设固定长度或纯十六进制。
  设备端/agent 在应答里**原样回显**，App/Web 用它把结果和「哪一次请求」对上；迟到的旧结果直接丢弃。


### 2.5 QoS1 重复投递：一次性事件去重，幂等事件不去重

QoS 1 允许 broker 重复投递（重连补发、未及时 PUBACK）。App 侧 `MessageRouter` 用
「键排序后的 JSON 签名 + 2 秒窗口」去重，但只针对**一次性事件**：
`alarm/report`、`runState/query/response`、`stream/start/response`、`stream/stop/response`、`car/servo/status/query/response`
（重复处理会出事，例如推流结果重复 → 播放器初始化两次）。
`online` 与 `baseState/query/response` 是幂等的（同一份状态重复应用无害），**不去重**。

### 2.6 retained 只给 online，发现靠回放而非轮询（N7）

设备列表 = 当前在线设备，直接来自 retained `online` 报文：App 一连上就收到回放，
**不需要广播轮询**。`discovery/query/request` 降级为按需手段（手动刷新、连上后一台设备都没
回放出来时自动兜底一次）。设备端对 discovery 应答做了**限频（2 秒/台）+ 随机抖动
（≤0.8 秒）**，避免多台手机同时扫描时回包风暴。

### 2.7 不订 `#` 整棵子树

MQTT 3.1.1 没有 no-local 语义，订 `walle/{t}/#` 会把自己发的请求再收一遍，浪费带宽、
干扰排查。所以各端都不订 `#` 整棵子树：

- App 订 12 条精确响应主题：`walle/{t}/device/+` 下的 `online`、`alarm/report`、10 条 `…/response`（见 `TopicNames.responseSubscriptions`）
- 设备端订 6 条：3 条按层数的 `+` 模式（`+/+/request`、`+/+/+/request`、`+/+/+/+/request`），
  另加租户级 `discovery/query/request`、`client/+/online`、`agent/+/online`（见 `config.subscriptions()`）。
  `+` 只匹配一层，因此只命中请求主题与客户端 presence，不会收到自己发布的 response/report；
  新增不同层数的请求主题时需补一条模式。

### 2.8 统一错误码（result）

所有带 `result` 字段的 `.../response` 使用同一套错误码：`0` 表示成功，负数表示失败。
**不同命令的同类错误必须使用相同码**；客户端只判断 `result != 0`，具体原因看 `cause`。

| result | 含义 | 典型场景 |
|---|---|---|
| `0` | 成功 | 所有请求-应答 |
| `-1` | 请求无效 | 缺少必填字段、空列表、未知 `action` |
| `-2` | 参数错误 | 类型错误、取值越界、互斥冲突、非法路径/文件名 |
| `-3` | 资源不可用 | 服务/节点/设备/链路不可用、文件不存在、未配置必要参数或依赖 |
| `-4` | 状态冲突 | 已在录制/未录制、没有可停止的流、正在使用、房间号与当前状态不一致 |
| `-5` | 控制权被占用 | 非当前 holder；控制类请求被拒绝（带 `error_code=control_denied`） |
| `-6` | 执行失败 | 内部异常、超时、进程启动/退出失败、查询/删除失败 |
| `-7` | 存储空间不足 | 录像磁盘低于最低阈值 |
| `-8` | 安全联锁 | 前方障碍，禁止前进 |

补充约定：

- `online`、`discovery/query/response`、`baseState/query/response`、`runState/query/response`、
  `alarm/report` 不带 `result`，不属于本表；`agent/chat/response` 使用 `ok` 布尔值。
- 失败时应尽量同时给出可读 `cause`；客户端不得依赖具体文案做逻辑判断。
- 新增命令只能复用表中已有的码；确需新语义时先更新本表与各端解析。

---

## 3. 公共约定

### 3.1 公共字段

绝大多数设备 → App 报文都带这些字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `device_id` | string | 稳定设备标识（见 §2.2，落盘复用）。设备端可省略，App 用主题里的设备段兜底注入，区别不同设备，重启不变。 |
| `hostname` | string | 主机名 |
| `ip` | string | 局域网 IP |
| `timestamp` | number | Unix 秒。**在线报文用它判定新旧**：device_id 漂移后旧主题遗留的 retained `online` 若比已知信息更旧，App 直接丢弃（§4.1） |
| `datetime` | string | `YYYY-MM-DD HH:MM:SS` 本地时间 |
| `request_id` | string | **仅应答报文**：原样回显请求的 request_id，标识请求-应答对，每次请求都不同。 |

### 3.2 request_id 配对规则

1. App 每发一个请求生成新 `request_id`（8 位 hex）；Web/agent 的格式见 §2.4，统一放进请求报文。
2. 设备端应答必须原样回显；没收到匹配 `request_id` 的应答前，UI 处于等待态。
   例外：`discovery/query/response` 自 2026-11-11 起与 `online` 完全一致，不再回显 `request_id`
   （App 不依赖它配对，扫描状态按超时结束）。
3. 旧请求迟到应答会被丢弃（App 只认「当前这次」的 rid）。

### 3.3 主题段清洗（sanitize）

各端**必须完全一致**，否则主题对不上。规则（`TopicNames.sanitize` / `main.sanitize_segment`）：

- 只保留 `[A-Za-z0-9_-]`；`.` `:` `@` 空格 → `_`；其余字符（含中文、通配符）**丢弃**；
- 清洗后为空 → `unknown`；超过 64 字符 → 前 40 位 + 短哈希（保证同输入得同输出）。

对照测试用例（各端同步维护）：

| 输入 | 输出 |
|---|---|
| `home` | `home` |
| `user@example.com` | `user_example_com` |
| `RPi 5` | `RPi_5` |
| `x/y+z#` | `xyz` |
| `中文租户` / 空 / null | `unknown` |

### 3.4 uuid 校验（v1.12 已删除）

v1.12 起请求不再携带 `uuid`，设备端也不再调用 `verify_uuid`。请求能否到达只由主题决定：
`walle/{tenant}/device/{device_id}/...`，Broker ACL 已保证非本设备的请求不会下发。

历史行为（已废弃）：除 `discovery/query/request` 外，所有请求必须带与设备当前值一致的
`uuid`，否则设备端静默忽略（只记日志、不回复）。该机制因 uuid 每次重启变化、
客户端缓存旧值会导致“设备在线但请求无响应”，已在 v1.12 完全移除。

### 3.5 App/Web/agent → 设备公共字段（V1.14）

所有 App/Web/agent 发往设备的 `.../request` 与 `.../cmd` 报文，统一携带：

| 字段 | 类型 | 说明 |
|---|---|---|
| `client_id` | string | App/Web/agent 的 MQTT client_id（持久化，经 `sanitize`）。与客户端 presence 主题 `client/{client_id}/online` 中的值一致。 |
| `client_name` | string | 客户端展示名，如 `Web`、`Android Pixel 7`；与 presence 报文中的 `client_name` 一致。 |

- 包括 `discovery/query/request`、查询请求、控制请求和 `.../cmd`；
- agent 作为独立控制端时，`client_id` 使用 `agent_id`，`client_name` 使用 `agent_name`（见 §4.21）；
- 设备端当前不校验这两个字段的真实性，仅用于日志、审计与控制权租约（§4.19）；
- 旧设备会忽略未知字段，旧客户端不携带时新设备也能正常处理。

---

## 4. 各协议详解

### 4.1 online —— 上线/下线（主动上报）（V1.1）

**作用**：App 设备列表的唯一数据源。上线 retained 报文只含基本身份，订阅即回放。

- 主题：`walle/{t}/device/{d}/online`，**retained**
- 上线：设备连上 broker 后立即发布 `online: true` + 基本身份（见下表）
- 下线：broker 通过 **LWT 遗嘱**发布 `online: false`（设备端注册 will，异常掉线也能通知）

字段（最小集；完整身份见 §4.2 `discovery/query/response`）：

| 字段 | 说明 |
|---|---|
| `online` | bool，true=上线 / false=下线 |
| `device_id` / `hostname` | 归并与展示用标识 |
| `version_code` / `version_name` | 设备端软件版本：版本号（整数，如 `1`）、版本名（如 `0.1.1.1`） |
| `os_description` | 操作系统描述，取 os-release 的 `PRETTY_NAME`（如 `Ubuntu 24.04.5 LTS`）；无 PRETTY_NAME 时回退 `NAME` + `VERSION`，文件缺失时回退内核信息 |
| `device_type` / `model` | 设备类型、型号（如 Raspberry Pi 5） |
| `ip` | 设备当前 IP，展示与排障用 |
| `uptime_seconds` | 运行时长（秒） |
| `timestamp` / `datetime` | 报文时间戳 |

> 2026-11-10 起，retained `online` **不再包含** `mac`、`serial`、`os`、`cpu`、内存/磁盘
> 总量、`boot_time`、`uuid_source` 等身份细节；这些字段不再通过 MQTT 下发，
> 设备详情请使用 `baseState/query/request`（§4.3）。

**注意**：`online: false` 的遗嘱只由 broker 代发；设备端正常关机也应主动发一次。
App 侧重复 online 不去重（幂等）。

**归并顺序（`DeviceRegistry.applyOnline`）**：`device_id` → **MAC（若存在）** → `hostname`
（v1.12 起不再使用 uuid）。
MAC 归并专治 `device_id` 漂移：同一块网卡的两条 online（旧 id 的
`online:false` + 新 id 的 `online:true`）会合成一张卡片；若旧报文按 MAC 命中已有设备、
却是下线且 `timestamp` 更旧，则整条丢弃，避免把在线设备刷成离线。
新版设备端 online 不含 MAC，App 需容忍该字段缺失，退化为按 `device_id`/`hostname`
归并；真实的同一启动周期遗嘱（`device_id` 相同、时间戳相同）不受影响。

### 4.2 设备发现 discovery/query/request → discovery/query/response（V1.1）

**作用**：设备列表页的按需刷新。应答内容与 §4.1 的 `online` **完全一致**（同一份
`build_online_payload`），只含最小身份字段，`online` 固定为 `true`，不再返回完整身份。
设备详情请使用 §4.3 `baseState/query/request`。

- 请求：`walle/{t}/discovery/query/request`（租户级，没有 device 段）
- 应答：`walle/{t}/device/{d}/discovery/query/response`

请求字段：

| 字段 | 必填 | 说明 |
|---|---|---|
| `request_id` | 否 | App 会携带，仅用于日志排查；应答不回显 |


设备端保护机制（防止广播风暴）：

| 机制 | 参数 | 说明 |
|---|---|---|
| 限频 | `DISCOVERY_MIN_INTERVAL_SEC = 2.0` | 每台设备 2 秒内只应答一次，重复查询丢弃 |
| 抖动 | `DISCOVERY_JITTER_SEC = 0.8` | 应答前随机延迟 0~0.8s，N 台设备错开回包 |

> 2026-11-11 起 discovery 应答与 online 统一为最小身份（此前返回 `mac`/`serial`/
> `os`/`cpu`/磁盘等完整身份并回显 `request_id`）。后续 App 改为
> `UNSUBSCRIBE/SUBSCRIBE online` 触发 retained 回放后，可删除 discovery 主题。

> 坑位提醒：设备级主题的第 3 段固定为 `device`，发现主题没有该段（第 3 段是
> `discovery`）。解析时必须先按完整主题特判 discovery，再校验 `device` 段——两端
> （`TopicNames.parse`、`main._handle_request`）都这么处理，别改没。

### 4.3 基础状态查询 baseState/query/request → baseState/query/response（V1.4，V1.7 增补 battery / sonar，移除 alarms，V1.10 精简为六组）

**作用**：拉取设备基础运行状态，驱动状态页 CPU/内存/磁盘/网络/USB 卡片。CPU/网络数据复用
监控循环的最近一次采样（`MONITOR_INTERVAL`，默认 5s），USB 列表复用 `check_usb` 的维护结果，
**查询路径不再阻塞采样**；内存/磁盘在应答时读取。

**V1.4 变更（报文精简，只保留原始数值）**：`cpu` 去掉 `per_core_percent[]` 与 `frequency_mhz`；
`memory` 去掉 `swap` 与所有 `*_human`；`disk` 由逐挂载点列表改为聚合总量且不带 `*_human`；
`network` 去掉 `interfaces[]`、`total_rx/tx_rate`；`usb` 只统计用户视角的 USB 设备
（过滤 root hub 与 Hub）。**App 端需同步调整 `StatusSnapshot` 解析。**

**V1.7 变更**：移除 `alarms` 分组（原 `active` / `thresholds`）。当前告警状态以
`alarm/report` 主题的主动上报为准（App 告警中心自行维护）。

**V1.10 变更**：`baseState/query/response` 只保留 `system`、`cpu`、`memory`、`disk`、`network`、
`usb` 六个分组；移除 `recording`、`video`、`battery`、`sonar`。录像状态由 `record/*` 主题维护，
推流状态以 `stream/start|stop/response` 为准，电池/声呐仍通过 `alarm/report` 上报。

- 请求：`walle/{t}/device/{d}/baseState/query/request`
- 应答：`walle/{t}/device/{d}/baseState/query/response`

请求字段：

| 字段 | 必填 | 说明 |
|---|---|---|
| `request_id` | 是 | 配对用 |

应答字段（嵌套结构）：

| 分组 | 字段 |
|---|---|
| 顶层 | `device_id`、`hostname`、`version_code`、`version_name`、`ip`、`timestamp`、`datetime`、`uptime_seconds`、`sample_age_sec`、`request_id` |
| `system` | `os`、`kernel`、`os_version`、`python`、`machine`、`model` |
| `cpu` | `percent`（总）、`count_logical/physical`、`load_avg_1m/5m/15m`、`temperature_c`、`sample_interval_sec` |
| `memory` | `percent`、`total_bytes`、`used_bytes`、`available_bytes`、`free_bytes`（均为字节，不提供格式化字符串） |
| `disk` | 全部真实磁盘的**聚合总量**：`total_bytes`、`used_bytes`、`free_bytes`、`percent`。统计口径：排除 zram（内存压缩盘）；同一设备只算一次（log2ram 会把根分区再挂到 /var/log.hdd，不去重总量翻倍），同设备优先取 `/` 挂载点 |
| `network` | `sample_interval_sec`、`total_rx_bytes_per_sec`、`total_tx_bytes_per_sec` |
| `usb` | `count`、`devices[]`。只统计用户视角的 USB 设备：跳过 root hub（usbN 节点）与 Hub 类设备（`bDeviceClass=09`，含板载/外接 Hub）；Hub 下挂的设备仍单独出现；板载蓝牙等功能芯片保留。插拔 Hub 不触发告警 |

**采样时效**：`cpu` / `network` 的 `sample_interval_sec` 是监控循环的实际采样周期；
`sample_age_sec` 表示这份 CPU/网络数据距采样时刻的秒数（0 ~ 采样周期）。设备刚启动、
监控尚未产出第一份快照时，查询会现场补采一次（约 0.2s）兜底。

解析入口（App 唯一）：`model/StatusSnapshot.java`。契约测试：`StatusSnapshotTest`
（缺分组归空、异常数值归一为 null 等）。**状态是实时查询数据，不落盘**
（N11 曾落盘又 revert，决策见 git 历史）。

### 4.4 运行状态查询 runState/query/request → runState/query/response（V1.10，V1.25 改 stream/is_supported）

**作用**：查询设备实时运行状态：是否推视频流、是否拉取手机音频、是否录像、电机/舵机
是否在运动、当前电压与声呐距离。

**V1.10 变更**：`capability/query/*` 改名为 `runState/query/*`，应答为破坏性精简：
`capability`、`capabilities[]`、`camera_count`、`cameras[]`、`audio`、`summary`、`probe`、
`cache` 全部删除，不再枚举摄像头/麦克风/音响能力，也不再做后台探测与能力缓存。
Android/Web 暂不同步适配（仍按旧能力字段解析，摄像头/音频入口会显示为空）。

**V1.25 变更（破坏性，不保留兼容）**：

- 推流分组 `video` 改名为 `stream`，与 `stream/start|stop` 主题命名一致；
- 所有功能分组新增 `is_supported`（设备是否支持该功能）；`is_supported=false` 时该分组
  其余字段一律为 `null` / `false`，客户端显示“不支持/隐藏”；
- `recording` 移除 `enable`，改由 `is_supported` 表示录像配置是否启用
  （取 `walle_camera.conf` 的 `record.enabled`）；
- `stream` / `recording` 的摄像头字段统一为 `camera_device`；
  `audio_input` 为录音设备名，配置为默认设备时输出字面量 `"default"`；
- `phone_audio` 仅保留 `is_supported` / `playing` / `room_number`；
- `battery` / `sonar` 移除 `age_sec`：超过 1 秒无新采样时 `available=false`，
  且测量值置 `null`（不再返回最后一次旧值）。

- 请求：`walle/{t}/device/{d}/runState/query/request`，字段 `{request_id}`
- 应答：`walle/{t}/device/{d}/runState/query/response`

应答字段（只有以下内容）：

| 字段 | 说明 |
|---|---|
| `device_id` / `hostname` / `ip` / `timestamp` / `datetime` | 公共字段（§3.1） |
| `request_id` | 原样回显请求的 request_id |
| `stream` | 推流状态：`is_supported`；`streaming`（是否在推视频流）；`camera_device`（摄像头设备节点，未推流时为已配置设备）；`audio_input`（录音设备名，默认 `default`）；推流中 `room_number` 为视频流房间号，否则 null；另含 `started_at`、`uptime_sec` |
| `phone_audio` | 手机音频上行：`is_supported`、`playing`（是否在拉取手机音频流）、`room_number`（播放中为音频流房间号，否则 null） |
| `recording` | 录像状态：`is_supported`（= `walle_camera.conf` 的 `record.enabled`）、`recording`（是否正在录像）、`filename`（当前录像文件名，未录像为 null）、`camera_device`、`audio_input` |
| `motor` | 电机：`is_supported`、`moving`（是否有运动状态，含 500ms 死区内） |
| `servo` | 舵机：`is_supported`、`moving`（是否处于 `duration` 动作窗口内） |
| `battery` | 电池：`is_supported`、`voltage_v`（V，两位小数）、`available`（1 秒内是否有新采样）。无数据时 `voltage_v=null`、`available=false` |
| `sonar` | 声呐：`is_supported`、`distance_cm`（cm，一位小数）、`available`（1 秒内是否有新采样）。无数据时 `distance_cm=null`、`available=false` |
| `control_holder` | 当前控制权持有者（V1.15）；无 holder 时为 `null`。结构见 §4.19 |

通用规则：`is_supported=false` 时该分组其余字段一律为 `null` / `false`；
`available=false` 时对应测量值为 `null`，客户端显示“无效”，不得沿用历史值。

**采集方式（设备端）**：查询到达时现场采集，不跑 `v4l2-ctl` / `pactl`，无后台缓存：
推流/手机音频复用进程状态，录像复用录像节点状态，电机/舵机由车控节点维护，
电池/声呐复用传感器最新采样。

### 4.5 开始推流 stream/start/request → stream/start/response（V1.11，V1.24 改为 stream）

**作用**：让设备端用默认摄像头推流到流媒体服务（RTMP），客户端从应答中拿到
`room_number` 后拼 WHEP 地址拉流播放（`BuildConfig.WHEP_URL_TEMPLATE`）。

- 请求：`walle/{t}/device/{d}/stream/start/request`
- 应答：`walle/{t}/device/{d}/stream/start/response`

请求字段：

| 字段 | 必填 | 校验 |
|---|---|---|
| `request_id` | 是 | 配对用 |

**V1.11 变更（重要）**：请求不再包含摄像头设备字段 / `room_number`。

- 摄像头由设备端固定使用默认设备（ROS2 `video_webrtc_bridge` 的 `camera_device`；
  linux-demo 的 `CAMERA_DEVICE`，未配置时取探测到的第一个 USB 采集节点）；
- 房间号由设备端自动生成 **8 位随机字符**（小写字母 + 数字），客户端不得自行指定，
  也不能缓存旧房间号发起推流。
- v1.24 起，应答与运行状态中的摄像头字段由 `video_device` 更名为 `camera_device`。

处理语义：

- 当前**没有推流**：用默认摄像头开始推流，并在应答里返回新生成的 `room_number`；
  设备没有可用摄像头时返回 `result=-3`；
- 当前**已在推流**：直接返回 `result=0`、`action=already_streaming` 和当前
  `room_number`，不重连、不重启、不切换摄像头。

应答字段：

| 字段 | 说明 |
|---|---|
| `result` | `0` 成功（新启动或已在推流）/ `-3` 资源不可用（没有摄像头、能力关闭、未配置 secret、缺少编码依赖）/ `-6` 执行失败（等待图像超时、编码进程启动失败或异常退出） |
| `cause` | 可读原因：`ok` / `此设备没有摄像头` / `资源不可用` / `执行失败` |
| `camera_device` | 实际使用的默认摄像头（没有摄像头时可能为 null） |
| `room_number` | 设备生成的 8 位房间号（成功时必有，客户端必须用它拼 WHEP 地址） |
| `request_id` | 原样回显 |
| `action` | `started` / `already_streaming` / `failed` |
| `rtmp_url` | **已脱敏**（secret 打码），仅用于日志展示 |
| `pid`、`started_at`、`startup_grace_sec` | 进程信息（`already_streaming` 时是当前流的） |
| `error` | `failed` 时的具体错误 |

### 4.6 停止推流 stream/stop/request → stream/stop/response（V1.0，V1.24 改为 stream）

**作用**：停掉设备当前推流。

- 请求：`walle/{t}/device/{d}/stream/stop/request`
- 应答：`walle/{t}/device/{d}/stream/stop/response`

请求字段：`{request_id}` + 可选 `room_number`。

应答字段：

| 字段 | 说明 |
|---|---|
| `result` | `0` 已停止 / `-3` 推流服务不可用 / `-4` 当前没有设备在推流 / `-6` 停止失败 |
| `cause` | 可读原因 |
| `stopped` | bool，是否真的停了一路流 |
| `room_number` / `camera_device` / `pid` / `started_at` | 被停流的信息（成功时） |
| `request_id` 及公共字段 | — |

**一致性校验**：请求带了 `room_number` 且与当前推流不一致 → `result=-4` 并在 `error`
里说明当前房间号，**不会误停别人的流**。

### 4.7 alarm/report —— 告警（主动上报）（V1.0，V1.6 增补 battery_low / obstacle_near）

**作用**：阈值越限/恢复、USB 插拔、推流/手机音频中断、电池低压与前方障碍的实时通知，
驱动 App 告警中心（App 侧 SQLite，200 条滚动淘汰）。**设备端只发布、不落盘**：告警历史
由服务端数据库持久化，设备端不建本地告警库（避免双份数据不一致与 SD 卡磨损）。

- 主题：`walle/{t}/device/{d}/alarm/report`（非 retained，QoS1，App 侧 2 秒签名去重）

字段：

| 字段 | 取值 | 说明 |
|---|---|---|
| `event` | `alarm` / `recover` / `usb_connect` / `usb_disconnect` | 事件类型 |
| `level` | `critical` / `warning` / `info` | 级别（阈值告警=warning，恢复=info，USB 拔出=warning，USB 接入=info，电池极低=critical） |
| `metric` | `cpu` / `memory` / `disk` / `network` / `temperature` / `usb` / `video` / `audio` / `battery` / `obstacle` | 指标 |
| `alarm_type` | 稳定代码如 `cpu_high` / `video_lost` / `phone_audio_lost` / `battery_low` / `obstacle_near` | 分类用 |
| `message` | 中文可读描述 | 直接可展示 |
| `detail` | 阈值类：`{value, unit, threshold}`；USB：`{device{...}, current_count}`；视频中断：`{room_number, exit_code, log_file, tail}`；手机音频中断：`{room_number, exit_code, reason, log_file, tail}`；电池低压：`{value, unit, threshold}`；障碍：`{distance_cm, threshold_cm, sensor, direction}` | 结构化细节 |
| `device_id`、`hostname`、`ip`、`timestamp`、`datetime` | 公共字段 | |

**高阈值回滞防抖（cpu/memory/disk/network/temperature）**：越限（≥阈值）告警后，要降到 **阈值 × 0.9** 以下才发 `recover`，防止边界
抖动反复报警。阈值均可用环境变量覆盖（`THRESH_CPU`/`THRESH_MEM`/`THRESH_DISK`/
`THRESH_TEMP`/`THRESH_NET_MBPS`），监控周期 `MONITOR_INTERVAL`（默认 5s）。
默认 CPU 温度阈值 70℃；网络只监控**出站**带宽（`alarm_type=network_tx_high`，
`THRESH_NET_MBPS` 默认 3 Mbps，约 0.375 MB/s），入站不产生告警。
告警 `message` 与 `detail.rate_human`/`threshold_human` 均以 Mbps 展示。

**媒体中断告警**：`video_lost`（`metric=video`）在 ffmpeg 推流进程非主动退出时上报；
`phone_audio_lost`（`metric=audio`）在 WHEP 播放器非主动退出时上报。两者 `event=alarm`、
`level=warning`，`detail` 带 `room_number`、`exit_code`、`log_file` 与 `tail`（日志尾部，
≤500 字符）用于定位；重新启动成功后发同 `alarm_type` 的 `event=recover`，主动 stop 不发告警。
达到最长时长自动停止时改发 `video_timeout` / `phone_audio_timeout`（`event=alarm`、`level=info`，
`detail` 带 `room_number`、`max_duration_sec`、`started_at`），不视为故障。

**电池低压与前方障碍告警（V1.6）**

复用同一个 `alarm/report` 主题，不新增主题。两者都是「低阈值」语义，与上面的高阈值告警**方向相反**：

- `battery_low`（`metric=battery`）：数据源为 ROS2 `ros_robot_controller` 节点的
  `/ros_robot_controller/battery`（`std_msgs/UInt16`，单位 mV，发布前除以 1000 换算成 V），
  `detail={value, unit, threshold}`；电压极低（≤ `THRESH_BATTERY_CRITICAL_V`，默认 6.6V，
  约 3.3V/节）时升为 `level=critical`。
- `obstacle_near`（`metric=obstacle`）：数据源为声呐 `sonar_controller/get_distance`
  （`std_msgs/Int32`，单位 mm，发布前换算成 cm），
  `detail={distance_cm, threshold_cm, sensor, direction}`（`sensor="sonar"`、`direction="front"`）。

| 告警 | 触发 `event=alarm` | 恢复 `event=recover` | 环境变量 |
|---|---|---|---|
| `battery_low` | 电压 ≤ `THRESH_BATTERY_V`（默认 7.0V，3.5V/节） | 电压 ≥ `RECOVER_BATTERY_V`（默认 7.4V，3.7V/节） | `THRESH_BATTERY_V` / `RECOVER_BATTERY_V` / `THRESH_BATTERY_CRITICAL_V` |
| `obstacle_near` | 距离 ≤ `THRESH_OBSTACLE_CM`（默认 30cm） | 距离 ≥ `RECOVER_OBSTACLE_CM`（默认 50cm） | `THRESH_OBSTACLE_CM` / `RECOVER_OBSTACLE_CM` |

**采样与防抖**：

- 电池：取最近 3 个采样（`MONITOR_INTERVAL`，默认 5s）的均值判断，连续 2 次越限才发
  `alarm`、连续 2 次高于恢复线才发 `recover`，避免电机启动瞬间的电压跌落误报。
- 障碍：边沿触发，只在跨越阈值时各发一条；同一 `alarm_type` 最小间隔
  `ALARM_MIN_INTERVAL`（默认 3s）。App 的 2 秒签名去重对障碍告警基本无效
  （`distance_cm` 每次变化都会改变签名），**限流必须在设备端完成**。
- 车静止时（500ms 内无有效 `car/motor/set/request`）检出障碍按 `level=info` 上报，避免有人
  从车前经过时刷 warning。

**安全边界（重要）**：

- 障碍急停必须在设备本地闭环完成（声呐/车控节点直接限速或停车），**不得依赖 MQTT
  往返**；`alarm/report` 只负责通知，不负责刹车。
- 告警主题不承载连续遥测：实时电压、实时距离不要按采样周期发布，否则会把 App 告警
  列表（200 条滚动）冲掉。需要实时表盘时走 `baseState/query/response` 或另设低频遥测主题。
- 设备端不持久化告警（不建 SQLite、不写盘），告警历史由服务端数据库保存。

### 4.8 手机麦克风上行 phone/audio/send|stop/request（控制面）＋ WebRTC WHIP/WHEP（媒体面）（V1.0）

**作用**：把手机麦克风的声音实时送到设备扬声器（「反向音频」）。**媒体不走 MQTT**，
走独立 WebRTC：App 用 WHIP 推 `https://www.baidong.xyz:443/rtc/v1/whip/?app=live&stream={room}_audio&secret=…`，
设备端用 WHEP 拉同一路流（`whep_audio_player.py`，aiortc + PortAudio）并播放到本机扬声器。
MQTT 只负责「开 / 关」控制。

> 为什么不用 RTMP：RTMP 走 TCP，没有丢帧/追帧机制，服务端起播缓存 + ffmpeg/声卡缓冲会累积成
> 秒级延迟（实测手机说话到出声约 10s）。WebRTC 走 UDP，晚到的包直接丢弃，实测端到端约 200ms。
> 另：服务端 WHIP 端点**必须带 `secret`**，不带会直接 502。这个 secret 由 Oryx 部署时生成、
> 每台服务器不同，因此**不写死在代码里**：App 构建期注入（`-PoryxSecret` / `gradle.properties`
> 的 `oryxSecret` / 环境变量 `WHIP_SECRET`），设备端读 `/etc/walle-ai.conf` 的 `MEDIA_SERVER_SECRET`。

- 开启请求：`walle/{t}/device/{d}/phone/audio/send/request` → 应答 `.../send/response`
- 关闭请求：`walle/{t}/device/{d}/phone/audio/stop/request` → 应答 `.../stop/response`

请求字段：`{request_id, room_number}`；`stop` 的 `room_number` 可选
（退出 App 兜底时不带，表示「停掉当前任何一路手机音频」）。

应答字段：

| 字段 | 说明 |
|---|---|
| `result` | `0` 成功（`send` 时含 `already_playing`）/ `-2` 参数错误（room_number 非法）/ `-3` 资源不可用（能力关闭、缺依赖、输出设备不可用）/ `-4` 当前没有音频流或房间号不一致（仅 stop）/ `-6` 执行失败（播放器启动失败或异常退出） |
| `cause` | 可读原因 |
| `action` | `send`：`started` / `already_playing` / `failed` |
| `stopped` | `stop`：是否真的停了一路播放 |
| `room_number` / `whep_url` / `pid` / `started_at` / `output` | 成功时的播放进程信息 |
| `error` | 失败时的具体错误（含播放器退出码与日志路径） |
| `request_id` 及公共字段 | — |

**关键约定**：

- **流名 = `{room}_audio`**（设备端按 `MEDIA_SERVER_HOST` 拼 `https://{host}/rtc/v1/whep/?app=live&stream={stream}`，可用
  `PHONE_AUDIO_STREAM_SUFFIX` 覆盖）。与设备自己推的视频流 `live/{room}` 错开，
  避免两端抢同一路 stream。
- **先连上再通知**：App 必须等 WHIP 的 PeerConnection 进入 `CONNECTED`（媒体面已通）才发
  `phone/audio/send/request`，否则设备端 WHEP 拉不到流（首帧 8s 超时会失败）。
- **App 不订阅两个 `/response`**：App 侧是单向动作，按钮状态由本地推流结果驱动
  （`responseSubscriptions()` 不含 `phone/audio/*`）。设备端仍会发布 response，供日志、
  调试与其他客户端使用。
- **一致性校验**：`stop` 带了 `room_number` 且与当前播放房间号不一致 → `result=-4`
  并在 `error` 里说明，**不会误停别人**。
- **旧停新启**：`send` 时若已有别的房间在播，先停旧的再起新的（同一时刻设备只有一路）。
- **退出即关**：`PlayerActivity.onDestroy()` 与进程退出都会发 `phone/audio/stop/request`，
  设备端 `run()` 的 finally 也会 `_stop_phone_audio`，避免扬声器残留出声。
- 设备端开关与输出可配：`PHONE_AUDIO_ENABLE`（默认 True）、`PHONE_AUDIO_DEVICE`
  （默认空 = 系统默认声卡，可填 `sounddevice` 设备名）、`PHONE_AUDIO_LATENCY`（默认 `low`）、
  `PHONE_AUDIO_FIRST_FRAME_TIMEOUT`（默认 8s）。

**媒体面（App 端）**：`io.getstream:stream-webrtc-android` 的 `PeerConnectionFactory`
（与视频会话共享同一个音频设备模块 ADM）→ `AudioSource/AudioTrack`（Opus 48kHz）→
PeerConnection（sendonly）→ ICE 收集完成后 POST 完整 SDP 到 WHIP 端点。推流 URL 模板由
`build.gradle` 根据构建期注入的 `WHIP_SECRET` 拼出 `WHIP_AUDIO_URL_TEMPLATE`，运行时把
`{room}` 替换成当前房间号；未注入 secret 时麦克风按钮会直接提示配置缺失。

**媒体面（设备端）**：`whep_audio_player.py` 用 aiortc 发 WHEP Offer，收到 `s16/48kHz`
帧后写入 `sounddevice.RawOutputStream`（blocksize=20ms，抖动队列最多 6 帧 ≈ 120ms，
溢出丢最旧帧）。依赖：`pip install aiortc sounddevice` + `apt install libportaudio2`。

### 4.9 远程命令执行 cmd/exec/request → cmd/exec/response（V1.1，已废弃）

**作用**：在设备上以 shell（`/bin/bash`）执行任意命令并回传结果。

- 请求：`walle/{t}/device/{d}/cmd/exec/request`
- 应答：`walle/{t}/device/{d}/cmd/exec/response`

请求字段：

| 字段 | 必填 | 说明 |
|---|---|---|
| `cmd` | 是 | 要执行的 shell 命令字符串 |
| `request_id` | 建议 | 缺省时设备端自生成（App 将无法配对，只用于展示） |
| `timeout` | 否 | 秒，1–300，默认 30，越界自动钳制 |

> 本节为已废弃能力的历史说明；v1.12 起请求已不再携带 `uuid`。

应答字段：

| 字段 | 说明 |
|---|---|
| `request_id` | 原样回显 |
| `cmd` | 回显命令原文 |
| `status` | `success`（exit 0）/ `failed`（exit 非 0）/ `timeout`（超时被杀）/ `denied`（白名单拒绝）/ `error`（执行异常或参数非法） |
| `exit_code` | 进程退出码（timeout 时为 null） |
| `stdout` / `stderr` | 输出，超过 64KB 截断（尾部带截断提示） |
| `duration_ms` / `timeout_sec` | 实际耗时 / 生效超时 |
| `request_id` 及公共字段 | —（v1.12 起无 uuid） |

安全开关（设备端环境变量 / 常量）：`ENABLE_CMD_EXEC`（总开关）、`CMD_ALLOWLIST`
（前缀白名单，为空=允许任意命令）。**这是高危能力**，Broker ACL 必须保证租户隔离。

### 4.10 电机持续控制 car/motor/set/request → car/motor/set/response（V1.2，V1.7 增补前方障碍联锁，V1.17 改为请求-应答）

**作用**：App 按住方向键时，以 200ms 周期下发目标运动状态；设备端保存最新状态，
以 10Hz 重发 `/cmd_vel`，500ms 没有收到有效命令就自动停车。

- 请求：`walle/{t}/device/{d}/car/motor/set/request`
- 应答：`walle/{t}/device/{d}/car/motor/set/response`
- 请求不 retained、QoS1；**每条可解析的请求都会收到一条应答**（含校验失败与控制权拒绝；
  空/非法 JSON 无法配对 `request_id`，不回执），应答不 retained、QoS1。应答频率 = 请求频率
  （按住时约 5 条/秒），客户端只应在 `result != 0` 时提示，避免状态条闪烁。
- 设备重启默认停车，运动状态不落盘。

**请求字段**（`request_id` 必填；v1.12 起 uuid 已删除，`request_id` 仅用于日志排查）：

```json
{
  "move":   {"forward": 0.6, "back": 0.0, "left": 0.0, "right": 0.0},
  "rotate": {"left": true, "right": false}
}
```

| 字段 | 类型 | 规则 |
|---|---|---|
| `move.forward/back/left/right` | number | 表示对应方向的线速度；只能有一个非 0；非 0 值必须在 `[0.5, 1.0]` m/s；全 0 = 停止线速度 |
| `rotate.left/right` | boolean | 恰好一个为 `true` 时旋转；都为 `false` = 停止旋转；都为 `true` = 命令错误 |
| `move` / `rotate` | object | **必须同时存在**；整个对象缺失 → 命令无效（设备端丢弃并停车）；对象内子字段缺失按 `0.0` / `false` 处理 |

**校验规则**：

- `move` 同时出现两个及以上非 0 值（含 `forward`+`back`、`left`+`right`）→ 命令错误；
- 非 0 值小于 0.5 或大于 1.0（含负数）→ 命令错误；
- `rotate` 取值不是 JSON 布尔、或 `left`/`right` 同时为 `true` → 命令错误；
- `move` 或 `rotate` 整个对象缺失 → 命令无效；
- 未知字段忽略。

**映射到 `/cmd_vel`**：

```
linear.x  = forward - back
linear.y  = left - right
angular.z =  2.5  (rotate.left = true)
          = -2.5  (rotate.right = true)
          =  0    (both false)
```

**轮速预算**：麦轮逆解的最坏轮速为 `|linear| + k*|angular|`，其中
`k = (wheelbase + track_width) / 2 = 0.1389`。角速度固定 2.5 时，
同时旋转的线速度上限为 `1.0 - 0.1389*2.5 = 0.653 m/s`；超过时按比例缩放线速度
（不改变角速度）。纯平移最大 1.0 m/s。

**前方障碍联锁（V1.7）**：收到 `car/motor/set/request` 后，若声呐存在 1s 内的有效读数且
`distance_cm ≤ THRESH_OBSTACLE_CM`（默认 30cm），则：

- `forward` 非 0 的命令不执行：清空运动状态、立即发布零速，回执 `result=-8`、
  `cause=前方有障碍物，不可前进`；
- 后退、转向、横移（`back` / `rotate` / `left` / `right`）不受影响；
- 声呐无数据或读数过期时不启用联锁，避免传感器故障导致车辆无法移动。

阈值与障碍告警共用 `THRESH_OBSTACLE_CM`，数据源 `/sonar_controller/get_distance`。

**停止**：

- 合法停止命令 = `move` 全 0 + `rotate` 都为 `false` → 清空状态、发布零速；
- 收到无效/错误命令 → 清空状态、立即发布零速；
- 收到前方障碍拦截的命令 → 清空状态、立即发布零速；
- 500ms 死区触发 → 清空状态、立即发布零速；
- 设备退出 → 清空状态、发布零速。

**应答字段**（V1.17 起恢复 MQTT 回执）：

```json
{
  "device_id": "666feb85d43410b2",
  "hostname": "orangepi5plus",
  "ip": "192.168.1.10",
  "request_id": "a1b2c3d4",
  "result": 0,
  "cause": "ok",
  "cmd_vel": {"linear_x": 0.6, "linear_y": 0.0, "angular_z": 0.0},
  "timestamp": 1763000000,
  "datetime": "2026-11-26 12:00:00"
}
```

| 字段 | 说明 |
|---|---|
| `request_id` | 原样回显 |
| `result` | 0 成功并已下发；-1 命令无效（缺 `move`/`rotate`）；-2 命令错误（类型/取值/互斥）；-3 底盘控制链路不可用（`/cmd_vel` 无订阅者）；-5 控制权被其他客户端占用（带 `error_code=control_denied`，见 §4.19）；-8 前方有障碍物，不可前进 |
| `cause` | 可读原因：`ok` / `命令无效` / `命令错误` / `底盘控制链路不可用` / `前方有障碍物，不可前进` / `设备控制权限已被别人占用` |
| `cmd_vel` | 实际下发的速度 `{linear_x, linear_y, angular_z}`（已按轮速预算缩放）；未执行时为 `null` |
| `device_id` / `hostname` / `ip` / `timestamp` / `datetime` | 公共字段（§3.1） |

`result=0` 表示“命令已接受并下发”，不是“车已经执行完成”。`result=-3/-8` 时设备端不会
保存这次状态，避免底盘恢复或障碍消失后自己动起来。

**App 行为**：按住方向键每 200ms 发一条；快速点击去重后仍按 200ms 节流；松开立即发
停止命令（停止不受 200ms 去重限制）。去重只针对完全相同的 payload，方向反转必须立即发。
App 订阅 `car/motor/set/response`，但只在 `result != 0` 时提示（正常运动回执约 5 条/秒，
不刷新状态条）。

**设备端实现**：`net_mqtt_bridge/car_motor.py`（校验/结果/障碍联锁）与 `node.py`
（`/cmd_vel` 发布、10Hz 重发、500ms 死区）；`/cmd_vel` 为 `geometry_msgs/msg/Twist`。
500ms 停车由 bridge 进程保证；若 bridge 本身崩溃，由底盘节点 `mecanum.py` 的
`cmd_vel_timeout`（默认 1.5s）兜底。

### 4.11 舵机状态查询 car/servo/status/query/request → car/servo/status/query/response（V1.2）

**作用**：查询 PWM 舵机的当前位置/偏移，供 App/网页显示舵机状态。设备端调用
`/ros_robot_controller/pwm_servo/get_state` 服务，再把结果汇总成一条 MQTT 应答。

- 请求：`walle/{t}/device/{d}/car/servo/status/query/request`
- 应答：`walle/{t}/device/{d}/car/servo/status/query/response`
- 请求不 retained，QoS1；应答只在收到查询后发布。

**请求字段**：

```json
{
  "request_id": "a1b2c3d4",
  "ids": [1, 2],
  "include_offset": false
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `request_id` | 是 | 原样回显 |
| `ids` | 否 | 要查询的舵机 ID，默认 `[1,2]`；当前小车 ID1=上下、ID2=左右；允许 1–4，不允许空数组或重复 |
| `include_offset` | 否 | 默认 `false`；为 `true` 时同时读取 offset |

**应答字段**：

```json
{
  "result": 0,
  "cause": "ok",
  "request_id": "a1b2c3d4",
  "servos": [
    {"id": 1, "position": 1500, "offset": null, "available": true, "error": null},
    {"id": 2, "position": 1480, "offset": null, "available": true, "error": null}
  ],
  "device_id": "666feb85d43410b2",
  "hostname": "orangepi5plus",
  "ip": "192.168.1.10",
  "timestamp": 1763000000,
  "datetime": "2026-11-13 22:30:00"
}
```

| 字段 | 说明 |
|---|---|
| `result` | `0` 成功 / `-1` 命令无效 / `-2` 命令错误 / `-3` 舵机控制链路不可用 |
| `cause` | `ok` / `命令无效` / `命令错误` / `舵机控制链路不可用` |
| `servos[]` | 按请求的 `ids` 顺序返回 |
| `servos[].id` | 舵机 ID；即使读取失败也要返回 |
| `servos[].position` | 当前绝对位置；失败为 `null` |
| `servos[].offset` | 偏移；未请求或失败为 `null` |
| `servos[].available` | 该舵机是否读取成功 |
| `servos[].error` | 失败原因，例如 `读取超时` / `服务不可用`；成功为 `null` |

**结果码规则**：

- `0`：所有请求的舵机都读取成功；
- `-1`：`ids` 为空；
- `-2`：`ids` 重复/超出 1–4，或 `ids` 不是数组、元素不是整数、`include_offset` 不是布尔等类型错误；
- `-3`：`get_state` 服务不可用，或任意一个请求的舵机读取失败。
  此时 `servos[]` 仍保留读取成功的舵机，App 可展示部分结果。

**性能与限制**：

- `get_state` 底层每个字段最多阻塞 2 秒；只读 2 个舵机的位置，最坏约 4 秒；
- `include_offset=true` 时，2 个舵机最多 4 次读取，最坏约 8 秒；
- App 查询超时建议：默认 6 秒；带 offset 时 10 秒；
- 不要每秒轮询；建议手动查询或 ≥5 秒一次；
- 设备端用独立的舵机查询线程池，不阻塞 baseState/runState 查询和推流动作线程。

**位置说明**：

- 返回的是查询瞬间的位置；如果舵机正在执行 `duration=1.0` 的动作，读到的是运动中的中间值，不是最终目标；
- 现有调试页的舵机范围是 800–2200，状态查询返回原始位置，不做钳位。

**设备端实现**：`net_mqtt_bridge/car_servo.py`（校验/结果/服务调用）与 `node.py`
（注册 `GetPWMServoState` 客户端）；`GetPWMServoState` 的 `id/position/offset` 都是数组，
MQTT 桥接层取数组第一个元素并回填请求的 ID。

### 4.12 舵机位置设置 car/servo/set/request → car/servo/set/response（V1.2，V1.9 取消应答，V1.18 改为请求-应答）

**作用**：按**绝对位置**设置一个或多个 PWM 舵机；设备端发布到
`/ros_robot_controller/pwm_servo/set_state`。

- 请求：`walle/{t}/device/{d}/car/servo/set/request`
- 应答：`walle/{t}/device/{d}/car/servo/set/response`
- 请求不 retained、QoS1；每条可解析的请求都会收到一条应答（含校验失败与控制权拒绝；
  空/非法 JSON 无法配对 `request_id`，不回执），应答不 retained、QoS1。

**请求字段**：

```json
{
  "request_id": "a1b2c3d4",
  "servos": [
    {"id": 1, "position": 1400},
    {"id": 2, "position": 1600}
  ],
  "duration": 1.0
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `request_id` | 是 | 原样回显，用于应答配对 |
| `servos` | 是 | 非空数组，最多 4 个；同一条消息一次下发 |
| `servos[].id` | 是 | 1–4；同一请求内不能重复 |
| `servos[].position` | 是 | 绝对位置整数，有效范围 800–2200 |
| `duration` | 否 | 秒，0.1–5.0，默认 1.0 |

**越界规则**：

- 所有请求的 `position` 都超出 800–2200 → 整条命令不执行；
- 只有一个越界、另一个合法 → 只执行合法的那一个；
- 越界的舵机记入应答的 `skipped[]`，合法的记入 `applied[]`；
- 单舵机请求且越界 → 没有可执行项。

**应答字段**（V1.18 起恢复 MQTT 回执）：

```json
{
  "device_id": "666feb85d43410b2",
  "hostname": "orangepi5plus",
  "ip": "192.168.1.10",
  "request_id": "a1b2c3d4",
  "result": 0,
  "cause": "ok",
  "duration": 1.0,
  "applied": [
    {"id": 1, "position": 1400},
    {"id": 2, "position": 1600}
  ],
  "skipped": [],
  "timestamp": 1763000000,
  "datetime": "2026-11-26 12:00:00"
}
```

| 字段 | 说明 |
|---|---|
| `result` | `0` 成功（至少一个舵机已发布）；`-1` 命令无效（`servos` 为空）；`-2` 命令错误（类型/ID/重复/`duration` 非法，或全部越界）；`-3` 舵机控制链路不可用；`-5` 控制权被其他客户端占用（带 `error_code=control_denied`，见 §4.19） |
| `cause` | `ok` / `命令无效` / `命令错误` / `底盘控制链路不可用` / `设备控制权限已被别人占用` |
| `duration` | 实际生效的动作时长（秒）；解析失败时为 `null` |
| `applied` | 已发布给底盘的舵机 `[{id, position}]`；`result != 0` 时为空数组 |
| `skipped` | 因超出 800–2200 被跳过的舵机 `[{id, position, reason}]` |
| `request_id` 及公共字段 | —（§3.1） |

> 控制权拒绝（`result=-5`）由设备端通用拒绝逻辑生成，只含公共字段与
> `result`/`cause`/`error_code`/`control_holder`，不含 `duration`/`applied`/`skipped`。

`result=0` 只表示“已发布到 `set_state`”，不表示舵机已经转到位；需要确认实际位置时，
在 `duration + 0.5s` 后调用 `car/servo/status/query/request`。

**注意事项**：

- `set_state` 是 ROS 话题，没有服务端回执；设备端内部结果 `0` 也只表示“已发布”，不表示舵机已经转到位；
- 多舵机必须放在同一条 `set_state` 消息里，不要拆成多次发布；
- `position` 是绝对位置，不是相对步进；重复发送同一条命令是幂等的；
- 连续设置时最后一条生效；
- 如果舵机正在运动，新命令会覆盖旧目标；
- 需要确认实际位置时，在 `duration + 0.5s` 后调用 `car/servo/status/query/request`。

**设备端实现**：`net_mqtt_bridge/car_servo.py` 负责校验/结果，
`node.py` 创建 `SetPWMServoState` 发布者；`state[].id`、`state[].position` 都是数组。

---

### 4.13 开始录像 record/start/request → record/start/response（V1.3）

**作用**：让设备端开始本地录像。`record_video_node` 订阅 `/image_raw`，通过 ffmpeg
编码为 H.264 并封装为 **fragmented MP4（fMP4）** 落盘到 `walle-device/data/recordings/`。

- 请求：`walle/{t}/device/{d}/record/start/request`
- 应答：`walle/{t}/device/{d}/record/start/response`
- 请求不 retained，QoS1；应答只在收到请求后发布。

**请求字段**：

```json
{
  "request_id": "r1",
  "segment_sec": 3600
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `request_id` | 是 | 原样回显 |
| `segment_sec` | 否 | 单文件录像时长（秒），0 或省略=使用配置默认值（3600） |

**应答字段**：

```json
{
  "result": 0,
  "cause": "ok",
  "request_id": "r1",
  "filename": "rec_20261115_143000.fmp4",
  "started_at": "2026-11-15 14:30:00",
  "started_ts": 1763187000.0,
  "play_url": "http://192.168.1.10:8088/recordings/rec_20261115_143000.fmp4",
  "device_id": "666feb85d43410b2",
  "hostname": "orangepi5plus",
  "ip": "192.168.1.10",
  "timestamp": 1763187000,
  "datetime": "2026-11-15 14:30:00"
}
```

| 字段 | 说明 |
|---|---|
| `result` | `0` 成功 / `-3` 录像服务不可用 / `-4` 已在录制 / `-6` 执行失败 / `-7` 磁盘空间不足 |
| `cause` | `ok` / `服务不可用` / `已在录制` / `启动录像失败` / `磁盘空间不足` |
| `filename` | 自动生成的录像文件名 `rec_YYYYMMDD_HHMMSS.fmp4` |
| `started_at` | 录像开始的本地时间字符串 |
| `started_ts` | 录像开始的 Unix 时间戳 |
| `play_url` | 录像文件的 HTTP 播放地址（`http://{device_ip}:8088/recordings/<filename>`） |

**结果码规则**：
- `0`：已启动录像；
- `-3`：`record_video_node` 服务不可用；
- `-4`：已有录像会话在进行，不重复启动；
- `-6`：启动 ffmpeg 失败；
- `-7`：磁盘剩余空间低于 6GB 最低要求，拒绝录像。

**注意事项**：
- 录像文件采用 fMP4（`+frag_keyframe+empty_moov+default_base_moof`），
  每个关键帧一个 fragment，**录制中断后已写入部分仍可播放**；
- 达到 `segment_sec` 后自动切到新文件（旧文件保留），同一会话连续分片；
- 录像目录超过存储上限（默认 16GB）时，自动按时间 FIFO 回收最旧文件；
- 播放通过设备内置 HTTP 服务（默认端口 8088），仅服务 `data/recordings/` 下的 `.fmp4` 文件。

**设备端实现**：`record_video/node.py` 提供 `/record_video/control` 服务；
`net_mqtt_bridge/record.py`（RecordNodeMixin）调用服务并发布 MQTT 应答。

---

### 4.14 停止录像 record/stop/request → record/stop/response（V1.3）

**作用**：停止当前录像会话，优雅关闭 ffmpeg 并写入文件尾部。

- 请求：`walle/{t}/device/{d}/record/stop/request`
- 应答：`walle/{t}/device/{d}/record/stop/response`

**请求字段**：

```json
{
  "request_id": "r2"
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `request_id` | 是 | 原样回显 |

**应答字段**：

```json
{
  "result": 0,
  "cause": "ok",
  "request_id": "r2",
  "filename": "rec_20261115_143000.fmp4",
  "device_id": "666feb85d43410b2",
  "hostname": "orangepi5plus",
  "ip": "192.168.1.10",
  "timestamp": 1763190600,
  "datetime": "2026-11-15 15:30:00"
}
```

| 字段 | 说明 |
|---|---|
| `result` | `0` 成功 / `-3` 录像服务不可用 / `-4` 当前未录制 / `-6` 执行失败 |
| `cause` | `ok` / `服务不可用` / `当前未录制` / `执行失败` |
| `filename` | 被停止的当前录像文件名 |

---

### 4.15 录像列表 record/list/query/request → record/list/query/response（V1.3）

**作用**：查询设备本地已保存的录像文件列表，含播放地址。

- 请求：`walle/{t}/device/{d}/record/list/query/request`
- 应答：`walle/{t}/device/{d}/record/list/query/response`

**请求字段**：

```json
{
  "request_id": "r3"
}
```

**应答字段**：

```json
{
  "result": 0,
  "cause": "ok",
  "request_id": "r3",
  "records": [
    {
      "filename": "rec_20261115_143000.fmp4",
      "size_bytes": 125829120,
      "created_at": "2026-11-15 14:30:00",
      "duration_sec": 3600,
      "play_url": "http://192.168.1.10:8088/recordings/rec_20261115_143000.fmp4"
    }
  ],
  "used_bytes": 125829120,
  "limit_bytes": 17179869184,
  "device_id": "666feb85d43410b2",
  "hostname": "orangepi5plus",
  "ip": "192.168.1.10",
  "timestamp": 1763190600,
  "datetime": "2026-11-15 15:30:00"
}
```

| 字段 | 说明 |
|---|---|
| `result` | `0` 成功 / `-3` 录像服务不可用 / `-6` 查询失败 |
| `cause` | `ok` / `服务不可用` / `查询录像列表失败` |
| `records[].filename` | 录像文件名 |
| `records[].size_bytes` | 文件大小（字节） |
| `records[].created_at` | 文件创建时间 |
| `records[].duration_sec` | 录像时长（秒），历史文件按码率估算 |
| `records[].play_url` | HTTP 播放地址，App 可直接用 `<video src>` 播放 |
| `used_bytes` | 录像目录已用空间（字节） |
| `limit_bytes` | 录像目录空间上限（字节） |

---

### 4.16 删除录像 record/delete/request → record/delete/response（V1.3）

**作用**：删除指定的录像文件。正在录制的文件不可删除。

- 请求：`walle/{t}/device/{d}/record/delete/request`
- 应答：`walle/{t}/device/{d}/record/delete/response`

**请求字段**：

```json
{
  "request_id": "r4",
  "filename": "rec_20261115_143000.fmp4"
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `request_id` | 是 | 原样回显 |
| `filename` | 是 | 要删除的文件名（仅文件名，不含路径；必须匹配 `rec_\d{8}_\d{6}\.fmp4`） |

**应答字段**：

```json
{
  "result": 0,
  "cause": "ok",
  "request_id": "r4",
  "filename": "rec_20261115_143000.fmp4",
  "device_id": "666feb85d43410b2",
  "hostname": "orangepi5plus",
  "ip": "192.168.1.10",
  "timestamp": 1763190600,
  "datetime": "2026-11-15 15:30:00"
}
```

| 字段 | 说明 |
|---|---|
| `result` | `0` 成功 / `-1` 命令无效（filename 为空）/ `-2` 非法文件名或路径 / `-3` 文件不存在或服务不可用 / `-4` 正在录制不可删 / `-6` 删除失败 |
| `cause` | `对应原因说明` |

**安全**：文件名白名单 `^rec_\d{8}_\d{6}\.fmp4$`，删除前校验真实路径在 `data/recordings/`
目录内，防止路径穿越。

### 4.17 播放录像 record/play/start/request → record/play/start/response（V1.5）

**作用**：把设备本地的 fMP4 录像文件用 ffmpeg 转封装（`-c copy`，不重编码）推到 SRS，
App 用返回的 `flv_url` / `whep_url` 拉流播放。同一时刻只允许一路播放，新请求会先停掉旧的。

- 请求：`walle/{t}/device/{d}/record/play/start/request`
- 应答：`walle/{t}/device/{d}/record/play/start/response`

**请求字段**：

```json
{
  "request_id": "r5",
  "filename": "rec_20261115_143000.fmp4",
  "offset_sec": 300
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `request_id` | 是 | 原样回显 |
| `filename` | 是 | 要播放的文件名（仅文件名，不含路径；白名单 `^rec_\d{8}_\d{6}(_\d+)?\.fmp4$`） |
| `offset_sec` | 否 | 从第几秒开始播放，默认 0；用于进度条拖动 |

**应答字段**：

```json
{
  "result": 0,
  "cause": "ok",
  "request_id": "r5",
  "filename": "rec_20261115_143000.fmp4",
  "stream": "record_a38e3322_ab12cd34",
  "flv_url": "http://srs-host:8080/live/record_a38e3322_ab12cd34.flv",
  "whep_url": "https://srs-host/rtc/v1/whep/?app=live&stream=record_a38e3322_ab12cd34",
  "offset_sec": 300
}
```

| 字段 | 说明 |
|---|---|
| `result` | `0` 成功 / `-1` 命令无效（filename 为空）/ `-2` 非法文件名或路径 / `-3` 未配置媒体服务器、文件不存在 / `-6` 启动 ffmpeg 失败 |
| `cause` | 对应原因说明 |
| `stream` | SRS 流名，每次播放唯一 |
| `flv_url` | HTTP-FLV 拉流地址（SRS 默认端口 8080，可由 `RECORD_PLAY_FLV_PORT` 覆盖） |
| `whep_url` | WebRTC WHEP 拉流地址（与直播共用，低延迟） |
| `offset_sec` | 实际生效的起始秒数（仅请求带了时回显） |

**停止播放**：App 退出播放时发 `record/play/stop/request`，设备端 kill ffmpeg 进程，
返回 `record/play/stop/response`（`result=0`、`stopped: true`；重复停止也返回 0）。

**实现要点**：
- ffmpeg 命令：`ffmpeg -re [-ss <offset>] -i <fmp4> -c copy -f flv rtmp://<srs>:1935/live/<stream>?secret=<secret>`
- `-c copy` 直接转封装 H.264 +（未来）AAC，CPU 占用极低
- `-ss` 放在 `-i` 前，快速跳到最近关键帧（差几秒，用户无感）
- 流名格式 `record_{device_id前8位}_{8位随机hex}`，避免与直播流冲突
- 路径安全：校验真实路径在 `RECORD_STORAGE_PATH` 目录内，防止路径穿越

**前置条件**：`MEDIA_SERVER_HOST` / `MEDIA_SERVER_SECRET` 已配置（与直播推流共用），且 SRS 服务器可达。

---

### 4.18 客户端在线状态 client/{client_id}/online（V1.13）

**作用**：让设备端知道 App/Web 客户端是否在线，作为控制权租约（§4.19）中
holder 掉线自动释放的依据。该主题是**租户级**的，不挂在 `device` 段下。

- 主题：`walle/{t}/client/{client_id}/online`
- 类型：主动上报（**retained + LWT**，QoS1）
- 请求/应答：无；设备端只订阅记录，不回复

**主题段**：

| 段 | 说明 |
|---|---|
| `{t}` | 租户，= App/Web 登录的 MQTT 用户名（清洗后） |
| `client` | 固定字面量，区分设备主题 |
| `{client_id}` | 客户端持久化标识；Web 存 localStorage，App 存 SharedPreferences |
| `online` | 固定字面量 |

**报文**：

```json
{
  "client_id": "web-1a2b3c4d",
  "client_name": "Web",
  "version_code": 3,
  "version_name": "0.1.3",
  "online": true,
  "timestamp": 1763000000
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `client_id` | 是 | 必须与主题第 4 段一致；同一客户端刷新/重启后保持不变 |
| `client_name` | 否 | 展示名，如 `Web`、`Android Pixel 7` |
| `version_code` | 否 | 客户端版本号（整数） |
| `version_name` | 否 | 客户端版本名（字符串） |
| `online` | 是 | `true` 上线 / `false` 下线 |
| `timestamp` | 是 | Unix 秒 |

**两端约定**：

- 连接成功后立即发布 `online:true`，**retained**；
- 连接时设置遗嘱：同一主题、同一 `client_id`、`online:false`、retained；
- 主动断开（退出登录/关闭页面）尽量先发布 `online:false` 再断开；
- 异常掉线由 Broker 代发遗嘱；
- 页面刷新会短暂出现 `false` → `true`，消费方需容忍（控制权租约应加 grace）；
- 设备端订阅 `walle/{t}/client/+/online`，按 `client_id` 维护在线表。

> 注意：`client_id` 是客户端自报的显示/关联标识，**不是权限凭证**。控制权租约（§4.19）
> 只用于同一租户内可信客户端之间的协作（提前约定“谁在用”），不能防伪造。

### 4.19 控制权租约 control/acquire|release/request → .../response（V1.15）

**作用**：同一租户下可能同时有多个 App/Web 在线。设备默认 **fail-open**：没有 holder 时
所有人都可以直接控制；当某个客户端想独占设备（不希望自己的操作被别人打断）时，先
`control/acquire` 取得 holder，用完再 `control/release`。

**设备端判定规则**

- 只读查询（`baseState`、`runState`、`car/servo/status`、`record/list`）始终放行；
  持有者发查询会续租，非持有者不会；
- 控制类请求（`video/*`、`phone/audio/*`、`record/start|stop|delete|play/*`、
  `car/motor/set/request`、`car/servo/set/request`）必须匹配当前 holder，否则拒绝；
- `control/acquire|release` 本身不参与租约校验；
- 租约 TTL 900 秒：持有者的有效请求（查询或控制）都会续租；
- holder 的 presence 离线后 **grace 10 秒**释放；grace 内重新上线则取消释放；
- agent 作为 holder 时，presence 使用 `walle/{t}/agent/{agent_id}/online`（retained + LWT），设备端把 `online:false` 当作 holder 离线；
- 设备重启后租约清空（纯内存，不落盘）。

**获取/续租（App → 设备）**

- 请求：`walle/{t}/device/{d}/control/acquire/request`

```json
{ "request_id": "a1b2c3d4", "client_id": "web-1a2b3c4d", "client_name": "Web" }
```

- 应答：`walle/{t}/device/{d}/control/acquire/response`

```json
{
  "device_id": "666feb85d43410b2",
  "hostname": "raspberrypi",
  "ip": "192.168.1.20",
  "request_id": "a1b2c3d4",
  "result": 0,
  "action": "granted",
  "cause": "ok",
  "lease_ttl_sec": 900,
  "control_holder": {
    "client_id": "web-1a2b3c4d",
    "client_name": "Web",
    "acquired_at": 1763000000,
    "expires_at": 1763000900,
    "remaining_sec": 900
  },
  "timestamp": 1763000000,
  "datetime": "2026-11-25 12:00:00"
}
```

| result | action | 含义 |
|---|---|---|
| 0 | `granted` | 原本空闲，获取成功 |
| 0 | `renewed` | 同一 `client_id` 续租成功 |
| -1 | `invalid` | 缺少 `client_id` / `client_name` |
| -5 | `occupied` | 已被别人占用；`control_holder` 返回当前持有者 |

**释放（App → 设备）**

- 请求：`walle/{t}/device/{d}/control/release/request`

```json
{ "request_id": "e5f6a7b8", "client_id": "web-1a2b3c4d" }
```

- 应答：`walle/{t}/device/{d}/control/release/response`（公共字段同上）

| result | action | 含义 |
|---|---|---|
| 0 | `released` | 释放成功 |
| 0 | `already_free` | 当前本来就没有 holder |
| -1 | `invalid` | 缺少 `client_id` |
| -5 | `not_holder` | 不是当前持有者；`control_holder` 返回当前持有者 |

**`control_holder` 对象**

| 字段 | 说明 |
|---|---|
| `client_id` | 持有者客户端 ID |
| `client_name` | 持有者展示名 |
| `acquired_at` | 获取时间（Unix 秒） |
| `expires_at` | 到期时间（Unix 秒） |
| `remaining_sec` | 剩余秒数（设备端计算） |

`control_holder` 为 `null` 表示空闲（fail-open）。

**控制请求被拒绝**

请求-应答类控制请求（如 `stream/start/request`）被拒绝时，设备在对应 `.../response`
里返回（字段与正常失败应答同构）：

```json
{
  "device_id": "666feb85d43410b2",
  "hostname": "raspberrypi",
  "ip": "192.168.1.20",
  "request_id": "v1a2b3c4",
  "result": -5,
  "cause": "设备控制权限已被别人占用",
  "error_code": "control_denied",
  "control_holder": { "client_id": "web-1a2b3c4d", "client_name": "Web",
                      "acquired_at": 1763000000, "expires_at": 1763000900,
                      "remaining_sec": 850 },
  "timestamp": 1763000050,
  "datetime": "2026-11-25 12:00:50"
}
```

- `car/motor/set/request` 与 `car/servo/set/request` 被拒绝时回执 `result=-5`；
  其它请求-应答被拒绝时同样返回 `-5`，统一见 §2.8；
  客户端应通过 `runState/query/response.control_holder` 或 acquire 失败提前判断。

**客户端约定**

- 不 acquire 也能控制（fail-open）；只有需要独占时才 acquire；
- 申请得到 `occupied` 时展示 `control_holder.client_name`，不要自动重试抢占；
- 收到 `error_code="control_denied"` 时同步 `control_holder` 并提示用户；
- 持有者离线超过 grace 或 TTL 到期后，设备自动恢复 fail-open。

### 4.20 agent 对话 agent/{agent}/chat/request|response（v1.16）

> 适用于 `walle-agent`（实现见 `walle-agent/src/car_agent.py`）。Web 控制台与 agent 之间使用
> 独立的 `agent/{agent}` 子树。

**请求 JSON（chat/request）**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `client_id` | string | 是 | 发起对话的 Web 客户端 `client_id`；缺失时 `error:"invalid"`，回复「参数非法」 |
| `client_name` | string | 是 | 发起对话的 Web 客户端 `client_name`；缺失时 `error:"invalid"`，回复「参数非法」 |
| `request_id` | string | 是 | 格式见 §2.4；缺失时 `error:"invalid"`，回复「参数非法」 |
| `device` | string | 是 | 缺失时 `error:"invalid"`，回复「请求里没有指定设备（device 字段为空）。」 |
| `text` | string | 是 | 缺失时 `error:"invalid"`，回复「请求里没有文本内容。」 |
| `ts` | number | 否 | 毫秒时间戳，agent 不读取 |
| `intent` | string | 否 | 急停通知用 `"abort"`，agent 不读取（靠 `text` 解析） |

- `client_id` / `client_name` 标识**发起对话的 Web 客户端**，用于日志与审计；agent 作为独立控制端向设备下发请求时使用自己的 `agent_id` / `agent_name`（见 §4.21）。
- `client_name` 同时用于 agent 日志与运动 request_id 前缀；`user` 字段已废弃，客户端不再需要发送。
- 未列出的字段（如旧客户端的 `user`）由 agent 忽略。

```json
{
  "client_id": "web-vg8ww5er",
  "client_name": "Web666",
  "request_id": "web-9f3c1a2b",
  "device": "0580a65014af4d0a",
  "text": "前进1米",
  "ts": 1795449999123
}
```

**应答 JSON（chat/response）**

公共字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `request_id` | string | 原样回传 |
| `agent_id` | string | agent 的 client_id（身份，不是展示名） |
| `device` | string | 原样回传 |
| `ts` | number | Unix 秒 |
| `ok` | bool | 成功/失败 |
| `reply` | string | 自然语言回复 |
| `intent` | object | 可选，解析结果 |
| `warning` | string | 可选，参数被夹紧的提示 |
| `error` | string | 可选，错误码 |
| `action` | string | 可选：`move` / `rotate` / `stop` / `status` |
| `duration_sec` | number | 可选，预计运动时长 |

成功示例（运动）：

```json
{"request_id":"web-9f3c1a2b","agent_id":"agent-b851fdde","device":"0580a65014af4d0a",
 "ts":1795449999,"ok":true,
 "reply":"已解析：前进 1.00 米，速度 0.50 m/s，预计 2.0 秒。 已开始执行。",
 "intent":{"action":"move","direction":"forward","distance_m":1.0,"speed_mps":0.5},
 "action":"move","duration_sec":2.0}
```

成功示例（状态 / 停止）：

```json
{"request_id":"web-9f3c1a2b","agent_id":"agent-b851fdde","device":"0580a65014af4d0a",
 "ts":1795449999,"ok":true,"reply":"状态查询完成。",
 "intent":{"action":"status"},"action":"status"}
{"request_id":"web-9f3c1a2b","agent_id":"agent-b851fdde","device":"0580a65014af4d0a",
 "ts":1795449999,"ok":true,"reply":"已下发停止（零速）。",
 "intent":{"action":"stop"},"action":"stop"}
```

`chat/response` 只携带上表字段；`mqtt`、`note`、`active_device`、`status`、`result`
等属于 agent 内部结果，不会透传到应答报文。

长会话进度：agent 可通过 §4.25 的 `chat/progress/report` 主动上报关键节点；
`chat/response` 仍是唯一的最终结果，进度报文不能替代应答。

控制权约定：

- 控制动作（运动/停止）前先 `control/acquire`，结束后 `control/release`；状态查询不 acquire；
- acquire 返回 `occupied` 时不自动抢占，直接拒绝本次请求，并在 `reply` 中提示当前 holder。
- 会话执行中用户可发送 `agent/{a}/chat/stop/request`（§4.24）优雅停止：中止当前运动、补发零速并释放控制权，无需等待本次 `chat/response`。

失败与错误码：

| 场景 | `ok` | 关键字段 |
|---|---|---|
| 文本无法识别 | false | `reply`，无 `error` |
| 参数超上限（距离/角度） | false | `reply` 说明，无 `warning` |
| 参数被夹紧（速度） | true | `warning`，`reply` 为已解析文案 |
| 请求缺 client_id/client_name/request_id/device/text | false | `error:"invalid"`，`reply` 说明 |
| MQTT 未连接 | false | `error:"mqtt_not_connected"`，`reply` 说明 |
| 正在执行上一条 | false | `error:"busy"`，`reply` 提示正在执行 |
| 设备控制权被占用 | false | `error:"control_occupied"`，`reply` 提示当前 holder |
| 控制权获取失败/超时 | false | `error:"control_failed"`，`reply` 说明 |
| 停止但无目标设备 | false | `error:"no_device"`，`reply` 说明 |
| agent 内部异常 | false | `error:"agent_exception"`，`reply` 含异常文本 |

`error` 取值集合：`invalid`、`mqtt_not_connected`、`busy`、`no_device`、`agent_exception`、`control_occupied`、`control_failed`。
agent 不发送「超时」响应；20 秒未收到响应：删除 pending、解锁发送按钮、提示「agent 超时未响应（20s）」。

**agent 订阅关系**

agent 启动/重连后订阅：

- `walle/{t}/agent/{agent}/chat/request`
- `walle/{t}/agent/{agent}/baseState/query/request`
- `walle/{t}/agent/{agent}/config/modify/request`
- `walle/{t}/agent/{agent}/chat/stop/request`
- `walle/{t}/device/+/online`
- `walle/{t}/device/+/+/response`（1 层资源）
- `walle/{t}/device/+/+/+/response`（2 层，如 `baseState/query/response`、`stream/start/response`、`control/acquire/response`）
- `walle/{t}/device/+/+/+/+/response`（3 层，如 `record/list/query/response`）
- `walle/{t}/device/+/+/+/+/+/response`（4 层，如 `car/servo/status/query/response`）

`response` 使用分层通配符，新增同层资源无需改订阅；新增更深层级时补一条模式。`WALLE_AGENT_DEBUG=1` 时启动会打印实际订阅主题。

### 4.21 agent 在线状态 agent/{agent}/online（v1.16）

**报文格式**

```json
{
  "agent_id": "agent-b851fdde",
  "agent_name": "小橙车",
  "online": true,
  "version_code": 1,
  "version_name": "0.1.1",
  "ts": 1795449995
}
```

- 主题后缀与设备端 `device/{device}/online` 对齐；载荷以本节示例为最小必选字段：`agent_id`、`agent_name`、`online`、`version_code`、`version_name`、`ts`。允许附加可选字段（如 `capabilities`），消费方应忽略未知字段。
- `agent_id` 为 agent 的 id，即 MQTT 中的 client_id，其前缀在 `walle-agent.conf` 中可配置，对应变量 `CLIENT_ID_PREFIX`。
- `agent_name` 为 agent 的展示名，保存在 agent 本机 `etc/.run/agent-name`：首次启动读取，文件不存在时自动生成 `agent_<hostname>` 并落盘；之后通过 `config/modify`（§4.23）修改并写回。名称不参与 topic/ACL。agent 作为独立控制端向设备下发请求时，`client_id` 使用 `agent_id`、`client_name` 使用 `agent_name`。
- `version_code` / `version_name` 来自 `walle-agent/VERSION` 的 `AGENT_VERSION_NUM` / `AGENT_VERSION_NAME`；启动时打印 `AGENT_VERSION_NAME` 与 `AGENT_VERSION_DATE`。`ts` 为 Unix 秒，与设备端 `timestamp` 语义相同。
- `TENANT` 必须显式配置为 Web/设备所属租户；独立部署时 `MQTT_USER` 是 agent 自己的账号，不能作为租户回落。
- 上线时发布 retained `online:true`；LWT 使用同一结构、`online:false`；正常退出也会主动发布一次离线报文。
- 设备端订阅 `walle/{t}/agent/+/online`，按 `agent_id` 维护在线表；设备账号 ACL 需允许该订阅。
- 设备端把 `agent/{agent_id}/online` 同时作为控制权 presence；收到 `online:false` 后按 §4.19 的 grace 释放租约，异常掉线由 LWT 覆盖。
- Web 控制台只把 `online:true` 的条目放进下拉，按 `ts` 降序。
- agent 作为独立控制端，控制动作前 `control/acquire`、结束后 `control/release`；设备端按 §4.19 处理租约与 grace 释放。

### 4.22 agent 基本状态查询 agent/{agent}/baseState/query/request → response（v1.20）

**作用**：查询 agent 及其**所在部署机器**的运行状态（OS、CPU、内存、磁盘、网络、运行时长），
供 Web 控制台展示机器资源情况。只读查询，不涉及设备控制权，也不需要 acquire。

- 请求：`walle/{t}/agent/{a}/baseState/query/request`
- 应答：`walle/{t}/agent/{a}/baseState/query/response`
- 请求/应答均不 retained，QoS1；agent 收到请求后发布一条应答。

**请求字段**：

```json
{ "request_id": "web-9f3c1a2b" }
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `request_id` | 是 | 原样回显，用于配对 |

**应答字段**：

```json
{
  "result": 0,
  "cause": "ok",
  "request_id": "web-9f3c1a2b",
  "agent_id": "agent-b851fdde",
  "agent_name": "小橙车",
  "version_code": 1,
  "version_name": "0.1.1",
  "tenant": "home",
  "hostname": "orangepi5plus",
  "ip": "192.168.1.10",
  "os": {
    "name": "Ubuntu",
    "version": "22.04",
    "pretty_name": "Ubuntu 22.04.4 LTS",
    "kernel": "5.10.160-legacy-rk35xx",
    "arch": "aarch64",
    "platform": "Linux-5.10.160-legacy-rk35xx-aarch64-with-glibc2.35",
    "python": "3.10.12"
  },
  "cpu": {
    "model": "ARMv8 Processor rev 0",
    "cores": 8,
    "usage_percent": 12.3,
    "temperature_c": 45.6,
    "load_avg": [0.52, 0.41, 0.33]
  },
  "memory": {
    "total_bytes": 4294967296,
    "used_bytes": 1288490189,
    "available_bytes": 3006477107,
    "usage_percent": 30.0
  },
  "disk": {
    "path": "/",
    "total_bytes": 137438953472,
    "used_bytes": 54975581389,
    "free_bytes": 82463372083,
    "usage_percent": 40.0
  },
  "network": {
    "rx_bytes_per_sec": 1024.5,
    "tx_bytes_per_sec": 512.0
  },
  "uptime_sec": 86400.0,
  "timestamp": 1763000000,
  "datetime": "2026-11-26 12:00:00"
}
```

| 字段 | 说明 |
|---|---|
| `result` / `cause` | 统一错误码（§2.8）：`0` 成功；`-1` 请求无效；`-6` 采集机器状态失败 |
| `request_id` | 原样回显 |
| `agent_id` / `agent_name` / `version_code` / `version_name` | agent 身份与版本（与 `agent/{a}/online` 一致） |
| `tenant` | 租户 |
| `hostname` / `ip` | agent 所在机器主机名与出口内网 IP |
| `os` | `name` / `version` / `pretty_name` / `kernel` / `arch` / `platform` / `python` |
| `cpu` | `model`、逻辑核数 `cores`、`usage_percent`（0.2s 采样）、`temperature_c`（CPU/SoC 温度，℃）、`load_avg`（1/5/15 分钟） |
| `memory` | 总/已用/可用字节与使用率 |
| `disk` | 根分区路径、总/已用/空闲字节与使用率 |
| `network` | 所有非 `lo` 网卡的聚合速率：`rx_bytes_per_sec`（每秒接收字节）、`tx_bytes_per_sec`（每秒发送字节） |
| `uptime_sec` | 机器运行时长（秒，来自 `/proc/uptime`） |
| `timestamp` / `datetime` | 应答时间（Unix 秒 / 本地时间） |

**实现与约束**：

- agent 使用标准库 + `/proc`、`/sys/class/thermal` 采集，不依赖 `psutil`；单字段读取失败时该字段返回 `null`，不影响整条应答。
- CPU 使用率与网络速率共用一次 0.2s 采样：分别读取 `/proc/stat` 和 `/proc/net/dev` 前后两次计数，按实际间隔换算。
- CPU 温度取 `thermal_zone` 中 CPU/SoC/package 类 zone 的最高值；没有 thermal zone 时为 `null`。
- 该查询只读，不 acquire 设备控制权，也不改变 agent 的运动状态。
- agent 无论 `CHAT_MQTT_ENABLED` 是否开启，都会订阅并应答本主题。

### 4.23 agent 配置修改 agent/{agent}/config/modify/request → response（v1.21）

**作用**：在线修改 agent 配置。当前仅支持 `agent_name`（展示名）；后续按需扩展，
agent 忽略未识别的字段。

- 请求：`walle/{t}/agent/{a}/config/modify/request`
- 应答：`walle/{t}/agent/{a}/config/modify/response`
- 不 retained，QoS1；agent 无论 `CHAT_MQTT_ENABLED` 是否开启都会订阅并应答。
- 修改成功后 agent 把名称写入本机 `etc/.run/agent-name`；若开启了 online 上报
  （`CHAT_MQTT_ENABLED=1`），立即重发 retained `agent/{a}/online`，控制端无需重连即可看到新名称。

**请求字段**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `request_id` | string | 是 | 配对用，原样回显 |
| `agent_name` | string | 是 | 新展示名；`strip` 后 1~64 字符，允许空格与中文，不允许控制字符 |

```json
{ "client_id": "web-vg8ww5er",
  "client_name": "Web666",
  "request_id": "web-9f3c1a2b", 
  "agent_name": "903" }
```

**应答字段**：

```json
{
  "result": 0,
  "cause": "ok",
  "request_id": "web-9f3c1a2b",
  "agent_id": "agent-772c194f",
  "agent_name": "903",
  "tenant": "home",
  "timestamp": 1763000000,
  "datetime": "2026-11-26 14:30:00"
}
```

| 字段 | 说明 |
|---|---|
| `result` / `cause` | 统一错误码（§2.8）：`0` 成功；`-1` 请求无效（缺 `agent_name`）；`-2` 参数错误（空、超长、含控制字符）；`-6` 写盘失败 |
| `request_id` | 原样回显 |
| `agent_id` | agent 的 client_id |
| `agent_name` | 修改后的名称；失败时为当前名称 |
| `tenant` | 租户 |
| `timestamp` / `datetime` | 应答时间（Unix 秒 / 本地时间） |

**约束**：

- 只改配置，与运动控制无关，不需要 acquire 设备控制权，也不影响进行中的运动任务。
- 名称仅用于展示与 `client_name`，不参与 topic、ACL 与身份计算；`agent_id` 始终是 clientid。
- 写盘失败返回 `-6`，内存中的名称保持不变。

### 4.24 agent 会话停止 agent/{agent}/chat/stop/request → response（v1.22）

**作用**：用户中止 agent 当前正在执行的会话。agent 会**优雅停止**：

1. 对当前运动线程置中止标记，线程退出时补发 3 次零速并释放控制权；
2. agent 再向目标设备补发一次零速（兜底），随后释放设备控制权。

没有进行中的会话且未持有控制权时幂等返回成功（`stopped:false`）。

- 请求：`walle/{t}/agent/{a}/chat/stop/request`
- 应答：`walle/{t}/agent/{a}/chat/stop/response`
- 不 retained，QoS1；agent 无论 `CHAT_MQTT_ENABLED` 是否开启都会订阅并应答。

**请求字段**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `request_id` | string | 是 | 配对用，原样回显 |
| `device` | string | 否 | 要停止的设备；缺省用当前会话设备或 agent 持有的控制权设备 |
| `client_id` / `client_name` | string | 否 | 发起方标识，仅用于 agent 审计日志 |

```json
{
  "client_id": "web-vg8ww5er",
  "client_name": "Web666",
  "request_id": "web-9f3c1a2b",
  "device": "0580a65014af4d0a"
}
```

**应答字段**：

```json
{
  "result": 0,
  "cause": "ok",
  "request_id": "web-9f3c1a2b",
  "agent_id": "agent-772c194f",
  "tenant": "home",
  "device": "0580a65014af4d0a",
  "stopped": true,
  "released": true,
  "timestamp": 1763000000,
  "datetime": "2026-11-26 15:00:00"
}
```

| 字段 | 说明 |
|---|---|
| `result` / `cause` | 统一错误码（§2.8）：`0` 成功（含无会话的幂等成功，此时 `cause="no_active_session"`）；`-1` 缺 `request_id`；`-3` MQTT 未连接；`-5` 控制权被其他客户端占用；`-6` 停止执行失败 |
| `request_id` | 原样回显 |
| `agent_id` / `tenant` | agent 身份与租户 |
| `device` | 实际停止的设备；没有会话/目标时为 `null` |
| `stopped` | 是否中止了进行中的会话 |
| `released` | 控制权是否已释放 |
| `timestamp` / `datetime` | 应答时间（Unix 秒 / 本地时间） |

**约束**：

- 只中止 agent 会话产生的电机运动；舵机是绝对位置控制，不存在持续转动，本命令不改变舵机位置。
- 幂等：重复发送不会报错；第二次起 `stopped:false`。
- 停止过程复用 `chat` 的停止流程，结束后 agent 不再持有该设备控制权。

### 4.25 agent 会话进度上报 agent/{agent}/chat/progress/report（v1.23）

**作用**：长会话执行期间，agent 主动上报关键进度节点，供 Web/App 展示进度、
避免把长任务误判为超时。当前会话较短，agent 暂不产生该上报；协议先行定义，
供后续 LLM/多步任务使用。

- 主题：`walle/{t}/agent/{a}/chat/progress/report`
- 方向：agent → Web/App（主动上报，无请求、无应答）
- 不 retained，QoS1；重复投递按 `request_id + stage` 幂等展示，`progress` 取较大值。

**报文字段**：

```json
{
  "agent_id": "agent-772c194f",
  "request_id": "web-9f3c1a2b",
  "device": "0580a65014af4d0a",
  "stage": "progress",
  "progress": 40,
  "message": "正在前进 1.2/3.0 米",
  "timestamp": 1763000000,
  "datetime": "2026-11-26 15:20:00"
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `agent_id` | 是 | agent clientid |
| `request_id` | 是 | 对应 `chat/request` 的 request_id，用于把进度关联回会话 |
| `device` | 否 | 目标设备；纯对话类会话可为空 |
| `stage` | 是 | 关键节点，取值见下表 |
| `progress` | 否 | 0–100，`completed` 时为 100；无百分比语义时可省略 |
| `message` | 否 | 人类可读说明，客户端只展示、不解析 |
| `cause` | 否 | `failed` / `stopped` 时的失败或停止原因 |
| `timestamp` / `datetime` | 是 | 上报时间（Unix 秒 / 本地时间） |

**stage 取值**：

| stage | 含义 |
|---|---|
| `accepted` | 请求已接受，尚未开始执行 |
| `started` | 会话开始执行 |
| `progress` | 执行中，通常带 `progress` |
| `waiting` | 等待外部事件（设备响应、用户确认等） |
| `completed` | 会话完成 |
| `failed` | 会话失败 |
| `stopped` | 被 `chat/stop` 中止 |

**约束**：

- 只在关键节点上报，不做高频轮询；带 `progress` 的上报建议不超过 1 次/秒。
- 进度是**通知**，不改变 `chat/response` 的请求-应答语义；客户端不能以进度报文替代最终应答。
- Web/App 订阅 `walle/{t}/agent/+/chat/progress/report`，按 `request_id` 归并；
  收到未知 `request_id` 时忽略。
- 该主题不 retained；客户端重连后只依赖 `chat/response` 的最终结果，不补发历史进度。

## 5. 修改协议的流程

1. **先改本文档**，让文档成为讨论与评审的载体。
2. 同步修改各端（App/Web/agent/设备）实现。
3. 同步更新测试，**缺一不可**：
   - App：`TopicNamesTest`（主题命名/清洗）、`MessageRouterTest`（路由/去重）、
     以及对应模型的解析测试（`StatusSnapshotTest` 等）
   - 设备端：`net_mqtt_bridge/test/test_protocol.py`（主题配对/订阅清单/request_id）、
     `test_car_motor.py`（车控校验、轮速预算与应答字段）等
   - agent：对话/在线报文字段与必填校验测试（待补）
4. 本地验证通过后提交（CI 工作流已移除，提交前务必本地跑通以下检查）：

```bash
./gradlew :app:testDebugUnitTest        # App 契约测试
python3 -m pytest -q walle-device/ros2_ws/src/net/net_mqtt_bridge/test   # 设备端契约测试
```

**三条铁律**：

- 应答必须原样回显 `request_id`（历史 bug：漏回显 → App 丢弃结果 → 一直转圈）；
- 请求按主题第 4 段的 `device_id` 路由，设备端不再校验 uuid（v1.12 起 uuid 已删除）；
- 新增主题段先过 `sanitize`，各端清洗规则必须逐字符一致。

---

## 6. 变更记录

> 历史条目中的结果码以当时版本为准；当前统一错误码见 §2.8。

### 2026-11-26：runState 分组改名 stream 并新增 is_supported（文档 v1.25）

**变更类型**：破坏性变更（分组改名 + 字段删改，不保留兼容）。

**变更内容**

- `runState/query/response` 的 `video` 分组改名为 `stream`，与 `stream/start|stop` 主题一致。
- 所有功能分组新增 `is_supported`：表示设备是否支持该功能；`is_supported=false` 时该分组
  其余字段一律为 `null` / `false`，客户端显示“不支持/隐藏”。
  - 当前 ROS2 端除 `recording` 外写死 `true`；`recording.is_supported` 取
    `walle_camera.conf` 的 `record.enabled`；linux-demo 的 `motor/servo/battery/sonar/recording`
    为 `false`。
- `stream` / `recording` 的摄像头字段统一为 `camera_device`；`audio_input` 为录音设备名，
  默认设备输出字面量 `"default"`。
- `recording` 移除 `enable`，改由 `is_supported` 表示配置是否启用录像。
- `phone_audio` 仅保留 `is_supported` / `playing` / `room_number`。
- `battery` / `sonar` 移除 `age_sec`：超过 1 秒无新采样时 `available=false`，
  且测量值置 `null`（不再返回最后一次旧值）。

**影响与兼容性**：App、Web、设备端（ROS2 与 linux-demo）必须同步升级；
旧字段 `video` / `recording.enable` / `age_sec` 不再返回。

**验证方式**：发送 `runState/query/request`，应答包含 `stream` 分组且每个分组都有
`is_supported`；断开传感器后 `battery/sonar.available=false` 且测量值为 `null`；
`record.enabled=false` 时 `recording.is_supported=false`。

### 2026-11-26：推流主题改为 stream，video_device 改为 camera_device（文档 v1.24）

**变更类型**：破坏性变更（主题资源名与应答字段重命名，不保留兼容）。

**变更内容**

- `device/{d}/video/start/request|response` → `device/{d}/stream/start/request|response`
- `device/{d}/video/stop/request|response` → `device/{d}/stream/stop/request|response`
- 推流相关字段 `video_device` → `camera_device`（`stream/start|stop/response`
  与 `runState/query/response` 的 `video` 分组）。
- Web/Android/设备端（ROS2 `net_mqtt_bridge` 与 linux-demo）同步；录像 `record/*` 不变。

**影响与兼容性**：旧主题 `video/start|stop/*` 与旧字段 `video_device` 不再可用，
所有客户端必须同步升级。

**验证方式**：订阅 `walle/{t}/device/{d}/stream/start/response` 并发送 start 请求，
应答含 `camera_device` 与 `room_number`、不含 `video_device`；stop 流程同样；
`runState/query/response` 的 `video.camera_device` 正常返回。

### 2026-11-26：新增 agent 会话进度上报（文档 v1.23）

**变更类型**：新增 agent 主动上报主题，不改变现有请求-应答协议。

**变更内容**

- 新增 `walle/{t}/agent/{a}/chat/progress/report`：长会话按关键节点上报
  `stage` / `progress` / `message` / `cause`（格式见 §4.25），用于进度展示与超时判定。
- 当前会话较短，agent 暂不产生该上报；后续 LLM/多步任务接入。

**影响与兼容性**：纯新增上报主题，不订阅不影响现有功能；`chat/response` 仍是唯一最终结果。

**验证方式**：联调时用 MQTT 客户端订阅该主题，并向其发布一条 `stage=progress`
样例报文，确认 Web/App 能按 `request_id` 关联会话；QoS1 重复投递时进度取较大值。

### 2026-11-26：新增 agent 会话停止（文档 v1.22）

**变更类型**：新增 agent 请求-应答主题，不改变现有设备协议。

**变更内容**

- 新增 `walle/{t}/agent/{a}/chat/stop/request → response`：中止当前会话的运动线程、
  补发零速并释放设备控制权；没有进行中的会话时幂等返回成功（格式见 §4.24）。
- agent 订阅该主题（不受 `CHAT_MQTT_ENABLED` 影响）；`chat` 会话执行中即可发送，
  无需等待本次 `chat/response`。

**影响与兼容性**：纯新增主题；客户端可用它优雅停止 agent 会话，替代直接向设备发零速。

**验证方式**：发起一条运动会话后发送 stop 请求，应答 `result=0`、`stopped=true`、
`released=true`；再次发送时 `stopped=false`；设备端收到零速并释放控制权。

### 2026-11-26：新增 agent 配置修改（文档 v1.21）

**变更类型**：新增 agent 请求-应答主题，不改变现有设备协议。

**变更内容**

- 新增 `walle/{t}/agent/{a}/config/modify/request → response`；当前仅支持修改
  `agent_name`（格式见 §4.23）。
- `agent_name` 不再从 `etc/walle-agent.conf` 读取：启动时读 `etc/.run/agent-name`，
  缺失则以 `agent_<hostname>` 生成并落盘；修改成功后写回该文件并重发 retained `online`。
- `scripts/auto_gen_conf_service.sh` 不再询问/写入 `AGENT_NAME`。

**影响与兼容性**：旧配置文件中的 `AGENT_NAME` 会被忽略；未创建 `.run/agent-name` 的
agent 首次启动后名称变为 `agent_<hostname>`，可通过新主题改回。

**验证方式**：向 `agent/{a}/config/modify/request` 发布
`{"request_id":"t1","agent_name":"903"}`，应答 `result=0` 且 `agent_name=903`；
`etc/.run/agent-name` 内容同步更新；`agent/{a}/online` 的 `agent_name` 变为新值。

### 2026-11-26：新增 agent 基本状态查询（文档 v1.20）

**变更类型**：新增 agent 请求-应答主题，不改变现有设备协议。

**变更内容**

- 新增 `walle/{t}/agent/{a}/baseState/query/request → response`；agent 只读返回自身
  与所在机器的 OS、CPU、内存、磁盘、网络、运行时长等信息（格式见 §4.22）。
- 应答包含 `agent_id`、`agent_name`、`version_code`、`version_name`、`tenant`、
  `hostname`、`ip`、`os`、`cpu`、`memory`、`disk`、`network`、`uptime_sec` 与公共时间字段。
- `walle-agent` 新增订阅该请求主题（不受 `CHAT_MQTT_ENABLED` 影响），使用标准库 + `/proc`
  采集，不新增第三方依赖。
- 查询只读，不 acquire 设备控制权；`result` 使用 §2.8 统一错误码（0 成功、-6 采集失败）。

**影响与兼容性**：纯新增主题；旧 Web/agent 不订阅时互不影响。Web 控制台可按需增加展示入口。

**验证方式**：向 `walle/{t}/agent/{a}/baseState/query/request` 发布
`{"request_id":"t1"}`，应收到同 `request_id` 的应答，且 `hostname`、`ip`、`cpu`、`memory`、
`disk`、`uptime_sec` 非空；agent 日志应打印该请求与发布结果。

### 2026-11-26：统一所有 response 错误码（文档 v1.19）

**变更类型**：破坏性变更（`result` 数值重映射，不保留兼容）。

**变更内容**

- 新增 §2.8 统一错误码表：`0` 成功、`-1` 请求无效、`-2` 参数错误、
  `-3` 资源不可用、`-4` 状态冲突、`-5` 控制权被占用、`-6` 执行失败、
  `-7` 存储空间不足、`-8` 安全联锁。
- 同一失败原因在所有命令中使用同一码：例如“服务/设备/链路不可用”统一 `-3`，
  “已在/不在某状态”统一 `-4`，“内部异常/超时/进程失败”统一 `-6`。
- 控制权拒绝统一 `-5`：`control/acquire|release` 的 occupied/not_holder、
  所有请求-应答类被拒绝的 `error_code=control_denied` 都改为 `-5`；
  缺少 `client_id`/`client_name` 改 `-1`。
- 车控前方障碍由 `-4` 改为 `-8`；舵机全部越界由 `-1` 改为 `-2`。
- 视频/手机音频/录像/车控/舵机/控制权设备端与 linux-demo 同步调整；
  Web 不再按码硬编码原因，统一展示 `cause`；Android 仅判断 `result != 0`，无需改解析。

**影响与兼容性**：客户端必须只判断 `result != 0`；旧版按具体数字判断原因的代码会显示错误文案。
App/Web/设备端需同版本部署。

**验证方式**：`python3 -m pytest -q walle-device/ros2_ws/src/net/net_mqtt_bridge/test`；
`cd walle-device/linux-demo && python3 -m unittest discover -s test -t .`；
实机抽查视频、手机音频、录像、车控、舵机的成功与失败回执，确认码值符合 §2.8。

### 2026-11-26：舵机位置设置改为 car/servo/set/request|response（文档 v1.18）

**变更类型**：破坏性变更（主题改名 + 恢复应答，不保留兼容）。

**变更内容**

- 主题：`car/servo/set/cmd` → 请求 `car/servo/set/request`、应答 `car/servo/set/response`。
- 设备端每条可解析请求发布一条应答（QoS1、不 retained），字段含 `request_id`、`result`、
  `cause`、`duration`、`applied`、`skipped` 与公共字段；`result`：0 成功、-1 命令无效、
  -2 命令错误、-3 舵机控制链路不可用、-5 控制权被其他客户端占用
  （带 `error_code=control_denied`）。`car/servo/status/query/response` 同步改用
  0/-1/-2/-3。
- 设备端 `REQUEST_TO_RESULT` 增加该配对，控制权拒绝自动回执到新应答主题；
  `car/servo/set/request` 为 4 层资源，复用既有的 `+/+/+/request` 订阅模式。
  原先仅用于 `.../cmd` 的 `+/+/+/cmd` 订阅模式随本次改版删除。
- Android（`TopicNames` / `MqttManager` / `MessageRouter` / `PlayerViewModel`）、Web 控制台
  与设备端（`net_mqtt_bridge` 的 `config.py` / `car_servo.py`）同步改名；App/Web 订阅
  `car/servo/set/response`，只在 `result != 0` 时提示。

**影响与兼容性**：App、Web、设备端必须同版本部署；旧主题 `car/servo/set/cmd`
不再有处理函数，设备端收到也只会忽略、不发布应答。

**验证方式**：设备端 `python3 -m pytest -q walle-device/ros2_ws/src/net/net_mqtt_bridge/test`；
App `./gradlew :app:testDebugUnitTest`；实机设置舵机应收到 `car/servo/set/response`
（`result=0`、`applied` 为实际下发项）；越界项应出现在 `skipped`；断开舵机控制链路应收到
`result=3`。

### 2026-11-26：车控命令改为 car/motor/set/request|response（文档 v1.17）

**变更类型**：破坏性变更（主题改名 + 恢复应答，不保留兼容）。

**变更内容**

- 主题：`car/motor/set/cmd` → 请求 `car/motor/set/request`、应答 `car/motor/set/response`。
- 设备端每条请求发布一条应答（QoS1、不 retained），字段含 `request_id`、`result`、
  `cause`、`cmd_vel` 与公共字段；`result`：0 成功、-1 命令无效、-2 命令错误、
  -3 底盘控制链路不可用、-4 前方有障碍物、-5 控制权被其他客户端占用
  （带 `error_code=control_denied`）。旧的正数结果码 1/2/3/4 随本次改版废弃。
- 设备端 `REQUEST_TO_RESULT` 增加该配对，控制权拒绝自动回执到新应答主题；
  `car/motor/set/request` 为 4 层资源，复用既有的 `+/+/+/request` 订阅模式。
- Android（`TopicNames` / `MqttManager` / `MessageRouter` / `PlayerViewModel`）、Web 控制台、
  `walle-agent` 与设备端（`net_mqtt_bridge` 的 `config.py` / `node.py` / `car_motor.py`）
  同步改名；App/Web 订阅 `car/motor/set/response`，只在 `result != 0` 时提示，
  避免正常运动回执（约 5 条/秒）刷状态条。

**影响与兼容性**：App、Web、agent、设备端必须同版本部署；旧主题 `car/motor/set/cmd`
不再有处理函数，设备端收到也只会忽略、不发布应答（500ms 死区仍会停车）。

**验证方式**：设备端 `python3 -m pytest -q walle-device/ros2_ws/src/net/net_mqtt_bridge/test`；
App `./gradlew :app:testDebugUnitTest`；实机按住方向键应收到连续的
`car/motor/set/response`（`result=0`、`cmd_vel` 为实际下发速度），前方 30cm 内前进应收到
`result=-4` 并立即停车。

### 2026-11-25：新增 agent 相关主题后，修改代码（文档 v1.16，保存不变）

**变更类型**：新增设备端订阅与 ACL 规则，修改agent报文相关代码。

- 设备端新增订阅 `walle/{t}/agent/+/online`，按 `agent_id` 维护 agent 在线表；
- 设备账号 ACL 增加 `walle/{t}/agent/+/online` 订阅；
- agent 相关的的主题段由 `agents` 调整为 `agent`；
- `chat/request|response` 使用新的json格式；
- `agent/{agent}/online` 使用新的json格式。
- 修改 agent 的主题订阅，覆盖现有与未来同层资源；
- 实现 agent 的版本，启动时从 walle-agent/VERSION 文件中读取；

### 2026-11-25：合并 walle-agent MQTT 协议（文档 v1.16）

**变更类型**：新增 agent 主题与报文约定；设备/App 现有主题与报文不变。

- 将 `walle-agent/docs/mqtt-protocol.md` 的对话与在线报文并入 §4.20、§4.21，agent 主题以本文为准。
- `chat/request` 明确 `client_id` / `client_name` / `request_id` / `device` / `text` 为必填；`chat/response` 统一使用 `agent_id`。
- `agent/{agent}/online` 载荷以 §4.21 示例为准（`agent_id` / `agent_name` / `version_code` / `version_name` / `ts`）。
- agent 订阅清单、presence、控制权 acquire/release 与 ACL 规则本次未变更/未补充，见 [agent_protocol_tasks.md](agent_protocol_tasks.md)。

**兼容性**：本次 v1.16 合并时 `walle-agent` 与 Web 仍使用 `agents/` 主题及 `agent` / `name` / `version` 等旧字段；主题已在 v1.17 统一为 `agent/`，字段对齐见任务清单。

### 2026-11-25：新增设备控制权租约 control/acquire|release（文档 v1.15）

**变更类型**：非破坏性增量（新增两个主题；runState 应答新增 `control_holder` 字段）。

**变更内容**

- 新增 `control/acquire/request → control/acquire/response`：获取/续租设备控制权；
- 新增 `control/release/request → control/release/response`：释放控制权；
- 设备默认 fail-open：没有 holder 时所有人都可以直接控制；有人 acquire 后才形成独占锁；
- 租约 TTL 900 秒；持有者任意有效请求都会续租；holder 的客户端 presence 离线后
  grace 10 秒自动释放；
- 非 holder 的控制类请求被拒绝：请求-应答类返回 `result=-4`、`cause`、
  `error_code="control_denied"` 与 `control_holder`；`.../cmd` 无应答，只记限频日志；
- `runState/query/response` 新增 `control_holder`（对象或 null）；
- 只读查询（baseState/runState/car servo status/record list）始终放行。

**影响与兼容性**：旧客户端不发送 control 请求时行为不变（fail-open）；旧设备忽略新主题，
新客户端 acquire 会超时，但可直接控制。不引入 `control_token`：`client_id` 是自报字段，
租约只是同租户可信客户端之间的协作锁，不是安全凭证。

**验证方式**：客户端 A acquire 后，客户端 B 的 `stream/start/request` 应返回
`error_code="control_denied"` 和 A 的 `control_holder`；A release 后 B 恢复正常。

### 2026-11-25：所有客户端请求补充 client_id / client_name（文档 v1.14）

**变更类型**：非破坏性增量（请求报文新增字段，设备端忽略未知字段）。

**变更内容**

- 所有 App/Web 发往设备的 `.../request` 与 `.../cmd` 报文统一增加 `client_id` 与 `client_name`；
  包括 `discovery/query/request`、`baseState`/`runState` 查询、视频/音频/录像动作和车控命令。
- `client_id` 使用客户端持久化的 MQTT client_id（Web 存 localStorage，App 存 SharedPreferences），
  与 presence 主题 `walle/{t}/client/{client_id}/online` 保持一致。
- `client_name` 与 presence 报文一致：Web 为 `Web`，App 为 `Android <机型>`。
- Android 在 `MqttManager.putRequestMeta()` 中统一注入；Web 在 `MqttWS.publish()` 中
  对 `/request` 与 `/cmd` 主题统一注入。
- 设备端暂不校验这两个字段，后续控制权租约直接复用。

**影响与兼容性**：旧设备忽略新字段；旧客户端不携带时新设备也能正常工作。

**验证方式**：抓取任意 `.../request` 或 `.../cmd` 报文，应包含 `client_id` 与 `client_name`；
设备端日志中的字段与客户端 presence 报文一致。

### 2026-11-25：新增客户端在线状态 client/{client_id}/online（文档 v1.13）

**变更类型**：非破坏性增量（新增租户级客户端在线主题）。

**变更内容**

- 新增主题 `walle/{t}/client/{client_id}/online`，retained + LWT，QoS1；
  App/Web 连接成功后发布 `online:true`，遗嘱与主动断开时发布 `online:false`。
- 报文包含 `client_id`、`client_name`、`version_code`、`version_name`、
  `online`、`timestamp`；设备端订阅 `walle/{t}/client/+/online` 并维护客户端在线表。
- `client_id` 必须是持久化标识（Web 存 localStorage，App 存 SharedPreferences），
  刷新页面/重启浏览器不能变化；topic 第 4 段与报文 `client_id` 必须一致。
- 该主题不参与 request/response 配对，没有 `request_id`；设备端只记录，不回复。

**影响与兼容性**：纯新增主题，不改变现有设备协议；旧客户端不发布 presence 时，
设备端只是看不到该客户端在线状态，不影响原有控制/查询。

**验证方式**：Web/App 连接后观察设备日志 `客户端上线`；关闭页面/断开 App 后观察
`客户端下线`；刷新页面应表现为短暂的 offline → online。

### 2026-11-24：彻底删除 uuid（文档 v1.12）

**变更类型**：破坏性变更（请求/应答字段删除 + 设备端校验移除，不保留兼容）。

**变更内容**

- 设备端（ROS2 `net_mqtt_bridge` 与 linux-demo）删除 `DEVICE_UUID`、`generate_device_uuid()`
  与全部 `verify_uuid`；请求只按主题第 4 段的 `device_id` 路由。
- 所有请求不再携带 `uuid`；`online`、`discovery/query/response`、`baseState`/`runState`、
  `video/*`、`phone/audio/*`、`record/*`、`alarm/report` 等报文不再包含 `uuid`（及 `uuid_source`）。
- Android App：`DeviceInfo` 删除 uuid 字段，列表键/主题段改为 `device_id`（回退 hostname）；
  `StatusSnapshot`、`LocalDb`、`AlarmStore`、`StatusViewModel`、`ControlViewModel`、
  `PlayerViewModel` 改为按 `device_id` 归属与缓存。
- Web 控制页与 `walle-agent` 删除 uuid 学习/门槛，直接按 `device_id` 发请求。
- `inject_request.py` 删除 `--uuid` 与 retained online 读取逻辑。

**影响与兼容性**：App、Web、设备端、walle-agent 必须同版本部署；旧客户端仍发送的 `uuid`
会被设备端当作未知字段忽略，但旧客户端若依赖应答里的 `uuid` 做归属判断，需要同步升级。

**验证方式**：`python3 -m pytest -q .../net_mqtt_bridge/test`；
`cd walle-device/linux-demo && python3 -m unittest discover -s test -t .`；
`./gradlew :app:testDebugUnitTest`；实机发送仅含 `{request_id}` 的 `baseState/query/request`
应正常收到应答。

### 2026-11-24：video/start 请求去掉 video_device / room_number（文档 v1.11）

**变更类型**：破坏性变更（请求字段删减 + 设备端行为调整，不保留兼容）。

**变更内容**

- `video/start/request` 只保留 `{uuid, request_id}`，删除 `video_device`、`room_number`。
- 摄像头由设备端固定使用默认设备：ROS2 用 `video_webrtc_bridge` 的 `camera_device`，
  linux-demo 用 `CAMERA_DEVICE`（未配置时取探测到的第一个 USB 采集节点）。
- 房间号由设备端自动生成 8 位随机字符（小写字母 + 数字），并在
  `video/start/response.room_number` 返回；客户端必须用它拼 WHEP 地址。
- 当前未推流：用默认摄像头开始推流；没有摄像头返回 `result=-1`。
- 当前已在推流：直接返回 `result=0`、`action=already_streaming` 和当前 `room_number`，
  不再停流重启，也不再支持通过本主题切换摄像头。
- Android（`MqttManager` / 控制页 / 全屏页）、Web 控制页、设备端
  （`video_webrtc_bridge` / `net_mqtt_bridge` / linux-demo）同步调整；
  旧客户端仍可发请求（多余字段被忽略），但会因使用自己生成的房间号而拉不到流。

**影响与兼容性**：App、Web、设备端必须同版本部署；旧版客户端无法正确播放新推流。

**验证方式**：`pytest -q walle-device/ros2_ws/src/video/video_webrtc_bridge/test`；
`python3 -m pytest -q walle-device/ros2_ws/src/net/net_mqtt_bridge/test`；
`cd walle-device/linux-demo && python3 -m unittest discover -s test -t .`；
实机发送 `{uuid, request_id}` 应收到含 8 位 `room_number` 的应答，
再次发送应返回 `already_streaming` 与同一 `room_number`。

### 2026-11-24：设备能力查询改为运行状态查询 runState（文档 v1.10）

**变更类型**：破坏性变更（主题改名 + 响应新增/调整，不保留兼容）。

**变更内容**

- 主题：`capability/query/request|response` → `runState/query/request|response`。
- 应答只保留运行状态分组：`video`（streaming / video_device / room_number / audio /
  pid / started_at / uptime_sec）、`phone_audio`（playing / room_number）、
  `recording`（enable / recording / filename）、`motor.moving`、`servo.moving`、
  `battery`（voltage_v / available）、`sonar`（distance_cm / available）。
- 删除 `cache`、`capability`、`capabilities`、`camera_count`、`cameras`、`audio`、
  `summary`、`probe`，并移除设备端能力后台探测/缓存线程；查询时现场采集运行状态。
- 电机/舵机状态由车控节点维护，录像/推流/手机音频复用各自已有状态。
- Android/Web 主题与路由同步改名；能力字段的解析暂不同步适配（摄像头/音频入口会为空）。

**影响与兼容性**：破坏性变更，App、Web、设备端必须同版本部署；旧 `capability/query/*`
不再响应，`runState/query/response` 不再包含摄像头/音频能力枚举。

**验证方式**：设备端 `pytest -q .../net_mqtt_bridge/test` 与 linux-demo unittest；
Web 发 `runState/query/request` 应收到仅含运行状态分组的应答。

### 2026-11-24：基础状态查询改名 baseState 并精简为六组（文档 v1.10）

**变更类型**：破坏性变更（主题改名 + 响应字段删减，不保留兼容）。

**变更内容**

- 主题：`state/query/request|response` → `baseState/query/request|response`。
- `baseState/query/response` 只保留 `system`、`cpu`、`memory`、`disk`、`network`、`usb`
  六个分组；移除 `recording`、`video`、`battery`、`sonar`。
- 录像状态由 `record/*` 主题维护；推流状态以 `video/start|stop/response` 为准；
  电池/声呐继续通过 `alarm/report` 上报。
- Android（`TopicNames` / `MessageRouter` / `StatusSnapshot` / 状态页 / 控制页）、
  Web 状态卡片与视频 UI、设备端 `build_status` 同步调整；App 控制页取消基于状态查询的推流接管。

**影响与兼容性**：App、Web、设备端必须同版本部署；旧版客户端查不到新主题。
状态页不再显示电池/声呐/推流/录像，相关入口改由运行状态查询与对应的 request/response 驱动。

**验证方式**：设备端 `pytest -q .../net_mqtt_bridge/test` 与
`python3 -m unittest discover -s walle-device/linux-demo/test -t walle-device/linux-demo`；
App `./gradlew :app:testDebugUnitTest`。

### 2026-11-24：主题后缀统一为 request/response/cmd/report（文档 v1.9）

**变更类型**：破坏性变更（所有请求/应答主题改名，不保留兼容）。

**变更内容**

- 请求-应答对：请求统一以 `.../request` 结尾、应答统一以 `.../response` 结尾
  （如 `state/query/request` → `state/query/response`）。
- 持续设置且不需要应答的主题统一以 `.../cmd` 结尾：
  `car/motor/set/cmd`、`car/servo/set/cmd`；取消原 `car/motor/result`、`car/servo/result` 应答。
- 主动上报：`alarm` → `alarm/report`；`online` 保持单独无后缀。
- 设备发现：请求 `walle/{t}/discovery/query/request`，应答
  `walle/{t}/device/{d}/discovery/query/response`。
- App（`TopicNames` / `MessageRouter` / `MqttManager`）、设备端（`ld_config` / `ld_protocol`、
  `net_mqtt_bridge/config.py` / `protocol.py` / `node.py` / `car_motor.py` / `car_servo.py`）、
  `walle-agent`、`web/car_ctl_by_mqtt.html` 与 Broker ACL 同步调整；旧主题直接删除。

**影响与兼容性**：App、设备端、walle-agent 与 Broker ACL 必须同版本部署；旧版无法互通。
`car/motor/set/cmd` 与 `car/servo/set/cmd` 不再有 MQTT 回执，执行结果只写设备端日志。

> 已废弃：`car/motor/set` 已于 v1.17、`car/servo/set` 已于 v1.18 改为 `request/response`
> 并恢复应答；本段仅作历史记录。

**验证方式**：`./gradlew :app:testDebugUnitTest`；
`python3 -m pytest -q walle-device/ros2_ws/src/net/net_mqtt_bridge/test`；
`cd walle-device/linux-demo && python3 -m unittest discover -s test -t .`。

### 2026-11-23：主题结构改为 walle/{tenant}/device/{device}（文档 v1.8）

**变更类型**：破坏性变更（所有主题改名，不保留兼容）。

**变更内容**

- 设备级主题统一为 `walle/{tenant}/device/{device}/{resource}`，移除原先的主题版本段 `v1`；
  第 3 段固定为字面量 `device`，第 4 段为稳定的 `device_id`。
- 设备发现保持租户级、没有 `device` 段：`walle/{tenant}/discovery/query`。
- App（`TopicNames`）、设备端（`ld_config` / `ld_protocol`、`net_mqtt_bridge/config.py` /
  `protocol.py`）与 `web/car_ctl_by_mqtt.html` 的主题拼装、订阅与解析同步调整；
  旧主题解析与兼容代码直接删除，不做灰度。
- Broker ACL 同步改为 `walle/+/device/${clientid}/#` 与 `walle/+/discovery/query`。

**影响与兼容性**：App、设备端与 Broker ACL 必须同版本部署；旧版 App/设备无法互通，
升级后旧主题上的 retained `online` 不会自动清理，由 App 侧归并消化。

**验证方式**：`./gradlew :app:testDebugUnitTest`；
`python3 -m pytest -q walle-device/ros2_ws/src/net/net_mqtt_bridge/test`；
`cd walle-device/linux-demo && python3 -m unittest discover -s test -t .`；
实机联调 App 能通过 retained `online` 看到设备并完成一次 `state/query`。

### 2026-11-19：car/motor/set 新增前方障碍联锁（V1.7）

**变更类型**：非破坏性增量（主题不变，新增结果码 `4`）。

**变更内容**

- 设备端收到 `car/motor/set` 后，若声呐 1s 内有有效读数且距离
  `≤ THRESH_OBSTACLE_CM`（默认 30cm），`forward` 非 0 的命令被拒绝：不更新运动状态、
  立即发布零速，返回 `result=4`、`cause=前方有障碍物，不可前进`。
- 后退、转向、横移不受影响；声呐无数据或读数过期时不启用联锁。
- 设备端 `car_motor.parse_drive` 新增 `forward_blocked` 参数，`node.py` 调用时注入
  当前声呐状态。

**影响与兼容性**：新增结果码 `4`，旧版 App/网页按 `result != 0` 展示 `cause` 即可；
主题与请求字段不变。

**验证方式**：`test_car_motor.py` 覆盖前进拦截、后退/转向/横移放行；
`test_alarm_sensors.py` 覆盖新鲜/过期声呐读数；实车前 30cm 内前进应返回 `result=4` 并停车。

> 已废弃（v1.19）：车控结果码已统一，前方障碍现为 `result=-8`；本段仅作历史记录。

### 2026-11-19：状态查询移除 alarms 分组（V1.7）

**变更类型**：破坏性变更（报文结构变化，主题不变）。

**变更内容**

- `state/result` 不再返回 `alarms.active` / `alarms.thresholds`。
- 当前告警状态统一以 `alarm` 主题的主动上报为准，由 App 告警中心维护。
- 设备端 ROS2 `net_mqtt_bridge` 与 `linux-demo` 两套实现同步移除。

**影响与兼容性**：仍有 `alarms` 解析逻辑的客户端会得到空列表，不会崩溃；
依赖状态接口获取告警列表的客户端需改为订阅 `alarm` 主题。

**验证方式**：设备端 `test_status_cache.py` 断言 `alarms` 不再出现；实车
`state/query` 回包确认无该字段。

### 2026-11-19：状态查询新增电池电压与前方声呐距离（V1.7）

**变更类型**：非破坏性增量（主题不变，`state/result` 新增两个可选分组）。

**变更内容**

- `state/result` 新增 `battery`：`{voltage_v, available, age_sec}`，数据来自
  `/ros_robot_controller/battery`（mV→V）。
- 新增 `sonar`：`{distance_cm, available, age_sec}`，数据来自
  `/sonar_controller/get_distance`（mm→cm）；读数 -1 不更新，超时后 `available=false`。
- 两组均为可选：无对应传感器或旧设备端不返回，App 按 null 处理并显示「—」。
- App `StatusSnapshot` 新增 `Battery`/`Sonar` 模型，状态页新增「电池 / 前方声呐」卡片。

**影响与兼容性**：旧版 App 忽略新分组即可；旧版设备端不返回时新版 App 正常降级，不崩溃。

**验证方式**：设备端 `test_status_cache.py` 校验空数据占位、
`test_alarm_sensors.py` 校验电压/距离与过期标记；App `StatusSnapshotTest` 覆盖完整、
缺失与异常值解析。

### 2026-11-19：新增电池低压与前方障碍告警（V1.6）

**变更类型**：非破坏性增量（主题不变，`alarm` 新增两个 `alarm_type`）。

**变更内容**

- `alarm` 新增 `metric=battery` / `alarm_type=battery_low`、`metric=obstacle` /
  `alarm_type=obstacle_near`，复用 `walle/{t}/device/{d}/alarm`，不新增主题。
- 明确低阈值回滞方向：≤阈值告警、回到恢复线以上才 `recover`；新增
  `THRESH_BATTERY_V` / `RECOVER_BATTERY_V` / `THRESH_BATTERY_CRITICAL_V`、
  `THRESH_OBSTACLE_CM` / `RECOVER_OBSTACLE_CM` 环境变量。
- 电池取 3 个采样均值、连续 2 次判定；障碍边沿触发并限频 3s；车静止时障碍降为 `info`。
- **设备端告警只发布不落盘**（不建本地 SQLite），告警历史由服务端数据库保存。

**影响与兼容性**：旧版 App 收到未知 `metric`/`alarm_type` 时应归入通用告警展示、不崩溃；
主题未变，不影响现有 App/设备。

**验证方式**：App 端无需改动（`AlarmAdapter`/`AlarmStore` 按通用字段展示，未知
`metric` 不会崩溃）；设备端 `net_mqtt_bridge/test/test_alarm_sensors.py` 覆盖低压/障碍
阈值与回滞，`test_protocol.py` 校验主题不变；实车触发低压与遮挡声呐，确认
`alarm → recover` 各一条。

### 2026-11-16：新增录像点播 record/play（V1.5）

**变更类型**：非破坏性增量（新增 2 组主题）。

**变更内容**

- 新增 2 组请求主题（V1.5）：
  - `record/play/start/request` → `record/play/start/result`：ffmpeg 推 fMP4 → SRS，App 拉流；
  - `record/play/stop/request` → `record/play/stop/result`：停止录像点播推流。
- 支持 `offset_sec` 从指定时间点开始播放（进度条拖动）。

### 2026-11-16：精简两个result报文 state|capability/result（V1.4）

**变更类型**：破坏性变更（协议 v1 主题不变，精简两个result报文）。

**变更内容**

- 精简 state/result 报文，recording 增加功能开关
- 精简 capability/result，cameras 去掉 formats 并新增 recording

**影响与兼容性**：变更主题，APP和web都需要更新；

**验证方式**：刷新设备详情和刷新设备能力验证

### 2026-11-15：新增录像 record/start|stop|list|delete（V1.3）

**变更类型**：非破坏性增量（协议 v1 主题不变，新增 4 组录像主题）。

**变更内容**

- 新增 4 组请求-应答主题（均为 V1.3）：
  - `record/start/request` → `record/start/result`：开始本地录像；
  - `record/stop/request` → `record/stop/result`：停止当前录像；
  - `record/list/query` → `record/list/result`：查询录像列表与播放地址；
  - `record/delete/request` → `record/delete/result`：删除指定录像文件。
- 设备端新增 ROS 包 `record_video`，`record_video_node` 订阅 `/image_raw`，
  通过 ffmpeg 编码 H.264 + fMP4 封装落盘（`+frag_keyframe+empty_moov+default_base_moof`），
  支持自动分片、FIFO 空间回收、断电可恢复。
- 录像文件命名 `rec_YYYYMMDD_HHMMSS.fmp4`，存储于 `walle-device/data/recordings/`，
  存储空间上限与单文件时长由 `walle_camera.conf` 的 `record` 块配置。
- 设备内置 HTTP 静态服务（默认端口 8088）提供 `GET /recordings/<file>.fmp4` 播放，
  文件名白名单防路径穿越。
- `walle_camera.conf` 升级到 `schema_version=2`，`camera` 与 `record` 分块存储，
  旧 v1 扁平格式由 `camera_conf.py` 自动兼容。
- `auto_gen_camera_conf.sh` 在摄像头配置后追加录像参数交互（目录名、存储上限、单文件时长）。

**影响与兼容性**：新增主题，不影响现有 App/设备；旧版 App 不订阅录像结果即可。
`walle_camera.conf` v1 格式自动兼容，无需手动迁移。

**验证方式**：设备端 `record_video` 包编译通过；`net_mqtt_bridge/test/test_protocol.py`
校验主题配对与订阅清单；MQTT 客户端发布 `record/list/query` 应返回录像列表。

### 2026-11-13：新增 car/servo/set 舵机位置设置（V1.2）

**变更类型**：非破坏性增量（协议 v1 主题不变，新增设置资源）。

**变更内容**

- 新增请求 `walle/{t}/device/{d}/car/servo/set` 与应答 `walle/{t}/device/{d}/car/servo/result`。
- `servos[]` 按绝对位置设置，支持单条消息设置多个舵机；`duration` 默认 1.0 秒。
- 越界规则：全部越界 → `result=1`；部分越界 → 只执行合法项，`result=0`，
  越界项放入 `skipped[]`。
- 设备端一次发布 `SetPWMServoState`，不拆成多次；`set_state` 无订阅者时返回 `result=3`。
- App 端新增 `TopicNames.carServoSet/carServoResult`、`MqttManager.setCarServo(s)`、
  `MessageRouter` 的 `car/servo/result` 路由与去重。
- App 订阅主题由 9 条增加到 10 条；设备端订阅由 9 条增加到 10 条。

**影响与兼容性**：新增主题，不影响现有 App/设备；旧版 App 不订阅该结果即可。

**验证方式**：设备端 `test_car_servo.py` 覆盖绝对位置、越界跳过和参数错误；
`test_protocol.py` 校验主题配对与订阅；App `TopicNamesTest` / `MessageRouterTest`
覆盖新增主题与路由。

### 2026-11-13：新增 car/servo/status/query 舵机状态查询（V1.2）

**变更类型**：非破坏性增量（协议 v1 主题不变，新增查询资源）。

**变更内容**

- 新增请求 `walle/{t}/device/{d}/car/servo/status/query` 与应答
  `walle/{t}/device/{d}/car/servo/status/result`。
- 请求可选 `ids`（默认 `[1,2]`）与 `include_offset`（默认 `false`）。
- 设备端调用 `/ros_robot_controller/pwm_servo/get_state`，返回每个舵机的
  `id/position/offset/available/error`；任意舵机读取失败时 `result=3`，但保留部分结果。
- 设备端使用独立的舵机查询线程池，避免阻塞 state/capability 查询与推流动作。
- App 端新增 `TopicNames.carServoStatusQuery/carServoStatusResult`、
  `MqttManager.requestCarServoStatus`、`MessageRouter` 的 `car/servo/status/result` 路由与去重。
- App 订阅主题由 8 条增加到 9 条；设备端订阅由 8 条增加到 9 条。

**影响与兼容性**：新增主题，不影响现有 App/设备；旧版 App 不订阅该结果即可。

**验证方式**：设备端 `test_car_servo.py` 覆盖 ids/include_offset 校验，
`test_protocol.py` 校验主题配对与订阅；App `TopicNamesTest` / `MessageRouterTest`
覆盖新增主题与路由。

### 2026-11-13：新增 car/motor/set 电机持续控制（V1.2）

**变更类型**：非破坏性增量（协议 v1 主题不变，新增资源与 `…/set` 后缀）。

**变更内容**

- 新增请求 `walle/{t}/device/{d}/car/motor/set` 与应答 `walle/{t}/device/{d}/car/motor/result`。
- 把 `…/set` 正式登记为「持续控制命令」后缀：最新一条覆盖旧状态，不排队。
- `move` 只允许一个方向非 0，取值 `0` 或 `[0.5,1.0]` m/s；`rotate.left/right` 为布尔且互斥。
- 设备端保存运动状态，以 10Hz 重发 `/cmd_vel`；500ms 没有有效命令、收到非法命令、
  收到停止命令或进程退出时清状态并立即发布零速。
- 同时旋转时线速度上限压到 `0.653 m/s`（麦轮轮速预算），超出按比例缩放。
- App 端新增 `TopicNames.carMotorSet/carMotorResult`、`MqttManager.sendCarMotorDrive/
  stopCarMotorDrive`、`MessageRouter` 的 `car/motor/result` 路由与去重。

**影响与兼容性**：旧版 App/设备不受影响；新版 App 需订阅 `car/motor/result`。
`set` 是新后缀，不会与现有主题冲突。

**验证方式**：设备端 `net_mqtt_bridge/test/test_car_motor.py` 覆盖取值/互斥/轮速缩放；
`test_protocol.py` 校验订阅清单与请求-应答配对；App `TopicNamesTest` / `MessageRouterTest`
覆盖新增主题与路由。

### 2026-11-10～11-12：online / discovery/result 统一为最小身份报文（V1.1）

**提交**：`104ed22`（设备端，`feat(linux-demo): 最小化 retained online 报文`）、
`b8957d5`（App，`refactor(mqtt): 适配设备最小身份报文`）。

**变更类型**：非破坏性收敛（协议 v1 主题不变，身份字段只减不增）。

**变更内容**

- 设备端：`online`（retained + LWT）与 `discovery/result` 统一只发最小身份
  （`device_id`/`uuid`/`version_code`/`version_name`/`os_description`/`device_type`/
  `model`/`hostname`/`ip`/`uptime_seconds`/`timestamp`/`datetime`）。
- `mac`/`serial`/`os`/`cpu`/内存/磁盘/`boot_time` 等不再随身份报文下发；系统、CPU、
  磁盘等明细统一走 `state/query`（§4.3）。
- App：`DeviceInfo` 只解析上述字段；`DeviceRegistry` 归并改为 `device_id` → `uuid` →
  `hostname`（去掉 MAC）；设备列表只展示在线状态、IP、系统描述、型号与运行时长。

**影响与兼容性**：旧版 App 仍可运行；新版 App 不再依赖 MAC，`device_id` 漂移时退化为
按 `hostname` 归并；设备详情仍以 `state/query` 为准。

**验证方式**：设备端 `linux-demo/test/test_online_identity.py` 校验最小字段；
App `DeviceInfoTest` / `DeviceRegistryTest` 共 21 个用例通过
（`./gradlew :app:testDebugUnitTest`）。

### 2026-11-10：state/query 改为复用监控采样快照

**变更类型**：非破坏性增量（协议 v1 字段只增不改）。

**原因**：`state/query` 原先在请求线程内阻塞 1s 采样，状态页刷新慢且占用请求线程。

**变更内容**：`state/result` 新增顶层 `sample_age_sec`；CPU/网络数据复用监控循环
最近一次采样，USB 列表复用 `check_usb` 的维护结果，查询不再阻塞采样。

**影响与兼容性**：App 可忽略新增字段；CPU/网络数据最多滞后一个采样周期（默认 5s）。

**验证方式**：`test/test_status_cache.py` 覆盖快照复用、无快照时兜底采样与查询 < 100ms。

### 2026-11-10：capability/query 改为后台缓存探测 （V1.1）

**变更类型**：非破坏性增量（协议 v1 字段只增不改）。

**原因**：`capability/query` 原先每次请求都在线程内跑 `v4l2-ctl` / `pactl`，
最坏数秒，阻塞请求线程并拖慢其它请求。

**变更内容**：`capability/result` 新增 `cache {source, age_sec, ttl_sec}` 与
`probe.pending`；设备端由后台线程启动即探测、按 `CAPABILITY_CACHE_TTL`
（默认 120s）刷新，查询只读缓存；首次未就绪时最多等待 `CAPABILITY_FIRST_WAIT`
（默认 3s），超时或 `CAPABILITY_CACHE_ONLY=True` 时返回 pending 占位。

**影响与兼容性**

- App 可忽略新增字段；若识别 `probe.pending=true`，应提示稍后重试而不是判定「无摄像头」。
- 缓存期内（默认 120s）热插拔摄像头/声卡不会立即出现在 capability 结果中。

**验证方式**

- `test/test_capability_cache.py`：capability 查询只读缓存、cache-only 立即返回
  pending、后台探测结果写入缓存、探测失败保留旧缓存。

### 2026-11-08 20:00（Asia/Shanghai, CST）：移除远程命令 cmd/exec → cmd/result （V1.1）

**变更类型**：破坏性删除（协议 v1 内直接移除，不保留兼容层）。

**原因**

- 该能力允许在设备上通过 `/bin/bash` 执行任意命令，属于最高危的远程命令执行面。
- 设备 `uuid` 会通过 `discovery/result` 与 retained `online` 报文下发，不能作为鉴权凭证；
  只要拿到租户 MQTT 凭据，就能完成「发现设备 → 取 uuid → 执行任意命令」的攻击链。
- 原实现使用前缀白名单 + `shell=True`，无法阻止 `;`、`&&`、`$()` 等注入；
  且 `ENABLE_CMD_EXEC` 默认为开启，不符合上线安全要求。

**变更内容**

- 删除请求主题 `walle/{t}/device/{d}/cmd/exec` 与应答主题 `walle/{t}/device/{d}/cmd/result`。
- 设备端不再订阅 `cmd/exec`、不再处理远程命令、不再发布 `cmd/result`；
  `capability/result` 的 `capabilities[]` 不再包含 `remote_cmd`。
- 测试页 `test/car_ctl_by_mqtt.html` 删除「远程指令」卡片及相关 MQTT 订阅/发送逻辑。
- 协议命名约定中的 `…/exec` 后缀语义一并删除。

**影响与兼容性**

- App 端必须同步移除远程命令入口；旧版 App 发送 `cmd/exec` 后不会收到任何应答
  （设备端未订阅该主题，也不会再发布 `cmd/result`）。
- 如确有设备运维需求，改用受控 SSH / 串口 / 带外通道，并为该通道增加独立认证与审计；
  不要恢复任意 shell 执行能力。
- 协议版本仍为 v1；本次属于破坏性变更，发布说明中必须标注 App 与固件需同步升级。

**验证方式**

- 在固件与测试页中执行 `grep -R "cmd/exec\|cmd/result\|remote_cmd"`，应无残留；
  协议文档仅在本文「§8 变更记录」中保留历史说明，不再作为有效契约。
- 设备启动日志中的 MQTT 订阅清单不再包含 `cmd/exec`。
- 使用 MQTT 客户端向 `walle/{t}/device/{d}/cmd/exec` 发布消息，设备应无任何响应。
- 打开 `test/car_ctl_by_mqtt.html`，页面不再出现「远程指令」卡片。


