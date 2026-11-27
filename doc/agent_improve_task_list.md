# walle-agent 全功能改造任务清单

> 目标：让 `walle-agent` 具备「理解自然语言 → 调用全部设备 MQTT 能力 → 创建并执行定时任务」的完整能力。
>
> 协议基线：[MQTT_protocol_convention.md](MQTT_protocol_convention.md) v1.24 / v1.25
> 代码基线：`walle-agent/src/car_agent.py`（约 1817 行，2026-11-26）
> 使用方式：每完成一项，把 `[ ]` 改成 `[x]`，补上完成日期、提交或证据，并更新「进度总览」和「更新记录」。
>
> 最后更新：2026-11-27
> 当前阶段：M2 进行中（5/10）

---

## 0. 最终验收目标

完成后必须能稳定演示下面这条链路：

```text
用户：明天早上8点开始录像，到晚上9点停止录像，每天执行

agent：
  1. 理解时间、设备、动作和重复规则
  2. 创建任务并持久化
  3. 08:00 发 record/start/request，等待 record/start/response
  4. 21:00 发 record/stop/request，等待 record/stop/response
  5. 每天重复；agent 重启后任务不丢
  6. 设备离线、磁盘不足、控制权被占用时能提示并重试/跳过
  7. 任务进度通过 agent 进度主题上报
```

同时满足：

- [ ] 设备侧 18 组 request/response 全部能由 agent 调用。
- [ ] 电机、舵机、推流、录像、录像列表/删除/回放、手机音频、发现、告警、状态查询全部覆盖。
- [ ] 自然语言可触发单步动作和多步任务。
- [ ] 混合 LLM：规则 → 本地 → 在线，断网时仍可用规则/本地。
- [ ] 控制权、停车语义、幂等、超时、错误码、进度、审计全部有明确行为。
- [ ] agent 契约测试 + fake device 端到端测试通过。

---

## 0.1 最终架构（先看这张图）

```text
┌─────────────────────────────────────────────────────────────┐
│                        用户 / Web 控制台                      │
│                 自然语言（“每天8点开始录像”）                    │
└───────────────────────────┬─────────────────────────────────┘
                            │ chat/request
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ L1 对话理解层                                                 │
│  规则解析  +  本地 LLM  +  在线 LLM  +  多轮上下文               │
│  职责：听懂人话，输出“工具调用” {tool, args}                     │
└───────────────────────────┬─────────────────────────────────┘
                            │ tool calls
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ L2 动作编排层                                                 │
│  ToolSpec（每个设备动作）                                      │
│  参数校验 · 控制权 · 幂等 · 重试 · 资源锁 · 结果翻译              │
│  职责：把“开始录像”变成安全、正确、可解释的动作序列                 │
└───────────────────────────┬─────────────────────────────────┘
                            │ device request
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ L3 设备通信与状态层                                            │
│  RequestBus（request_id 配对）                                │
│  DeviceRegistry（在线/状态/录像/推流/控制权）                    │
│  MQTT 订阅/发布 · online · alarm/report · baseState/runState  │
│  职责：真正和设备说话，收结果，记住设备状态                        │
└───────────────────────────┬─────────────────────────────────┘
                            │ MQTT
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                          设备                                │
│      电机 · 舵机 · 摄像头 · 录像 · 麦克风 · 传感器               │
└─────────────────────────────────────────────────────────────┘
                            ▲
                            │ 到点触发
┌───────────────────────────┴─────────────────────────────────┐
│ L4 任务调度层                                                 │
│  SQLite 任务表 · 定时器 · daily_window/once/cron              │
│  重启恢复 · 错过补偿 · 重试 · 进度/通知                          │
│  职责：记住“每天8点录像”，到点调用 L2                            │
└─────────────────────────────────────────────────────────────┘
```

一句话理解四层：

- **L1 负责“听懂”**：把用户的话翻译成工具调用。
- **L2 负责“安排”**：把工具调用变成安全、正确的动作。
- **L3 负责“打电话”**：真正通过 MQTT 和设备通信，并记住状态。
- **L4 负责“定闹钟”**：把长期任务存起来，到点触发。

---

## 0.2 四层模型：每一层是干什么的

### L1 对话理解层

**作用**：把用户自然语言变成结构化指令。

- 输入：`"明天早上8点开始录像，到晚上9点停止，每天执行"`
- 输出：
  ```json
  {
    "tool": "task.create",
    "args": {
      "schedule": {"type": "daily_window", "start": "08:00", "stop": "21:00"},
      "actions": [{"phase": "start", "tool": "record.start"}, {"phase": "stop", "tool": "record.stop"}]
    }
  }
  ```
- 包含能力：
  - 规则解析：常见命令直接识别，快且稳；
  - 本地 LLM：隐私、省钱、断网可用；
  - 在线 LLM：复杂表达、多轮上下文更强；
  - 多轮、澄清、确认：支持“改成晚上10点”“删除最新录像”。
- **不负责**：直接发 MQTT、直接操作设备。
- **小白类比**：餐厅前台，听懂你要点什么，然后写成厨房能看懂的订单。

### L2 动作编排层

**作用**：把“开始录像”这样一个意图，变成一组安全、正确、可解释的动作。

- 检查参数：文件名是否合法、舵机角度是否越界、速度是否超限；
- 申请控制权：避免和 Web/其他客户端抢控制；
- 幂等：已经在录像时，`-4` 不应该当失败；
- 资源锁：推流和录像可能抢摄像头，要排队或互斥；
- 错误翻译：把设备的 `-5/-7/-8` 翻译成“别人在控制/磁盘满了/前面有障碍”；
- 结果整理：把设备原始 response 变成 `data` 给聊天窗口。
- **不负责**：定时、MQTT 连接细节。
- **小白类比**：餐厅领班，拿到订单后安排谁去做、先做什么、遇到冲突怎么处理。

### L3 设备通信与状态层

**作用**：真正通过 MQTT 和设备说话，并记住设备的状态。

- RequestBus：发请求、按 `request_id` 等对应回复、超时处理；
- DeviceRegistry：记住设备是否在线、是否在录像/推流、谁持有控制权、电量/声呐；
- 订阅 `online`、`alarm/report`、所有 `/response`；
- 查询 `baseState`、`runState`；
- 把设备上报的告警交给 L2/L1 提示用户。
- **不负责**：理解人话、定时。
- **小白类比**：电话线 + 档案柜。负责打电话，也负责把每次通话结果记在档案里。

### L4 任务调度层

**作用**：把“每天8点录像”这种长期任务存起来，到点触发。

- SQLite：任务和每次执行记录；
- 定时器：每天/一次/ cron /间隔；
- 重启恢复：agent 重启后任务不丢；
- 对账：用 `runState.recording` 判断是不是漏录/还在录；
- 重试/幂等：磁盘满不重试，已经在录算成功；
- 进度/通知：任务开始/成功/失败告诉用户。
- **不负责**：怎么发 MQTT（交给 L2/L3）。
- **小白类比**：闹钟 + 日历。负责记住“每天8点”，到点叫 L2 去执行。

### 四层怎么配合：以“每天8点录像”为例

1. 用户说一句话 → **L1** 解析成 `task.create`。
2. **L2** 检查参数、查设备能力（通过 L3），确认“录像功能可用”。
3. **L4** 把任务写进 SQLite，算出下次执行时间。
4. 到 08:00 → **L4** 调用 **L2**。
5. **L2** 申请控制权 → **L3** 发 `record/start/request`。
6. 设备回 `record/start/response` → **L3** 按 `request_id` 匹配 → **L2** 翻译结果。
7. **L4** 记录这次执行成功/失败，并推送进度。
8. **L1/Web** 显示“录像已开始”。

任何一层缺失，链路都会断：

- 没有 L1：听不懂“明天8点”；
- 没有 L2：不知道先申请控制权、参数是否合法；
- 没有 L3：发了请求收不到结果；
- 没有 L4：只能手动执行，不能每天自动。

### 任务层级速查

| 层 | 任务编号 |
|---|---|
| L1 对话理解层 | M4-01 ~ M4-10、M2-06、M5-07 |
| L2 动作编排层 | M0-01、M0-04、M0-05、M0-10、M2-01 ~ M2-10、M5-01 ~ M5-05 |
| L3 设备通信与状态层 | M0-02、M0-03、M0-06、M1-01 ~ M1-08、M5-04、M5-06、M5-08 |
| L4 任务调度层 | M3-01 ~ M3-10、M2-07、M3-09 |
| 跨层/工程 | M0-07 ~ M0-09、M5-09、M5-10 |

> 每张任务卡下面还会单独标注「层级」和「小白解释」，以任务卡为准。

---

## 1. 当前基线

### 1.1 已完成/已确认

- [x] 主题统一为 `walle/{t}/agent/{a}/...`（单数 `agent`）
- [x] `chat/request` 必填校验：`client_id` / `client_name` / `request_id` / `device` / `text`
- [x] `chat/response` 使用 `agent_id`，不透传内部 `mqtt/status/control`；v1.26 起可选 `data` / `result`（M2-06）
- [x] agent online 字段：`agent_id` / `agent_name` / `online` / `version_code` / `version_name` / `ts`
- [x] agent presence：retained + LWT + 正常退出补发 `online:false`
- [x] agent 身份文件：`etc/.run/agent-id`、`etc/.run/agent-name`
- [x] agent 版本：读取 `walle-agent/VERSION`
- [x] agent 作为独立控制端：设备请求注入 `client_id=agent_id`、`client_name=agent_name`
- [x] 运动控制：`car/motor/set/request` 5Hz 发送、停止零速、释放控制权
- [x] 控制权：`control/acquire|release`（目前仅运动；停止不 acquire）
- [x] agent 基本状态：`agent/{a}/baseState/query/request|response`
- [x] agent 配置修改：`agent/{a}/config/modify/request|response`（仅 `agent_name`）
- [x] agent 会话停止：`agent/{a}/chat/stop/request|response`（目前仅电机运动）
- [x] agent 进度上报：`agent/{a}/chat/progress/report` 的方法与主题已定义
- [x] agent 自身请求使用层级通配订阅
- [x] 设备协议侧：电机/舵机已恢复 request/response
- [x] 设备协议侧：统一错误码 §2.8（`0/-1..-8`）
- [x] 设备协议侧：推流主题 `video/*` → `stream/*`，字段 `video_device` → `camera_device`
- [x] 设备协议侧：`runState` 每组带 `is_supported`

