# linux-demo 上线前修复清单

> 生成日期：2026-11-10
> 依据：`walle-device/linux-demo` 当前工作区代码静态审查 + git 历史核对（非渗透测试、非实机验证）
> 对照基线：`walle-device/doc/walle-launch-checklist.md`（ROS2 侧已整改项）与 `doc/MQTT_protocol_convention.md` §8 变更记录
> 状态图例：🔴 未解决　🟡 进行中　✅ 已解决
> 更新方式：完成一项把 `- [ ]` 改成 `- [x]`，把 🔴 改成 ✅，并在文末「完成记录」补一行；进行中则把 🔴 改成 🟡、复选框保持未勾选。
> 路径约定：除特别标注外，文中路径均相对于 `walle-device/linux-demo/`；跨目录路径会写全 `walle-device/...` 或 `doc/...`。

## 审查对象

| 文件 | 行数 | 说明 |
| --- | ---: | --- |
| `main.py` | 2807 | 非 ROS2 设备端主程序：MQTT、监控、摄像头能力、RTMP 推流、手机麦 WHEP 播放 |
| `whep_audio_player.py` | 223 | 手机麦上行 WHEP 拉流 → PortAudio 本机播放 |
| `README.md` | 34 | 部署说明（迁移后大幅删减） |
| `walle-ai.service.example` | 19 | systemd 单元模板 |

审查结论摘要（最严重的五项）：

1. ✅ 已修复：`cmd/exec` 远程命令能力已整体删除（2026-11-10，P0-1.1 / P0-1.2 / P0-1.5）。
2. ✅ 已修复：默认 Broker、账号与口令已删除，缺失配置拒绝启动；生产强制 TLS 8883（2026-11-10，P0-2.1 / P0-2.2）。
3. 部分修复：配置模板与 README 已恢复/更新，`requirements.txt` 与 `selftest_topics.py` 仍缺失（P1-1.1 / P2-2.5）。
4. ✅ 已修复：手机麦上行 WHEP 改为 Nginx `secure_link` + 设备短期 token（2026-11-10，P0-4.1）。
5. 推流/播放子进程使用 `start_new_session=True`，主进程被 `kill -9` 或崩溃后 ffmpeg/播放器仍会继续运行（摄像头/麦克风持续工作）。

## 进度总览

| 优先级 | 任务数 | 已完成 | 剩余 |
| --- | ---: | ---: | ---: |
| P0 上线阻断 | 15 | 11 | 4 |
| P1 上线前应修 | 18 | 0 | 18 |
| P2 后续优化 | 22 | 1 | 21 |
| **合计** | **55** | **12** | **43** |

> linux-demo 已开始按本清单整改（当前 12/55）；每次勾选后请同步更新本表数字。
> P0-1.3 / P0-1.4 进行中：ACL 与审计规则已写入 `doc/service_deploy.md`，待服务端实施验证。

## 与 ROS2 清单的适用性对照

| ROS2 清单条目 | 在 linux-demo 的适用性 |
| --- | --- |
| P0-2 ROS 网络暴露面（rosbridge / web_video_server） | 不适用：无 ROS 节点、无监听端口；对应风险改为 MQTT 出站连接与音视频流鉴权（P0-2、P0-4） |
| P0-3 底盘失联保护（cmd_vel 看门狗、串口停车、MCU 超时） | 不适用：不控制底盘；对应风险改为子进程与设备占用安全（P0-5） |
| P0-4 多车同域串控（ROS_DOMAIN_ID） | 不适用：不使用 ROS 发现 |
| P1-3.1 / P1-3.2 串口断连重连、串口 I/O 不阻塞 executor | 不适用：无串口；外设断连见 P1-3 |
| P2-3 AI 演示节点 | 不适用：linux-demo 不含 AI 节点 |
| P2-4 usb_cam 控制名、多 video 节点固定设备 | 部分适用：摄像头 preflight 与固定设备见 P0-4.2、P2-3.1 |
| P2-5 rosbridge / web_video_server 调试开关 | 不适用：无该调试服务 |

---

## P0 上线阻断项（必须全部清零）

### P0-1 远程命令执行面

> `doc/MQTT_protocol_convention.md` §8（2026-11-08）已明确删除 `cmd/exec` → `cmd/result`，
> 并要求固件与测试页 `grep -R "cmd/exec\|cmd/result\|remote_cmd"` 无残留。
> 2026-11-10 已将该整改回移到 linux-demo，P0-1.1 / P0-1.2 / P0-1.5 一并关闭。

- [x] ✅ **P0-1.1 删除 `cmd/exec` 远程命令能力**
  - 处理：删除 `REQ_CMD` / `RES_CMD_RESULT`、`handle_cmd_exec` / `_publish_cmd_result` / `_truncate`、订阅与 `REQUEST_HANDLERS` 映射、`build_capability()` 中的 `remote_cmd`，以及 `ENABLE_CMD_EXEC` / `CMD_ALLOWLIST` / `CMD_TIMEOUT_*` / `CMD_OUTPUT_LIMIT`；模块 docstring 协议表同步删除
  - 验收：源码无 `cmd/exec` / `cmd/result` / `remote_cmd` / `handle_cmd_exec` / `shell=True`；`subscriptions()` 由 8 个减为 7 个；`--check` 订阅清单无命令主题；`py_compile` 通过
  - 证据：整改后 `main.py` 已无相关符号；`doc/MQTT_protocol_convention.md` L551-L585

- [x] ✅ **P0-1.2 禁止 shell 拼接与前缀白名单绕过**
  - 处理：随 P0-1.1 整体删除命令能力，源码中已无 `subprocess.run(..., shell=True)` 路径
  - 验收：`grep -n "shell=True" walle-device/linux-demo/main.py` 无匹配
  - 证据：`main.py`（原 L2542-L2570 已删除）

- [ ] 🟡 **P0-1.3 uuid 不再作为动作鉴权凭证**
  - 现状：`uuid` 仍随 discovery/online 明文下发；代码 `verify_uuid()` 只做请求配对。访问边界已改由 Broker ACL 承担，部署文档 §10 明确「uuid 不是鉴权凭证」，并要求每台设备独立账号仅访问自己的子树
  - 动作：按 `doc/service_deploy.md` §10 在 EMQX 落地 ACL；设备侧保持 `uuid` 仅用于请求配对，不再作为安全边界
  - 验收：只拿到 discovery/online 报文（含 uuid）无法发布动作主题；设备 A 账号访问设备 B 主题被拒（§10.1 四条命令）
  - 证据：`doc/service_deploy.md` §10、§10.1；`main.py` 的 `verify_uuid` / `build_device_info` / `discovery/query`

- [ ] 🟡 **P0-1.4 Broker 按设备 ACL + 动作审计**
  - 现状：`doc/service_deploy.md` §10 已补每设备独立账号、`walle/+/${clientid}/#` 规则与 `{deny, all}.`；§10.2 已补 EMQX 规则引擎审计（来源账号/clientid/topic/payload/timestamp → `walle/audit/...`），设备日志可按 `request_id` 关联结果；服务端尚未实际配置验证
  - 动作：按 §10 / §10.1 / §10.2 在 EMQX 落地并复测；设备侧保持动作结果日志
  - 验收：A 账号无法订阅/发布 B 主题；触发一次 video/phone audio 动作后 `walle/audit/#` 收到来源与时间；`journalctl -u walle-ai | grep <request_id>` 可还原结果
  - 证据：`doc/service_deploy.md` §10、§10.1、§10.2、§11 第 9-11 项

