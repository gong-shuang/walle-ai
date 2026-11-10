#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""环境文件加载、配置常量、配置校验与运行期依赖检查。"""
import logging
import os
import re
import shutil
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


def _required_mqtt_env_present():
    """必需项是否已由 shell/systemd 注入，避免生产环境 600 配置不可读时误报。"""
    return all(os.environ.get(k) for k in
               ("MQTT_HOST", "MQTT_USERNAME", "MQTT_PASSWORD", "MQTT_TENANT"))


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
                value = value.strip()
                if value and value[0] in "\"'":
                    # 引号内的内容原样保留（可含 #），只取到未转义的配对引号为止
                    quote = value[0]
                    end = -1
                    for idx in range(1, len(value)):
                        if value[idx] == quote and value[idx - 1] != "\\":
                            end = idx
                            break
                    if end != -1:
                        value = value[1:end].replace(f"\\{quote}", quote)
                else:
                    # 未加引号：仅当 # 前面有空白时视为行内注释
                    value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
                if key and key not in os.environ:
                    os.environ[key] = value
        return True
    except OSError as exc:
        if _required_mqtt_env_present():
            print(f"[info] 环境文件 {path} 不可读，使用已注入的环境变量", file=sys.stderr)
        else:
            print(f"[warn] 读取环境文件失败 {path}: {exc}", file=sys.stderr)
            print("       生产环境请安装为 /etc/walle-ai.conf（root:root 600），"
                  "由 systemd EnvironmentFile 注入；开发环境请使用脚本同级 "
                  "walle-ai.conf 或 --env-file 指定可读文件", file=sys.stderr)
        return False


def _init_env():
    """按优先级加载环境文件，返回加载到的路径。"""
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

# ---- 运行环境（prod/debug）----
# 生产环境强制 TLS；仅 debug 允许显式关闭 TLS 做本地联调
WALLE_RUNTIME_ENV = os.environ.get("WALLE_RUNTIME_ENV", "prod").strip().lower()

# ---- MQTT ----
# 必填项，不提供默认值：缺失或非法时拒绝启动（--check 会列出缺失项）
MQTT_HOST = os.environ.get("MQTT_HOST", "").strip()
_MQTT_PORT_RAW = os.environ.get("MQTT_PORT", "").strip()
try:
    MQTT_PORT = int(_MQTT_PORT_RAW) if _MQTT_PORT_RAW else 8883
except ValueError:
    MQTT_PORT = None
MQTT_USER = os.environ.get("MQTT_USERNAME", "").strip()
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "")
MQTT_KEEPALIVE = 60
MQTT_QOS = 1
# TLS：生产强制开启；仅 WALLE_RUNTIME_ENV=debug 且显式 MQTT_TLS=off 才允许明文
MQTT_TLS = os.environ.get("MQTT_TLS", "on").strip().lower()  # on | off（off 仅 debug）
MQTT_CA_FILE = os.environ.get("MQTT_CA_FILE", "").strip()      # 留空则用系统 CA
# MQTT 断连超过该秒数后停止推流/手机音频（App 已无法控制，避免一直占用外设）；0 = 不断流
MQTT_DISCONNECT_STOP_SEC = int(os.environ.get("MQTT_DISCONNECT_STOP_SEC", "60"))

# ---- 主题命名约定（完整主题 = walle/{tenant}/{device}/v1/{资源}）----
#   请求按语义命名：查询 .../query、动作 .../request；
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
#   tenant = MQTT 用户名（清洗后），Broker 用 walle/${username}/# 做 ACL；
#   device = 下面的 DEVICE_ID（跨重启/重装不变）。
#
MQTT_TENANT = os.environ.get("MQTT_TENANT", "").strip()
TOPIC_ROOT = "walle"
# 主题协议版本段：walle/{tenant}/{device}/v1/{resource}
# 升协议（报文结构变了）时新增 v2 段，旧设备继续用 v1，两套主题可以并存。
PROTOCOL_VERSION = "v1"