### 1.2 已知问题（必须在 M0/M1 修掉）

- [x] `online` 布尔值 bug：`_on_message` 存的是整个 payload dict，`health()` 用 `bool(dict)` 恒为 true。（M0-02）
- [x] 5Hz 运动循环同步 `wait_for_publish(timeout=2)`，Broker 慢时会触发 500ms 死区停车。（M0-05）
- [ ] `self.worker`、`self.control_held` 都是单值，只能同时控制一台设备。
- [x] 订阅里有无效的 `+/response` 层；没有订阅 `device/+/alarm/report`。（M0-03）
- [ ] 对话「状态」仍返回 agent health，不是设备 `baseState` / `runState`。
- [ ] `capabilities.actions` 仍只有 `move/rotate/stop/status`。
- [x] `chat/response` 没有 `data` / `result`，无法返回房间号、录像列表、舵机位置、任务 ID。（M2-06）
- [ ] `report_progress()` 已定义但业务未调用。
- [ ] `chat/stop` 只停运动，不能停止录像/推流/任务。
- [x] `chat/stop` 在没有自己会话时会去 acquire 别人的租约，失败后返回 `-5`，无法区分“本来就没在动”。（M0-10）
- [ ] 没有调度器、任务表、任务恢复。
- [ ] `_llm_parse` 仍只接受 4 个 action，没有工具调用。
- [ ] 没有 agent 契约测试和 fake device 测试。

---

## 2. 进度总览

| 里程碑 | 状态 | 完成项 | 说明 |
|---|---|---|---|
| M0 安全、Bug 与契约 | 已完成 | 10/10 | 安全停止、控制权、PC 模拟与回归测试全部落地 |
| M1 请求总线与设备状态 | 已完成 | 8/8 | RequestBus、DeviceRegistry、状态工具/区分、能力预检、告警接入全部落地 |
| M2 核心设备动作 | 进行中 | 6/10 | record、stream、servo、电机回执、控制权/会话 per-device、chat/response 结构化已落地 |
| M3 调度与任务 | 未开始 | 0/10 | SQLite、daily_window、重启恢复、进度/事件 |
| M4 混合 LLM | 未开始 | 0/10 | 规则→本地→在线、工具调用、多轮、确认 |
| M5 全量与硬化 | 未开始 | 0/10 | 删除/回放/手机音频、多设备、capabilities、ACL、测试 |

> 状态取值：未开始 / 进行中 / 已完成 / 阻塞。完成一项就更新对应里程碑的计数。

---

## 3. 任务清单

### 3.0 任务卡阅读说明

每张任务卡格式：

- **层级**：属于 L1-L4 哪一层，跨层的会注明；
- **小白解释**：为什么需要这个功能，尽量不用技术词；
- **待办**：具体改动；
- **文件 / 验收 / 完成**：实现位置、怎么验证、完成证据。

---

### M0：安全、Bug 与契约（最高优先级）

#### M0-01 零速停车不受控制权租约限制

- **层级**：L2 动作编排层 + L3 设备通信层
- **小白解释**：原设想是“别人拿着遥控器时，任何人喊停车都能停”。讨论后确认它会变成一条捣蛋通道：任何人反复发零速，就能持续打断正在驾驶的人。
- **结论（已讨论结案，2026-11-27，不做）**：
  - 不做“任何客户端零速豁免”，不加急停锁存，不新增 `car/motor/stop/request`。
  - 租约是同租户可信客户端之间的协作锁，不是权限凭证：`client_id` 可伪造，`runState` 对所有客户端开放，Broker ACL 也只到租户级。放开停车权限防不了故意干扰，反而多出一条干扰通道。
  - 真要防“别人故意操作”，前置条件是每人独立 MQTT 账号 + Broker ACL，不在本任务范围。
  - 实际要修的是 agent 的停车语义，见 M0-10。
- 文件：—
- 验收：—
- 完成：2026-11-27 / 讨论结论，无代码改动 / 后续见 M0-10

#### M0-02 修正设备 online 布尔值

- **层级**：L3 设备通信与状态层
- **小白解释**：现在设备已经离线了，agent 可能还觉得它在线，把命令发给“空气”。修好后它知道设备到底在不在。
- [x] `_on_message` 存 `item["online"] = payload.get("online")`，不要存整个 payload。
- [x] 同时记录 `device_id` / `hostname` / `timestamp`，`health()` 的设备列表一并返回。
- [x] `health()` 使用布尔值判断在线；当前代码没有独立的 LLM 设备列表，后续新增时直接复用 `health()["devices"]`。
- 文件：`walle-agent/src/car_agent.py`
- 验收：设备发 `online:false` 后，agent 设备列表显示离线。
- 完成：2026-11-27 / e9e23ec / 模拟 online:true→false 后 `health().devices` 显示离线

#### M0-03 修订阅清单

- **层级**：L3 设备通信与状态层
- **小白解释**：agent 现在收不到设备告警；补上订阅后，设备电池低、前方有障碍、推流断了，它才能第一时间知道。
- [x] 新增 `walle/{t}/device/+/alarm/report` 订阅。
- [x] 删除无效的 `prefix + "/+/response"` 层（没有 1 层资源的 response 主题）。
- [x] 修正订阅注释层级，与 §4.20 的订阅关系一致。
- [ ] 可选：订阅 `walle/{t}/client/+/online`，用于展示控制权竞争者（暂不做）。
- 文件：`walle-agent/src/car_agent.py`、`doc/MQTT_protocol_convention.md`（§4.20）
- 验收：`WALLE_AGENT_DEBUG=1` 打印的订阅清单与协议文档一致。
- 完成：2026-11-27 / b8fdeba / 订阅清单与 §4.20 一致，含 `alarm/report`

#### M0-04 统一错误码解码器

- **层级**：L2 动作编排层 + L3 设备通信层
- **小白解释**：设备回复的 `-1/-5/-8` 像密码，用户看不懂。解码器把它们翻译成“参数错了/别人在控制/前面有障碍”，这样回复才像人话。
- [x] 新增 `decode_result(payload)`，按 §2.8 映射：
  - `0` 成功
  - `-1` 请求无效
  - `-2` 参数错误
  - `-3` 资源不可用
  - `-4` 状态冲突
  - `-5` 控制权被占用
  - `-6` 执行失败
  - `-7` 存储空间不足
  - `-8` 安全联锁
- [x] `-5` 时提取 `control_holder`；`-8` 时提示障碍。
- [x] 控制权路径与设备 `/response` 已接入；任务/LLM 工具当前不存在，后续新增时直接复用该解码器。
- 文件：`walle-agent/src/car_agent.py`（后续可拆到 `walle_protocol.py`）
- 验收：任意 response 都能输出 `ok/error/cause/holder`。
- 完成：2026-11-27 / fca5b4c / 8 个错误码、holder、障碍、缺失 result、未知码断言通过

#### M0-05 运动发布路径拆分

- **层级**：L2 动作编排层 + L3 设备通信层
- **小白解释**：车需要每 0.2 秒收到一次“继续走”的命令，否则会以为你松手了而停下。如果每次都等网络确认，就会卡顿；这个改动让发送不被网络拖慢。
- [x] 新增 `publish_motor()`：只 publish，不等待 PUBACK。
- [x] 运动 response 异步处理：
  - `result=0` 不提示；
  - `result=-5` 立即停止并提示 holder；
  - `result=-8` 立即停止并提示前方障碍；
  - `result=-3` 立即停止并提示底盘链路不可用。
- [x] 中止原因记录在 `health().last_motion_error`（含 result / cause / holder），日志告警一次。
- [x] 运动 response 不按 5Hz 写 INFO 日志，只记录异常或采样。
- 文件：`walle-agent/src/car_agent.py`
- 验收：5Hz 发送稳定；Broker 延迟不会触发 500ms 死区停车。
- 完成：2026-11-27 / 27d583a / `publish_motor` 不等待 PUBACK；-5/-8 触发 abort

#### M0-06 统一 request_id 生成器

- **层级**：L3 设备通信与状态层
- **小白解释**：每条命令都要有唯一编号，设备回复时才能对上号；否则会把“A 的结果”当成“B 的结果”。
- [x] 新增 `new_request_id(tool)`：`agent-{tool}-{8hex}`（`secrets.token_hex(4)`）。
- [x] 所有 agent 主动发起的 device request 使用新生成器；同一运动会话保持单个 session id（方案 A，不逐帧加序号）。
- [x] 所有 device request/cmd 注入 `client_id` / `client_name`（`_publish` / `publish_motor` 已覆盖）；客户端请求仍原样回显客户端自己的 request_id。
- 文件：`walle-agent/src/car_agent.py`
- 验收：抓包可见 `client_id` / `client_name`；request_id 不重复。
- 完成：2026-11-27 / c1ecc39 / 格式与 2000 次唯一性断言通过；三类请求均带身份字段

#### M0-07 PC 节点模拟小车环境

- **层级**：跨层（开发/测试环境）
- **小白解释**：没有真车时，希望设备端也能在 PC 上完整跑起来。做法是加一个“PC 节点”：在 PC 环境下只启动它，不启动 `car_control` 里依赖真实硬件的节点；由它模拟电机、舵机和传感器。这样 `net_mqtt_bridge` 一行都不用改，Web/agent 看到的仍是一台正常设备。摄像头、麦克风、扬声器直接用 PC 自带的，不做模拟。
- **方案**：
  - 新增 PC 节点 `walle-device/ros2_ws/src/simulation/pc_robot`，对 `net_mqtt_bridge` 提供与真机一致的话题/服务：
    - 电机：订阅 `/cmd_vel`（让 bridge 的订阅者检查通过），维护运动状态并按 `cmd_vel_timeout`（默认 0.6s）停车；可选 `/odom` 未实现。
    - 舵机：提供 `/ros_robot_controller/pwm_servo/get_state` 服务，订阅 `/ros_robot_controller/pwm_servo/set_state`，保存并返回模拟位置/偏移。
    - 传感器：发布 `/ros_robot_controller/battery`（mV）、`/sonar_controller/get_distance`（mm）；IMU/按键未实现。
    - 故障注入：运行时用 `ros2 param set` 修改 `battery_mv` / `sonar_distance_mm` / `sonar_valid` / `battery_enabled`。
  - 启动时按环境选择：PC 环境只启动 `pc_robot` + `net_mqtt_bridge`（以及 PC 自带且存在的摄像头/音频相关节点），不启动 `ros_robot_controller`、`sonar_controller`、`controller/mecanum` 等真实底盘节点；真机环境保持现状。
  - 环境判定：`env_walle.sh` 导出 `WALLE_PLATFORM=robot|pc`（有 `/proc/device-tree` 为 robot，否则 pc，可显式覆盖）；`bringup.launch.py` 同时接受 `platform:=pc`。
  - `net_mqtt_bridge` 不做任何修改；PC 模式通过 `pc_robot/config/net_mqtt_bridge_pc.yaml` 覆盖 `support_*`。
  - 其他落地细节：
    - `bringup` 的 PC 分支仅在 `walle_camera.conf` 存在且设备节点存在时启动 `usb_cam` / `video_webrtc_bridge` / `record_video`；
    - 语音盒子与 `large_models` 在 PC 默认关闭，可用 `enable_speech:=true` / `enable_ai:=true` 开启；
    - `startup_check` 按平台校验必需节点/话题，PC 跳过蜂鸣器/PWM 播报；
    - `env_walle.sh` 新增 PC venv 自动定位、按 Ubuntu 版本选择 ROS2 发行版（22.04→Humble，24.04→Jazzy）、修复 `WALLE_RUNTIME_ENV` 恒为 debug 的问题。
