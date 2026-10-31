# MQTT 协议约定（协议 v2 · 多租户主题）

> 本文是 App（Android）与设备端（`linux/rpi_monitor.py`）之间 MQTT 报文契约的**唯一权威文档**。
> 改主题、改字段、改配对逻辑之前先读完本文，并按文末「修改协议的流程」同步两端与测试。
>
> - 协议版本：**v2**（多租户主题）。v1 旧主题已彻底删除，无兼容层。
> - 传输：MQTT 3.1.1，报文一律 **JSON（UTF-8）**，**QoS 1**，Keep Alive 60s。
> - App 端实现：`app/src/main/java/www/walle/ai/net/`（TopicNames / MessageRouter / MqttManager）
> - 设备端实现：`linux/rpi_monitor.py`
> - 契约交叉验证：`app` 单测（TopicNamesTest / MessageRouterTest / …）+ `linux/selftest_topics.py`

---

## 1. 总览

```
┌──────────────┐   walle/{tenant}/{device}/{resource}   ┌──────────────┐
│  Android App │ ◄────────── MQTT Broker ─────────────► │ Linux 设备端  │
└──────────────┘         （EMQX / Mosquitto）           └──────────────┘
```

主题结构：

```
walle/{tenant}/{device}/{resource}
```

- `tenant`：租户标识 = **App 登录用的 MQTT 用户名**（经清洗）。同一账号 = 同一租户。
- `device`：**稳定**的设备标识 `device_id`（设备端用 MAC + CPU + 内存算 md5 取前 16 位，重启不变）。
- `resource`：按语义命名（见 §2.3），可能含 `/`（如 `video/start/result`）。

特例：设备发现是**租户级**广播，只有 3 段、没有 device 段：`walle/{tenant}/discovery/query`。

### 主题总表

| # | 协议 | App → 设备（请求） | 设备 → App（应答/上报） | 类型 |
|---|---|---|---|---|
| 1 | 上线/下线 | — | `walle/{t}/{d}/online` | 主动上报（retained + LWT） |
| 2 | 设备发现 | `walle/{t}/discovery/query` | `walle/{t}/{d}/discovery/result` | 请求-应答（按需、限频） |
| 3 | 状态查询 | `walle/{t}/{d}/state/query` | `walle/{t}/{d}/state/result` | 请求-应答 |
| 4 | 远程命令 | `walle/{t}/{d}/cmd/exec` | `walle/{t}/{d}/cmd/result` | 请求-应答 |
| 5 | 设备能力 | `walle/{t}/{d}/capability/query` | `walle/{t}/{d}/capability/result` | 请求-应答 |
| 6 | 开始推流 | `walle/{t}/{d}/video/start/request` | `walle/{t}/{d}/video/start/result` | 请求-应答 |
| 7 | 停止推流 | `walle/{t}/{d}/video/stop/request` | `walle/{t}/{d}/video/stop/result` | 请求-应答 |
| 8 | 告警 | — | `walle/{t}/{d}/alarm` | 主动上报 |
| 9 | 手机音频上行·开 | `walle/{t}/{d}/phone/audio/send` | `walle/{t}/{d}/phone/audio/send/result` | 单向动作（App 不订阅应答） |
| 10 | 手机音频上行·关 | `walle/{t}/{d}/phone/audio/stop` | `walle/{t}/{d}/phone/audio/stop/result` | 单向动作（App 不订阅应答） |

> `{t}` = tenant，`{d}` = device_id。
> 9/10 只承载「开/关」控制，音频本身走独立 RTMP（§4.9）。

---

## 2. 设计原则（为什么这么设计）

修改协议前务必理解这些约束——它们都是踩过坑后定下来的。

### 2.1 主题即权限边界

把租户编码进主题路径，Broker 就能直接用 `walle/${username}/#` 做 ACL：
用户 A 的 App 和设备只能看到 `walle/A/#`，天然隔离多用户，无需服务端代码。
**因此主题段里绝不允许出现 `+ # /` 等通配/分隔符**（见 §3.3 清洗规则），否则 ACL 会失效。
详细方案与 EMQX ACL 配置步骤见 [s3-multi-tenant-topics.md](dev_record/s3-multi-tenant-topics.md)。

