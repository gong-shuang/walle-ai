# agent 协议后续任务清单

> 对应 [MQTT_protocol_convention.md](MQTT_protocol_convention.md) §4.20 / §4.21。
> 按优先级逐项实现，完成一项勾选一项；本次已完成的项见下。

## 已确认的约定

- online 报文以 §4.21 示例为准：`agent_id` / `agent_name` / `online` / `version_code` / `version_name` / `ts`。
- agent 主题统一为 `walle/{t}/agent/{agent_id}/...`（单数 `agent`，不是 `agents`）。
- agent 作为独立控制端，使用 `agent_id` / `agent_name`；presence 复用 `agent online`，控制权 `acquire/release` 已实现。
- `user` 字段废弃，agent 改用 `client_name`。
- 本次新增设备订阅 agent online 的 ACL；agent/Web 主题已切到单数 `agent/`；其他 ACL 规则不变。

## 本次已完成

- [x] 主题 `agents/` → `agent/`（walle-ai web、walle-service user.html、agent、ACL）
- [x] 设备端订阅 `walle/{t}/agent/+/online`，维护 agent 在线表并记录上下线
- [x] walle-service ACL：设备可订阅 agent online；agent ACL 改为 `agent/${clientid}/#`
- [x] chat/request 必填校验、`client_name` 替换 `user`
- [x] chat/response `agent_id` 与 online 新字段
- [x] agent 独立控制端：`control/acquire|release` + presence 复用
- [x] agent 订阅改用分层通配符（含 `device/+/alarm/report`），debug 启动打印订阅主题
- [x] agent 启动读取 `walle-agent/VERSION`，打印版本名与时间，online 使用该版本
- [x] 停车语义：`chat/stop` 只停 agent 自己的会话；无会话返回 `no_session`；租约不承担急停职责

## P0 字段与校验对齐（不改主题）

- [x] 0. 主题命名 `agents` → `agent`（代码先对齐）
  - Web：`web/car_ctl_by_mqtt.html` 的 `agentPrefix`、订阅 `agent/+/online` / `agent/+/chat/response`、发布 `agent/{a}/chat/request`
  - agent：`walle-agent/src/car_agent.py` 的 `chat_topic()`、`agent_status_topic()` 改为 `walle/{t}/agent/...`
  - 验收：抓包主题全部为 `walle/{t}/agent/...`；Web 能收到 online 和 chat/response。

- [x] 1. agent online 字段对齐 §4.21 示例
  - 改为 `agent_id` / `agent_name` / `online` / `version_code` / `version_name` / `ts`
  - 已移除 `agent` / `name` / `version`；保留 `tenant` / `capabilities` 作为可选字段
  - `version_code` / `version_name` 从 `walle-agent/VERSION` 的 `AGENT_VERSION_NUM` / `AGENT_VERSION_NAME` 读取
  - Web 已改为优先读 `agent_name` / `version_name`，兼容旧字段
  - 文件：`walle-agent/src/car_agent.py` `_status_payload`
  - 验收：抓 `walle/{t}/agent/{a}/online`，必选字段与示例一致；Web 下拉仍显示名称。

- [x] 2. chat/response 字段 `agent` → `agent_id`
  - 文件：`walle-agent/src/car_agent.py` `_run_chat_request`
  - 验收：响应含 `agent_id`，不再含 `agent`。

- [x] 3. chat/request 必填校验
  - `client_id` / `client_name` / `request_id` / `device` / `text` 缺失时：`ok:false`、`error:"invalid"`、`reply` 说明
  - 已移除现有 `request_id` 缺失时的 `chat-` 兜底生成
  - 文件：`walle-agent/src/car_agent.py` `_on_chat_message` / `_run_chat_request`
  - 验收：逐项缺失测试，响应与 §4.20 失败表一致。

- [x] 4. 用 `client_name` 替代 `user`
  - 日志使用 `client_name`（清洗后；为空回落 `agent_id`）
  - 运动 `request_id` 统一为 `agent-{tool}-{8hex}`，不再使用 `client_name`（M0-06）
  - 文件：`walle-agent/src/car_agent.py` `_run_chat_request` / `_run_motion`
  - Web 可继续发送 `user`，agent 不再读取
  - 验收：日志出现 `client_name`；运动 `request_id` 符合 `agent-{tool}-{8hex}`。

- [x] 5. 修正 busy / warning 的回复行为
  - busy：`reply` 明确提示“正在执行上一条”
  - 速度夹紧：`ok:true` + `warning`，`reply` 可保持已解析文案
  - 距离/角度超限：`ok:false` + `reply`，不要求 `warning`
  - 文件：`walle-agent/src/car_agent.py` `_reply_text` / `handle_chat`
  - 验收：三种场景与 §4.20 失败表一致。