- [x] ✅ **P0-1.5 远程命令子进程不得继承密钥环境变量**
  - 处理：随 P0-1.1 删除远程命令能力，不再存在由 MQTT 触发的任意子进程；ffmpeg / 播放器仍继承环境变量，但只接受固定参数，不构成命令执行面
  - 验收：源码无 `handle_cmd_exec` / `shell=True`；无远程命令入口
  - 证据：`main.py`（原 L2503-L2589 已删除）

### P0-2 MQTT 连接与配置安全

- [x] ✅ **P0-2.1 删除代码内默认 Broker 与默认凭据**
  - 处理：`MQTT_HOST` / `MQTT_USERNAME` / `MQTT_PASSWORD` / `MQTT_TENANT` 改为空默认值；`MQTT_PORT` 不再默认 1883，空值或非整数记为 `None`；新增 `validate_config()` 校验五项必填与端口范围；`main()` 正式启动前校验并以退出码 2 拒绝启动，`--check` 返回 1 并列出缺失项
  - 验收：无配置时 `--check` 退出 1 并列出 4 个缺失项（端口在 P0-2.2 后默认 8883），`python3 main.py` 退出 2；合法配置 `--check` 退出 0；`MQTT_PORT=abc` 返回友好错误而非 traceback；源码无 `RaspPi5` / `123456` / MQTT 默认域名
  - 证据：`main.py` L140-L158、L206-L246、L2566-L2579、L2630-L2697

- [x] ✅ **P0-2.2 强制 TLS 8883，禁止明文回退**
  - 处理：`MQTT_PORT` 默认 8883；`MQTT_TLS` 默认 `on`，仅接受 `on/off`；生产环境（`WALLE_RUNTIME_ENV=prod`）拒绝 `MQTT_TLS=off` 与端口 1883；仅 `WALLE_RUNTIME_ENV=debug` 允许显式关闭 TLS；`MQTT_CA_FILE` 不存在/不可读时拒绝启动；`run()` 加载 CA 失败时以明确错误退出
  - 验收：默认配置 `TLS=on`、端口 8883；`MQTT_TLS=off` 在 prod 下退出 1、debug 下通过；`MQTT_TLS=auto` 退出 1；`MQTT_PORT=1883` 在 prod 下退出 1；`MQTT_CA_FILE=/no/such` 退出 1；可读 CA 通过
  - 证据：`main.py` L140-L158、L221-L246、L2611-L2626、L2658-L2666

- [x] ✅ **P0-2.3 配置文件名、权限、模板与 gitignore 统一**
  - 处理：配置统一为 `/etc/walle-ai.conf` + 脚本同级 `walle-ai.conf`（`ENV_CANDIDATES`、docstring 同步）；新增 `walle-ai.conf.example`；新增 `linux-demo/.gitignore` 忽略 `walle-ai.conf` / `walle-ai.env` / `walle-ai.service` / `.device-id` / `log/`；systemd 改为 `EnvironmentFile=/etc/walle-ai.conf`（去掉 `-`）；`_load_env_file()` 在配置不可读且必需变量已注入时只打印 info，不再建议 `chown`；README 补充 600 root:root 安装方式
  - 验收：`git ls-files` 无真实配置/服务文件；`git check-ignore` 四类文件均命中；开发环境自动读取脚本同级 `walle-ai.conf`（实测通过）；不可读配置且无环境变量时提示改用 systemd 注入；普通用户读取 `/etc/walle-ai.conf` 被拒绝；systemd 缺配置时的启动失败待实机验证
  - 证据：`main.py` L76-L129、`walle-ai.conf.example`、`linux-demo/.gitignore`、`walle-ai.service.example` L11、`README.md`

- [x] ✅ **P0-2.4 启动前配置校验**
  - 处理：`validate_config()` 已校验必填项、端口范围、运行环境、TLS 模式、生产端口 1883、CA 可读性；模板非空时校验 `{room}`/`{stream}` 占位符及 `{secret}`/`WHIP_SECRET` 匹配；空模板不阻塞仅监控用途的启动，但 `--check` 与启动日志输出警告，动作入口返回 -2
  - 验收：删除任一必填项、非法端口、坏 CA、模板缺占位符或 secret 时 `--check` 返回非 0；空模板仅告警；正式启动同样先校验 MQTT 配置
  - 证据：`main.py` L221-L264、L1294-L1325、L2737-L2748、L2763-L2771

- [x] ✅ **P0-2.5 推流/拉流地址去硬编码**
  - 处理：删除 `VIDEO_RTMP_URL_TEMPLATE` / `PHONE_AUDIO_WHEP_URL_TEMPLATE` 的生产域名默认值；新增 `rtmp_url_ready()` / `whep_url_ready()`，video/phone audio 动作入口校验模板、占位符与 `WHIP_SECRET`，未就绪返回 -2；`walle-ai.conf.example` 提供模板占位符与能力开关
  - 验收：`grep -R "baidong" walle-device/linux-demo` 无匹配；空模板时两个动作均返回 -2 并给出明确原因（mock 冒烟通过）；完整模板时 `build_rtmp_url` 正常生成；`--check` 显示模板状态与告警
  - 证据：`main.py` L409-L445、L1294-L1325、L2022-L2027、L2324-L2333；`walle-ai.conf.example`

### P0-3 密钥与历史泄露

- [ ] 🔴 **P0-3.1 全历史扫描并轮换已暴露凭据**
  - 现状：当前 HEAD 即包含 `MQTT_PASSWORD=123456`、`RaspPi5`、`www.baidong.xyz`；历史提交 `054db6c` 明确「给 MQTT 服务默认配置值，仅用于演示」；迁移提交 `947f445` 删除的 `walle-ai.env.example` 也含域名与账号示例
  - 动作：对全部历史执行 `git log --all -p --text`，扫描 `MQTT_PASSWORD`、`WHIP_SECRET`、`sk-`、`ALIYUN_API_KEY`、私钥/令牌特征；确认 `123456` / `RaspPi5` 是否为真实可用账号，若是立即在 Broker 改密并同步 App/设备；`WHIP_SECRET` 若曾以明文提交则重置；整改后增加提交前检查
  - 验收：形成历史扫描报告；Broker 侧旧口令失效；`git grep -nE "MQTT_PASSWORD=|WHIP_SECRET="` 无真实值
  - 证据：`main.py` L143-L146；`git log --all -S 'MQTT_PASSWORD'`、`-S 'www.baidong.xyz'`、`-S 'RaspPi5'`；commit `054db6c`、`947f445`

### P0-4 音视频流鉴权与隐私

- [x] ✅ **P0-4.1 手机麦克风上行 WHEP 拉流鉴权**
  - 处理：新增 Nginx 8443 `/whep-audio/` 入口与 `secure_link` 校验（`doc/service_deploy.md` §6.2）；设备端新增 `PHONE_AUDIO_WHEP_SECRET` / `PHONE_AUDIO_WHEP_PATH` / `PHONE_AUDIO_WHEP_TOKEN_TTL`，`_phone_audio_url()` 生成 `expires` + `token`（base64url(md5(expires+path+stream+secret))），`whep_url_ready()` 校验模板/secret/TTL；日志对 `token=` 脱敏
  - 验收：无 token 403、过期 410、正确 token 502（Nginx 服务端实测待执行）；设备端 token 与独立 MD5 计算一致（mock 验证通过）；缺少 `{expires}` / `{token}` 或 secret 时 `--check` 返回 1
  - 证据：`main.py` L451-L461、L1321-L1339、L2185-L2196；`walle-ai.conf.example`；`doc/service_deploy.md` §6.2、§11 第 13 项

