# walle-agent 动作清单与调用示例

> 适用版本：walle-agent 0.1.1，协议文档 v1.26。
> 实现入口：`walle-agent/src/car_agent.py` 的 `Agent` 类。
> 协议契约以 `doc/MQTT_protocol_convention.md` 为准，本文只说明 agent 动作层怎么调用、返回什么。

## 1. 层次与调用方式

```
Web/App --chat/request--> L1 规则/LLM 解析 --> L2 Agent.execute() --> L3 RequestBus/MQTT --> 设备
                                                |
                                                +--> chat/response（reply + data + result）
                                                +--> chat/progress/report（长动作进度）
```

- 自然语言入口：`handle_chat(client_name, device, text)`，返回 `reply` 与动作结果。
- 结构化入口：`Agent.execute(device, intent, client_name)`，`intent` 至少包含 `action`。
- 动作方法（如 `agent.record_start(device)`）可单独调用，但不会产生 `chat/progress/report`
  （进度依赖对话线程上下文）。

```python
import car_agent

agent = car_agent._get_agent()
out = agent.execute("0580a65014af4d0a", {
    "action": "move", "direction": "forward",
    "distance_m": 1.0, "speed_mps": 0.5,
}, "Web A")
print(out["reply"])
```

## 2. 通用返回约定

| 字段 | 说明 |
|---|---|
| `ok` | 动作是否成功；`record -4`、`stream -4` 等幂等场景可能为 `true` |
| `action` | 动作名，与 intent 的 `action` 对应 |
| `device` | 目标设备 |
| `request_id` | agent 主动请求编号 `agent-{tool}-{8hex}` |
| `error` | agent 错误码；设备错误码见 §2.8 映射表 |
| `cause` | 人类可读原因 |
| `result` | 设备原始 `result` 数值（动作内部使用） |
| `payload` | 设备原始 response，`chat/response` 会透传到 `result`（超过 4096 字节转摘要） |
| `reply` | 自然语言回复 |

常见 agent 错误码：`mqtt_not_connected`、`timeout`、`device_offline`、
`control_occupied`、`control_failed`、`resource_busy`、`unsupported`、
`invalid_response`、`parameter_error`。

设备 `result` 与 agent 错误码映射：

| result | error | 含义 |
|---|---|---|
| 0 | — | 成功 |
| -1 | `invalid_request` | 请求无效 |
| -2 | `bad_param` | 参数错误 |
| -3 | `unavailable` | 资源不可用 |
| -4 | `conflict` | 状态冲突（已在录/未录/未推流） |
| -5 | `control_denied` | 控制权被其他客户端占用 |
| -6 | `failed` | 执行失败 |
| -7 | `no_space` | 存储空间不足 |
| -8 | `safety` | 前方有障碍物 |

## 3. 动作速查表

| action | Agent 方法 | 设备主题 | 控制权 | 进度上报 |
|---|---|---|---|---|
| `move` / `rotate` | `execute()` → `_run_motion` | `car/motor/set/request` | acquire/release | 否 |
| `stop` | `stop()` | `car/motor/set/request`（零速） | 仅释放自己的 | 否 |
| `status` | `device_status()` | `baseState/query` + `runState/query` | 否 | 否 |
| `device_base_state` | `device_base_state()` | `baseState/query/request` | 否 | 否 |
| `device_run_state` | `device_run_state()` | `runState/query/request` | 否 | 否 |
| `device_discover` | `discover_devices()` | `discovery/query/request` | 否 | 否 |
| `agent_status` | `agent_status()` | 本机采集 | 否 | 否 |
| `service_health` | `service_health()` | 本地 health | 否 | 否 |
| `alarm_status` | `recent_alarm()` | 缓存 `alarm/report` | 否 | 否 |
| `record_start` | `record_start()` | `record/start/request` | acquire/release + camera 锁 | 是 |
| `record_stop` | `record_stop()` | `record/stop/request` | acquire/release + camera 锁 | 否 |
| `record_list` | `record_list()` | `record/list/query/request` | 否 | 否 |
| `stream_start` | `stream_start()` | `stream/start/request` | acquire/release + camera 锁 | 是 |
| `stream_stop` | `stream_stop()` | `stream/stop/request` | acquire/release + camera 锁 | 否 |
| `servo_status` | `servo_status()` | `car/servo/status/query/request` | 否 | 是 |
| `servo_set` | `servo_set()` | `car/servo/set/request` | acquire/release + servo 锁 | 否 |
| `servo_center` | `servo_center()` | `car/servo/set/request` | acquire/release + servo 锁 | 否 |
| `servo_relative` | `servo_relative()` | status → set | acquire/release + servo 锁 | 是（status 阶段） |

