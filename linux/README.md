# 树莓派监控 Agent（MQTT v2）

配套 Android App《树莓派控制器》的设备端程序：周期采集树莓派状态、异常告警、远程执行命令、枚举摄像头并推流，全部通过 MQTT 与手机交互。

> **协议**：多租户主题 `walle/{tenant}/{device}/{resource}`。
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
| 上下线 | retained 上线报文 + 遗嘱（LWT），App 一打开就能看到设备是否在线 |

---

## 二、协议 v2

全部主题都在 `walle/{tenant}/{device}/` 下：

| 用途 | App → 设备（请求） | 设备 → App（应答） | 说明 |
|---|---|---|---|
| 设备发现 | `walle/{t}/discovery/query` | `.../discovery/result` | 租户内广播；应答携带型号 / 序列号 / IP / 系统 / 内核 … |
| 状态 | `.../state/query` | `.../state/result` | 完整状态快照 |
| 命令 | `.../cmd/exec` | `.../cmd/result` | 请求带 `request_id`、`timeout` |
| 摄像头 | `.../camera/capability/query` | `.../camera/capability/result` | v4l2 枚举结果 |
| 推流 | `.../video/start/request` | `.../video/start/result` | 带 `room_number` |
| 停止推流 | `.../video/stop/request` | `.../video/stop/result` | 带 `room_number` |
| 上线/下线（主动） | — | `.../online` | retained + LWT，`{"online":true/false,...}` |
| 告警（主动） | — | `.../alarm` | `level` / `event` / `metric` |

**命名约定**：请求按语义划分 —— 查询用 `…/query`、命令用 `…/exec`、动作（推流）用 `…/request`；
应答一律 `…/result`，且**只在收到对应请求后发布**；只有 `online` 与 `alarm` 是设备主动上报。

**配对**：所有请求都带 `request_id`，设备在对应的 `…/result` 里**原样回显**，
App 据此把结果与请求对上（日志里也会打印 `request_id=...` 方便对照）。

规则：

- 除 `discovery/query` 外，**每个请求都必须带匹配的 `uuid`**（本次运行实例标识），否则设备静默忽略；
- 设备只订阅自己子树下的**请求类**主题，不订通配子串（避免收到自己发布的上报）；
- 主题段只允许 `[A-Za-z0-9_-]`，`.` `:` `@` 空格会转成 `_`，其它字符丢弃 —— **App 与设备端使用同一套清洗规则**（`selftest_topics.py` 会校验一致性）。

---

## 三、首次部署到新树莓派

### 3.1 前置条件

| 项 | 要求 |
|---|---|
| 系统 | Raspberry Pi OS（Bookworm 及以上），已联网 |
| 用户 | 有 sudo 权限的普通用户（脚本会以该用户跑服务） |
| 依赖 | 脚本自动安装：`python3-venv`、`v4l-utils`（摄像头）、`ffmpeg`（推流） |
| Broker | EMQX（5.x / 6.x），已开 8883(TLS) 或 1883，并已建好设备账号 |

### 3.2 方式 A：一键脚本（推荐）

在 Mac/PC 上把 `linux/` 目录整体拷到树莓派，然后运行：

```bash
scp -r linux gs@<树莓派IP>:~/
ssh gs@<树莓派IP>
cd ~/linux
sudo ./install.sh
```

脚本会**逐项询问 5 个必填变量**（直接回车使用中括号里的默认值）：

| 变量 | 含义 | 示例 | 说明 |
|---|---|---|---|
| `MQTT_HOST` | Broker 地址 | `www.baidong.xyz` | 域名或 IP |
| `MQTT_PORT` | 端口 | `8883` | 8883=TLS（推荐），1883=明文 |
| `MQTT_USERNAME` | **设备自己**的 MQTT 账号 | `RaspPi5` | 不是手机账号 |
| `MQTT_PASSWORD` | 设备账号口令 | — | 输入时不回显 |
| `MQTT_TENANT` | 租户 = **手机 App 的登录用户名** | `home` | 两边不一致 App 就收不到设备 |

随后脚本会自动完成：装依赖 → 建 venv → 安依赖 → 拷贝代码到 `~/mqtt_demo` → 写 `/etc/rpi-monitor.env`（600）→ 安装并启动 systemd → 跑协议自检 → 打印设备主题前缀。

