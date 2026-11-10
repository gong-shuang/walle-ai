# MQTT 协议约定（协议 v1 · 多租户主题）

> 本文是 App（Android）与设备端（`walle-device/linux-demo/main.py`）之间 MQTT 报文契约的**唯一权威文档**。
> 改主题、改字段、改配对逻辑之前先读完本文，并按文末「修改协议的流程」同步两端与测试。
>
> - 协议版本：**v1**（多租户主题）。旧版全局主题（如 `device/online`）已彻底删除，无兼容层。
> - 传输：MQTT 3.1.1，报文一律 **JSON（UTF-8）**，**QoS 1**，Keep Alive 60s。
> - App 端实现：`app/src/main/java/www/walle/ai/net/`（TopicNames / MessageRouter / MqttManager）
> - 设备端实现：`walle-device/linux-demo/main.py`
> - 契约交叉验证：`app` 单测（TopicNamesTest / MessageRouterTest / …）+ `walle-device/linux-demo/selftest_topics.py`

> **2026-11-08 安全变更**：`walle/{t}/{d}/v1/cmd/exec` 与 `walle/{t}/{d}/v1/cmd/result`
> 已从协议中移除，原因、影响与验证方式见文末「§8 变更记录」。旧版 App 调用将不会收到应答。

---

## 1. 总览

```
┌──────────────┐   walle/{tenant}/{device}/v1/{resource}   ┌──────────────┐
│  Android App │ ◄────────── MQTT Broker ─────────────► │ Linux 设备端  │
└──────────────┘         （EMQX / Mosquitto）           └──────────────┘
```

主题结构：

```
walle/{tenant}/{device}/v1/{resource}
```

- `tenant`：租户标识 = **App 登录用的 MQTT 用户名**（经清洗）。同一账号 = 同一租户。
- `device`：**稳定**的设备标识 `device_id`。首次启动由设备端按 `md5(MAC | CPU 物理核数 | 内存 GiB)` 取前 16 位，
  随后**落盘复用**（`main.resolve_device_id`），因此重启、重装、换 IP 都不会变。
  注意内存必须按 GiB 取整：`MemTotal` 在两次启动之间会浮动几 KiB，直接进哈希会让同一台硬件换 identity。
- `resource`：按语义命名（见 §2.3），可能含 `/`（如 `video/start/result`）。

特例：设备发现是**租户级**广播，没有 device 段（第 3 段是 `v1` 版本段）：`walle/{tenant}/v1/discovery/query`。

### 主题总表

| # | 协议 | App → 设备（请求） | 设备 → App（应答/上报） | 类型 |
|---|---|---|---|---|
| 1 | 上线/下线 | — | `walle/{t}/{d}/v1/online` | 主动上报（retained + LWT） |
| 2 | 设备发现 | `walle/{t}/v1/discovery/query` | `walle/{t}/{d}/v1/discovery/result` | 请求-应答（按需、限频） |
| 3 | 状态查询 | `walle/{t}/{d}/v1/state/query` | `walle/{t}/{d}/v1/state/result` | 请求-应答 |
| 4 | 设备能力 | `walle/{t}/{d}/v1/capability/query` | `walle/{t}/{d}/v1/capability/result` | 请求-应答 |
| 5 | 开始推流 | `walle/{t}/{d}/v1/video/start/request` | `walle/{t}/{d}/v1/video/start/result` | 请求-应答 |
| 6 | 停止推流 | `walle/{t}/{d}/v1/video/stop/request` | `walle/{t}/{d}/v1/video/stop/result` | 请求-应答 |
| 7 | 告警 | — | `walle/{t}/{d}/v1/alarm` | 主动上报 |
| 8 | 手机音频上行·开 | `walle/{t}/{d}/v1/phone/audio/send` | `walle/{t}/{d}/v1/phone/audio/send/result` | 单向动作（App 不订阅应答） |
| 9 | 手机音频上行·关 | `walle/{t}/{d}/v1/phone/audio/stop` | `walle/{t}/{d}/v1/phone/audio/stop/result` | 单向动作（App 不订阅应答） |