- [x] ✅ **P0-4.2 摄像头设备白名单与 preflight**
  - 处理：新增 `CAMERA_DEVICE`（可选固定设备，支持 udev 符号链接）；`camera_preflight()` 校验 realpath 为 `/dev/videoN`、sysfs 路径含 `usb`、`v4l2-ctl` 支持 `VIDEO_VIDEO_SIZE`（不强制 MJPG，ffmpeg 使用默认格式）；`handle_video_request` 在启动 ffmpeg 前调用，失败返回 -1；`--check` 校验 `CAMERA_DEVICE` 是否存在
  - 验收：ISP/解码/元数据节点（如 `/dev/video19`）被拒；不支持 640x480 的摄像头被拒；未安装 v4l2-ctl 返回明确原因；合法 USB 摄像头通过；`CAMERA_DEVICE` 不存在时 `--check` 退出 1
  - 证据：`main.py` L419-L423、L257-L258、L977-L1008、L2067-L2077；`walle-ai.conf.example`

### P0-5 子进程与退出安全

- [x] ✅ **P0-5.1 主进程崩溃后清理推流/播放子进程**
  - 处理：新增 `_pdeathsig_preexec()`，通过 `PR_SET_PDEATHSIG` 让子进程在父进程退出/被 `kill -9` 时收到 `SIGTERM`；ffmpeg 与 WHEP 播放器的 `Popen` 均接入；保留 `start_new_session=True`，systemd `KillMode=control-group` 作为兜底
  - 验收：子进程 `PR_GET_PDEATHSIG` 返回 15（SIGTERM，自测通过）；父进程被 `kill -9` 后 ffmpeg/播放器应随之退出（实机待验证）；服务重启后无重复推流
  - 证据：`main.py` L1525-L1552、L2035-L2042、L2364-L2371

- [ ] 🔴 **P0-5.2 优雅退出不被长耗时任务阻塞**
  - 现状：所有请求共用一个 4 线程池；`handle_cmd_exec` 最长 300s，`capability` 探测最坏数十秒；`pool.shutdown(wait=False)` 后解释器仍会等待非 daemon 的池线程，systemd `TimeoutStopSec` 超时后会 SIGKILL，可能跳过子进程清理
  - 动作：随 P0-1.1 删除长命令；其余处理函数设置明确上限；信号处理中主动终止子进程并限制退出时间；systemd 设置 `TimeoutStopSec=10`、`KillMode=mixed/control-group`
  - 验收：执行一个长耗时请求期间 `systemctl stop walle-ai` 在 5s 内退出；退出后无 ffmpeg / 播放器残留
  - 证据：`main.py` L1405、L2528-L2589、L2729-L2743

---

## P1 上线前应修

### P1-1 部署可复现

- [ ] 🔴 **P1-1.1 恢复 linux-demo 部署文档、依赖清单与自检工具**
  - 现状：迁移提交 `947f445` 删除了 `requirements.txt`、`walle-ai.env.example`、`selftest_topics.py`，README 从 284 行压缩到 34 行；`main.py` docstring 引用的 README「首次部署」章节已不存在；`walle-device/doc/requirements.txt` 不含 `aiortc` / `sounddevice`，但 `whep_audio_player.py` 依赖它们；`doc/MQTT_protocol_convention.md` §7 仍把 linux-demo README 作为设备端部署说明
  - 动作：恢复 `requirements.txt`（`paho-mqtt`、`psutil`、`aiortc`、`sounddevice`）；恢复配置模板；重写 README：系统依赖（`python3-venv`、`v4l-utils`、`ffmpeg`、`pulseaudio-utils`、`libportaudio2`）、venv、pip、配置、安装、自检、升级、日志；`doc/requirements.txt` 补充或拆分音视频依赖；修复手动运行命令
  - 验收：按文档在全新 Raspberry Pi OS 上完成部署；`pip install -r requirements.txt` 后 `main.py --check` 通过，`whep_audio_player.py` 可启动；README 中所有命令可直接复制执行
  - 证据：`README.md` L7-L31；`main.py` L39-L52；`git show 947f445 --stat`；`walle-device/doc/requirements.txt` L7-L14

- [ ] 🔴 **P1-1.2 修复 systemd 单元模板**
  - 现状：`User=gs` 硬编码；注释要求替换 `$WALLE_PATH`，README 却写 `$WALLE_HOME`；`ExecStart=$VENV_PATH/venv/bin/python` 与 README 的 `/home/ubuntu/venv/python3` 不一致（会得到 `venv/venv/bin/python`）；`EnvironmentFile=-/etc/walle-ai.env` 允许缺文件启动；无启动频率限制与沙箱加固
  - 动作：统一变量为 `WALLE_HOME` / `WALLE_VENV`，由部署脚本渲染实际用户与路径；`ExecStart=$WALLE_VENV/bin/python $WALLE_HOME/main.py`；`EnvironmentFile=/etc/walle-ai.conf`（去掉 `-`）；增加 `StartLimitIntervalSec` / `StartLimitBurst`、`TimeoutStopSec`、`KillMode`、`NoNewPrivileges=true`、`ProtectSystem=strict`、`ProtectHome=read-only`、`PrivateTmp=true`、`ReadWritePaths=<log/device-id 目录>`、`SupplementaryGroups=audio video`
  - 验收：`systemd-analyze verify walle-ai.service` 通过；新机器按文档替换变量后可启动；缺配置时启动失败并提示；`systemctl stop` 5s 内完成
  - 证据：`walle-ai.service.example` L6-L16；`README.md` L11-L31

- [ ] 🔴 **P1-1.3 提供 linux-demo 部署脚本**
  - 现状：`walle-device/scripts/deploy.sh` 只处理 ROS2（`/etc/walle-ai.conf` + ROS2 服务）；linux-demo 完全依赖手工复制与 `sed`，且步骤易错
  - 动作：新增 `linux-demo/deploy.sh`，或扩展 `scripts/deploy.sh` 支持 `--target linux-demo|ros2`；脚本负责配置检查、600 安装、service 渲染与 enable、可选 udev 规则；按执行用户动态推导 `User`/`Group`，不硬编码用户名
  - 验收：脚本可重复执行无副作用；不同用户名/路径环境可安装成功；`systemctl status walle-ai` 正常
  - 证据：`walle-device/scripts/deploy.sh`；`README.md` L11-L24

- [ ] 🔴 **P1-1.4 版本号读取一致性**
  - 现状：`main.py` 优先读取 `main.py` 同级 `VERSION`，其次 `../VERSION`（即 `walle-device/VERSION`）；单独部署 linux-demo 时两者都不存在，回退到硬编码 `VERSION_NAME=0.1.1`；`VERSION_CODE` 默认 1
  - 动作：部署时一并安装 `VERSION`；找不到文件时至少打印明确警告（或拒绝启动）；增加测试覆盖「环境变量 > 文件 > 默认值」优先级；文档说明与 Android `versionName` 的同步要求
  - 验收：单独部署目录放入 `VERSION` 后 `--check` / `discovery/result` 返回文件中的版本；删除 `VERSION` 有明确警告；`WALLE_VERSION_NUM/NAME` 覆盖生效
  - 证据：`main.py` L215-L255；`walle-device/VERSION`