> `record/delete`、`record/play/start|stop`、`phone/audio/*` 尚未实现（M5）。

## 4. 运动控制

### 4.1 move / rotate

```json
{"action":"move","direction":"forward","distance_m":1.0,"speed_mps":0.5}
{"action":"rotate","direction":"left","angle_deg":90}
```

- `direction`：move 支持 `forward/backward/left/right`；rotate 支持 `left/right`。
- 参数在 `validate_intent()` 中夹紧：超过上限返回 `ok:false`；速度超限会带 `warning`。
- 运动线程以 5Hz 发 `car/motor/set/request`，不等待 PUBACK；收到 `-5/-8/-3` 立即停车。
- 典型返回：`{"ok":true,"accepted":true,"action":"move","duration_sec":2.0}`。

### 4.2 stop

```json
{"action":"stop"}
```

- 只停止 agent 自己的会话，不抢占别人的控制权。
- 没有会话时幂等：`ok:true, stopped:false, released:false, cause:"no_session"`。

## 5. 状态与发现

| action | intent | 关键返回 |
|---|---|---|
| `status` | `{"action":"status"}` | `baseState`、`runState`、`summary`、`reply` |
| `device_base_state` | `{"action":"device_base_state"}` | `baseState`、`summary`（CPU/内存/磁盘/网络/USB） |
| `device_run_state` | `{"action":"device_run_state"}` | `runState`、`summary`（录像/推流/电量/声呐/控制权） |
| `device_discover` | `{"action":"device_discover"}` | `devices`、`count`、`reply` |
| `agent_status` | `{"action":"agent_status"}` | `summary`（agent 所在机器） |
| `service_health` | `{"action":"service_health"}` | `health`、`reply` |
| `alarm_status` | `{"action":"alarm_status"}` | `alarm`（最近一条告警或 `null`） |

说明：

- `status` 会同时查 `baseState` 与 `runState`；一侧失败时另一侧仍返回，`reply` 注明失败项。
- `device_discover` 以 retained `online` 为主，3 秒内的 `discovery/query/response` 为辅。
- 状态查询不申请控制权。

## 6. 录像

### 6.1 record_start

```json
{"action":"record_start","segment_sec":0}
```

```python
out = agent.execute("dev-1", {"action": "record_start", "segment_sec": 1800}, "Web A")
# {"ok":true,"recording":true,"already_recording":false,
#  "filename":"rec_20261127_140000.fmp4","started_at":"...","play_url":"..."}
```

- `segment_sec`：分段秒数，0 表示设备默认。
- `-4` 已在录像：幂等成功，`already_recording:true`。
- `-7` 空间不足：`error:"no_space"`，不重试。
- 进度：`accepted → waiting → completed/failed`。

### 6.2 record_stop

```json
{"action":"record_stop"}
```

- `-4` 当前未录制：幂等成功，`already_stopped:true`。
- 返回 `filename`（设备实际停止的文件）。

### 6.3 record_list

```json
{"action":"record_list"}
```

```python
out = agent.record_list("dev-1")
# {"ok":true,"count":2,"records":[{"filename":"...","size_bytes":100,
#  "created_at":"...","duration_sec":10,"play_url":"http://..."}],
#  "used_bytes":300,"limit_bytes":1000}
```

- 查询类动作，不申请控制权。
- `chat/response` 的 `data.records` 最多 50 条，超出附 `records_total`。