> `{t}` = tenant，`{d}` = device_id。
> 8/9 只承载「开/关」控制，音频本身走独立 RTMP（§4.8）。

---

## 2. 设计原则（为什么这么设计）

修改协议前务必理解这些约束——它们都是踩过坑后定下来的。

### 2.1 主题即权限边界

把租户编码进主题路径，Broker 就能直接用 `walle/${username}/#` 做 ACL：
用户 A 的 App 和设备只能看到 `walle/A/#`，天然隔离多用户，无需服务端代码。
**因此主题段里绝不允许出现 `+ # /` 等通配/分隔符**（见 §3.3 清洗规则），否则 ACL 会失效。

### 2.2 设备段必须稳定，禁止用 uuid

`uuid` 由设备端按 `uuid5(MAC|IP|启动时间)` 生成，**每次重启都会变**。
如果主题用 uuid，设备重启后 App 订阅的旧主题全部失效，还会产生「幽灵设备卡片」。
所以主题里的设备段只用 `device_id`（稳定标识）；`uuid` 降级为**报文字段**，
用于请求校验（§3.4）和设备归并。App 侧归并逻辑见 `DeviceRegistry`。

**`device_id` 必须落盘，不能每次启动重算**（历史 bug）：它原来的算法是
`md5(MAC | CPU 核数 | 内存字节数)` 取 16 位，但 `MemTotal` 在两次启动之间会浮动几 KiB
（内核保留页、启动参数、内存训练），于是同一台机器算出了两个 `device_id` —— 表现为
App 里同一台设备出现两张卡片、旧主题上的 retained `online:false` 一直残留。
现在的规则：

- 首次启动按 `md5(MAC | CPU 物理核数 | 内存 **GiB**)` 生成，写入 `<安装目录>/.device-id`
  （可用 `DEVICE_ID_FILE` 覆盖）；
- 之后**只读文件复用**，硬件指纹再怎么漂移都不影响；
- 只有「文件里的 MAC 集合不包含当前 MAC」时才重新生成（换网卡 / 克隆镜像到新机器），
  旧值记入 `previous` 便于排查；
- App 侧还有一层兜底：`device_id` 变了但 MAC 相同时按同一台设备归并，并用报文
  `timestamp` 丢弃更旧的 retained `online:false`（见 `DeviceRegistry.applyOnline`）。

> 代价：`device_id` 一旦变化（换网卡），旧主题上的 retained `online` 无法由设备自己清理
> ——Broker ACL 只允许它写 `walle/{tenant}/${clientid}/#`。这类残留由 App 侧按 MAC 归并消化。

### 2.3 资源按语义命名，应答一律 `/result`

| 后缀 | 语义 | 例子 |
|---|---|---|
| `…/query` | 查询（读，无副作用） | `state/query` |
| `…/request` | 动作请求（生命周期管理） | `video/start/request` |
| `…/result` | 应答，**只在收到对应请求后发布**，绝不主动上报 | `state/result` |
| `…/send` | 手机侧发起「开始上行」的新后缀（App 是动作发起方，不是设备推流） | `phone/audio/send` |
| `…/stop` | 手机侧发起「停止上行」的新后缀（无 room 也可发，用于退出兜底） | `phone/audio/stop` |
| 无后缀 | 主动上报，只有 `online` 和 `alarm` 两个 | — |

这套约定由 `walle-device/linux-demo/selftest_topics.py` 第 4 节强制检查：每个 `REQ_*` 必须在
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
`alarm`、`capability/result`、`video/start/result`、`video/stop/result`
（重复处理会出事，例如推流结果重复 → 播放器初始化两次）。
`online` 与 `state/result` 是幂等的（同一份状态重复应用无害），**不去重**。

### 2.6 retained 只给 online，发现靠回放而非轮询（N7）