- [x] 新增 PC 节点包与 launch。
- [x] `bringup` 增加 PC 环境分支，只启动 PC 节点，不启动 `car_control` 硬件节点。
- [x] PC 节点实现电机/舵机/电池/声呐接口与可注入故障。
- [x] 摄像头/麦克风/扬声器直接复用 PC 设备，不模拟。
- 文件：`walle-device/ros2_ws/src/simulation/pc_robot/`、`walle-device/ros2_ws/src/bringup/launch/bringup.launch.py`、`walle-device/ros2_ws/src/bringup/bringup/startup_check.py`、`walle-device/scripts/env_walle.sh`
- 验收（ROS 侧已验证，MQTT 端到端待补测）：PC 上启动后不启动任何真实底盘节点；`car/motor/set`、`car/servo/set|status`、`runState`、`alarm/report` 的 ROS 链路正常；`net_mqtt_bridge` 无改动。设备 `online` 与 MQTT 端到端需配置 Broker 后验证。
- 完成：2026-11-27 / 0b87a50 / `colcon build` 通过；PC 有/无摄像头启动通过；`/cmd_vel`、舵机 set/get、电池、声呐链路计数正确；真机分支冒烟通过；`startup_check` 正常退出

#### M0-08 控制权与安全停止回归测试

- **层级**：跨层（测试工程）
- **小白解释**：保证“别人控制时不能抢”“agent 只停自己的车”“断线会自己停”这些规则不会被后续改动破坏。
- **依赖**：M0-07 的 PC 模拟环境已落地（`platform:=pc`），端到端用例可直接在此基础上编写。
- [x] 用例：Web holder 时 agent 运动请求 → `-5`。
  - `walle-device/.../test_control_lease.py::test_car_motor_control_denied_uses_minus_five`
  - `walle-agent/test/test_safety_regression.py::test_web_holder_agent_move_is_control_occupied`
- [x] 用例：Web holder 时 agent `chat/stop` → `no_session`，设备端收不到零速。
  - `walle-agent/test/test_safety_regression.py::test_web_holder_agent_chat_stop_is_no_session_without_zero`
- [x] 用例：agent 自己运动中 `chat/stop` → 零速 + 释放租约。
  - `walle-agent/test/test_safety_regression.py::test_agent_own_motion_chat_stop_sends_zero_and_releases`
- [x] 用例：agent 运动中断线 → 设备 500ms 死区停车。
  - `walle-device/.../test_safety_stop.py::test_deadman_stops_after_500ms_without_command`
  - `walle-device/.../test_safety_stop.py::test_deadman_republishes_before_timeout`
- [x] 用例：obstacle `-8` → 立即停车。
  - `walle-device/.../test_safety_stop.py::test_obstacle_minus_8_stops_car_immediately`
- [x] chat/stop 收尾去重：`stop()` 统一发送 3 次零速并释放租约；`_run_motion.finally` 通过 `_stop_owns_cleanup` 跳过重复收尾（零速帧 6 → 3）。
  - `walle-agent/test/test_safety_regression.py::test_real_motion_chat_stop_sends_only_one_zero_burst`
  - 正常跑完与 `-8` 中止仍由运动线程收尾，见 `test_real_motion_normal_completion_still_zeroes_and_releases`、`test_real_motion_motor_error_still_worker_cleanup`
- 文件：`walle-agent/src/car_agent.py`、`walle-agent/test/conftest.py`、`walle-agent/test/test_safety_regression.py`、`walle-device/ros2_ws/src/net/net_mqtt_bridge/test/test_safety_stop.py`
- 验收：上述 5 个用例全部通过（agent 6 个 + device 3 个，共 9 个测试函数）。
- 完成：2026-11-27 / 本次提交 / `pytest walle-agent/test` 6 passed；`pytest .../test_safety_stop.py` 3 passed；chat/stop 零速帧由 6 降为 3

#### M0-09 文档同步

- **层级**：跨层（文档）
- **小白解释**：代码改了、协议没改，后面的人会按旧文档调用，必然出错。文档和代码要一起更新。
- [x] 更新 `doc/MQTT_protocol_convention.md`：`chat/stop` 的 `no_session` 语义、租约不承担急停职责的说明、订阅修正。
- [x] 更新 `doc/agent_protocol_tasks.md` 或本清单的关联说明。
- 文件：`doc/`
- 验收：协议、实现、测试三者一致。
- 完成：2026-11-27 / 本次提交 / §2.4、§4.20、§4.24 与 agent_protocol_tasks、已知问题清单已同步

#### M0-10 agent 的停车只停自己的会话

- **层级**：L2 动作编排层 + L3 设备通信层
- **小白解释**：你正拿着“遥控器”（控制权）开小车时，别人——包括 agent——不应该把你的车停掉。agent 的“停止”只负责停它自己发起的运动；手上没有会话时就直接说“没有可停的会话”，而不是报一个看不懂的“控制权被占用”。
- [x] `stop()` 不再先 `_acquire_control`：只有存在自己的运动 worker，或 `control_held` 指向该设备时，才向设备发零速。
- [x] 没有自己的会话且未持有租约 → 返回 `ok=True, stopped=False, released=False, cause="no_session"`，不向设备发任何命令。
- [x] 有自己会话时顺序固定：`abort.set()` → `worker.join()` → 3 次零速（间隔 50ms）→ 尝试 `release`；`release` 失败不回滚已完成的停车。
- [x] `stopped` / `released` 按实际结果回报：worker 收尾时已释放租约也算 `released=true`。
- [x] `_serve_agent_stop` 删除 `control_occupied` 分支；`no_session` 返回 `result=0`、`cause=no_session`。
- [x] Web「停止会话」收到 `no_session` 时提示“当前没有 agent 会话”，不要显示成功或超时。
- 文件：`walle-agent/src/car_agent.py`、`web/car_ctl_by_mqtt.html`、
  `doc/MQTT_protocol_convention.md`（§4.19 与 `chat/stop` 补充说明）
- 验收：
  1. Web 持有租约行驶时，agent 收到 `chat/stop` → 返回 `result=0, cause=no_session`，设备端收不到任何 `car/motor/set/request`，车继续按 Web 操作。
  2. agent 自己发起的运动进行中 → `chat/stop` → 车立即停住，租约已释放。
  3. 日志不再出现 `agent stop ... result=-5`。
- 完成：2026-11-27 / c27ba79 / 无会话返回 no_session；有会话零速 + 释放租约

---

### M1：请求总线与设备状态

#### M1-01 RequestBus：通用请求-应答总线

- **层级**：L3 设备通信与状态层
- **小白解释**：agent 发命令后要等设备回话。现在没有统一的“等回话”机制，所以录像、推流的结果收不到；这个功能就是“发出去、等回复、对上号”。
- [x] `pending: {request_id: Future}`。
- [x] 发布前注册 pending，收到 response 按 `request_id` 唤醒。
- [x] 校验 response topic 与期望 topic、device 一致。
- [x] 超时、重复 response、`control_denied`、设备离线时让 pending 失败。
- [x] 查询类（baseState/runState/servo status/record list）不申请租约。
  - `RequestBus` 本身不参与租约；`link_test` 的 baseState 查询已直接走总线，不调用 `_acquire_control`。
- 实现：
  - 新增 `walle-agent/src/walle_protocol.py`：`RequestBus`、`PendingRequest`、`REQUEST_TO_RESPONSE`；`decode_result` / `new_request_id` 同步迁入并保持原导入名兼容。
  - `Agent` 接入 `request_bus`：`_on_message` 按 `request_id` 分发一对一 response；`online:false` 调 `fail_device`；`_on_disconnect` / `shutdown` 调 `fail_all`。
  - `_control_request` 与 `link_test` 改为走 RequestBus；`car/motor/set` 5Hz 持续帧仍走 `publish_motor`，不进总线。
  - 重复/迟到 response 立即从 pending 移除，只保留第一个匹配回包；错误 device/topic 计入 mismatch 并丢弃。
- 文件：`walle-agent/src/walle_protocol.py`、`walle-agent/src/car_agent.py`、`walle-agent/test/test_request_bus.py`、`walle-agent/test/test_agent_request_bus.py`
- 验收：任意 request 都能拿到对应 response；迟到/重复 response 被丢弃。
- 完成：2026-11-27 / 本次提交 / 新增 12 个 RequestBus 单元测试 + 3 个 Agent 集成测试；`pytest walle-agent/test` 21 passed；M0-08 device 3 passed；未修改 MQTT 协议

#### M1-02 DeviceRegistry：设备状态缓存

- **层级**：L3 设备通信与状态层
- **小白解释**：agent 要像档案柜一样记住每台设备：在不在线、在录什么、在推流吗、谁在控制、电量多少。这样用户一问就能答，不用每次现查。
- [x] 字段：`online`、`online_ts`、`baseState`、`runState`、`recording`、`stream`、
  `phone_audio`、`motor`、`servo`、`battery`、`sonar`、`control_holder`、`last_alarm`。
