#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RaspberryPi 系统监控 Agent（基于 paho-mqtt 2.x）

功能：
1. 周期巡检，异常实时上报（USB 插拔 / CPU / 内存 / 磁盘 / 带宽 / 温度），带状态机：
   越限才报，恢复后发 recover 事件，不会重复刷屏。
2. 响应设备身份查询、状态查询、远程命令、摄像头能力查询、推流/停止推流。
3. 上线/下线：retained + 遗嘱（LWT），App 重启或重连后能立刻看到设备在线状态。

协议：多租户主题 v1
    所有主题都在 walle/{tenant}/{device}/v1/ 下展开，租户 = MQTT 用户名（清洗后），
    device 用稳定的 device_id（MAC + CPU 核心数 + 内存容量 的 md5 前 16 位）。

    请求（App -> 设备）                          应答（设备 -> App）                      说明
    ------------------------------------------------------------------------------------------------
    —                                          .../online                          主动上报（retained + LWT）
    —                                          .../alarm                           主动上报（告警 / USB）
    walle/{t}/discovery/query                   .../discovery/result                 设备发现（租户级广播）
    .../state/query                             .../state/result                     状态快照
    .../cmd/exec                                .../cmd/result                       远程命令（执行动作）
    .../capability/query                      .../capability/result                  设备能力（摄像头/麦克风/音响等外设）
    .../video/start/request                     .../video/start/result               开始推流（动作）
    .../video/stop/request                      .../video/stop/result                停止推流（动作）
    .../phone/audio/send                        .../phone/audio/send/result            手机麦上行·开（App 发起）
    .../phone/audio/stop                        .../phone/audio/stop/result            手机麦上行·关（App 发起）

    命名约定：请求按语义划分 —— 查询 /query、命令 /exec、动作 /request；
    应答一律 /result，且只在收到对应请求后才发布，只有 online 与 alarm 是主动上报。

    除 discovery 广播外，设备只订阅自己子树下的「请求类」主题，
    不订通配子树（避免收到自己发布的上报报文）。

安全：所有请求报文必须带匹配的 uuid（本次运行实例标识），否则静默忽略；
      设备连接用独立 MQTT 账号，Broker 侧按 walle/${clientid}/# 授权（见 emqx/acl.conf.example）。

依赖：pip install -r requirements.txt  （paho-mqtt>=2.0、psutil）
     摄像头能力探测需 v4l-utils，推流需 ffmpeg。

配置：全部通过环境变量；前台运行时会自动加载 /etc/walle-ai.env，
      查找顺序 --env-file > $RPI_MONITOR_ENV > /etc/walle-ai.env > ./walle-ai.env，
      已存在的 shell 变量优先。必填项见 README「首次部署」。

命令行：
    python3 main.py                         前台运行（自动读 /etc/walle-ai.env）
    python3 main.py --check                 只打印解析后的配置与订阅主题，不连 broker
    python3 main.py --env-file PATH         使用指定配置文件
    python3 main.py --help                  查看本说明

