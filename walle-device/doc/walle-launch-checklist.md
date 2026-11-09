# Walle 上线前修复清单

> 生成日期：2026-11-08
> 依据：当前工作区代码静态审查（非渗透测试、非路测结论）
> 状态图例：🔴 未解决　🟡 进行中　✅ 已解决
> 更新方式：完成一项把 `- [ ]` 改成 `- [x]`，把 🔴 改成 ✅，并在文末「完成记录」补一行；进行中则把 🔴 改成 🟡、复选框保持未勾选。
> 路径约定：除特别标注外，文中路径均相对于 `walle-device/` 目录；`web/` 等位于仓库根目录的路径会单独标注。

## 进度总览

| 优先级 | 任务数 | 已完成 | 剩余 |
| --- | ---: | ---: | ---: |
| P0 上线阻断 | 18 | 15 | 3 |
| P1 上线前应修 | 19 | 17 | 2 |
| P2 后续优化 | 21 | 2 | 19 |
| **合计** | **58** | **34** | **24** |

> 每次勾选后请同步更新本表数字。

---

## P0 上线阻断项（必须全部清零）

### P0-1 MQTT 远程命令执行面

> 2026-11-08：`cmd/exec` / `cmd/result` 已从固件、协议文档、测试页整体删除，
> P0-1.1~P0-1.3 关闭；P0-1.4 已通过强制 TLS 与必填项校验关闭；P0-1.5 仍待处理。

- [x] ✅ **P0-1.1 默认关闭远程命令执行**
  - 处理：已直接删除该能力，不再存在远程命令入口
  - 验收：设备订阅清单无 `cmd/exec`；向该主题发布消息无任何响应

- [x] ✅ **P0-1.2 命令白名单改为严格匹配，禁止 shell 注入**
  - 处理：`handle_cmd_exec` / `shell=True` 执行逻辑已整体删除
  - 验收：源码中已无远程 shell 命令执行路径

- [x] ✅ **P0-1.3 uuid 不再作为鉴权凭证（针对远程命令）**
  - 处理：命令能力已删除；uuid 仍用于其它请求配对，但无法再触发命令执行
  - 验收：仅拿到 discovery/online 中的 uuid，无法执行任何命令

- [x] ✅ **P0-1.4 MQTT 默认 TLS 8883，缺配置拒绝启动**
  - 处理：删除代码内默认 broker / 用户名；新增 `validate_mqtt_config()`，`MQTT_HOST` / `MQTT_USERNAME` / `MQTT_PASSWORD` / `MQTT_TENANT` 缺失、端口非法或 `MQTT_CA_FILE` 不可读时拒绝启动；默认 `MQTT_PORT=8883` + `MQTT_TLS=on`，禁止 `MQTT_TLS=off` 明文回退
  - 验收：无 `/etc/walle-ai.conf` 时 `net_mqtt_bridge` 构造即报错退出，`agent.py --check` 输出缺失项；TLS 强制开启，抓包无明文 MQTT 报文

- [ ] 🔴 **P0-1.5 Broker 按设备 ACL + 请求审计**
  - 动作：每台设备独立 MQTT 账号，仅允许读写 `walle/{tenant}/{device}/#`；对状态、视频、音频等动作请求记录来源、结果、时间
  - 验收：设备 A 的账号无法订阅/发布设备 B 的主题；关键动作可追溯

### P0-2 ROS 网络暴露面

> 2026-11-08：P0-2.1（rosbridge 收口）已降级为 P2-5.1，P0-2.2（web_video_server 收口）
> 已降级为 P2-5.2——两者均定位为开发调试工具，`bringup` 默认不启动（9090/8080 不监听）。

- [x] ✅ **P0-2.3 whip_secret 不再声明为 ROS 参数**
  - 处理：`video_webrtc_bridge` 不再 `declare_parameter("whip_secret")`，只从 `/etc/walle-ai.conf` 的 `WHIP_SECRET` 读取
  - 验收：`ros2 param get /video_webrtc_bridge whip_secret` 返回参数不存在；`ros2 param dump` 无明文

- [ ] 🔴 **P0-2.4 防火墙只放行必要端口**
  - 动作：默认拒绝入站，仅放行 MQTT 出站、必要的鉴权视频/控制端口
  - 验收：外部端口扫描只看到预期端口

### P0-3 底盘失联保护

- [x] ✅ **P0-3.1 cmd_vel 看门狗**
  - 处理：`mecanum.py` 新增 100ms 定时器与 `cmd_vel_timeout` 参数（默认 1.5s），运动状态下超时未收到新 `Twist` 即发布零速度并告警；同时 `speech` / `llm_control_move` / `vllm_obstacle_avoidance` 改为运动期间 10Hz 重发 `cmd_vel`，避免看门狗误停
  - 验收：发布前进指令后断开 Wi-Fi / 关闭 App，1.5s 内轮子停转；语音/LLM 动作执行期间不误停

- [x] ✅ **P0-3.2 进程退出与串口异常安全停车**
  - 处理：`main()` 注册 SIGTERM/SIGINT，退出前统一调用 `stop_motors()`；电机写入失败、串口读取异常、遥测连续 1s 无数据（以约 10Hz 的电池报文为主心跳，兼容无 IMU 底盘）时均尝试下发零速度
  - 验收：`kill -TERM` 控制器进程后电机停转；串口拔插 / 控制器断连后不出现持续转动（固件通信超时为最终兜底，见 P0-3.3）

- [ ] 🟡 **P0-3.3 MCU 固件通信超时停车（软硬双保险）**
  - 状态：阻塞——`ros_robot_controller` 为 STM32 固件，源码不在本仓库，需固件方实现并烧录
  - 需求：见 `doc/mcu-comm-timeout-requirement.md`（合法电机帧计时、默认 500ms 超时四轮置 0、仅电机帧刷新、超时后新指令可恢复）
  - 验证：架空车轮后运行 `tools/test_mcu_timeout.py`，并补 `kill -9` 与整机断电测试
  - 验收：上位机死机/断电时，底盘在 0.5s 内自动停止