def validate_config():
    """校验 MQTT 必填配置，返回错误列表；空列表表示通过。"""
    from ld_media import rtmp_url_ready, whep_url_ready
    errors = []
    if WALLE_RUNTIME_ENV not in ("prod", "debug"):
        errors.append("WALLE_RUNTIME_ENV 只能是 prod/debug")
    if not MQTT_HOST:
        errors.append("MQTT_HOST 未配置")
    if MQTT_PORT is None:
        errors.append("MQTT_PORT 未配置或非法（应为 1-65535 的整数）")
    elif not (1 <= MQTT_PORT <= 65535):
        errors.append(f"MQTT_PORT 超出范围（{MQTT_PORT}，应为 1-65535）")
    elif MQTT_PORT == 1883 and WALLE_RUNTIME_ENV == "prod":
        errors.append("生产环境禁止使用明文端口 1883（TLS 请使用 8883）")
    if not MQTT_USER:
        errors.append("MQTT_USERNAME 未配置")
    if not MQTT_PASSWORD:
        errors.append("MQTT_PASSWORD 未配置")
    if not MQTT_TENANT:
        errors.append("MQTT_TENANT 未配置")
    if MQTT_TLS not in ("on", "off"):
        errors.append("MQTT_TLS 只能是 on/off（auto 已移除）")
    elif MQTT_TLS == "off" and WALLE_RUNTIME_ENV != "debug":
        errors.append("MQTT_TLS=off 仅允许在 WALLE_RUNTIME_ENV=debug 下使用")
    if MQTT_CA_FILE and not (os.path.isfile(MQTT_CA_FILE) and os.access(MQTT_CA_FILE, os.R_OK)):
        errors.append(f"MQTT_CA_FILE 不存在或不可读: {MQTT_CA_FILE}")
    if CAMERA_DEVICE and not os.path.exists(CAMERA_DEVICE):
        errors.append(f"CAMERA_DEVICE 不存在: {CAMERA_DEVICE}")
    if VIDEO_ENABLE and VIDEO_RTMP_URL_TEMPLATE:
        if "{room}" not in VIDEO_RTMP_URL_TEMPLATE:
            errors.append("VIDEO_RTMP_URL_TEMPLATE 缺少 {room} 占位符")
        elif "{secret}" in VIDEO_RTMP_URL_TEMPLATE and not WHIP_SECRET:
            errors.append("VIDEO_RTMP_URL_TEMPLATE 含 {secret} 但 WHIP_SECRET 未配置")
    if PHONE_AUDIO_ENABLE and PHONE_AUDIO_WHEP_URL_TEMPLATE:
        ok, reason = whep_url_ready()
        if not ok:
            errors.append(reason)
    return errors


# 设备端软件版本：与 ROS2 共用 walle-device/VERSION，环境变量优先。
# 查找顺序：WALLE_VERSION_FILE > 同目录 VERSION（单独部署时一并拷贝）> 上级目录 VERSION（仓库内）。
# 优先级：环境变量 > VERSION 文件 > 内置默认值；三者都没有时启动/--check 会告警。
VERSION_CODE = 1
VERSION_NAME = "0.1.1"
VERSION_SOURCE = "内置默认值"


def _parse_version_file(path):
    values = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].lstrip()
                key, sep, value = line.partition("=")
                if sep:
                    values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def _load_version():
    """返回 (版本字典, 命中的文件路径)；都没命中时路径为空。"""
    here = Path(__file__).resolve().parent
    for path in (os.environ.get("WALLE_VERSION_FILE", ""),
                 str(here / "VERSION"), str(here.parent / "VERSION")):
        if path and os.path.isfile(path):
            return _parse_version_file(path), path
    return {}, ""


_VERSION_VALUES, _VERSION_FILE = _load_version()
_VERSION_ENV = bool(os.environ.get("WALLE_VERSION_NUM")
                    or os.environ.get("WALLE_VERSION_NAME"))
try:
    VERSION_CODE = int(os.environ.get("WALLE_VERSION_NUM")
                       or _VERSION_VALUES.get("WALLE_VERSION_NUM") or VERSION_CODE)
except ValueError:
    pass
VERSION_NAME = (os.environ.get("WALLE_VERSION_NAME")
                or _VERSION_VALUES.get("WALLE_VERSION_NAME") or VERSION_NAME)
if _VERSION_ENV:
    VERSION_SOURCE = "环境变量"
elif _VERSION_FILE:
    VERSION_SOURCE = f"文件 {_VERSION_FILE}"
else:
    VERSION_SOURCE = "内置默认值（未找到 VERSION 文件）"