设备列表 = 当前在线设备，直接来自 retained `online` 报文：App 一连上就收到回放，
**不需要广播轮询**。`discovery/query` 降级为按需手段（手动刷新、连上后一台设备都没
回放出来时自动兜底一次）。设备端对 discovery 应答做了**限频（2 秒/台）+ 随机抖动
（≤0.8 秒）**，避免多台手机同时扫描时回包风暴。

### 2.7 不订通配子树

MQTT 3.1.1 没有 no-local 语义，订 `walle/{t}/#` 会把自己发的请求再收一遍，浪费带宽、
干扰排查。所以两端都只订**精确的响应/请求主题清单**：

- App 订 7 条：`walle/{t}/+/v1` 下的 `online`、`alarm`、5 条 `…/result`（见 `TopicNames.responseSubscriptions`）
- 设备端订 7 条：6 条设备级请求（含 `phone/audio/send|stop`）+ 1 条租户级 `discovery/query`（见 `main.subscriptions()`）

---

## 3. 公共约定

### 3.1 公共字段

绝大多数设备 → App 报文都带这些字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `device_id` | string | 稳定设备标识（见 §2.2，落盘复用）。设备端可省略，App 用主题里的设备段兜底注入，区别不同设备，重启不变。 |
| `uuid` | string | 本次启动的 uuid（重启会变），device_id相同但uuid不同说明设备重启了。 |
| `hostname` | string | 主机名 |
| `ip` | string | 局域网 IP |
| `timestamp` | number | Unix 秒。**在线报文用它判定新旧**：device_id 漂移后旧主题遗留的 retained `online` 若比已知信息更旧，App 直接丢弃（§4.1） |
| `datetime` | string | `YYYY-MM-DD HH:MM:SS` 本地时间 |
| `request_id` | string | **仅应答报文**：原样回显请求的 request_id，标识请求-应答对，每次请求都不同。 |

### 3.2 request_id 配对规则

1. App 每发一个请求生成新 `request_id`（8 位 hex），并把它放进请求报文。
2. 设备端应答必须原样回显；没收到匹配 `request_id` 的应答前，UI 处于等待态。
3. 旧请求迟到应答会被丢弃（App 只认「当前这次」的 rid）。

### 3.3 主题段清洗（sanitize）

两端**必须完全一致**，否则主题对不上。规则（`TopicNames.sanitize` / `main.sanitize_segment`）：

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

- 主题：`walle/{t}/{d}/v1/online`，**retained**
- 上线：设备连上 broker 后立即发布 `online: true` + 完整身份（同 §4.2 的身份字段全集）
- 下线：broker 通过 **LWT 遗嘱**发布 `online: false`（设备端注册 will，异常掉线也能通知）

关键字段（其余同 `build_device_info`）：

| 字段 | 说明 |
|---|---|
| `online` | bool，true=上线 / false=下线 |
| `device_id` / `uuid` / `hostname` | 归并与展示用标识 |
| `version_code` / `version_name` | 设备端软件版本：版本号（整数，如 `1`）、版本名（如 `0.1.1.1`） |
| `device_type` / `vendor` / `model` / `serial` | 设备类型、厂商、型号（如 Raspberry Pi 5）、序列号 |
| `ip` / `mac` | 网络身份 |
| `os{name,kernel,kernel_build,arch,python}` | 系统与内核版本 |
| `cpu{model,cores_logical,cores_physical,frequency_mhz,temperature_c}` | CPU 概览 |
| `memory_total_bytes/human`、`disk_total_bytes/human` | 总量 |
| `uptime_seconds` / `uptime_human` / `boot_time` | 运行时长 |
| `uuid_source{mac,ip,note}` | uuid 生成依据（提示重启会变，用 discovery 拿最新） |

**注意**：`online: false` 的遗嘱只由 broker 代发；设备端正常关机也应主动发一次。
App 侧重复 online 不去重（幂等）。