- [x] ✅ **P0-3.4 速度与加速度限幅**
  - 处理：`mecanum.py` 新增 `max_linear_speed` / `max_angular_speed` / `max_linear_accel` / `max_angular_accel` 参数（默认 1.0 m/s、3.0 rad/s、1.0 m/s²、3.0 rad/s²）；输入先限幅，再按实际 dt 做加速度斜坡；看门狗急停绕过斜坡立即停车
  - 验收：阶跃指令下电机输出平滑，无电流冲击；参数可通过 ROS 参数在线调整

- [x] ✅ **P0-3.5 禁止绕过底盘节点直接控制电机**
  - 处理：低层接口移入内部命名空间——`ros_robot_controller` 改为订阅 `~/internal/set_motor_speeds`、`~/internal/enable_reception`，`mecanum.py` / `hardware_test.py` 同步改发布名；`walle_net/launch/net.launch.py` 为 rosbridge 增加 `topics_glob` 话题白名单，只放行调试页实际使用的 `/cmd_vel`、电池/声呐与灯光、蜂鸣器、OLED、舵机话题，其余话题（含内部直控接口）的 publish / subscribe / advertise 一律拒绝
  - 证据：`walle_ws/src/car_control/ros_robot_controller/ros_robot_controller/ros_robot_controller_node.py` L52-L53
  - 验收：rosbridge 向 `/ros_robot_controller/internal/set_motor_speeds` 或 `/ros_robot_controller/internal/enable_reception` 发布时被拒绝（服务端日志 `No match found for topic`）且不生效；调试页 `/cmd_vel` 遥控不受影响

### P0-4 多车同域串控

- [x] ✅ **P0-4.1 启用本机 ROS 隔离**
  - 处理：`scripts/env_walle.sh` 保留 `ROS_DOMAIN_ID=1` 用于与本机其它 ROS 栈隔离，并新增 `ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST`（Jazzy 推荐，`ROS_LOCALHOST_ONLY` 已弃用）；启动横幅同步显示 `ROS_DISCOVERY`
  - 证据：`scripts/env_walle.sh`（`ROS_DOMAIN_ID` / `ROS_AUTOMATIC_DISCOVERY_RANGE`）
  - 验收：同一局域网两台 Walle 互不可见；车上 `ros2 node list` 正常，另一台电脑执行 `ros2 node list` 为空；rosbridge 调试页不受影响

- [x] ✅ **P0-4.2 或按设备分配 ROS_DOMAIN_ID**
  - 处理：二选一，已选择 P0-4.1 本机隔离——设备不跨机通信，无需按 device_id 派生 domain；若后续需要跨机 ROS 调试再启用本项
  - 验收：不适用（风险已由 P0-4.1 关闭）

### P0-5 密钥与默认凭据

- [x] ✅ **P0-5.1 删除代码内兜底凭据**
  - 处理：与 P0-1.4 一并完成——`MQTT_HOST` / `MQTT_USERNAME` 默认值已删除，必填项缺失时 `validate_mqtt_config()` 直接报错拒绝启动
  - 验收：`agent.py` 中无默认 MQTT 凭据；无 env 文件时节点拒绝启动

- [x] ✅ **P0-5.2 env 文件模板化并加入 gitignore**
  - 处理：env 模板统一为 `*.example`（`scripts/walle-ai.conf.example`、`linux/walle-ai.conf.example`）；仓库根与 `walle-device/.gitignore` 忽略 `*.env`、`walle-ai.conf`；`start.sh` / `deploy.sh` 在缺少配置时提示从模板复制；部署文档与 `agent.py` 报错提示同步改为模板路径
  - 验收：`git ls-files '*.env' 'walle-ai.conf'` 为空；新建 `scripts/walle-ai.conf` 后 `git check-ignore -v` 命中忽略规则，`git status` 不跟踪

- [x] ✅ **P0-5.3 扫描历史并轮换已暴露密钥**
  - 处理：对全部 88 个提交执行 `git log --all -p --text`，扫描 `sk-`、`ALIYUN_API_KEY`、`MQTT_PASSWORD`、`WHIP_SECRET` 及私钥 / GitHub / AWS 令牌特征，仅发现占位符（`sk-你的新key`、`xxx`）与 shell 变量引用，无真实密钥；无需轮换
  - 验收：历史扫描无有效密钥；若后续私下使用过真实密钥，仍建议主动轮换

- [x] ✅ **P0-5.4 收紧配置文件权限**
  - 处理：`deploy.sh` 检查 `scripts/walle-ai.conf` 是否存在、MQTT 必填项与 `WHIP_SECRET` 是否已填写，安装为 `/etc/walle-ai.conf`（`root:root 600`），并渲染、安装、启用 `walle-ai.service`（`EnvironmentFile` 注入、`ExecStart` 为 `source env_walle.sh + ros2 launch`、`User=<部署用户>`、`Restart=always`、网络就绪后启动）；`start.sh` 仅用于开发环境并读取脚本同级 `walle-ai.conf`；`large_models/config.py` 改为容忍配置不可读，依赖环境变量注入
  - 证据：`scripts/deploy.sh`、`scripts/start.sh`、`scripts/walle-ai.service.in`
  - 验收：生产部署后普通用户 `cat /etc/walle-ai.conf` 返回 Permission denied；`systemctl cat walle-ai` 可见 `EnvironmentFile` 与 `env_walle.sh` 启动命令；开发 `./start.sh` 可正常启动

---

## P1 上线前应修

### P1-1 进程守护与启动可靠性

