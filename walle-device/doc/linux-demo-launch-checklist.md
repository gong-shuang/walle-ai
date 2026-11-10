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
| P0 上线阻断 | 15 | 12 | 3 |
| P1 上线前应修 | 18 | 15 | 3 |
| P2 后续优化 | 22 | 3 | 19 |
| **合计** | **55** | **30** | **25** |

> linux-demo 已开始按本清单整改（当前 30/55）；每次勾选后请同步更新本表数字。
> P0-1.3 / P0-1.4 / P0-3.1 进行中：ACL 与审计规则已写入 `doc/service_deploy.md`；历史扫描已完成，待服务端改密与清理外部明文。

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
  - 验收：无配置时 `--check` 退出 1 并列出 4 个缺失项（端口在 P0-2.2 后默认 8883），`python3 main.py` 退出 2；合法配置 `--check` 退出 0；`MQTT_PORT=abc` 返回友好错误而非 traceback；源码无默认账号/口令与 MQTT 默认域名
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

- [ ] 🟡 **P0-3.1 全历史扫描并轮换已暴露凭据**
  - 现状：历史扫描确认提交 `8f112d5` 的 README 明文写了 App 测试账号 `home` / `<已泄露口令>`；根 `README.md` 与 `web/car_ctl_by_mqtt.html` 已在 2026-11-10 清理；`walle-device/scripts/walle-ai.conf`（本地真实配置，已被忽略）仍使用同一口令；未发现 `sk-`、私钥或真实 `WHIP_SECRET`
  - 处理：已删除根 README 的明文账号/口令；已移除 web 测试页硬编码密码并改为 `type=password` 输入；本地 `scripts/walle-ai.conf` 待改密后更新
  - 待办：在 EMQX 修改/禁用 `home` 与设备账号口令，同步 App 与 `/etc/walle-ai.conf`
  - 验收：旧口令无法登录；`git grep -nE "MQTT_PASSWORD=[^x< ]{6,}"` 无真实值；README / web 页无明文凭据
  - 证据：commit `8f112d5`、`README.md` L111、`web/car_ctl_by_mqtt.html` L307、`walle-device/scripts/walle-ai.conf` L14（本地，不入库）

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

- [x] ✅ **P0-5.2 优雅退出不被长耗时任务阻塞**
  - 处理：删除远程命令后最长阻塞已消除；`main()` 在 `monitor.run()` 完成清理后调用 `logging.shutdown()` + `os._exit(0)`，不再等待非 daemon 线程池；systemd 模板增加 `TimeoutStopSec=10`、`KillMode=control-group`
  - 验收：模拟残留 30s 非 daemon 线程时进程 0.25s 退出（自测通过）；`--check` 不受影响；systemd stop 5s 内完成（实机待验证）
  - 证据：`main.py` L2883-L2889；`walle-ai.service.example` L14-L16

---

## P1 上线前应修

### P1-1 部署可复现

- [x] ✅ **P1-1.1 恢复 linux-demo 部署文档、依赖清单与自检工具**
  - 处理：新增 `linux-demo/requirements.txt`（`paho-mqtt`、`psutil`、`aiortc`、`sounddevice`），与 ROS2 侧 `walle-device/doc/requirements.txt` 拆分，不再混用；重写 README：系统依赖（`python3-venv` / `python3-dev` / `v4l-utils` / `ffmpeg` / `pulseaudio-utils` / `libportaudio2`）、venv 与 pip、配置项与查找顺序、`--check` 自检、`deploy.sh` 安装、状态与日志、开发手动运行（先停服务）、升级、常见问题；配置模板沿用 P0-2.3 的 `walle-ai.conf.example`
  - 验收：`requirements.txt` 四项依赖齐全；README「首次部署」章节与 `main.py` docstring 指向一致；手动运行命令改为 `venv/bin/python main.py`；`--check` 输出 7 条订阅主题；全新 Raspberry Pi OS 实机部署待执行
  - 证据：`linux-demo/requirements.txt`、`linux-demo/README.md`、`linux-demo/deploy.sh`

