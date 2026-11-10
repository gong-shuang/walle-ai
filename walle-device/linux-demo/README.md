# linux-demo 设备端（无 ROS2 环境）

在树莓派等 Linux 设备上运行的 Walle 设备端：MQTT 接入 App、系统监控、USB 摄像头 RTMP 推流、
手机麦克风上行 WHEP 拉流播放。不依赖 ROS2。

## 目录说明

| 文件 | 说明 |
| --- | --- |
| `main.py` | 主程序：MQTT 协议、监控、摄像头能力、推流控制 |
| `whep_audio_player.py` | 手机麦上行播放器（由 `main.py` 以子进程拉起） |
| `requirements.txt` | Python 依赖 |
| `walle-ai.conf.example` | 配置模板 |
| `walle-ai.service.example` | systemd 单元模板（由 `deploy.sh` 渲染） |
| `deploy.sh` | 部署脚本：配置检查、安装配置与服务 |
| `ld_*.py` | 按职责拆分的配置/协议/媒体/监控模块（`main.py` 仅入口与组合） |
| `test/` | 单元测试（unittest / pytest） |

## 首次部署

以下命令以仓库放在 `~/walle-ai`、venv 放在 `~/venv` 为例；路径不同时按实际修改。
所有命令在设备本机执行。

### 1. 系统依赖

```bash
sudo apt update
sudo apt install -y python3-venv python3-dev v4l-utils ffmpeg pulseaudio-utils libportaudio2
```

- `v4l-utils`：`v4l2-ctl`，摄像头能力探测与 preflight 必需；
- `ffmpeg`：RTMP 推流；
- `pulseaudio-utils`：`pactl`，选择录音源；
- `libportaudio2`：`sounddevice` 的本机声卡输出，缺失时手机音频退出码 3；
- `python3-dev`：`aiortc` 依赖无预编译 wheel 时用于本地构建。

若 `pip install` 在编译 `av` / `pylibsrtp` 时报缺少头文件，再安装：

```bash
sudo apt install -y pkg-config libavformat-dev libavcodec-dev libavdevice-dev \
    libavutil-dev libswscale-dev libswresample-dev libavfilter-dev
```

### 2. Python 依赖

```bash
cd ~/walle-ai/walle-device/linux-demo
python3 -m venv ~/venv
~/venv/bin/pip install --upgrade pip
~/venv/bin/pip install -r requirements.txt
```

不需要 `--system-site-packages`（本目录不使用 ROS2 系统包）。venv 不在 `~/venv` 时，
部署时用 `./deploy.sh --venv /path/to/venv` 或环境变量 `WALLE_VENV` 指定。

### 3. 配置文件

```bash
cd ~/walle-ai/walle-device/linux-demo
cp walle-ai.conf.example walle-ai.conf
vi walle-ai.conf
```

- 必填：`MQTT_HOST`、`MQTT_PORT`、`MQTT_USERNAME`、`MQTT_PASSWORD`、`MQTT_TENANT`；
- 视频：`WHIP_SECRET` 与 `VIDEO_RTMP_URL_TEMPLATE`；
- 手机麦上行：`PHONE_AUDIO_WHEP_SECRET` 与 `PHONE_AUDIO_WHEP_URL_TEMPLATE`（模板需含
  `{expires}` / `{token}`，由 Nginx `secure_link` 校验，见
  [`doc/service_deploy.md`](../../../doc/service_deploy.md) §6.2）；
- 不需要对应能力时把 `VIDEO_ENABLE` / `PHONE_AUDIO_ENABLE` 置为 `false`。

配置查找顺序：`--env-file` > `$RPI_MONITOR_ENV` > `/etc/walle-ai.conf` > 脚本同级
`walle-ai.conf`；已存在的环境变量优先。开发环境直接运行会自动读取同级 `walle-ai.conf`；
生产环境由 `deploy.sh` 安装到 `/etc/walle-ai.conf`（`root:root 600`），不要 `chown` 给普通用户。

### 4. 自检

```bash
cd ~/walle-ai/walle-device/linux-demo
~/venv/bin/python main.py --check
```

