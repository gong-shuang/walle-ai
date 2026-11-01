# 树莓派监控 Agent（MQTT）

配套 Android App《树莓派控制器》的设备端程序：周期采集树莓派状态、异常告警、远程执行命令、枚举摄像头并推流，全部通过 MQTT 与手机交互。

> **协议**：多租户主题 `walle/{tenant}/{device}/v1/{resource}`。
> **tenant** = 手机 App 登录用的用户名；**device** = 本机稳定 `device_id`（MAC + CPU 核心数 + 内存容量的 md5 前 16 位，重启/重装都不变）。

---

## 一、功能

| 能力 | 说明 |
|---|---|
| 状态采集 | CPU（使用率/温度/频率/各核）、内存、磁盘各挂载点、网络速率、系统信息 |
| 异常告警 | CPU / 内存 / 磁盘 / 带宽 / 温度超阈值，USB 插拔；带状态机，越限才报、恢复后发 recover |
| 远程命令 | 执行 shell 命令并回传 stdout/err/退出码，支持超时与输出截断 |
| 摄像头能力 | `v4l2-ctl` 枚举 `/dev/video*` 的格式 / 分辨率 / 帧率 |
| 视频推流 | 按 App 下发的 `room_number` 拼 RTMP 地址，`ffmpeg` 推流，可停止 |
| 麦克风 | 支持 `portaudio` 接收 App 上行的音频，可停止 |
| 上下线 | retained 上线报文 + 遗嘱（LWT），App 一打开就能看到设备是否在线 |

---

## 二、协议

设备与 App 的全部 MQTT 通信约定（主题命名、请求/应答配对、报文格式、ACL、修改协议的流程）
唯一定义在 **[doc/MQTT_protocol_convention.md](../doc/MQTT_protocol_convention.md)**，本文不复述，避免两处维护同一套规则产生偏差。

设备侧只需记住两点：

- 所有主题都在 `walle/{tenant}/{device}/v1/` 子树下，`tenant` = App 登录用户名，`device` = 本机稳定 `device_id`；
- 主题段清洗规则与 App 逐字符一致（`[A-Za-z0-9_-]` 保留，`.` `:` `@` 空格转 `_`），由 [`selftest_topics.py`](selftest_topics.py) 校验。

---

## 三、部署

### 3.1 前置条件

| 项 | 要求 |
|---|---|
| 系统 | Raspberry Pi OS（Bookworm 及以上），已联网 |
| 用户 | 有 sudo 权限的普通用户（服务会以该用户跑） |
| 依赖 | 手动安装：`python3-venv`、`v4l-utils`（摄像头）、`ffmpeg`（推流）、`libportaudio2`（手机麦上行出声） |
| 服务器端 | 必须部署了EMQX和Oyxl服务，具体部署请参考[doc/service_deploy.md](../doc/service_deploy.md) |

### 3.2 首次配置环境

在 Mac/PC 上把 `linux/` 目录整体拷到树莓派，假设树莓派的用户名是 `gs`，项目的目录是 `~/walle_linux`，则：

```bash
scp -r linux gs@<树莓派IP>:~/walle_linux
ssh gs@<树莓派IP> && cd ~/walle_linux
```

装系统依赖、建虚拟环境：