- [x] ✅ **P1-1.1 为关键节点增加 respawn**
  - 处理：`controller.launch.py`（ros_robot_controller 2s、mecanum 5s）、`usb_cam`、声呐、语音、`vocal_detect` / `agent_process` / `tts_node` 均加 `respawn=True` + `respawn_delay=5.0`；net/video 原本已有
  - 验收：`kill` 任意节点后约 5s 内自动恢复，无需人工重启

- [x] ✅ **P1-1.2 增加 systemd 服务单元**
  - 处理：新增 `scripts/walle-ai.service.in`，由 `deploy.sh` 渲染安装并 enable；`EnvironmentFile=/etc/walle-ai.conf`、`Restart=always`、`RestartSec=5`、`After/Wants=network-online.target`、`User=<部署用户>`；`WatchdogSec` 未启用（节点未实现 sd_notify），进程崩溃由 `Restart` 兜底
  - 证据：`scripts/walle-ai.service.in`、`scripts/deploy.sh`
  - 验收：整机重启后服务自启；`kill` 主进程后 5s 内被拉起

- [x] ✅ **P1-1.3 明确启动顺序与健康检查**
  - 处理：`bringup.launch.py` 按「底盘 → usb_cam/声呐 → 视频桥/语音/大模型 → MQTT/自检」分 4 个阶段启动，每阶段打印 `[bringup] 阶段 x/4`；`startup_check` 等待 `ros_robot_controller` / `mecanum_chassis_node` / `usb_cam` / `video_webrtc_bridge` / `net_mqtt_bridge` 最多 30s，并检查 `/image_raw` 有发布者，就绪打印 `STARTUP READY` 并发布 `/bringup/startup_status`（`ready`），超时打印缺失节点/话题
  - 验收：冷启动日志可见 4 个阶段标记与 `STARTUP READY`；`ros2 topic echo /bringup/startup_status` 可看到 `ready`

### P1-2 部署可复现

- [x] ✅ **P1-2.1 修正部署文档**
  - 处理：`doc/README.md` 补齐项目介绍、Ubuntu/ROS2 安装、venv 与 `requirements.txt` 依赖、`colcon build`、udev 规则、`walle-ai.conf` 配置、`deploy.sh`/systemd 启动与上线环境切换
  - 验收：按文档可从全新 Ubuntu 24.04 完成环境与部署

- [x] ✅ **P1-2.2 deploy.sh 覆盖完整部署**
  - 处理：按职责拆分——`deploy.sh` 只做系统级部署（校验配置 → 安装 `/etc/walle-ai.conf` 600 → 渲染安装并启用 `walle-ai.service`，禁止 root 直接执行）；编译、Python 依赖、udev 规则在 `doc/README.md` 写明，脚本可重复执行
  - 验收：按 README 顺序（环境 → 依赖 → `colcon build` → udev → `./deploy.sh`）可在新系统完成部署；`deploy.sh` 重复执行无副作用

- [x] ✅ **P1-2.3 增加依赖清单**
  - 处理：新增 `doc/requirements.txt`，锁定 `paho-mqtt`、`psutil`、`pyserial`、`smbus2`、`numpy`、`dashscope`、`PyYAML`、`transforms3d` 版本；OpenCV / ffmpeg / python3-smbus 作为 apt 依赖在 README 说明
  - 验收：按 README 安装后无缺包；ROS2 包由 apt/colcon 提供，不混入 pip

- [x] ✅ **P1-2.4 去掉 scripts/env_walle.sh 的环境硬编码**
  - 处理：新增 `WALLE_VENV` 变量（默认 `$HOME/venv`），激活与 site-packages 均按实际 venv 路径和 Python 版本动态获取；venv 缺失时明确报错并提示用 `WALLE_VENV` 指定；README 同步说明
  - 验收：更换用户名/路径/Python 小版本后，设置 `WALLE_VENV` 即可正常启动

### P1-3 硬件断连恢复

- [x] ✅ **P1-3.1 串口断连自动重连**
  - 处理：`Board` 增加 `_connect` / `_disconnect` 与 1s 周期重连；串口打开失败不再阻塞节点启动；读/写异常会关闭端口并自动重连；重连后清空解析状态、重新使能接收；写入失败返回 False 不抛异常
  - 验收：拔插控制器 USB 后自动恢复，无需重启节点（需实车验证）

- [x] ✅ **P1-3.2 串口 I/O 不阻塞 ROS executor**
  - 处理：串口读 `timeout=1s`、写 `write_timeout=0.5s`；舵机读取队列 `timeout=2s` 超时返回 None；节点改用 `MultiThreadedExecutor`，底盘/执行器写回调放入 `ReentrantCallbackGroup`，与状态查询回调互不阻塞
  - 验收：串口卡顿时 `/cmd_vel` 与状态查询仍能及时响应（需实车验证）

- [x] ✅ **P1-3.3 摄像头设备节点固定**
  - 处理：新增 `99-walle-camera.rules`，按 `idVendor/idProduct` 创建一级符号链接 `/dev/walle-camera`；`usb_cam_param.yaml` 与 `video_webrtc_bridge.yaml` 使用该路径（`usb_cam` 不支持 by-id 深层链接，会把 `../../video0` 拼成 `/dev/../../video0`）
  - 验收：重启/插拔后 `/dev/walle-camera` 始终指向正确摄像头，`usb_cam` 正常打开；换相机时更新 udev id 与配置

- [x] ✅ **P1-3.4 补齐 `/dev/rrc` udev 规则**
  - 处理：规则改为 `99-rrc.rules`——`ttyAMA0` 创建 `SYMLINK+="rrc"`，属组 `dialout`、`MODE=0660`；兼容 `ttyACM0` 并忽略 ModemManager；`create_udev_rules.sh` 自动安装
  - 验收：全新镜像安装规则后存在可读写的 `/dev/rrc`；仅 `dialout` 组可访问