### 2.2 设备段必须稳定，禁止用 uuid

`uuid` 由设备端按 `uuid5(MAC|IP|启动时间)` 生成，**每次重启都会变**。
如果主题用 uuid，设备重启后 App 订阅的旧主题全部失效，还会产生「幽灵设备卡片」。
所以主题里的设备段只用 `device_id`（硬件指纹，重启不变）；`uuid` 降级为**报文字段**，
用于请求校验（§3.4）和设备归并。App 侧归并逻辑见 `DeviceRegistry`。

### 2.3 资源按语义命名，应答一律 `/result`

| 后缀 | 语义 | 例子 |
|---|---|---|
| `…/query` | 查询（读，无副作用） | `state/query` |
| `…/exec` | 命令执行（写，有副作用） | `cmd/exec` |
| `…/request` | 动作请求（生命周期管理） | `video/start/request` |
| `…/result` | 应答，**只在收到对应请求后发布**，绝不主动上报 | `state/result` |
| `…/send` | 手机侧发起「开始上行」的新后缀（App 是动作发起方，不是设备推流） | `phone/audio/send` |
| `…/stop` | 手机侧发起「停止上行」的新后缀（无 room 也可发，用于退出兜底） | `phone/audio/stop` |
| 无后缀 | 主动上报，只有 `online` 和 `alarm` 两个 | — |

这套约定由 `linux/selftest_topics.py` 第 4 节强制检查：每个 `REQ_*` 必须在
`REQUEST_TO_RESULT` 里登记对应的 `RES_*`，不允许存在「没人请求的 /result 主题」。

### 2.4 请求-应答严格配对：request_id + uuid 双校验

- `request_id`：App 生成的 8 位十六进制随机串（`UUID.randomUUID()` 前 8 位）。
  设备端在应答里**原样回显**，App 用它把结果和「哪一次请求」对上；迟到的旧结果直接丢弃。
- `uuid`：除 `discovery/query` 外，**所有请求必须带设备当前 uuid**。设备端校验不匹配就
  **静默忽略**（不执行、不回复），防止同一租户内多台设备串控（重启换 uuid 后旧指令自然失效）。

> 历史教训：设备端曾重建请求字典时漏掉 `request_id`，应答回显成空串，App 严格配对后
> 丢弃结果，UI 表现为「一直转圈」。`selftest_topics.py` 第 7 节是该 bug 的回归测试。

### 2.5 QoS1 重复投递：一次性事件去重，幂等事件不去重

QoS 1 允许 broker 重复投递（重连补发、未及时 PUBACK）。App 侧 `MessageRouter` 用
「键排序后的 JSON 签名 + 2 秒窗口」去重，但只针对**一次性事件**：
`alarm`、`cmd/result`、`capability/result`、`video/start/result`、`video/stop/result`
（重复处理会出事，例如推流结果重复 → 播放器初始化两次）。
`online` 与 `state/result` 是幂等的（同一份状态重复应用无害），**不去重**。

### 2.6 retained 只给 online，发现靠回放而非轮询（N7）

设备列表 = 当前在线设备，直接来自 retained `online` 报文：App 一连上就收到回放，
**不需要广播轮询**。`discovery/query` 降级为按需手段（手动刷新、连上后一台设备都没
回放出来时自动兜底一次）。设备端对 discovery 应答做了**限频（2 秒/台）+ 随机抖动
（≤0.8 秒）**，避免多台手机同时扫描时回包风暴。背景见 [n7-discovery-scaling.md](dev_record/n7-discovery-scaling.md)。

### 2.7 不订通配子树

MQTT 3.1.1 没有 no-local 语义，订 `walle/{t}/#` 会把自己发的请求再收一遍，浪费带宽、
干扰排查。所以两端都只订**精确的响应/请求主题清单**：

