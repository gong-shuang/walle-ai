# web ↔ agent MQTT 协议

> 本文描述当前实际实现（`walle-agent` + `walle-service/src/web/user.html`），不含计划项。
> 版本：agent 0.2.0；主题任何位置都**不带**版本段（没有 `v1`）。

## 1. 段定义

| 段 | 取值来源 | 说明 |
|---|---|---|
| `tenant` | agent 配置 `TENANT`，缺省回落 `MQTT_USER` | 当前为 `home` |
| `agent` | agent 的固定 clientid，来自 `etc/.device-id` | 形如 `agent-b851fdde`；即 MQTT 的 clientid |
| `device` | 浏览器页面当前选中的设备（设备绑定关系里的 device 段） | 形如 `0580a65014af4d0a` |

固定命名空间：第 3 段只有 `device`、`agents`、`discovery` 三种取值，与设备 ID 不会冲突。

## 2. Topic 清单

| Topic | 方向（publish → subscribe） | QoS | retained |
|---|---|---|---|
| `walle/{tenant}/agents/{agent}/chat/request` | 浏览器 → agent | 1 | false |
| `walle/{tenant}/agents/{agent}/chat/response` | agent → 浏览器 | 1 | false |
| `walle/{tenant}/agents/{agent}/online` | agent → 浏览器/所有人 | 1 | **true** |
| `walle/{tenant}/device/{device}/car/motor/set/cmd` | agent 或浏览器 → 设备 | 1 | false |
| `walle/{tenant}/device/{device}/baseState/query/request` | agent → 设备 | 1 | false |
| `walle/{tenant}/device/{device}/baseState/query/response` | 设备 → agent/浏览器 | 1 | false |
| `walle/{tenant}/discovery/query/request` | 浏览器 → 设备（租户级广播） | 1 | false |

浏览器订阅 `walle/{tenant}/agents/+/online` 与 `walle/{tenant}/agents/+/chat/response`，一次覆盖所有 agent。

## 3. 请求 JSON（chat/request）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `request_id` | string | 前端必发 | 前端规则 `web-` + 8 位十六进制；缺失时 agent 生成 `chat-<6 位随机数>` |
| `device` | string | 是 | 缺失时回复「请求里没有指定设备（device 字段为空）。」 |
| `text` | string | 是 | 缺失时回复「请求里没有文本内容。」 |
| `user` | string | 否 | 默认 `"mqtt"`；影响日志与运动 request_id |
| `ts` | number | 否 | 毫秒时间戳，agent 不读取 |
| `intent` | string | 否 | 急停通知用 `"abort"`，agent 不读取（靠 `text` 解析） |

```json
{
  "request_id": "web-9f3c1a2b",
  "device": "0580a65014af4d0a",
  "text": "前进1米",
  "user": "web",
  "ts": 1795449999123
}
```

## 4. 响应 JSON（chat/response）

公共字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `request_id` | string | 原样回传 |
| `agent` | string | clientid（身份，不是展示名） |
| `device` | string | 原样回传 |
| `ts` | number | Unix 秒 |
| `ok` | bool | 成功/失败 |
| `reply` | string | 自然语言回复 |
| `intent` | object | 可选，解析结果 |
| `warning` | string | 可选，参数被夹紧的提示 |
| `error` | string | 可选，错误码 |
| `action` | string | 可选：`move` / `rotate` / `stop` / `status` |
| `duration_sec` | number | 可选，预计运动时长 |

### 4.1 成功示例（运动）

```json
{"request_id":"web-9f3c1a2b","agent":"agent-b851fdde","device":"0580a65014af4d0a",
 "ts":1795449999,"ok":true,
 "reply":"已解析：前进 1.00 米，速度 0.50 m/s，预计 2.0 秒。 已开始执行。",
 "intent":{"action":"move","direction":"forward","distance_m":1.0,"speed_mps":0.5},
 "action":"move","duration_sec":2.0,"accepted":true,"started":true}
```

### 4.2 成功示例（状态 / 停止）

```json
{"ok":true,"action":"status","device":"0580a65014af4d0a","status":{...health...},
 "reply":"状态查询完成，详见下方状态。"}
{"ok":true,"action":"stop","device":"0580a65014af4d0a",
 "result":{"published":true,"topic":"walle/home/device/0580a65014af4d0a/car/motor/set/cmd",
           "payload":{...},"rc":0},"mqtt":{...}}
```

### 4.3 失败与错误码

| 场景 | `ok` | 关键字段 |
|---|---|---|
| 文本无法识别 | false | `reply`，无 `error` |
| 参数非法（超出上限） | false | `reply=warning`，无 `error` |
| 请求缺 device/text | false | `reply` 说明，无 `error` |
| MQTT 未连接 | false | `error:"mqtt_not_connected"`, `accepted:false`, `note` |
| 正在执行上一条 | false | `error:"busy"`, `active_device`, `duration_sec`, `note` |
| 停止但无目标设备 | false | `error:"no_device"`, `note` |
| agent 内部异常 | false | `error:"agent_exception"`，`reply` 含异常文本 |