- [x] 设备上线/下线/重启更新缓存。
- [x] 过期数据按 `is_supported` / `available` 处理，不沿用旧值。
- 实现：
  - `Agent.devices` 扩展为设备状态缓存：baseState/runState 原文 + 各 runState 分组 + control_holder + last_alarm。
  - `_on_message` 自动更新缓存：`online`、`baseState/query/response`、`runState/query/response`、`alarm/report`、`control/acquire|release/response`。
  - 离线或重启（`online:false`、再次上线、device_id 变化）时清空 baseState/runState/分组，避免沿用旧值。
  - 新增 `Agent.device_state(device)` 快照：按 `WALLE_DEVICE_STATE_TTL_SEC`（默认 15s）过期；`is_supported=false` / `available=false` 的分组值置空；离线时状态分组全部为 None。
  - `health(device)` 增加 `device_state`，方便后续状态对话直接读缓存。
- 文件：`walle-agent/src/car_agent.py`、`walle-agent/test/test_device_registry.py`、`walle-agent/test/test_agent_request_bus.py`
- 验收：对话能回答录像/推流/电池/声呐/控制权，不依赖现场查询。
  - 缓存层已提供上述数据；对话工具在 M1-03/M1-04/M1-06 接入。
- 完成：2026-11-27 / 本次提交 / 新增 8 个缓存单元测试 + 1 个 `_on_message` 缓存集成测试；`pytest walle-agent/test` 30 passed；未修改 MQTT 协议

#### M1-03 device.base_state 工具

- **层级**：L3 设备通信与状态层
- **小白解释**：回答“CPU 多少、内存够不够、磁盘满没满、网络快不快”这些设备的“身体状况”。
- [x] 发 `baseState/query/request`，解析 `system/cpu/memory/disk/network/usb`。
- [x] 超时 6s；部分字段缺失不报错。
- [x] 对话「设备状态」返回摘要。
- 实现：
  - 新增 `Agent.device_base_state(device)`：走 RequestBus 发 `baseState/query/request`，超时 6s，不申请控制权。
  - 解析摘要：IP、CPU%（核数/温度）、内存%（已用/总量）、磁盘%（剩余）、网络上下行、USB 数量、运行时长、系统/内核。
  - 新增 `parse_intent` 路由：包含“设备状态/设备信息/CPU/内存/磁盘/运行多久”等词时走 `device_base_state`；单独的“状态”仍保持原语义，留给 M1-06。
  - `execute` / `_reply_text` / LLM 动作白名单同步支持 `device_base_state`。
- 文件：`walle-agent/src/car_agent.py`、`walle-agent/test/test_agent_request_bus.py`
- 验收：能回答 CPU/内存/磁盘/IP/运行时长。
- 完成：2026-11-27 / 本次提交 / 新增 3 个 baseState 工具与意图测试；`pytest walle-agent/test` 33 passed；未修改 MQTT 协议

#### M1-04 device.run_state 工具

- **层级**：L3 设备通信与状态层
- **小白解释**：回答“在录像吗、在推流吗、电量多少、前面多远、谁在控制”这些“正在做什么”的问题。
- [x] 发 `runState/query/request`，解析 `stream/phone_audio/recording/motor/servo/battery/sonar/control_holder`。
- [x] 尊重 `is_supported=false`：该分组不展示、不允许后续控制。
- [x] `available=false` 时显示「无效」，不沿用旧值。
- 实现：
  - 新增 `Agent.device_run_state(device)`：走 RequestBus 发 `runState/query/request`，超时 6s，不申请控制权。
  - 复用 `_normalize_run_group`：`is_supported=false` 的分组从摘要和回复中隐藏，并记入 `unsupported`；`available=false` 的电池/声呐显示“无效”，值置 None。
  - 新增 `Agent.device_supports(device, feature)`，供后续动作在控制前检查能力。
  - 对话路由：“录像/推流/电量/电池/声呐/前方多远/控制权/运行状态”等走 `device_run_state`；单独的“状态”仍保持原语义，留给 M1-06。
  - `execute` / `_reply_text` / LLM 动作白名单同步支持 `device_run_state`。
- 文件：`walle-agent/src/car_agent.py`、`walle-agent/test/test_agent_request_bus.py`
- 验收：能回答「在录像吗」「在推流吗」「电量多少」「前方多远」「谁持有控制权」。
- 完成：2026-11-27 / 本次提交 / 新增 4 个 runState 工具与意图测试；`pytest walle-agent/test` 37 passed；未修改 MQTT 协议

#### M1-05 device.discover 工具

- **层级**：L3 设备通信与状态层
- **小白解释**：设备列表平时靠“上线公告”自动出现；有时需要主动扫一遍，看看有没有新设备或漏掉的设备。
- [x] 发布租户级 `discovery/query/request`，携带 `client_id/client_name`。
- [x] 收集 3s 内的 `device/{d}/discovery/query/response`。
- [x] retained online 为主，discovery 为辅。
- 实现：
  - 新增 `Agent.discover_devices(timeout=3.0)`：发布 `walle/{tenant}/discovery/query/request`，payload 带 `client_id/client_name`。
  - `_on_message` 在 discovery 收集窗口内记录 `device/{d}/discovery/query/response`；窗口结束自动停止收集。
  - 结果以 `Agent.devices` 的 retained online 为主，discovery 应答补充身份/型号；`source` 标记 `online`/`discovery`/`both`。
  - 无设备返回 `ok=True, count=0, reply="未发现设备。"`；并发扫描返回 busy；发布失败返回明确错误。
  - 对话“刷新设备/发现设备/设备列表/有哪些设备”走 `device_discover`，且**不要求已选设备**；`execute` / `_reply_text` / LLM 动作白名单同步支持。
- 文件：`walle-agent/src/car_agent.py`、`walle-agent/test/test_agent_request_bus.py`
- 验收：对话「刷新设备」能发现新设备；无设备时不报错。
- 完成：2026-11-27 / 本次提交 / 新增 4 个 discovery 与意图测试；`pytest walle-agent/test` 41 passed；未修改 MQTT 协议

#### M1-06 真实「状态」与 agent/设备状态区分

- **层级**：L1 对话理解层 + L3 设备通信与状态层
- **小白解释**：现在说“状态”，agent 回答的是自己的健康，不是车的状态。要分清“车状态”“agent 状态”“服务健康”，用户问什么就答什么。
- [x] 对话「状态」默认查设备 `baseState` + `runState`。
- [x] 「agent 状态」查 `agent/baseState`。
- [x] 「服务健康」查 agent health。
- [x] 删除 `execute(status)` 返回 agent health 的旧行为。
- 实现：
  - `execute(status)` 改为 `device_status()`：同时查 baseState + runState，任一成功即返回部分结果，失败侧在回复中注明。
  - `agent_status()` 返回本机 `machine_state()`（与 `agent/baseState/query/response` 同源），包含 CPU/内存/磁盘/IP/运行时长。
  - `service_health()` 返回 agent health 摘要：MQTT 状态、在线设备数、运动状态、chat 请求/错误、LLM 开关。
  - 意图路由：
    - “状态” → `status`（设备 base+run）；
    - “agent 状态/你的状态” → `agent_status`；
    - “服务健康/服务状态/健康检查” → `service_health`；
    - “设备状态”仍走 `device_base_state`，“运行状态”仍走 `device_run_state`。
  - “agent 状态/服务健康”不要求已选设备；`execute` / `_reply_text` / LLM 动作白名单同步支持。
- 文件：`walle-agent/src/car_agent.py`、`walle-agent/test/test_agent_request_bus.py`
- 验收：用户说「状态」得到设备状态；说「agent 状态」得到机器状态。
- 完成：2026-11-27 / 本次提交 / 新增 6 个状态区分与聚合测试；`pytest walle-agent/test` 47 passed；未修改 MQTT 协议

#### M1-07 能力缓存与 is_supported

- **层级**：L3 设备通信与状态层（L2 使用）
- **小白解释**：设备可能没有摄像头、没有录像功能。先问清楚“你支持吗”，再下命令，避免白忙一场。
- [x] 从 `runState` 缓存 `recording/stream/phone_audio/motor/servo/battery/sonar` 的 `is_supported`。
- [x] 控制前预检：不支持则直接返回明确原因，不发请求。
- [x] 任务创建前预检录像能力。
- 实现：
  - 新增 `Agent.capabilities(device)`：从 DeviceRegistry 的 runState 缓存读取 7 个分组的 `is_supported`，返回 True/False/None。
  - 新增 `Agent.preflight_features(device, features, refresh=False)`：明确 `False` 拒绝并给出 `unsupported`；未知能力默认放行，`refresh=True` 时先查一次 runState 再判断。
  - 新增 `Agent.preflight_recording_task(device)`：录像能力 `False` 或无法确认时拒绝，并返回 `capability_unsupported` / `capability_unknown` 及中文原因。
  - `execute(move/rotate)` 增加 motor 能力预检：`motor.is_supported=false` 时直接返回 `unsupported`，不申请控制权、不发运动请求。
- 文件：`walle-agent/src/car_agent.py`、`walle-agent/test/test_agent_request_bus.py`
- 验收：`recording.is_supported=false` 时创建录像任务被拒绝并说明。
- 完成：2026-11-27 / 本次提交 / 新增 5 个能力缓存与预检测试；`pytest walle-agent/test` 52 passed；未修改 MQTT 协议

#### M1-08 alarm/report 接入

- **层级**：L3 设备通信与状态层
- **小白解释**：设备会主动报告“电池低了”“前面有障碍”“推流断了”。agent 收到后要能提醒用户，必要时停车。
- [x] 订阅 `device/+/alarm/report`。
- [x] 更新 DeviceRegistry：`last_alarm`、电池、障碍、推流/音频中断。
- [x] `obstacle_near` 时若 agent 正在前进，立即停车并提示。
- [x] `battery_low/critical` 时提示；critical 可停止运动。
- 实现：
  - 复用已有 `device/+/alarm/report` 订阅；`_on_message` 更新缓存后调用 `_handle_alarm`。
  - `_update_alarm` 更新 `last_alarm`，并按告警类型更新 `battery`（电压）、`sonar`（距离）、`stream`（中断）、`phone_audio`（中断）；记录 `alarm_groups` 以便 runState 过期后仍能展示告警带来的新鲜值。
  - 新增 `Agent.last_alarm_notice` 与 `health().last_alarm`，时间线内可提示用户。
  - 新增 `Agent.recent_alarm(device=None)`：对话「告警/最新告警」返回最近一次告警；不要求已选设备。
  - 障碍联锁：`obstacle_near` 时若 agent 正在 forward 运动，后台线程立即 `stop()`；非前进方向不停。
  - 电池联锁：`battery_low` 且 `level=critical` 时停止当前运动；warning 只提示。