- App 订 8 条：`walle/{t}/+` 下的 `online`、`alarm`、6 条 `…/result`（见 `TopicNames.responseSubscriptions`）
- 设备端订 6 条：5 条设备级请求 + 1 条租户级 `discovery/query`（见 `rpi_monitor.subscriptions()`）

---

## 3. 公共约定

### 3.1 公共字段

绝大多数设备 → App 报文都带这些字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `device_id` | string | 稳定设备标识（md5 前 16 位）。设备端可省略，App 用主题里的设备段兜底注入，区别不同设备，重启不变。 |
| `uuid` | string | 本次启动的 uuid（重启会变），device_id相同但uuid不同说明设备重启了。 |
| `hostname` | string | 主机名 |
| `ip` | string | 局域网 IP |
| `timestamp` | number | Unix 秒 |
| `datetime` | string | `YYYY-MM-DD HH:MM:SS` 本地时间 |
| `request_id` | string | **仅应答报文**：原样回显请求的 request_id，标识请求-应答对，每次请求都不同。 |

### 3.2 request_id 配对规则

1. App 每发一个请求生成新 `request_id`（8 位 hex），并把它放进请求报文。
2. 设备端应答必须原样回显；没收到匹配 `request_id` 的应答前，UI 处于等待态。
3. 旧请求迟到应答会被丢弃（App 只认「当前这次」的 rid）。

### 3.3 主题段清洗（sanitize）

两端**必须完全一致**，否则主题对不上。规则（`TopicNames.sanitize` / `rpi_monitor.sanitize_segment`）：

- 只保留 `[A-Za-z0-9_-]`；`.` `:` `@` 空格 → `_`；其余字符（含中文、通配符）**丢弃**；
- 清洗后为空 → `unknown`；超过 64 字符 → 前 40 位 + 短哈希（保证同输入得同输出）。

对照测试用例（两端同步维护）：

| 输入 | 输出 |
|---|---|
| `home` | `home` |
| `user@example.com` | `user_example_com` |
| `RPi 5` | `RPi_5` |
| `x/y+z#` | `xyz` |
| `中文租户` / 空 / null | `unknown` |

### 3.4 uuid 校验（设备端 `verify_uuid`）

- `discovery/query`：**免校验**（App 此时还不知道任何 uuid）。
- 其余所有请求：报文必须是 JSON 对象且 `uuid` 与设备当前值一致（大小写不敏感），
  否则**静默忽略**（只记日志，不回复——不回复是刻意的，避免泄露设备存在性）。

---

## 4. 各协议详解

### 4.1 online —— 上线/下线（主动上报）

**作用**：App 设备列表的唯一数据源。上线 retained 报文含完整身份，订阅即回放。

- 主题：`walle/{t}/{d}/online`，**retained**
- 上线：设备连上 broker 后立即发布 `online: true` + 完整身份（同 §4.2 的身份字段全集）
- 下线：broker 通过 **LWT 遗嘱**发布 `online: false`（设备端注册 will，异常掉线也能通知）

关键字段（其余同 `build_device_info`）：

| 字段 | 说明 |
|---|---|
| `online` | bool，true=上线 / false=下线 |
| `device_id` / `uuid` / `hostname` | 归并与展示用标识 |
| `device_type` / `vendor` / `model` / `serial` | 设备类型、厂商、型号（如 Raspberry Pi 5）、序列号 |
| `ip` / `mac` | 网络身份 |
| `os{name,kernel,kernel_build,arch,python}` | 系统与内核版本 |
| `cpu{model,cores_logical,cores_physical,frequency_mhz,temperature_c}` | CPU 概览 |
| `memory_total_bytes/human`、`disk_total_bytes/human` | 总量 |
| `uptime_seconds` / `uptime_human` / `boot_time` | 运行时长 |
| `uuid_source{mac,ip,note}` | uuid 生成依据（提示重启会变，用 discovery 拿最新） |

