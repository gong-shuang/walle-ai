# 任务清单

> 本文件记录待开发任务。完成一项就把状态改为「已完成」，并在下方补充实际改动与验证方式。
> 涉及 MQTT 报文的改动，先读 [MQTT_protocol_convention.md](MQTT_protocol_convention.md)，
> 并按其中「修改协议的流程」同步更新两端实现与契约测试。

## 总览

| # | 任务 | 状态 | 优先级 |
|---|---|---|---|
| 1 | 状态报文携带推流信息，控制页自动接管正在进行的推流 | ✅ 已完成（2026-10-30） | 高 |
| 2 | App → 设备端音频传输（反向音频） | 待开始 | 中 |
| 3 | 告警拆分为「当前」与「历史」两个 Fragment | 待开始 | 中 |
| 4 | 设备能力从「摄像头」扩展为通用外设能力（麦克风、音响等） | ✅ 已完成（2026-10-30） | 中 |

---

## 任务 1：状态查询返回推流信息，控制页自动显示正在播放的视频

### 目标

App 打开设备控制页时，如果该设备**正在推流**，直接显示视频画面，不需要用户再点一次「开始推流」。

### 设备端改动

- [x] `state/result` 中新增推流信息（建议字段，最终以协议文档为准）：

```json
{
  "video": {
    "streaming": true,
    "room_number": "545754",
    "video_device": "/dev/video0",
    "audio": { "available": true, "device": "default", "codec": "aac", "bitrate": "128k" },
    "pid": 12345,
    "started_at": "2026-10-30 18:47:42",
    "uptime_sec": 128.5
  }
}
```

- [x] 未推流时 `video.streaming = false`，其余字段可为 null；`video` 整体缺省时 App 按「未推流」处理。
- [x] 数据来源：复用已有的 `self.video_meta`（含 room_number / video_device / pid / started_at / audio / started_ts），不要重复探测。
- [x] 注意加锁读取 `video_lock`，避免与推流启停竞态。
      → 新增 `_video_status()`：锁内取快照，锁外组报文；`proc.poll()` 非 None 视为未推流。
- [x] 顺带确认 `video_meta` 在 ffmpeg 进程异常退出后会被清理（否则会误报 streaming=true）。
      → 既有 `_video_watch` 在 `proc.wait()` 返回后（含异常退出）即清空 `video_proc/video_meta`；
      `_video_status()` 再加一道 `poll()` 兜底，selftest 第 11 节覆盖。

### App 端改动

- [x] `model/StatusSnapshot` 解析新增 `video` 分组（新增强类型字段，缺失时安全降级）。
      → 新增 `StatusSnapshot.VideoStatus`（streaming/roomNumber/videoDevice/pid/startedAt/uptimeSec/audioAvailable），
      分组缺失归 null，`takeoverReady()` 统一判定。
- [x] `ControlViewModel` 进入页面时：若 `video.streaming == true` 且 `video_device` 与当前选中一致，
      直接用 `room_number` 拼 WHEP 地址发起拉流，跳过「请求推流」步骤。
      → `onCapability` 成功后发一次 `state/query`（`takeoverRid` 配对，不吃状态页的周期查询），
      `onMachineStatus` 里接管：置 LIVE + activeRoom/activeCamera，Spinner 跟随选中推流摄像头。
- [x] 若 `video.streaming == true` 但设备与用户选中不同，提示「设备正在推流（room x），是否切换摄像头」，
      切换走已有的 `video/start/request`（复用房间号语义）。
      → 实现为直接接管并在 Spinner 选中实际推流的摄像头（同一时刻设备只有一路流，
      接管显示即为用户想看的内容；推流设备不在能力清单时也能播）。
- [x] 自动接管失败（WHEP 拉流失败）时回退到正常的手动推流流程，并给出可读原因。
      → 复用既有 `onPlayerError`：stopStream + 「播放失败: …」文案。

### 验收标准

- App 全新启动（或杀掉后台）后进入控制页，设备正在推流 → 画面自动出现，无需交互。
- 设备未推流 → 控制页仍是「点击开始推流」的空态。
- 设备端 ffmpeg 异常退出后，下一次 `state/query` 返回 `streaming: false`。

### 验证记录（2026-10-30）

- `./gradlew :app:testDebugUnitTest`：**57 个单测全部通过**（StatusSnapshotTest 8 个，含 3 个 video 分组新用例）。
- `python3 linux/selftest_topics.py`：全部通过（新增第 11 节 video 快照：未推流/推流中/进程已退出 3 种形态）。
- 设备端 MQTT 实测（本机 lavfi testsrc2 假摄像头推流，room=887766）：
  - 推流前 `state/result.video.streaming=false`（其余字段全 null）；
  - `video/start/result` result=0 后，`state/result.video` 回显 room/device/pid/uptime_sec/audio。