### P1-2 进程守护与启动可靠性

- [ ] 🔴 **P1-2.1 启动健康检查与 Broker 不可达重试**
  - 现状：`client.connect()` 首次失败直接抛异常退出，依赖 systemd 反复重启；没有就绪标记；启动前不检查 `ffmpeg` / `v4l2-ctl` / `pactl` / 播放器脚本是否存在
  - 动作：实现「配置校验 → 依赖检查 → 连接 Broker（带指数退避或 paho 重连）→ 订阅成功 → 打印 `STARTUP READY`」；缺失可选依赖时告警但不阻塞；`--check` 覆盖依赖检查
  - 验收：Broker 不可达时日志持续重试而不是快速 crash-loop；Broker 恢复后自动上线；冷启动日志出现 `STARTUP READY`；缺失 ffmpeg 时 capability 明确反映推流不可用
  - 证据：`main.py` L2685-L2727、L2780-L2803

- [ ] 🔴 **P1-2.2 断线重连、LWT 与发布失败处理**
  - 现状：`clean_session=True` 每次重连重新订阅，LWT 使用 retained offline；但 `_publish_one()` 不检查 `publish()` 返回码，断线期间的动作结果可能静默丢失；`on_connect` 失败只记录 reason_code
  - 动作：检查 `MQTT_ERR_NO_CONN` / `rc` 并记录；正常退出时 `publish(...).wait_for_publish(timeout)` 后再断开；增加 `on_connect_fail` 日志；验证重连后 retained online 覆盖旧值
  - 验收：断网 1 分钟后恢复，App 看到 online 且请求可用；断线期间的动作请求不会留下「已成功」假象；无幽灵 online
  - 证据：`main.py` L1441-L1458、L1497-L1508、L2726-L2743

### P1-3 外设断连与降级

- [ ] 🔴 **P1-3.1 推流进程异常退出的上报与恢复**
  - 现状：`_video_watch()` 仅在本地日志记录退出码，不向 App 发布事件；App 仍认为推流中，画面黑屏无感知；不自动重试
  - 动作：异常退出时发布 `alarm`（新增 `video_lost`）或主动 `video/stop/result` 事件，携带 room、device、exit code、日志尾部；可选有限次自动重启；恢复成功后清除告警
  - 验收：拔掉摄像头后 5s 内 App 收到告警；插回后可重新 `video/start` 成功；日志可按 request_id 关联
  - 证据：`main.py` L1841-L1858、L1534-L1552

- [ ] 🔴 **P1-3.2 手机音频播放异常退出的上报**
  - 现状：`_phone_audio_watch()` 只写日志；`whep_audio_player.py` 已定义退出码 3（缺依赖）/4（首帧超时）/5（WHEP 失败）/6（声卡失败），但设备端未上报给 App
  - 动作：异常退出时发布 `alarm` 或动作结果事件，按退出码给出可读原因与处理建议；正常停止不告警
  - 验收：停止 PulseAudio / 拔声卡 / 手机停止推流后 App 能收到对应告警；重新 `phone/audio/send` 可恢复
  - 证据：`main.py` L2169-L2186；`whep_audio_player.py` L13、L143-L153、L165-L180

- [ ] 🔴 **P1-3.3 PulseAudio 会话依赖与 systemd 运行方式**
  - 现状：linux-demo 作为系统级服务运行时默认没有用户音频会话；`pactl`、`ffmpeg -f pulse`、`sounddevice` 可能全部失败。`detect_microphone()` 会降级为纯视频，用户无感知；手机音频则直接失败
  - 动作：文档明确要求 `systemd --user` + `loginctl enable-linger <user>`，或为系统服务设置 `XDG_RUNTIME_DIR` / `PULSE_SERVER`；启动时检测音频会话并打印明确原因；`capability/result` 的 `audio.capture/playback.reason` 反映会话问题
  - 验收：按文档配置后系统服务可正常使用麦克风/音响；未配置时 App 能看到明确不可用原因，而不是静默无音频
  - 证据：`main.py` L1043-L1143、L1696-L1748、L2685-L2727；`walle-ai.service.example`

- [ ] 🔴 **P1-3.4 摄像头节点变化与能力刷新**
  - 现状：`capability/query` 每次实时探测（结果最新但慢）；推流使用 App 传入的 `/dev/videoN`，重新插拔后编号可能变化；旧节点 start 只返回「设备节点不存在」
  - 动作：推流失败时返回当前可用摄像头列表；USB 插拔事件（`check_usb`）触发能力缓存失效；可选 udev 固定 `/dev/walle-camera`
  - 验收：重新插拔后重新查询 capability 能拿到新节点；对旧节点 start 返回新节点列表与明确原因
  - 证据：`main.py` L1577-L1591、L1925-L1946、L926-L986

- [ ] 🔴 **P1-3.5 推流/播放缺少租约与空闲回收**
  - 现状：`video/start` 与 `phone/audio/send` 没有过期时间；App 崩溃/断网后设备会一直推流或播放；MQTT 断连不会停止流；WHEP 播放器在 track 停滞时只持续 0.5s 超时循环，不会自行退出
  - 动作：增加最大时长与空闲超时（如 `VIDEO_MAX_DURATION_SEC`、`PHONE_AUDIO_IDLE_TIMEOUT_SEC`），到期自动停止并发布事件；可选在 MQTT 断开超过 N 秒后停止所有流；播放器增加无帧超时退出
  - 验收：启动推流后不续约会自动停止；App 被杀后摄像头/麦克风在设定时间内释放；无残留子进程
  - 证据：`main.py` L1424-L1431、L1925-L2026、L2188-L2286、L1457-L1458；`whep_audio_player.py` L165-L180

### P1-4 日志与可观测性

- [ ] 🔴 **P1-4.1 ffmpeg 日志保留策略**
  - 现状：每次推流新建 `log/ffmpeg_{time}.log`；单文件超过 20MB 只滚动为 `.1`，没有按天/数量/总容量的清理，长期运行会持续占用磁盘
  - 动作：参考 ROS2 `video_webrtc_bridge`，按「保留 7 天 / 最多 10 个文件 / 总量 100MB」清理最旧日志；清理在每次新建日志前执行，参数可配置
  - 验收：多次启停推流、连续运行后日志总量受控；不影响正在写入的文件
  - 证据：`main.py` L387-L393、L1275-L1390

- [ ] 🔴 **P1-4.2 MQTT 报文日志降级与脱敏**
  - 现状：`on_message()` 在 INFO 级别打印 `payload[:500]`，包含 uuid、房间号、设备信息，整改前还包含命令内容；`cmd/result` 会把 stdout/stderr 写入日志
  - 动作：默认只记录主题与字节数；完整报文改为 DEBUG，并对 `password`/`secret`/`token`/`key`/`room` 等字段脱敏后截断；删除命令能力后移除 stdout/stderr 日志路径
  - 验收：生产 INFO 日志不包含报文内容与敏感字段；DEBUG 日志中 secret 显示为 `***`
  - 证据：`main.py` L1460-L1469、L2591-L2618

- [ ] 🔴 **P1-4.3 远程日志与关键指标（暂缓）**
  - 状态：暂缓——与 ROS2 清单 P1-4.3 保持一致，作为新需求后续统一设计
  - 动作：接入 journald/远程日志；上报在线率、请求失败率、推流失败率、CPU/温度
  - 验收：现场故障可远程定位，不依赖 SSH