- [x] ✅ **P1-1.2 修复 systemd 单元模板**
  - 处理：模板改为 `@WALLE_USER@` / `@WALLE_GROUP@` / `@WALLE_HOME@` / `@WALLE_VENV@` 占位符，由部署脚本按执行用户与实际路径渲染（不再硬编码 `User=gs`）；`WorkingDirectory=@WALLE_HOME@`、`ExecStart=@WALLE_VENV@/bin/python @WALLE_HOME@/main.py`；`EnvironmentFile=/etc/walle-ai.conf`（去掉 `-`）；新增 `StartLimitIntervalSec=300` / `StartLimitBurst=5`、`NoNewPrivileges=true`、`PrivateTmp=true`、`ProtectSystem=strict`、`ProtectHome=read-only`、`ReadWritePaths=@WALLE_HOME@`、`SupplementaryGroups=audio video`、`XDG_RUNTIME_DIR=/run/user/%U`
  - 验收：按测试值渲染后 `systemd-analyze verify` 通过（exit 0）；缺 `/etc/walle-ai.conf` 时 systemd 启动失败；`systemctl stop` 由 `TimeoutStopSec=10` / `KillMode=control-group` 兜底（实机待验证）
  - 证据：`walle-ai.service.example` L1-L48

- [x] ✅ **P1-1.3 提供 linux-demo 部署脚本**
  - 处理：新增 `linux-demo/deploy.sh`：`main.py --check` 前置自检 → `install -m 600 root:root` 安装 `/etc/walle-ai.conf` → 按执行用户渲染 `walle-ai.service.example` 占位符 → `daemon-reload` + `enable` + `restart`；支持 `--venv PATH` / `WALLE_VENV` 指定虚拟环境、`--no-start` 只安装不启动；拒绝 root 执行，可重复执行
  - 验收：`bash -n` 通过；stub `sudo`/`systemctl` 下完整跑通，渲染后的 unit `systemd-analyze verify` 通过（exit 0）；重复执行与 `--no-start` 正常；缺 venv / 缺配置 / 未知参数均以非 0 退出并给出提示
  - 证据：`linux-demo/deploy.sh` L1-L92

- [x] ✅ **P1-1.4 版本号读取一致性**
  - 处理：新增 `VERSION_SOURCE`，优先级明确为「环境变量 > `WALLE_VERSION_FILE` > 同级 `VERSION` > 上级 `walle-device/VERSION` > 内置默认值」；`--check` 打印生效版本与来源，找不到文件时输出警告；启动日志记录版本并在使用默认值时告警；新增 `linux-demo/test_version.py` 覆盖文件/环境变量/显式文件/默认值四种场景；README 增加「版本号」章节与 App 同步链接
  - 验收：仓库内 `--check` 显示 `0.0.2`（来源 `walle-device/VERSION`）；`WALLE_VERSION_NUM=42` / `WALLE_VERSION_NAME=9.9.9` 覆盖生效；单独复制 `main.py` 且无 VERSION 时显示 `0.1.1` 并告警；`python3 -m unittest test_version -v` 4 项通过
  - 证据：`main.py` L272-L323、L2824-L2827、L2857-L2865、L2870-L2878；`test_version.py`；`README.md`「版本号」

### P1-2 进程守护与启动可靠性

- [x] ✅ **P1-2.1 启动健康检查与 Broker 不可达重试**
  - 处理：改为 `connect_async` + `loop_start`，Broker 不可达时按 `reconnect_delay_set(3s→30s)` 在后台持续重试，进程不再因首次连接失败退出；新增 `on_connect_fail` 日志；首次连接并订阅后打印 `STARTUP READY`（含 broker/device/version）；新增 `dependency_status()` / `dependency_warnings()`，启动与 `--check` 检查 `ffmpeg`、`v4l2-ctl`、`pactl`、`whep_audio_player.py`，缺失只告警；`build_capability()` 在缺 ffmpeg 时不再上报 `rtmp_push`，并在 `probe.missing_dependencies` 列出缺失项
  - 验收：Broker 指向 `127.0.0.1:1` 运行 9s，进程保持运行并周期性输出连接失败日志（无 traceback、无 crash-loop）；mock `on_connect` 输出 `STARTUP READY` 且 `startup_ready=True`；移除 ffmpeg 依赖后 capability 为 `['system_status']` 且 `missing_dependencies=['ffmpeg']`；`--check` 输出版本、依赖检查与 7 条订阅主题
  - 证据：`main.py` L1408-L1433、L1645-L1664、L1903-L1914、L2791-L2792、L2812-L2815、L2833-L2838、L2915-L2918；`README.md`「自检」与常见问题