**注意**：`online: false` 的遗嘱只由 broker 代发；设备端正常关机也应主动发一次。
App 侧重复 online 不去重（幂等）。

### 4.2 设备发现 discovery/query → discovery/result

**作用**：按需拉取设备身份详情。日常列表不靠它（靠 retained online），仅用于手动
「重新扫描」和「连上后零回放」的自动兜底。

- 请求：`walle/{t}/discovery/query`（租户级，3 段主题）
- 应答：`walle/{t}/{d}/discovery/result`

请求字段：

| 字段 | 必填 | 说明 |
|---|---|---|
| `request_id` | 是 | 配对用，应答原样回显 |

应答 = §4.1 的身份字段全集 + `request_id`。**不带 uuid 校验**（免校验主题）。

设备端保护机制（防止广播风暴）：

| 机制 | 参数 | 说明 |
|---|---|---|
| 限频 | `DISCOVERY_MIN_INTERVAL_SEC = 2.0` | 每台设备 2 秒内只应答一次，重复查询丢弃 |
| 抖动 | `DISCOVERY_JITTER_SEC = 0.8` | 应答前随机延迟 0~0.8s，N 台设备错开回包 |

> 坑位提醒：这个主题只有 3 段，解析时不能切成 `device="discovery", resource="query"`——
> 两端（`TopicNames.parse`、`rpi_monitor._handle_request`）都对它做了特判，别改没。

### 4.3 状态查询 state/query → state/result

**作用**：拉取设备实时运行状态（一次约 1 秒的采样），驱动状态页所有卡片。

- 请求：`walle/{t}/{d}/state/query`
- 应答：`walle/{t}/{d}/state/result`

请求字段：

| 字段 | 必填 | 说明 |
|---|---|---|
| `uuid` | 是 | 设备当前 uuid，不匹配静默忽略 |
| `request_id` | 是 | 配对用 |

应答字段（嵌套结构）：

| 分组 | 字段 |
|---|---|
| 顶层 | `device_id`、`uuid`、`hostname`、`ip`、`timestamp`、`datetime`、`uptime_seconds`、`request_id` |
| `system` | `os`、`kernel`、`os_version`、`python`、`machine`、`model` |
| `cpu` | `percent`（总）、`per_core_percent[]`（各核）、`count_logical/physical`、`load_avg_1m/5m/15m`、`frequency_mhz{current,min,max}`、`temperature_c`、`sample_interval_sec` |
| `memory` | `percent`、`total/used/available/free_bytes` + `*_human`、`swap{percent,total_bytes,used_bytes,total_human,used_human}`（无 swap 时为 null） |
| `disk` | `mounts[]`：每挂载点容量/使用率/文件系统类型 |
| `network` | `sample_interval_sec`、`total_rx/tx_bytes_per_sec`、`total_rx/tx_rate`（可读）、`interfaces[]`（每网卡 rx/tx 速率） |
| `usb` | `count`、`devices[]` |
| `video` | 推流状态（任务 1）：`streaming`、`room_number`、`video_device`、`audio`（推流时的音频信息，无音频/未推流为 null）、`pid`、`started_at`、`uptime_sec`；未推流时 `streaming=false` 且其余字段全为 null。数据来自推流时记录的 `video_meta`，ffmpeg 异常退出后看护线程清理，下一次查询即 `streaming=false` |

**video 分组的用途**：App 控制页进入时查一次状态，若 `video.streaming=true` 且
`room_number`/`video_device` 齐全，直接用该房间号拼 WHEP 地址拉流，**自动接管设备正在
进行的推流**，无需用户再点「开始推流」。App 侧只认自己这次查询的 `request_id`
（`ControlViewModel.takeoverRid`），状态页的周期查询不会触发接管。

解析入口（App 唯一）：`model/StatusSnapshot.java`。契约测试：`StatusSnapshotTest`
（缺分组归空、异常数值归一为 null、网卡按名称排序等）。**状态是实时查询数据，不落盘**
（N11 曾落盘又 revert，决策见 git 历史）。