- [x] ✅ **P1-3.5 声呐 I2C 异常与超时处理**
  - 处理：`Sonar.getDistance()` 不再吞异常，I2C 失败向上抛出；节点读取线程增加 try/except、连续失败计数，失败时发布 `-1` 无效值并按 1/50 次节流告警，恢复后打印恢复日志；RGB 写入与退出清理同样加保护，线程不退出
  - 验收：拔掉声呐后节点存活，`/sonar_controller/get_distance` 持续输出 `-1` 并告警；插回后自动恢复读数

### P1-4 日志与可观测性

- [x] ✅ **P1-4.1 ffmpeg 日志保留策略**
  - 处理：`video_webrtc_bridge` 每次新建日志前按「7 天 / 最多 10 个文件 / 总 100MB」清理最旧的 `ffmpeg_*.log`；可通过 `log_retention_days` / `log_max_files` / `log_max_total_bytes` 调整
  - 验收：连续推流 7 天后日志占用受控在约 100MB / 10 个文件以内

- [x] ✅ **P1-4.2 MQTT 日志降级与脱敏**
  - 处理：`on_message` 默认只记录主题与字节数；完整报文改为 DEBUG 级别，并对 password/secret/token/key/room 等字段脱敏后截断
  - 验收：生产 INFO 日志不包含报文内容与敏感字段

- [ ] 🔴 **P1-4.3 远程日志与关键指标**
  - 状态：暂缓——远程日志文件上报作为新需求后续处理（2026-11-09 确认），指标上报随该需求一起设计
  - 动作：接入 journald/远程日志；上报在线率、请求失败率、推流失败率、CPU/温度
  - 验收：现场故障可远程定位，不依赖 SSH

### P1-5 测试与 CI

- [x] ✅ **P1-5.1 远程命令移除回归测试**
  - 处理：新增 `test/test_remote_cmd_removed.py`，断言 `subscriptions()` 不含 `cmd/exec`、`REQUEST_HANDLERS` 不含 `handle_cmd_exec`，且 `agent.py` 源码不含 `remote_cmd` / `cmd/exec` / `handle_cmd_exec`
  - 验收：本地 `pytest` 3 项通过；CI（P1-5.4）接入后任何重新引入都会失败

- [x] ✅ **P1-5.2 运动学与协议单测**
  - 处理：新增 `controller/test/test_mecanum.py`（四轮解算、`Twist` 限幅/加速度斜坡/看门狗停车）、`net_mqtt_bridge/test/test_protocol.py`（`uuid` 校验、`request_id` 缺省与回传、订阅主题格式、主题段清洗）、`video_webrtc_bridge/test/test_room.py`（房间号校验）；房间号校验抽为 `valid_room_number()`，改用 `fullmatch` 避免未加锚点的 pattern 被前缀匹配绕过
  - 验收：三个包 `pytest` 共 63 项通过（21 + 23 + 19，含 P1-5.1 的 3 项）；`controller` 的 copyright/flake8/pep257 为包内历史遗留失败，与本次单测无关

- [x] ✅ **P1-5.3 故障注入与长稳测试**
  - 处理：新增 `scripts/stability_test.py`：`kill` 逐个 SIGKILL 关键节点并等待 launch/systemd 拉起；`camera` 通过 sysfs 解绑/绑定 uvcvideo 模拟拔插；`network` 默认用 iptables 只拦截 Broker 流量（不影响 SSH），`--mode link` 才断整块网卡并带延时恢复兜底；`soak` 周期采样 RSS/FD/磁盘/温度并输出 CSV，结束时判定泄漏趋势。串口掉线无法软件模拟，README 补充手动拔插步骤
  - 验收：脚本 `--help`、`health`、`kill`（无节点）、`camera`（非 USB 设备）、`soak`（9 样本）冒烟通过；真机需按 README 执行 kill/camera/network/soak 24h 并留存 CSV

- [ ] 🔴 **P1-5.4 CI 流水线**
  - 动作：build + lint + test；合并前必须通过
  - 验收：CI 红灯阻断合并

---

## P2 后续优化

### P2-1 性能

- [ ] 🔴 **P2-1.1 state/query 使用缓存快照**
  - 证据：`walle_ws/src/net/net_mqtt_bridge/net_mqtt_bridge/agent.py` 的 `handle_query_status()`（L2484）每次查询阻塞 1s 采样
  - 动作：复用 monitor_loop 的最近一次采样结果
  - 验收：`state/query` 响应 < 100ms

- [ ] 🔴 **P2-1.2 capability 探测异步/按需**
  - 证据：`walle_ws/src/net/net_mqtt_bridge/net_mqtt_bridge/agent.py` 的 `handle_capability_query()`（L1747）在请求线程里跑 v4l2/pactl，可能数秒
  - 动作：后台缓存能力结果，按 TTL 刷新；支持“只读缓存”模式
  - 验收：capability 查询不阻塞其它请求

- [ ] 🔴 **P2-1.3 视频硬件编码**
  - 证据：`walle_ws/src/video/video_webrtc_bridge/video_webrtc_bridge/node.py` L602-L633 逐帧写 pipe，软编码 640x480@30
  - 动作：评估 `v4l2h264enc`/`h264_v4l2m2m`/GStreamer，降低 CPU 与延迟
  - 验收：同等画质下 CPU 占用明显下降，端到端延迟不升高

- [ ] 🔴 **P2-1.4 MQTT 消息处理与监控解耦**
  - 动作：命令线程池与采样线程分离；限制并发与队列长度
  - 验收：大量查询/命令下监控采样不丢点

### P2-2 代码质量

- [ ] 🔴 **P2-2.1 拆分 agent.py**
  - 证据：`walle_ws/src/net/net_mqtt_bridge/net_mqtt_bridge/agent.py` 2687 行，混合设备发现、系统监控、推流、音频、协议处理
  - 动作：按职责拆为 config、identity、monitor、protocol、media 模块
  - 验收：单文件 < 500 行，模块可独立测试