- [x] ✅ **P1-2.2 断线重连、LWT 与发布失败处理**
  - 处理：`_publish_one()` 检查 `info.rc`，非 `MQTT_ERR_SUCCESS` 时记录「消息未送达」并返回 False，`publish_json()` 透传结果；新增 `wait_timeout` 参数，正常退出时 offline 报文 `wait_for_publish(2s)` 后再 `disconnect()`；`on_connect_fail` 日志已在 P1-2.1 补齐；重连由 paho 自动完成，每次 `on_connect` 都重新订阅并发布 retained `online=true` 覆盖遗嘱的 `offline`
  - 验收：stub `rc=MQTT_ERR_NO_CONN` 时 `publish_json` 返回 False 且日志含「消息未送达」；`rc=0` + `wait_timeout=2.0` 时调用 `wait_for_publish(2.0)`；连续两次 mock `on_connect` 均发布 `retain=True, online=True`；真实断网 1 分钟恢复待实机验证
  - 证据：`main.py` L1703-L1725、L1645-L1661、L2857-L2862

### P1-3 外设断连与降级

- [x] ✅ **P1-3.1 推流进程异常退出的上报与恢复**
  - 处理：`_video_watch()` 区分主动停止、被新推流替换、非主动退出三种情况；非主动退出且仍是当前推流时发布 `alarm`（`metric=video`、`alarm_type=video_lost`），`detail` 携带 `room_number`、`exit_code`、`log_file`、`tail`（≤500 字符）；记录 `video_lost`，重新推流成功后 `_clear_video_lost()` 发同类型 `recover`；未做自动重启，由 App 重新发起 `video/start`
  - 验收：mock 异常退出发布 `video_lost` 且 detail 正确；主动 stop 与被新推流替换均不告警；成功后发出 `recover`。拔摄像头 5s 内告警、插回后可重新推流待实机验证
  - 证据：`main.py` L2064-L2121、L2293-L2295；`doc/MQTT_protocol_convention.md` §4.7

- [x] ✅ **P1-3.2 手机音频播放异常退出的上报**
  - 处理：`_phone_audio_watch()` 与推流看护一致，区分主动停止/被替换/非主动退出；非主动退出发布 `alarm`（`metric=audio`、`alarm_type=phone_audio_lost`），按退出码映射可读原因（3 缺依赖 / 4 首帧超时 / 5 WHEP 协商失败 / 6 声卡失败）写入 `detail.reason`；启动宽限期内的失败也在动作结果错误信息中附带原因；重新 `phone/audio/send` 成功后 `_clear_phone_audio_lost()` 发 `recover`
  - 验收：mock exit=6 发布 `phone_audio_lost`，`detail.reason` 为声卡失败；`recover` 正常发出；主动停止不告警。停 PulseAudio / 拔声卡 / 手机停止推流的真机告警待验证
  - 证据：`main.py` L487-L493、L2451-L2507、L2546-L2549、L2614；`doc/MQTT_protocol_convention.md` §4.7

- [x] ✅ **P1-3.3 PulseAudio 会话依赖与 systemd 运行方式**
  - 处理：新增 `audio_session_hint()`，启动时用 `pactl list short sources` 探测音频会话，失败时告警并在缺少 `XDG_RUNTIME_DIR` 时提示按 README 配置（pactl 未安装时交给依赖检查，不重复告警）；systemd 模板已设 `Environment=XDG_RUNTIME_DIR=/run/user/%U` 并补注释；README 新增「音频会话」章节：`pactl info` 验证、`loginctl enable-linger`、用户级 `systemctl --user`、`PULSE_SERVER` 显式指定；`capability/result` 的 `audio.capture/playback.reason` 沿用 pactl stderr 说明
  - 验收：mock「pactl 返回 1 + 无 XDG_RUNTIME_DIR」输出「音频会话不可用…」提示；pactl 正常或无 pactl 时返回 None（不误报）；模板渲染后 `systemd-analyze verify` 通过；真机按文档配置后可录音/放音待验证
  - 证据：`main.py` L1443-L1455、L2911-L2913；`README.md`「音频会话」；`walle-ai.service.example` L29-L31