```bash
sudo apt update && sudo apt install -y python3-venv v4l-utils ffmpeg libportaudio2
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

改配置：直接编辑本目录的 `walle-ai.env`（必填一定要正常，可以参考文档 [doc/service_deploy.md](../doc/service_deploy.md)），安装到 `/etc`：

```bash
sudo nano walle-ai.env
sudo install -m 604 walle-ai.env /etc/walle-ai.env   # 604，所有用户可读，仅 root 为可读
```

`walle-ai.env` 中的 6 个必填项：

| 变量 | 含义 | 示例 | 说明 |
|---|---|---|---|
| `MQTT_HOST` | Broker 地址 | `www.baidong.xyz` | 域名或 IP |
| `MQTT_PORT` | 端口 | `8883` | 8883=TLS（推荐），1883=明文 |
| `MQTT_USERNAME` | **设备自己**的 MQTT 账号 | `RaspPi5` | 不是手机账号 |
| `MQTT_PASSWORD` | 设备账号口令 | — | 默认 `123456`，请改掉 |
| `MQTT_TENANT` | 租户 = **手机 App 的登录用户名** | `home` | 两边不一致 App 就收不到设备 |
| `WHIP_SECRET` | **WHIP 推流鉴权 secret** | — | 每台服务器不同，重置后旧客户端失效，不配置则视频推流会被服务端拒绝 |

> App 端也要用同一个 secret。
> 可选覆盖：`VIDEO_RTMP_URL_TEMPLATE` 支持 `{room}` / `{secret}` 两个占位符，用来换推流域名。

### 3.3 使用 systemctl 运行（长期部署）

安装 systemd 服务时按当前用户名和home目录改写模板（walle-ai.service）里的用户和路径，然后启动：

```bash
sudo cp walle-ai.service /etc/systemd/system/
sudo sed -i "s#/home/gs/walle_linux#$HOME/walle_linux#g; s/^User=.*/User=$USER/" /etc/systemd/system/walle-ai.service # 按“当前登录用户 + 当前Home目录”把模板里的路径和用户替换成真实值
sudo systemctl daemon-reload && sudo systemctl enable --now walle-ai
```

自检 + 看日志：

```bash
./.venv/bin/python selftest_topics.py --tenant home
sudo journalctl -u walle-ai -n 30 --no-pager
```

启动日志里应出现：

```
Nov 01 20:10:21 orangepi5plus systemd[1]: Started RaspberryPi System Monitor over MQTT.
Nov 01 20:10:21 orangepi5plus python[46768]: 2026-11-01 20:10:21 [INFO] 配置来源: /etc/walle-ai.env
Nov 01 20:10:21 orangepi5plus python[46768]: 2026-11-01 20:10:21 [INFO] 设备 device_id: f1873c8b51019dc0（MAC=e0:75:26:6f:32:1f, CPU核心数=8, 内存总容量=8320167936字节）
Nov 01 20:10:21 orangepi5plus python[46768]: 2026-11-01 20:10:21 [INFO] 设备 UUID: ac2e9320-0889-5266-8a12-bb2d38025448（MAC=e0:75:26:6f:32:1f, IP=192.168.31.38, 启动时间=2026-11-01 20:10:21）
Nov 01 20:10:21 orangepi5plus python[46768]: 2026-11-01 20:10:21 [INFO] 设备 f1873c8b51019dc0 (192.168.31.38) 启动监控，采样周期 5s
Nov 01 20:10:21 orangepi5plus python[46768]: 2026-11-01 20:10:21 [INFO] 协议 v1: tenant=home device=f1873c8b51019dc0 broker=www.baidong.xyz:8883
Nov 01 20:10:21 orangepi5plus python[46768]: 2026-11-01 20:10:21 [INFO] 遗嘱(LWT)主题: walle/home/f1873c8b51019dc0/v1/online
Nov 01 20:10:21 orangepi5plus python[46768]: 2026-11-01 20:10:21 [INFO] 启用 TLS 连接 www.baidong.xyz:8883（CA=system）
Nov 01 20:10:21 orangepi5plus python[46768]: 2026-11-01 20:10:21 [INFO] MQTT 连接成功: www.baidong.xyz:8883（tenant=home device=f1873c8b51019dc0）
Nov 01 20:10:22 orangepi5plus python[46768]: 2026-11-01 20:10:22 [INFO] 已订阅 8 个主题；当前 USB 设备 11 个作为基线，不产生历史告警
Nov 01 20:10:22 orangepi5plus python[46768]: 2026-11-01 20:10:22 [INFO]   · walle/home/f1873c8b51019dc0/v1/state/query
Nov 01 20:10:22 orangepi5plus python[46768]: 2026-11-01 20:10:22 [INFO]   · walle/home/f1873c8b51019dc0/v1/cmd/exec
Nov 01 20:10:22 orangepi5plus python[46768]: 2026-11-01 20:10:22 [INFO]   · walle/home/f1873c8b51019dc0/v1/capability/query
Nov 01 20:10:22 orangepi5plus python[46768]: 2026-11-01 20:10:22 [INFO]   · walle/home/f1873c8b51019dc0/v1/video/start/request
Nov 01 20:10:22 orangepi5plus python[46768]: 2026-11-01 20:10:22 [INFO]   · walle/home/f1873c8b51019dc0/v1/video/stop/request
Nov 01 20:10:22 orangepi5plus python[46768]: 2026-11-01 20:10:22 [INFO]   · walle/home/f1873c8b51019dc0/v1/phone/audio/send
Nov 01 20:10:22 orangepi5plus python[46768]: 2026-11-01 20:10:22 [INFO]   · walle/home/f1873c8b51019dc0/v1/phone/audio/stop
Nov 01 20:10:22 orangepi5plus python[46768]: 2026-11-01 20:10:22 [INFO]   · walle/home/v1/discovery/query