期望输出 `== 配置检查通过 ==` 与 7 条订阅主题，退出码 0；失败时逐条列出缺失/非法项，退出码 1。
`--check` 只解析配置，不连接 Broker、不写设备标识文件；末尾的「依赖检查」会列出
`ffmpeg` / `v4l2-ctl` / `pactl` / `whep_audio_player.py` 与 Python 包（paho-mqtt、psutil、
aiortc、sounddevice），缺失只告警、不阻塞启动。

需要验证 Broker 连通性时加 `--connect`：

```bash
~/venv/bin/python main.py --check --connect
```

依次检查 DNS、TCP、TLS、MQTT 登录（CONNACK）、订阅回执（SUBACK）、发布回执（PUBACK），
并向临时主题 `walle/{租户}/{device_id}-check/v1/selfcheck` 发布一次性 nonce，确认能收到回投；
回投成功即说明「账号 + ACL（发布/订阅）+ Broker 转发」整条链路可用。使用
`<device_id>-check` 临时 clientId，不会把正在运行的服务踢下线；主题按该 clientId 生成，
与 `doc/service_deploy.md` §10 的 `walle/+/${clientid}/#` ACL 规则一致。若未收到回投，
通常是设备账号 ACL 未生效或授权源顺序不对（EMQX `deny_action=ignore` 时 SUBACK 仍可能显示成功）。

摄像头单独确认（推流前还会自动 preflight）：

```bash
v4l2-ctl --list-devices
v4l2-ctl -d /dev/video0 --list-formats-ext   # 确认支持 640x480
```

### 5. 安装为 systemd 服务

```bash
cd ~/walle-ai/walle-device/linux-demo
./deploy.sh
```

脚本依次：检查 venv → 检查 `walle-ai.conf` → `main.py --check` → 安装
`/etc/walle-ai.conf`（`root:root 600`）→ 按当前用户渲染并安装 `walle-ai.service` →
`daemon-reload`、`enable`、`restart`。可重复执行；`--no-start` 只安装不启动。

服务以执行 `deploy.sh` 的用户运行，并获得 `audio`、`video` 附加组；已启用
`ProtectSystem=strict`、`ProtectHome=read-only`，只有 linux-demo 目录（`log/`、`.device-id`）可写。
若把 `DEVICE_ID_FILE` 指到该目录之外，需要把路径追加到 `walle-ai.service.example` 的
`ReadWritePaths=` 后重新部署。

### 6. 状态与日志

```bash
sudo systemctl status walle-ai
journalctl -u walle-ai -f                 # 实时日志
journalctl -u walle-ai -n 200 --no-pager  # 最近 200 行
```

ffmpeg 推流日志写在安装目录的 `log/ffmpeg_*.log`（默认以 `main.py` 所在目录为基准，与启动时
的工作目录无关；可用 `WALLE_LOG_DIR` 改到其他目录，若在安装目录外需同步追加
systemd 模板的 `ReadWritePaths`）。每次新建日志前按「保留 7 天 / 最多 10 个文件 / 总量
100MB」清理最旧文件，可用 `VIDEO_LOG_KEEP_DAYS`、`VIDEO_LOG_KEEP_FILES`、
`VIDEO_LOG_MAX_TOTAL_MB` 调整（0 表示不限制）。

### 7. 音频会话（麦克风 / 扬声器）

`pactl`、`ffmpeg -f pulse` 与 `sounddevice` 都需要访问运行用户的 PulseAudio/PipeWire 会话。
systemd 单元已设置 `Environment=XDG_RUNTIME_DIR=/run/user/%U`，以桌面登录用户运行时通常可直接使用。

无桌面会话（Lite 镜像）或日志出现 `pactl 返回 1（Connection refused / No such file or directory）` 时：

```bash
pactl info                          # 确认当前用户能连上音频服务
loginctl enable-linger "$USER"      # 允许用户服务在未登录时运行
systemctl --user start pulseaudio   # 或 pipewire-pulse，视系统实际服务名
```