# ---- 监控阈值（均可用环境变量覆盖，写进 /etc/walle-ai.conf 即可）----
MONITOR_INTERVAL = int(os.environ.get("MONITOR_INTERVAL", "5"))          # 采样周期（秒）
# 查询（state/capability/discovery）与动作（video/phone audio）分池，避免动作占满查询线程；
# 每个池限制「执行中 + 排队」总量，超限丢弃并计数
QUERY_WORKERS = int(os.environ.get("QUERY_WORKERS", "2"))
ACTION_WORKERS = int(os.environ.get("ACTION_WORKERS", "2"))
QUERY_QUEUE_LIMIT = int(os.environ.get("QUERY_QUEUE_LIMIT", "16"))
ACTION_QUEUE_LIMIT = int(os.environ.get("ACTION_QUEUE_LIMIT", "4"))
# 设备身份缓存刷新周期（秒）：online/LWT 只读缓存，磁盘扫描放到监控线程
IDENTITY_CACHE_TTL = int(os.environ.get("IDENTITY_CACHE_TTL", "60"))
THRESH_CPU = float(os.environ.get("THRESH_CPU", "90.0"))                 # CPU 使用率 %
THRESH_MEM = float(os.environ.get("THRESH_MEM", "90.0"))                 # 内存使用率 %
THRESH_DISK = float(os.environ.get("THRESH_DISK", "90.0"))               # 磁盘使用率 %
THRESH_TEMP = float(os.environ.get("THRESH_TEMP", "80.0"))               # CPU 温度 ℃
# 网络带宽阈值：默认 10 MiB/s，环境变量按 MiB/s 配置
THRESH_NET_BPS = float(os.environ.get("THRESH_NET_MIB", "10")) * 1024 * 1024
RECOVER_RATIO = 0.9                      # 回滞系数：降到阈值的 90% 以下才算恢复，防止边界抖动

# ---- 配置辅助 ----
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
CAMERA_CACHE_TTL = 10.0                  # 摄像头能力缓存（秒）；USB 插拔时立即失效
CAM_ENABLE = _env_flag("CAM_ENABLE", True)   # 不需要摄像头能力查询时置 False
# 设备能力（capability）后台探测：启动即探测，按 TTL 刷新；查询只读缓存
CAPABILITY_CACHE_TTL = float(os.environ.get("CAPABILITY_CACHE_TTL", "120"))
CAPABILITY_FIRST_WAIT = float(os.environ.get("CAPABILITY_FIRST_WAIT", "3"))
CAPABILITY_CACHE_ONLY = _env_flag("CAPABILITY_CACHE_ONLY", False)

# ---- 视频推流（video/start，ffmpeg -> RTMP）----
VIDEO_ENABLE = _env_flag("VIDEO_ENABLE", True)  # 不需要推流能力时置 False
# 可选：固定摄像头设备（如 udev 符号链接 /dev/walle-camera）；留空接受任意 /dev/videoN
CAMERA_DEVICE = os.environ.get("CAMERA_DEVICE", "").strip()
VIDEO_FFMPEG_BIN = "ffmpeg"              # ffmpeg 可执行文件（不在 PATH 就写绝对路径）
# 推流地址模板，必须显式配置：{room}=房间号，{secret}=Oryx 推流 secret
VIDEO_RTMP_URL_TEMPLATE = os.environ.get("VIDEO_RTMP_URL_TEMPLATE", "").strip()
# Oryx 部署后会生成一个推流 secret（每台服务器都不同，在控制台「推流鉴权」可查看/重置），
# 重置后所有旧客户端立即失效。禁止写死在源码里：用 WHIP_SECRET 环境变量
# （/etc/walle-ai.conf）注入；不配置则视频推流会被服务端拒绝。
WHIP_SECRET = os.environ.get("WHIP_SECRET", "").strip()
VIDEO_LOG_FILE = "log/ffmpeg_{time}.log"  # ffmpeg 日志文件（相对路径以进程工作目录为基准）
                                         # {time} 会替换成推流时刻，如 20261029_162650，
                                         # 所以每次推流都是一个独立的日志文件；目录不存在会自动创建
VIDEO_LOG_LEVEL = "info"                 # ffmpeg 日志级别：quiet/panic/fatal/error/warning/info/verbose/debug
                                         # info（默认）= 记录版本、编解码器、输入/输出流信息 + 实时帧统计，
                                         # 和手动跑 ffmpeg 看到的日志一样；只想记错误就改成 "warning"