常用参数：

```bash
sudo ./install.sh --dry-run     # 只演示流程，不落盘（可在任何机器上跑）
sudo ./install.sh --update      # 只更新代码并重启，沿用已有配置
sudo ./install.sh --skip-deps   # 已装好依赖时跳过 apt
sudo ./install.sh -h            # 帮助
```

非交互（批量部署 / 自动化）：

```bash
MQTT_HOST=www.baidong.xyz MQTT_PORT=8883 MQTT_USERNAME=RaspPi5 \
MQTT_PASSWORD='设备口令' MQTT_TENANT=home sudo -E ./install.sh
```

### 3.3 方式 B：手动部署

```bash
# 1) 依赖
sudo apt update && sudo apt install -y python3-venv v4l-utils ffmpeg

# 2) 代码与虚拟环境
mkdir -p ~/mqtt_demo && cd ~/mqtt_demo
cp /path/to/linux/{rpi_monitor.py,selftest_topics.py,requirements.txt} .
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

# 3) 配置（600，仅 root 可读）
sudo install -m 600 /dev/null /etc/rpi-monitor.env
sudo tee /etc/rpi-monitor.env >/dev/null <<'EOF'
MQTT_HOST=www.baidong.xyz
MQTT_PORT=8883
MQTT_USERNAME=RaspPi5
MQTT_PASSWORD=设备口令
MQTT_TENANT=home
MQTT_TLS=auto
MQTT_CA_FILE=
EOF

# 4) 服务
sudo cp /path/to/linux/rpi-monitor.service /etc/systemd/system/
sudo sed -i "s#/home/gs/mqtt_demo#$HOME/mqtt_demo#g; s/^User=.*/User=$USER/" /etc/systemd/system/rpi-monitor.service
sudo systemctl daemon-reload && sudo systemctl enable --now rpi-monitor

# 5) 自检 + 看日志
./.venv/bin/python selftest_topics.py --tenant home
sudo journalctl -u rpi-monitor -n 30 --no-pager
```

启动日志里应出现：

```
设备 device_id: 666feb85d43410b2（MAC=..., CPU核心数=4, ...）
协议 v2: tenant=home device=666feb85d43410b2 broker=www.baidong.xyz:8883
遗嘱(LWT)主题: walle/home/666feb85d43410b2/online
已订阅 6 个主题；当前 USB 设备 N 个作为基线，不产生历史告警
```

### 3.4 不加 systemd，直接在前台运行（调试 / 临时）

程序会自动读取 `/etc/rpi-monitor.env`，所以**不用手动 export**：

```bash
# ① 先停掉 systemd 服务：同一个 clientId 同时连两次会被 broker 互相顶掉
sudo systemctl stop rpi-monitor
sudo systemctl disable rpi-monitor     # 想永久不自动启动再加这句

# ② 进目录、激活 venv（不激活会报 ModuleNotFoundError: paho）
cd ~/mqtt_demo
source .venv/bin/activate              # 或者直接 ./.venv/bin/python rpi_monitor.py

# ③ 先看解析结果，确认配置对（不连 broker）
python3 rpi_monitor.py --check

# ④ 前台运行，日志直接打在终端；Ctrl+C 会优雅退出并发送 offline
python3 rpi_monitor.py
```

`--check` 输出示例：

```
== 生效配置 ==
  环境文件 : /etc/rpi-monitor.env
  Broker   : www.baidong.xyz:8883   TLS=on (MQTT_TLS=auto)
  用户名   : RaspPi5
  密码     : ********
  租户     : home
  设备号   : 666feb85d43410b2
  主题前缀 : walle/home/666feb85d43410b2/
  订阅主题 :
    - walle/home/666feb85d43410b2/state/query
    ...
```

其他用法：

```bash
python3 rpi_monitor.py --env-file ./my.env     # 指定别的配置文件
MQTT_TENANT=home python3 rpi_monitor.py        # shell 变量优先于文件
./.venv/bin/python rpi_monitor.py --help       # 查看内置说明
```

要点：