### 4.4 远程命令 cmd/exec → cmd/result

**作用**：在设备上以 shell（`/bin/bash`）执行任意命令并回传结果。

- 请求：`walle/{t}/{d}/cmd/exec`
- 应答：`walle/{t}/{d}/cmd/result`

请求字段：

| 字段 | 必填 | 说明 |
|---|---|---|
| `uuid` | 是 | 不匹配静默忽略（不执行、不回复） |
| `cmd` | 是 | 要执行的 shell 命令字符串 |
| `request_id` | 建议 | 缺省时设备端自生成（App 将无法配对，只用于展示） |
| `timeout` | 否 | 秒，1–300，默认 30，越界自动钳制 |

应答字段：

| 字段 | 说明 |
|---|---|
| `request_id` | 原样回显 |
| `cmd` | 回显命令原文 |
| `status` | `success`（exit 0）/ `failed`（exit 非 0）/ `timeout`（超时被杀）/ `denied`（白名单拒绝）/ `error`（执行异常或参数非法） |
| `exit_code` | 进程退出码（timeout 时为 null） |
| `stdout` / `stderr` | 输出，超过 64KB 截断（尾部带截断提示） |
| `duration_ms` / `timeout_sec` | 实际耗时 / 生效超时 |
| `device_id`、`uuid`、`hostname`、`timestamp`、`datetime` | 公共字段 |

安全开关（设备端环境变量 / 常量）：`ENABLE_CMD_EXEC`（总开关）、`CMD_ALLOWLIST`
（前缀白名单，为空=允许任意命令）。**这是高危能力**，Broker ACL 必须保证租户隔离。

### 4.5 设备能力 capability/query → capability/result

**作用**：枚举设备端外设能力——摄像头（v4l2）、麦克风/音响（PulseAudio）等，供控制页
展示与选择。历史上只查摄像头（`camera/capability/*`，已删除），任务 4 起升级为通用能力查询。

- 请求：`walle/{t}/{d}/capability/query`，字段 `{uuid, request_id}`
- 应答：`walle/{t}/{d}/capability/result`

应答字段：

| 字段 | 说明 |
|---|---|
| `capability` | 固定 `"device"` |
| `capabilities[]` | 能力标签，按探测结果条件出现：`system_status`、`remote_cmd` 恒有；有摄像头加 `camera_video_capture`；`VIDEO_ENABLE` 开启加 `rtmp_push`；音频可用分别加 `audio_capture` / `audio_playback` |
| `camera_count` / `cameras[]` | 摄像头列表，每项含 `node`（如 `/dev/video0`）、格式/分辨率/帧率明细、`format_count`、`resolution_count`；无 `node` 的条目 App 侧会过滤 |
| `audio.capture` | 麦克风：`{available, device, sources[], reason}`；`device` 为默认录音源（通常 `default`），不可用时 `reason` 给出原因 |
| `audio.playback` | 音响：`{available, device, sinks[], volume_percent, muted, reason}`；`volume_percent` 未知为 null（App 归一为 -1） |
| `summary` | 聚合统计：`total_cameras`、`total_formats`、`total_resolutions`、`audio_sources`、`audio_sinks`、`probe_cost_ms` |
| `probe` | `{tool: "v4l2-ctl + pactl", error, audio_error}`：`error` / `audio_error` 非 null 分别表示视频 / 音频探测失败（含 CAM_ENABLE=False 的提示） |
| `request_id` 及公共字段 | — |

音频探测（设备端 `probe_audio_capture` / `probe_audio_playback`）：优先
`pactl list short sources/sinks`（录音侧排除 `*.monitor` 回环源）；pactl 缺失时录音侧用
ffmpeg 试读 0.2s 兜底；播放侧另取默认 sink、音量与静音状态。**任何一步失败只降级**
（`available:false` + `reason`），不影响整体应答；单项探测超时 3s，整体控制在 2 秒量级。