```

升级代码：把新的 `main.py` 等文件拷到 `~/walle_linux` 覆盖，再 `sudo systemctl restart walle-ai`（配置保留在 `/etc/walle-ai.env`，不受影响）。

### 3.4 调试：直接在终端运行，方便查看日志

程序会自动读取 `/etc/walle-ai.env`，所以**不用手动 export**：

```bash
# ① 先停掉 systemd 服务：同一个 clientId 同时连两次会被 broker 互相顶掉
sudo systemctl stop walle-ai
sudo systemctl disable walle-ai     # 想永久不自动启动再加这句

# ② 进目录、激活 venv（不激活会报 ModuleNotFoundError: paho）
cd ~/walle_linux
source .venv/bin/activate              # 或者直接 ./.venv/bin/python main.py

# ③ 先看解析结果，确认配置对（不连 broker）
python3 main.py --check

# ④ 前台运行，日志直接打在终端；Ctrl+C 会优雅退出并发送 offline
sudo python3 main.py
```

`--check` 输出示例：

```
(.venv) orangepi@orangepi5plus:~/walle_linux$ python3 main.py 
2026-11-01 20:08:38 [INFO] 配置来源: /etc/walle-ai.env
2026-11-01 20:08:38 [INFO] 设备 device_id: f1873c8b51019dc0（MAC=e0:75:26:6f:32:1f, CPU核心数=8, 内存总容量=8320167936字节）
2026-11-01 20:08:38 [INFO] 设备 UUID: 930213af-aad0-53ef-9645-2e400f8a4863（MAC=e0:75:26:6f:32:1f, IP=192.168.31.38, 启动时间=2026-11-01 20:08:38）
2026-11-01 20:08:38 [INFO] 设备 f1873c8b51019dc0 (192.168.31.38) 启动监控，采样周期 5s
2026-11-01 20:08:38 [INFO] 协议 v1: tenant=home device=f1873c8b51019dc0 broker=www.baidong.xyz:8883
2026-11-01 20:08:38 [INFO] 遗嘱(LWT)主题: walle/home/f1873c8b51019dc0/v1/online
2026-11-01 20:08:38 [INFO] 启用 TLS 连接 www.baidong.xyz:8883（CA=system）
2026-11-01 20:08:39 [INFO] MQTT 连接成功: www.baidong.xyz:8883（tenant=home device=f1873c8b51019dc0）
2026-11-01 20:08:39 [INFO] 已订阅 8 个主题；当前 USB 设备 11 个作为基线，不产生历史告警