- App 端到端：emulator-5554 全新启动 → aigo「控制」页 → **未点任何按钮**，
  自动用 state 里的 room=887766 发起 WHEP 拉流（日志 `POST WHEP -> HTTP 201, stream=887766`，
  PeerConnection CONNECTED），画面渲染出测试图，状态「● 推流中」，无崩溃。
- 测试流已通过 `video/stop/request` 正常停止（result=0, stopped=true）。
- 协议文档 `doc/MQTT_protocol_convention.md` §4.3 已补 video 分组说明。

### 涉及文件

- 设备端：`linux/rpi_monitor.py`（`build_status` / `_handle_request`）
- App：`model/StatusSnapshot.java`、`ui/ControlViewModel.java`、`ui/ControlFragment.java`
- 测试：`StatusSnapshotTest`（新增 video 分组解析用例）、`linux/selftest_topics.py`（可选：字段存在性）

---

## 任务 2：App 将音频数据传输到树莓派端

### 目标

手机录音（麦克风）实时送到树莓派，实现「对着手机说话，设备端出声」的反向音频链路。

### 关键问题（先做技术选型再动手）

MQTT 不适合传音频流（QoS1 + JSON + 小报文），必须走独立通道。候选：

| 方案 | 说明 | 代价 |
|---|---|---|
| A. 复用 WHEP/WebRTC 反向流 | 在现有 WebRTC 会话里加 audio track（sendrecv），设备端由流媒体服务转发 | 需要设备端有接收端（SRS/MediaMTX 的 WebRTC 或自建） |
| B. 独立 RTMP/RTSP 推流 | App 用 MediaCodec + 轻量 RTMP 客户端把 AAC 推给设备端 | App 需引入 RTMP/RTSP 客户端，设备端需接收并播放 |
| C. 设备端常驻音频服务 | 设备端跑一个简单 UDP/HTTP 音频接收服务，App 直连（不走 Broker） | 需要设备端常驻进程与配对/鉴权机制 |

- [ ] 与设备端确认流媒体服务能力后选定方案，并记录决策理由（写进 `doc/dev_record/`）。
- [ ] 补充鉴权设计：不能让租户 A 的手机把音频推给租户 B 的设备。

### 实现要点（选定方案后细化）

- [ ] App：录音权限（`RECORD_AUDIO`）运行时申请；前台页录音时保持屏幕/服务活跃。
- [ ] App：音频采集与编码（建议 16kHz/单声道/AAC 或 Opus），做成可开关的「按住说话 / 持续说话」。
- [ ] 设备端：接收并播放（`aplay` / `pulseaudio` / ffplay），音量与设备选择可配。
- [ ] 与任务 4 联动：设备能力里要能查到「有无音响、当前音量」。
- [ ] 失败兜底：网络断开、设备忙、无扬声器时给 App 明确错误码。

### 验收标准

- 手机说话，设备端扬声器 1 秒内出声（同局域网）。
- 关闭开关后设备端立即静音，无残留缓冲。
- 未授权录音权限时功能入口不可用，并提示原因。

---

## 任务 3：告警中心拆分为「当前」与「历史」

### 目标

告警页分两个 Tab/Fragment：

- **当前**：尚未恢复的告警（同 `metric` 只保留最新一条，恢复后自动移出）；
- **历史**：已恢复的告警 + 所有事件的完整流水（按时间倒序、可按级别/类型筛选）。

### 实现要点

- [ ] `AlarmStore` 增加「活动告警」的判定：收到 `alarm` 事件进入活动集合；收到对应 `recover`
      从活动集合移除并转入历史。
- [ ] 活动集合的 key 建议用 `metric + alarm_type`，同 key 重复告警只更新值与时间，不新增卡片。
- [ ] USB 插拔事件（`usb_connect` / `usb_disconnect`）只进历史，不进「当前」。
- [ ] 未读计数语义确认：只统计「当前」新增？还是两者都算？先定规则再改 UI（建议：红点只统计当前告警）。
- [ ] UI：`AlarmFragment` 拆为 `ActiveAlarmFragment` + `AlarmHistoryFragment`，共用列表 item 布局。
- [ ] 持久化：活动告警是否落盘需要决策（现有策略是告警全量落盘、设备状态不落盘）。
      重启后活动告警可从历史里按「最近一条 alarm 且其后无 recover」重建。

### 验收标准

- 触发一条 CPU 告警 → 出现在「当前」，未读红点 +1；恢复后从「当前」消失，出现在「历史」。
- 同一指标反复越限/恢复 10 次，「当前」始终最多 1 条，「历史」完整 20 条。
- App 重启后「当前」与「历史」与重启前一致。

### 涉及文件

- `net/AlarmStore.java`、`data/LocalDb.java`、`ui/AlarmFragment.java`、`ui/AlarmViewModel.java`、
  `res/layout/` 告警相关布局、`AlarmStoreTest`