**归并顺序（`DeviceRegistry.applyOnline`）**：`device_id` → `uuid` → **MAC** → `hostname`。
最后这层 MAC 归并专治 `device_id` 漂移：同一块网卡的两条 online（旧 id 的
`online:false` + 新 id 的 `online:true`）会合成一张卡片；若旧报文按 MAC 命中已有设备、
却是下线且 `timestamp` 更旧，则整条丢弃，避免把在线设备刷成离线。
真实的同一启动周期遗嘱（`device_id` 相同、时间戳相同）不受影响。

### 4.2 设备发现 discovery/query → discovery/result

**作用**：按需拉取设备身份详情。日常列表不靠它（靠 retained online），仅用于手动
「重新扫描」和「连上后零回放」的自动兜底。

- 请求：`walle/{t}/v1/discovery/query`（租户级，没有 device 段）
- 应答：`walle/{t}/{d}/v1/discovery/result`

请求字段：

| 字段 | 必填 | 说明 |
|---|---|---|
| `request_id` | 是 | 配对用，应答原样回显 |


设备端保护机制（防止广播风暴）：

| 机制 | 参数 | 说明 |
|---|---|---|
| 限频 | `DISCOVERY_MIN_INTERVAL_SEC = 2.0` | 每台设备 2 秒内只应答一次，重复查询丢弃 |
| 抖动 | `DISCOVERY_JITTER_SEC = 0.8` | 应答前随机延迟 0~0.8s，N 台设备错开回包 |

> 坑位提醒：这个主题没有 device 段（第 3 段是 `v1` 版本段），解析时不能切成
> `device="v1", resource="discovery/query"`——两端（`TopicNames.parse`、`main._handle_request`）
> 都对它做了特判，别改没。

### 4.3 状态查询 state/query → state/result

**作用**：拉取设备运行状态，驱动状态页所有卡片。CPU/网络数据复用监控循环的
最近一次采样（`MONITOR_INTERVAL`，默认 5s），USB 列表复用 `check_usb` 的维护结果，
**查询路径不再阻塞采样**；内存/磁盘/推流/告警在应答时读取。

- 请求：`walle/{t}/{d}/v1/state/query`
- 应答：`walle/{t}/{d}/v1/state/result`

请求字段：

| 字段 | 必填 | 说明 |
|---|---|---|
| `uuid` | 是 | 设备当前 uuid，不匹配静默忽略 |
| `request_id` | 是 | 配对用 |

应答字段（嵌套结构）：

| 分组 | 字段 |
|---|---|
| 顶层 | `device_id`、`uuid`、`hostname`、`version_code`、`version_name`、`ip`、`timestamp`、`datetime`、`uptime_seconds`、`sample_age_sec`、`request_id` |
| `system` | `os`、`kernel`、`os_version`、`python`、`machine`、`model` |
| `cpu` | `percent`（总）、`per_core_percent[]`（各核）、`count_logical/physical`、`load_avg_1m/5m/15m`、`frequency_mhz{current,min,max}`、`temperature_c`、`sample_interval_sec` |
| `memory` | `percent`、`total/used/available/free_bytes` + `*_human`、`swap{percent,total_bytes,used_bytes,total_human,used_human}`（无 swap 时为 null） |
| `disk` | `mounts[]`：每挂载点容量/使用率/文件系统类型 |
| `network` | `sample_interval_sec`、`total_rx/tx_bytes_per_sec`、`total_rx/tx_rate`（可读）、`interfaces[]`（每网卡 rx/tx 速率） |
| `usb` | `count`、`devices[]` |
| `video` | 推流状态（任务 1）：`streaming`、`room_number`、`video_device`、`audio`（推流时的音频信息，无音频/未推流为 null）、`pid`、`started_at`、`uptime_sec`；未推流时 `streaming=false` 且其余字段全为 null。数据来自推流时记录的 `video_meta`，ffmpeg 异常退出后看护线程清理，下一次查询即 `streaming=false` |

**采样时效**：`cpu` / `network` 的 `sample_interval_sec` 是监控循环的实际采样周期；
`sample_age_sec` 表示这份 CPU/网络数据距采样时刻的秒数（0 ~ 采样周期）。设备刚启动、
监控尚未产出第一份快照时，查询会现场补采一次（约 0.2s）兜底。

