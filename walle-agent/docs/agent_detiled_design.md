


### 4.0 clientid（固定身份）

首次启动按本机 MAC 生成 `agent-` + 8 位字母数字，写入 `etc/.device-id`（权限 600），之后每次启动复
用该值；不再随机、不再用 pid。取不到真实 MAC 时依次退回 `/etc/machine-id`、`/var/lib/dbus/machine-id`；
两者都没有则随机生成并写日志告警（重启会变化）。该值同时是 `agent/{clientid}/` 主题段。

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
walle-agent ready: clientid=agent-b851fdde tenant=home chat=walle/home/agent/agent-b851fdde/chat/request device_filter=walle/home/device/+
```

显式设置 `WALLE_AGENT_DEBUG=1` 后，按方向标记打印所有 MQTT 报文：

```
<<< SUB topic=walle/home/agent/agent-b851fdde/chat/request qos=1 retain=False ts=23:19:15
{ "request_id": "web-9f3c1a2b", "device": "0580a65014af4d0a", "text": "状态" }

>>> PUB topic=walle/home/device/0580a65014af4d0a/baseState/query/request qos=1 retain=False ts=23:19:15
{ "request_id": "agent-web-12ab34" }

<<< SUB topic=walle/home/device/0580a65014af4d0a/baseState/query/response qos=1 retain=False ts=23:19:15

>>> PUB topic=walle/home/agent/agent-b851fdde/chat/response qos=1 retain=False ts=23:19:16
{ "request_id": "web-9f3c1a2b", "ok": true, "reply": "状态查询完成..." }
```

非 debug 时全部报文只进 `log/car_agent.log`，终端保持安静。