- [ ] 🔴 **P2-2.2 删除被 node 覆盖的旧视频实现**
  - 证据：`walle_ws/src/net/net_mqtt_bridge/net_mqtt_bridge/node.py` L113-L116 覆盖 `handle_video_request` 等方法，`agent.py` 内仍保留整套 ffmpeg 代码
  - 动作：删除死代码，避免两套逻辑分叉
  - 验收：全仓库只有一套推流实现

- [ ] 🔴 **P2-2.3 版本号单一来源**
  - 证据：`walle_ws/src/net/net_mqtt_bridge/net_mqtt_bridge/agent.py` L221-L222 的 `VERSION_CODE/VERSION_NAME` 与 `scripts/env_walle.sh` 的 `VERSION`
  - 动作：统一由构建注入或读取同一个文件
  - 验收：发版只改一处

- [ ] 🔴 **P2-2.4 修复 enable_reception 属性覆盖**
  - 证据：`walle_ws/src/car_control/ros_robot_controller/ros_robot_controller/ros_robot_controller_node.py` L46/L116-L119，回调把方法名覆盖为 bool
  - 动作：改名为 `_reception_enabled`，方法保持独立
  - 验收：重复调用 `enable_reception` 不抛 `TypeError`

- [ ] 🔴 **P2-2.5 移除节点构造函数内的 rclpy.init()**
  - 证据：`walle_ws/src/car_control/ros_robot_controller/ros_robot_controller/ros_robot_controller_node.py` L26、`walle_ws/src/car_control/sonar_controller/sonar_controller/sonar_controller_node.py` L24
  - 动作：只保留 main 中的 `rclpy.init`，构造函数不初始化上下文
  - 验收：节点可被测试/组合复用，不出现重复初始化

- [ ] 🔴 **P2-2.6 修复 PWM 舵机状态服务**
  - 证据：`walle_ws/src/car_control/ros_robot_controller/ros_robot_controller/ros_robot_controller_node.py` L163-L176 返回 list 而非 response，服务签名也不符
  - 动作：改为 `(request, response)` 并填充 `response`
  - 验收：`ros2 service call .../pwm_servo/get_state` 正常返回

- [ ] 🔴 **P2-2.7 修正 IMU 数据语义**
  - 证据：`walle_ws/src/car_control/ros_robot_controller/ros_robot_controller/ros_robot_controller_node.py` L326-L341 orientation 恒 0 但 covariance 非 0
  - 动作：无姿态解算时把协方差首项设为 -1（REP-145），或融合出真实姿态
  - 验收：下游不会把零四元数当有效姿态

- [ ] 🔴 **P2-2.8 补 URDF/TF**
  - 动作：至少提供 `base_link → imu_link → camera_link`，扩展导航时再补 `map→odom→base_link`
  - 验收：`ros2 run tf2_tools view_frames` 树完整

### P2-3 AI 演示节点

- [ ] 🔴 **P2-3.1 消除 process() 忙等**
  - 证据：`walle_ws/src/ai/large_models/large_models/vllm_obstacle_avoidance.py` L232-L275 无 sleep
  - 动作：改为事件/条件变量或固定周期 sleep
  - 验收：空闲时 CPU 占用接近 0

- [ ] 🔴 **P2-3.2 去除阻塞式 time.sleep**
  - 证据：`walle_ws/src/ai/large_models/large_models/vllm_obstacle_avoidance.py` L294-L299
  - 动作：改为定时器/状态机，不阻塞回调线程
  - 验收：动作执行期间状态查询与停止指令仍可响应

- [ ] 🔴 **P2-3.3 声呐超时保护**
  - 证据：`walle_ws/src/ai/large_models/large_models/vllm_obstacle_avoidance.py` L116-L117 只保存最后一次距离，断流后可能继续前进
  - 动作：记录时间戳，超时即停车并上报
  - 验收：断开声呐后 0.5s 内停车

- [ ] 🔴 **P2-3.4 从生产 launch 移除或加开关**
  - 动作：`walle_ws/src/ai/large_models/launch/start.launch.py` 不默认启动实验性 AI 节点；或加 `enable_ai` 参数默认 false
  - 验收：生产启动不包含未验收的自动驾驶逻辑

### P2-4 摄像头与兼容性

- [ ] 🔴 **P2-4.1 usb_cam 控制名适配**
  - 证据：`walle_ws/src/video/walle_video/config/usb_cam_param.yaml` L18-L23 注释已说明 kernel 6.8 控制名不匹配
  - 动作：升级/补丁 usb_cam，或增加 V4L2 控制名映射；无法解决则明确记录为已知告警
  - 验收：启动日志无控制设置失败错误

- [ ] 🔴 **P2-4.2 多 video 节点环境固定设备**
  - 证据：本机存在 `/dev/video0`–`/dev/video33`
  - 动作：按 by-id/udev 固定；启动前校验设备能力（分辨率/格式）
  - 验收：不会误开 ISP/编解码节点

- [ ] 🔴 **P2-4.3 修复测试页缺失服务**
  - 证据：`web/car_ctl_by_bridge.html`（仓库根目录）L323 调用 `/avoidance_node/enter`，当前代码库无此服务
  - 动作：实现服务或移除按钮
  - 验收：点击对应按钮不再出现 rosbridge 错误

### P2-5 调试工具

- [x] ✅ **P2-5.1 rosbridge 默认关闭（仅调试开启）**
  - 处理：`scripts/env_walle.sh` 新增 `WALLE_RUNTIME_ENV`（`prod`/`debug`，默认 `prod`）；`bringup.launch.py` 的 `enable_rosbridge` 默认值随之为 `debug=true`、`prod=false`，也可显式 `enable_rosbridge:=true` 覆盖
  - 验收：`WALLE_RUNTIME_ENV=prod` 启动后 9090 无监听；`WALLE_RUNTIME_ENV=debug` 启动才监听 9090，局域网其他主机仅在调试模式可连接

