#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""net_mqtt_bridge 配置与主题常量。

集中环境文件加载、MQTT 连接参数、主题命名与运行期开关；其余模块统一读取本模块。
"""

import hashlib
import os
import socket
import sys
from pathlib import Path

# ============================== 启动参数 / 环境文件 ==============================
# 目的：既能被 systemd 用 EnvironmentFile= 拉起，也能直接 `python3 main.py`
# 前台运行（自动读取 /etc/walle-ai.conf），两者共用同一份配置。
#
# 优先级：已存在的环境变量（shell / systemd） > 环境文件里的值。
# 文件查找顺序：--env-file 指定 > $RPI_MONITOR_ENV > /etc/walle-ai.conf > ./walle-ai.conf

ENV_CANDIDATES = ("/etc/walle-ai.conf", "walle-ai.conf")
ENV_SOURCE = None      # 实际加载到的文件（None 表示没加载）


def _cli_arg(name):
    """从 sys.argv 里取 `--name value` 或 `--name=value`，没有则返回 None。"""
    for i, arg in enumerate(sys.argv[1:]):
        if arg == name and i + 2 <= len(sys.argv) - 1:
            return sys.argv[i + 2]
        if arg.startswith(name + "="):
            return arg.split("=", 1)[1]
    return None


def _load_env_file(path):
    """把 KEY=VALUE 文件读进 os.environ（不覆盖已有变量）。返回是否成功。"""
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].lstrip()
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
        return True
    except OSError as exc:
        # 生产环境 /etc/walle-ai.conf 为 600 root:root，由 systemd EnvironmentFile 注入；
        # 手动开发请改用 scripts/start.sh，不要为了读取而放宽文件权限。
        print(f"[warn] 读取环境文件失败 {path}: {exc}", file=sys.stderr)
        print("       若由 systemd 启动，配置已通过 EnvironmentFile 注入，可忽略；"
              "手动运行请改用 scripts/start.sh", file=sys.stderr)
        return False


def _init_env():
    """按优先级加载环境文件，返回加载到的路径。"""
    # systemd EnvironmentFile 已注入时无需再读文件（生产配置为 600 root:root，
    # 普通用户不可读，直接读会误报“不存在或不可读”）
    required = ("MQTT_HOST", "MQTT_USERNAME", "MQTT_PASSWORD", "MQTT_TENANT")
    if all(os.environ.get(k) for k in required):
        return None
    explicit = _cli_arg("--env-file") or os.environ.get("RPI_MONITOR_ENV")
    candidates = [explicit] if explicit else list(ENV_CANDIDATES)
    for path in candidates:
        if not path:
            continue
        if os.path.isfile(path) and _load_env_file(path):
            return path
        if explicit:
            print(f"[warn] 指定的环境文件不存在或不可读：{path}", file=sys.stderr)
    return None


ENV_SOURCE = _init_env()

# ============================== 配置区 ==============================

# ---- MQTT ----
# 不提供默认 broker / 用户名：必须由 /etc/walle-ai.conf（或 systemd Environment=）注入。
# 缺失时节点拒绝启动（validate_mqtt_config），避免设备误连演示服务器或使用默认凭据。
MQTT_HOST = os.environ.get("MQTT_HOST", "").strip()
_MQTT_PORT_RAW = os.environ.get("MQTT_PORT", "8883").strip()
try:
    MQTT_PORT = int(_MQTT_PORT_RAW)
except ValueError:
    MQTT_PORT = 0
MQTT_USER = os.environ.get("MQTT_USERNAME", "").strip()
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "")
MQTT_KEEPALIVE = 60
MQTT_QOS = 1
# TLS：默认强制开启，不再支持明文回退；auto 仅在 8883/8084 端口下自动启用
MQTT_TLS = os.environ.get("MQTT_TLS", "on").strip().lower()  # on | auto（off 会被拒绝）
MQTT_CA_FILE = os.environ.get("MQTT_CA_FILE", "").strip()      # 留空则用系统 CA
TLS_PORTS = (8883, 8084)

# ---- 主题命名约定（完整主题 = walle/{tenant}/{device}/v1/{资源}）----
#   请求按语义命名：查询 .../query、动作 .../request、持续控制 .../set；
#   应答统一 .../result，且**只在收到对应请求后发布**，绝不主动上报；
#   主动上报只有 online（上线/下线）与 alarm（告警/USB 插拔）两个。
# ---- 应答（设备 -> App）----
RES_DISCOVERY_RESULT = "discovery/result"           # 收到 discovery/query 后发布（设备身份详情）
RES_STATE_RESULT = "state/result"                   # 收到 state/query 后发布（状态快照）
RES_CAPABILITY_RESULT = "capability/result"         # 收到 capability/query 后发布
RES_VIDEO_START_RESULT = "video/start/result"       # 收到 video/start/request 后发布
RES_VIDEO_STOP_RESULT = "video/stop/result"         # 收到 video/stop/request 后发布
RES_PHONE_AUDIO_SEND_RESULT = "phone/audio/send/result"   # 收到 phone/audio/send 后发布
RES_PHONE_AUDIO_STOP_RESULT = "phone/audio/stop/result"   # 收到 phone/audio/stop 后发布
RES_CAR_MOTOR_RESULT = "car/motor/result"                 # 收到 car/motor/set 后发布

# ---- 主动上报（设备 -> App，不必等查询）----
RES_ONLINE = "online"                       # retained + 遗嘱

# ---- 设备发现防抖（N7）----
# 正常情况下 App 不再自动广播 discovery，改为订阅 retained online 建列表；
# discovery 只在「手动下拉刷新」和「一台设备都没回放出来」时出现。
# 但多台手机同时点刷新仍会让 N 台设备同时回包，因此设备侧再加两道保护：
DISCOVERY_JITTER_SEC = 0.8                  # 首次应答前的随机延迟上限（秒），避免同时回包
DISCOVERY_MIN_INTERVAL_SEC = 2.0            # 同一台设备两次应答的最小间隔（秒）
RES_ALARM = "alarm"                         # 告警 / 恢复 / USB 插拔

# ---- 订阅的请求（App -> 设备）----
REQ_DISCOVERY = "discovery/query"
REQ_STATE = "state/query"
REQ_CAPABILITY = "capability/query"
REQ_VIDEO_START = "video/start/request"
REQ_VIDEO_STOP = "video/stop/request"
REQ_PHONE_AUDIO_SEND = "phone/audio/send"     # 手机麦上行：App 开始往 {room}_audio 推音频
REQ_PHONE_AUDIO_STOP = "phone/audio/stop"     # 手机麦上行：App 停止推音频
REQ_CAR_MOTOR_SET = "car/motor/set"           # 持续控制：最新一条覆盖旧状态

# ---- car/motor/set 常量（协议固定值，改协议时同步文档 §4.10）----
CAR_MOTOR_LINEAR_MIN = 0.5          # 允许的线速度下限（m/s）
CAR_MOTOR_LINEAR_MAX = 1.0          # 允许的线速度上限（m/s）
CAR_MOTOR_ANGULAR_SPEED = 2.5       # 固定角速度（rad/s）
CAR_MOTOR_WHEEL_FACTOR = 0.1389     # (wheelbase + track_width) / 2，用于轮速预算
# 同时旋转时，|linear| 的上限 = 轮速上限 - k*|angular| = 1.0 - 0.347
CAR_MOTOR_LINEAR_LIMIT_WITH_ROTATE = round(
    CAR_MOTOR_LINEAR_MAX - CAR_MOTOR_WHEEL_FACTOR * CAR_MOTOR_ANGULAR_SPEED, 3)
CAR_MOTOR_STATE_HZ = 10.0           # 运动状态下本地重发 /cmd_vel 的频率
CAR_MOTOR_DEADMAN_SEC = 0.5         # 超过该时间没有有效命令则自动停车

# ---- 设备标识 ----
HOSTNAME = socket.gethostname()
# device_id：设备唯一标识。首次启动按「MAC + CPU 核心数 + 内存 GiB」md5 取 16 位生成，
#             随后落盘复用（resolve_device_id），因此重启 / 重装 / 换 IP 都不会变。
#             **不能每次启动重算**：MemTotal 在两次启动间会浮动几 KiB（内核保留页、启动参数），
#             直接进哈希会让同一台设备换一个 identity —— 新主题、旧主题上的 retained
#             online 变成幽灵卡片（实测同一台机器两次启动 device_id 不同）。
# uuid：程序实例标识，由「MAC + IP + 启动时间」生成，本次运行期间不变，下次运行会变
DEVICE_ID = ""      # 在 RpiMonitor.__init__ 中解析（落盘优先）
DEVICE_UUID = ""    # 在 RpiMonitor.__init__ 中计算
DEVICE_ID_SOURCE = ""   # device_id 的来源说明（日志 / --check 用）

# 标识落盘位置：默认与 main.py 同级的 .device-id（升级只覆盖 main.py，不会丢），
# 可用环境变量 DEVICE_ID_FILE 覆盖；写不进去时回退 ~/.config/walle-ai/device-id。
DEVICE_ID_FILE = os.environ.get("DEVICE_ID_FILE", "").strip()

# ---- 多租户主题（命名规则与 Android 端 TopicNames 完全一致）----
# 主题形如 walle/{tenant}/{device}/v1/{resource}：
#   tenant = MQTT_TENANT（App 登录用户名，清洗后），Broker 用 walle/${tenant}/# 做 ACL；
#   device = 下面的 DEVICE_ID（跨重启/重装不变）。
#
# 租户必须显式配置（= 手机 App 登录用户名），不再回退到 MQTT 用户名
MQTT_TENANT = os.environ.get("MQTT_TENANT", "").strip()
TOPIC_ROOT = "walle"
# 主题协议版本段：walle/{tenant}/{device}/v1/{resource}
# 升协议（报文结构变了）时新增 v2 段，旧设备继续用 v1，两套主题可以并存。
PROTOCOL_VERSION = "v1"

# 设备端软件版本：环境变量优先（env_walle.sh 会 source 并 export）；
# 未注入时读取设备端根目录（walle-device）下的 VERSION，linux-demo 与 ROS2 共用；
# 路径按目录结构推导，不写死绝对路径；非标准部署可用 WALLE_VERSION_FILE 覆盖。
def _version_root():
    """定位设备端根目录（VERSION 所在目录）：环境变量优先，其次按包路径推导。"""
    for key in ("WALLE_HOME", "WALLE_WS"):
        value = os.environ.get(key)
        if value:
            base = Path(value)
            return base.parent if base.name == "ros2_ws" else base
    here = Path(__file__).resolve()
    for parent in here.parents:
        if parent.name == "ros2_ws":
            return parent.parent
    return here.parent


VERSION_FILE = os.environ.get("WALLE_VERSION_FILE") or str(
    _version_root() / "VERSION")


def _parse_version_file(path):
    values = {}
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            key, sep, value = line.partition("=")
            if sep:
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


try:
    _VERSION_VALUES = _parse_version_file(VERSION_FILE)
except OSError:
    _VERSION_VALUES = {}

try:
    VERSION_CODE = int(os.environ.get("WALLE_VERSION_NUM")
                       or _VERSION_VALUES.get("WALLE_VERSION_NUM") or 0)
except ValueError:
    VERSION_CODE = 0
VERSION_NAME = (os.environ.get("WALLE_VERSION_NAME")
                or _VERSION_VALUES.get("WALLE_VERSION_NAME") or "0.0.0")


def _is_ascii_alnum(ch):
    return ("a" <= ch <= "z") or ("A" <= ch <= "Z") or ("0" <= ch <= "9")


def sanitize_segment(raw):
    """主题段清洗：必须与 Android 端 TopicNames.sanitize 完全一致。

    只保留 [A-Za-z0-9_-]；`.` `:` `@` 空格 → `_`；其余字符丢弃；空值 → unknown；
    超过 64 字符时保留前 40 位 + 短哈希（正常配置不会触发）。
    """
    if raw is None:
        return "unknown"
    raw = str(raw)
    out = []
    for ch in raw:
        if _is_ascii_alnum(ch) or ch in "_-":
            out.append(ch)
        elif ch in ".:@ ":
            out.append("_")
    seg = "".join(out)
    if not seg:
        return "unknown"
    if len(raw) > 64:
        return seg[:40] + "-" + hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]
    return seg


def tls_enabled():
    """是否启用 TLS。已禁止明文，返回 False 时节点拒绝启动。"""
    if MQTT_TLS == "on":
        return True
    if MQTT_TLS == "auto":
        return MQTT_PORT in TLS_PORTS
    return False


def validate_mqtt_config():
    """校验 MQTT 必填配置，返回问题列表；非空表示必须拒绝启动。"""
    problems = []
    if not MQTT_HOST:
        problems.append("MQTT_HOST 未配置：请填写 Broker 地址")
    if not MQTT_USER:
        problems.append("MQTT_USERNAME 未配置：请填写设备自己的 MQTT 账号")
    if not MQTT_PASSWORD:
        problems.append("MQTT_PASSWORD 未配置：请填写设备 MQTT 密码")
    if not MQTT_TENANT:
        problems.append("MQTT_TENANT 未配置：请填写手机 App 登录用户名（两者必须一致）")
    if not 1 <= MQTT_PORT <= 65535:
        problems.append(f"MQTT_PORT 无效（{_MQTT_PORT_RAW!r}）：应为 1-65535，TLS 默认 8883")
    if MQTT_TLS not in ("on", "auto"):
        problems.append(f"MQTT_TLS={MQTT_TLS!r} 不安全：已禁止明文 MQTT，请设置 MQTT_TLS=on")
    elif MQTT_TLS == "auto" and MQTT_PORT not in TLS_PORTS:
        problems.append(
            f"MQTT_TLS=auto 但端口 {MQTT_PORT} 不是 8883/8084，无法确认 TLS；"
            "请设置 MQTT_TLS=on 或改用 8883")
    if MQTT_CA_FILE:
        if not os.path.isfile(MQTT_CA_FILE):
            problems.append(f"MQTT_CA_FILE 不存在或不可读：{MQTT_CA_FILE}")
    else:
        # 留空表示使用系统 CA，这里确认系统信任库可用
        try:
            import ssl
            ssl.create_default_context()
        except Exception as exc:  # noqa: BLE001
            problems.append(f"系统 CA 不可用（{exc}）：请通过 MQTT_CA_FILE 指定 CA 文件")
    return problems


def device_topic_base(tenant=None, device=None):
    """walle/{tenant}/{device}/v1"""
    t = sanitize_segment(MQTT_TENANT if tenant is None else tenant)
    d = sanitize_segment(DEVICE_ID if device is None else device)
    return f"{TOPIC_ROOT}/{t}/{d}/{PROTOCOL_VERSION}"


def device_topic(resource):
    return device_topic_base() + "/" + resource


def tenant_topic(suffix):
    return f"{TOPIC_ROOT}/{sanitize_segment(MQTT_TENANT)}/{PROTOCOL_VERSION}/{suffix}"


# 请求 → 应答 的对应关系（也用于自检，保证命名成对）
REQUEST_TO_RESULT = {
    REQ_DISCOVERY: RES_DISCOVERY_RESULT,
    REQ_STATE: RES_STATE_RESULT,
    REQ_CAPABILITY: RES_CAPABILITY_RESULT,
    REQ_VIDEO_START: RES_VIDEO_START_RESULT,
    REQ_VIDEO_STOP: RES_VIDEO_STOP_RESULT,
    REQ_PHONE_AUDIO_SEND: RES_PHONE_AUDIO_SEND_RESULT,
    REQ_PHONE_AUDIO_STOP: RES_PHONE_AUDIO_STOP_RESULT,
    REQ_CAR_MOTOR_SET: RES_CAR_MOTOR_RESULT,
}

# 请求资源 → 处理函数名
REQUEST_HANDLERS = {
    REQ_STATE: "handle_query_status",
    REQ_CAPABILITY: "handle_capability_query",
    REQ_VIDEO_START: "handle_video_request",
    REQ_VIDEO_STOP: "handle_video_stop",
    REQ_PHONE_AUDIO_SEND: "handle_phone_audio_send",
    REQ_PHONE_AUDIO_STOP: "handle_phone_audio_stop",
    REQ_CAR_MOTOR_SET: "handle_car_motor_set",
}

# 动作类请求：可能阻塞数秒（等首帧 / 推流宽限期 / 音频启动），单独线程池处理，
# 避免占满查询线程（P2-1.4）
ACTION_REQUESTS = frozenset({
    REQ_VIDEO_START, REQ_VIDEO_STOP, REQ_PHONE_AUDIO_SEND, REQ_PHONE_AUDIO_STOP,
})

# 实时请求：处理只做校验 + 发布 /cmd_vel，必须在 MQTT 回调线程内立即执行，
# 不能排进查询/动作队列，否则 200ms 的控制周期会被慢查询拖垮。
REALTIME_REQUESTS = frozenset({REQ_CAR_MOTOR_SET})


def subscriptions():
    """本设备要订阅的主题（只有请求类 + 租户内发现广播）。

    不订 walle/{t}/{d}/v1/#：MQTT 3.1.1 会把客户端自己发布的报文也投递回来，
    订全子树会收到自己发的 state/alarm，白白浪费带宽。
    """
    base = device_topic_base()
    return [
        (f"{base}/{REQ_STATE}", MQTT_QOS),
        (f"{base}/{REQ_CAPABILITY}", MQTT_QOS),
        (f"{base}/{REQ_VIDEO_START}", MQTT_QOS),
        (f"{base}/{REQ_VIDEO_STOP}", MQTT_QOS),
        (f"{base}/{REQ_PHONE_AUDIO_SEND}", MQTT_QOS),
        (f"{base}/{REQ_PHONE_AUDIO_STOP}", MQTT_QOS),
        (f"{base}/{REQ_CAR_MOTOR_SET}", MQTT_QOS),
        (tenant_topic(REQ_DISCOVERY), MQTT_QOS),
    ]


# ---- 监控阈值（均可用环境变量覆盖，写进 /etc/walle-ai.conf 即可）----
MONITOR_INTERVAL = int(os.environ.get("MONITOR_INTERVAL", "5"))          # 采样周期（秒）
# 首次采样提前到 1s 完成，让 state/query 尽快命中缓存（后续仍按 MONITOR_INTERVAL）
STATUS_PRIME_INTERVAL = min(1.0, MONITOR_INTERVAL)
THRESH_CPU = float(os.environ.get("THRESH_CPU", "90.0"))                 # CPU 使用率 %
THRESH_MEM = float(os.environ.get("THRESH_MEM", "90.0"))                 # 内存使用率 %
THRESH_DISK = float(os.environ.get("THRESH_DISK", "90.0"))               # 磁盘使用率 %
THRESH_TEMP = float(os.environ.get("THRESH_TEMP", "80.0"))               # CPU 温度 ℃
# 网络带宽阈值：默认 10 MiB/s，环境变量按 MiB/s 配置
THRESH_NET_BPS = float(os.environ.get("THRESH_NET_MIB", "10")) * 1024 * 1024
RECOVER_RATIO = 0.9                      # 回滞系数：降到阈值的 90% 以下才算恢复，防止边界抖动

# ---- 请求处理并发（P2-1.4：查询/动作分池，限制并发与排队长度）----
QUERY_WORKERS = max(1, int(os.environ.get("MQTT_QUERY_WORKERS", "2")))
ACTION_WORKERS = max(1, int(os.environ.get("MQTT_ACTION_WORKERS", "2")))
MAX_QUERY_BACKLOG = max(0, int(os.environ.get("MQTT_MAX_QUERY_BACKLOG", "16")))
MAX_ACTION_BACKLOG = max(0, int(os.environ.get("MQTT_MAX_ACTION_BACKLOG", "8")))

# ---- 布尔环境变量 ----


def _env_flag(name, default):
    """布尔环境变量：1/true/yes/on 为真，0/false/no/off 为假。"""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# USB 设备扫描路径
USB_SYSFS = Path("/sys/bus/usb/devices")
SKIP_FSTYPE = {"tmpfs", "devtmpfs", "squashfs", "iso9660", "overlay", "aufs", "fuse.portal"}

# ---- 摄像头能力探测（v4l2-ctl，需要 sudo apt install -y v4l-utils）----
CAM_CMD_TIMEOUT = 8                      # 单条 v4l2-ctl 命令超时（秒）
CAM_ENABLE = _env_flag("CAM_ENABLE", True)   # 不需要摄像头能力查询时置 False

# ---- 能力缓存（capability/query：后台探测，查询只读缓存）----
CAPABILITY_CACHE_TTL = float(os.environ.get("CAPABILITY_CACHE_TTL", "120"))  # 缓存有效期（秒）
CAPABILITY_CACHE_ONLY = _env_flag("CAPABILITY_CACHE_ONLY", False)  # True=查询只读缓存，不等待首次探测
CAPABILITY_FIRST_WAIT = float(os.environ.get("CAPABILITY_FIRST_WAIT", "3"))   # 首次查询等待探测的秒数上限

# ---- 视频推流（video/start，ffmpeg -> RTMP）----
VIDEO_ENABLE = _env_flag("VIDEO_ENABLE", True)  # 不需要推流能力时置 False
VIDEO_FFMPEG_BIN = "ffmpeg"              # ffmpeg 可执行文件（不在 PATH 就写绝对路径）
# ---- 媒体服务器（Oryx：视频 RTMP 推流 + 手机音频 WHEP 拉流）----
# 只配服务器地址与推流 secret；端口与路径由协议固定：
#   推流 rtmp://{host}:1935/live/{room}?secret={secret}
#   拉流 https://{host}/rtc/v1/whep/?app=live&stream={stream}
MEDIA_SERVER_HOST = os.environ.get("MEDIA_SERVER_HOST", "").strip()
# Oryx 控制台「推流鉴权」生成的 secret；重置后需同步更新 App 与设备
MEDIA_SERVER_SECRET = os.environ.get("MEDIA_SERVER_SECRET", "").strip()
VIDEO_LOG_FILE = "log/ffmpeg_{time}.log"  # ffmpeg 日志文件（相对路径以进程工作目录为基准）
                                         # {time} 会替换成推流时刻，如 20261029_162650，
                                         # 所以每次推流都是一个独立的日志文件；目录不存在会自动创建
VIDEO_LOG_MAX_BYTES = 20 * 1024 * 1024   # 单个日志超过 20MB 时在下次写入前滚动为 .1 备份
VIDEO_ROOM_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"  # 房间号白名单，防注入/防拼错

# ---- 音频采集（检测到麦克风时自动把默认录音源混入推流）----
VIDEO_AUDIO_ENABLE = _env_flag("VIDEO_AUDIO_ENABLE", True)  # 不需要声音时置 False
VIDEO_AUDIO_DEVICE = "default"           # ffmpeg pulse 输入设备名（PulseAudio 默认录音源）
VIDEO_PACTL_BIN = "pactl"                # pactl 可执行文件（来自 pulseaudio-utils）
VIDEO_AUDIO_PROBE_TIMEOUT = 3            # 麦克风探测超时（秒）
VIDEO_AUDIO_CODEC = "aac"                # 音频编码
VIDEO_AUDIO_BITRATE = "128k"             # 音频码率

# ---- 反向音频（phone/audio/*：手机麦 → WebRTC(WHIP→WHEP) → 本机扬声器）----
PHONE_AUDIO_ENABLE = _env_flag("PHONE_AUDIO_ENABLE", True)  # 不需要时置 False
PHONE_AUDIO_DEVICE = os.environ.get("PHONE_AUDIO_DEVICE", "")   # sounddevice 输出设备名，空=系统默认
PHONE_AUDIO_LATENCY = os.environ.get("PHONE_AUDIO_LATENCY", "low")  # PortAudio 延迟档位：low/high
PHONE_AUDIO_FIRST_FRAME_TIMEOUT = 8.0     # 首帧超时：超时即判定手机端没在推流
PHONE_AUDIO_STARTUP_GRACE = 2.0          # 启动宽限：播放进程活过这段时间即视为开始播放（秒）
PHONE_AUDIO_STREAM_SUFFIX = "_audio"     # 音频流名后缀：与视频房间号错开，避免抢同一路 stream
PHONE_AUDIO_PLAYER_SCRIPT = os.path.join(  # WHEP 播放器脚本（与被监控脚本同目录）
    os.path.dirname(os.path.abspath(__file__)), "whep_audio_player.py")


def build_phone_audio_url(stream):
    """手机音频 WHEP 拉流地址（Oryx 443 固定路径，无需鉴权）。"""
    return f"https://{MEDIA_SERVER_HOST}/rtc/v1/whep/?app=live&stream={stream}"