VIDEO_LOG_MAX_BYTES = 20 * 1024 * 1024   # 单个日志超过 20MB 时在下次写入前滚动为 .1 备份
# 历史日志清理：每次新建推流日志前执行，保留最近 N 天 / 最多 M 个文件 / 总量不超过 X MB
VIDEO_LOG_KEEP_DAYS = int(os.environ.get("VIDEO_LOG_KEEP_DAYS", "7"))
VIDEO_LOG_KEEP_FILES = int(os.environ.get("VIDEO_LOG_KEEP_FILES", "10"))
VIDEO_LOG_MAX_TOTAL_MB = int(os.environ.get("VIDEO_LOG_MAX_TOTAL_MB", "100"))
# 推流最长时长（秒）；到期自动停止并告警，0 = 不限制（App 崩溃/断网时避免一直占用摄像头）
VIDEO_MAX_DURATION_SEC = int(os.environ.get("VIDEO_MAX_DURATION_SEC", "1800"))
VIDEO_VIDEO_SIZE = os.environ.get("VIDEO_VIDEO_SIZE", "640x480").strip()  # 采集分辨率
VIDEO_FRAMERATE = int(os.environ.get("VIDEO_FRAMERATE", "30"))           # 采集帧率
VIDEO_BITRATE = os.environ.get("VIDEO_BITRATE", "1000k")                 # 视频码率
VIDEO_GOP = int(os.environ.get("VIDEO_GOP", "60"))                       # 关键帧间隔
# 编码器：auto（优先硬件 h264_v4l2m2m，否则 libx264）/ libx264 / h264_v4l2m2m
VIDEO_ENCODER = os.environ.get("VIDEO_ENCODER", "auto").strip().lower()
VIDEO_PRESET = os.environ.get("VIDEO_PRESET", "ultrafast")               # libx264 预设
VIDEO_TUNE = os.environ.get("VIDEO_TUNE", "zerolatency")                 # libx264 调优
VIDEO_STARTUP_GRACE = 3.0                # 启动宽限：ffmpeg 活过这段时间即视为推流成功（秒）
VIDEO_ROOM_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"  # 房间号白名单，防注入/防拼错

# ---- 音频采集（检测到麦克风时自动把默认录音源混入推流）----
VIDEO_AUDIO_ENABLE = _env_flag("VIDEO_AUDIO_ENABLE", True)  # 不需要声音时置 False
VIDEO_AUDIO_DEVICE = "default"           # ffmpeg pulse 输入设备名（PulseAudio 默认录音源）
VIDEO_PACTL_BIN = "pactl"                # pactl 可执行文件（来自 pulseaudio-utils）
VIDEO_AUDIO_PROBE_TIMEOUT = 3            # 麦克风探测超时（秒）
VIDEO_AUDIO_CODEC = "aac"                # 音频编码
VIDEO_AUDIO_BITRATE = "128k"             # 音频码率
VIDEO_AUDIO_FRAGMENT_SIZE = 1024         # pulse 采集分片（字节）：越小采集端延迟越低，
                                         # 过大是混音后播放端延迟明显增大的主要原因

# ---- 反向音频（phone/audio/*：手机麦 → WebRTC(WHIP→WHEP) → 本机扬声器）----
PHONE_AUDIO_ENABLE = _env_flag("PHONE_AUDIO_ENABLE", True)  # 不需要时置 False
# 拉流地址模板，必须显式配置：{stream}/{expires}/{token} 由设备填充
PHONE_AUDIO_WHEP_URL_TEMPLATE = os.environ.get("PHONE_AUDIO_WHEP_URL_TEMPLATE", "").strip()
# WHEP 拉流鉴权：与 Nginx secure_link 的 secret 一致（见 doc/service_deploy.md §6.2）
PHONE_AUDIO_WHEP_SECRET = os.environ.get("PHONE_AUDIO_WHEP_SECRET", "").strip()
PHONE_AUDIO_WHEP_PATH = os.environ.get("PHONE_AUDIO_WHEP_PATH", "/whep-audio/").strip()
try:
    PHONE_AUDIO_WHEP_TOKEN_TTL = int(os.environ.get("PHONE_AUDIO_WHEP_TOKEN_TTL", "300"))
except ValueError:
    PHONE_AUDIO_WHEP_TOKEN_TTL = 0