### P1-5 测试与 CI

- [ ] 🔴 **P1-5.1 远程命令移除回归测试**
  - 动作：新增 `test/test_remote_cmd_removed.py`，断言 `subscriptions()` 不含 `cmd/exec`、`REQUEST_HANDLERS` 不含 `handle_cmd_exec`、`capabilities` 不含 `remote_cmd`，且包内源码不含 `shell=True` / `cmd/exec`
  - 验收：本地 pytest 通过；CI 接入后任何回流都会失败
  - 证据：ROS2 侧同类测试 `walle-device/ros2_ws/src/net/net_mqtt_bridge/test/test_remote_cmd_removed.py`

- [ ] 🔴 **P1-5.2 协议与配置单元测试**
  - 动作：覆盖 `sanitize_segment`（与 Android `TopicNames` 一致）、`device_topic`、`subscriptions` 主题配对、`verify_uuid`、`VIDEO_ROOM_PATTERN`（含 `../`、超长、空值）、`mask_rtmp_url`、`resolve_device_id` 落盘/复用/换 MAC、TLS 判定、`validate_config` 缺失项
  - 验收：新增 `test/` 目录可被 pytest 自动发现；全部用例通过并纳入 CI
  - 证据：`main.py` L258-L340、L676-L724、L2462-L2485

- [ ] 🔴 **P1-5.3 故障注入与长稳测试**
  - 动作：复用 `walle-device/scripts/stability_test.py` 思路，覆盖 `kill -9`（验证子进程清理）、断网/恢复、摄像头解绑/绑定、声卡不可用、MQTT Broker 重启、24h soak（RSS/FD/磁盘/温度）
  - 验收：`kill -9` 后无 ffmpeg/播放器残留；断网恢复后自动上线；24h 无泄漏趋势
  - 证据：`walle-device/scripts/stability_test.py`；`main.py` L1841-L1858、L2169-L2186

- [ ] 🔴 **P1-5.4 CI 覆盖 linux-demo**
  - 现状：`.github/workflows/ci.yml` 的 `Critical lint` 覆盖 `walle-device`（包含 linux-demo 的语法级检查），但 `Lint scripts and tests` 不包含 linux-demo，也没有 linux-demo 的 pytest
  - 动作：将 `walle-device/linux-demo` 纳入严格 flake8；新增 linux-demo 测试 job（安装 `requirements.txt` 后运行 pytest）
  - 验收：PR 触发 lint + linux-demo 测试；故意引入未定义名/语法错误时 CI 失败
  - 证据：`.github/workflows/ci.yml` L29-L41、L71-L82

---

## P2 后续优化

### P2-1 性能

- [ ] 🔴 **P2-1.1 state/query 复用监控采样快照**
  - 现状：`build_status()` 在请求线程内调用 `sample_once(1.0)`，固定阻塞 1 秒；4 个工作线程容易被状态查询占满
  - 动作：`monitor_loop` 每轮采样后写入快照，`build_status()` 直接复用；USB 列表复用 `check_usb()` 结果；应答新增 `sample_age_sec`（与 `doc/MQTT_protocol_convention.md` 2026-11-10 变更一致）；首份快照未建立时才兜底采样
  - 验收：`state/query` 处理 < 100ms；连续查询不触发重复 1s 采样
  - 证据：`main.py` L2317-L2330、L2630-L2681

- [ ] 🔴 **P2-1.2 capability 探测异步化与缓存**
  - 现状：`build_capability()` 每次请求都同步执行 `v4l2-ctl`（每节点最多 8s）与 `pactl`（3s），最坏可达数十秒，阻塞请求线程
  - 动作：后台线程启动即探测并按 TTL（默认 120s）刷新；查询只读缓存；首次未就绪最多等待 3s 或立即返回 `probe.pending`；应答新增 `cache {source, age_sec, ttl_sec}`（与协议 2026-11-10 变更一致）；探测失败保留旧缓存
  - 验收：`capability/query` 只读缓存；热插拔后 TTL 内不刷新、过期后刷新；探测失败不会把已有能力清空
  - 证据：`main.py` L1696-L1748、L926-L986

- [ ] 🔴 **P2-1.3 查询/动作请求分池与有界队列**
  - 现状：所有请求共用 `ThreadPoolExecutor(max_workers=4)`；推流启动（宽限 3s）、能力探测、整改前的长命令会占满线程，查询请求排队
  - 动作：查询（state/capability/discovery）与动作（video/phone audio）分池；用 `BoundedSemaphore` 限制「执行中 + 排队」总量；超限丢弃并周期性汇总告警
  - 验收：动作池占满时 state/query 仍能响应；洪峰下丢弃有日志、有计数
  - 证据：`main.py` L1405、L1510-L1532

- [ ] 🔴 **P2-1.4 discovery 抖动不占用工作线程**
  - 现状：`handle_device_query()` 在池线程内 `time.sleep(random.uniform(0, 0.8))`，多台设备同时刷新时会占用查询线程
  - 动作：改为 `threading.Timer` / 调度队列延迟发布，处理函数立即返回
  - 验收：discovery 洪峰下查询池不被抖动 sleep 占满
  - 证据：`main.py` L1668-L1692

- [ ] 🔴 **P2-1.5 在线/遗嘱报文避免慢采集**
  - 现状：`build_device_info()` 会调用 `disk_mounts()` 等可能阻塞的系统调用；`_will_message()` 在启动连接前构造遗嘱，若存在卡死的网络挂载，可能拖住启动；retained online 报文也包含完整身份信息
  - 动作：在线/遗嘱只使用轻量缓存信息；磁盘等慢采集放到监控线程；必要时限制 `disk_partitions` 范围
  - 验收：无网络挂载/挂载卡死时启动与 LWT 构造不被阻塞
  - 证据：`main.py` L1435-L1437、L1595-L1666

### P2-2 代码质量

- [ ] 🔴 **P2-2.1 按职责拆分 main.py**
  - 现状：单文件 2807 行，配置、身份、MQTT 协议、监控、能力探测、推流、手机音频、命令行全部混在一起
  - 动作：参考 ROS2 `net_mqtt_bridge`，拆为 `config` / `identity` / `monitor` / `protocol` / `capability` / `video` / `phone_audio` / `audio` / `cmdline` 等模块，单文件 < 500 行；`main.py` 只保留入口与组合逻辑，保持 CLI、环境变量、MQTT 主题与报文完全兼容
  - 验收：各模块可独立导入；全部 `.py` < 500 行；拆分前后 `--check`、主题订阅清单、报文结构一致
  - 证据：`main.py` 全文件；`walle-device/doc/walle-launch-checklist.md` P2-2.1

- [ ] 🔴 **P2-2.2 清理重复实现与遗留注释**
  - 现状：`_video_status()` 注释仍写 `state/result`，但段落标题是 `device/detail/result`；`import ssl` 在 `run()` 内局部导入；`build_rtmp_url()` 被重复调用并在多处拼接/脱敏；`_load_env_file()` 不处理行内注释
  - 动作：统一术语与导入；缓存 URL 构造结果；解析配置时支持行内注释与转义
  - 验收：`flake8` 通过；日志/注释术语一致；同一推流只构造一次 RTMP URL
  - 证据：`main.py` L98-L120、L1253-L1262、L1868-L1874、L2006-L2013、L2428-L2458