- 文件：`walle-agent/src/car_agent.py`、`walle-agent/test/test_agent_request_bus.py`
- 验收：模拟障碍告警，agent 停车并回复；模拟电池低压，agent 提示。
- 完成：2026-11-27 / 本次提交 / 新增 5 个告警缓存、联锁与对话测试；`pytest walle-agent/test` 57 passed；未修改 MQTT 协议

---

### M2：核心设备动作

#### M2-01 record.start / stop / list

- **层级**：L2 动作编排层（底层由 L3 发请求）
- **小白解释**：这是你最想要的“定时录像”的核心：开始录、停止录、查看录了哪些文件。
- [x] `record/start/request`：支持 `segment_sec`；`0/-4/-6/-7` 统一解码。
- [x] `record/stop/request`：`0/-4/-6`；`-4` 视为已停止（幂等）。
- [x] `record/list/query/request`：返回 `records[]`、`used_bytes`、`limit_bytes`。
- [x] 控制类请求前 acquire，结束后 release。
- 实现：
  - 新增 `Agent.record_start(device, segment_sec=0)`：能力预检 → acquire → `record/start/request`（12s）→ release。
    - `0` 成功；`-4` 已在录制（幂等成功，`already_recording=true`）；`-7` 磁盘不足（`no_space`）；其余按统一错误码解码。
  - 新增 `Agent.record_stop(device)`：能力预检 → acquire → `record/stop/request` → release；`-4` 视为已停止（`already_stopped=true`）。
  - 新增 `Agent.record_list(device)`：只读查询，不申请控制权，返回 `records/count/used_bytes/limit_bytes`。
  - 对话路由：「开始录像[30分钟]」「停止录像」「查看录像/录像列表」；`segment_sec` 支持“分钟/分/秒”。
  - `execute` / `_reply_text` / LLM 动作白名单同步支持。
- 文件：`walle-agent/src/car_agent.py`、`walle-agent/test/test_agent_request_bus.py`
- 验收：对话「开始录像/停止录像/查看录像」可用；磁盘不足提示明确。
- 完成：2026-11-27 / 本次提交 / 新增 6 个录像动作、幂等、查询与控制权测试；`pytest walle-agent/test` 63 passed；未修改 MQTT 协议

#### M2-02 stream.start / stop

- **层级**：L2 动作编排层
- **小白解释**：远程看画面需要设备把摄像头画面“推”到流媒体服务器。这个功能让 agent 能开始/停止推流，并拿到房间号给 Web 播放。
- [x] `stream/start/request`：无参数；返回 `room_number`、`camera_device`、`action`。
- [x] `action=already_streaming` 时复用当前房间号，不重启。
- [x] `stream/stop/request`：可带 `room_number`；`0/-4`。
- [x] `is_supported=false` 或 `-3` 时提示设备无推流能力/摄像头。
- 实现：
  - 新增 `Agent.stream_start(device)`：能力预检 → acquire → `stream/start/request`（20s）→ release。
    - 返回 `room_number/camera_device/action/pid/started_at`；
    - `action=already_streaming` 时 `already_streaming=true`，复用当前房间号，不重启。
  - 新增 `Agent.stream_stop(device, room_number=None)`：能力预检 → acquire → `stream/stop/request` → release。
    - `0` 已停止；`-4` 无推流时幂等成功；带房间号且不一致时返回 `room_mismatch`，不误停；
    - `-3` 提示无推流能力/摄像头。
  - 对话路由：「开始推流/开始直播」「停止推流/停止直播」；「在推流吗」仍走运行状态。
  - `execute` / `_reply_text` / LLM 动作白名单同步支持。
- 文件：`walle-agent/src/car_agent.py`、`walle-agent/test/test_agent_request_bus.py`
- 验收：对话「开始推流」返回房间号；「停止推流」可用；重复开始不重启。
- 完成：2026-11-27 / 本次提交 / 新增 8 个推流启动、复用、停止、房间号校验、能力拦截与意图测试；`pytest walle-agent/test` 71 passed；未修改 MQTT 协议

#### M2-03 servo.status / servo.set

- **层级**：L2 动作编排层
- **小白解释**：摄像头的上下左右是舵机控制的。用户说“抬高一点”，agent 要先查当前位置，再算新位置，再设置。
- [x] `car/servo/status/query/request`：`ids`、`include_offset`；超时 6s/10s。
- [x] `car/servo/set/request`：绝对位置、`duration`；返回 `applied/skipped`。
- [x] ID 映射：ID1=上下、ID2=左右；范围 800–2200。
- [x] 「抬高一点」等相对语义：先 status 再 set。
- 实现：
  - 新增 `Agent.servo_status(device, ids=[1,2], include_offset=False)`：只读查询，不申请控制权；6s，带 offset 时 10s；`-3` 保留部分结果并标记 `partial`。
  - 新增 `Agent.servo_set(device, servos, duration=1.0)`：能力预检 → acquire → `car/servo/set/request` → release；返回 `applied/skipped/duration`；越界项由设备放入 `skipped`，agent 原样提示。
  - 新增 `Agent.servo_center(device)`：ID1/ID2 设为 1500。
  - 新增 `Agent.servo_relative(device, servo_id, delta)`：先 `servo_status` 读当前位置，再 `servo_set` 绝对位置；结果附带 `previous_position/target/delta`。
  - ID 映射常量 `SERVO_NAMES`；范围 800–2200、中位 1500、步进 100（若实机“抬高”方向相反，调整 `SERVO_STEP` 符号即可）。
  - 对话路由：「舵机回中」「抬高一点/降低一点」「舵机状态」「舵机1到1400」。
  - `execute` / `_reply_text` / LLM 动作白名单同步支持。
- 文件：`walle-agent/src/car_agent.py`、`walle-agent/test/test_agent_request_bus.py`
- 验收：对话「舵机回中」「抬高一点」可用；越界项在 `skipped` 中提示。
- 完成：2026-11-27 / 本次提交 / 新增 8 个舵机查询、设置、回中、相对调整、越界提示、能力拦截与意图测试；`pytest walle-agent/test` 79 passed；未修改 MQTT 协议

#### M2-04 电机运动回执处理

- **层级**：L2 动作编排层 + L3 设备通信与状态层
- **小白解释**：现在电机每条命令都有回复了，agent 不能发了不管；遇到“别人在控制”或“前面有障碍”要立即停车。
- [x] `_run_motion` 使用 `publish_motor()`，不等待 PUBACK。
- [x] 处理 `car/motor/set/response`：`-5/-8/-3` 立即停止并提示。
- [x] `result=0` 不逐条提示，避免 5Hz 刷屏。
- [x] 运动结束补发 3 次零速并 release。
- 实现：
  - `_run_motion` 以 5Hz 调用 `publish_motor()`，只 publish，不等待 PUBACK。
  - `_on_motor_response` 只处理当前运动 `request_id`；`-5/-8/-3` 对应 `control_denied/safety/unavailable` 时立即 `abort.set()`，记录 `last_motion_error`（result/error/cause/holder）并 warning。
  - `result=0` 不打印 INFO，仅 debug，避免 5Hz 刷屏。
  - 运动结束 `finally` 补发 3 次零速并 release；`chat/stop` 触发时由 `stop()` 统一收尾，避免重复。
  - 提示口径：日志 + `health().last_motion_error`；短会话不主动推送对话消息。
- 文件：`walle-agent/src/car_agent.py`、`walle-agent/test/test_safety_regression.py`
- 验收：运动过程中 obstacle `-8` 能立即停车；控制权被抢 `-5` 能提示。
- 完成：2026-11-27 / 本次提交 / `-8` 既有用例 + 新增 `-5/-3` 参数化用例；`pytest walle-agent/test` 81 passed；未修改 MQTT 协议

#### M2-05 控制权 per-device

- **层级**：L2 动作编排层 + L3 设备通信与状态层
- **小白解释**：如果有两台车，不能一台在动另一台就不能动。每台设备要有自己的“遥控器”和“工作线程”。
- [x] `control_held` 改为 `{device: lease}`。
- [x] `worker` 改为 `{device: MotionSession}`。
- [x] 每设备资源锁：`motion`、`camera`、`servo`、`playback`。
- [x] `chat/stop` 支持按 device 停止。
- 实现：
  - `control_held` 从单个 lease 改为 `{device: lease}`；`_acquire_control/_release_control/_on_message` 按设备读写，`control_denied` 只清理对应设备，两台设备可同时持有租约。
  - 新增 `MotionSession`（`device/intent/client_name/request_id/abort/thread/stop_owns_cleanup/resource_lock`）；`self.sessions` 改为 `{device: MotionSession}`，运动线程、abort、回执匹配全部按设备隔离。
  - 新增 `self.resource_locks` 与 `_resource_lock_for/_acquire_resource/_release_resource`，按设备维护 `motion/camera/servo/playback` 四类锁；统一非阻塞 `acquire(False)`，冲突返回 `resource_busy`，不申请控制权、不影响其它设备。
  - 锁位置：`motion` 在 `execute` 起步；`camera` 在 `record_start/record_stop/stream_start/stream_stop`；`servo` 在 `servo_set`；`playback` 预留给 M5-02 录像播放。
  - `stop(client_name, device=None)`：指定 `device` 只停该设备的会话与租约；不传则遍历 `sessions` 与 `control_held` 全部停止，返回 `devices` 汇总。
  - `health()` 增加 `active_devices`、`control_devices`；`active/moving` 在多设备时不再误报单设备。
- 文件：`walle-agent/src/car_agent.py`、`walle-agent/test/test_safety_regression.py`、`walle-agent/test/test_agent_request_bus.py`
- 验收：两台设备可分别运动/录像；一台被占用不影响另一台。
- 完成：2026-11-27 / 本次提交 / 新增两台设备并行运动、按设备停止、摄像头资源隔离用例；`pytest walle-agent/test` 84 passed；未修改 MQTT 协议

#### M2-06 chat/response 结构化返回