```

其他用法：

```bash
python3 main.py --env-file ./my.env     # 指定别的配置文件
MQTT_TENANT=home python3 main.py        # shell 变量优先于文件
./.venv/bin/python main.py --help       # 查看内置说明
```

要点：

- **优先级**：已存在的环境变量（shell / systemd） > 环境文件；
- **文件查找顺序**：`--env-file` > `$RPI_MONITOR_ENV` > `/etc/walle-ai.env` > `./walle-ai.env`；
- `/etc/walle-ai.env` 默认是 `600 root:root`，普通用户读不到时会打印 `Permission denied` 警告并回退到默认值——
  执行 `sudo chown $(whoami) /etc/walle-ai.env` 即可（仍保持 600）；
- 前台运行**不要**同时开着 systemd 服务（同 clientId 会互踢，表现为连接反复断开）；
- 想看告警/命令输出，前台运行比 `journalctl` 更直观；长期跑还是建议回到 systemd。




---

## 四、配置项

全部通过 `/etc/walle-ai.env` 覆盖（改完 `sudo systemctl restart walle-ai`）：

| 变量 | 默认 | 必填 | 说明 |
|---|---|---|---|
| `MQTT_HOST` | `www.abcde.xyz` | ✅ | Broker 地址 |
| `MQTT_PORT` | `8883` | ✅ | 建议 8883 |
| `MQTT_USERNAME` | `RaspPi5` | ✅ | 设备账号 |
| `MQTT_PASSWORD` | `` | ✅ | 设备口令（请改掉） |
| `MQTT_TENANT` | `home` | ✅ | 租户 = App 用户名 |
| `WHIP_SECRET` | `xxxxxx` | ✅ | WHIP 密钥 |
| `MQTT_TLS` | `auto` | — | `auto`：8883/8084 自动 TLS；`on` / `off` 强制 |
| `MQTT_CA_FILE` | 空 | — | 自签 CA 路径，空则用系统 CA |
| `MONITOR_INTERVAL` | `5` | — | 巡检周期（秒） |
| `THRESH_CPU` / `THRESH_MEM` / `THRESH_DISK` | `90.0` | — | 阈值（%） |
| `THRESH_TEMP` | `80.0` | — | CPU 温度阈值（℃） |
| `THRESH_NET_MIB` | `10` | — | 带宽阈值（MiB/s） |
| `ENABLE_CMD_EXEC` | `true` | — | 是否允许远程命令 |
| `CAM_ENABLE` | `true` | — | 是否枚举摄像头 |
| `VIDEO_ENABLE` | `true` | — | 是否允许推流 |

推流地址模板、命令白名单等少量参数在 `main.py` 顶部「配置区」（`VIDEO_RTMP_URL_TEMPLATE`、`CMD_ALLOWLIST`）。

---

## 五、日常运维

```bash
sudo systemctl status walle-ai        # 状态
sudo journalctl -u walle-ai -f        # 实时日志
sudo journalctl -u walle-ai -n 100    # 最近 100 行
sudo systemctl restart walle-ai       # 改完 /etc/walle-ai.env 后重启
sudo nano /etc/walle-ai.env           # 改配置
cd ~/walle_linux && ./.venv/bin/python selftest_topics.py --tenant home   # 协议自检
```

升级代码：拷新文件覆盖 `~/walle_linux` 后 `sudo systemctl restart walle-ai`（见 3.3）。

自检脚本会校验：租户/设备段清洗规则与 App 一致、主题拼装、订阅清单、请求→处理函数、发布落在命名空间、v1 常量已清除。

---

## 六、排障

| 现象 | 可能原因 | 处理 |
|---|---|---|
| App 里设备不出现 | 租户与 App 用户名不一致 | 对比 `journalctl` 里的 `tenant=` 与 App 设置页用户名 |
| 设备在线但状态查询无响应 | ACL 拒了 publish 到 `.../state` | EMQX → 客户端 → 该设备 → 看 ACL 命中/拒绝记录 |
| 连接被拒（reason 5 / 32100） | 账号密码错，或 ACL 无该主题权限 | 核对 `/etc/walle-ai.env` 与 EMQX 用户 |
| TLS 握手失败 | 端口不是 TLS / 证书域名不匹配 / 缺 CA | `openssl s_client -connect host:8883 -servername host`；必要时配 `MQTT_CA_FILE` |
| 摄像头列表为空 | 未装 `v4l-utils` 或没有 `/dev/video*` | `sudo apt install -y v4l-utils`、`v4l2-ctl --list-devices` |
| 推流黑屏 | ffmpeg 起不来 / RTMP secret 错 / 房间号不一致 | `journalctl -u walle-ai | grep ffmpeg`；看 `log/ffmpeg_*.log` |
| 设备掉线后一直显示在线 | 遗嘱只在进程异常退出时由 broker 代发；`kill -9` 与 broker 断连场景依赖 keepalive | 60s keepalive 内应自动转 offline；长期不转检查网络与 broker |
| 命令被忽略 | 报文里的 `uuid` 不匹配 | App 会带当前 `uuid`；手动测试时用 `mosquitto_pub` 记得带上 |
| 命令执行了但 App 没结果 | 看设备日志有没有 `已响应命令执行 -> walle/.../cmd/result（...）`；有则说明已发出，查 ACL 是否允许 publish 到该主题；没有则该请求未被处理（主题名或 `uuid` 不匹配） | `journalctl -u walle-ai -n 50 \| grep "命令"` |

手动验证（不依赖 App）：

```bash
# 订阅本租户（应能立刻看到 retained 的 online）
mosquitto_sub -h www.baidong.xyz -p 8883 -u home -P '<口令>' -t 'walle/home/#' -v

# 触发一次状态查询（uuid 换成日志里的 device uuid）
mosquitto_pub -h www.baidong.xyz -p 8883 -u home -P '<口令>' \
  -t 'walle/home/666feb85d43410b2/v1/state/query' -m '{"uuid":"<uuid>"}'
```

---

## 七、目录结构

```
linux/
├── main.py                     # 主程序（单文件）
├── whep_audio_player.py        # 手机麦上行播放端（WHEP 拉流 + PortAudio 出声）
├── requirements.txt            # paho-mqtt / psutil / aiortc / sounddevice
├── selftest_topics.py          # 主题契约自检
├── walle-ai.service            # systemd 模板（部署时按实际用户 / 目录改写，见 3.3）
├── walle-ai.env                # 配置样例（部署时安装到 /etc/walle-ai.env）
├── emqx/acl.conf.example       # EMQX 授权规则
└── docs/legacy-v1-spec.md      # v1 时期的需求文档（归档，可删除）
```

---

## 八、版本说明

- 必须与支持 的 Android App 配套；
- 16KB page size、TLS 证书等与 App 端改造无关，设备端只需保证 `MQTT_TLS` / `MQTT_CA_FILE` 配置正确。