- [x] ✅ **P1-3.4 摄像头节点变化与能力刷新**
  - 处理：新增 `cameras_cached()`（`CAMERA_CACHE_TTL=10s`）供 `capability/query` 复用探测结果；`check_usb()` 检测到 USB 接入/拔出时调用 `invalidate_camera_cache()` 立即失效；`video/start` 的设备不存在或 preflight 失败时，结果新增 `cameras` 列表（`node`/`name`），便于 App 重新选择；`CAMERA_DEVICE` 固定设备沿用 udev 符号链接方案
  - 验收：连续两次 `capability` 只调用一次 `list_cameras()`；手动失效后重新探测；USB 变化后缓存为 None；`camera_nodes()` 返回当前节点；失败结果包含 `cameras` 列表（真实插拔待实机验证）
  - 证据：`main.py` L431-L432、L1943-L1964、L1971、L1828-L1830、L2260-L2271；`README.md` 常见问题

- [x] ✅ **P1-3.5 推流/播放缺少租约与空闲回收**
  - 处理：新增 `VIDEO_MAX_DURATION_SEC`（默认 1800）、`PHONE_AUDIO_MAX_DURATION_SEC`（默认 1800）、`PHONE_AUDIO_IDLE_TIMEOUT`（默认 30s，`whep_audio_player.py` 无帧超时退出码 7）、`MQTT_DISCONNECT_STOP_SEC`（默认 60s）；`monitor_loop` 每周期调用 `_enforce_stream_leases()`，到期停止并发 `video_timeout` / `phone_audio_timeout`（`level=info`，不视为故障）；MQTT 断连超时后停止推流与手机音频；配置模板补充四项说明
  - 验收：mock 过期 `started_ts` 时同时停止推流/播放并发出两个 timeout 事件；`disconnected_at` 超过 60s 后停止两路流并复位；播放器 `--idle-timeout` 生效（退出码 7）。真机到期自动停止与 App 被杀场景待验证
  - 证据：`main.py` L173、L456、L488-L489、L2892-L2925；`whep_audio_player.py` L62-L67、L178-L181、L210-L211；`walle-ai.conf.example` L49-L56；`doc/MQTT_protocol_convention.md` §4.7

### P1-4 日志与可观测性

- [x] ✅ **P1-4.1 ffmpeg 日志保留策略**
  - 处理：新增 `cleanup_video_logs()`，在 `make_log_sink()` 新建日志前按「保留 7 天 / 最多 10 个文件 / 总量 100MB」删除最旧的 `ffmpeg_*.log` 与 `.log.1`；参数可用 `VIDEO_LOG_KEEP_DAYS` / `VIDEO_LOG_KEEP_FILES` / `VIDEO_LOG_MAX_TOTAL_MB` 调整（0 不限制）；不处理非 `ffmpeg_` 文件，也不影响尚未创建的新日志
  - 验收：临时目录 12 个日志清理后保留 10 个；8 天前的文件被删除；`note.txt` 未受影响；总量上限生效（20 字节上限保留 2×10 字节）；`py_compile` 通过
  - 证据：`main.py` L454-L458、L1582-L1619、L1622-L1627；`walle-ai.conf.example` L58-L61；`README.md`「状态与日志」

- [x] ✅ **P1-4.2 MQTT 报文日志降级与脱敏**
  - 处理：`on_message()` 的 INFO 日志改为只记录主题与字节数；完整报文降为 DEBUG，并先经 `mask_payload()` 脱敏（`password` / `secret` / `token` / `key` / `room` / `room_number` 字段及嵌套对象替换为 `***`，URL 中 `secret=` / `token=` 也替换，最后截断 500 字符）；非 DEBUG 级别用 `isEnabledFor` 跳过脱敏计算；远程命令的 stdout/stderr 日志路径已随 P0-1.1 删除，无需额外处理
  - 验收：样例报文输出 `room_number="***"`、`password="***"`、嵌套 `secret="***"`、URL `token=***`；非 JSON 文本同样脱敏并截断；源码中无 INFO 级别打印 payload 的位置（`grep` 仅剩主题与字节数）
  - 证据：`main.py` L1637-L1669、L1785-L1791

- [ ] 🔴 **P1-4.3 远程日志与关键指标（暂缓）**
  - 状态：暂缓——与 ROS2 清单 P1-4.3 保持一致，作为新需求后续统一设计
  - 动作：接入 journald/远程日志；上报在线率、请求失败率、推流失败率、CPU/温度
  - 验收：现场故障可远程定位，不依赖 SSH

### P1-5 测试与 CI