- **层级**：L1 对话理解层 + L2 动作编排层
- **小白解释**：聊天窗口只会显示文字，但“房间号”“录像文件列表”“舵机结果”需要结构化数据才能展示和播放。这个功能让 agent 把结果整理好交给 Web。
- [x] 增加可选 `data`：`room_number`、`records`、`servos`、`recording`、`task`。
- [x] 增加可选 `result`：设备原始 response（过大时截断/摘要）。
- [x] 更新 `doc/MQTT_protocol_convention.md` §4.20。
- [x] Web 兼容读取 `data`。
- 文件：`walle-agent/src/car_agent.py`、`web/car_ctl_by_mqtt.html`、`doc/`
- 验收：推流返回房间号；录像列表返回文件名；舵机返回 applied/skipped。
- 完成：2026-11-27 / 本次改动 / `data` 覆盖 room_number/records/servos/recording/task；`result` 超过 4096 字节转摘要；新增 4 个 chat/response 用例；`pytest walle-agent/test` 88 passed；未修改 MQTT topic

#### M2-07 长动作进度上报

- **层级**：L2 动作编排层 + L4 任务调度层
- **小白解释**：推流/录像启动可能要几秒；没有进度，前端 20 秒就以为超时了。进度上报让用户知道“还在做，不是卡死”。
- [ ] `record.start`、`stream.start`、`record.play.start`、servo.status 调用 `report_progress()`。
- [ ] stage：`accepted` / `started` / `waiting` / `completed` / `failed`。
- [ ] 进度带 `request_id`、`device`、`message`。
- 文件：`walle-agent/src/car_agent.py`
- 验收：Web 能在 20s 内看到进度，不误判超时。
- 完成：<!-- 日期 / commit / 证据 -->

#### M2-08 动作层单元测试

- **层级**：跨层（测试工程）
- **小白解释**：保证每个动作的参数拼装正确、错误码翻译正确，不会因为改一个功能弄坏另一个。
- [ ] 每个动作的 payload 构造、结果解码、幂等、错误码。
- [ ] `record -4/-7`、`stream -3/-4`、`servo skipped`、`motor -5/-8`。
- [ ] `chat/response` 的 `data` 字段。
- 文件：`walle-agent/test/`
- 验收：`pytest -q walle-agent/test` 全部通过。
- 完成：<!-- 日期 / commit / 证据 -->

#### M2-09 动作层文档

- **层级**：跨层（文档）
- **小白解释**：以后别人接手，或者你自己过几个月再看，能知道每个动作怎么调用、返回什么。
- [ ] 在 `walle-agent/docs/` 增加动作清单/示例。
- [ ] 更新本清单的协议覆盖矩阵。
- 文件：`walle-agent/docs/`、本文件
- 验收：新成员能按文档调用每个动作。
- 完成：<!-- 日期 / commit / 证据 -->

#### M2-10 资源互斥与冲突提示

- **层级**：L2 动作编排层
- **小白解释**：推流和录像可能抢同一个摄像头；同时开可能黑屏或录像失败。这个功能让它们排队或互斥，并告诉用户原因。
- [ ] 推流/录像共用摄像头时按实测结果决定互斥或并行。
- [ ] `record/play` 单路播放锁。
- [ ] 运动、舵机、摄像头冲突时给出明确提示。
- 文件：`walle-agent/src/car_agent.py`
- 验收：同时发起冲突动作时不会互相破坏状态。
- 完成：<!-- 日期 / commit / 证据 -->

---

### M3：调度与任务

#### M3-01 SQLite 任务存储

- **层级**：L4 任务调度层
- **小白解释**：定时任务要存在硬盘里，agent 重启后不能忘。SQLite 就是一个小型本地数据库，不需要额外服务器。
- [ ] `tasks` 表：id、name、device、timezone、schedule_json、actions_json、enabled、created_at、updated_at、next_run_at。
- [ ] `task_runs` 表：run_id、task_id、phase、scheduled_at、started_at、finished_at、request_id、result、cause、error。
- [ ] WAL 模式；默认 `walle-agent/data/agent.db`。
- 文件：`walle-agent/src/car_agent.py`（或 `scheduler.py`）
- 验收：创建任务后重启 agent，任务仍在。
- 完成：<!-- 日期 / commit / 证据 -->

#### M3-02 调度线程

- **层级**：L4 任务调度层
- **小白解释**：每天 8 点要有个“闹钟”去触发任务。调度线程就是 agent 内部的闹钟。
- [ ] 独立线程，1s tick。
- [ ] 时区 `Asia/Shanghai`，可配置 `TASK_TIMEZONE`。
- [ ] 到期任务分发到 worker；`(task_id, phase, scheduled_at)` 唯一防重。
- 文件：`walle-agent/src/car_agent.py`
- 验收：到点触发一次，不重复。
- 完成：<!-- 日期 / commit / 证据 -->

#### M3-03 任务类型

- **层级**：L4 任务调度层
- **小白解释**：除了“每天”，用户可能说“只明天一次”“工作日”“每隔两小时”。支持多种类型才能听懂更多话。
- [ ] `daily_window`：每天 start-stop 窗口。
- [ ] `once`：一次性。
- [ ] `cron`：cron 表达式。
- [ ] `interval`：间隔执行。
- 文件：`walle-agent/src/car_agent.py`
- 验收：四种类型都有单元测试。
- 完成：<!-- 日期 / commit / 证据 -->

#### M3-04 task.create / list / cancel / pause / resume

- **层级**：L4 任务调度层（L1 提供自然语言入口）
- **小白解释**：用户要能“创建任务、查看任务、取消任务、暂停任务”。没有这些，任务只能靠改数据库。
- [ ] 任务工具 + 自然语言入口。
- [ ] 创建前查 `runState.recording.is_supported`。
- [ ] 返回 task_id、首次执行时间、下次执行时间。
- 文件：`walle-agent/src/car_agent.py`
- 验收：对话创建/查看/取消任务可用。
- 完成：<!-- 日期 / commit / 证据 -->

#### M3-05 任务执行：录像 start/stop

- **层级**：L4 任务调度层（调用 L2 动作）
- **小白解释**：到点真正去发“开始录像/停止录像”命令，而不是只响个闹钟。
- [ ] start：acquire（按策略）→ `record/start/request` → 解码 → 记录 run。
- [ ] stop：`record/stop/request` → `0/-4` 视为成功。
- [ ] `-7` 磁盘不足不重试；`-5` 通知 holder；`-6` 重试。
- 文件：`walle-agent/src/car_agent.py`
- 验收：每天 8 点开始、21 点停止稳定执行。
- 完成：<!-- 日期 / commit / 证据 -->

#### M3-06 控制权策略 shared / exclusive

- **层级**：L4 任务调度层 + L2 动作编排层
- **小白解释**：如果 Web 正在控制，agent 的定时任务要不要抢？`shared` 是让路，`exclusive` 是独占。要能配置，避免打架。
- [ ] `shared`：动作前 acquire，结束后 release；被占则通知/跳过。
- [ ] `exclusive`：任务期间持有，每 5 分钟用查询续租，结束时 release。
- [ ] 配置 `TASK_CONTROL_MODE`，默认 `shared`。
- 文件：`walle-agent/src/car_agent.py`
- 验收：两种策略都有测试；被占时行为符合配置。
- 完成：<!-- 日期 / commit / 证据 -->

#### M3-07 重启对账

- **层级**：L4 任务调度层
- **小白解释**：agent 重启/断网后，不知道录像是不是还在跑。对账就是“醒来后先看一眼”，避免漏录或一直录。
- [ ] 启动时加载任务，重算 `next_run_at`。
- [ ] 查 `runState.recording`：窗口内未录补启动；窗口外仍在录补停止。
- [ ] 错过触发点按 `TASK_MISSED_GRACE_SEC` 处理。
- 文件：`walle-agent/src/car_agent.py`
- 验收：录像中重启 agent，任务恢复且不会重复/遗漏。
- 完成：<!-- 日期 / commit / 证据 -->

#### M3-08 重试与幂等

- **层级**：L4 任务调度层
- **小白解释**：设备说“已经在录”不是失败；“磁盘满了”重试也没用。要能区分，不然会做无用功。
- [ ] `record.start -4` 已在录 → 成功；`-7` → 不重试；`-6` → 重试。
- [ ] `record.stop -4` 未录制 → 成功。
- [ ] 重试次数 `TASK_RETRY_MAX`，退避。
- 文件：`walle-agent/src/car_agent.py`
- 验收：模拟各错误码，任务状态正确。
- 完成：<!-- 日期 / commit / 证据 -->

#### M3-09 任务进度/事件

- **层级**：L4 任务调度层 + L1 展示
- **小白解释**：任务成功/失败/跳过要通知用户，不然用户不知道到底录没录。
- [ ] 复用 `chat/progress/report`（`request_id=task-...`）或新增 `agent/{a}/task/report`。
- [ ] 字段：`task_id`、`run_id`、`stage`、`progress`、`message`、`cause`。
- [ ] Web 订阅并展示任务状态。
- 文件：`walle-agent/src/car_agent.py`、`web/`、`doc/`
- 验收：任务开始/成功/失败/跳过都有通知。
- 完成：<!-- 日期 / commit / 证据 -->

#### M3-10 调度测试

- **层级**：跨层（测试工程）
- **小白解释**：用“假时钟”测试，不用真的等到明天 8 点；保证任务逻辑正确。
- [ ] 用可注入时钟测试 daily_window、once、cron、interval。
- [ ] 测试重启恢复、错过触发、磁盘不足、控制权被占。
- 文件：`walle-agent/test/`
- 验收：`pytest -q walle-agent/test` 全部通过。
- 完成：<!-- 日期 / commit / 证据 -->

---

### M4：混合 LLM

#### M4-01 规则层扩展

- **层级**：L1 对话理解层
- **小白解释**：常见话（前进、开始录像）不应依赖大模型，规则直接识别，快、稳、便宜。
- [ ] 录像：开始/停止/查看/删除/播放。
- [ ] 推流：开始/停止。
- [ ] 舵机：回中/抬高/降低/左右。
- [ ] 状态：设备状态/agent 状态/服务健康。
- [ ] 任务：每天/一次性/取消/查看。
- 文件：`walle-agent/src/car_agent.py`
- 验收：常见命令不依赖 LLM 即可执行。
- 完成：<!-- 日期 / commit / 证据 -->

#### M4-02 工具 schema

- **层级**：L1 对话理解层
- **小白解释**：告诉大模型“你只能用这些工具，参数长这样”，它就不容易乱编。
- [ ] 覆盖 M1-M3 全部工具；JSON Schema。
- [ ] 每个工具包含 name、description、args schema、required、enum。
- [ ] LLM 只输出工具调用，不生成 topic/payload。
- 文件：`walle-agent/src/`（建议拆 `llm_router.py`）
- 验收：schema 校验失败时能回退/反问。
- 完成：<!-- 日期 / commit / 证据 -->