App 解析：`model/DeviceCapability.java`（摄像头部分复用 `CameraCapability.parse`）；
缺 `audio` 分组（旧设备端）归一为 unavailable，绝不崩。契约测试：`DeviceCapabilityTest`、
`CameraCapabilityTest`。

### 4.6 开始推流 video/start/request → video/start/result

**作用**：让设备端用 ffmpeg 把摄像头画面推到流媒体服务（RTMP），App 拿到
`room_number` 后拼 WHEP 地址拉流播放（`BuildConfig.WHEP_URL_TEMPLATE`）。

- 请求：`walle/{t}/{d}/video/start/request`
- 应答：`walle/{t}/{d}/video/start/result`

请求字段：

| 字段 | 必填 | 校验 |
|---|---|---|
| `uuid` | 是 | 不匹配静默忽略 |
| `video_device` | 是 | 必须匹配 `^/dev/video\d+$` 且节点存在，否则 `result=-1` |
| `room_number` | 是 | 1–64 位 `[A-Za-z0-9_-]`（要拼进 RTMP URL，严格白名单），非法 `result=-2` |
| `request_id` | 是 | 配对用 |

应答字段：

| 字段 | 说明 |
|---|---|
| `result` | **0** 成功 / **-1** 没有此摄像头设备 / **-2** 命令执行失败（能力关闭、参数非法、ffmpeg 启动失败） |
| `cause` | 可读原因：`ok` / `没有此摄像头设备` / `命令执行失败` |
| `video_device` / `room_number` / `request_id` | 回显 |
| `action` | `started` / `restarted` / `already_streaming` / `failed` |
| `rtmp_url` | **已脱敏**（secret 打码），仅用于日志展示 |
| `pid`、`started_at`、`startup_grace_sec` | 进程信息 |
| `previous_room_number` / `previous_video_device` | `restarted` 时的旧值 |
| `error` | `failed` 时的具体错误 |

**房间号复用语义（重要）**：

- 请求参数与当前推流**完全一致**（同 device 同 room）→ 回 `result=0, action=already_streaming`，**不重连不重启**；
- 任一参数不同 → 先停旧流再用新参数推（同一时刻设备只有一路流）；
- 因此 App 切换摄像头时**复用同一 room_number**，避免 WHEP 侧重新协商导致黑屏。

### 4.7 停止推流 video/stop/request → video/stop/result

**作用**：停掉设备当前推流。

- 请求：`walle/{t}/{d}/video/stop/request`
- 应答：`walle/{t}/{d}/video/stop/result`

请求字段：`{uuid, request_id}` + 可选 `room_number`。

应答字段：

| 字段 | 说明 |
|---|---|
| `result` | **0** 已停止 / **-1** 当前没有设备在推流 |
| `cause` | 可读原因 |
| `stopped` | bool，是否真的停了一路流 |
| `room_number` / `video_device` / `pid` / `started_at` | 被停流的信息（成功时） |
| `request_id` 及公共字段 | — |

**一致性校验**：请求带了 `room_number` 且与当前推流不一致 → `result=-1` 并在 `error`
里说明当前房间号，**不会误停别人的流**。

### 4.8 alarm —— 告警（主动上报）

**作用**：阈值越限/恢复、USB 插拔的实时通知，驱动 App 告警中心（落 SQLite，200 条滚动淘汰）。

- 主题：`walle/{t}/{d}/alarm`（非 retained，QoS1，App 侧 2 秒签名去重）

字段：

| 字段 | 取值 | 说明 |
|---|---|---|
| `event` | `alarm` / `recover` / `usb_connect` / `usb_disconnect` | 事件类型 |
| `level` | `critical` / `warning` / `info` | 级别（阈值告警=warning，恢复=info，USB 拔出=warning，USB 接入=info） |
| `metric` | `cpu` / `memory` / `disk` / `network` / `temperature` / `usb` | 指标 |
| `alarm_type` | 稳定代码如 `cpu_high` | 分类用 |
| `message` | 中文可读描述 | 直接可展示 |
| `detail` | 阈值类：`{value, unit, threshold}`；USB：`{device{...}, current_count}` | 结构化细节 |
| `device_id`、`uuid`、`hostname`、`ip`、`timestamp`、`datetime` | 公共字段 | |