- [x] ✅ **P1-5.1 远程命令移除回归测试**
  - 处理：新增 `linux-demo/test/`（`__init__.py`、`conftest.py`）与 `test_remote_cmd_removed.py`：断言 `subscriptions()` 为 7 个主题且不含 `cmd/exec`、`cmd/result`，`REQUEST_HANDLERS` 不含 `handle_cmd_exec`，`build_capability` 源码不含 `remote_cmd`，`main.py` / `whep_audio_player.py` 不含 `cmd/exec`、`cmd/result`、`remote_cmd`、`handle_cmd_exec`、`shell=True`
  - 验收：`cd linux-demo && python3 -m unittest discover -s test -t .` 通过（当前 19 项）；pytest 环境同样可发现（unittest 类）
  - 证据：`linux-demo/test/test_remote_cmd_removed.py`、`linux-demo/test/conftest.py`

- [x] ✅ **P1-5.2 协议与配置单元测试**
  - 处理：新增 `test/test_protocol.py`，覆盖 `sanitize_segment`（长段截断+短哈希）、`device_topic` / `tenant_topic`、`subscriptions`（7 个主题与请求/应答配对、discovery 独立）、`verify_uuid`（匹配/不匹配/空/非 JSON）、`VIDEO_ROOM_PATTERN`（`../`、`/`、65 字符、空、中文）、`mask_rtmp_url` 与 `mask_payload`、`resolve_device_id`（落盘/复用/换 MAC 记入 previous）、TLS 判定（prod 禁 1883、off 仅 debug）与 `validate_config` 缺失项；`test_version.py` 迁入 `test/`，统一用 `unittest discover` 运行
  - 验收：`python3 -m unittest discover -s test -t .` 共 19 项通过（15 协议/配置 + 4 版本）；pytest 环境下同类可被发现（本机未安装 pytest，未实跑）；CI 接入见 P1-5.4
  - 证据：`linux-demo/test/test_protocol.py`、`linux-demo/test/test_version.py`、`linux-demo/test/conftest.py`；`README.md`「测试」

- [ ] 🟡 **P1-5.3 故障注入与长稳测试**
  - 状态：暂缓至实机阶段（2026-11-10 确认）。代码侧已具备 `PR_SET_PDEATHSIG`、`TimeoutStopSec/KillMode`、断线重连与流租约回收（P0-5.1 / P0-5.2 / P1-2.2 / P1-3.5），需在真实设备上执行故障注入与 24h soak
  - 动作：复用 `walle-device/scripts/stability_test.py` 思路，覆盖 `kill -9`（验证子进程清理）、断网/恢复、摄像头解绑/绑定、声卡不可用、MQTT Broker 重启、24h soak（RSS/FD/磁盘/温度）
  - 验收：`kill -9` 后无 ffmpeg/播放器残留；断网恢复后自动上线；24h 无泄漏趋势
  - 证据：`walle-device/scripts/stability_test.py`；`main.py` L1841-L1858、L2169-L2186

- [ ] 🟡 **P1-5.4 CI 覆盖 linux-demo**
  - 状态：暂缓（2026-11-10 确认不修改 `.github/workflows/ci.yml`）。现有 `Critical lint` 已覆盖 linux-demo 的语法级检查；本地已提供 `linux-demo/test/`（19 项 unittest，可用 pytest 运行），CI job 待后续统一处理
  - 现状：`.github/workflows/ci.yml` 的 `Critical lint` 覆盖 `walle-device`（包含 linux-demo 的语法级检查），但 `Lint scripts and tests` 不包含 linux-demo，也没有 linux-demo 的 pytest
  - 动作：将 `walle-device/linux-demo` 纳入严格 flake8；新增 linux-demo 测试 job（安装 `requirements.txt` 后运行 pytest）
  - 验收：PR 触发 lint + linux-demo 测试；故意引入未定义名/语法错误时 CI 失败
  - 证据：`.github/workflows/ci.yml` L29-L41、L71-L82；`linux-demo/test/`

---

## P2 后续优化

### P2-1 性能

- [x] ✅ **P2-1.1 state/query 复用监控采样快照**
  - 处理：`monitor_loop` 每轮把 CPU/网络采样写入 `status_snapshot`（含采样时刻），`build_status()` 直接复用并按 `time.time() - sampled_at` 计算 `sample_age_sec`；USB 列表复用 `check_usb()` 维护的 `usb_state`；仅首份快照未建立时现场补采 0.2s（原为 1.0s）
  - 验收：无快照时 `build_status()` 约 0.21s 返回、`sample_age_sec=0`；注入 1.5s 前的快照后查询 3.5ms 返回、`sample_age_sec=1.5`、`sample_interval_sec=5.0`；连续查询不再触发 1s 采样
  - 证据：`main.py` L1745-L1747、L2798-L2813、L2829-L2830、L2842、L3041-L3050