部署：见 linux/README.md「首次部署」（systemd 单元为 walle-ai.service）
"""

import hashlib
import json
import logging
import os
import platform
import random
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import paho.mqtt.client as mqtt
import psutil


# ============================== 启动参数 / 环境文件 ==============================
# 目的：既能被 systemd 用 EnvironmentFile= 拉起，也能直接 `python3 main.py`
# 前台运行（自动读取 /etc/walle-ai.env），两者共用同一份配置。
#
# 优先级：已存在的环境变量（shell / systemd） > 环境文件里的值。
# 文件查找顺序：--env-file 指定 > $RPI_MONITOR_ENV > /etc/walle-ai.env > ./walle-ai.env

ENV_CANDIDATES = ("/etc/walle-ai.env", "walle-ai.env")
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
        print(f"[warn] 读取环境文件失败 {path}: {exc}", file=sys.stderr)
        print("       若是 Permission denied：该文件默认 600 root:root，"
              "请执行 sudo chown $(whoami) " + path + "，或改用 sudo 运行", file=sys.stderr)
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

# ---- MQTT ----
# 既保留原来的默认值，也支持用环境变量覆盖（systemd 里配 Environment= 即可，避免口令写进代码）
MQTT_HOST = os.environ.get("MQTT_HOST", "www.baidong.xyz")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USERNAME", "RaspPi5")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "123456")
MQTT_KEEPALIVE = 60
MQTT_QOS = 1
# TLS：8883 走 ssl://，1883 走明文（与 App 的端口/TLS 开关对应）
MQTT_TLS = os.environ.get("MQTT_TLS", "auto").strip().lower()  # auto | on | off
MQTT_CA_FILE = os.environ.get("MQTT_CA_FILE", "").strip()      # 留空则用系统 CA

# ---- 主题命名约定（完整主题 = walle/{tenant}/{device}/v1/{资源}）----
#   请求按语义命名：查询 .../query、命令 .../exec、动作 .../request；
#   应答统一 .../result，且**只在收到对应请求后发布**，绝不主动上报；
#   主动上报只有 online（上线/下线）与 alarm（告警/USB 插拔）两个。
# ---- 应答（设备 -> App）----
RES_DISCOVERY_RESULT = "discovery/result"           # 收到 discovery/query 后发布（设备身份详情）
RES_STATE_RESULT = "state/result"                   # 收到 state/query 后发布（状态快照）
RES_CMD_RESULT = "cmd/result"                       # 收到 cmd/exec 后发布
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
REQ_CMD = "cmd/exec"
REQ_CAPABILITY = "capability/query"
REQ_VIDEO_START = "video/start/request"
REQ_VIDEO_STOP = "video/stop/request"
REQ_PHONE_AUDIO_SEND = "phone/audio/send"     # 手机麦上行：App 开始往 {room}_audio 推音频
REQ_PHONE_AUDIO_STOP = "phone/audio/stop"     # 手机麦上行：App 停止推音频

# ---- 设备标识 ----
HOSTNAME = socket.gethostname()
# device_id：设备唯一标识，由「MAC + CPU 核心数 + 内存总容量」md5 取 16 位生成，
#             只要不换网卡/CPU/内存就永远不变（重装系统、换 IP、重启都不影响），见 generate_device_id()
# uuid：程序实例标识，由「MAC + IP + 启动时间」生成，本次运行期间不变，下次运行会变
DEVICE_ID = ""      # 在 RpiMonitor.__init__ 中计算
DEVICE_UUID = ""    # 在 RpiMonitor.__init__ 中计算

# ---- 多租户主题（命名规则与 Android 端 TopicNames 完全一致）----
# 主题形如 walle/{tenant}/{device}/v1/{resource}：
#   tenant = MQTT 用户名（清洗后），Broker 用 walle/${username}/# 做 ACL；
#   device = 下面的 DEVICE_ID（跨重启/重装不变）。
#
MQTT_TENANT = os.environ.get("MQTT_TENANT", MQTT_USER).strip()
TOPIC_ROOT = "walle"
# 主题协议版本段：walle/{tenant}/{device}/v1/{resource}
# 升协议（报文结构变了）时新增 v2 段，旧设备继续用 v1，两套主题可以并存。
PROTOCOL_VERSION = "v1"


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
    REQ_CMD: RES_CMD_RESULT,
    REQ_CAPABILITY: RES_CAPABILITY_RESULT,
    REQ_VIDEO_START: RES_VIDEO_START_RESULT,
    REQ_VIDEO_STOP: RES_VIDEO_STOP_RESULT,
    REQ_PHONE_AUDIO_SEND: RES_PHONE_AUDIO_SEND_RESULT,
    REQ_PHONE_AUDIO_STOP: RES_PHONE_AUDIO_STOP_RESULT,
}

# 请求资源 → 处理函数名
REQUEST_HANDLERS = {
    REQ_STATE: "handle_query_status",
    REQ_CMD: "handle_cmd_exec",
    REQ_CAPABILITY: "handle_capability_query",
    REQ_VIDEO_START: "handle_video_request",
    REQ_VIDEO_STOP: "handle_video_stop",
    REQ_PHONE_AUDIO_SEND: "handle_phone_audio_send",
    REQ_PHONE_AUDIO_STOP: "handle_phone_audio_stop",
}


def subscriptions():
    """本设备要订阅的主题（只有请求类 + 租户内发现广播）。

    不订 walle/{t}/{d}/v1/#：MQTT 3.1.1 会把客户端自己发布的报文也投递回来，
    订全子树会收到自己发的 state/alarm，白白浪费带宽。
    """
    base = device_topic_base()
    return [
        (f"{base}/{REQ_STATE}", MQTT_QOS),
        (f"{base}/{REQ_CMD}", MQTT_QOS),
        (f"{base}/{REQ_CAPABILITY}", MQTT_QOS),
        (f"{base}/{REQ_VIDEO_START}", MQTT_QOS),
        (f"{base}/{REQ_VIDEO_STOP}", MQTT_QOS),
        (f"{base}/{REQ_PHONE_AUDIO_SEND}", MQTT_QOS),
        (f"{base}/{REQ_PHONE_AUDIO_STOP}", MQTT_QOS),
        (tenant_topic(REQ_DISCOVERY), MQTT_QOS),
    ]

# ---- 监控阈值（均可用环境变量覆盖，写进 /etc/walle-ai.env 即可）----
MONITOR_INTERVAL = int(os.environ.get("MONITOR_INTERVAL", "5"))          # 采样周期（秒）
THRESH_CPU = float(os.environ.get("THRESH_CPU", "90.0"))                 # CPU 使用率 %
THRESH_MEM = float(os.environ.get("THRESH_MEM", "90.0"))                 # 内存使用率 %
THRESH_DISK = float(os.environ.get("THRESH_DISK", "90.0"))               # 磁盘使用率 %
THRESH_TEMP = float(os.environ.get("THRESH_TEMP", "80.0"))               # CPU 温度 ℃
# 网络带宽阈值：默认 10 MiB/s，环境变量按 MiB/s 配置
THRESH_NET_BPS = float(os.environ.get("THRESH_NET_MIB", "10")) * 1024 * 1024
RECOVER_RATIO = 0.9                      # 回滞系数：降到阈值的 90% 以下才算恢复，防止边界抖动

# ---- 远程命令（注意安全！）----
def _env_flag(name, default):
    """布尔环境变量：1/true/yes/on 为真，0/false/no/off 为假。"""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


ENABLE_CMD_EXEC = _env_flag("ENABLE_CMD_EXEC", True)
CMD_TIMEOUT_DEFAULT = 30                 # 命令默认超时（秒）
CMD_TIMEOUT_MAX = 300                    # 命令最大超时（秒）
CMD_OUTPUT_LIMIT = 64 * 1024             # stdout/stderr 最大回传字节（超出截断）
# 命令白名单：为空表示允许任意命令；填写后只允许前缀匹配，例如：
# CMD_ALLOWLIST = ["ls", "uptime", "df -h", "free -m", "vcgencmd measure_temp"]
CMD_ALLOWLIST = []

# USB 设备扫描路径
USB_SYSFS = Path("/sys/bus/usb/devices")
SKIP_FSTYPE = {"tmpfs", "devtmpfs", "squashfs", "iso9660", "overlay", "aufs", "fuse.portal"}

# ---- 摄像头能力探测（v4l2-ctl，需要 sudo apt install -y v4l-utils）----
CAM_CMD_TIMEOUT = 8                      # 单条 v4l2-ctl 命令超时（秒）
CAM_ENABLE = _env_flag("CAM_ENABLE", True)   # 不需要摄像头能力查询时置 False

# ---- 视频推流（video/start，ffmpeg -> RTMP）----
VIDEO_ENABLE = _env_flag("VIDEO_ENABLE", True)  # 不需要推流能力时置 False
VIDEO_FFMPEG_BIN = "ffmpeg"              # ffmpeg 可执行文件（不在 PATH 就写绝对路径）
VIDEO_RTMP_URL_TEMPLATE = os.environ.get(  # 推流地址模板：{room}=房间号，{secret}=Oryx 推流 secret
    "VIDEO_RTMP_URL_TEMPLATE",
    "rtmp://www.baidong.xyz:1935/live/{room}?secret={secret}")
# Oryx 部署后会生成一个推流 secret（每台服务器都不同，在控制台「推流鉴权」可查看/重置），
# 重置后所有旧客户端立即失效。禁止写死在源码里：用 WHIP_SECRET 环境变量
# （/etc/walle-ai.env）注入；不配置则视频推流会被服务端拒绝。
WHIP_SECRET = os.environ.get("WHIP_SECRET", "").strip()
VIDEO_LOG_FILE = "log/ffmpeg_{time}.log"  # ffmpeg 日志文件（相对路径以进程工作目录为基准）
                                         # {time} 会替换成推流时刻，如 20261029_162650，
                                         # 所以每次推流都是一个独立的日志文件；目录不存在会自动创建
VIDEO_LOG_LEVEL = "info"                 # ffmpeg 日志级别：quiet/panic/fatal/error/warning/info/verbose/debug
                                         # info（默认）= 记录版本、编解码器、输入/输出流信息 + 实时帧统计，
                                         # 和手动跑 ffmpeg 看到的日志一样；只想记错误就改成 "warning"
VIDEO_LOG_MAX_BYTES = 20 * 1024 * 1024   # 单个日志超过 20MB 时在下次写入前滚动为 .1 备份
VIDEO_VIDEO_SIZE = "640x480"             # 采集分辨率
VIDEO_FRAMERATE = 30                     # 采集帧率
VIDEO_BITRATE = "1000k"                  # 视频码率
VIDEO_GOP = 60                           # 关键帧间隔
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
PHONE_AUDIO_WHEP_URL_TEMPLATE = os.environ.get(    # 拉流地址模板，{stream} 会被替换成流名
    "PHONE_AUDIO_WHEP_URL_TEMPLATE",
    "https://www.baidong.xyz:443/rtc/v1/whep/?app=live&stream={stream}")
PHONE_AUDIO_DEVICE = os.environ.get("PHONE_AUDIO_DEVICE", "")   # sounddevice 输出设备名，空=系统默认
PHONE_AUDIO_LATENCY = os.environ.get("PHONE_AUDIO_LATENCY", "low")  # PortAudio 延迟档位：low/high
PHONE_AUDIO_FIRST_FRAME_TIMEOUT = 8.0     # 首帧超时：超时即判定手机端没在推流
PHONE_AUDIO_STARTUP_GRACE = 2.0          # 启动宽限：播放进程活过这段时间即视为开始播放（秒）
PHONE_AUDIO_STREAM_SUFFIX = "_audio"     # 音频流名后缀：与视频房间号错开，避免抢同一路 stream
PHONE_AUDIO_PLAYER_SCRIPT = os.path.join(  # WHEP 播放器脚本（与被监控脚本同目录）
    os.path.dirname(os.path.abspath(__file__)), "whep_audio_player.py")


# ============================== 工具函数 ==============================

def now_ts():
    return int(time.time())


def now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def human_size(num):
    """字节数转人类可读字符串，如 1.5 GB"""
    if num is None:
        return None
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024 or unit == "TB":
            return f"{num:.1f} {unit}"
        num /= 1024.0


def human_rate(bps):
    """字节/秒转人类可读速率，如 10.5 MB/s"""
    if bps is None:
        return None
    return human_size(bps) + "/s"


def get_lan_ip():
    """获取本机局域网 IP（不会真正发包）；链路本地地址 169.254.x 视为无效并回退"""
    ip = None
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
    except OSError:
        ip = None
    finally:
        sock.close()
    if ip and not ip.startswith(("127.", "169.254.")):
        return ip
    # 回退：遍历网卡找第一个有效 IPv4
    try:
        for name, entries in psutil.net_if_addrs().items():
            if name == "lo":
                continue
            for a in entries:
                if (a.family == socket.AF_INET
                        and not a.address.startswith(("127.", "169.254."))):
                    return a.address
    except OSError:
        pass
    return ip or "127.0.0.1"


def get_pi_model():
    """读取树莓派型号，如 Raspberry Pi 5 Model B Rev 1.0"""
    p = Path("/proc/device-tree/model")
    if p.exists():
        try:
            return p.read_text(encoding="utf-8", errors="replace").strip("\x00").strip()
        except OSError:
            pass
    return None


def get_os_pretty_name():
    """读取发行版完整名称，如 Debian GNU/Linux 12 (bookworm)"""
    p = Path("/etc/os-release")
    if p.exists():
        try:
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
        except OSError:
            pass
    return f"{platform.system()} {platform.release()}"


def get_cpu_model():
    """读取 CPU 型号：优先 model name / cpu model，树莓派取 Model 行"""
    p = Path("/proc/cpuinfo")
    if p.exists():
        try:
            candidates = {}
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                key, value = key.strip().lower(), value.strip()
                if key in ("model name", "cpu model", "hardware", "model") and value:
                    candidates.setdefault(key, value)
            for key in ("model name", "cpu model", "hardware", "model"):
                if candidates.get(key):
                    return candidates[key]
        except OSError:
            pass
    return platform.processor() or None


def get_device_serial():
    """读取设备序列号（树莓派每块板唯一，是区分设备最可靠的标识）"""
    for path in ("/proc/device-tree/serial-number",
                 "/sys/firmware/devicetree/base/serial-number"):
        p = Path(path)
        if p.exists():
            try:
                return p.read_text(encoding="utf-8", errors="replace").strip("\x00").strip()
            except OSError:
                pass
    p = Path("/proc/cpuinfo")
    if p.exists():
        try:
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.lower().startswith("serial"):
                    return line.split(":", 1)[-1].strip()
        except OSError:
            pass
    return None


def get_net_interfaces():
    """各网卡的 IP / MAC / 状态（局域网内识别设备用）"""
    result = []
    try:
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
    except OSError:
        return result
    af_link = getattr(psutil, "AF_LINK", None)
    for name, entries in addrs.items():
        if name == "lo":
            continue
        ip = mac = None
        for a in entries:
            if a.family == socket.AF_INET and not a.address.startswith("127."):
                ip = ip or a.address
            elif af_link is not None and a.family == af_link:
                mac = mac or a.address
        if not ip and not mac:
            continue
        st = stats.get(name)
        result.append({
            "name": name,
            "ip": ip,
            "mac": mac,
            "is_up": bool(st.isup) if st else None,
            "speed_mbps": st.speed if st else None,
        })
    return result


def human_duration(seconds):
    """秒数转可读时长，如 3天4小时5分6秒"""
    seconds = max(int(seconds), 0)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}天")
    if hours:
        parts.append(f"{hours}小时")
    if minutes:
        parts.append(f"{minutes}分")
    parts.append(f"{secs}秒")
    return "".join(parts)


def pick_primary_mac(ip=None):
    """取主网卡 MAC：优先与指定 IP 同网卡的，其次任意有效 MAC"""
    interfaces = get_net_interfaces()
    if ip:
        for i in interfaces:
            if i["ip"] == ip and i["mac"]:
                return i["mac"]
    for i in interfaces:
        if i["mac"]:
            return i["mac"]
    return "00:00:00:00:00:00"


def get_cpu_core_count():
    """CPU 核心数（优先物理核，取不到则用逻辑核），用于 device_id 生成"""
    try:
        cores = psutil.cpu_count(logical=False)
        if not cores:
            cores = psutil.cpu_count(logical=True)
        return int(cores or 0)
    except Exception:  # noqa: BLE001
        return 0


def generate_device_id(mac, cpu_cores, mem_total_bytes):
    """
    生成设备唯一标识 device_id：MAC + CPU 核心数 + 内存总容量 → md5 → 取前 16 位十六进制。
    这三个量都是硬件属性，所以只要不更换网卡/CPU/内存，device_id 永远不变；
    换 IP、改主机名、重装系统、重启进程都不会改变它。
    """
    raw = f"{mac}|{cpu_cores}|{mem_total_bytes}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def generate_device_uuid(mac, ip, ts=None):
    """
    根据 MAC + IP + 启动时间生成网络内唯一的 uuid。
    用 uuid5（命名空间 + 名字哈希），同样输入必得同样结果，便于排查复现。
    """
    ts = int(ts if ts is not None else time.time())
    name = f"{mac}|{ip}|{ts}"
    return str(uuid.uuid5(uuid.NAMESPACE_OID, name))


def get_cpu_temp():
    """读取 CPU 温度（℃），优先读 thermal_zone0，再尝试 psutil"""
    zone = Path("/sys/class/thermal/thermal_zone0/temp")
    if zone.exists():
        try:
            return round(int(zone.read_text().strip()) / 1000.0, 1)
        except (OSError, ValueError):
            pass
    try:
        temps = psutil.sensors_temperatures()
        for key in ("cpu-thermal", "cpu_thermal", "thermal_zone0"):
            if temps.get(key):
                return round(temps[key][0].current, 1)
        for entries in temps.values():
            if entries:
                return round(entries[0].current, 1)
    except (AttributeError, OSError):
        pass
    return None


def scan_usb_devices():
    """
    扫描当前 USB 设备列表。
    返回 {sysfs名: 设备信息}，如 {"1-1.2": {...}}。
    只读取 sysfs，不依赖 lsusb / pyudev。
    """
    devices = {}
    if not USB_SYSFS.exists():
        return devices
    for dev_path in sorted(USB_SYSFS.iterdir()):
        if not dev_path.is_dir() or ":" in dev_path.name:
            # 带冒号的是接口（如 1-1.2:1.0），跳过；只看设备节点
            continue

        def read(name):
            try:
                return (dev_path / name).read_text().strip()
            except OSError:
                return None

        vid = read("idVendor")
        if not vid:
            # 没有 idVendor 的条目忽略（通常是控制器占位节点）
            continue
        # 顺便找出该 USB 设备下挂的块设备节点（如 U 盘 /dev/sda1）
        block_nodes = []
        try:
            block_nodes = sorted({f"/dev/{p.name}" for p in dev_path.glob("**/block/*")})
        except OSError:
            pass
        devices[dev_path.name] = {
            "sysfs": dev_path.name,
            "vendor_id": vid,
            "product_id": read("idProduct"),
            "manufacturer": read("manufacturer"),
            "product": read("product"),
            "serial": read("serial"),
            "busnum": read("busnum"),
            "devnum": read("devnum"),
            "version": read("version"),
            "block_nodes": block_nodes,
        }
    return devices


def net_snapshot():
    """各网卡（排除 lo）的 (累计接收字节, 累计发送字节)"""
    return {
        name: (c.bytes_recv, c.bytes_sent)
        for name, c in psutil.net_io_counters(pernic=True).items()
        if name != "lo"
    }


def calc_net_rates(prev, elapsed):
    """根据两次快照计算每个网卡及总速率"""
    cur = net_snapshot()
    per_iface = {}
    total_rx = total_tx = 0
    for name, (rx, tx) in cur.items():
        prx, ptx = prev.get(name, (rx, tx))
        rx_rate = max(rx - prx, 0) / elapsed
        tx_rate = max(tx - ptx, 0) / elapsed
        per_iface[name] = {
            "rx_bytes_per_sec": round(rx_rate, 1),
            "tx_bytes_per_sec": round(tx_rate, 1),
            "rx_rate": human_rate(rx_rate),
            "tx_rate": human_rate(tx_rate),
            "rx_total_bytes": rx,
            "tx_total_bytes": tx,
        }
        total_rx += rx_rate
        total_tx += tx_rate
    return cur, per_iface, total_rx, total_tx


def disk_mounts():
    """所有真实磁盘挂载点的使用情况"""
    mounts = []
    for part in psutil.disk_partitions(all=False):
        if part.fstype in SKIP_FSTYPE or part.mountpoint.startswith(("/snap/", "/proc", "/sys")):
            continue
        try:
            u = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        mounts.append({
            "mount": part.mountpoint,
            "device": part.device,
            "fstype": part.fstype,
            "total_bytes": u.total,
            "used_bytes": u.used,
            "free_bytes": u.free,
            "percent": u.percent,
            "total_human": human_size(u.total),
            "used_human": human_size(u.used),
            "free_human": human_size(u.free),
        })
    return mounts


# ==================== 摄像头能力探测（v4l2-ctl） ====================

def _v4l2(args, timeout=CAM_CMD_TIMEOUT):
    """执行一条 v4l2-ctl 命令，返回 CompletedProcess；超时返回 None"""
    return subprocess.run(["v4l2-ctl"] + args, capture_output=True,
                          text=True, timeout=timeout)


def _parse_v4l2_devices(out):
    """
    解析 `v4l2-ctl --list-devices` 输出，得到 {节点: {"name":卡片名, "detail":驱动+总线}}。
    形如：
        USB 2.0 Camera: USB Camera (usb-xhci-hcd.1-1):
            /dev/video0
            /dev/video1
    卡片名取第一个冒号前的部分（即摄像头友好名），冒号后为驱动与总线信息。
    """
    names = {}
    current = None
    for line in out.splitlines():
        if not line.strip():
            current = None
            continue
        if not line[0].isspace():
            head = line.strip().rstrip(":").strip()
            card, _, detail = head.partition(":")
            current = {"name": card.strip() or head, "detail": detail.strip() or None}
        else:
            for node in re.findall(r"/dev/video\d+", line):
                if current:
                    names[node] = current
    return names


def _parse_formats(stdout):
    """解析 `--list-formats-ext` 输出，返回 [{"code","desc","resolutions":[...]}]"""
    formats = []
    curr_fmt = None
    curr_res = None
    for line in stdout.splitlines():
        line = line.strip()
        fmt_match = re.search(r"\[\d+\]: '(\w+)' \((.*?)\)", line)
        if fmt_match:
            curr_fmt = {"code": fmt_match.group(1),
                        "desc": fmt_match.group(2),
                        "resolutions": []}
            formats.append(curr_fmt)
            curr_res = None
            continue
        res_match = re.search(r"Size: Discrete (\d+)x(\d+)", line)
        if res_match and curr_fmt is not None:
            w, h = int(res_match.group(1)), int(res_match.group(2))
            curr_res = {"res": f"{w}x{h}", "width": w, "height": h, "fps": []}
            curr_fmt["resolutions"].append(curr_res)
            continue
        fps_match = re.search(r"Interval: Discrete [\d\.]+s \((\d+\.?\d*) fps\)", line)
        if fps_match and curr_res is not None:
            fps = float(fps_match.group(1))
            if fps not in curr_res["fps"]:
                curr_res["fps"].append(fps)
    # 帧率排序，方便下游直接展示
    for f in formats:
        for r in f["resolutions"]:
            r["fps"].sort()
    return formats


def list_cameras():
    """
    枚举当前设备支持的摄像头视频采集能力。

    判定规则（与已验证的脚本一致）：v4l2-ctl --list-devices 拿到所有 /dev/videoN，
    逐个 --list-formats-ext，returncode==0 且输出含 'Size: Discrete' 才算有效摄像头
    节点（过滤掉纯元数据节点）。

    返回 (cameras, error)：
      cameras: [{"node","name","formats":[{code,desc,resolutions[{res,width,height,fps}]}],
                 "format_count","max_resolution","resolution_count"}]
      error:   None 或 描述字符串（如 v4l2-ctl 未安装）
    """
    try:
        proc = _v4l2(["--list-devices"])
    except FileNotFoundError:
        return [], "未安装 v4l2-ctl，请执行: sudo apt install -y v4l-utils"
    except subprocess.TimeoutExpired:
        return [], "v4l2-ctl --list-devices 执行超时"
    except Exception as e:  # noqa: BLE001
        return [], f"调用 v4l2-ctl 失败: {e}"

    if proc.returncode != 0:
        return [], f"v4l2-ctl --list-devices 返回 {proc.returncode}: {(proc.stderr or '').strip()[:200]}"

    names = _parse_v4l2_devices(proc.stdout or "")
    nodes = list(dict.fromkeys(re.findall(r"/dev/video\d+", proc.stdout or "")))

    cameras = []
    for node in nodes:
        try:
            res = _v4l2(["-d", node, "--list-formats-ext"])
        except subprocess.TimeoutExpired:
            logging.warning("探测 %s 格式超时，跳过", node)
            continue
        except Exception as e:  # noqa: BLE001
            logging.warning("探测 %s 格式失败: %s", node, e)
            continue
        # 只有能列出离散分辨率、且命令成功的节点才是真正的视频采集节点
        if res.returncode != 0 or "Size: Discrete" not in (res.stdout or ""):
            continue

        formats = _parse_formats(res.stdout)
        resolutions = [r["res"] for f in formats for r in f["resolutions"]]
        max_res = ""
        if resolutions:
            max_res = max(
                resolutions,
                key=lambda x: (int(x.split("x")[0]) * int(x.split("x")[1])),
            )
        cameras.append({
            "node": node,
            "name": (names.get(node) or {}).get("name"),
            "detail": (names.get(node) or {}).get("detail"),
            "format_count": len(formats),
            "resolution_count": len(resolutions),
            "max_resolution": max_res or None,
            "formats": formats,
        })

    return cameras, None


# ==================== 视频推流（ffmpeg -> RTMP） ====================

def _pactl_list_names(kind):
    """
    `pactl list short sources|sinks` 的名称列。

    返回 (names, reason)：成功时 names 为 [name, ...]（可能为空列表），
    pactl 未安装/PulseAudio 未运行/超时等失败时 names=None、reason 为原因。
    """
    try:
        proc = subprocess.run(
            [VIDEO_PACTL_BIN, "list", "short", kind],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=VIDEO_AUDIO_PROBE_TIMEOUT,
        )
    except FileNotFoundError:
        return None, "未安装 pactl（sudo apt install -y pulseaudio-utils）"
    except subprocess.TimeoutExpired:
        return None, f"pactl 探测超时（>{VIDEO_AUDIO_PROBE_TIMEOUT}s）"
    except Exception as e:  # noqa: BLE001
        return None, f"调用 pactl 失败: {e}"

    if proc.returncode != 0:
        detail = (proc.stderr or b"").decode("utf-8", "replace").strip()[:200]
        return None, f"pactl 返回 {proc.returncode}（PulseAudio 未运行？）{detail}"

    names = []
    for line in (proc.stdout or b"").decode("utf-8", "replace").splitlines():
        # 输出形如：0<TAB>alsa_input.usb-xxx<TAB>module-alsa-card.c<TAB>s16le ...
        cols = line.split("\t")
        if len(cols) > 1 and cols[1].strip():
            names.append(cols[1].strip())
    return names, None


def _pactl_value(args):
    """执行一条 pactl 查询命令（get-default-sink 等），成功返回 stdout 文本，失败返回 None"""
    try:
        proc = subprocess.run(
            [VIDEO_PACTL_BIN] + args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=VIDEO_AUDIO_PROBE_TIMEOUT,
        )
        if proc.returncode != 0:
            return None
        return (proc.stdout or b"").decode("utf-8", "replace").strip() or None
    except Exception:  # noqa: BLE001
        return None


def _pulse_has_input_source():
    """
    用 pactl 判断 PulseAudio 上是否存在真实录音源（排除 *.monitor 回环源）。
    （*.monitor 是输出设备把播放内容回环出来的虚拟源，不是麦克风。）

    返回 (state, reason)：
      state=True  有录音源
      state=False 没有录音源
      state=None  无法判断（pactl 未安装 / PulseAudio 未运行等），reason 为原因
    """
    names, reason = _pactl_list_names("sources")
    if names is None:
        return None, reason
    if any(not n.endswith(".monitor") for n in names):
        return True, None
    return False, "未检测到录音源（没有麦克风，或只有 *.monitor 回环源）"


def _ffmpeg_audio_probe(device):
    """pactl 不可用时，用 ffmpeg 试读 0.2 秒，验证 `-f pulse` 录音通路确实可用"""
    try:
        proc = subprocess.run(
            [VIDEO_FFMPEG_BIN, "-hide_banner", "-loglevel", "error",
             "-f", "pulse", "-i", device, "-t", "0.2", "-f", "null", "-"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=VIDEO_AUDIO_PROBE_TIMEOUT + 3,
        )
    except FileNotFoundError:
        return False, "未安装 ffmpeg，请执行: sudo apt install -y ffmpeg"
    except subprocess.TimeoutExpired:
        return False, "ffmpeg 音频探测超时"
    except Exception as e:  # noqa: BLE001
        return False, f"ffmpeg 音频探测失败: {e}"

    if proc.returncode == 0:
        return True, None
    tail = (proc.stdout or b"").decode("utf-8", "replace").strip().splitlines()[-1:]
    return False, "ffmpeg 无法读取默认录音源" + (f": {tail[0][:200]}" if tail else "")


def detect_microphone():
    """
    判断本机是否有可用麦克风，决定推流是否带声音。

    判定顺序：
      1) VIDEO_AUDIO_ENABLE=False → 直接纯视频；
      2) pactl 有真实录音源（排除 *.monitor）→ 用默认录音源；
      3) pactl 不可用/未运行（精简系统常见）→ 退化为 ffmpeg 试读一次。

    返回 {"available","device","reason","codec","bitrate"}，
    该结构会原样回填到 video/start/result 的 audio 字段，便于 App/日志排查。
    """
    info = {
        "available": False,
        "device": None,
        "reason": None,
        "codec": VIDEO_AUDIO_CODEC,
        "bitrate": VIDEO_AUDIO_BITRATE,
    }

    if not VIDEO_AUDIO_ENABLE:
        info["reason"] = "音频采集已关闭（VIDEO_AUDIO_ENABLE=False）"
        return info

    state, reason = _pulse_has_input_source()
    if state is None:
        ok, ff_reason = _ffmpeg_audio_probe(VIDEO_AUDIO_DEVICE)
        if not ok:
            info["reason"] = f"{reason}；{ff_reason}"
            return info
    elif state is False:
        info["reason"] = reason
        return info

    info["available"] = True
    info["device"] = VIDEO_AUDIO_DEVICE
    return info


def probe_audio_capture():
    """
    麦克风（录音）能力，供 capability/result 上报。

    可用性判定复用 detect_microphone（含 VIDEO_AUDIO_ENABLE 开关与 ffmpeg 兜底），
    附带 pactl 列出的真实录音源清单（排除 *.monitor）。
    返回 {"available","device","sources","reason"}，任何一步失败都降级为
    available=False + reason，绝不抛异常。
    """
    base = detect_microphone()
    info = {
        "available": base["available"],
        "device": base["device"],
        "sources": [],
        "reason": base["reason"],
    }
    names, _ = _pactl_list_names("sources")
    if names:
        info["sources"] = [n for n in names if not n.endswith(".monitor")]
    return info


def probe_audio_playback():
    """
    音响/扬声器（播放）能力，供 capability/result 上报。

    返回 {"available","device","volume_percent","muted","sinks","reason"}：
      available:       是否存在音频输出设备
      device:          默认输出 sink 名（pactl get-default-sink，老版本没有该命令则取第一个）
      volume_percent:  默认输出音量（0-150+），未知为 None
      muted:           默认输出是否静音，未知为 None
      sinks:           全部输出 sink 名清单
      reason:          不可用原因（pactl 缺失 / 无输出设备）
    任何一步失败都降级，绝不抛异常；音量/静音拿不到时保留 None 而不是报错。
    """
    info = {
        "available": False,
        "device": None,
        "volume_percent": None,
        "muted": None,
        "sinks": [],
        "reason": None,
    }
    names, reason = _pactl_list_names("sinks")
    if names is None:
        info["reason"] = reason
        return info
    if not names:
        info["reason"] = "未检测到音频输出设备（无扬声器/声卡）"
        return info

    info["available"] = True
    info["sinks"] = names
    info["device"] = _pactl_value(["get-default-sink"]) or names[0]

    volume = _pactl_value(["get-sink-volume", info["device"]])
    if volume:
        m = re.search(r"(\d+)%", volume)
        if m:
            info["volume_percent"] = int(m.group(1))

    mute = _pactl_value(["get-sink-mute", info["device"]])
    if mute:
        lowered = mute.lower()
        info["muted"] = ("yes" in lowered) or ("是" in mute)
    return info


def build_ffmpeg_cmd(video_device, room_number, audio=None):
    """
    按收到的请求动态拼 ffmpeg 参数（视频设备、房间号都不写死）。
    用参数列表而不是 shell 字符串，避免命令注入。

    audio: detect_microphone() 的返回值；available=True 时追加 PulseAudio 默认
    录音源（-f pulse -i default）并编码为 AAC，否则保持 -an 纯视频。
    """
    cmd = [
        VIDEO_FFMPEG_BIN,
        # 不加 -hide_banner：日志里保留 ffmpeg 版本/编译选项/流信息，便于排查
        "-loglevel", VIDEO_LOG_LEVEL,
        "-f", "v4l2",
        "-thread_queue_size", "8192",
        "-video_size", VIDEO_VIDEO_SIZE,
        "-framerate", str(VIDEO_FRAMERATE),
        "-i", video_device,
    ]

    has_audio = bool(audio and audio.get("available"))
    if has_audio:
        # 音频输入必须紧跟视频输入之后、编码参数之前，且单独给足队列；
        # fragment_size 限制 PulseAudio 每次攒的采样量，直接决定采集端缓冲时长
        cmd += [
            "-thread_queue_size", "8192",
            "-f", "pulse",
            "-fragment_size", str(VIDEO_AUDIO_FRAGMENT_SIZE),
            "-i", audio.get("device") or VIDEO_AUDIO_DEVICE,
        ]

    cmd += [
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-pix_fmt", "yuv420p",
        "-g", str(VIDEO_GOP),
        "-b:v", VIDEO_BITRATE,
    ]

    if has_audio:
        cmd += [
            "-c:a", VIDEO_AUDIO_CODEC,
            "-b:a", VIDEO_AUDIO_BITRATE,
        ]
    else:
        cmd += ["-an"]

    # 低延迟封装参数（混音流验证过；纯视频流保持原参数不动）：
    #   -vsync passthrough        时间戳直通，不为凑 CFR 复制/丢弃帧
    #   -max_interleave_delta 0   不缓存交错，音视频随到随发（音画等待是延迟主因）
    #   -muxdelay 0.1 -muxpreload 0  最小化 flv 复用器的起始缓冲
    #   -rtmp_live live           声明直播流，rtmp 发送不攒包
    if has_audio:
        cmd += [
            "-vsync", "passthrough",
            "-max_interleave_delta", "0",
            "-muxdelay", "0.1",
            "-muxpreload", "0",
            "-rtmp_live", "live",
        ]

    cmd += [
        "-f", "flv",
        VIDEO_RTMP_URL_TEMPLATE.format(room=room_number, secret=WHIP_SECRET),
    ]
    return cmd


def build_rtmp_url(room_number):
    return VIDEO_RTMP_URL_TEMPLATE.format(room=room_number, secret=WHIP_SECRET)


def rtmp_secret_ready():
    """模板里还留着 {secret} 占位符时，必须配置了 WHIP_SECRET 才能推流。"""
    return "{secret}" not in VIDEO_RTMP_URL_TEMPLATE or bool(WHIP_SECRET)


def video_log_path():
    """解析 ffmpeg 日志文件路径：支持 {time} 占位符，相对路径按当前工作目录解析"""
    name = VIDEO_LOG_FILE.replace("{time}", time.strftime("%Y%m%d_%H%M%S"))
    return os.path.abspath(os.path.expanduser(name))


class _LogSink:
    """
    ffmpeg 实时输出的落地目标：
      - 由独立线程持续把 ffmpeg 的 stdout/stderr 写进日志文件（管道始终有人读，不会塞满阻塞 ffmpeg）
      - 单个文件超过 VIDEO_LOG_MAX_BYTES 时自动滚动为 .1 再续写，避免日志无限变大
      - 内存里保留最后 8KB，用于失败时快速给出原因
    """

    def __init__(self, path, header=b"", max_bytes=VIDEO_LOG_MAX_BYTES, keep_tail=8192):
        self.path = path
        self.header = header or b""
        self.max_bytes = max_bytes
        self.keep_tail = keep_tail
        self.fallback_error = None
        self.lock = threading.Lock()
        self.buf = bytearray()
        self.size = 0
        self.written = 0          # 本次推流累计写入字节数（含滚动后的新文件）
        self.rotations = 0
        self.fh = None
        self._open()

    def _open(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self.fh = open(self.path, "ab")
        if self.header:
            self.fh.write(self.header)
            self.fh.flush()
        self.size = self.fh.tell()

    def write(self, data):
        if not data:
            return
        with self.lock:
            self.buf.extend(data)
            if len(self.buf) > self.keep_tail:
                del self.buf[:len(self.buf) - self.keep_tail]
            self.written += len(data)
            if self.fh is None:
                return
            try:
                # 先判断是否已到上限再写，保证每条日志都完整落在某一个文件里
                if self.max_bytes and self.size >= self.max_bytes:
                    self._rotate()
                if self.fh is None:
                    return
                self.fh.write(data)
                self.fh.flush()
                self.size += len(data)
            except Exception as e:  # noqa: BLE001
                self.fallback_error = str(e)

    def _rotate(self):
        """当前文件超限：改名成 .1，重新开一个新文件继续写（ffmpeg 写的是管道，不受影响）"""
        try:
            self.fh.close()
        except Exception:
            pass
        try:
            os.replace(self.path, self.path + ".1")
        except Exception as e:  # noqa: BLE001
            self.fallback_error = f"日志滚动失败: {e}"
        try:
            self.fh = None
            self._open()
            self.rotations += 1
            note = (f"===== {now_str()} 日志超过 {self.max_bytes} 字节，"
                    f"已滚动为 {os.path.basename(self.path)}.1，继续记录 =====\n")
            self.fh.write(note.encode("utf-8"))
            self.fh.flush()
            self.size = self.fh.tell()
            logging.warning("ffmpeg 日志超过上限，已滚动: %s -> %s.1", self.path, self.path)
        except Exception as e:  # noqa: BLE001
            self.fallback_error = f"日志滚动后重开失败: {e}"

    def tail(self, limit=400):
        return bytes(self.buf).decode("utf-8", "replace").strip()[-limit:]

    _ERROR_HINTS = ("error", "cannot", "invalid", "no such", "failed", "failure",
                    "not found", "denied", "busy", "refused", "unable", "unsupported")

    def error_line(self, limit=400):
        """
        取“最像原因”的那一行：日志级别是 info 时文件开头有整段 banner，
        直接取尾部容易被版本信息淹没，所以优先挑含错误关键词的最后一行。
        """
        text = bytes(self.buf).decode("utf-8", "replace")
        lines = [ln.strip() for ln in text.replace("\r", "\n").splitlines() if ln.strip()]
        picked = [ln for ln in lines
                  if any(k in ln.lower() for k in self._ERROR_HINTS)]
        if picked:
            return picked[-1][:limit]
        return text.strip()[-limit:]

    def close(self):
        with self.lock:
            try:
                if self.fh:
                    self.fh.close()
            except Exception:
                pass
            self.fh = None


def make_log_sink(header=b""):
    """按配置创建日志落地点；配置路径不可写时退回临时文件，保证推流本身不受影响"""
    path = video_log_path()
    try:
        return _LogSink(path, header)
    except Exception as e:  # noqa: BLE001
        logging.warning("无法写入 ffmpeg 日志文件 %s（%s），改用临时文件", path, e)
        fd, tmp = tempfile.mkstemp(prefix="ffmpeg_", suffix=".log")
        os.close(fd)
        sink = _LogSink(tmp, header)
        sink.fallback_error = str(e)
        return sink


def mask_rtmp_url(url):
    """日志/报文里隐藏 secret 参数，避免密钥外泄到日志"""
    return re.sub(r"(secret=)[^&]+", r"\1***", url)


# ============================== 主类 ==============================

class RpiMonitor:
    def __init__(self):
        self.client = None
        self.stop_event = threading.Event()
        # 命令/查询单独走线程池，避免长命令阻塞 MQTT 收发线程
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="mqtt-worker")
        # psutil 的 CPU 采样在进程内共享上一次时间点，多线程采样需加锁
        self.sample_lock = threading.Lock()
        self.active_alarms = set()
        self.usb_state = scan_usb_devices()
        self.lan_ip = get_lan_ip()
        self.mac = pick_primary_mac(self.lan_ip)
        # device_id：设备唯一标识（MAC + CPU 核心数 + 内存容量），跨重启/跨系统不变
        self.cpu_cores = get_cpu_core_count()
        self.mem_total = psutil.virtual_memory().total
        global DEVICE_ID, DEVICE_UUID
        DEVICE_ID = generate_device_id(self.mac, self.cpu_cores, self.mem_total)
        logging.info("设备 device_id: %s（MAC=%s, CPU核心数=%s, 内存总容量=%s字节）",
                     DEVICE_ID, self.mac, self.cpu_cores, self.mem_total)
        # uuid：程序实例标识（MAC + IP + 启动时间），本次运行不变、下次运行变化
        DEVICE_UUID = generate_device_uuid(self.mac, self.lan_ip)
        logging.info("设备 UUID: %s（MAC=%s, IP=%s, 启动时间=%s）",
                     DEVICE_UUID, self.mac, self.lan_ip, now_str())
        # 当前推流进程（同一时刻只保留一路推流）
        self.video_proc = None
        self.video_lock = threading.Lock()
        self.video_meta = {}
        # 反向音频：手机麦上行后在本地播放的 ffmpeg 进程（与视频互不影响）
        self.audio_proc = None
        self.audio_lock = threading.Lock()
        self.audio_meta = {}
        # 上一次应答 discovery 的时间（单调时钟），用于限频
        self._last_discovery_at = 0.0

    def _will_message(self):
        """遗嘱消息：掉线时由 broker 代发 offline 到 walle/{t}/{d}/v1/online。"""
        return device_topic(RES_ONLINE), self.build_online_payload(False)

    # ---------- MQTT 回调 ----------

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            logging.info("MQTT 连接成功: %s:%s（tenant=%s device=%s）",
                         MQTT_HOST, MQTT_PORT, sanitize_segment(MQTT_TENANT),
                         sanitize_segment(DEVICE_ID))
            subs = subscriptions()
            client.subscribe(subs)
            # 上线通知（retained；掉线时由遗嘱发 offline）
            self.publish_json(RES_ONLINE, self.build_online_payload(True), retain=True)
            logging.info("已订阅 %d 个主题；当前 USB 设备 %d 个作为基线，不产生历史告警",
                         len(subs), len(self.usb_state))
            for t, _ in subs:
                logging.info("  · %s", t)
        else:
            logging.error("MQTT 连接失败，原因码: %s", reason_code)

    def on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        logging.warning("MQTT 连接断开（reason_code=%s），paho 将自动重连", reason_code)

    def on_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8", errors="replace")
        except Exception:
            payload = ""
        logging.info("收到消息 %s: %s", msg.topic, payload[:500])
        if msg.topic.startswith(TOPIC_ROOT + "/"):
            self._handle_request(msg.topic, payload)
        else:
            logging.warning("忽略非本协议主题: %s", msg.topic)

    # ---------- 发布封装 ----------

    @staticmethod
    def _safe_json(payload):
        """把报文解析成 dict；空/非法 JSON 返回 {}。"""
        if not payload or not str(payload).strip():
            return {}
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _rid(data):
        """取请求里的 request_id（统一字符串；缺省为空串）。"""
        return str((data or {}).get("request_id") or "")

    def _video_req(self, data, video_device, room_number):
        """推流请求的归一化副本：用于构造应答报文，必须把 request_id 一并带上。"""
        return {
            "video_device": video_device,
            "room_number": room_number,
            "request_id": self._rid(data),
        }

    def publish_json(self, resource, data, retain=False):
        """按资源名发布到 walle/{tenant}/{device}/v1/{resource}。

        参数 resource 用模块顶部的 RES_* 常量，例如 RES_ALARM / RES_CMD_RESULT。
        """
        self._publish_one(device_topic(resource), data, retain)

    def _publish_one(self, topic, data, retain=False):
        info = self.client.publish(
            topic, json.dumps(data, ensure_ascii=False), qos=MQTT_QOS, retain=retain
        )
        logging.debug("publish -> %s mid=%s", topic, info.mid)

    def _handle_request(self, topic, payload):
        """把请求分发到对应处理函数。

        注意两种主题层级不同：
          · 设备级请求：walle/{tenant}/{device}/v1/{resource}   （5 段）
          · 发现广播  ：walle/{tenant}/v1/discovery/query         （4 段，没有 device 段）
        早期实现统一按 4 段切分，导致 discovery 被解析成 device="discovery"、
        resource="query"，匹配不到处理函数而被静默忽略 —— 设备永远不会应答发现。
        """
        if topic == tenant_topic(REQ_DISCOVERY):
            self.pool.submit(self.handle_device_query, payload)
            return

        parts = topic.split("/", 4)
        if len(parts) < 5 or parts[3] != PROTOCOL_VERSION:
            logging.warning("忽略无法解析的主题: %s", topic)
            return
        resource = parts[4]
        handler_name = REQUEST_HANDLERS.get(resource)
        if handler_name:
            self.pool.submit(getattr(self, handler_name), payload)
        else:
            logging.debug("忽略非请求类主题: %s", topic)

    def _event(self, event, level, metric, alarm_type, message, detail=None):
        """统一告警报文封装"""
        payload = {
            "device_id": DEVICE_ID,
            "uuid": DEVICE_UUID,
            "hostname": HOSTNAME,
            "ip": self.lan_ip,
            "event": event,           # alarm / recover / usb_connect / usb_disconnect
            "level": level,           # info / warning / critical
            "metric": metric,         # cpu / memory / disk / network / temperature / usb
            "alarm_type": alarm_type, # 稳定的告警类型代码，方便服务端分类
            "message": message,       # 直接可展示的中文描述
            "timestamp": now_ts(),
            "datetime": now_str(),
        }
        if detail:
            payload["detail"] = detail
        self.publish_json(RES_ALARM, payload)
        logging.warning("[%s] %s %s", level, alarm_type, message)

    # ---------- 告警状态机（带回滞） ----------

    def check_threshold(self, key, value, threshold, metric, alarm_type,
                        unit, label, extra=None):
        detail = {"value": round(value, 2), "unit": unit, "threshold": threshold}
        if extra:
            detail.update(extra)
        if value >= threshold:
            if key not in self.active_alarms:
                self.active_alarms.add(key)
                self._event(
                    "alarm", "warning", metric, alarm_type,
                    f"{label}当前 {value:.1f}{unit}，已达到/超过阈值 {threshold:g}{unit}",
                    detail,
                )
        elif key in self.active_alarms and value < threshold * RECOVER_RATIO:
            self.active_alarms.discard(key)
            self._event(
                "recover", "info", metric, alarm_type,
                f"{label}已恢复，当前 {value:.1f}{unit}（阈值 {threshold:g}{unit}）",
                detail,
            )

    def check_usb(self):
        current = scan_usb_devices()
        added = sorted(set(current) - set(self.usb_state))
        removed = sorted(set(self.usb_state) - set(current))
        for key in added:
            self._event("usb_connect", "info", "usb", "usb_connect",
                        f"USB 设备接入：{current[key].get('product') or key}",
                        {"device": current[key], "current_count": len(current)})
        for key in removed:
            old = self.usb_state[key]
            self._event("usb_disconnect", "warning", "usb", "usb_disconnect",
                        f"USB 设备拔出：{old.get('product') or key}",
                        {"device": old, "current_count": len(current)})
        self.usb_state = current
        return current

    # ---------- identity 设备身份信息 ----------

    def build_device_info(self):
        """组装设备身份信息：型号、主机名、IP、系统版本、内核、运行时长等
        （不含 interfaces / mqtt / monitor，保持报文精简）"""
        vm = psutil.virtual_memory()
        boot = psutil.boot_time()
        uptime = int(time.time() - boot)
        try:
            freq = psutil.cpu_freq()
        except (OSError, RuntimeError, SystemError):
            freq = None
        mac = self.mac
        mounts = disk_mounts()
        disk_total = sum(m["total_bytes"] for m in mounts)
        model = get_pi_model()

        return {
            "device_id": DEVICE_ID,
            "uuid": DEVICE_UUID,
            "device_type": ("raspberrypi"
                            if model and "raspberry pi" in model.lower()
                            else "embedded-linux"),
            "vendor": "Raspberry Pi Foundation" if model else None,
            "model": model,
            "serial": get_device_serial(),
            "hostname": HOSTNAME,
            "ip": self.lan_ip,
            "mac": mac,
            "uuid_source": {                      # uuid 生成依据，便于服务端核对
                "mac": mac,
                "ip": self.lan_ip,
                "note": "uuid5(MAC|IP|启动时间)，每次重启会变化，请用 discovery/query 获取最新值",
            },
            "os": {
                "name": get_os_pretty_name(),        # 系统版本，如 Debian GNU/Linux 12 (bookworm)
                "kernel": platform.release(),        # 内核版本，如 6.6.51+rpt-rpi-2712
                "kernel_build": platform.version(),  # 内核编译信息
                "arch": platform.machine(),          # aarch64 / armv7l
                "python": platform.python_version(),
            },
            "cpu": {
                "model": get_cpu_model(),
                "cores_logical": psutil.cpu_count(logical=True),
                "cores_physical": psutil.cpu_count(logical=False),
                "frequency_mhz": round(freq.current, 1) if freq else None,
                "temperature_c": get_cpu_temp(),
            },
            "memory_total_bytes": vm.total,
            "memory_total_human": human_size(vm.total),
            "disk_total_bytes": disk_total,
            "disk_total_human": human_size(disk_total),
            "uptime_seconds": uptime,
            "uptime_human": human_duration(uptime),
            "boot_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(boot)),
            "timestamp": now_ts(),
            "datetime": now_str(),
        }

    def build_online_payload(self, online=True):
        """
        online 报文：内容与 identity 保持一致（同一份 build_device_info），
        只额外加一个 online 布尔字段区分上线/下线。
        """
        info = self.build_device_info()
        payload = {
            "device_id": info["device_id"],
            "uuid": info["uuid"],
            "online": bool(online),
        }
        payload.update({k: v for k, v in info.items() if k not in ("device_id", "uuid")})
        return payload

    def handle_device_query(self, payload):
        rid = self._rid(self._safe_json(payload))
        # 限频：一台设备在 DISCOVERY_MIN_INTERVAL_SEC 内只应答一次，避免多台手机重复扫描
        now = time.monotonic()
        # getattr 兜底：自检脚本等场景会用未走 __init__ 的实例。
        # 0 表示「还没应答过」——不能用 now - 0 比较，否则刚开机的机器
        # （monotonic < 2s）会把第一条查询丢掉。
        last = getattr(self, "_last_discovery_at", 0.0)
        if last > 0 and now - last < DISCOVERY_MIN_INTERVAL_SEC:
            logging.info("忽略重复的设备身份查询（%.1fs 内已应答过，request_id=%s）",
                         now - last, rid or "-")
            return
        self._last_discovery_at = now
        # 抖动：N 台设备同时收到广播时错开回包，避免瞬间灌满 App 与 broker
        if DISCOVERY_JITTER_SEC > 0:
            time.sleep(random.uniform(0, DISCOVERY_JITTER_SEC))
        try:
            info = self.build_device_info()
            info["request_id"] = rid
            self.publish_json(RES_DISCOVERY_RESULT, info)
            logging.info("已响应设备身份查询 -> %s（request_id=%s，型号=%s 序列号=%s 主机名=%s）",
                         device_topic(RES_DISCOVERY_RESULT), rid or "-", info.get("model"),
                         info.get("serial"), info.get("hostname"))
        except Exception:
            logging.exception("上报 %s 失败", device_topic(RES_DISCOVERY_RESULT))

    # ---------- capability/query 设备能力查询（摄像头/麦克风/音响等外设） ----------

    def build_capability(self):
        """采集本机外设能力（摄像头 + 麦克风 + 音响），生成 capability/result 报文"""
        t0 = time.time()
        cameras, cam_error = ([], "摄像头能力探测已关闭（CAM_ENABLE=False）")
        if CAM_ENABLE:
            cameras, cam_error = list_cameras()
        capture = probe_audio_capture()
        playback = probe_audio_playback()
        cost_ms = int((time.time() - t0) * 1000)

        total_formats = sum(c["format_count"] for c in cameras)
        total_resolutions = sum(c["resolution_count"] for c in cameras)

        # 能力清单如实反映探测结果：App 按它显隐功能入口
        capabilities = ["system_status", "remote_cmd"]
        if cameras:
            capabilities.append("camera_video_capture")
        if VIDEO_ENABLE:
            capabilities.append("rtmp_push")
        if capture["available"]:
            capabilities.append("audio_capture")
        if playback["available"]:
            capabilities.append("audio_playback")

        return {
            "device_id": DEVICE_ID,
            "uuid": DEVICE_UUID,
            "hostname": HOSTNAME,
            "ip": self.lan_ip,
            "capability": "device",
            "capabilities": capabilities,
            "camera_count": len(cameras),
            "cameras": cameras,
            "audio": {
                "capture": capture,
                "playback": playback,
            },
            "summary": {
                "total_cameras": len(cameras),
                "total_formats": total_formats,
                "total_resolutions": total_resolutions,
                "audio_sources": len(capture["sources"]),
                "audio_sinks": len(playback["sinks"]),
                "probe_cost_ms": cost_ms,
            },
            "probe": {
                "tool": "v4l2-ctl + pactl",
                "error": cam_error,          # None 表示摄像头探测正常
                "audio_error": capture["reason"] or playback["reason"],
            },
            "timestamp": now_ts(),
            "datetime": now_str(),
        }

    def handle_capability_query(self, payload):
        # 该主题必须校验 uuid，不匹配/格式非法直接忽略
        ok, data = self.verify_uuid(payload, REQ_CAPABILITY)
        if not ok:
            return
        rid = self._rid(data)
        try:
            result = self.build_capability()
            result["request_id"] = rid
            self.publish_json(RES_CAPABILITY_RESULT, result)
            audio = result["audio"]
            logging.info("已响应能力查询 -> %s（request_id=%s，摄像头 %d 个，麦克风%s，音响%s，耗时 %dms%s）",
                         device_topic(RES_CAPABILITY_RESULT), rid or "-", result["camera_count"],
                         "" if audio["capture"]["available"] else "：无",
                         "" if audio["playback"]["available"] else "：无",
                         result["summary"]["probe_cost_ms"],
                         f"，探测异常: {result['probe']['error']}" if result["probe"]["error"] else "")
        except Exception:
            logging.exception("上报 capability/result 失败")

    # ---------- video/start 推流 ----------

    def _publish_video_result(self, req, result_code, cause, extra=None):
        """统一推流结果报文：result 固定语义 -1/0/-2，细节放附加字段"""
        payload = {
            "device_id": DEVICE_ID,
            "uuid": DEVICE_UUID,
            "hostname": HOSTNAME,
            "ip": self.lan_ip,
            "video_device": req.get("video_device"),
            "room_number": req.get("room_number"),
            "request_id": self._rid(req),
            "result": result_code,
            "cause": cause,
            "timestamp": now_ts(),
            "datetime": now_str(),
        }
        if extra:
            payload.update(extra)
        self.publish_json(RES_VIDEO_START_RESULT, payload)
        logging.info("推流结果 -> %s: request_id=%s result=%s cause=%s%s",
                     device_topic(RES_VIDEO_START_RESULT), payload["request_id"] or "-",
                     result_code, cause,
                     f"（{extra['error']}）" if extra and extra.get("error") else "")
        return payload

    def _stop_video_stream(self, reason=""):
        """停掉当前推流（如果有），返回是否真的停了一个在跑的进程"""
        with self.video_lock:
            proc = self.video_proc
            meta = self.video_meta
            self.video_proc = None
            self.video_meta = {}
        if not proc or proc.poll() is not None:
            return False
        # 标记为主动停止，看护线程据此只记 info 日志，不报"异常退出"
        proc._intentional_stop = True
        try:
            proc.terminate()  # SIGTERM，ffmpeg 会自己收尾
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        except Exception:
            logging.exception("停止推流进程失败")
        logging.info("已停止推流%s（room=%s, pid=%s）",
                     f"：{reason}" if reason else "", meta.get("room_number"), meta.get("pid"))
        return True

    @staticmethod
    def _log_pump(proc, sink):
        """
        把 ffmpeg 的实时输出持续写进日志文件，直到管道关闭。
        按块读而不是按行读：ffmpeg 的帧统计（frame= ... fps= ...）是用 \r 原地刷新的，
        按行读会一直等不到换行，日志里就看不到实时进度。
        """
        try:
            fd = proc.stdout.fileno()
            while True:
                chunk = os.read(fd, 4096)   # 阻塞到有数据，有多少写多少
                if not chunk:
                    break
                sink.write(chunk)
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass
            sink.close()

    def _video_watch(self, proc, room_number, sink):
        """推流进程看护：退出后清理状态并记录原因"""
        try:
            code = proc.wait()
        finally:
            tail = sink.tail()
            sink.close()
            with self.video_lock:
                if self.video_proc is proc:
                    self.video_proc = None
                    self.video_meta = {}
        if getattr(proc, "_intentional_stop", False):
            logging.info("推流已按要求停止（room=%s, exit=%s）", room_number, code)
        elif code == 0:
            logging.info("推流进程已退出（room=%s, exit=0）", room_number)
        else:
            logging.warning("推流进程异常退出（room=%s, exit=%s）%s",
                            room_number, code, f"，stderr: {tail}" if tail else "")

    def _start_video_stream(self, video_device, room_number, audio=None):
        """
        启动 ffmpeg 推流。返回 (proc, error)：
          - proc 不为 None：启动宽限期内未退出，视为推流成功
          - proc 为 None：error 为失败原因

        audio: detect_microphone() 的结果，决定是否混入音频（见 build_ffmpeg_cmd）。
        """
        cmd = build_ffmpeg_cmd(video_device, room_number, audio)
        rtmp = build_rtmp_url(room_number)
        # 日志文件开头写一段分隔头，记录这次推流的时间/房间号/设备/实际命令
        header = (
            f"\n===== {now_str()} 启动推流 room={room_number} device={video_device} =====\n"
            f"$ {' '.join(cmd).replace(rtmp, mask_rtmp_url(rtmp))}\n"
        ).encode("utf-8", "replace")
        sink = make_log_sink(header)
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,          # 由泵线程实时转存到日志文件
                stderr=subprocess.STDOUT,        # ffmpeg 的日志本来就走 stderr，合并后一起收
                bufsize=0,                       # 不缓冲，泵线程读到多少就写多少
                start_new_session=True,          # 独立进程组，避免 Ctrl+C 连带影响
            )
        except FileNotFoundError:
            sink.close()
            return None, "未安装 ffmpeg，请执行: sudo apt install -y ffmpeg"
        except Exception as e:  # noqa: BLE001
            sink.close()
            return None, f"启动 ffmpeg 失败: {e}"

        threading.Thread(target=self._log_pump, args=(proc, sink),
                         daemon=True, name="video-log").start()
        logging.info("已启动推流: %s（日志: %s）", mask_rtmp_url(rtmp), sink.path)

        # 宽限期内就退出，说明参数/设备/网络有问题，直接判失败
        try:
            code = proc.wait(timeout=VIDEO_STARTUP_GRACE)
        except subprocess.TimeoutExpired:
            code = None
        if code is not None:
            time.sleep(0.2)          # 给泵线程一点时间把最后的输出写完
            tail = sink.error_line()
            msg = f"ffmpeg 退出码 {code}（日志: {sink.path}）"
            if sink.fallback_error:
                msg += f"（日志文件不可写: {sink.fallback_error}）"
            sink.close()
            return None, msg + (f": {tail}" if tail else "")

        with self.video_lock:
            self.video_proc = proc
            self.video_meta = {
                "room_number": room_number,
                "video_device": video_device,
                "pid": proc.pid,
                "started_at": now_str(),
                "started_ts": time.time(),
                "rtmp_url": mask_rtmp_url(build_rtmp_url(room_number)),
                "audio": audio or {"available": False, "reason": None},
            }
        threading.Thread(target=self._video_watch, args=(proc, room_number, sink),
                         daemon=True, name="video-watch").start()
        return proc, None

    def handle_video_request(self, payload):
        # 该主题必须校验 uuid
        ok, data = self.verify_uuid(payload, REQ_VIDEO_START)
        if not ok:
            return

        video_device = str(data.get("video_device") or "").strip()
        room_number = str(data.get("room_number") or "").strip()
        # 注意：这里曾重建 dict 时漏掉 request_id，导致应答回显成空串、App 严格配对后丢弃结果
        req = self._video_req(data, video_device, room_number)

        if not VIDEO_ENABLE:
            self._publish_video_result(req, -2, "命令执行失败",
                                       {"error": "推流能力已关闭（VIDEO_ENABLE=False）"})
            return

        # 1) 摄像头设备是否存在
        if not re.match(r"^/dev/video\d+$", video_device) or not os.path.exists(video_device):
            self._publish_video_result(
                req, -1, "没有此摄像头设备",
                {"error": f"设备节点不存在: {video_device or '<空>'}"})
            return

        # 2) 房间号白名单（要拼进 RTMP URL，必须严格校验）
        if not re.match(VIDEO_ROOM_PATTERN, room_number):
            self._publish_video_result(
                req, -2, "命令执行失败",
                {"error": "room_number 不合法（只允许 1-64 位字母/数字/下划线/短横线）"})
            return

        # 2.1) Oryx 推流 secret：每台服务器不同，部署时用 WHIP_SECRET 注入。
        #      没配置时不提前拦下，ffmpeg 会因为鉴权失败一直重连，看起来像“推流成功但黑屏”。
        if not rtmp_secret_ready():
            self._publish_video_result(
                req, -2, "命令执行失败",
                {"error": "未配置 Oryx 推流 secret（请在 /etc/walle-ai.env 设置 WHIP_SECRET）"})
            return

        # 3) 已有推流时：video_device 和 room_number 都一样 → 不处理（不重连、不重启）
        #    任一不同 → 停掉旧的，用新参数重新推流
        with self.video_lock:
            cur_proc = self.video_proc
            cur_meta = dict(self.video_meta)
        streaming = bool(cur_proc is not None and cur_proc.poll() is None)
        if streaming and (
            cur_meta.get("video_device") == video_device
            and cur_meta.get("room_number") == room_number
        ):
            logging.info(
                "推流请求与当前推流一致（device=%s room=%s），不重复推流",
                video_device, room_number,
            )
            self._publish_video_result(req, 0, "ok", {
                "action": "already_streaming",
                "note": "相同 video_device 和 room_number 已在推流，本次不做处理",
                "video_device": video_device,
                "room_number": room_number,
                "rtmp_url": mask_rtmp_url(build_rtmp_url(room_number)),
                "pid": cur_meta.get("pid"),
                "started_at": cur_meta.get("started_at"),
                "audio": cur_meta.get("audio") or {"available": False, "reason": None},
                "uptime_sec": round(
                    time.time() - (cur_meta.get("started_ts") or time.time()), 1
                ),
            })
            return

        # 3.1) 参数有变化（或旧进程已退出）：先停旧的，保证同一时刻只有一路推流
        previous_room = cur_meta.get("room_number") if streaming else None
        previous_device = cur_meta.get("video_device") if streaming else None
        self._stop_video_stream("收到新的推流请求")

        # 4) 检测麦克风：有可用录音源就带上声音，否则纯视频（-an）
        audio = detect_microphone()
        if audio["available"]:
            logging.info("检测到麦克风，推流将混入音频（device=%s, %s/%s）",
                         audio["device"], audio["codec"], audio["bitrate"])
        else:
            logging.info("未启用音频推流：%s", audio["reason"] or "未检测到麦克风")

        # 5) 启动 ffmpeg 推流
        proc, err = self._start_video_stream(video_device, room_number, audio)
        extra = {
            "rtmp_url": mask_rtmp_url(build_rtmp_url(room_number)),
            "command": " ".join(build_ffmpeg_cmd(video_device, room_number, audio)).replace(
                build_rtmp_url(room_number), mask_rtmp_url(build_rtmp_url(room_number))),
            "pid": proc.pid if proc else None,
            "audio": audio,
        }
        extra["action"] = "restarted" if previous_room else "started"
        if previous_room:
            extra["previous_room_number"] = previous_room
        if previous_device:
            extra["previous_video_device"] = previous_device
        if proc is None:
            extra["action"] = "failed"
            extra["error"] = err
            self._publish_video_result(req, -2, "命令执行失败", extra)
            return
        extra["started_at"] = self.video_meta.get("started_at")
        extra["startup_grace_sec"] = VIDEO_STARTUP_GRACE
        self._publish_video_result(req, 0, "ok", extra)

    # ---------- video/stop/send 停止推流 ----------

    def _publish_video_stop_result(self, request_id, result_code, cause, extra=None):
        """统一停止推流结果报文：result -1 当前没有设备在推流 / 0 ok"""
        payload = {
            "device_id": DEVICE_ID,
            "uuid": DEVICE_UUID,
            "hostname": HOSTNAME,
            "ip": self.lan_ip,
            "request_id": str(request_id or ""),
            "result": result_code,
            "cause": cause,
            "timestamp": now_ts(),
            "datetime": now_str(),
        }
        if extra:
            payload.update(extra)
        self.publish_json(RES_VIDEO_STOP_RESULT, payload)
        logging.info("停止推流结果 -> %s: request_id=%s result=%s cause=%s%s",
                     device_topic(RES_VIDEO_STOP_RESULT), payload["request_id"] or "-",
                     result_code, cause,
                     f"（{extra['error']}）" if extra and extra.get("error") else "")
        return payload

    def handle_video_stop(self, payload):
        # 该主题必须校验 uuid
        ok, data = self.verify_uuid(payload, REQ_VIDEO_STOP)
        if not ok:
            return

        rid = self._rid(data)
        req_room = str(data.get("room_number") or "").strip()

        # 先取当前推流状态（meta 会在停止后清空，所以要提前留一份）
        with self.video_lock:
            proc = self.video_proc
            meta = dict(self.video_meta)
        running = bool(proc is not None and proc.poll() is None)

        if not running:
            extra = {"room_number": req_room or None, "stopped": False}
            self._publish_video_stop_result(rid, -1, "当前没有设备在推流", extra)
            return

        # 可选：带 room_number 时做一致性校验，避免停错流
        current_room = meta.get("room_number")
        if req_room and current_room and req_room != current_room:
            self._publish_video_stop_result(
                rid, -1, "当前没有设备在推流",
                {"room_number": req_room, "stopped": False,
                 "error": f"当前推流房间号是 {current_room}，与请求的 {req_room} 不一致"})
            return

        stopped = self._stop_video_stream("收到停止推流请求")
        if not stopped:
            self._publish_video_stop_result(rid, -1, "当前没有设备在推流",
                                            {"room_number": current_room, "stopped": False})
            return

        self._publish_video_stop_result(rid, 0, "ok", {
            "room_number": current_room,
            "video_device": meta.get("video_device"),
            "pid": meta.get("pid"),
            "started_at": meta.get("started_at"),
            "audio": meta.get("audio") or {"available": False, "reason": None},
            "stopped": True,
        })

    # ---------- phone/audio/send|stop 手机麦上行播放 ----------

    def _phone_audio_url(self, room_number):
        """手机推音频的流名 = 视频房间号 + _audio，避免与设备自己的视频流抢同一路 stream。

        媒体面走 WebRTC：App 用 WHIP 推、本机用 WHEP 拉。相比 RTMP 拉流，RTC 走 UDP、
        按最新帧转发，端到端延迟约 200ms（RTMP+ffmpeg 实测会被各级缓冲拖到数秒）。
        """
        return PHONE_AUDIO_WHEP_URL_TEMPLATE.format(
            stream=room_number + PHONE_AUDIO_STREAM_SUFFIX)

    def _publish_phone_audio_result(self, resource, req, result_code, cause, extra=None):
        """手机音频动作的结果报文（result 语义与 video 一致：0 成功 / -1 无流 / -2 失败）"""
        payload = {
            "device_id": DEVICE_ID,
            "uuid": DEVICE_UUID,
            "hostname": HOSTNAME,
            "ip": self.lan_ip,
            "room_number": req.get("room_number"),
            "request_id": self._rid(req),
            "result": result_code,
            "cause": cause,
            "timestamp": now_ts(),
            "datetime": now_str(),
        }
        if extra:
            payload.update(extra)
        self.publish_json(resource, payload)
        logging.info("手机音频结果 -> %s: request_id=%s result=%s cause=%s",
                     device_topic(resource), payload["request_id"] or "-", result_code, cause)
        # 失败细节（播放器退出码/日志路径/stderr 摘要）App 不订阅 result，
        # 本地日志必须打出来，否则设备侧只能看到「失败」看不到原因
        err = payload.get("error")
        if result_code != 0 and err:
            logging.warning("手机音频失败详情: %s", err)
        return payload

    def _stop_phone_audio(self, reason=""):
        """停掉正在播放的手机音频（如果有），返回是否真的停了一个在跑的进程"""
        with self.audio_lock:
            proc = self.audio_proc
            meta = self.audio_meta
            self.audio_proc = None
            self.audio_meta = {}
        if not proc or proc.poll() is not None:
            return False
        proc._intentional_stop = True
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        except Exception:
            logging.exception("停止手机音频播放失败")
        logging.info("已停止手机音频播放%s（room=%s, pid=%s）",
                     f"：{reason}" if reason else "", meta.get("room_number"), meta.get("pid"))
        return True

    def _build_phone_audio_cmd(self, room_number):
        """WHEP 拉 WebRTC 音频 → 本机声卡（独立进程 whep_audio_player.py）。

        为什么不用 ffmpeg 拉 RTMP：RTMP 走 TCP，且服务端/解码器/声卡各级缓冲会把
        「手机说话 → 扬声器出声」拖到数秒（实测 ~10s）。WebRTC 走 UDP、按最新帧转发，
        端到端约 200ms，与视频方向（WHEP）同级。
        """
        cmd = [sys.executable, PHONE_AUDIO_PLAYER_SCRIPT,
               "--url", self._phone_audio_url(room_number),
               "--latency", PHONE_AUDIO_LATENCY,
               "--first-frame-timeout", str(PHONE_AUDIO_FIRST_FRAME_TIMEOUT)]
        if PHONE_AUDIO_DEVICE:
            cmd += ["--device", PHONE_AUDIO_DEVICE]
        return cmd

    def _phone_audio_watch(self, proc, room_number, sink):
        """播放进程看护：退出后清理状态并记录原因"""
        try:
            code = proc.wait()
        finally:
            tail = sink.tail()
            sink.close()
            with self.audio_lock:
                if self.audio_proc is proc:
                    self.audio_proc = None
                    self.audio_meta = {}
        if getattr(proc, "_intentional_stop", False):
            logging.info("手机音频播放已按要求停止（room=%s, exit=%s）", room_number, code)
        elif code == 0:
            logging.info("手机音频播放结束（room=%s）", room_number)
        else:
            logging.warning("手机音频播放异常退出（room=%s, exit=%s）%s",
                            room_number, code, f"，stderr: {tail}" if tail else "")

    def _start_phone_audio(self, room_number):
        cmd = self._build_phone_audio_cmd(room_number)
        url = self._phone_audio_url(room_number)
        header = (
            f"\n===== {now_str()} 启动手机音频播放 room={room_number} =====\n"
            f"$ {' '.join(cmd).replace(url, mask_rtmp_url(url))}\n"
        ).encode("utf-8", "replace")
        sink = make_log_sink(header)
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
                start_new_session=True,
            )
        except FileNotFoundError:
            sink.close()
            return None, (f"未找到播放器脚本 {PHONE_AUDIO_PLAYER_SCRIPT}"
                          "（请把 linux/whep_audio_player.py 一起部署到同目录）")
        except Exception as e:  # noqa: BLE001
            sink.close()
            return None, f"启动 WHEP 播放器失败: {e}"

        threading.Thread(target=self._log_pump, args=(proc, sink),
                         daemon=True, name="phone-audio-log").start()

        # 宽限期内就退出：多半是流还不存在（App 推流未就绪）、缺依赖或声卡不可用
        try:
            code = proc.wait(timeout=PHONE_AUDIO_STARTUP_GRACE)
        except subprocess.TimeoutExpired:
            code = None
        if code is not None:
            time.sleep(0.2)
            tail = sink.error_line()
            msg = f"播放器退出码 {code}（日志: {sink.path}）"
            if code == 3:
                msg += "；缺依赖：pip install aiortc sounddevice + apt install libportaudio2"
            sink.close()
            return None, msg + (f": {tail}" if tail else "")

        with self.audio_lock:
            self.audio_proc = proc
            self.audio_meta = {
                "room_number": room_number,
                "pid": proc.pid,
                "started_at": now_str(),
                "started_ts": time.time(),
                "whep_url": self._phone_audio_url(room_number),
                "output": f"sounddevice/{PHONE_AUDIO_DEVICE or 'default'}",
            }
        threading.Thread(target=self._phone_audio_watch,
                         args=(proc, room_number, sink), daemon=True,
                         name="phone-audio-watch").start()
        return proc, None

    def handle_phone_audio_send(self, payload):
        ok, data = self.verify_uuid(payload, REQ_PHONE_AUDIO_SEND)
        if not ok:
            return

        room_number = str(data.get("room_number") or "").strip()
        req = {"room_number": room_number, "request_id": self._rid(data)}

        if not PHONE_AUDIO_ENABLE:
            self._publish_phone_audio_result(
                RES_PHONE_AUDIO_SEND_RESULT, req, -2, "命令执行失败",
                {"error": "反向音频已关闭（PHONE_AUDIO_ENABLE=False）"})
            return
        if not re.match(VIDEO_ROOM_PATTERN, room_number):
            self._publish_phone_audio_result(
                RES_PHONE_AUDIO_SEND_RESULT, req, -2, "命令执行失败",
                {"error": "room_number 不合法（只允许 1-64 位字母/数字/下划线/短横线）"})
            return

        with self.audio_lock:
            cur_proc = self.audio_proc
            cur_meta = dict(self.audio_meta)
        playing = bool(cur_proc is not None and cur_proc.poll() is None)
        if playing and cur_meta.get("room_number") == room_number:
            self._publish_phone_audio_result(
                RES_PHONE_AUDIO_SEND_RESULT, req, 0, "ok",
                {"action": "already_playing", "room_number": room_number,
                 "pid": cur_meta.get("pid"), "started_at": cur_meta.get("started_at")})
            return

        self._stop_phone_audio("收到新的音频上行请求")
        proc, err = self._start_phone_audio(room_number)
        if proc is None:
            self._publish_phone_audio_result(
                RES_PHONE_AUDIO_SEND_RESULT, req, -2, "命令执行失败",
                {"action": "failed", "error": err})
            return
        self._publish_phone_audio_result(
            RES_PHONE_AUDIO_SEND_RESULT, req, 0, "ok",
            {"action": "started", "room_number": room_number,
             "whep_url": self._phone_audio_url(room_number),
             "pid": proc.pid, "output": f"sounddevice/{PHONE_AUDIO_DEVICE or 'default'}"})

    def handle_phone_audio_stop(self, payload):
        ok, data = self.verify_uuid(payload, REQ_PHONE_AUDIO_STOP)
        if not ok:
            return

        req_room = str(data.get("room_number") or "").strip()
        req = {"room_number": req_room, "request_id": self._rid(data)}

        with self.audio_lock:
            cur_proc = self.audio_proc
            cur_meta = dict(self.audio_meta)
        playing = bool(cur_proc is not None and cur_proc.poll() is None)
        current_room = cur_meta.get("room_number")
        if playing and req_room and current_room and req_room != current_room:
            self._publish_phone_audio_result(
                RES_PHONE_AUDIO_STOP_RESULT, req, -1, "当前没有音频流",
                {"stopped": False,
                 "error": f"当前播放房间号是 {current_room}，与请求的 {req_room} 不一致"})
            return

        stopped = self._stop_phone_audio("收到停止音频上行请求")
        self._publish_phone_audio_result(
            RES_PHONE_AUDIO_STOP_RESULT, req,
            0 if stopped else -1,
            "ok" if stopped else "当前没有音频流",
            {"stopped": stopped, "room_number": current_room})

    # ---------- device/detail/result 状态采集 ----------

    def sample_once(self, interval=1.0):
        """做一次 interval 秒的实时采样，返回 (各核CPU%, 网络快照, 每网卡速率, 总rx, 总tx)"""
        with self.sample_lock:
            psutil.cpu_percent(percpu=True)  # 先打点
            prev = net_snapshot()
            t0 = time.time()
            time.sleep(interval)
            elapsed = time.time() - t0
            cores = psutil.cpu_percent(percpu=True)
            _, per_iface, total_rx, total_tx = calc_net_rates(prev, elapsed)
        return cores, per_iface, total_rx, total_tx, elapsed

    def build_status(self):
        cores, per_iface, total_rx, total_tx, interval = self.sample_once(1.0)

        vm = psutil.virtual_memory()
        sw = None
        try:
            sw = psutil.swap_memory()
        except OSError:
            sw = None
        try:
            load1, load5, load15 = os.getloadavg()
        except (OSError, AttributeError):
            load1 = load5 = load15 = None
        try:
            freq = psutil.cpu_freq()
        except (OSError, RuntimeError, SystemError):
            freq = None
        usb = scan_usb_devices()

        return {
            "device_id": DEVICE_ID,
            "uuid": DEVICE_UUID,
            "hostname": HOSTNAME,
            "ip": self.lan_ip,
            "timestamp": now_ts(),
            "datetime": now_str(),
            "uptime_seconds": int(time.time() - psutil.boot_time()),
            "system": {
                "os": platform.system(),
                "kernel": platform.release(),
                "os_version": platform.version(),
                "python": platform.python_version(),
                "machine": platform.machine(),
                "model": get_pi_model(),
            },
            "cpu": {
                "percent": round(sum(cores) / len(cores), 1),
                "per_core_percent": cores,
                "count_logical": psutil.cpu_count(logical=True),
                "count_physical": psutil.cpu_count(logical=False),
                "load_avg_1m": round(load1, 2) if load1 is not None else None,
                "load_avg_5m": round(load5, 2) if load5 is not None else None,
                "load_avg_15m": round(load15, 2) if load15 is not None else None,
                "frequency_mhz": {
                    "current": round(freq.current, 1) if freq else None,
                    "min": round(freq.min, 1) if freq and freq.min else None,
                    "max": round(freq.max, 1) if freq and freq.max else None,
                },
                "temperature_c": get_cpu_temp(),
                "sample_interval_sec": interval,
            },
            "memory": {
                "percent": vm.percent,
                "total_bytes": vm.total,
                "used_bytes": vm.used,
                "available_bytes": vm.available,
                "free_bytes": vm.free,
                "total_human": human_size(vm.total),
                "used_human": human_size(vm.used),
                "available_human": human_size(vm.available),
                "swap": ({
                    "percent": sw.percent,
                    "total_bytes": sw.total,
                    "used_bytes": sw.used,
                    "total_human": human_size(sw.total),
                    "used_human": human_size(sw.used),
                } if sw is not None else None),
            },
            "disk": {
                "mounts": disk_mounts(),
            },
            "network": {
                "sample_interval_sec": round(interval, 2),
                "total_rx_bytes_per_sec": round(total_rx, 1),
                "total_tx_bytes_per_sec": round(total_tx, 1),
                "total_rx_rate": human_rate(total_rx),
                "total_tx_rate": human_rate(total_tx),
                "interfaces": per_iface,
            },
            "usb": {
                "count": len(usb),
                "devices": list(usb.values()),
            },
            "video": self._video_status(),
            "alarms": {
                "active": sorted(self.active_alarms),
                "thresholds": {
                    "cpu_percent": THRESH_CPU,
                    "memory_percent": THRESH_MEM,
                    "disk_percent": THRESH_DISK,
                    "temperature_c": THRESH_TEMP,
                    "network_bytes_per_sec": THRESH_NET_BPS,
                    "network_rate_human": human_rate(THRESH_NET_BPS),
                },
            },
        }

    def _video_status(self):
        """
        当前推流状态快照（state/result 的 video 分组）。

        数据复用推流时写入的 self.video_meta；进程已退出（poll() 非 None，看护线程
        还来不及清理）也按未推流返回，避免误报 streaming=true。未推流时除
        streaming=False 外所有字段为 null，App 端按「无推流」处理。
        """
        with self.video_lock:
            proc = self.video_proc
            meta = dict(self.video_meta)
        if not proc or proc.poll() is not None or not meta:
            return {
                "streaming": False,
                "room_number": None,
                "video_device": None,
                "audio": None,
                "pid": None,
                "started_at": None,
                "uptime_sec": None,
            }
        started_ts = meta.get("started_ts")
        return {
            "streaming": True,
            "room_number": meta.get("room_number"),
            "video_device": meta.get("video_device"),
            "audio": meta.get("audio"),
            "pid": meta.get("pid"),
            "started_at": meta.get("started_at"),
            "uptime_sec": round(time.time() - started_ts, 1) if started_ts else None,
        }

    # ---------- 订阅报文 UUID 校验 ----------

    @staticmethod
    def verify_uuid(payload, topic):
        """
        校验订阅报文 JSON 里的 uuid 是否与本地一致。
        返回 (是否通过, 解析后的 dict)。不通过时静默忽略该消息（只记日志，不回复）。
        discovery/query 不调用本方法（免校验）。
        """
        if not payload.strip():
            logging.warning("忽略 %s：报文为空（必须带 uuid）", topic)
            return False, None
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as e:
            logging.warning("忽略 %s：JSON 解析失败（%s）", topic, e)
            return False, None
        if not isinstance(data, dict):
            logging.warning("忽略 %s：报文不是 JSON 对象", topic)
            return False, None
        req_uuid = str(data.get("uuid") or "").strip().lower()
        if not req_uuid or req_uuid != DEVICE_UUID.lower():
            logging.warning("忽略 %s：uuid 不匹配（收到=%s，本地=%s）",
                            topic, req_uuid or "<空>", DEVICE_UUID)
            return False, None
        return True, data

    def handle_query_status(self, payload):
        ok, data = self.verify_uuid(payload, REQ_STATE)
        if not ok:
            return
        rid = self._rid(data)
        try:
            status = self.build_status()
            status["request_id"] = rid
            self.publish_json(RES_STATE_RESULT, status)
            logging.info("已响应状态查询 -> %s（request_id=%s）",
                         device_topic(RES_STATE_RESULT), rid or "-")
        except Exception:
            logging.exception("采集/上报 %s 失败", device_topic(RES_STATE_RESULT))

    # ---------- cmd 远程命令 ----------

    def handle_cmd_exec(self, payload):
        request_id = str(uuid.uuid4())[:8]
        result = {
            "device_id": DEVICE_ID,
            "uuid": DEVICE_UUID,
            "hostname": HOSTNAME,
            "request_id": request_id,
            "cmd": None,
            "status": "error",
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "duration_ms": 0,
            "timestamp": now_ts(),
            "datetime": now_str(),
        }
        # 校验 uuid：不匹配或格式非法则静默忽略，不执行、不回复
        ok, data = self.verify_uuid(payload, REQ_CMD)
        if not ok:
            return

        if data.get("request_id"):
            request_id = str(data["request_id"])
            result["request_id"] = request_id

        if not ENABLE_CMD_EXEC:
            result["stderr"] = "设备已禁用远程命令执行（ENABLE_CMD_EXEC=False）"
            result["datetime"] = now_str()
            self._publish_cmd_result(result)
            return

        cmd = data.get("cmd") if isinstance(data, dict) else None
        result["cmd"] = cmd
        if not cmd or not isinstance(cmd, str) or not cmd.strip():
            result["stderr"] = "缺少 cmd 字段或 cmd 为空"
            result["datetime"] = now_str()
            self._publish_cmd_result(result)
            return

        # 白名单校验（按命令前缀匹配）
        if CMD_ALLOWLIST:
            head = cmd.strip()
            if not any(head == w or head.startswith(w + " ") for w in CMD_ALLOWLIST):
                result["status"] = "denied"
                result["stderr"] = f"命令不在白名单中，允许的前缀: {CMD_ALLOWLIST}"
                result["datetime"] = now_str()
                self._publish_cmd_result(result, note="命令被白名单拒绝")
                logging.warning("拒绝非白名单命令: %s", cmd)
                return

        try:
            timeout = int(data.get("timeout", CMD_TIMEOUT_DEFAULT))
        except (TypeError, ValueError):
            timeout = CMD_TIMEOUT_DEFAULT
        timeout = max(1, min(timeout, CMD_TIMEOUT_MAX))
        result["timeout_sec"] = timeout

        logging.info("执行远程命令: %s (timeout=%ss, request_id=%s)", cmd, timeout, request_id)
        start = time.time()
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                executable="/bin/bash",
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            result["exit_code"] = proc.returncode
            result["status"] = "success" if proc.returncode == 0 else "failed"
            result["stdout"] = self._truncate(proc.stdout)
            result["stderr"] = self._truncate(proc.stderr)
        except subprocess.TimeoutExpired as e:
            result["status"] = "timeout"
            result["exit_code"] = None
            result["stderr"] = f"命令执行超时（>{timeout}s）已被终止"
            out = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            result["stdout"] = self._truncate(out)
        except Exception as e:  # noqa: BLE001
            result["status"] = "error"
            result["stderr"] = f"执行异常: {e!r}"
        finally:
            result["duration_ms"] = int((time.time() - start) * 1000)
            result["timestamp"] = now_ts()
            result["datetime"] = now_str()
            # 发布 + 打印「已响应命令执行 -> .../cmd/result（...）」
            self._publish_cmd_result(result)

    def _publish_cmd_result(self, result, note=None):
        """发布 cmd/result 并打印应答日志（格式与「已响应能力查询」保持一致）。

        note 为空时自动从 stdout/stderr 里提炼一句简述：
          · 成功：补充 stdout 字符数
          · 失败/超时/拒绝：取 stderr 第一行（过长截断）
        """
        self.publish_json(RES_CMD_RESULT, result)

        detail = note
        if detail is None:
            stderr = str(result.get("stderr") or "").strip()
            stdout = str(result.get("stdout") or "")
            if stderr:
                first = stderr.splitlines()[0]
                detail = first if len(first) <= 80 else first[:80] + "…"
            elif stdout:
                detail = f"stdout {len(stdout)} 字符"

        logging.info(
            "已响应命令执行 -> %s（request_id=%s，status=%s，exit=%s，耗时 %dms%s）",
            device_topic(RES_CMD_RESULT),
            result.get("request_id"),
            result.get("status"),
            result.get("exit_code"),
            int(result.get("duration_ms") or 0),
            f"，{detail}" if detail else "",
        )

    @staticmethod
    def _truncate(text):
        if not text:
            return ""
        if len(text) <= CMD_OUTPUT_LIMIT:
            return text
        return text[:CMD_OUTPUT_LIMIT] + f"\n...[输出过长，已截断，原始长度 {len(text)} 字符]"

    # ---------- 周期监控主循环 ----------

    def monitor_loop(self):
        # CPU 百分比首次打点
        with self.sample_lock:
            psutil.cpu_percent(percpu=True)
        prev_net = net_snapshot()
        prev_t = time.time()

        while not self.stop_event.is_set():
            if self.stop_event.wait(MONITOR_INTERVAL):
                break
            now = time.time()
            elapsed = max(now - prev_t, 0.1)

            with self.sample_lock:
                cores = psutil.cpu_percent(percpu=True)
            cpu = round(sum(cores) / len(cores), 1)
            mem = psutil.virtual_memory()
            mounts = disk_mounts()
            temp = get_cpu_temp()
            prev_net, _ifaces, rx_rate, tx_rate = calc_net_rates(prev_net, elapsed)
            prev_t = now

            # 1) 阈值类告警
            self.check_threshold("cpu_high", cpu, THRESH_CPU, "cpu", "cpu_high",
                                 "%", "CPU 使用率", {"per_core_percent": cores})
            self.check_threshold("memory_high", mem.percent, THRESH_MEM, "memory", "memory_high",
                                 "%", "内存使用率",
                                 {"used_human": human_size(mem.used),
                                  "total_human": human_size(mem.total)})
            if temp is not None:
                self.check_threshold("temperature_high", temp, THRESH_TEMP, "temperature",
                                     "temperature_high", "℃", "CPU 温度")
            for mnt in mounts:
                key = f"disk_high:{mnt['mount']}"
                self.check_threshold(key, mnt["percent"], THRESH_DISK, "disk", "disk_high",
                                     "%", f"磁盘 {mnt['mount']} 使用率",
                                     {"mount": mnt["mount"], "device": mnt["device"],
                                      "free_human": mnt["free_human"]})
            self.check_threshold("network_rx_high", rx_rate, THRESH_NET_BPS, "network",
                                 "network_rx_high", "B/s", "网络入站带宽",
                                 {"rate_human": human_rate(rx_rate),
                                  "threshold_human": human_rate(THRESH_NET_BPS)})
            self.check_threshold("network_tx_high", tx_rate, THRESH_NET_BPS, "network",
                                 "network_tx_high", "B/s", "网络出站带宽",
                                 {"rate_human": human_rate(tx_rate),
                                  "threshold_human": human_rate(THRESH_NET_BPS)})

            # 2) USB 热插拔
            try:
                self.check_usb()
            except Exception:
                logging.exception("USB 扫描异常")

    # ---------- 启动 ----------

    def run(self):
        logging.info("设备 %s (%s) 启动监控，采样周期 %ss", DEVICE_ID, self.lan_ip, MONITOR_INTERVAL)
        logging.info("协议 v1: tenant=%s device=%s broker=%s:%s",
                     sanitize_segment(MQTT_TENANT), sanitize_segment(DEVICE_ID),
                     MQTT_HOST, MQTT_PORT)
        if sanitize_segment(MQTT_TENANT) == sanitize_segment(MQTT_USER):
            logging.warning(
                "租户当前取自 MQTT 用户名（%s）。如果手机 App 用的是另一个账号登录，"
                "请在 /etc/walle-ai.env 里设置 MQTT_TENANT=<App 的登录用户名>，"
                "否则 App 订阅 walle/<App用户名>/# 时收不到本设备的报文。",
                sanitize_segment(MQTT_TENANT))
        # clean_session=True：每次连接都重新订阅，改了请求主题名也不会残留旧订阅
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id=DEVICE_ID, clean_session=True)
        self.client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
        # 遗嘱：异常掉线时 broker 代发 offline 到 walle/{tenant}/{device}/v1/online
        # （遗嘱必须在 connect 前设置，因此里面的静态信息取自启动时刻）
        will_topic, will_payload = self._will_message()
        self.client.will_set(
            will_topic,
            json.dumps(will_payload, ensure_ascii=False),
            qos=MQTT_QOS, retain=True,
        )
        logging.info("遗嘱(LWT)主题: %s", will_topic)
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message
        self.client.reconnect_delay_set(min_delay=3, max_delay=30)
        # TLS：MQTT_TLS=auto 时，8883/8084 视为 TLS 端口
        tls_on = MQTT_TLS == "on" or (MQTT_TLS == "auto" and MQTT_PORT in (8883, 8084))
        if tls_on:
            import ssl
            if MQTT_CA_FILE:
                ctx = ssl.create_default_context(cafile=MQTT_CA_FILE)
            else:
                ctx = ssl.create_default_context()
            self.client.tls_set_context(ctx)
            logging.info("启用 TLS 连接 %s:%s（CA=%s）",
                         MQTT_HOST, MQTT_PORT, MQTT_CA_FILE or "system")
        else:
            logging.warning("使用明文连接 %s:%s（建议改用 8883 + TLS）", MQTT_HOST, MQTT_PORT)
        self.client.connect(MQTT_HOST, MQTT_PORT, MQTT_KEEPALIVE)
        self.client.loop_start()

        try:
            self.monitor_loop()
        finally:
            logging.info("正在退出，停止推流、发送 offline 并断开连接...")
            self._stop_video_stream("程序退出")
            self._stop_phone_audio("程序退出")
            try:
                # 双发：LWT 只挂了一个主题，正常退出时把另一套也置为 offline
                self.publish_json(RES_ONLINE, self.build_online_payload(False), retain=True)
                time.sleep(0.5)
            except Exception:
                pass
            self.pool.shutdown(wait=False, cancel_futures=True)
            self.client.loop_stop()
            self.client.disconnect()


def print_config_check():
    """--check：打印解析后的配置与主题，不连 broker、不做采集。"""
    global DEVICE_ID
    try:
        mac = pick_primary_mac(get_lan_ip())
        DEVICE_ID = generate_device_id(mac, get_cpu_core_count(), psutil.virtual_memory().total)
    except Exception as exc:                      # 取不到网卡/内存信息时不影响看配置
        DEVICE_ID = f"<无法计算: {exc}>"
    tls_on = MQTT_TLS == "on" or (MQTT_TLS == "auto" and MQTT_PORT in (8883, 8084))
    print("== 生效配置 ==")
    print(f"  环境文件 : {ENV_SOURCE or '未加载（使用环境变量/默认值）'}")
    print(f"  Broker   : {MQTT_HOST}:{MQTT_PORT}   TLS={'on' if tls_on else 'off'} (MQTT_TLS={MQTT_TLS})")
    print(f"  用户名   : {MQTT_USER}")
    print(f"  密码     : {'********' if MQTT_PASSWORD else '(空)'}")
    print(f"  Oryx secret: {'已配置' if WHIP_SECRET else '未配置（视频推流会被服务端拒绝）'}")
    print(f"  租户     : {sanitize_segment(MQTT_TENANT)}")
    print(f"  设备号   : {DEVICE_ID}")
    if DEVICE_ID.startswith("<"):
        print("  订阅主题 : （设备号计算失败，跳过拼接；在树莓派上运行可正常计算）")
    else:
        print(f"  主题前缀 : walle/{sanitize_segment(MQTT_TENANT)}/{DEVICE_ID}/")
        print("  订阅主题 :")
        for topic, _qos in subscriptions():
            print(f"    - {topic}")
    if sanitize_segment(MQTT_TENANT) == sanitize_segment(MQTT_USER):
        print(f"  提示     : 租户与用户名相同（{MQTT_TENANT}）。若手机 App 用别的账号登录，"
              f"请把 MQTT_TENANT 改成 App 的用户名")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    if "-h" in sys.argv or "--help" in sys.argv:
        print(__doc__)
        return 0
    if "--check" in sys.argv:
        print_config_check()
        return 0

    logging.info("配置来源: %s", ENV_SOURCE or "环境变量/默认值")
    monitor = RpiMonitor()

    def _shutdown(signum, frame):
        logging.info("收到信号 %s，准备停止...", signum)
        monitor.stop_event.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    monitor.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