- [x] ✅ **P2-5.2 web_video_server 默认关闭（仅调试开启）**
  - 处理：`bringup.launch.py` 新增 `enable_web_video_server`，默认值同样由 `WALLE_RUNTIME_ENV` 控制（debug=true / prod=false），也可显式 `enable_web_video_server:=true` 覆盖
  - 验收：`WALLE_RUNTIME_ENV=prod` 启动后 8080 无监听，`/stream`、`/image_raw` 不可访问；仅调试模式开放

---

## 回归验收命令（参考）

> 涉及运动的测试必须把车架空，确认轮子悬空后再执行。

```bash
# 1) 局域网暴露面（在另一台电脑执行）
#    生产期望：9090 / 8080 均拒绝连接（rosbridge / web_video_server 默认关闭，仅 debug 开启）
nc -vz <车IP> 9090
nc -vz <车IP> 8080

# 1.1) rosbridge 话题白名单（在另一台电脑执行，需 pip install websocket-client）
#      被拒的 publish 不会回包，只在车上 rosbridge 日志打印
#      "No match found for topic, cancelling publish to: ..."
python3 - <<'PY'
import json, websocket
ws = websocket.create_connection("ws://<车IP>:9090")
for topic in ("/ros_robot_controller/internal/set_motor_speeds",
              "/ros_robot_controller/internal/enable_reception"):
    ws.send(json.dumps({"op": "publish", "topic": topic, "msg": {"data": []}}))
ws.close()
PY
# 在车上确认参数与拦截日志（期望两条 No match 记录）
ros2 param get /rosbridge_websocket topics_glob
journalctl -u walle-ai.service --since "1 min ago" | grep "No match found"

# 2) 参数泄露检查
ros2 param get /video_webrtc_bridge whip_secret

# 3) 密钥扫描
git grep -nE "sk-[A-Za-z0-9]{10,}|MQTT_PASSWORD=|WHIP_SECRET="

# 4) 进程与设备
ros2 node list
ros2 topic hz /image_raw
ls -l /dev/rrc /dev/v4l/by-id/

# 5) 失联停车（车架空）
# 发布前进指令后断开 Wi-Fi 或 kill rosbridge，观察轮子是否在 0.5s 内停止

# 6) 本机 ROS 隔离（在另一台电脑执行，需与车同一网段）
#    期望：看不到车上的节点/话题（车上为 ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST）
ros2 node list
ros2 topic list

# 7) 配置文件不入库（在仓库根目录执行）
#    期望：无输出；真实 walle-ai.conf 命中忽略规则
git ls-files '*.env' 'walle-ai.conf'
git check-ignore -v walle-device/scripts/walle-ai.conf

# 8) 生产配置权限与服务
ls -l /etc/walle-ai.conf                  # 期望 -rw------- 1 root root
systemctl cat walle-ai | grep -E "EnvironmentFile|ExecStart"
sudo -u gs cat /etc/walle-ai.conf         # 期望 Permission denied

# 9) 开发一键启动（读取 scripts/walle-ai.conf）
cd walle-device/scripts && ./start.sh
```

---

## 完成记录