- [ ] 🔴 **P2-2.3 capability 按实际配置上报**
  - 现状：`capabilities` 无条件包含 `remote_cmd`；即使 `rtmp_secret_ready()` 为假、视频实际不可用，也仍按 `VIDEO_ENABLE` 上报 `rtmp_push`
  - 动作：`remote_cmd` 随 P0-1.1 删除；`rtmp_push` 需同时满足 `VIDEO_ENABLE` 与模板/secret 就绪；音频能力与实际检测结果一致
  - 验收：未配置 `WHIP_SECRET` 时 `capabilities` 不含 `rtmp_push`，或附带明确不可用原因；App 不再显示必失败入口
  - 证据：`main.py` L1696-L1748、L1264-L1266

- [ ] 🔴 **P2-2.4 `--check` 扩展为完整自检**
  - 动作：增加配置校验、依赖检查（python 包/ffmpeg/v4l2-ctl/pactl/播放器脚本）、设备节点、配置模板占位符、版本号、主题配对；可选 `--check --connect` 做 DNS/TLS/登录/订阅验证
  - 验收：部署前一条命令即可发现缺失依赖与配置错误；不产生任何 MQTT 副作用
  - 证据：`main.py` L2746-L2793

- [ ] 🔴 **P2-2.5 恢复协议自检脚本**
  - 现状：`selftest_topics.py` 在迁移提交中被删除，当前没有主题/报文配对自检工具
  - 动作：恢复并改造为 pytest 用例，校验 `REQUEST_TO_RESULT` 配对、订阅主题格式、与 Android `TopicNames` 一致性
  - 验收：`pytest` 可执行；主题改名/漏配对应答时失败
  - 证据：`git show 947f445^:walle-device/linux-demo/selftest_topics.py`；`main.py` L300-L340

### P2-3 兼容性与功能

- [ ] 🔴 **P2-3.1 推流参数与设备 preflight**
  - 现状：`VIDEO_VIDEO_SIZE` 固定 `640x480`、30fps、`libx264`；不检查摄像头是否支持该分辨率/格式，失败只能等 ffmpeg 退出
  - 动作：推流前用 `v4l2-ctl` 校验格式/分辨率；不支持时自动降级或返回可用组合；参数支持环境变量覆盖
  - 验收：不支持 640x480 的摄像头返回明确原因或自动选择支持的分辨率；正常摄像头推流成功
  - 证据：`main.py` L393-L399、L1192-L1257

- [ ] 🔴 **P2-3.2 推流编码器与参数可配置**
  - 现状：`libx264` / `ultrafast` / `zerolatency` / 码率 / GOP 均硬编码；无法按设备性能调整
  - 动作：新增 `VIDEO_ENCODER`（`auto|libx264|h264_v4l2m2m`）与码率/GOP/分辨率配置；启动时探测硬件编码器，失败回退并打印原因
  - 验收：Pi 5 无硬件编码器时自动回退 `libx264`；带 V4L2 M2M 的设备可切换成功；参数可通过环境变量调整
  - 证据：`main.py` L1200-L1256；`walle-device/doc/walle-launch-checklist.md` P2-1.3

- [ ] 🔴 **P2-3.3 视频日志路径固定**
  - 现状：`VIDEO_LOG_FILE` 默认相对路径 `log/ffmpeg_{time}.log`，按进程工作目录解析；手动从其他目录启动会把日志写到意外位置
  - 动作：默认解析到脚本目录或 `WALLE_LOG_DIR`；systemd 与手动运行行为一致
  - 验收：任意工作目录启动，日志都写到同一预期目录；目录不可写时回退临时文件并告警
  - 证据：`main.py` L387-L389、L1269-L1272、L1379-L1390

- [ ] 🔴 **P2-3.4 retained `online` 报文最小化**
  - 现状：`online` retained 报文复用 `build_device_info()`，包含 MAC、序列号、IP、磁盘总量、启动时间等；任何订阅该租户主题的客户端都能长期读取
  - 动作：retained `online` 只保留 `device_id` / `uuid` / `online` / 版本等最小字段；完整身份信息仅通过 `discovery/query` 应答返回
  - 验收：retained online 不含 MAC/序列号/磁盘明细；App 仍能正常显示在线与基本型号
  - 证据：`main.py` L1654-L1666、L1449

- [ ] 🔴 **P2-3.5 动作成功判定与实际可用性对齐**
  - 现状：视频推流以「进程活过 3s」判成功，手机音频以「播放器活过 2s」判成功；而手机音频首帧超时是 8s，可能出现「返回成功但实际无声」；推流鉴权失败也可能在宽限期后才退出
  - 动作：成功判定改为「首帧/首个媒体包到达」或延长宽限期并异步更正结果；失败后发布事件
  - 验收：无声/黑屏场景不会再返回成功；App 能收到后续失败事件
  - 证据：`main.py` L398、L419、L1897-L1908、L2217-L2228；`whep_audio_player.py` L143-L153

- [ ] 🔴 **P2-3.6 device-id 持久化位置**
  - 现状：`resolve_device_id()` 默认把 `.device-id` 写在 `main.py` 同级；目录不可写时回退 `~/.config/walle-ai/device-id`；升级时若整目录替换，可能丢失标识；`.device-id` 未加入 gitignore
  - 动作：统一到 `/var/lib/walle-ai/device-id`（或可配置 `DEVICE_ID_FILE`）；部署脚本负责创建目录与权限；升级说明保留该文件
  - 验收：升级/重装后 `device_id` 不变，App 不出现重复设备卡片；`git status` 不出现 `.device-id`
  - 证据：`main.py` L200-L202、L622-L724

- [ ] 🔴 **P2-3.7 手机音频启动超时参数化**
  - 现状：`PHONE_AUDIO_FIRST_FRAME_TIMEOUT` / `STARTUP_GRACE` 为硬编码 8.0 / 2.0，其余配置均可通过环境变量覆盖
  - 动作：改为环境变量；与 P2-3.5 的成功判定一起调整
  - 验收：弱网环境可通过配置延长首帧超时；启动宽限与首帧超时语义一致
  - 证据：`main.py` L418-L419

### P2-4 调试工具

- [ ] 🔴 **P2-4.1 `--check` 增加连接诊断**
  - 动作：可选 `--check --connect`：DNS 解析、TCP 连接、TLS 握手/证书、MQTT 登录、订阅回执、发布一条测试消息到独立主题
  - 验收：现场可一条命令定位「配置错 / 证书错 / 账号错 / ACL 错」
  - 证据：`main.py` L2746-L2793

- [ ] 🔴 **P2-4.2 本地请求注入脚本**
  - 动作：提供命令行小工具，模拟 App 发送 `state/query`、`capability/query`、`video/start`、`video/stop`、`phone/audio/*`，支持 `--dry-run` 只打印主题与报文
  - 验收：无手机时可完成全链路联调；默认不会真正推流/播放
  - 证据：`main.py` L1510-L1532、L301-L340

### P2-5 文档一致性

- [ ] 🔴 **P2-5.1 修正 ROS2 清单中的 linux-demo 描述**
  - 现状：`walle-device/doc/walle-launch-checklist.md` P0-5.2 声称「`linux-demo/walle-ai.conf.example`」已模板化，但该文件实际在 `947f445` 中随 `walle-ai.env.example` 一起被删除
  - 动作：修正该条描述，或在新清单中明确 linux-demo 尚未完成配置模板化
  - 验收：两份清单关于 linux-demo 的状态一致，不再引用不存在的文件
  - 证据：`walle-device/doc/walle-launch-checklist.md` L104-L106；`git show 947f445 --stat`