- [x] 6. Web 兼容新 online 字段
  - 读取 `agent_name` / `version_name`，兼容旧 `name` / `version`
  - `capabilities.llm` 改为可选（无 `capabilities` 不报错）
  - 文件：`web/car_ctl_by_mqtt.html` 的 `walle-agent-status` 处理
  - 验收：新旧 agent 报文都能显示名称/版本。

## P1 agent 作为独立控制端

- [x] 7. agent → 设备请求携带自身身份
  - 所有 `.../request` 与 `.../cmd` 注入 `client_id=agent_id`、`client_name=agent_name`
  - 文件：`walle-agent/src/car_agent.py` `_publish` / `_drive_payload`
  - 验收：抓包可见字段；设备审计显示 agent。

- [x] 8. 实现设备控制权 acquire/release
  - 控制类请求前 acquire，结束后 release；查询不 acquire
  - 处理 `occupied` 与 `control_denied`，不自动抢占；回复中提示当前 holder
  - 文件：`walle-agent/src/car_agent.py`
  - 验收：Web 持有租约时 agent acquire 返回 `occupied`；release 后恢复。

- [x] 9. 实现 agent presence
  - 复用 `agent/{agent_id}/online` 同时作为 Web 展示与控制权 presence；设备端 `_handle_agent_presence` 调用 `control_lease.on_presence(agent_id, online)`
  - retained + LWT，正常退出发 `online:false`
  - 文件：`walle-agent/src/car_agent.py`、`walle-device/.../protocol.py`、`ld_monitor_base.py`
  - 验收：设备在 holder 离线 grace 后自动释放。

- [x] 10. 明确与 Web 抢控制权策略
  - 采用“拒绝 + 提示当前 holder”：agent acquire 返回 occupied 时直接拒绝，不自动重试/抢占
  - 已写入 §4.20/§4.21 与任务清单
  - 验收：文档与实现一致。

- [x] 11. 回归现有 online 离线通知
  - `agent/{a}/online` 在 Ctrl+C / systemctl stop 时主动发 `online:false`；LWT 覆盖异常掉线
  - 文件：`walle-agent/src/car_agent.py` `shutdown`
  - 验收：正常退出后 retained 报文为离线。

## P2 订阅、全功能与文档

- [x] 12. agent 订阅清单
  - response 使用分层通配符：`device/+/+/+/response`（2 层资源）、`device/+/+/+/+/response`（3 层）、`device/+/+/+/+/+/response`（4 层）
  - 保留 `device/+/online`、`device/+/alarm/report`
- agent 请求订阅：`agent/{self}/+/request`（`chat/request`）、`agent/{self}/+/+/request`（`baseState/query`、`config/modify`、`chat/stop`）
  - `WALLE_AGENT_DEBUG=1` 时启动打印实际订阅主题
  - 已写入协议文档「agent 订阅关系」

- [x] 13. 全功能请求信封（已废弃：不做）
  - 结论：用户与 agent 使用自然语言对话，不引入 `resource` / `params` 这类编程参数；
    agent 解析自然语言后调用设备，再把设备端的 `response` 翻译成自然语言回复，
    因此 `chat/request` 保持 `text`；`chat/response` 的 `data` / `result` 仅用于回传动作结果
    （v1.26 / M2-06），不引入结构化请求参数。

- [ ] 14. capabilities 协议
  - 若保留：定义 `actions` / `resources` / limits / `llm`；Web 按能力禁用不支持功能

- [x] 15. 异步事件
  - agent → Web/App 的 `alarm/report`（§4.7）与 `task/report`（§4.27）已定义；长任务进度见 §4.25；设备上下线沿用 `online`

- [ ] 16. 并发与超时
  - 定义单运动 / 多查询、排队、长任务超时，与 Web 20s 超时协调

- [x] 17. ACL（其余）
  - 设备订阅 `agent/+/online` 的 ACL 已完成；presence 复用 agent online，无需额外 client presence ACL；其他新增主题再按需补规则

- [ ] 18. 文档补全
  - 订阅关系、ACL、配置（`TENANT` / `CLIENT_ID_PREFIX` / `DEVICE_ID_FILE`（默认 `etc/.run/agent-id`）/ `etc/.run/agent-name`）、停车语义（`chat/stop` 只停自己的会话、`no_session`）、debug 开关
  - `walle-agent/docs/mqtt-protocol.md` 已删除，保持本文为唯一来源