PHONE_AUDIO_DEVICE = os.environ.get("PHONE_AUDIO_DEVICE", "")   # sounddevice 输出设备名，空=系统默认
PHONE_AUDIO_LATENCY = os.environ.get("PHONE_AUDIO_LATENCY", "low")  # PortAudio 延迟档位：low/high
# 首帧超时（秒）：超过即判定手机端没在推流（播放器以退出码 4 退出）；弱网可调大
PHONE_AUDIO_FIRST_FRAME_TIMEOUT = float(
    os.environ.get("PHONE_AUDIO_FIRST_FRAME_TIMEOUT", "8.0"))
# 启动宽限（秒）：在首帧超时之外再等这么久，留给播放器写完错误日志并退出
PHONE_AUDIO_STARTUP_GRACE = float(
    os.environ.get("PHONE_AUDIO_STARTUP_GRACE", "2.0"))
# 无音频帧超时（秒）：超过即退出播放器（退出码 7），0 = 关闭
PHONE_AUDIO_IDLE_TIMEOUT = float(os.environ.get("PHONE_AUDIO_IDLE_TIMEOUT", "30"))
# 最长播放时长（秒）：到期由主程序停止并告警，0 = 不限制
PHONE_AUDIO_MAX_DURATION_SEC = int(os.environ.get("PHONE_AUDIO_MAX_DURATION_SEC", "1800"))
PHONE_AUDIO_STREAM_SUFFIX = "_audio"     # 音频流名后缀：与视频房间号错开，避免抢同一路 stream
PHONE_AUDIO_PLAYER_SCRIPT = os.path.join(  # WHEP 播放器脚本（与被监控脚本同目录）
    os.path.dirname(os.path.abspath(__file__)), "whep_audio_player.py")
# 播放器退出码 → 可读原因（与 whep_audio_player.py 顶部说明一致）
PHONE_AUDIO_EXIT_REASONS = {
    3: "缺少依赖（aiortc / sounddevice / libportaudio2）",
    4: "首帧超时（手机未推流或网络不通）",
    5: "WHEP 协商失败（拉流地址、token 或服务端异常）",
    6: "声卡打开失败（检查 libportaudio2 与音频会话）",
    7: "长时间无音频帧（手机停止推流或网络中断）",
}


def media_config_warnings():
    """音视频模板未配置时的告警；不阻塞仅使用监控功能的设备启动。"""
    from ld_media import rtmp_url_ready, whep_url_ready
    warnings = []
    if VIDEO_ENABLE:
        ok, reason = rtmp_url_ready()
        if not ok:
            warnings.append(reason + "；视频推流请求将返回 -2")
    if PHONE_AUDIO_ENABLE:
        ok, reason = whep_url_ready()
        if not ok:
            warnings.append(reason + "；手机音频请求将返回 -2")
    return warnings


def dependency_status():
    """外部依赖检查：返回 [{name, path, ok, impact}]，只告警不阻塞启动。"""
    here = os.path.dirname(os.path.abspath(__file__))
    items = [
        ("ffmpeg", VIDEO_FFMPEG_BIN, "视频推流不可用"),
        ("v4l2-ctl", "v4l2-ctl", "摄像头能力探测不可用"),
        ("pactl", VIDEO_PACTL_BIN, "音频采集源探测不可用"),
    ]
    status = []
    for name, exe, impact in items:
        path = shutil.which(exe)
        status.append({"name": name, "path": path, "ok": bool(path), "impact": impact})
    player = os.path.join(here, "whep_audio_player.py")
    player_ok = os.path.isfile(player)
    status.append({
        "name": "whep_audio_player.py",
        "path": player if player_ok else None,
        "ok": player_ok,
        "impact": "手机麦上行不可用",
    })
    return status


def dependency_warnings():
    return [f"未找到 {item['name']}（{item['impact']}）"
            for item in dependency_status() if not item["ok"]]


def audio_session_hint():
    """检测用户音频会话是否可用；返回可读提示，正常/未启用音视频时返回 None。"""
    from ld_audio import _pactl_list_names
    if not (VIDEO_ENABLE or PHONE_AUDIO_ENABLE):
        return None
    if not shutil.which(VIDEO_PACTL_BIN):
        return None          # 已在依赖检查里提示，避免重复
    names, reason = _pactl_list_names("sources")
    if names is not None:
        return None
    hint = f"音频会话不可用：{reason}"
    if not os.environ.get("XDG_RUNTIME_DIR"):
        hint += "；当前进程没有 XDG_RUNTIME_DIR，系统服务请按 README「音频会话」配置"
    return hint