---

## 任务 4：设备能力模型通用化（摄像头之外的麦克风、音响等外设）

### 目标

把「摄像头能力查询」升级为「设备能力查询」，一份报文覆盖：

- 摄像头（现有：节点、格式、分辨率、帧率）
- 麦克风（有无、设备名、采样率、通道、是否被占用）
- 音响/扬声器（有无、设备名、当前音量、是否静音）
- 显示设备、GPIO、USB 外设等（按设备实际支持扩展）

### 报文设计（草案，最终以协议文档为准）

```json
{
  "capability": "device",
  "capabilities": ["camera_video_capture", "audio_capture", "audio_playback", "remote_cmd"],
  "cameras": [ ... 保持现有结构 ... ],
  "audio": {
    "capture": { "available": true, "device": "default", "sample_rates": [44100, 48000], "channels": 2 },
    "playback": { "available": true, "device": "default", "volume_percent": 70, "muted": false }
  },
  "summary": { "total_cameras": 1, "total_audio_devices": 2, "probe_cost_ms": 320 },
  "request_id": "..."
}
```

- [x] 主题命名需要决策：新增 `walle/{t}/{d}/capability/query`，还是保留
      `camera/capability/query` 并向后兼容？建议新增通用主题，旧主题保留一个版本周期。
      → **决策：直接以 `capability/query|result` 替换 `camera/capability/*`，不留兼容层**
      （两端同仓同部署，与 v1 删除先例一致）。
- [x] 采集复用：麦克风探测复用任务 2/推流用的 `detect_microphone()`，不要重复实现。
      → `probe_audio_capture()` 复用 `detect_microphone()` + `_pactl_list_names("sources")`。
- [x] 音响探测：`pactl list short sinks` + `pactl get-sink-volume` / `get-sink-mute`。
- [x] 探测失败要降级为「该能力 unavailable + reason」，不能让整个报文失败。
- [x] App：`CameraCapability` 重构为 `DeviceCapability`（保留 CameraCapability 的解析兼容），
      控制页与设置页按能力列表显隐入口（例如没有音响就不显示音量控件）。
      → 新增 `model/DeviceCapability.java`（摄像头部分复用 `CameraCapability.parse`）；
      控制页新增 `tvAudioInfo` 一行展示麦克风/音响状态。
- [x] 契约测试同步：`CameraCapabilityTest` → 扩展为 `DeviceCapabilityTest`；`selftest_topics.py` 新增主题。

### 验收标准

- 有麦克风无音响的设备：能力报文如实反映，App 不显示播音入口、推流自动带音频。
- 探测超时（如 `pactl` 卡住）时能力查询仍在 2 秒内返回，缺失能力标记为 unavailable。

### 验证记录（2026-10-30）

- `./gradlew :app:testDebugUnitTest`：**54 个单测全部通过**（TopicNamesTest 10 /
  MessageRouterTest 7 / DeviceCapabilityTest 4 / CameraCapabilityTest 5 等，0 失败）。
- `python3 linux/selftest_topics.py`：**全部通过**（契约与 Android 端一致）。
- 本地端到端：`./linux/.venv/bin/python3 linux/rpi_monitor.py --env-file linux/rpi-monitor.env.example`
  启动后，用 paho 客户端向 `walle/home/c07920c42d6af684/capability/query` 发请求：
  - `request_id` 原样回显，`capability="device"`；
  - 本机（USB 摄像头带麦、双 sink）：`audio.capture.available=true`（device=default，
    sources 含 USB 麦克风），`audio.playback.available=true`（音量 100%、未静音、2 个 sink）；
  - `capabilities` 条件化正确：本机无可用摄像头（v4l2 权限拒绝），故无 `camera_video_capture`，
    有 `audio_capture` / `audio_playback` / `rtmp_push`；探测总耗时 119ms（远低于 2s 预算）。
- App 端到端：`assembleDebug` 构建成功，`adb install -r` 到 emulator-5554 后进入
  aigo 设备「控制」页，日志确认收到 `capability/result`，UI 显示
  「麦克风：default · 音响：100%」「设备未检测到摄像头」，无崩溃。
- 协议文档 `doc/MQTT_protocol_convention.md` 已同步（总表、§2.5 去重清单、§4.5 整节、§6 对照表）。

---

## 通用检查清单（每个任务完成前过一遍）

- [ ] 协议改动已同步到 `doc/MQTT_protocol_convention.md`
- [ ] 两端实现一致（App 与 `linux/rpi_monitor.py`）
- [ ] `./gradlew :app:testDebugUnitTest` 通过
- [ ] `python3 linux/selftest_topics.py` 通过
- [ ] 真机/树莓派端到端验证，记录验证命令与结果
