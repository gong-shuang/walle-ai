# walle-agent 独立部署说明

## 1. 角色

`walle-agent` 是独立进程，负责「自然语言 → 车控指令」和 MQTT 收发；
`walle-service` 只保留网页与登录鉴权，不连 MQTT、不转发 agent 请求。
本进程是纯 MQTT 常驻进程，**不监听任何端口**。

```
浏览器 ──WSS/MQTT(home 账号)──> EMQX ──MQTT──> walle-agent ──MQTT──> 设备
浏览器 ──HTTP──> walle-service（仅登录与业务接口）
```

## 2. 目录

```
walle-agent/
├── src/            car_agent.py（唯一入口：解析/执行/MQTT）、vendor/paho
├── etc/            walle-agent.conf、walle-agent.service
├── scripts/        auto_gen_conf_service.sh、start_agent.sh
├── docs/
└── log/
```

## 3. 配置

```bash
bash scripts/auto_gen_conf_service.sh
```

依次输入 MQTT 域名、端口、用户名、密码（为空会重问、密码不回显、已存在会先确认覆盖），
生成 `etc/walle-agent.conf` 与 `etc/walle-agent.service`。

关键项：

| 项 | 说明 |
|---|---|
| `MQTT_HOST` / `MQTT_PORT` / `MQTT_TLS` | 端口 8883 自动置 `MQTT_TLS=1` |
| `MQTT_USER` / `MQTT_PASSWORD` | agent 的 MQTT 凭据 |
| `TENANT` | 主题里的租户段，默认等于用户名 |
| `CLIENT_ID_PREFIX` | clientid 前缀，默认 `agent-` |
| `AGENT_NAME` | 展示名，可选；为空时回落为 clientid，不参与 topic/ACL |
| `DEVICE_ID_FILE` | 可选，固定 clientid 的保存路径，默认 `etc/.device-id` |

## 4. 启动

```bash
bash scripts/start_agent.sh                 # 常驻，终端安静，日志写 log/car_agent.log
WALLE_AGENT_DEBUG=1 bash scripts/start_agent.sh   # 调试：格式化打印所有 MQTT 收发报文
```

### 4.0 clientid（固定身份）

首次启动按本机 MAC 生成 `agent-` + 8 位字母数字，写入 `etc/.device-id`（权限 600），之后每次启动复
用该值；不再随机、不再用 pid。取不到真实 MAC 时依次退回 `/etc/machine-id`、`/var/lib/dbus/machine-id`；
两者都没有则随机生成并写日志告警（重启会变化）。该值同时是 `agents/{clientid}/` 主题段。

同一台机器跑多个实例时，MAC 相同会生成相同 clientid，必须给每个实例单独的 `DEVICE_ID_FILE`
或预置不同的 `etc/.device-id`，否则会互相顶掉。

systemd：

```bash
sudo cp etc/walle-agent.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now walle-agent
```


### 4.1 调试日志（WALLE_AGENT_DEBUG=1）

默认安静，只有首次 MQTT 就绪时打印一行：

```
walle-agent ready: clientid=agent-b851fdde tenant=home chat=walle/home/agents/agent-b851fdde/chat/request device_filter=walle/home/device/+
```

显式设置 `WALLE_AGENT_DEBUG=1` 后，按方向标记打印所有 MQTT 报文：

```
<<< SUB topic=walle/home/agents/agent-b851fdde/chat/request qos=1 retain=False ts=23:19:15
{ "request_id": "web-9f3c1a2b", "device": "0580a65014af4d0a", "text": "状态" }

>>> PUB topic=walle/home/device/0580a65014af4d0a/state/query qos=1 retain=False ts=23:19:15
{ "request_id": "agent-web-12ab34", "uuid": "a26d..." }

<<< SUB topic=walle/home/device/0580a65014af4d0a/state/result qos=1 retain=False ts=23:19:15

>>> PUB topic=walle/home/agents/agent-b851fdde/chat/response qos=1 retain=False ts=23:19:16
{ "request_id": "web-9f3c1a2b", "ok": true, "reply": "状态查询完成..." }
```

非 debug 时全部报文只进 `log/car_agent.log`，终端保持安静。

## 5. 健康与状态（MQTT）

本进程没有 HTTP 接口。健康信息统一由 MQTT 获取：

| 方式 | 说明 |
|---|---|
| retained `walle/{tenant}/agents/{clientid}/online` | 含 `online`、`name`、`version`、`capabilities`；异常掉线由 LWT 发 `online:false` |
| 发 `chat/request` 文本「状态」 | 返回设备 CPU/内存/温度/电量/声呐等运行状态 |
| `log/car_agent.log` | 本地排障用；启动成功后含 `walle-agent ready: clientid=...` |

## 6. 主题规范

| 用途 | 主题 |
|---|---|
| 设备控制/回执 | `walle/{tenant}/device/{device}/{resource}` |
| 发现 | `walle/{tenant}/discovery/query` |
| 对话 | `walle/{tenant}/agents/{clientid}/chat/request|response`、`/online`（retained） |

任何位置都不带版本段（没有 `v1`）。