- [x] ✅ **P2-1.2 capability 探测异步化与缓存**
  - 处理：新增 `CAPABILITY_CACHE_TTL`（默认 120s）、`CAPABILITY_FIRST_WAIT`（默认 3s）、`CAPABILITY_CACHE_ONLY`；实际探测移到 `_probe_capability()`，由 `_capability_refresh_loop()` 后台线程启动即探测、按 TTL 或 USB 变化事件刷新；`build_capability()` 只读缓存并返回 `cache {source, age_sec, ttl_sec}`，首次未就绪最多等待 3s，超时或 cache-only 返回 `probe.pending=true` 占位（`capabilities` 仅 `system_status`）；探测异常或摄像头探测失败时沿用上一次摄像头列表，不清空已有能力
  - 验收：新增 `test/test_capability_cache.py`（3 项）+ 现有用例共 22 项通过；cache-only 时 pending 返回 <0.5s；注入 5s 前缓存返回 `source=cache, age_sec>=5`；摄像头探测失败后仍保留旧 `cameras`；USB 变化置位 `capability_refresh`
  - 证据：`main.py` L436-L439、L1752-L1756、L1930-L1933、L2071-L2150、L3198-L3199；`test/test_capability_cache.py`；`doc/MQTT_protocol_convention.md` §4.4

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
grep -rnE "baidong|RaspPi5|MQTT_PASSWORD[\"']?[=:][\"']?[^x< ]{6,}|shell=True|cmd/exec|remote_cmd" \
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
| 2026-11-10 | P0-5.2 | 待提交 | 残留 30s 非 daemon 线程时进程 0.25s 退出；systemd 模板加 TimeoutStopSec/KillMode | 待确认 |
|  |  |  |  |  |

---

## 备注

- 2026-11-10：capability 改为后台探测 + 120s 缓存，查询只读缓存并返回 `cache`
  字段；首次未就绪等待 3s 或返回 `probe.pending`；摄像头探测失败沿用旧缓存（P2-1.2）。
- 2026-11-10：`state/query` 复用监控循环快照（含 `sample_age_sec`），USB 复用
  `check_usb()` 结果；首份快照未建立时补采 0.2s，实测热查询 3.5ms（P2-1.1）。
- 2026-11-10：经确认 P1-5.3（故障注入/24h soak）暂缓至实机阶段，P1-5.4（CI 接入）暂缓，
  本次不修改 `.github/workflows/ci.yml`；本地测试已就绪（19 项 unittest）。
- 2026-11-10：新增 `linux-demo/test/`：远程命令移除回归（3 项）+ 协议/配置用例（15 项）
  + 版本优先级用例（4 项），统一 `python3 -m unittest discover -s test -t .` 运行（P1-5.1 / P1-5.2）。
- 2026-11-10：MQTT 报文 INFO 日志只保留主题与字节数，完整报文降为 DEBUG 并经
  `mask_payload()` 脱敏（password/secret/token/key/room 及 URL 参数），避免凭据入日志（P1-4.2）。
- 2026-11-10：推流日志增加保留策略（7 天 / 10 个 / 100MB，可配置），每次新建日志前
  清理最旧文件，不影响当前日志与非 ffmpeg 文件（P1-4.1）。
- 2026-11-10：推流/手机音频增加租约（默认 30 分钟）与无帧超时（30s，退出码 7），
  MQTT 断连 60s 后自动释放外设；到期停止发 `video_timeout` / `phone_audio_timeout`
  （P1-3.5）。
- 2026-11-10：摄像头能力增加 10s 缓存，USB 插拔立即失效；`video/start` 失败时回传
  当前 `cameras` 节点列表，便于 App 重新选择（P1-3.4）。
- 2026-11-10：启动增加音频会话探测 `audio_session_hint()`，pactl 连不上时给出
  XDG_RUNTIME_DIR / linger / 用户级服务建议；README 新增「音频会话」章节（P1-3.3）。
