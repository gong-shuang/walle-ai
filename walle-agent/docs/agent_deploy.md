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