**video 分组的用途**：App 控制页进入时查一次状态，若 `video.streaming=true` 且
`room_number`/`video_device` 齐全，直接用该房间号拼 WHEP 地址拉流，**自动接管设备正在
进行的推流**，无需用户再点「开始推流」。App 侧只认自己这次查询的 `request_id`
（`ControlViewModel.takeoverRid`），状态页的周期查询不会触发接管。

解析入口（App 唯一）：`model/StatusSnapshot.java`。契约测试：`StatusSnapshotTest`
（缺分组归空、异常数值归一为 null、网卡按名称排序等）。**状态是实时查询数据，不落盘**
（N11 曾落盘又 revert，决策见 git 历史）。

### 4.4 设备能力 capability/query → capability/result

**作用**：枚举设备端外设能力——摄像头（v4l2）、麦克风/音响（PulseAudio）等，供控制页
展示与选择。早期只查摄像头，任务 4 起升级为通用能力查询（旧主题已彻底删除，无兼容层）。

- 请求：`walle/{t}/{d}/v1/capability/query`，字段 `{uuid, request_id}`
- 应答：`walle/{t}/{d}/v1/capability/result`

应答字段：

| 字段 | 说明 |
|---|---|
| `capability` | 固定 `"device"` |
| `capabilities[]` | 能力标签，按探测结果条件出现：`system_status` 恒有；有摄像头加 `camera_video_capture`；`VIDEO_ENABLE` 开启加 `rtmp_push`；音频可用分别加 `audio_capture` / `audio_playback` |
| `camera_count` / `cameras[]` | 摄像头列表，每项含 `node`（如 `/dev/video0`）、格式/分辨率/帧率明细、`format_count`、`resolution_count`；无 `node` 的条目 App 侧会过滤 |
| `audio.capture` | 麦克风：`{available, device, sources[], reason}`；`device` 为默认录音源（通常 `default`），不可用时 `reason` 给出原因 |
| `audio.playback` | 音响：`{available, device, sinks[], volume_percent, muted, reason}`；`volume_percent` 未知为 null（App 归一为 -1） |
| `summary` | 聚合统计：`total_cameras`、`total_formats`、`total_resolutions`、`audio_sources`、`audio_sinks`、`probe_cost_ms` |
| `probe` | `{tool: "v4l2-ctl + pactl", error, audio_error}`：`error` / `audio_error` 非 null 分别表示视频 / 音频探测失败（含 CAM_ENABLE=False 的提示）；`pending=true` 表示首次探测尚未完成（见下） |
| `cache` | `{source: "cache" \| "pending", age_sec, ttl_sec}`：本次应答来自缓存（附缓存年龄）还是首次探测未完成时的占位 |
| `request_id` 及公共字段 | — |

**能力缓存（设备端）**：`v4l2-ctl` / `pactl` 探测耗时可达数秒，设备端由后台线程
在启动时立即探测一次、之后按 `CAPABILITY_CACHE_TTL`（默认 120s）刷新；查询线程只读
缓存，不再执行探测。首次查询时若探测尚未完成，最多等待 `CAPABILITY_FIRST_WAIT`
（默认 3s），仍未完成则返回 `probe.pending=true` 的占位报文（`capabilities` 仅
`system_status`），App 应稍后重新查询。`CAPABILITY_CACHE_ONLY=True` 时查询完全不等待，
只返回缓存或 pending。

音频探测（设备端 `probe_audio_capture` / `probe_audio_playback`）：优先
`pactl list short sources/sinks`（录音侧排除 `*.monitor` 回环源）；pactl 缺失时录音侧用
ffmpeg 试读 0.2s 兜底；播放侧另取默认 sink、音量与静音状态。**任何一步失败只降级**
（`available:false` + `reason`），不影响整体应答；单项探测超时 3s，整体控制在 2 秒量级。