- **优先级**：已存在的环境变量（shell / systemd） > 环境文件；
- **文件查找顺序**：`--env-file` > `$RPI_MONITOR_ENV` > `/etc/rpi-monitor.env` > `./rpi-monitor.env`；
- `/etc/rpi-monitor.env` 默认是 `600 root:root`，普通用户读不到时会打印 `Permission denied` 警告并回退到默认值——
  执行 `sudo chown $(whoami) /etc/rpi-monitor.env` 即可（仍保持 600）；
- 前台运行**不要**同时开着 systemd 服务（同 clientId 会互踢，表现为连接反复断开）；
- 想看告警/命令输出，前台运行比 `journalctl` 更直观；长期跑还是建议回到 systemd。

### 3.5 EMQX 侧（一次性）

1. **客户端认证 → 内置数据库**：确认设备账号存在（如 `RaspPi5`），并给手机端准备账号（如 `home`）。
2. **客户端授权 → File → 设置**：粘贴 [`emqx/acl.conf.example`](emqx/acl.conf.example) 的内容，把 `home` / `RaspPi5` 换成你的实际值；保存后热加载，无需重启。

```erlang
{allow, {username, {re, "^home$"}}, all, ["walle/home/#"]}.
{allow, {username, {re, "^RaspPi5$"}}, publish,   ["walle/home/${clientid}/#"]}.
{allow, {username, {re, "^RaspPi5$"}}, subscribe, ["walle/home/${clientid}/#",
                                                   "walle/home/discovery/query"]}.
```

> 若你的 EMQX 版本不支持 `${clientid}` 占位符，把设备规则里的 `${clientid}` 换成 `+`。

### 3.6 手机 App 侧

App → 设置 → 服务器连接：

| 字段 | 填什么 |
|---|---|
| Broker / 端口 | 与设备一致（`www.baidong.xyz` / `8883`） |
| MQTT 用户名 | **`MQTT_TENANT` 的值**（如 `home`） |
| MQTT 密码 | 该账号口令 |
| TLS | 端口 8883 时自动开启 |

App 订阅的是「设备 → App」的 8 个主题（`walle/<用户名>/+/{online,alarm,discovery/result,state/result,cmd/result,camera/capability/result,video/start/result,video/stop/result}`），
所以**租户与用户名必须是同一个值**。家里多人共用时，所有人填同一个账号即可看到同一台小车。

> App 不订阅 `walle/<用户名>/#`：MQTT 3.1.1 没有 no-local 语义，订通配子树会把自己发出去的
> `state/query`、`discovery/query` 再收回来一遍（日志里会看到 `← RECV` 自己发的报文）。

---

## 四、配置项

全部通过 `/etc/rpi-monitor.env` 覆盖（改完 `sudo systemctl restart rpi-monitor`）：

| 变量 | 默认 | 必填 | 说明 |
|---|---|---|---|
| `MQTT_HOST` | `www.baidong.xyz` | ✅ | Broker 地址 |
| `MQTT_PORT` | `1883` | ✅ | 建议 8883 |
| `MQTT_USERNAME` | `RaspPi5` | ✅ | 设备账号 |
| `MQTT_PASSWORD` | `123456` | ✅ | 设备口令（请改掉） |
| `MQTT_TENANT` | 同用户名 | ✅ | 租户 = App 用户名 |
| `MQTT_TLS` | `auto` | — | `auto`：8883/8084 自动 TLS；`on` / `off` 强制 |
| `MQTT_CA_FILE` | 空 | — | 自签 CA 路径，空则用系统 CA |
| `MONITOR_INTERVAL` | `5` | — | 巡检周期（秒） |
| `THRESH_CPU` / `THRESH_MEM` / `THRESH_DISK` | `90.0` | — | 阈值（%） |
| `THRESH_TEMP` | `80.0` | — | CPU 温度阈值（℃） |
| `THRESH_NET_MIB` | `10` | — | 带宽阈值（MiB/s） |
| `ENABLE_CMD_EXEC` | `true` | — | 是否允许远程命令 |
| `CAM_ENABLE` | `true` | — | 是否枚举摄像头 |
| `VIDEO_ENABLE` | `true` | — | 是否允许推流 |

推流地址模板、命令白名单等少量参数在 `rpi_monitor.py` 顶部「配置区」（`VIDEO_RTMP_URL_TEMPLATE`、`CMD_ALLOWLIST`）。