- [ ] 🔴 **P2-5.2 明确 ROS2 与 linux-demo 二选一部署**
  - 现状：两套实现使用同名 systemd 服务 `walle-ai`、不同配置文件（`.conf` / `.env`），同机不能共存；文档未说明
  - 动作：在 `walle-device/doc/README.md` 与 `linux-demo/README.md` 增加选择说明、冲突提示与迁移步骤
  - 验收：新用户能明确选择部署形态，不会互相覆盖配置或服务
  - 证据：`walle-ai.service.example`；`walle-device/scripts/walle-ai.service.in`；`walle-device/doc/README.md`

- [x] ✅ **P2-5.3 README 手动运行命令修正**
  - 处理：`/home/ubuntu/venv/python3 main.py` 改为 `/home/ubuntu/venv/bin/python main.py`，并补充「先停 systemd 服务，避免同一 clientId 互相踢下线」
  - 验收：命令路径与 venv 布局一致
  - 证据：`README.md` 手动运行章节

---

## 回归验收命令（参考）

> 涉及摄像头/麦克风的测试请先确认没有其它程序占用设备；`kill` 测试请在可重启的开发设备上执行。

```bash
# 0) 静态问题扫描（整改后期望无输出）
grep -rnE "baidong|RaspPi5|MQTT_PASSWORD[\"']?[=:][\"']?123456|shell=True|cmd/exec|remote_cmd" \
  walle-device/linux-demo --include='*.py'

# 1) 缺失配置必须拒绝启动（期望非 0）
env -i PATH=/usr/bin:/bin python3 walle-device/linux-demo/main.py --check; echo "exit=$?"

# 2) 明文 MQTT 必须拒绝（期望非 0）
MQTT_HOST=example.com MQTT_PORT=1883 MQTT_TLS=off \
MQTT_USERNAME=u MQTT_PASSWORD=p MQTT_TENANT=t \
python3 walle-device/linux-demo/main.py --check; echo "exit=$?"

# 3) 配置文件不得入库（期望无输出 / 命中忽略规则）
git ls-files 'walle-device/linux-demo/*.env' 'walle-device/linux-demo/walle-ai.conf' \
             'walle-device/linux-demo/walle-ai.service' 'walle-device/linux-demo/.device-id'
git check-ignore -v walle-device/linux-demo/walle-ai.env

# 4) 全历史密钥扫描
git log --all -p --text | grep -nE "MQTT_PASSWORD=|WHIP_SECRET=|sk-[A-Za-z0-9]{10,}" | head -50

# 5) 远程命令应无响应（整改后执行；需 mosquitto_pub）
mosquitto_pub -h <broker> -p 8883 --cafile <ca> -u <user> -P <password> \
  -t "walle/<tenant>/<device>/v1/cmd/exec" \
  -m '{"uuid":"<uuid>","cmd":"id"}'
# 期望：设备日志无「执行远程命令」，App 收不到 cmd/result

# 6) kill -9 后不得残留子进程（期望仅输出 no orphan）
sudo kill -9 "$(pgrep -f 'linux-demo/main.py')"
sleep 1
pgrep -af 'ffmpeg|whep_audio_player' || echo "no orphan"

# 7) systemd 部署检查
systemd-analyze verify walle-ai.service
sudo systemctl restart walle-ai
journalctl -u walle-ai -n 50 --no-pager
# 期望：有 STARTUP READY；缺配置时启动失败并打印缺失项

# 8) 外设与能力
v4l2-ctl --list-devices
pactl list short sources
pactl list short sinks
python3 walle-device/linux-demo/main.py --check
```

---

## 完成记录

| 日期 | 任务编号 | 提交/PR | 验证方式 | 验证人 |
| --- | --- | --- | --- | --- |
| 2026-11-10 | P0-1.1 / P0-1.2 / P0-1.5 | 15ca54b | 源码无 `cmd/exec`、`remote_cmd`、`shell=True`；`subscriptions()` 7 项；`py_compile` 与 `--check` 通过 | 待确认 |
| 2026-11-10 | P0-2.1 | f525ec8 | 无配置 `--check` 退出 1 并列出 5 项缺失；正式启动退出 2；合法配置退出 0；`py_compile`/flake8 通过 | 待确认 |
| 2026-11-10 | P0-2.2 | 待提交 | 默认 TLS=on/8883；prod 下 TLS=off、端口 1883、auto、坏 CA 均退出 1；debug 下 TLS=off 通过 | 待确认 |
| 2026-11-10 | P0-2.3 | 待提交 | 配置统一为 `/etc/walle-ai.conf`；本地模板与 .gitignore 生效；开发配置自动加载；不可读配置提示 systemd 注入 | 待确认 |
| 2026-11-10 | P2-5.3 | 待提交 | README 手动运行命令改为 `venv/bin/python`，并补充先停 systemd 服务 | 待确认 |
| 2026-11-10 | P0-2.4 | 待提交 | 必填项/端口/TLS/CA/模板校验通过；空模板仅告警；非法模板 `--check` 退出 1 | 待确认 |
| 2026-11-10 | P0-2.5 | 待提交 | 源码无 `baidong`；空模板 video/phone audio 返回 -2；完整模板 `build_rtmp_url` 正常 | 待确认 |
| 2026-11-10 | P0-1.3 / P0-1.4 | 待提交 | `doc/service_deploy.md` §10 已补每设备 ACL、deny-all 与规则引擎审计；待 EMQX 实测 | 待确认 |
| 2026-11-10 | P0-4.1 | 待提交 | 设备端 token 与独立 MD5 一致；缺 secret/占位符 `--check` 退出 1；Nginx 403/410/502 待实测 | 待确认 |
| 2026-11-10 | P0-4.2 | 待提交 | ISP/非 USB 节点、错误分辨率、缺 v4l2-ctl 均被拒；合法 USB 节点通过；坏 `CAMERA_DEVICE` `--check` 退出 1 | 待确认 |
| 2026-11-10 | P0-5.1 | 待提交 | 子进程 `PR_GET_PDEATHSIG=15`；ffmpeg/播放器 Popen 均接入；实机 `kill -9` 待验证 | 待确认 |
|  |  |  |  |  |

---

## 备注

- 2026-11-10：ffmpeg 与 WHEP 播放器 Popen 增加 `PR_SET_PDEATHSIG`，主进程崩溃/被
  `kill -9` 时子进程收到 SIGTERM；子进程 `PR_GET_PDEATHSIG` 自测为 15，实机待验证（P0-5.1）。
- 2026-11-10：linux-demo 推流前增加摄像头 preflight：`CAMERA_DEVICE` 可选固定设备，
  realpath 必须是 `/dev/videoN`、sysfs 含 `usb`、`v4l2-ctl` 支持 `VIDEO_VIDEO_SIZE`；
  非采集节点（ISP/解码/元数据）与不支持的分辨率在启动 ffmpeg 前返回 -1（P0-4.2）。
- 2026-11-10：`doc/service_deploy.md` §6.2 新增 Nginx 8443 `/whep-audio/` + `secure_link`
  鉴权，设备端生成 5 分钟 token（`PHONE_AUDIO_WHEP_SECRET` / `PHONE_AUDIO_WHEP_PATH` /
  `PHONE_AUDIO_WHEP_TOKEN_TTL`）；无 token 403、过期 410。P0-4.1 待服务端实测。