App 解析：`model/DeviceCapability.java`（摄像头部分复用 `CameraCapability.parse`）；
缺 `audio` 分组（旧设备端）归一为 unavailable，绝不崩。契约测试：`DeviceCapabilityTest`、
`CameraCapabilityTest`。

### 4.5 开始推流 video/start/request → video/start/result

**作用**：让设备端用 ffmpeg 把摄像头画面推到流媒体服务（RTMP），App 拿到
`room_number` 后拼 WHEP 地址拉流播放（`BuildConfig.WHEP_URL_TEMPLATE`）。

- 请求：`walle/{t}/{d}/v1/video/start/request`
- 应答：`walle/{t}/{d}/v1/video/start/result`

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

### 4.6 停止推流 video/stop/request → video/stop/result

**作用**：停掉设备当前推流。

- 请求：`walle/{t}/{d}/v1/video/stop/request`
- 应答：`walle/{t}/{d}/v1/video/stop/result`

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

### 4.7 alarm —— 告警（主动上报）

**作用**：阈值越限/恢复、USB 插拔、推流/手机音频中断的实时通知，驱动 App 告警中心（落 SQLite，200 条滚动淘汰）。

- 主题：`walle/{t}/{d}/v1/alarm`（非 retained，QoS1，App 侧 2 秒签名去重）

字段：

| 字段 | 取值 | 说明 |
|---|---|---|
| `event` | `alarm` / `recover` / `usb_connect` / `usb_disconnect` | 事件类型 |
| `level` | `critical` / `warning` / `info` | 级别（阈值告警=warning，恢复=info，USB 拔出=warning，USB 接入=info） |
| `metric` | `cpu` / `memory` / `disk` / `network` / `temperature` / `usb` / `video` / `audio` | 指标 |
| `alarm_type` | 稳定代码如 `cpu_high` / `video_lost` / `phone_audio_lost` | 分类用 |
| `message` | 中文可读描述 | 直接可展示 |
| `detail` | 阈值类：`{value, unit, threshold}`；USB：`{device{...}, current_count}`；视频中断：`{room_number, exit_code, log_file, tail}`；手机音频中断：`{room_number, exit_code, reason, log_file, tail}` | 结构化细节 |
| `device_id`、`uuid`、`hostname`、`ip`、`timestamp`、`datetime` | 公共字段 | |

**回滞防抖**：越限（≥阈值）告警后，要降到 **阈值 × 0.9** 以下才发 `recover`，防止边界
抖动反复报警。阈值均可用环境变量覆盖（`THRESH_CPU`/`THRESH_MEM`/`THRESH_DISK`/
`THRESH_TEMP`/`THRESH_NET_MIB`），监控周期 `MONITOR_INTERVAL`（默认 5s）。

**媒体中断告警**：`video_lost`（`metric=video`）在 ffmpeg 推流进程非主动退出时上报；
`phone_audio_lost`（`metric=audio`）在 WHEP 播放器非主动退出时上报。两者 `event=alarm`、
`level=warning`，`detail` 带 `room_number`、`exit_code`、`log_file` 与 `tail`（日志尾部，
≤500 字符）用于定位；重新启动成功后发同 `alarm_type` 的 `event=recover`，主动 stop 不发告警。

### 4.8 手机麦克风上行 phone/audio/send|stop（控制面）＋ WebRTC WHIP/WHEP（媒体面）

**作用**：把手机麦克风的声音实时送到设备扬声器（「反向音频」）。**媒体不走 MQTT**，
走独立 WebRTC：App 用 WHIP 推 `https://www.baidong.xyz:443/rtc/v1/whip/?app=live&stream={room}_audio&secret=…`，
设备端用 WHEP 拉同一路流（`whep_audio_player.py`，aiortc + PortAudio）并播放到本机扬声器。
MQTT 只负责「开 / 关」控制。