`error` 实际取值集合：`mqtt_not_connected`、`busy`、`no_device`、`agent_exception`。
agent 不发送「超时」响应；超时由前端本地判定（见第 6 节）。

## 5. online 与 LWT

```json
{
  "agent": "agent-b851fdde",
  "name": "小橙车",
  "tenant": "home",
  "online": true,
  "version": "0.2.0",
  "ts": 1795449995,
  "capabilities": {
    "chat": true,
    "actions": ["move", "rotate", "stop", "status"],
    "min_speed_mps": 0.5,
    "max_speed_mps": 0.6,
    "max_distance_m": 10.0,
    "max_angle_deg": 360.0,
    "llm": false
  }
}
```

- 主题后缀与设备端 `device/{device}/online` 对齐；载荷仍是完整 status（含 `name`、`version`、`capabilities`）。
- `agent` 恒为 clientid；`name` 为展示名，`AGENT_NAME` 为空时等于 clientid。
- 上线时发布 retained `online:true`；LWT 使用同一结构、`online:false`，由 broker 在异常掉线时代发。
- 正常退出时也会主动发布一次离线报文。
- 浏览器只把 `online:true` 的条目放进下拉，按 `ts` 降序。

## 6. request_id、超时、busy、多 agent

- 前端以 `pending[request_id]` 记录在途请求；收到响应按 `request_id` 匹配，不匹配直接丢弃。
- 20 秒未收到响应：删除 pending、解锁发送按钮、提示「agent 超时未响应（20s）」。
- `busy` 是前端本地锁：同一时刻只允许一条在途对话请求。
- 多 agent：`agents/+/online` 全量维护；选择优先级为 `sessionStorage['walle_agent']` → 最近 `ts` 的在线 agent；选中项离线后自动回落到第一个在线项。
- 下拉文案为 `name`，`title` 为 clientid；`capabilities.llm=true` 时追加 `· LLM`。

## 7. 急停（两条独立消息）

1. 设备零速帧（不依赖 agent）：

```json
topic: walle/home/device/0580a65014af4d0a/car/motor/set/cmd
{
  "request_id": "web-estop-3f2a1c",
  "move": {"forward": 0, "back": 0, "left": 0, "right": 0},
  "rotate": {"left": false, "right": false}
}
```

2. agent 中止通知（尽力而为，失败不影响第 1 条）：

```json
topic: walle/home/agents/agent-b851fdde/chat/request
{"request_id":"web-abort-7c1d2e","device":"0580a65014af4d0a",
 "text":"停止","user":"web","intent":"abort","ts":1795449999123}
```

agent 侧按 `text` 解析为 stop，`intent` 字段当前不参与判断。

## 8. ACL 对应关系

```erlang
{allow, {username, "home"},    all,      ["walle/home/#"]}.
{allow, {username, "apple"},   publish,  ["walle/home/device/${clientid}/#"]}.
{allow, {username, "apple"},   subscribe,["walle/home/device/${clientid}/#", "walle/home/discovery/query/request"]}.
{allow, {username, "agent-b851fdde"}, all, ["walle/home/agents/${clientid}/#"]}.
{allow, {username, "agent-b851fdde"}, all, ["walle/home/device/#"]}.
{deny,  all, all, ["walle/#"]}.
```

- agent 的 clientid 与主题里 `agents/{agent}` 段必须完全一致，`${clientid}` 才会命中。
- `name` 只出现在报文里，任何时候都不参与 topic 拼接，因此改名不会破坏 ACL；若把名字放进 topic，就等于让 ACL 随展示名变化，既无法用 `${clientid}` 约束，也会让旧规则失效。
- 浏览器用 `home` 账号，`walle/home/#` 已覆盖全部对话与设备主题。

## 9. 相关配置

| 键 | 说明 |
|---|---|
| `MQTT_HOST` / `MQTT_PORT` / `MQTT_TLS` | broker 连接；8883 端口时 `MQTT_TLS=1` |
| `MQTT_USER` / `MQTT_PASSWORD` | agent 的 MQTT 凭据 |
| `TENANT` | 主题租户段，默认等于 `MQTT_USER` |
| `CLIENT_ID_PREFIX` | clientid 前缀，默认 `agent-` |
| `AGENT_NAME` | 展示名，可选；不参与 topic/ACL |
| `DEVICE_ID_FILE` | 可选，固定 clientid 文件路径，默认 `etc/.device-id` |

本进程不监听任何端口；健康与状态一律通过 retained `status` 主题或对话「状态」获取。

## 10. 调试

`WALLE_AGENT_DEBUG=1` 时按同一套方向标记打印：

```
<<< SUB topic=walle/home/agents/agent-b851fdde/chat/request qos=1 retain=False
>>> PUB topic=walle/home/agents/agent-b851fdde/chat/response qos=1 retain=False
>>> PUB topic=walle/home/device/0580a65014af4d0a/car/motor/set/cmd qos=1 retain=False
<<< SUB topic=walle/home/device/0580a65014af4d0a/baseState/query/response qos=1 retain=False
```

非 debug 时终端安静，关键事件写 `log/car_agent.log`。