也可以把服务改为用户级：把渲染后的 unit 放到 `~/.config/systemd/user/`，执行
`systemctl --user enable --now walle-ai`（此时不需要 `User=`/`Group=`）。
若音频服务由其他用户或容器提供，可显式设置
`PULSE_SERVER=unix:/run/user/<uid>/pulse/native`（systemd 中用 `Environment=` 注入）。
未配置音频会话时，`capability/result` 的 `audio.capture.reason` / `audio.playback.reason`
会给出具体原因，启动日志也会输出「音频会话不可用」提示。

## 手动运行（仅开发调试）

同一 `clientId` 同时连接会被 Broker 互相踢下线，先停服务再手动运行：

```bash
sudo systemctl stop walle-ai        # 尚未安装服务时忽略报错
cd ~/walle-ai/walle-device/linux-demo
~/venv/bin/python main.py
```

使用其他配置文件：

```bash
~/venv/bin/python main.py --env-file /path/to/walle-ai.conf
```

## 升级

```bash
cd ~/walle-ai
git pull
cd walle-device/linux-demo
~/venv/bin/pip install -r requirements.txt
```

依赖或代码更新后重启服务；systemd 模板、配置项有变化时重新执行 `./deploy.sh`：

```bash
sudo systemctl restart walle-ai
# 或
./deploy.sh
```

设备标识默认存放在 `/var/lib/walle-ai/device-id`（`deploy.sh` 会创建目录并迁移旧的安装目录
`.device-id`），升级时不要删除；删除后会按硬件指纹重新生成，旧主题上的 retained online
会变成幽灵卡片。

## 版本号

版本来自 `VERSION`（与 ROS2 共用 `walle-device/VERSION`），查找顺序：
`WALLE_VERSION_FILE` > `main.py` 同级 `VERSION` > 上一级 `walle-device/VERSION`；
环境变量 `WALLE_VERSION_NUM` / `WALLE_VERSION_NAME` 优先于文件。

- 在仓库内用 `./deploy.sh` 部署不需要额外操作，`--check` 会打印生效版本与来源；
- 只拷贝 linux-demo 目录单独部署时，把 `walle-device/VERSION` 放到 `main.py` 同级；
- 两处都找不到时使用内置默认版本，并在 `--check` 与启动日志中告警；
- 与 App `versionName` 的同步要求见
  [`doc/version_record.md`](../../../doc/version_record.md)。

版本读取逻辑包含在「测试」章节的用例中。

## 测试

```bash
cd ~/walle-ai/walle-device/linux-demo
python3 -m unittest discover -s test -t . -v
# 已安装 pytest 时也可：python3 -m pytest test -q
```

测试不连接 Broker、不访问摄像头，覆盖主题命名与配对、UUID 校验、房间号白名单、
报文脱敏、设备标识落盘复用、配置校验与远程命令移除回归。

## 常见问题

| 现象 | 排查 |
| --- | --- |
| `--check` 列出 `MQTT_* 未配置` | 配置未加载：确认 `walle-ai.conf` 在脚本同级，或显式 `--env-file` |
| 正式启动退出码 2 | 配置校验失败（必填项、端口、TLS、CA、模板占位符），按日志逐条修复 |
| `Permission denied` 读取 `/etc/walle-ai.conf` | 正常：生产配置仅 root 可读，由 systemd 注入；调试用同级 `walle-ai.conf` |
| 服务启动失败 | `journalctl -u walle-ai -n 50`；常见原因是缺少 `/etc/walle-ai.conf` |
| Broker 不可达 | 进程不会退出，按 3s→30s 持续重试；首次连接并订阅成功时日志出现 `STARTUP READY` |
| 推流失败 | 确认 `ffmpeg` 已安装、`v4l2-ctl -d <设备> --list-formats-ext` 支持 640x480；查看 `log/ffmpeg_*.log` |
| 手机音频无声音 | 确认 `libportaudio2` 已安装，`aplay -l` / `pactl list short sinks` 能看到声卡 |
| 摄像头节点不对 | 用 udev 符号链接并设置 `CAMERA_DEVICE=/dev/walle-camera`；`video/start` 失败结果会带回当前 `cameras` 列表 |