> 为什么不用 RTMP：RTMP 走 TCP，没有丢帧/追帧机制，服务端起播缓存 + ffmpeg/声卡缓冲会累积成
> 秒级延迟（实测手机说话到出声约 10s）。WebRTC 走 UDP，晚到的包直接丢弃，实测端到端约 200ms。
> 另：服务端 WHIP 端点**必须带 `secret`**，不带会直接 502。这个 secret 由 Oryx 部署时生成、
> 每台服务器不同，因此**不写死在代码里**：App 构建期注入（`-PoryxSecret` / `gradle.properties`
> 的 `oryxSecret` / 环境变量 `WHIP_SECRET`），设备端读 `/etc/walle-ai.conf` 的 `WHIP_SECRET`。

- 开启请求：`walle/{t}/{d}/v1/phone/audio/send` → 应答 `.../send/result`
- 关闭请求：`walle/{t}/{d}/v1/phone/audio/stop` → 应答 `.../stop/result`

请求字段：`{uuid, request_id, room_number}`；`stop` 的 `room_number` 可选
（退出 App 兜底时不带，表示「停掉当前任何一路手机音频」）。

应答字段：

| 字段 | 说明 |
|---|---|
| `result` | **0** 成功（`send` 时含 `already_playing`）/ **-1** 当前没有音频流（仅 stop）/ **-2** 失败（缺依赖、输出设备不可用、参数非法等） |
| `cause` | 可读原因 |
| `action` | `send`：`started` / `already_playing` / `failed` |
| `stopped` | `stop`：是否真的停了一路播放 |
| `room_number` / `whep_url` / `pid` / `started_at` / `output` | 成功时的播放进程信息 |
| `error` | 失败时的具体错误（含播放器退出码与日志路径） |
| `request_id` 及公共字段 | — |

**关键约定**：

- **流名 = `{room}_audio`**（设备端 `PHONE_AUDIO_WHEP_URL_TEMPLATE` 拼 `{stream}`，可用
  `PHONE_AUDIO_STREAM_SUFFIX` 覆盖）。与设备自己推的视频流 `live/{room}` 错开，
  避免两端抢同一路 stream。
- **先连上再通知**：App 必须等 WHIP 的 PeerConnection 进入 `CONNECTED`（媒体面已通）才发
  `phone/audio/send`，否则设备端 WHEP 拉不到流（首帧 8s 超时会失败）。
- **App 不订阅两个 `/result`**：App 侧是单向动作，按钮状态由本地推流结果驱动
  （`responseSubscriptions()` 不含 `phone/audio/*`）。设备端仍会发布 result，供日志、
  调试与其他客户端使用。
- **一致性校验**：`stop` 带了 `room_number` 且与当前播放房间号不一致 → `result=-1`
  并在 `error` 里说明，**不会误停别人**。
- **旧停新启**：`send` 时若已有别的房间在播，先停旧的再起新的（同一时刻设备只有一路）。
- **退出即关**：`PlayerActivity.onDestroy()` 与进程退出都会发 `phone/audio/stop`，
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

---

## 5. 修改协议的流程（改前必读）

1. **先改本文档**，让文档成为讨论与评审的载体。
2. 同步修改两端实现，对照表见 §6。命名约定由自检强制，不要发明新的后缀语义。
3. 同步更新测试，**缺一不可**：
   - App：`TopicNamesTest`（主题命名/清洗）、`MessageRouterTest`（路由/去重）、
     以及对应模型的解析测试（`StatusSnapshotTest` 等）
   - 设备端：`walle-device/linux-demo/selftest_topics.py`（清洗对照、订阅清单、命名成对、request_id 回显）
4. 本地验证通过后提交（CI 工作流已移除，提交前务必本地跑通以下检查）：

```bash
./gradlew :app:testDebugUnitTest        # App 契约测试
python3 walle-device/linux-demo/selftest_topics.py       # 设备端契约自检
```

**三条铁律**：

- 应答必须原样回显 `request_id`（历史 bug：漏回显 → App 丢弃结果 → 一直转圈）；
- 除 `discovery/query` 外请求必须带 `uuid`，设备端不匹配必须静默忽略；
- 新增主题段先过 `sanitize`，两端清洗规则必须逐字符一致。

## 6. 两端代码对照表