| 日期 | 任务编号 | 提交/PR | 验证方式 | 验证人 |
| --- | --- | --- | --- | --- |
| 2026-11-08 | P0-1.1~P0-1.3 | 未提交 | 固件/协议/测试页已无 cmd 残留；`net_mqtt_bridge` 重建通过 | 待确认 |
| 2026-11-08 | P0-1.4 | 未提交 | 无 env 时节点构造报错退出；`--check` 输出缺失项；TLS 强制开启 | 待确认 |
| 2026-11-08 | P0-5.1 | 未提交 | `agent.py` 已无默认 broker/用户名；缺项时拒绝启动 | 待确认 |
| 2026-11-08 | P0-2.3 | 未提交 | `whip_secret` 不再是 ROS 参数；`ros2 param get` 应返回参数不存在 | 待确认 |
| 2026-11-08 | P0-3.1 | 未提交 | 前进中断开 cmd_vel 后 0.5s 内自动发零速度（需架空验证） | 待确认 |
| 2026-11-08 | P0-3.2 | 未提交 | `kill -TERM` 与串口断连后均尝试下发零速度（需架空验证） | 待确认 |
| 2026-11-09 | P0-3.4 | 未提交 | 阶跃指令按 10Hz 采样输出平滑递增，无单周期跳变（需架空验证） | 待确认 |
| 2026-11-09 | P0-3.5 | 未提交 | 本机 Jazzy rosbridge 实测：两个内部话题 publish 均被拒（No match），`/cmd_vel` 发布与 `/ros_robot_controller/battery` 订阅正常；真机待复测 | 待确认 |
| 2026-11-08 | P2-5.1 | 未提交 | `WALLE_RUNTIME_ENV=prod` 时 9090 无监听；`debug` 或显式传参才启动 | 待确认 |
| 2026-11-08 | P2-5.2 | 未提交 | `WALLE_RUNTIME_ENV=prod` 时 8080 无监听；`debug` 或显式传参才启动 | 待确认 |
| 2026-11-09 | P0-4.1 / P0-4.2 | 未提交 | 选择 4.1：干净环境 source 后 `ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST`；需两台车同网段复测互不可见 | 待确认 |
| 2026-11-09 | P0-5.2 | 未提交 | 模板统一为 `*.example`；`git ls-files '*.env' 'walle-ai.conf'` 为空，真实配置命中忽略 | 待确认 |
| 2026-11-09 | P0-5.3 | 未提交 | 88 个提交全历史扫描，仅占位符，无真实密钥 | 待确认 |
| 2026-11-09 | P0-5.4 | 未提交 | deploy.sh 完成配置检查 + 600 安装 + systemd 单元，`systemd-analyze verify` 通过；开发 `./start.sh` 读取 `walle-ai.conf`；真机待 `./deploy.sh` 复测 | 待确认 |
| 2026-11-09 | P1-1.1 | 未提交 | 关键节点已加 respawn；需实车逐个 `kill` 验证 5s 内恢复 | 待确认 |
| 2026-11-09 | P1-1.2 | 未提交 | `walle-ai.service` 已由 deploy.sh 安装并 enable，Restart/network-online 已配置；待整机重启与 kill 验证 | 待确认 |
| 2026-11-09 | P1-1.3 | 未提交 | 4 阶段启动 + `startup_check` 节点就绪判定与 `/bringup/startup_status`；已补充 `/image_raw` 发布者检查，待冷启动验证 | 待确认 |
| 2026-11-09 | P1-2.1 | 未提交 | README 补齐项目介绍/依赖/编译/udev/deploy/启动步骤；待按文档在全新环境复测 | 待确认 |
| 2026-11-09 | P1-2.3 | 未提交 | 新增 `doc/requirements.txt`，直接依赖锁定版本；apt 依赖在 README 说明 | 待确认 |
| 2026-11-09 | P1-2.2 | 未提交 | 职责拆分：deploy.sh 只做系统级安装；README 覆盖环境/依赖/build/udev；待按文档在全新系统复测 | 待确认 |
| 2026-11-09 | P1-2.4 | 未提交 | `WALLE_VENV` 可覆盖 venv 路径，site-packages 按实际 Python 版本取；默认路径与不存在路径均已验证 | 待确认 |
| 2026-11-09 | P1-3.1 | 未提交 | SDK 增加串口自动重连；编译通过；需实车拔插 USB 验证 | 待确认 |
| 2026-11-09 | P1-3.2 | 未提交 | 串口读写超时收紧 + MultiThreadedExecutor/回调组；编译通过；需实车验证卡顿不阻塞 | 待确认 |
| 2026-11-09 | P1-3.3 | 未提交 | 新增 `99-walle-camera.rules`，相机固定 `/dev/walle-camera`；待安装 udev 规则后重启验证 | 待确认 |
| 2026-11-09 | P1-3.4 | 未提交 | `99-rrc.rules` 创建 `/dev/rrc`（ttyAMA0，dialout 0660）；待全新镜像或 reload udev 验证 | 待确认 |
| 2026-11-09 | P1-3.5 | 未提交 | 声呐 I2C 异常不再吞掉，失败发布 -1 并节流告警；编译后待拔插验证 | 待确认 |
| 2026-11-09 | P1-4.1 | 未提交 | ffmpeg 日志按 7 天/10 个/100MB 清理；编译通过，待连续推流验证 | 待确认 |
| 2026-11-09 | P1-4.2 | 未提交 | MQTT 报文默认只记录主题与长度，DEBUG 才输出脱敏内容；编译通过 | 待确认 |
| 2026-11-09 | P1-5.1 | 未提交 | 新增远程命令移除回归测试，本地 pytest 3 项通过；待 P1-5.4 CI 接入 | 待确认 |
| 2026-11-09 | P1-5.2 | 未提交 | 新增运动学/协议/房间号单测共 60 项，三包 pytest 通过；controller 历史 lint 失败不影响用例 | 待确认 |
| 2026-11-09 | P1-5.3 | 未提交 | 新增 `stability_test.py`（kill/camera/network/soak）并完成冒烟；真机 24h 长稳待执行 | 待确认 |
|  |  |  |  |  |
|  |  |  |  |  |

---

## 备注

- 本清单中的“证据”行号基于 2026-11-08 的工作区版本，代码变更后请同步核对。
- 2026-11-08：P0-1.1~P0-1.3 已通过删除 `cmd/exec` / `cmd/result` 完成，详见
  `doc/MQTT_protocol_convention.md` §8 变更记录。
- 2026-11-08：P0-1.4 已删除默认 broker/用户名，新增 `validate_mqtt_config()`；
  无配置、缺租户或明文配置时节点拒绝启动，TLS 默认 8883 且强制开启。
  同一改动同时关闭了 P0-5.1（代码内兜底凭据）。
- 2026-11-08：P0-2.3 已移除 `whip_secret` 的 ROS 参数声明，只从 `/etc/walle-ai.conf`
  的 `WHIP_SECRET` 读取，避免 `ros2 param get/dump` 泄露明文。
- 2026-11-09：P1-5.1 新增 `test/test_remote_cmd_removed.py`，覆盖订阅主题、
  REQUEST_HANDLERS 与源码中的 remote_cmd/cmd/exec/handle_cmd_exec。
- 2026-11-09：P1-5.3 新增 `scripts/stability_test.py`：`kill` 验证节点自治恢复；
  `camera` 用 sysfs unbind/bind 模拟拔插；`network` 默认只拦截 Broker 流量，
  `--mode link` 断网卡并带延时恢复兜底；`soak` 采样 RSS/FD/磁盘/温度并判定泄漏。
  README 增加「稳定性测试（上线前）」操作步骤，串口掉线保留手动拔插验证。
- 2026-11-09：P1-5.2 新增三组单测：`test_mecanum.py` 覆盖四轮映射、
  Twist 限幅/加速度斜坡/看门狗停车；`test_protocol.py` 覆盖 uuid 校验、
  request_id 缺省与回传、订阅主题无通配符；`test_room.py` 覆盖房间号白名单。
  房间号校验抽为 `valid_room_number()` 并改用 `fullmatch`，
  `re.match` 会放过 `room/../x` 这类前缀匹配。
- 2026-11-09：P1-4.3 暂缓——远程日志文件上报作为新需求后续处理，
  指标上报（在线率/请求失败率/推流失败率）随该需求一起设计。
- 2026-11-09：P1-4.1 ffmpeg 日志按 7 天/10 个文件/100MB 清理；
  P1-4.2 MQTT 报文默认只记录主题与长度，DEBUG 才输出脱敏内容。