#### M4-03 本地模型客户端

- **层级**：L1 对话理解层
- **小白解释**：本地模型在自己电脑上跑，隐私好、不花钱、断网也能用。
- [ ] Ollama/vLLM，OpenAI 兼容。
- [ ] 支持 `tools` 或 `response_format=json_object`。
- [ ] 超时、重试、错误日志。
- 文件：`walle-agent/src/llm_router.py`
- 验收：本地 Qwen2.5-7B/14B 能解析常用命令。
- 完成：<!-- 日期 / commit / 证据 -->

#### M4-04 在线模型客户端

- **层级**：L1 对话理解层
- **小白解释**：在线模型更聪明，适合复杂说法；但需要网络和 API Key。
- [ ] DeepSeek/Qwen 等 OpenAI 兼容。
- [ ] API Key 从配置读取，不写日志。
- [ ] 超时、熔断、降级。
- 文件：`walle-agent/src/llm_router.py`
- 验收：复杂语句能走在线模型；失败时回退。
- 完成：<!-- 日期 / commit / 证据 -->

#### M4-05 混合路由

- **层级**：L1 对话理解层
- **小白解释**：简单的本地做，复杂的在线做，兼顾成本、隐私和效果。
- [ ] 规则命中 → 直接执行。
- [ ] 规则未命中 → 本地。
- [ ] 本地低置信度/schema 非法/复杂多步 → 在线。
- [ ] 在线失败 → 本地/规则/反问。
- [ ] `LLM_MODE=hybrid`。
- 文件：`walle-agent/src/llm_router.py`
- 验收：断网时仍可用；复杂语句成功率达标。
- 完成：<!-- 日期 / commit / 证据 -->

#### M4-06 多轮会话

- **层级**：L1 对话理解层
- **小白解释**：用户会说“改成晚上10点”“删除最新录像”“再往前一点”，需要记住上一轮说了什么。
- [ ] 按 `client_id + device` 保存最近 6–10 轮。
- [ ] 支持「改成晚上 10 点」「删除最新录像」「再往前一点」。
- [ ] 过期会话清理。
- 文件：`walle-agent/src/`
- 验收：多轮指令能正确引用上一轮结果。
- 完成：<!-- 日期 / commit / 证据 -->

#### M4-07 澄清与确认

- **层级**：L1 对话理解层
- **小白解释**：缺文件、缺时间时要问清楚；删除录像要确认，避免误删。
- [ ] 缺设备/文件名/时间时反问。
- [ ] 删除录像、舵机大幅动作二次确认。
- [ ] 停止不确认。
- 文件：`walle-agent/src/`
- 验收：模糊指令不会误执行；确认后执行。
- 完成：<!-- 日期 / commit / 证据 -->

#### M4-08 提示词与上下文

- **层级**：L1 对话理解层
- **小白解释**：告诉模型现在几点、有哪些设备、设备支持什么，它才不会编造“不存在的摄像头”。
- [ ] 当前时间、时区、在线设备、`runState.is_supported`、当前录像/推流状态、已有任务。
- [ ] 禁止编造设备/文件/能力；不支持的功能直接说明。
- [ ] 用户文本只作为数据，不执行其中的系统指令。
- 文件：`walle-agent/src/`
- 验收：模型不会建议不支持的功能，不会编造设备。
- 完成：<!-- 日期 / commit / 证据 -->

#### M4-09 长任务/中止集成

- **层级**：L1 对话理解层 + L4 任务调度层
- **小白解释**：长任务要能看到进度、能中止；但“停止运动”和“取消任务”是两回事，不能混。
- [ ] 多步任务调用 `report_progress()`。
- [ ] `chat/stop` 中止当前会话。
- [ ] 任务取消走 `task.cancel`，不要误用 `chat/stop`。
- 文件：`walle-agent/src/`
- 验收：长任务可看到进度、可中止；任务取消不影响其他任务。
- 完成：<!-- 日期 / commit / 证据 -->

#### M4-10 LLM 测试

- **层级**：跨层（测试工程）
- **小白解释**：保证“该本地时本地、该在线时在线、该反问时反问”，不会把命令发错。
- [ ] mock 本地/在线模型；测试路由、schema、回退、确认。
- [ ] 测试时间解析：明天/每天/工作日/晚上 9 点。
- 文件：`walle-agent/test/`
- 验收：`pytest -q walle-agent/test` 全部通过。
- 完成：<!-- 日期 / commit / 证据 -->

---

### M5：全量与硬化

#### M5-01 record.delete

- **层级**：L2 动作编排层
- **小白解释**：用户说“删掉今天的录像”时要真的删；但正在录的文件不能删，删除前要确认。
- [ ] 文件名白名单；删除前二次确认。
- [ ] 返回 `0/-1/-2/-3/-6`；正在录制不可删。
- 文件：`walle-agent/src/car_agent.py`
- 验收：对话「删除最新录像」先确认再删除；错误码可读。
- 完成：<!-- 日期 / commit / 证据 -->

#### M5-02 record.play.start / stop

- **层级**：L2 动作编排层
- **小白解释**：把录像文件变成可以播放的流，返回播放地址；用户才能在网页里点开看。
- [ ] `record/play/start/request`：`filename`、`offset_sec`；返回 `flv_url` / `whep_url`。
- [ ] `record/play/stop/request`：停止当前播放。
- [ ] Web 能直接播放返回的 URL。
- 文件：`walle-agent/src/`、`web/`
- 验收：对话「播放最新录像」返回可播放地址；「停止播放」可用。
- 完成：<!-- 日期 / commit / 证据 -->

#### M5-03 phone.audio.send / stop

- **层级**：L2 动作编排层
- **小白解释**：把手机麦克风的声音送到车喇叭；但 MQTT 只能开关，真正的声音要 Web/App 用 WebRTC 推，agent 不能代替。
- [ ] 仅控制面：`phone/audio/send|stop/request`。
- [ ] 明确媒体面由 Web/App WHIP 负责，agent 不能代替推音频。
- [ ] `is_supported=false` 时提示。
- 文件：`walle-agent/src/`
- 验收：对话「打开麦克风」能说明限制；Web 媒体会话存在时可开关。
- 完成：<!-- 日期 / commit / 证据 -->

#### M5-04 alarm 深度处理

- **层级**：L3 设备通信与状态层 + L2 动作编排层
- **小白解释**：电池低、前方障碍、推流断了，agent 要知道并做出反应；但刹车必须由设备本地保证，不能只靠 MQTT。
- [ ] 障碍：停车 + 提示；不依赖 MQTT 刹车（设备本地安全）。
- [ ] 电池低压/极低：提示；极低可停车。
- [ ] 推流/音频中断：更新状态并提示。
- 文件：`walle-agent/src/`
- 验收：告警场景都有明确回复，不刷屏。
- 完成：<!-- 日期 / commit / 证据 -->

#### M5-05 多设备

- **层级**：L2 动作编排层 + L3 设备通信与状态层
- **小白解释**：两台车要能分别控制，不能一台在动另一台就动不了。
- [ ] per-device worker/lease/registry。
- [ ] 「所有设备」扇出可选。
- [ ] 任务绑定 device。
- 文件：`walle-agent/src/`
- 验收：两台设备并行控制互不影响。
- 完成：<!-- 日期 / commit / 证据 -->

#### M5-06 capabilities 全量

- **层级**：L3 设备通信与状态层（L1/Web 使用）
- **小白解释**：告诉前端“这台设备能做什么”，按钮该显示还是禁用；避免用户点了没反应。
- [ ] `actions`：全部工具。
- [ ] `resources`：baseState/runState/record/stream/servo/task。
- [ ] `limits`、`llm_mode`、`scheduler`。
- [ ] Web 按 capabilities / `is_supported` 禁用入口。
- 文件：`walle-agent/src/`、`web/`
- 验收：online 报文能力与实际一致。
- 完成：<!-- 日期 / commit / 证据 -->

#### M5-07 chat 协议扩展

- **层级**：L1 对话理解层 + L2 动作编排层
- **小白解释**：让 Web 既能“说人话”，也能直接调用具体功能，并拿到结构化结果；两种方式共用一套动作。
- [ ] `chat/request` 增加可选 `resource` + `params`（结构化调用）。
- [ ] `chat/response` 增加 `data` / `result` / `accepted` / `task_id`。
- [ ] 更新协议版本与 Web 解析。
- 文件：`doc/MQTT_protocol_convention.md`、`walle-agent/src/`、`web/`
- 验收：自然语言与结构化调用都能工作。
- 完成：<!-- 日期 / commit / 证据 -->

#### M5-08 ACL 验证

- **层级**：L3 设备通信与状态层
- **小白解释**：Broker 权限不够，代码写得再对也发不出去；要确认 agent 能发/能收所有需要的主题。
- [ ] agent 可 publish：所有 device `/request`、`discovery/query/request`、
  `agent/{self}/online|chat/response|progress|task/report`。
- [ ] agent 可 subscribe：所有 device `/response`、`online`、`alarm/report`、
  `agent/{self}/+/request`、`agent/{self}/+/+/request`。
- [ ] Web 可 subscribe agent 主题。
- 文件：`doc/`、Broker ACL
- 验收：发布/订阅无 ACL 拒绝；跨租户仍被拒绝。
- 完成：<!-- 日期 / commit / 证据 -->

#### M5-09 文档与部署

- **层级**：跨层（文档/部署）
- **小白解释**：把新配置、数据库路径、LLM 模式写清楚，部署时不用猜。
- [ ] 更新 `walle-agent/docs/agent_deploy.md`：新增配置、任务库路径、LLM 模式。
- [ ] 更新 `doc/agent_protocol_tasks.md` 勾选状态。
- [ ] 更新本清单的覆盖矩阵和进度总览。
- 文件：`doc/`、`walle-agent/docs/`
- 验收：文档与代码一致。
- 完成：<!-- 日期 / commit / 证据 -->

#### M5-10 端到端回归

- **层级**：跨层（测试工程）
- **小白解释**：单个功能能用不代表串起来能用；最后要完整走一遍用户真实场景。
- [ ] 场景：状态 → 录像 → 列表 → 停止 → 删除 → 播放 → 停止播放。
- [ ] 场景：推流 → 停止 → runState 状态正确。
- [ ] 场景：每天 8 点录像任务跨重启。
- [ ] 场景：控制权冲突、磁盘不足、障碍、断网、LLM 回退。
- 文件：`walle-agent/test/`
- 验收：全部通过；日志无 ERROR/异常堆栈。
- 完成：<!-- 日期 / commit / 证据 -->