---

## 五、日常运维

```bash
sudo systemctl status rpi-monitor        # 状态
sudo journalctl -u rpi-monitor -f        # 实时日志
sudo journalctl -u rpi-monitor -n 100    # 最近 100 行
sudo systemctl restart rpi-monitor       # 改完 /etc/rpi-monitor.env 后重启
sudo nano /etc/rpi-monitor.env           # 改配置
cd ~/mqtt_demo && ./.venv/bin/python selftest_topics.py --tenant home   # 协议自检
sudo ./install.sh --update               # 升级代码（保留配置）
```

自检脚本会校验：租户/设备段清洗规则与 App 一致、主题拼装、订阅清单、请求→处理函数、发布落在命名空间、v1 常量已清除。

---

## 六、排障

| 现象 | 可能原因 | 处理 |
|---|---|---|
| App 里设备不出现 | 租户与 App 用户名不一致 | 对比 `journalctl` 里的 `tenant=` 与 App 设置页用户名 |
| 设备在线但状态查询无响应 | ACL 拒了 publish 到 `.../state` | EMQX → 客户端 → 该设备 → 看 ACL 命中/拒绝记录 |
| 连接被拒（reason 5 / 32100） | 账号密码错，或 ACL 无该主题权限 | 核对 `/etc/rpi-monitor.env` 与 EMQX 用户 |
| TLS 握手失败 | 端口不是 TLS / 证书域名不匹配 / 缺 CA | `openssl s_client -connect host:8883 -servername host`；必要时配 `MQTT_CA_FILE` |
| 摄像头列表为空 | 未装 `v4l-utils` 或没有 `/dev/video*` | `sudo apt install -y v4l-utils`、`v4l2-ctl --list-devices` |
| 推流黑屏 | ffmpeg 起不来 / RTMP secret 错 / 房间号不一致 | `journalctl -u rpi-monitor | grep ffmpeg`；看 `log/ffmpeg_*.log` |
| 设备掉线后一直显示在线 | 遗嘱只在进程异常退出时由 broker 代发；`kill -9` 与 broker 断连场景依赖 keepalive | 60s keepalive 内应自动转 offline；长期不转检查网络与 broker |
| 命令被忽略 | 报文里的 `uuid` 不匹配 | App 会带当前 `uuid`；手动测试时用 `mosquitto_pub` 记得带上 |
| 命令执行了但 App 没结果 | 看设备日志有没有 `已响应命令执行 -> walle/.../cmd/result（...）`；有则说明已发出，查 ACL 是否允许 publish 到该主题；没有则该请求未被处理（主题名或 `uuid` 不匹配） | `journalctl -u rpi-monitor -n 50 \| grep "命令"` |

手动验证（不依赖 App）：

```bash
# 订阅本租户（应能立刻看到 retained 的 online）
mosquitto_sub -h www.baidong.xyz -p 8883 -u home -P '<口令>' -t 'walle/home/#' -v

# 触发一次状态查询（uuid 换成日志里的 device uuid）
mosquitto_pub -h www.baidong.xyz -p 8883 -u home -P '<口令>' \
  -t 'walle/home/666feb85d43410b2/state/query' -m '{"uuid":"<uuid>"}'
```

---

## 七、目录结构

```
linux/
├── rpi_monitor.py              # 主程序（单文件）
├── requirements.txt            # paho-mqtt / psutil
├── selftest_topics.py          # 主题契约自检
├── install.sh                  # 一键安装 / 升级
├── rpi-monitor.service         # systemd 模板（install.sh 会按实际路径改写）
├── rpi-monitor.env.example     # 配置样例
├── emqx/acl.conf.example       # EMQX 授权规则
└── docs/legacy-v1-spec.md      # v1 时期的需求文档（归档，可删除）
```

---

## 八、版本说明

- 本版本为**协议 v2（多租户主题）**，已移除全部 v1 全局主题（`device/online`、`cmd/exec` …）。
- 必须与支持 v2 的 Android App 配套；老版本 App 无法再与本设备通信。
- 16KB page size、TLS 证书等与 App 端改造无关，设备端只需保证 `MQTT_TLS` / `MQTT_CA_FILE` 配置正确。