- 2026-11-09：P1-3.5 声呐 I2C 读取失败不再静默，节点发布 `-1` 无效值并节流告警，
  恢复后自动继续；RGB 写入与退出清理加异常保护。
- 2026-11-09：P1-3.3/P1-3.4 相机固定为 `/dev/walle-camera`（`99-walle-camera.rules`，
  usb_cam 不支持 by-id 深层链接）；udev 规则改为 `99-rrc.rules`
  （ttyAMA0 → `/dev/rrc`，dialout 0660），README 同步说明。
- 2026-11-09：P1-3.1/P1-3.2 `Board` 增加串口自动重连（1s 周期），读写异常关闭
  端口并重连；串口读 1s/写 0.5s/舵机读取 2s 超时；节点改用 MultiThreadedExecutor
  与 ReentrantCallbackGroup，避免串口卡顿阻塞 `/cmd_vel` 与状态查询（需实车验证）。
- 2026-11-09：P1-2.4 `env_walle.sh` 新增 `WALLE_VENV`（默认 `$HOME/venv`），
  venv 路径与 site-packages 均动态获取；缺 venv 时明确报错。
- 2026-11-09：P1-2.2 按职责拆分：`deploy.sh` 只做系统级安装（配置 + service），
  编译/依赖/udev 由 README 说明；脚本禁止 root 直接执行，可重复运行。
- 2026-11-09：P1-2.1 README 补齐 Ubuntu/ROS2/venv/依赖/编译/udev/部署步骤；
  P1-2.3 新增 `doc/requirements.txt`（锁定直接 pip 依赖，OpenCV/ffmpeg 走 apt）。
- 2026-11-09：P1-1.3 `bringup` 改为 4 阶段顺序启动，`startup_check`
  等待关键节点并检查 `/image_raw` 有发布者，就绪发布 `/bringup/startup_status`
  （`ready` / `timeout: missing=...`）；声呐、语音、AI 节点只告警不判失败。
- 2026-11-09：P1-1.1 已给底盘、usb_cam、声呐、语音、大模型节点加
  `respawn=True`；P1-1.2 的 `walle-ai.service` 由 `deploy.sh` 安装并 enable，
  已有 `Restart=always`/网络就绪，`WatchdogSec` 暂未启用（节点无 sd_notify）。
- 2026-11-09：P0-5.4 新增 `walle-ai.service.in` 与 `deploy.sh`：`deploy.sh`
  校验 `scripts/walle-ai.conf` 的 MQTT 必填项与 `WHIP_SECRET` 后，安装为
  `/etc/walle-ai.conf`（固定 `root:root 600`），systemd 通过 `EnvironmentFile`
  注入并直接 `source env_walle.sh` 启动；`start.sh` 仅用于开发环境。
  `WatchdogSec` 暂未启用（节点未实现 sd_notify，见 P1-1.2）。
- 2026-11-09：P0-5.2 已把 env 模板统一为 `*.example`
  （`scripts/walle-ai.conf.example`、`linux/walle-ai.conf.example`），仓库根与
  `walle-device/.gitignore` 忽略 `*.env`、`walle-ai.conf`；开发时先
  `cp walle-ai.conf.example walle-ai.conf`，生产由 `deploy.sh` 安装。
- 2026-11-09：P0-5.3 对 88 个提交做全历史扫描，仅发现占位符（`sk-你的新key`、`xxx`），
  未发现有效 `ALIYUN_API_KEY` / MQTT 密码 / `WHIP_SECRET`，无需轮换。
- 2026-11-09：P0-4 采用二选一中的 P0-4.1——`scripts/env_walle.sh` 新增
  `ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST`，所有节点仅本机发现；
  P0-4.2（按 device_id 派生 domain）不启用。注意：开发机将无法直接
  `ros2 topic echo` 车上话题，远程调试走 rosbridge（9090）。
- 2026-11-09：P0-3.5 已把低层电机占空比与上报开关接口移入
  `/ros_robot_controller/internal/*`，并在 `walle_net/launch/net.launch.py` 为
  rosbridge 配置 `topics_glob` 白名单；白名单只放行调试页所需的安全话题，
  新增话题需同步修改该列表，禁止使用 `set_*` 等会匹配到内部接口的通配。
- 2026-11-08：P0-3.1 已在 `mecanum.py` 增加 cmd_vel 看门狗（100ms 检查，
  默认 0.5s 超时发零速度）；语音、LLM 控制节点同步改为 10Hz 重发 cmd_vel，
  需架空实车验证。
- 2026-11-08：P0-3.2 已接管 SIGTERM/SIGINT，并在电机写失败、串口异常、
  遥测超时时尝试下发零速度；本车无 IMU，实测电池报文约 9Hz，已以其为主心跳，
  需架空实车验证。
- 2026-11-09：P0-3.4 已在 `mecanum.py` 增加速度限幅与加速度斜坡（默认
  1.0 m/s、3.0 rad/s、1.0 m/s²、3.0 rad/s²），看门狗急停不经斜坡；需架空验证。
- 2026-11-09：P0-3.3 阻塞于 MCU 固件源码缺失；需求见
  `doc/mcu-comm-timeout-requirement.md`，验证脚本 `tools/test_mcu_timeout.py`
  （需架空车轮）。
- 2026-11-08：rosbridge / web_video_server 定位为开发调试工具，`scripts/env_walle.sh` 新增
  `WALLE_RUNTIME_ENV`（prod/debug），`bringup` 的 `enable_rosbridge` /
  `enable_web_video_server` 默认值随之为 false/true；
  P0-2.1 / P0-2.2 降级为 P2-5.1 / P2-5.2，生产启动不再监听 9090 / 8080。
- P0 未清零前，不建议把设备接入非受控网络或交付给最终用户。
- 静态审查无法替代实车路测；P0-3 的验收必须在架空状态下逐项完成。