| 职责 | App（`app/src/main/java/www/walle/ai/net/`） | 设备端（`walle-device/linux-demo/main.py`） |
|---|---|---|
| 主题拼装/解析/清洗 | `TopicNames.java` | `sanitize_segment` / `device_topic` / `_handle_request` |
| 报文路由 + QoS1 去重 | `MessageRouter.java` | `_handle_request` + `REQUEST_HANDLERS` |
| 请求发送 API | `MqttManager.java` | — |
| online 发布/遗嘱 | `DeviceRegistry.java`（消费） | `build_online_payload` / `publish_json(..., retain=True)` |
| 发现应答 | `DevicesViewModel.java`（消费） | `handle_device_query`（限频+抖动） |
| 状态采集 | `StatusSnapshot.java`（解析） | `build_status` / `handle_query_status` |
| 设备能力 | `DeviceCapability.java`（解析，摄像头部分复用 `CameraCapability`） | `build_capability` / `probe_audio_capture` / `probe_audio_playback` / `handle_capability_query` |
| 推流控制 | `ControlViewModel` / `PlayerViewModel`（消费） | `handle_video_request` / `handle_video_stop` |
| 手机音频上行 | `PlayerActivity`（按钮/开关）+ `MqttManager`（发 send/stop）+ `WhipAudioSession`（WebRTC 音频 WHIP 推流） | `handle_phone_audio_send` / `handle_phone_audio_stop` / `_start_phone_audio`（WHEP 拉流 + 声卡播放） |
| 告警 | `AlarmStore.java`（落盘）/ `AlarmViewModel` | `_event` / `check_threshold` / `check_usb` |
| uuid 校验 | —（App 负责携带） | `verify_uuid` |
| 配置/常量 | `Topics.java`（QoS=1、KeepAlive=60s） | 模块顶部常量（`MQTT_QOS`、阈值、开关） |

## 7. 相关文档

- [设备端部署说明](../walle-device/linux-demo/README.md) — main.py 安装与配置

---

## 8. 变更记录

### 2026-11-10：state/query 改为复用监控采样快照

**变更类型**：非破坏性增量（协议 v1 字段只增不改）。

**原因**：`state/query` 原先在请求线程内阻塞 1s 采样，状态页刷新慢且占用请求线程。

**变更内容**：`state/result` 新增顶层 `sample_age_sec`；CPU/网络数据复用监控循环
最近一次采样，USB 列表复用 `check_usb` 的维护结果，查询不再阻塞采样。

**影响与兼容性**：App 可忽略新增字段；CPU/网络数据最多滞后一个采样周期（默认 5s）。

**验证方式**：`test/test_status_cache.py` 覆盖快照复用、无快照时兜底采样与查询 < 100ms。

### 2026-11-10：capability/query 改为后台缓存探测

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

### 2026-11-08 20:00（Asia/Shanghai, CST）：移除远程命令 cmd/exec → cmd/result

**变更类型**：破坏性删除（协议 v1 内直接移除，不保留兼容层）。

**原因**

- 该能力允许在设备上通过 `/bin/bash` 执行任意命令，属于最高危的远程命令执行面。
- 设备 `uuid` 会通过 `discovery/result` 与 retained `online` 报文下发，不能作为鉴权凭证；
  只要拿到租户 MQTT 凭据，就能完成「发现设备 → 取 uuid → 执行任意命令」的攻击链。
- 原实现使用前缀白名单 + `shell=True`，无法阻止 `;`、`&&`、`$()` 等注入；
  且 `ENABLE_CMD_EXEC` 默认为开启，不符合上线安全要求。

**变更内容**

- 删除请求主题 `walle/{t}/{d}/v1/cmd/exec` 与应答主题 `walle/{t}/{d}/v1/cmd/result`。
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
- 使用 MQTT 客户端向 `walle/{t}/{d}/v1/cmd/exec` 发布消息，设备应无任何响应。
- 打开 `test/car_ctl_by_mqtt.html`，页面不再出现「远程指令」卡片。