**回滞防抖**：越限（≥阈值）告警后，要降到 **阈值 × 0.9** 以下才发 `recover`，防止边界
抖动反复报警。阈值均可用环境变量覆盖（`THRESH_CPU`/`THRESH_MEM`/`THRESH_DISK`/
`THRESH_TEMP`/`THRESH_NET_MIB`），监控周期 `MONITOR_INTERVAL`（默认 5s）。

### 4.9 手机麦克风上行 phone/audio/send|stop（控制面）＋ RTMP（媒体面）

**作用**：把手机麦克风的声音实时送到设备扬声器（「反向音频」）。**媒体不走 MQTT**，
走独立 RTMP：App 推 `rtmp://www.baidong.xyz:1935/live/{room}_audio`，
设备端 ffmpeg 拉同一路流并播放到本机扬声器。MQTT 只负责「开 / 关」控制。

- 开启请求：`walle/{t}/{d}/phone/audio/send` → 应答 `.../send/result`
- 关闭请求：`walle/{t}/{d}/phone/audio/stop` → 应答 `.../stop/result`

请求字段：`{uuid, request_id, room_number}`；`stop` 的 `room_number` 可选
（退出 App 兜底时不带，表示「停掉当前任何一路手机音频」）。

应答字段：

| 字段 | 说明 |
|---|---|
| `result` | **0** 成功（`send` 时含 `already_playing`）/ **-1** 当前没有音频流（仅 stop）/ **-2** 失败（未装 ffmpeg、输出设备不可用、参数非法等） |
| `cause` | 可读原因 |
| `action` | `send`：`started` / `already_playing` / `failed` |
| `stopped` | `stop`：是否真的停了一路播放 |
| `room_number` / `rtmp_url`（已脱敏）/ `pid` / `started_at` / `output` | 成功时的播放进程信息 |
| `error` | 失败时的具体错误（含 ffmpeg 退出码与日志路径） |
| `request_id` 及公共字段 | — |

**关键约定**：

- **流名 = `{room}_audio`**（设备端 `build_rtmp_url(room + "_audio")`，可用
  `PHONE_AUDIO_STREAM_SUFFIX` 覆盖）。与设备自己推的视频流 `live/{room}` 错开，
  避免两端抢同一路 stream。
- **先连上再通知**：App 必须等 RTMP `publish` 握手成功（`onConnected`）才发
  `phone/audio/send`，否则设备端 ffmpeg 面对不存在的流会在启动宽限期（2s）内退出。
- **App 不订阅两个 `/result`**：App 侧是单向动作，按钮状态由本地推流结果驱动
  （`responseSubscriptions()` 不含 `phone/audio/*`）。设备端仍会发布 result，供日志、
  调试与其他客户端使用。
- **一致性校验**：`stop` 带了 `room_number` 且与当前播放房间号不一致 → `result=-1`
  并在 `error` 里说明，**不会误停别人**。
- **旧停新启**：`send` 时若已有别的房间在播，先停旧的再起新的（同一时刻设备只有一路）。
- **退出即关**：`PlayerActivity.onDestroy()` 与进程退出都会发 `phone/audio/stop`，
  设备端 `run()` 的 finally 也会 `_stop_phone_audio`，避免扬声器残留出声。
- 设备端开关与输出可配：`PHONE_AUDIO_ENABLE`（默认 True）、`PHONE_AUDIO_OUTPUT`
  （默认 `pulse`，无 pulse 的板子设 `alsa`）、`PHONE_AUDIO_DEVICE`（默认 `default`）。

**媒体面参数（App 端）**：`AudioRecord` 44.1kHz / 单声道 / PCM16 → MediaCodec
`audio/mp4a-latm`（AAC-LC，64kbps）→ 自拼 FLV audio tag（`0xAE` + 序列头/原始帧）
→ `librtmp`（`io.antmedia:rtmp-client`）`write`。推流 URL 模板在 `build.gradle` 的
`RTMP_AUDIO_URL_TEMPLATE`，运行时把 `{room}` 替换成当前房间号。