---

## 4. 全量协议覆盖矩阵

> 目标：18 组设备 request/response 全部由 agent 覆盖；`online`、`alarm/report` 为订阅上报。

| # | 协议 | 主题 | 里程碑 | 所属层 | 当前状态 |
|---|---|---|---|---|---|
| 1 | 设备发现 | `discovery/query/request` → `device/{d}/discovery/query/response` | M1-05 | L3 | 未实现 |
| 2 | 基础状态 | `baseState/query/request` → `.../response` | M1-03 | L3 | 仅 link_test |
| 3 | 运行状态 | `runState/query/request` → `.../response` | M1-04 | L3 | 未实现 |
| 4 | 获取控制权 | `control/acquire/request` → `.../response` | M2-05 | L2/L3 | 部分（仅运动） |
| 5 | 释放控制权 | `control/release/request` → `.../response` | M2-05 | L2/L3 | 部分（仅运动） |
| 6 | 开始推流 | `stream/start/request` → `.../response` | M2-02 | L2 | 未实现 |
| 7 | 停止推流 | `stream/stop/request` → `.../response` | M2-02 | L2 | 未实现 |
| 8 | 手机音频开 | `phone/audio/send/request` → `.../response` | M5-03 | L2 | 未实现 |
| 9 | 手机音频关 | `phone/audio/stop/request` → `.../response` | M5-03 | L2 | 未实现 |
| 10 | 开始录像 | `record/start/request` → `.../response` | M2-01 | L2 | 未实现 |
| 11 | 停止录像 | `record/stop/request` → `.../response` | M2-01 | L2 | 未实现 |
| 12 | 录像列表 | `record/list/query/request` → `.../response` | M2-01 | L2/L3 | 未实现 |
| 13 | 删除录像 | `record/delete/request` → `.../response` | M5-01 | L2 | 未实现 |
| 14 | 播放录像 | `record/play/start/request` → `.../response` | M5-02 | L2 | 未实现 |
| 15 | 停止播放 | `record/play/stop/request` → `.../response` | M5-02 | L2 | 未实现 |
| 16 | 电机控制 | `car/motor/set/request` → `.../response` | M2-04 | L2/L3 | 部分（5Hz/回执未处理） |
| 17 | 舵机状态 | `car/servo/status/query/request` → `.../response` | M2-03 | L2/L3 | 未实现 |
| 18 | 舵机设置 | `car/servo/set/request` → `.../response` | M2-03 | L2 | 未实现 |
| 19 | 设备在线 | `device/{d}/online` | M0-02 | L3 | 已订阅但有 bug |
| 20 | 告警上报 | `device/{d}/alarm/report` | M1-08 | L3 | 未订阅 |
| 21 | agent 对话 | `agent/{a}/chat/request|response` | M4 | L1 | 部分（4 action） |
| 22 | agent 在线 | `agent/{a}/online` | — | L3 | 已完成 |
| 23 | agent 基本状态 | `agent/{a}/baseState/query/request|response` | — | L3 | 已完成 |
| 24 | agent 配置修改 | `agent/{a}/config/modify/request|response` | — | L3 | 已完成 |
| 25 | agent 会话停止 | `agent/{a}/chat/stop/request|response` | M3 | L2/L3 | 部分（仅运动） |
| 26 | agent 进度 | `agent/{a}/chat/progress/report` | M2-07/M3-09 | L2/L4 | 方法有，未使用 |
| 27 | 客户端在线 | `client/{client_id}/online` | 可选 | L3 | 未订阅 |

---

## 5. 关键验收场景

### 5.1 设备状态

- [ ] 「状态」→ 设备 `baseState` + `runState` 摘要。
- [ ] 「agent 状态」→ `agent/baseState`。
- [ ] 「电量」「声呐」「在录像吗」「在推流吗」「谁在控制」都能回答。

### 5.2 录像

- [ ] 「开始录像」→ `record/start`，返回文件名。
- [ ] 「停止录像」→ `record/stop`，返回文件名。
- [ ] 「查看录像」→ `record/list`，返回最近文件列表。
- [ ] 「删除最新录像」→ 二次确认 → `record/delete`。
- [ ] 「播放最新录像」→ `record/play/start`，返回 `flv_url/whep_url`。
- [ ] 「停止播放」→ `record/play/stop`。
- [ ] 磁盘不足 `-7`、已在录 `-4`、未录制 `-4` 都有正确提示。

### 5.3 推流

- [ ] 「开始推流」→ `stream/start`，返回 `room_number`、`camera_device`。
- [ ] 重复「开始推流」→ `already_streaming`，不重启。
- [ ] 「停止推流」→ `stream/stop`。
- [ ] 无摄像头 `-3` 有明确提示。

### 5.4 舵机

- [ ] 「舵机回中」→ `car/servo/set` 1500。
- [ ] 「抬高一点」→ status → set，返回 applied/skipped。
- [ ] 越界值在 `skipped` 中提示。

### 5.5 定时任务

- [ ] 「明天早上 8 点开始录像，晚上 9 点停止，每天执行」→ 创建任务。
- [ ] 08:00 自动 `record/start`，21:00 自动 `record/stop`。
- [ ] agent 重启后任务仍在。
- [ ] 设备离线/磁盘不足/控制权被占时提示并重试/跳过。
- [ ] 「取消每天录像」→ `task.cancel`。
- [ ] 「查看任务」→ 任务列表和下次执行时间。

### 5.6 安全与控制权

- [ ] 非 holder 的零速停车被拒绝，车继续由 holder 控制。
- [ ] agent 没有自己会话时 `chat/stop` → `no_session`，不干扰 holder。
- [ ] agent 运动时 Web acquire → agent 收到 `-5` 并停车/提示。
- [ ] 前方障碍 `-8` → 立即停车并提示。
- [ ] `chat/stop` 能中止当前运动并释放控制权。

### 5.7 LLM

- [ ] 规则命中不调用 LLM。
- [ ] 复杂语句调用本地/在线模型。
- [ ] 断网时规则/本地可用。
- [ ] 删除录像需确认。
- [ ] 模型不会编造不支持的功能/设备/文件。

---

## 6. 更新记录

| 日期 | 任务 | 变更 | 证据 |
|---|---|---|---|
| 2026-11-26 | 初始版本 | 建立 M0-M5 任务清单 | 本文件 |
| 2026-11-26 | 文档增强 | 增加最终架构、四层模型、每项任务的层级与小白解释 | 本文件 |
| 2026-11-27 | M0-07 | PC 模拟环境落地：pc_robot、bringup platform 分支、startup_check 平台化、env_walle 平台判定与 22.04/Humble 兼容 | 0b87a50 |
| 2026-11-27 | M0-08 | 新增控制权与安全停止回归：agent 6 例 + device 3 例；chat/stop 收尾去重（零速帧 6→3） | 本次提交 |
| 2026-11-27 | M1-01 | RequestBus 落地：pending/超时/重复/离线失败，control 与 link_test 接入；M1 进度 1/8 | 本次提交 |
| 2026-11-27 | M1-02 | DeviceRegistry 落地：online/baseState/runState/告警/控制权缓存，TTL 与 is_supported/available 处理；M1 进度 2/8 | 本次提交 |
| 2026-11-27 | M1-03 | device_base_state 工具落地：baseState 查询、摘要与对话路由；M1 进度 3/8 | 本次提交 |
| 2026-11-27 | M1-04 | device_run_state 工具落地：录像/推流/电量/声呐/控制权解析、is_supported/available 处理；M1 进度 4/8 | 本次提交 |
| 2026-11-27 | M1-05 | device_discover 工具落地：租户级扫描、3s 收集、online 优先合并、无设备容错；M1 进度 5/8 | 本次提交 |
| 2026-11-27 | M1-06 | 状态区分落地：状态=设备 base+run、agent 状态=本机 machine_state、服务健康=health；M1 进度 6/8 | 本次提交 |
| 2026-11-27 | M1-07 | 能力缓存与预检落地：capabilities、preflight_features、录像任务预检、motor 控制前拦截；M1 进度 7/8 | 本次提交 |
| 2026-11-27 | M1-08 | alarm/report 接入：告警缓存、障碍/低压联锁停车、recent_alarm 对话；M1 完成 8/8 | 本次提交 |
| 2026-11-27 | M2-01 | record.start/stop/list 落地：能力预检、控制权 acquire/release、-4 幂等、列表查询；M2 进度 1/10 | 本次提交 |
| 2026-11-27 | M2-02 | stream.start/stop 落地：房间号复用、房间号一致性校验、-3 无摄像头提示；M2 进度 2/10 | 本次提交 |
| 2026-11-27 | M2-03 | servo.status/set 落地：6s/10s 查询、applied/skipped、回中、先查后设相对调整；M2 进度 3/10 | 本次提交 |
| 2026-11-27 | M2-04 | 电机运动回执补齐：-5/-8/-3 中止、零速+release、成功帧静默；新增 -5/-3 用例；M2 进度 4/10 | 本次提交 |
| 2026-11-27 | M2-05 | 控制权与运动会话 per-device：`control_held`/`sessions` 按设备、四类资源锁、chat/stop 按设备；M2 进度 5/10 | 本次提交 |

---

## 7. 更新约定

1. **先改协议，再改代码**：涉及主题/字段/错误码时，先更新 `MQTT_protocol_convention.md`。
2. **一项一勾选**：完成一项就把 `[ ]` 改成 `[x]`，在「完成」处写日期、commit、日志或测试命令。
3. **更新进度总览**：每完成一项，更新对应里程碑的完成计数和状态。
4. **不要虚勾**：没有验收证据的项保持未完成；部分完成写「进行中」并注明剩余项。
5. **保持矩阵同步**：新增/修改设备协议时，同步更新第 4 节覆盖矩阵。
6. **测试优先**：动作层/调度/LLM 路由都要有对应测试；没有测试的项不能标记完成。
7. **安全优先**：停车语义、控制权、`-8` 障碍、`-7` 磁盘不足必须先处理。
8. **按层排查**：出问题时先判断是哪一层：
   - 听不懂 → L1；
   - 参数/权限/幂等错 → L2；
   - 收不到设备回复/状态不对 → L3；
   - 定时不准/重启丢任务 → L4。