- 2026-11-10：推流/手机音频看护线程区分主动停止、被新流替换与非主动退出；非主动退出发
  `alarm`（`video_lost` / `phone_audio_lost`），重新启动成功发 `recover`；协议文档 §4.7
  已同步补充 metric=`video`/`audio` 与 detail 字段（P1-3.1 / P1-3.2）。
- 2026-11-10：`_publish_one` 检查发布返回码并记录「消息未送达」；退出时 offline 报文
  `wait_for_publish(2s)` 后再断开；重连后 retained online 覆盖旧值（P1-2.2）。
- 2026-11-10：启动改为 `connect_async` + `loop_start`，Broker 不可达时持续重试不退出；
  首次连接/订阅后打印 `STARTUP READY`；`--check` 增加依赖检查，缺 ffmpeg 时 capability
  不再上报 `rtmp_push`（P1-2.1）。
- 2026-11-10：版本号读取增加来源追踪与告警（P1-1.4）：`--check` 打印版本/来源，未找到
  `VERSION` 时告警；新增 `linux-demo/test_version.py`（环境变量 > 文件 > 默认值，4 项通过）。
- 2026-11-10：恢复 `linux-demo/requirements.txt`（paho-mqtt / psutil / aiortc / sounddevice），
  重写 README 的「首次部署」（系统依赖、venv、配置、`--check`、`deploy.sh`、日志、升级、FAQ）；
  ROS2 侧 `doc/requirements.txt` 保持原样，音视频依赖只在 linux-demo 安装（P1-1.1）。
- 2026-11-10：新增 `linux-demo/deploy.sh`（P1-1.3）：先跑 `main.py --check` 再安装
  `/etc/walle-ai.conf`（600），按部署用户渲染 systemd 单元并 enable/restart；支持
  `--venv` / `WALLE_VENV` 与 `--no-start`，stub 环境下完整跑通、可重复执行。
- 2026-11-10：`walle-ai.service.example` 改为占位符模板（`@WALLE_USER@` / `@WALLE_GROUP@` /
  `@WALLE_HOME@` / `@WALLE_VENV@`），补启动频率限制与 systemd 沙箱（`ProtectSystem=strict`、
  `ProtectHome=read-only`、`ReadWritePaths`、`SupplementaryGroups=audio video`）；按测试值渲染后
  `systemd-analyze verify` 通过（P1-1.2）。
- 2026-11-10：历史密钥扫描确认 `8f112d5` 的 README 明文写了 App 测试账号 `home` / `<已泄露口令>`；
  根 `README.md` 与 `web/car_ctl_by_mqtt.html` 已清理；本地 `walle-device/scripts/walle-ai.conf`
  仍使用同一口令。未发现 `sk-`、私钥或真实 `WHIP_SECRET`；需在 EMQX 改密并更新本地配置（P0-3.1）。
- 2026-11-10：`main()` 在清理完成后 `logging.shutdown()` + `os._exit(0)`，不再等待非 daemon
  线程池；systemd 模板增加 `TimeoutStopSec=10` / `KillMode=control-group`（P0-5.2）。
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
| S3 | P0-3.1 | 历史 README 曾暴露 App 测试账号 `home` / `<已泄露口令>`；README 与 web 页已清理；本地 `scripts/walle-ai.conf` 与 EMQX 账号仍需改密，同步 App 与设备配置 | 旧口令无法登录；`git grep -nE "MQTT_PASSWORD=[^x< ]{6,}"` 无真实值 |
| S4 | P0-4.1 | 在 8443 配置 Nginx `secure_link`（`doc/service_deploy.md` §6.2）；生成 `PHONE_AUDIO_WHEP_SECRET`；安全组放行 8443/tcp；设备配置写入 WHEP 模板/secret/path | 无 token 403、过期 410、正确 token 502；设备手机音频可出声 |
| S5 | P0-2.3 / P1-1.2 | 安装 `/etc/walle-ai.conf`（root:root 600），按 README 部署 systemd 服务 | 普通用户不可读配置；`systemctl stop walle-ai` 5s 内退出；重启后自动上线 |
| S6 | P0-4.2 / P0-5.1 / P0-5.2 | 按对应项整改完成后，在真实设备上验证摄像头 preflight、`kill -9` 子进程清理、systemd 停止耗时 | 非 USB 节点被拒；`kill -9` 后无 ffmpeg/播放器残留；stop 5s 内完成 |

> 服务端事项完成前，即使代码侧清单已勾选，也不要把设备接入非受控网络。