## 7. 推流

### 7.1 stream_start

```json
{"action":"stream_start"}
```

```python
out = agent.stream_start("dev-1")
# {"ok":true,"streaming":true,"already_streaming":false,
#  "room_number":"ab12cd34","camera_device":"/dev/video0","pid":123}
```

- 设备返回 `action=already_streaming` 时复用当前房间号，不重启。
- `-3` 无推流能力/摄像头：`error:"unavailable"`。
- 进度：`accepted → waiting → completed/failed`。

### 7.2 stream_stop

```json
{"action":"stream_stop"}
```

- 可选带 `room_number`：

```python
out = agent.stream_stop("dev-1", "ab12cd34")
```

- `-4` 且房间号一致：幂等成功，`already_stopped:true`。
- `-4` 且房间号不一致：`error:"room_mismatch"`，不会误停别的推流。

## 8. 舵机

### 8.1 servo_status

```json
{"action":"servo_status"}
```

```python
out = agent.servo_status("dev-1", ids=[1, 2], include_offset=False)
# {"ok":true,"partial":false,
#  "servos":[{"id":1,"position":1500,"available":true,"error":null}, ...]}
```

- 只读，不申请控制权；`-3` 时保留可用结果并置 `partial:true`。

### 8.2 servo_set

```json
{"action":"servo_set","duration":1.0,
 "servos":[{"id":1,"position":1400},{"id":2,"position":1600}]}
```

- 绝对位置，范围 800–2200；越界项由设备放入 `skipped`，不影响其他舵机。
- 返回 `applied/skipped/duration`。
- `-5`：`error:"control_denied"`，带 `control_holder`。

### 8.3 servo_center

```json
{"action":"servo_center"}
```

- ID1/ID2 设为 1500。

### 8.4 servo_relative

```json
{"action":"servo_relative","servo_id":1,"delta":100,"duration":1.0}
```

- 先 `servo_status` 读当前位置，再 `servo_set` 绝对位置。
- 返回 `previous_position/target/delta/servo_id/servo_name`。

## 9. 进度上报（§4.25）

长动作在对话线程内自动上报：

```json
{"agent_id":"agent-772c194f","request_id":"web-9f3c1a2b",
 "device":"0580a65014af4d0a","stage":"waiting",
 "message":"等待设备推流应答","timestamp":1763000000,
 "datetime":"2026-11-26 15:20:00"}
```

- 已接入：`record_start`、`stream_start`、`servo_status`。
- stage：`accepted` / `waiting` / `completed` / `failed`（协议还定义了 `started/progress/stopped`）。
- 无对话上下文（测试或直接调用动作方法）时不上报，避免假 request_id。
- Web 收到进度后会重置 20 秒超时，推流/录像启动不再被误判超时。

## 10. chat/response 的 data / result

| data 字段 | 来源动作 | 内容 |
|---|---|---|
| `room_number` | `stream_start` / `stream_stop` | 推流房间号 |
| `recording` | `record_start` / `record_stop` | `{recording, filename, started_at, play_url}` |
| `records` | `record_list` | 录像文件数组（最多 50 条，超出附 `records_total`） |
| `servos` | `servo_status` | 舵机数组 |
| `servos` | `servo_set` / `servo_center` / `servo_relative` | `{applied, skipped}` |
| `task` | 任务类动作（M3 预留） | 任务信息 |

`chat/response.result` 为设备原始 response；超过 4096 字节时形如：

```json
{"truncated":true,"size_bytes":8123,
 "summary":{"result":0,"cause":"ok","records_count":120}}
```

## 11. 测试入口

```bash
python3 -m pytest -q walle-agent/test
```

- `test_agent_request_bus.py`：动作 payload、结果解码、幂等、错误码、chat data。
- `test_safety_regression.py`：控制权、资源互斥、运动回执 `-5/-8/-3`、零速与释放。
- `test_request_bus.py`：request_id 配对、超时、重复应答。
- `test_device_registry.py`：在线状态、告警、能力缓存。