---

## 5. 修改协议的流程（改前必读）

1. **先改本文档**，让文档成为讨论与评审的载体。
2. 同步修改两端实现，对照表见 §6。命名约定由自检强制，不要发明新的后缀语义。
3. 同步更新测试，**缺一不可**：
   - App：`TopicNamesTest`（主题命名/清洗）、`MessageRouterTest`（路由/去重）、
     以及对应模型的解析测试（`StatusSnapshotTest` 等）
   - 设备端：`linux/selftest_topics.py`（清洗对照、订阅清单、命名成对、request_id 回显）
4. 本地验证通过后提交（CI 工作流已移除，提交前务必本地跑通以下检查）：

```bash
./gradlew :app:testDebugUnitTest        # App 契约测试
python3 linux/selftest_topics.py       # 设备端契约自检
bash -n linux/install.sh               # 部署脚本语法检查
```

**三条铁律**：

- 应答必须原样回显 `request_id`（历史 bug：漏回显 → App 丢弃结果 → 一直转圈）；
- 除 `discovery/query` 外请求必须带 `uuid`，设备端不匹配必须静默忽略；
- 新增主题段先过 `sanitize`，两端清洗规则必须逐字符一致。

## 6. 两端代码对照表

| 职责 | App（`app/src/main/java/www/walle/ai/net/`） | 设备端（`linux/rpi_monitor.py`） |
|---|---|---|
| 主题拼装/解析/清洗 | `TopicNames.java` | `sanitize_segment` / `v2_topic` / `_handle_request` |
| 报文路由 + QoS1 去重 | `MessageRouter.java` | `_handle_request` + `REQUEST_HANDLERS` |
| 请求发送 API | `MqttManager.java` | — |
| online 发布/遗嘱 | `DeviceRegistry.java`（消费） | `build_online_payload` / `publish_json(..., retain=True)` |
| 发现应答 | `DevicesViewModel.java`（消费） | `handle_device_query`（限频+抖动） |
| 状态采集 | `StatusSnapshot.java`（解析） | `build_status` / `handle_query_status` |
| 命令执行 | `CommandViewModel.java`（消费） | `handle_cmd_exec` / `_publish_cmd_result` |
| 设备能力 | `DeviceCapability.java`（解析，摄像头部分复用 `CameraCapability`） | `build_capability` / `probe_audio_capture` / `probe_audio_playback` / `handle_capability_query` |
| 推流控制 | `ControlViewModel` / `PlayerViewModel`（消费） | `handle_video_request` / `handle_video_stop` |
| 手机音频上行 | `PlayerActivity`（按钮/开关）+ `MqttManager`（发 send/stop）+ `RtmpAudioStreamer`（采集/AAC/FLV/librtmp 推流） | `handle_phone_audio_send` / `handle_phone_audio_stop` / `_start_phone_audio`（ffmpeg 拉流播放） |
| 告警 | `AlarmStore.java`（落盘）/ `AlarmViewModel` | `_event` / `check_threshold` / `check_usb` |
| uuid 校验 | —（App 负责携带） | `verify_uuid` |
| 配置/常量 | `Topics.java`（QoS=1、KeepAlive=60s） | 模块顶部常量（`MQTT_QOS`、阈值、开关） |

## 7. 相关文档

- [多租户主题方案（s3）](dev_record/s3-multi-tenant-topics.md) — ACL 配置与验收
- [设备发现收敛（n7）](dev_record/n7-discovery-scaling.md) — retained 驱动列表的演进
- [MQTT5 升级评估](dev_record/mqtt5-assessment.md) — 未来升级的工作量与触发条件
- [架构与技术评审](dev_record/architecture-review.md) — 分层与技术债全景
- [设备端部署说明](../linux/README.md) — rpi_monitor 安装与配置