- 2026-11-10：`doc/service_deploy.md` §10 已补每设备 MQTT ACL
  （`walle/+/${clientid}/#`、discovery、`{deny, all}.`）与动作审计
  （EMQX 规则引擎 → `walle/audit/...`）；P0-1.3 / P0-1.4 待服务端落地验证。
- 2026-11-10：linux-demo 删除推流/拉流地址中的生产域名默认值，模板改为显式配置；
  空模板时仅告警并在 video/phone audio 动作入口返回 -2，完整模板时校验占位符与
  `WHIP_SECRET`（P0-2.4 / P0-2.5）。
- 2026-11-10：linux-demo 配置统一为 `/etc/walle-ai.conf`（生产）与脚本同级 `walle-ai.conf`（开发），
  新增 `walle-ai.conf.example` 与 `linux-demo/.gitignore`；systemd 改为
  `EnvironmentFile=/etc/walle-ai.conf`，缺配置时启动失败；配置不可读但环境变量已注入时只打印 info。
  同时修正 README 手动运行命令为 `venv/bin/python`（P0-2.3 / P2-5.3）。
- 2026-11-10：linux-demo 生产环境强制 MQTT TLS（默认 8883 + `MQTT_TLS=on`），仅
  `WALLE_RUNTIME_ENV=debug` 允许 `MQTT_TLS=off`；生产环境端口 1883 被拒绝（P0-2.2）。
  端口改为默认 8883 后，P0-2.1 的缺失项由 5 个调整为 4 个。
- 2026-11-10：已删除 linux-demo 的 MQTT 默认 Broker / 账号 / 口令（P0-2.1）；
  `MQTT_HOST` / `MQTT_PORT` / `MQTT_USERNAME` / `MQTT_PASSWORD` / `MQTT_TENANT` 改为必填，
  缺失时 `--check` 返回 1、正式启动返回 2。
- 2026-11-10：已删除 linux-demo 的 `cmd/exec` / `cmd/result` 远程命令能力（P0-1.1 / P0-1.2 / P0-1.5）；
  `capability/result` 不再上报 `remote_cmd`，订阅主题由 8 个减为 7 个，与协议 §8 对齐。
- 2026-11-10：本清单为新生成清单，审查对象为 `walle-device/linux-demo` 当前 HEAD（`947f445`）。
  linux-demo 是早期非 ROS2 实现，ROS2 侧 `net_mqtt_bridge` 已完成的安全整改（移除 `cmd/exec`、
  强制 TLS、必填配置、日志脱敏等）没有回移到本目录。
- 2026-11-10：`doc/MQTT_protocol_convention.md` §8 已记录 2026-11-08 删除 `cmd/exec` / `cmd/result`，
  并要求 `grep -R "cmd/exec\|cmd/result\|remote_cmd"` 无残留；linux-demo 的实现已于 2026-11-10
  删除，P0-1.1 / P0-1.2 / P0-1.5 关闭。
- 2026-11-10：迁移提交 `947f445` 删除了 `linux-demo/requirements.txt`、`walle-ai.env.example`、
  `selftest_topics.py`，README 从 284 行缩减为 34 行；`main.py` docstring 仍引用 README「首次部署」
  章节，`doc/MQTT_protocol_convention.md` §7 仍指向 linux-demo README 作为部署说明，形成文档断链。
  相关修复见 P1-1.1、P2-2.5。
- 2026-11-10：配置文件名存在两套：linux-demo `/etc/walle-ai.env`，ROS2 `/etc/walle-ai.conf`；
  `walle-ai.service.example` 使用 `EnvironmentFile=-/etc/walle-ai.env`（`-` 表示缺失也继续），
  叠加代码内默认 Broker/口令，会在无配置时连到默认地址。统一方案见 P0-2.3。
- 2026-11-10：`walle-device/.gitignore` 只忽略 `scripts/walle-ai.conf`；`linux-demo/walle-ai.env`、
  `linux-demo/walle-ai.service`、`linux-demo/.device-id` 均未忽略，按 README 手工创建的文件有误入库风险，
  见 P0-2.3、P2-3.6。
- 2026-11-10：手机麦克风上行链路（App WHIP 推 → Oryx/SRS → 设备 WHEP 拉）当前无任何 token；
  `whep_audio_player.py` 的 SDP POST 也没有鉴权头。房间号沿用视频房间号 + `_audio`，
  知道房间号即可尝试拉流，见 P0-4.1。
- 2026-11-10：`ffmpeg` 与 `whep_audio_player.py` 均以 `start_new_session=True` 启动，
  正常退出路径会 `terminate()`，但 `kill -9`/崩溃不会触发；systemd 默认 cgroup 清理只覆盖服务停止，
  手工运行场景无兜底，见 P0-5.1。
- 2026-11-10：`main.py` 的 `_check` 只打印不校验，且 `MQTT_PORT` 的 `int()` 转换在导入期执行；
  配置错误时可能先以默认值连上 Broker 再暴露问题，见 P0-2.1、P0-2.4。
- 2026-11-10：本清单行号基于 `947f445` 版本；代码变更后请同步核对，验收以行为为准，不以行号为准。
- 静态审查无法替代实机验证：P0-4、P0-5、P1-3、P1-5.3 必须在上线设备上逐项复测。

---

## 需要你在服务端执行的事项

> 以下事项无法在代码仓库内完成，需要在 EMQX / Nginx / 云服务器上落地；完成后把对应清单项改为 ✅。

| # | 对应清单项 | 操作 | 验收 |
| --- | --- | --- | --- |
| S1 | P0-1.3 / P0-1.4 | EMQX 创建每台设备独立账号 `dev_<device_id>`；按 `doc/service_deploy.md` §10 配置 ACL、`{deny, all}.`，并启用 §10.2 审计规则 | §10.1 四条命令：App 允许、设备 A 跨设备订阅/发布被拒；`walle/audit/#` 能收到动作来源与时间 |
| S2 | P0-1.4 | 把 EMQX 审计动作接到日志系统（Republish 或 Webhook） | 触发一次 video/phone audio 动作后可按 `request_id` 追溯 |
| S3 | P0-3.1 | 确认历史中的 `RaspPi5` / `123456` 是否为真实 Broker 账号；若是，在 EMQX 改密并同步 `/etc/walle-ai.conf` 与 App | 旧口令无法登录；`git log --all -p --text` 扫描报告留档 |
| S4 | P0-4.1 | 在 8443 配置 Nginx `secure_link`（`doc/service_deploy.md` §6.2）；生成 `PHONE_AUDIO_WHEP_SECRET`；安全组放行 8443/tcp；设备配置写入 WHEP 模板/secret/path | 无 token 403、过期 410、正确 token 502；设备手机音频可出声 |
| S5 | P0-2.3 / P1-1.2 | 安装 `/etc/walle-ai.conf`（root:root 600），按 README 部署 systemd 服务 | 普通用户不可读配置；`systemctl stop walle-ai` 5s 内退出；重启后自动上线 |
| S6 | P0-4.2 / P0-5.1 / P0-5.2 | 按对应项整改完成后，在真实设备上验证摄像头 preflight、`kill -9` 子进程清理、systemd 停止耗时 | 非 USB 节点被拒；`kill -9` 后无 ffmpeg/播放器残留；stop 5s 内完成 |

> 服务端事项完成前，即使代码侧清单已勾选，也不要把设备接入非受控网络。
