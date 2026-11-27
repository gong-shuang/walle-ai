#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""car-agent：网页对话 -> 意图 -> MQTT 车控指令。

设计约束：
  - 通过 MQTT 主题控制：walle/{tenant}/device/{device}/car/motor/set/request；
    tenant 取服务端 MQTT 账号名，device 取页面当前选中的设备。
  - 运动指令通过 MQTT 真实下发；急停优先，随时可零速打断。
  - 简单指令走确定性解析；复杂语句可选走 LLM（默认关闭；失败则提示，不猜）。
"""

import hashlib
import copy
import glob
import json
import logging
import os
import platform
import re
import secrets
import shutil
import signal
import socket
import sys
import threading
import time
import uuid
from urllib import error as urlerror
from urllib import request as urlrequest
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(BASE_DIR)
VENDOR_DIR = os.path.join(BASE_DIR, "vendor")
if os.path.isdir(VENDOR_DIR) and VENDOR_DIR not in sys.path:
    sys.path.insert(0, VENDOR_DIR)

from walle_protocol import (RESULT_CODES, RequestBus, decode_result,  # noqa: E402,F401
                            new_request_id)
from task_store import TaskStore  # noqa: E402
import task_schedule  # noqa: E402
from task_scheduler import TaskScheduler  # noqa: E402

try:
    import paho.mqtt.client as mqtt
except Exception:  # pragma: no cover - 允许无 paho 时仍可解析意图
    mqtt = None

DEFAULTS = {
    "MQTT_HOST": "127.0.0.1",
    "MQTT_PORT": "1883",
    "MQTT_TLS": "0",
    "MQTT_USER": "",
    "MQTT_PASSWORD": "",
    # 独立部署时 MQTT_USER 是 agent 自己的账号，租户要单独配置；
    # 留空则退回旧行为（服务端账号即租户，即 MQTT_USER）。
    "TENANT": "",
    # 对话 topic 的 agent 段；留空则用 MQTT_USER。
    # 是否订阅/回复对话 topic（walle/{tenant}/agent/{agent}/chat/*）。
    "CHAT_MQTT_ENABLED": "1",
    # MQTT clientid 前缀；完整格式为「前缀 + 8 位随机十六进制」，进程启动时生成一次
    "CLIENT_ID_PREFIX": "agent-",
    "DEFAULT_SPEED": "0.5",
    "MIN_SPEED": "0.5",
    "MAX_SPEED": "0.6",
    "MAX_DISTANCE": "10",
    "DEFAULT_ANGULAR": "1.0",
    "MAX_ANGLE": "360",
    "SEND_HZ": "5",
    "LLM_ENABLED": "0",
    "LLM_BASE_URL": "",
    "LLM_API_KEY": "",
    "LLM_MODEL": "",
    # M3-01：任务库路径；相对路径按 walle-agent/ 解析，默认 data/agent.db
    "TASK_DB_PATH": "data/agent.db",
    # M3-02：任务调度时区
    "TASK_TIMEZONE": "Asia/Shanghai",
    # M3-05/M3-08：任务失败重试次数与退避秒数
    "TASK_RETRY_MAX": "2",
    "TASK_RETRY_BACKOFF_SEC": "2",
    # M3-06：任务控制权策略；当前实现 shared
    "TASK_CONTROL_MODE": "shared",
    # M3-07：启动对账时，错过触发点在宽限内补执行，超过则跳过
    "TASK_MISSED_GRACE_SEC": "300",
}

AGENT_VERSION_FILE = os.path.join(REPO_ROOT, "VERSION")

# M1-02：设备状态缓存新鲜度。超过该时长未更新的 baseState/runState 不再展示。
DEVICE_STATE_TTL_SEC = float(os.environ.get("WALLE_DEVICE_STATE_TTL_SEC", "15"))

# runState 子分组在 is_supported=false / available=false 时的空值模板。
_RUN_GROUP_DEFAULTS = {
    "stream": {
        "streaming": False, "room_number": None, "camera_device": None,
        "audio_input": None, "started_at": None, "uptime_sec": None,
    },
    "phone_audio": {"playing": False, "room_number": None},
    "recording": {
        "recording": False, "filename": None, "camera_device": None,
        "audio_input": None,
    },
    "motor": {"moving": False},
    "servo": {"moving": False},
    "battery": {"voltage_v": None, "available": False},
    "sonar": {"distance_cm": None, "available": False},
}

# M1-07：可从 runState 缓存的能力分组
CAPABILITY_FEATURES = tuple(_RUN_GROUP_DEFAULTS)

# M3-04：任务动作（首期只支持录像开始/停止）
TASK_PHASE_TOOLS = {"start": "record.start", "stop": "record.stop"}
TASK_PHASE_REVERSE = {tool: phase for phase, tool in TASK_PHASE_TOOLS.items()}


def _load_agent_version(path):
    """读取 walle-agent/VERSION（shell export 格式）。缺失或非法时回落默认值。"""
    values = {}
    try:
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
    except OSError:
        pass
    try:
        version_num = int(values.get("AGENT_VERSION_NUM", "0"))
    except ValueError:
        version_num = 0
    return (values.get("AGENT_VERSION_NAME", "0.0.0"), version_num,
            values.get("AGENT_VERSION_DATE", ""))


AGENT_VERSION, AGENT_VERSION_CODE, AGENT_VERSION_DATE = _load_agent_version(
    AGENT_VERSION_FILE)

# ---------------- 调试输出 ----------------
# WALLE_AGENT_DEBUG=1 时，把所有收发到的 MQTT 报文格式化打印到终端；
# 关闭时只写日志文件，终端保持安静。
DEBUG_TRAFFIC = os.environ.get("WALLE_AGENT_DEBUG", "").lower() in ("1", "true", "yes", "on")


def _trace(direction, topic, payload, qos=0, retain=False):
    if not DEBUG_TRAFFIC:
        return
    if isinstance(payload, (dict, list)):
        body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    else:
        try:
            body = json.dumps(json.loads(payload), ensure_ascii=False, indent=2, sort_keys=True)
        except Exception:  # noqa: BLE001
            body = str(payload)
    arrow = ">>> PUB" if direction == "OUT" else "<<< SUB"
    now = time.time()
    print("\n{} topic={} qos={} retain={} ts={}.{:03d}".format(
        arrow, topic, qos, retain,
        time.strftime("%H:%M:%S", time.localtime(now)), int(now * 1000) % 1000))
    print(body, flush=True)


_CFG = None
_LOG = None
_AGENT = None
_INIT_LOCK = threading.RLock()  # 可重入：_get_agent 持锁时会再次调用 load_config

_CN_DIGITS = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_NUM_RE = r"([0-9]+(?:\.[0-9]+)?|[零一二两三四五六七八九十百半]+)"


_CLIENT_ID = None


DEVICE_ID_FILE = os.path.join(REPO_ROOT, "etc", ".run", "agent-id")
# 展示名运行时状态：首次启动生成 agent_{hostname} 并落盘，之后 MQTT 修改也写这里
AGENT_NAME_FILE = os.path.join(REPO_ROOT, "etc", ".run", "agent-name")

_AGENT_NAME = None
_AGENT_NAME_LOCK = threading.RLock()


def _machine_fingerprint():
    """取本机 MAC 作为稳定种子；取不到真实 MAC 时退回 machine-id，再退回 None。"""
    mac = uuid.getnode()
    # 第 41 位为 1 表示随机生成的伪 MAC，不能当硬件标识
    if mac and not (mac & (1 << 40)):
        return format(mac, "012x")
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            with open(path, encoding="utf-8") as fh:
                seed = fh.read().strip()
            if seed:
                return seed
        except OSError:
            continue
    return None


def mqtt_client_id():
    """固定 clientid：首次启动按本机 MAC 生成并写入 etc/.run/agent-id，之后复用。

    文件存在即直接使用，格式为「前缀 + 8 位字母数字」，权限 600。
    取不到 MAC 时依次退回 machine-id；两者都没有则随机生成并告警（下次启动会变化）。
    """
    global _CLIENT_ID
    if _CLIENT_ID:
        return _CLIENT_ID
    cfg = load_config()
    path = cfg.get("DEVICE_ID_FILE") or DEVICE_ID_FILE
    try:
        with open(path, encoding="utf-8") as fh:
            value = fh.read().strip()
        if re.fullmatch(r"[0-9A-Za-z_-]{6,64}", value):
            _CLIENT_ID = value
            return _CLIENT_ID
        log().warning("%s 内容不合法，将重新生成", path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        log().warning("读取 %s 失败：%s，将重新生成", path, exc)

    prefix = sanitize_segment(cfg.get("CLIENT_ID_PREFIX") or "agent") or "agent"
    if not prefix.endswith("-"):
        prefix += "-"
    seed = _machine_fingerprint()
    if seed:
        value = prefix + hashlib.sha256(seed.encode()).hexdigest()[:8]
    else:
        value = prefix + secrets.token_hex(4)
        log().warning("取不到 MAC 与 machine-id，clientid 用随机值 %s（重启后会变化）", value)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(value + "\n")
        log().info("已生成 clientid=%s -> %s", value, path)
    except FileExistsError:
        try:
            with open(path, encoding="utf-8") as fh:
                existing = fh.read().strip()
            if re.fullmatch(r"[0-9A-Za-z_-]{6,64}", existing):
                value = existing
        except OSError:
            pass
    except OSError as exc:
        log().error("写入 %s 失败：%s（本次使用 %s）", path, exc, value)
    _CLIENT_ID = value
    return _CLIENT_ID


_READY_PRINTED = False


def _announce_ready(agent):
    """首次连接并完成订阅后向 stdout 打印一行就绪信息（非 debug 也打印一次）。"""
    global _READY_PRINTED
    if _READY_PRINTED:
        return
    _READY_PRINTED = True
    print("walle-agent ready: clientid=%s tenant=%s chat=%s device_filter=%s" % (
        mqtt_client_id(), agent.tenant,
        chat_topic("request") if agent.chat_enabled else "-",
        "walle/%s/device/+" % agent.tenant), flush=True)


def log():
    global _LOG
    if _LOG is None:
        log_dir = os.path.join(REPO_ROOT, "log")
        os.makedirs(log_dir, exist_ok=True)
        logger = logging.getLogger("car_agent")
        logger.setLevel(logging.INFO)
        if not logger.handlers:
            fh = logging.FileHandler(os.path.join(log_dir, "car_agent.log"), encoding="utf-8")
            fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(fh)
        _LOG = logger
    return _LOG


def load_config():
    global _CFG
    with _INIT_LOCK:
        if _CFG is not None:
            return _CFG
        cfg = dict(DEFAULTS)
        path = os.path.join(REPO_ROOT, "etc", "walle-agent.conf")
        if not os.path.exists(path):
            path = os.path.join(REPO_ROOT, "etc", "car_agent.conf")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, val = line.split("=", 1)
                    cfg[key.strip()] = val.strip()
        for key in DEFAULTS:
            env_key = "CAR_AGENT_" + key
            if os.environ.get(env_key):
                cfg[key] = os.environ[env_key]
        _CFG = cfg
        return cfg


def sanitize_segment(value):
    """MQTT topic 段只保留字母数字下划线连字符，避免拼出非法主题。"""
    text = re.sub(r"[^0-9A-Za-z_-]+", "_", str(value or ""))
    return text.strip("_") or "unknown"


def server_tenant():
    """租户名：独立 agent 用 TENANT，内嵌模式下退回 MQTT_USER。

    独立部署时 MQTT_USER 是 agent 自己的身份，不再等于租户，
    所以必须允许 TENANT 显式覆盖，否则会把对话 topic 拼到自己账号名下。
    """
    cfg = load_config()
    return sanitize_segment(cfg.get("TENANT") or cfg.get("MQTT_USER") or "")


def agent_identity():
    """对话 topic 的 agent 段 = 固定 clientid，与 ACL 的 agent/${clientid}/# 对齐。"""
    return mqtt_client_id()


def _write_agent_name(value):
    """把展示名写入 etc/.run/agent-name，供下次启动复用。"""
    os.makedirs(os.path.dirname(AGENT_NAME_FILE), exist_ok=True)
    with open(AGENT_NAME_FILE, "w", encoding="utf-8") as fh:
        fh.write(value + "\n")


def agent_name():
    """展示名：优先读 etc/.run/agent-name，缺失时用 agent_{hostname} 并落盘。

    不再从 walle-agent.conf 读取；名称不参与任何 topic/ACL 计算。
    """
    global _AGENT_NAME
    with _AGENT_NAME_LOCK:
        if _AGENT_NAME:
            return _AGENT_NAME
        try:
            with open(AGENT_NAME_FILE, encoding="utf-8") as fh:
                value = fh.read().strip()
        except OSError:
            value = ""
        if not value:
            value = "agent_" + (socket.gethostname().strip() or "unknown")
            try:
                _write_agent_name(value)
                log().info("已生成 agent 名称 %s -> %s", value, AGENT_NAME_FILE)
            except OSError as exc:
                log().warning("写入 %s 失败：%s（本次使用 %s）",
                              AGENT_NAME_FILE, exc, value)
        _AGENT_NAME = value
        return _AGENT_NAME


def set_agent_name(value):
    """更新展示名并落盘；值由调用方校验，写盘失败抛 OSError。"""
    global _AGENT_NAME
    with _AGENT_NAME_LOCK:
        _write_agent_name(value)
        _AGENT_NAME = value


def normalize_agent_name(value):
    """校验 agent_name，返回 (清洗后的名字, 错误原因)；合法时原因为 None。"""
    if not isinstance(value, str):
        return None, "agent_name 必须是字符串"
    name = value.strip()
    if not name:
        return None, "agent_name 不能为空"
    if len(name) > 64:
        return None, "agent_name 不能超过 64 个字符"
    if any(ord(ch) < 32 for ch in name):
        return None, "agent_name 不能包含控制字符"
    return name, None


def chat_topic(resource):
    return "walle/{}/agent/{}/chat/{}".format(
        server_tenant(), agent_identity(), resource)


def agent_status_topic():
    # 与设备端 walle/{t}/device/{d}/online 对齐：online 主题承载在线状态（retained + LWT）
    return "walle/{}/agent/{}/online".format(server_tenant(), agent_identity())


def agent_base_state_topic(resource):
    """agent 基本状态查询主题：walle/{t}/agent/{a}/baseState/query/{request|response}"""
    return "walle/{}/agent/{}/baseState/query/{}".format(
        server_tenant(), agent_identity(), resource)


def agent_config_topic(resource):
    """agent 配置修改主题：walle/{t}/agent/{a}/config/modify/{request|response}"""
    return "walle/{}/agent/{}/config/modify/{}".format(
        server_tenant(), agent_identity(), resource)


def agent_stop_topic(resource):
    """agent 会话停止主题：walle/{t}/agent/{a}/chat/stop/{request|response}"""
    return "walle/{}/agent/{}/chat/stop/{}".format(
        server_tenant(), agent_identity(), resource)


def agent_progress_topic():
    """agent 会话进度上报主题：walle/{t}/agent/{a}/chat/progress/report"""
    return "walle/{}/agent/{}/chat/progress/report".format(
        server_tenant(), agent_identity())


def agent_task_topic(action, resource="request"):
    """任务管理主题：walle/{t}/agent/{a}/task/{action}/{request|response}"""
    return "walle/{}/agent/{}/task/{}/{}".format(
        server_tenant(), agent_identity(), action, resource)


def agent_task_report_topic():
    """任务事件上报主题：walle/{t}/agent/{a}/task/report"""
    return "walle/{}/agent/{}/task/report".format(
        server_tenant(), agent_identity())


def agent_alarm_topic():
    """agent 任务告警：walle/{t}/agent/{a}/alarm/report"""
    return "walle/{}/agent/{}/alarm/report".format(
        server_tenant(), agent_identity())


# 统一错误码与 request_id 生成器已迁移到 walle_protocol.py（M1-01），
# 这里通过顶部 import 保持 car_agent.decode_result / new_request_id 兼容。


def _read_first_line(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.readline().strip()
    except OSError:
        return ""


def _os_info():
    release = {}
    try:
        with open("/etc/os-release", encoding="utf-8") as fh:
            for line in fh:
                key, sep, value = line.strip().partition("=")
                if sep:
                    release[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return {
        "name": release.get("NAME") or platform.system() or None,
        "version": release.get("VERSION_ID") or release.get("VERSION") or None,
        "pretty_name": release.get("PRETTY_NAME") or None,
        "kernel": platform.release() or None,
        "arch": platform.machine() or None,
        "platform": platform.platform() or None,
        "python": platform.python_version() or None,
    }


def _cpu_model():
    for key in ("model name", "Hardware", "Processor"):
        try:
            with open("/proc/cpuinfo", encoding="utf-8") as fh:
                for line in fh:
                    name, sep, value = line.partition(":")
                    if sep and name.strip() == key and value.strip():
                        return value.strip()
        except OSError:
            break
    return platform.processor() or platform.machine() or None


def _cpu_times():
    parts = _read_first_line("/proc/stat").split()
    if len(parts) < 5 or parts[0] != "cpu":
        return None
    try:
        values = [int(value) for value in parts[1:]]
    except ValueError:
        return None
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values), idle


def _cpu_temperature():
    """读取 CPU/SoC 温度（摄氏度）；无 thermal zone 时返回 None。"""
    candidates = []
    for zone in glob.glob("/sys/class/thermal/thermal_zone*"):
        try:
            with open(os.path.join(zone, "type"), encoding="utf-8") as fh:
                zone_type = fh.read().strip().lower()
            with open(os.path.join(zone, "temp"), encoding="utf-8") as fh:
                raw = float(fh.read().strip())
        except (OSError, ValueError):
            continue
        # 内核一般上报毫摄氏度；少数平台直接上报摄氏度
        value = raw / 1000.0 if raw > 1000 else raw
        candidates.append((zone_type, value))
    if not candidates:
        return None
    preferred = [value for zone_type, value in candidates
                 if any(key in zone_type for key in
                        ("cpu", "soc", "package", "coretemp", "x86_pkg_temp"))]
    values = preferred or [value for _zone_type, value in candidates]
    return round(max(values), 1)


def _network_totals():
    """所有非 lo 网卡的累计收发字节；读取失败返回 None。"""
    rx_total = tx_total = 0
    try:
        with open("/proc/net/dev", encoding="utf-8") as fh:
            lines = fh.readlines()[2:]
        for line in lines:
            name, sep, data = line.partition(":")
            fields = data.split()
            if not sep or len(fields) < 16 or name.strip() == "lo":
                continue
            rx_total += int(fields[0])
            tx_total += int(fields[8])
    except (OSError, ValueError):
        return None
    return rx_total, tx_total


def _cpu_and_net_sample(interval=0.2):
    """一次采样同时计算 CPU 使用率与网络收发速率（字节/秒）。"""
    cpu_first = _cpu_times()
    net_first = _network_totals()
    started = time.monotonic()
    time.sleep(interval)
    cpu_second = _cpu_times()
    net_second = _network_totals()
    elapsed = time.monotonic() - started

    cpu_percent = None
    if cpu_first is not None and cpu_second is not None:
        total = cpu_second[0] - cpu_first[0]
        idle = cpu_second[1] - cpu_first[1]
        if total > 0:
            cpu_percent = round(
                max(0.0, min(100.0, (1.0 - idle / total) * 100.0)), 1)

    rx_rate = tx_rate = None
    if net_first is not None and net_second is not None and elapsed > 0:
        rx_rate = round(max(0, net_second[0] - net_first[0]) / elapsed, 1)
        tx_rate = round(max(0, net_second[1] - net_first[1]) / elapsed, 1)
    return cpu_percent, rx_rate, tx_rate


def _memory_info():
    values = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                key, sep, value = line.partition(":")
                if not sep:
                    continue
                key = key.strip()
                if key in ("MemTotal", "MemAvailable", "MemFree", "Buffers", "Cached"):
                    values[key] = int(value.strip().split()[0]) * 1024
    except (OSError, ValueError):
        pass
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if available is None:
        available = (values.get("MemFree", 0) + values.get("Buffers", 0)
                     + values.get("Cached", 0))
    if not total:
        return {"total_bytes": None, "used_bytes": None,
                "available_bytes": None, "usage_percent": None}
    used = max(0, total - available)
    return {
        "total_bytes": total,
        "used_bytes": used,
        "available_bytes": available,
        "usage_percent": round(used / total * 100.0, 1),
    }


def _disk_info(path="/"):
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return {"path": path, "total_bytes": None, "used_bytes": None,
                "free_bytes": None, "usage_percent": None}
    percent = round(usage.used / usage.total * 100.0, 1) if usage.total else None
    return {
        "path": path,
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "usage_percent": percent,
    }


def _local_ip():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        sock.close()


def _uptime_sec():
    parts = _read_first_line("/proc/uptime").split()
    try:
        return round(float(parts[0]), 1)
    except (IndexError, ValueError):
        return None


def _load_avg():
    try:
        return [round(value, 2) for value in os.getloadavg()]
    except (AttributeError, OSError):
        return []


def machine_state():
    """采集 agent 所在机器的运行状态；单字段失败时返回 null，不抛异常。"""
    ip = _local_ip()
    cpu_percent, rx_rate, tx_rate = _cpu_and_net_sample()
    return {
        "hostname": socket.gethostname() or None,
        "ip": ip,
        "os": _os_info(),
        "cpu": {
            "model": _cpu_model(),
            "cores": os.cpu_count(),
            "usage_percent": cpu_percent,
            "temperature_c": _cpu_temperature(),
            "load_avg": _load_avg(),
        },
        "memory": _memory_info(),
        "disk": _disk_info("/"),
        "network": {
            "rx_bytes_per_sec": rx_rate,
            "tx_bytes_per_sec": tx_rate,
        },
        "uptime_sec": _uptime_sec(),
    }


def topic_for(device, resource):
    return "walle/{}/device/{}/{}".format(server_tenant(),
                                          sanitize_segment(device), resource)


# ---------------- 意图解析 ----------------

def _normalize(text):
    s = (text or "").strip().lower()
    s = s.translate(str.maketrans("０１２３４５６７８９．", "0123456789."))
    s = s.replace(" ", "").replace("，", ",").replace("。", ".")
    return s


def _cn_number(token):
    token = (token or "").strip().replace("两", "二")
    if not token:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", token):
        return float(token)
    if token == "半":
        return 0.5
    if "百" in token:
        left, right = token.split("百", 1)
        hundreds = _cn_number(left) if left else 1
        rest = _cn_number(right) if right else 0
        if hundreds is None or rest is None:
            return None
        return hundreds * 100 + rest
    if "十" in token:
        left, right = token.split("十", 1)
        tens = _cn_number(left) if left else 1
        ones = _cn_number(right) if right else 0
        if tens is None or ones is None:
            return None
        return tens * 10 + ones
    total = 0.0
    for ch in token:
        if ch not in _CN_DIGITS:
            return None
        total = total * 10 + _CN_DIGITS[ch]
    return total


def _distance(token, unit):
    value = _cn_number(token)
    if value is None:
        return None
    if unit in ("厘米", "cm"):
        value = value / 100.0
    return value


def _speed(text):
    m = re.search(r"(?:以|速度)\s*" + _NUM_RE + r"\s*(?:米每秒|m/s|米/秒)", text)
    if not m:
        return None
    return _cn_number(m.group(1))


def _human_bytes(value):
    """字节数转可读文本；非法/缺失返回 None。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(number) < 1024.0 or unit == "TB":
            return "{:.1f}{}".format(number, unit)
        number /= 1024.0
    return None


def _human_duration(seconds):
    """秒数转“1天2小时3分”；非法/缺失返回 None。"""
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return None
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days:
        parts.append("{}天".format(days))
    if hours:
        parts.append("{}小时".format(hours))
    if minutes or not parts:
        parts.append("{}分".format(minutes))
    return "".join(parts)


DISCOVER_WORDS = ("刷新设备", "发现设备", "扫描设备", "设备列表",
                  "有哪些设备", "查找设备", "搜设备")
SERVO_NAMES = {1: "上下", 2: "左右", 3: "ID3", 4: "ID4"}
SERVO_MIN = 800
SERVO_MAX = 2200
SERVO_CENTER = 1500
SERVO_STEP = 100


def _record_segment(text):
    """从“开始录像30分钟/60秒”中解析 segment_sec；默认 0（用设备默认值）。"""
    m = re.search(r"(\d+)\s*(分钟|分|秒)", text or "")
    if not m:
        return 0
    value = int(m.group(1))
    if m.group(2) in ("分钟", "分"):
        value *= 60
    return max(0, min(value, 86400))


_RECORD_FILE_RE = re.compile(
    r"([A-Za-z0-9_.-]+\.(?:mp4|fmp4|mkv|avi|mov|flv))", re.IGNORECASE)


def _record_filename(text):
    """从“删除/播放录像 xxx.mp4”中提取文件名。"""
    match = _RECORD_FILE_RE.search(text or "")
    return match.group(1) if match else None


def _record_target(intent, text):
    """补充录像文件名或 latest 标记。"""
    filename = _record_filename(text)
    if filename:
        intent["filename"] = filename
    elif any(w in text for w in ("最新", "最后一个", "最后一条")):
        intent["latest"] = True
    return intent


def _servo_error_text(result):
    error = result.get("error")
    if error == "mqtt_not_connected":
        return "MQTT 未连接"
    if error in ("control_occupied", "control_denied"):
        holder = result.get("control_holder") or {}
        who = holder.get("client_name") or holder.get("client_id") or "其他客户端"
        return "设备控制权已被 {} 占用".format(who)
    if error == "unsupported":
        return result.get("cause") or "设备不支持舵机"
    return result.get("cause") or error or "未知错误"


# ---------------- 任务意图（M3-04）----------------

_TASK_TIME_RE = re.compile(
    r"(?:(凌晨|早上|上午|中午|下午|傍晚|晚上)\s*)?"
    r"([0-9]{1,2}|[零一二三四五六七八九十]{1,3})\s*(?:点|时|:|：)\s*"
    r"(半|[0-9]{1,2})?")


def _task_times(text):
    """从自然语言中提取 (hour, minute)，支持“晚上9点”“8点半”。"""
    times = []
    for match in _TASK_TIME_RE.finditer(text):
        period, hour_text, minute_text = match.groups()
        hour = _cn_number(hour_text)
        if hour is None:
            continue
        hour = int(hour)
        if minute_text == "半":
            minute = 30
        elif minute_text:
            try:
                minute = int(minute_text)
            except ValueError:
                continue
        else:
            minute = 0
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            continue
        if period in ("下午", "傍晚", "晚上") and hour < 12:
            hour += 12
        elif period == "中午" and hour < 11:
            hour += 12
        if hour > 23:
            continue
        times.append((hour, minute))
    return times


def _fmt_task_time(value):
    return "{:02d}:{:02d}".format(value[0], value[1])


def _parse_task_intent(text):
    """识别任务管理/创建意图；不是任务语句时返回 (None, None)。"""
    if any(word in text for word in (
            "查看任务", "任务列表", "有哪些任务", "我的任务", "列出任务")):
        return {"action": "task_list"}, None

    for action, words in (
            ("task_cancel", ("取消任务", "删除任务")),
            ("task_pause", ("暂停任务",)),
            ("task_resume", ("恢复任务", "继续任务"))):
        for word in words:
            if word not in text:
                continue
            target = text.replace(word, "", 1).strip(" :：,，.。")
            intent = {"action": action}
            if target:
                if re.fullmatch(r"task-[0-9a-fA-F]{8}", target):
                    intent["task_id"] = target
                else:
                    intent["task_name"] = target
            return intent, None

    if "录像" not in text:
        return None, None

    interval = re.search(
        r"每隔\s*([0-9]+|[零一二两三四五六七八九十]+)\s*(小时|分钟|秒)", text)
    if interval:
        count = _cn_number(interval.group(1))
        if count and count > 0:
            unit = interval.group(2)
            seconds = int(count * {"小时": 3600, "分钟": 60, "秒": 1}[unit])
            return {
                "action": "task_create",
                "name": "每隔{}{}录像".format(int(count), unit),
                "schedule": {"type": "interval", "seconds": seconds},
                "actions": [{"phase": "start", "tool": "record.start"}],
            }, None

    times = _task_times(text)
    if not times:
        if any(word in text for word in ("每天", "每日", "明天", "工作日", "任务")):
            return None, ("任务时间没识别出来。"
                          "请说“每天8点开始录像，晚上9点停止”。")
        return None, None

    if len(times) >= 2:
        start, stop = times[0], times[1]
        if "工作日" in text:
            schedule = {"type": "daily_window", "start": _fmt_task_time(start),
                        "stop": _fmt_task_time(stop),
                        "weekdays": [0, 1, 2, 3, 4]}
            name = "工作日 {}–{} 录像".format(
                _fmt_task_time(start), _fmt_task_time(stop))
        elif "每天" in text or "每日" in text:
            schedule = {"type": "daily_window", "start": _fmt_task_time(start),
                        "stop": _fmt_task_time(stop)}
            name = "每天 {}–{} 录像".format(
                _fmt_task_time(start), _fmt_task_time(stop))
        else:
            return None, "一次任务暂只支持一个开始时间。"
        return {
            "action": "task_create", "name": name, "schedule": schedule,
            "actions": [{"phase": "start", "tool": "record.start"},
                        {"phase": "stop", "tool": "record.stop"}],
        }, None

    hour, minute = times[0]
    if "明天" in text:
        tomorrow = (task_schedule.now_in_timezone(
            task_schedule.DEFAULT_TIMEZONE) + timedelta(days=1)).date()
        schedule = {"type": "once",
                    "at": "{} {:02d}:{:02d}:00".format(
                        tomorrow.isoformat(), hour, minute)}
        name = "明天 {:02d}:{:02d} 录像".format(hour, minute)
    elif "工作日" in text:
        schedule = {"type": "cron",
                    "expr": "{} {} * * 1-5".format(minute, hour)}
        name = "工作日 {:02d}:{:02d} 录像".format(hour, minute)
    elif "每天" in text or "每日" in text:
        schedule = {"type": "cron",
                    "expr": "{} {} * * *".format(minute, hour)}
        name = "每天 {:02d}:{:02d} 录像".format(hour, minute)
    else:
        return None, "请说明是每天、明天还是工作日执行。"
    return {
        "action": "task_create", "name": name, "schedule": schedule,
        "actions": [{"phase": "start", "tool": "record.start"}],
    }, None


def parse_intent(text):
    """返回 (intent, error)。intent 为 None 时 error 给出可读原因。"""
    t = _normalize(text)
    if not t:
        return None, "请输入指令，例如：前进3米、左转90度、停止。"

    if re.fullmatch(r"(停止|停|急停|刹车|别动|stop)", t) or "急停" in t:
        return {"action": "stop"}, None

    task_intent, task_error = _parse_task_intent(t)
    if task_intent is not None:
        return task_intent, task_error
    if task_error:
        return None, task_error

    if any(w in t for w in DISCOVER_WORDS):
        return {"action": "device_discover"}, None
    if any(w in t for w in ("告警", "报警", "最新告警", "有没有告警", "alarm")):
        return {"action": "alarm_status"}, None
    if any(w in t for w in ("停止播放", "结束播放", "关闭播放", "停止回放")):
        return {"action": "record_play_stop"}, None
    if (("播放" in t or "回放" in t)
            and ("录像" in t or "视频" in t)):
        return _record_target({"action": "record_play_start"}, t), None
    if (("删除" in t or "删掉" in t)
            and ("录像" in t or "视频" in t) and "任务" not in t):
        return _record_target({"action": "record_delete"}, t), None
    if any(w in t for w in ("开始录像", "开始录制", "开启录像", "打开录像",
                             "帮我录像", "录一段", "录一下")):
        return {"action": "record_start",
                "segment_sec": _record_segment(t)}, None
    if any(w in t for w in ("停止录像", "停止录制", "结束录像",
                             "关闭录像", "关闭录制", "别录了", "停录")):
        return {"action": "record_stop"}, None
    if any(w in t for w in ("查看录像", "录像列表", "有哪些录像",
                             "录像文件", "录像记录", "查录像", "查一下录像")):
        return {"action": "record_list"}, None
    if any(w in t for w in ("开始推流", "开始直播", "开启推流", "打开推流")):
        return {"action": "stream_start"}, None
    if any(w in t for w in ("停止推流", "停止直播", "结束推流",
                             "关闭推流", "关播")):
        return {"action": "stream_stop"}, None
    if any(w in t for w in ("舵机", "云台")):
        if any(w in t for w in ("回中", "居中", "中位", "复位")):
            return {"action": "servo_center"}, None
        m = re.search(r"舵机\s*([1-4])\s*(?:到|为|至|设成|设置为)?\s*(\d{3,4})", t)
        if m:
            return {"action": "servo_set", "duration": 1.0,
                    "servos": [{"id": int(m.group(1)),
                                "position": int(m.group(2))}]}, None
        if any(w in t for w in ("状态", "位置", "查询", "多少")):
            return {"action": "servo_status"}, None
    if any(w in t for w in ("抬高", "升高", "抬一点")):
        return {"action": "servo_relative", "servo_id": 1,
                "delta": SERVO_STEP}, None
    if any(w in t for w in ("降低", "放低", "降一点")):
        return {"action": "servo_relative", "servo_id": 1,
                "delta": -SERVO_STEP}, None
    if any(w in t for w in ("舵机", "云台")) and any(
            w in t for w in ("往上一点", "往上抬", "向上一点")):
        return {"action": "servo_relative", "servo_id": 1,
                "delta": SERVO_STEP}, None
    if any(w in t for w in ("舵机", "云台")) and any(
            w in t for w in ("往下一点", "往下压", "向下一点")):
        return {"action": "servo_relative", "servo_id": 1,
                "delta": -SERVO_STEP}, None
    if ("舵机" in t or "云台" in t or "摄像头" in t) and any(
            w in t for w in ("向左一点", "左一点")):
        return {"action": "servo_relative", "servo_id": 2,
                "delta": -SERVO_STEP}, None
    if ("舵机" in t or "云台" in t or "摄像头" in t) and any(
            w in t for w in ("向右一点", "右一点")):
        return {"action": "servo_relative", "servo_id": 2,
                "delta": SERVO_STEP}, None

    if (("agent" in t and ("状态" in t or "status" in t or "状况" in t))
            or any(w in t for w in ("你的状态", "你自己状态", "自身状态"))):
        return {"action": "agent_status"}, None
    if any(w in t for w in ("服务健康", "服务状态", "健康检查", "health")):
        return {"action": "service_health"}, None

    motion_words = ("前进", "向前", "往前", "前行", "后退", "向后", "往后", "倒车",
                    "左移", "右移", "左转", "右转")
    if (any(w in t for w in ("设备状态", "设备信息", "设备情况", "cpu", "内存",
                             "磁盘", "运行多久", "运行时间"))
            and not any(w in t for w in motion_words)):
        return {"action": "device_base_state"}, None
    if (any(w in t for w in ("录像", "录制", "推流", "直播", "电量", "电池",
                             "battery", "声呐", "前方多远", "前方距离", "障碍",
                             "控制权", "谁在控制", "谁持有", "在做什么",
                             "正在做什么", "运行状态", "车状态"))
            and not any(w in t for w in motion_words)):
        return {"action": "device_run_state"}, None
    if re.search(r"(状态|电量|battery|status)", t) and not any(w in t for w in motion_words):
        return {"action": "status"}, None

    # 旋转：左转/右转 N 度
    for direction, words in (("left", ("左转", "向左转", "左旋")),
                             ("right", ("右转", "向右转", "右旋"))):
        for word in words:
            m = re.search(word + r"\s*" + _NUM_RE + r"\s*(?:度|°|deg)?", t)
            if m:
                angle = _cn_number(m.group(1))
                if angle is None:
                    return None, "没有识别出角度，请说“左转90度”。"
                return {"action": "rotate", "direction": direction,
                        "angle_deg": angle}, None
    if any(w in t for w in ("左转", "右转")):
        return None, "请告诉我要转多少度，例如“左转90度”。"

    # 平移：左移/右移 N 米
    for direction, words in (("left", ("左移", "向左平移", "往左平移")),
                             ("right", ("右移", "向右平移", "往右平移"))):
        for word in words:
            m = re.search(word + r"\s*" + _NUM_RE + r"\s*(米|m|厘米|cm)?", t)
            if m:
                dist = _distance(m.group(1), m.group(2))
                if dist is None:
                    return None, "没有识别出距离，请说“左移1米”。"
                return {"action": "move", "direction": direction,
                        "distance_m": dist, "speed_mps": _speed(t)}, None
        if any(w in t for w in words):
            return None, "请告诉我要{}多少米。".format(words[0])

    # 前进/后退 N 米
    for direction, words in (("forward", ("前进", "向前", "往前", "往前走", "前行")),
                             ("backward", ("后退", "向后", "往后", "倒车", "退后"))):
        for word in words:
            m = re.search(word + r"\s*" + _NUM_RE + r"\s*(米|m|厘米|cm)?", t)
            if m:
                dist = _distance(m.group(1), m.group(2))
                if dist is None:
                    return None, "没有识别出距离，请说“前进3米”。"
                return {"action": "move", "direction": direction,
                        "distance_m": dist, "speed_mps": _speed(t)}, None
        if any(w in t for w in words):
            return None, "请告诉我要{}多少米。".format(words[0])

    return None, "我暂时只支持：前进N米、后退N米、左移/右移N米、左转/右转N度、停止、状态。"


def validate_intent(intent, cfg):
    """返回 (intent, warning)。校验失败时 intent=None，warning 为原因。"""
    if intent["action"] in ("stop", "status", "device_base_state",
                            "device_run_state", "device_discover",
                            "agent_status", "service_health", "alarm_status",
                            "record_start", "record_stop", "record_list",
                            "record_delete", "record_play_start",
                            "record_play_stop",
                            "stream_start", "stream_stop", "servo_status",
                            "servo_set", "servo_center", "servo_relative",
                            "task_list", "task_create", "task_cancel",
                            "task_pause", "task_resume"):
        return intent, None

    max_distance = float(cfg["MAX_DISTANCE"])
    max_speed = float(cfg["MAX_SPEED"])
    min_speed = float(cfg.get("MIN_SPEED", "0.5"))
    max_angle = float(cfg["MAX_ANGLE"])
    warn = []

    if intent["action"] == "move":
        dist = float(intent.get("distance_m") or 0)
        if dist <= 0:
            return None, "距离必须大于 0 米。"
        if dist > max_distance:
            return None, "单次距离不能超过 {} 米。".format(max_distance)
        speed = intent.get("speed_mps")
        if speed is None:
            speed = float(cfg["DEFAULT_SPEED"])
        speed = float(speed)
        if speed < min_speed:
            warn.append("速度已调整为最低 {} m/s".format(min_speed))
        if speed > max_speed:
            warn.append("速度已限制为 {} m/s".format(max_speed))
        speed = max(min_speed, min(speed, max_speed))
        intent["speed_mps"] = round(speed, 3)
        intent["distance_m"] = round(dist, 3)

    if intent["action"] == "rotate":
        angle = float(intent.get("angle_deg") or 0)
        if angle <= 0:
            return None, "角度必须大于 0 度。"
        if angle > max_angle:
            return None, "单次角度不能超过 {} 度。".format(max_angle)
        intent["angle_deg"] = round(angle, 2)

    return intent, ("；".join(warn) if warn else None)


# ---------------- Agent ----------------

class MotionSession:
    """单台设备的一次运动会话；不同设备各自独立。"""

    def __init__(self, device, intent, client_name):
        self.device = device
        self.intent = dict(intent)
        self.client_name = client_name
        self.request_id = None
        self.abort = threading.Event()
        self.thread = None
        self.stop_owns_cleanup = False
        self.resource_lock = None


class Agent:
    def __init__(self, cfg):
        self.cfg = cfg
        self.tenant = server_tenant()
        self.mqtt_configured = bool(cfg.get("MQTT_USER") and cfg.get("MQTT_PASSWORD"))
        self.mqtt_connected = False
        self.mqtt_error = None
        self.mqtt_client = None
        # device_id -> {last_result, last_publish, online, online_ts}
        self.devices = {}
        self.dev_lock = threading.Lock()
        # device_id -> MotionSession；每台设备独立工作线程
        self.sessions = {}
        self.last_motion_error = None
        self.state_lock = threading.Lock()
        # device_id -> {resource_name: Lock}；仅同资源串行（motion/servo），
        # 摄像头相关动作（录像/推流/回放）按设计允许并行，不使用 camera 锁
        self.resource_locks = {}
        self.resource_lock = threading.Lock()
        # device_id -> lease；每台设备独立持有控制权
        self.control_held = {}
        self.control_lock = threading.Lock()
        # 通用请求-应答总线：control/record/stream/state 等一对一请求统一走这里
        self.request_bus = RequestBus(
            publish=self._publish,
            topic_for=topic_for,
            default_timeout=6.0,
            is_online=self._device_online,
            decode=decode_result,
            request_id_factory=new_request_id,
        )
        # 租户级 discovery 收集器
        self._discovery_lock = threading.Lock()
        self._discovery_results = {}
        self._discovery_until = 0.0
        self._discovery_running = False
        # 告警提示（最近一次）与运动意图（供障碍/低压联锁判断）
        self.alarm_lock = threading.Lock()
        self.last_alarm_notice = None
        self.motion_intent = None
        self.chat_enabled = cfg.get("CHAT_MQTT_ENABLED", "1") == "1"
        self.chat_requests = 0
        self.chat_errors = 0
        self.chat_last = None
        # 当前线程正在处理的 chat request_id，长动作据此主动上报进度（§4.25）
        self.progress_local = threading.local()
        # M3-01：任务库懒加载，避免只做对话时也创建 SQLite 文件
        self.task_store = None
        self.task_store_lock = threading.Lock()
        # M3-05：任务调度器，由 main() 显式启动，测试可单独控制
        self.task_scheduler = None
        self.task_scheduler_lock = threading.Lock()
        self._connect_mqtt()

    def _dev(self, device):
        with self.dev_lock:
            item = self.devices.get(device)
            if item is None:
                item = {
                    "last_result": None,
                    "last_decode": None,
                    "last_publish": None,
                    "online": None,
                    "online_ts": None,
                    "device_id": None,
                    "hostname": None,
                    "ip": None,
                    "model": None,
                    "device_type": None,
                    "os_description": None,
                    "version_name": None,
                    "timestamp": None,
                    # M1-02 设备状态缓存
                    "baseState": None,
                    "baseState_ts": None,
                    "runState": None,
                    "runState_ts": None,
                    "recording": None,
                    "stream": None,
                    "phone_audio": None,
                    "motor": None,
                    "servo": None,
                    "battery": None,
                    "sonar": None,
                    "control_holder": None,
                    "control_holder_ts": None,
                    "last_alarm": None,
                    "last_alarm_ts": None,
                    "alarm_groups": None,
                    "alarm_groups_ts": None,
                }
                self.devices[device] = item
            return item

    def _device_online(self, device):
        """未知在线状态按在线处理（fail-open），明确 online=false 才拒绝发送。"""
        with self.dev_lock:
            item = self.devices.get(device)
            online = item.get("online") if item else None
        return online is not False

    @staticmethod
    def _reset_device_state(item):
        """设备重启/离线时清空状态，避免沿用旧值。"""
        for key in ("baseState", "baseState_ts", "runState", "runState_ts",
                    "recording", "stream", "phone_audio", "motor", "servo",
                    "battery", "sonar", "control_holder", "control_holder_ts",
                    "alarm_groups", "alarm_groups_ts"):
            item[key] = None

    def _update_device_cache(self, item, topic, payload, now=None):
        """根据设备主动上报/应答更新 M1-02 缓存。"""
        if not isinstance(payload, dict):
            return
        now = time.time() if now is None else now
        with self.dev_lock:
            if topic.endswith("/online"):
                self._update_online(item, payload, now)
            elif topic.endswith("/alarm/report"):
                self._update_alarm(item, payload, now)
            elif topic.endswith("/baseState/query/response"):
                self._update_base_state(item, payload, now)
            elif topic.endswith("/runState/query/response"):
                self._update_run_state(item, payload, now)
            elif (topic.endswith("/control/acquire/response")
                  or topic.endswith("/control/release/response")):
                self._update_control_holder(item, payload, now)

    def _update_online(self, item, payload, now):
        online = payload.get("online")
        if online is not True and online is not False:
            return
        previous = item.get("online")
        new_device_id = payload.get("device_id") or item.get("device_id")
        device_changed = bool(
            item.get("device_id") and new_device_id
            and item["device_id"] != new_device_id)
        if online is False or device_changed or (
                previous is False and online is True):
            self._reset_device_state(item)
        item["online"] = online
        item["online_ts"] = now
        item["device_id"] = new_device_id
        item["hostname"] = payload.get("hostname") or item.get("hostname")
        item["ip"] = payload.get("ip") or item.get("ip")
        for key in ("model", "device_type", "os_description", "version_name"):
            if payload.get(key):
                item[key] = payload[key]
        item["timestamp"] = payload.get("timestamp")

    def _update_base_state(self, item, payload, now):
        item["baseState"] = copy.deepcopy(payload)
        item["baseState_ts"] = now
        for key in ("device_id", "hostname", "ip"):
            if payload.get(key):
                item[key] = payload[key]

    def _update_run_state(self, item, payload, now):
        item["runState"] = copy.deepcopy(payload)
        item["runState_ts"] = now
        for name in _RUN_GROUP_DEFAULTS:
            item[name] = copy.deepcopy(payload.get(name))
        if "control_holder" in payload:
            item["control_holder"] = copy.deepcopy(payload.get("control_holder"))
            item["control_holder_ts"] = now
        for key in ("device_id", "hostname", "ip"):
            if payload.get(key):
                item[key] = payload[key]

    def _update_alarm(self, item, payload, now):
        item["last_alarm"] = copy.deepcopy(payload)
        item["last_alarm_ts"] = now
        alarm_type = str(payload.get("alarm_type") or "")
        detail = payload.get("detail")
        detail = detail if isinstance(detail, dict) else {}
        touched = []
        if alarm_type == "battery_low" and detail.get("value") is not None:
            group = dict(item.get("battery") or {})
            group["voltage_v"] = detail.get("value")
            group["available"] = True
            item["battery"] = group
            touched.append("battery")
        elif alarm_type == "obstacle_near":
            group = dict(item.get("sonar") or {})
            if detail.get("distance_cm") is not None:
                group["distance_cm"] = detail.get("distance_cm")
            group["available"] = True
            item["sonar"] = group
            touched.append("sonar")
        if "video" in alarm_type or "stream" in alarm_type:
            group = dict(item.get("stream") or {})
            group["streaming"] = False
            group["reason"] = payload.get("message")
            item["stream"] = group
            touched.append("stream")
        if "audio" in alarm_type:
            group = dict(item.get("phone_audio") or {})
            group["playing"] = False
            group["reason"] = payload.get("message")
            item["phone_audio"] = group
            touched.append("phone_audio")
        touched_existing = set(item.get("alarm_groups") or [])
        touched_existing.update(touched)
        item["alarm_groups"] = sorted(touched_existing)
        item["alarm_groups_ts"] = now

    def _handle_alarm(self, device, payload):
        """设备主动告警：记录提示，障碍/严重低压时尝试停车。"""
        if not isinstance(payload, dict):
            return
        notice = {
            "device": device,
            "event": payload.get("event"),
            "level": payload.get("level"),
            "alarm_type": payload.get("alarm_type"),
            "message": payload.get("message"),
            "detail": copy.deepcopy(payload.get("detail")),
            "ts": time.time(),
            "action": None,
        }
        with self.alarm_lock:
            self.last_alarm_notice = notice
        log().warning("设备告警 device=%s type=%s level=%s message=%s",
                      device, notice["alarm_type"], notice["level"],
                      notice["message"])
        event = notice["event"]
        alarm_type = str(notice["alarm_type"] or "")
        level = str(notice["level"] or "")
        if event == "alarm" and (alarm_type == "obstacle_near"
                                 or level == "critical"):
            threading.Thread(
                target=self._alarm_stop,
                args=(device, alarm_type == "obstacle_near"),
                daemon=True, name="alarm-stop").start()

    def _agent_moving(self, device, forward_only=False):
        with self.state_lock:
            session = self.sessions.get(device)
            if session is None:
                return False
            moving = bool(session.thread is not None
                          and session.thread.is_alive()
                          and not session.abort.is_set())
            intent = session.intent
        if not moving:
            return False
        if forward_only:
            return (isinstance(intent, dict)
                    and intent.get("action") == "move"
                    and intent.get("direction") == "forward")
        return True

    def _alarm_stop(self, device, forward_only):
        if not self._agent_moving(device, forward_only):
            return
        result = self.stop("device_alarm", device)
        with self.alarm_lock:
            if self.last_alarm_notice is not None:
                self.last_alarm_notice["action"] = "stopped"
                self.last_alarm_notice["stop_result"] = result

    def recent_alarm(self, device=None):
        """返回最近一次告警，供对话提示。"""
        with self.dev_lock:
            if device:
                item = self.devices.get(device)
                alarm = (copy.deepcopy(item.get("last_alarm"))
                         if item else None)
            else:
                best = None
                best_ts = -1.0
                for item_device, item in self.devices.items():
                    ts = item.get("last_alarm_ts")
                    if ts is not None and ts > best_ts:
                        best = (item_device, item.get("last_alarm"))
                        best_ts = ts
                alarm = copy.deepcopy(best[1]) if best else None
                device = best[0] if best else device
        if not isinstance(alarm, dict):
            return {
                "ok": True,
                "action": "alarm_status",
                "device": device,
                "alarm": None,
                "reply": "该设备暂无告警。" if device else "暂无告警。",
            }
        message = alarm.get("message") or alarm.get("alarm_type") or "未知告警"
        level = alarm.get("level")
        reply = "最近告警：{}".format(message)
        if level:
            reply += "（{}）".format(level)
        return {
            "ok": True,
            "action": "alarm_status",
            "device": device,
            "alarm": alarm,
            "reply": reply,
        }

    def _update_control_holder(self, item, payload, now):
        if "control_holder" in payload:
            item["control_holder"] = copy.deepcopy(payload.get("control_holder"))
            item["control_holder_ts"] = now

    def _record_discovery(self, device, payload):
        """discovery 收集窗口内记录设备身份应答。"""
        if not isinstance(payload, dict):
            return
        now = time.monotonic()
        with self._discovery_lock:
            if now > self._discovery_until:
                return
            self._discovery_results[device] = {
                "payload": copy.deepcopy(payload),
                "ts": time.time(),
            }

    @staticmethod
    def _normalize_run_group(name, group):
        """is_supported=false / available=false 时清空该分组的值，避免沿用旧值。"""
        if not isinstance(group, dict):
            return None
        result = copy.deepcopy(group)
        if (result.get("is_supported") is False
                or result.get("available") is False):
            for key, default in _RUN_GROUP_DEFAULTS.get(name, {}).items():
                result[key] = default
        return result

    @staticmethod
    def _fresh_payload(payload, ts, now):
        if not isinstance(payload, dict) or ts is None:
            return None
        if now - float(ts) > DEVICE_STATE_TTL_SEC:
            return None
        return copy.deepcopy(payload)

    def device_state(self, device, now=None):
        """返回设备状态快照；过期或离线时状态分组为 None。"""
        now = time.time() if now is None else now
        with self.dev_lock:
            item = self.devices.get(device)
            data = copy.deepcopy(item) if item is not None else None
        if data is None:
            return None

        online = data.get("online")
        base_state = self._fresh_payload(
            data.get("baseState"), data.get("baseState_ts"), now)
        run_state = self._fresh_payload(
            data.get("runState"), data.get("runState_ts"), now)
        if online is False:
            base_state = None
            run_state = None

        snapshot = {
            "device": device,
            "device_id": data.get("device_id"),
            "hostname": data.get("hostname"),
            "ip": data.get("ip"),
            "online": online,
            "online_ts": data.get("online_ts"),
            "baseState": base_state,
            "runState": run_state,
            "control_holder": None,
            "last_alarm": data.get("last_alarm"),
            "last_alarm_ts": data.get("last_alarm_ts"),
        }
        if run_state is None:
            alarm_ts = data.get("last_alarm_ts")
            alarm_fresh = (alarm_ts is not None
                           and now - float(alarm_ts) <= DEVICE_STATE_TTL_SEC)
            if not alarm_fresh:
                for name in _RUN_GROUP_DEFAULTS:
                    snapshot[name] = None
            else:
                touched = set(data.get("alarm_groups") or [])
                for name in _RUN_GROUP_DEFAULTS:
                    snapshot[name] = (
                        self._normalize_run_group(name, data.get(name))
                        if name in touched else None)
        else:
            for name in _RUN_GROUP_DEFAULTS:
                group = data.get(name)
                if group is None:
                    group = run_state.get(name)
                snapshot[name] = self._normalize_run_group(name, group)
            holder_ts = data.get("control_holder_ts")
            holder = data.get("control_holder")
            if holder_ts is None:
                holder = run_state.get("control_holder")
            elif now - float(holder_ts) > DEVICE_STATE_TTL_SEC:
                holder = None
            snapshot["control_holder"] = holder
        if online is False:
            snapshot["control_holder"] = None
            for name in _RUN_GROUP_DEFAULTS:
                snapshot[name] = None
        return snapshot

    @staticmethod
    def _topic_device(topic):
        """从 walle/{tenant}/device/{device}/{resource} 中取出 device。"""
        parts = str(topic or "").split("/")
        if len(parts) >= 5 and parts[0] == "walle" and parts[2] == "device":
            return parts[3]
        return None

    # ---- MQTT ----
    def _connect_mqtt(self):
        if not self.mqtt_configured:
            log().info("MQTT 账号未配置，仅做解析与记录")
            return
        if mqtt is None:
            self.mqtt_error = "paho_missing"
            log().error("paho-mqtt 不可用，无法连接 MQTT")
            return
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                 client_id=mqtt_client_id(),
                                 clean_session=True)
            client.username_pw_set(self.cfg["MQTT_USER"], self.cfg["MQTT_PASSWORD"])
            client.on_connect = self._on_connect
            client.on_disconnect = self._on_disconnect
            client.on_message = self._on_message
            # 遗嘱：异常掉线时由 broker 代发 retained status，前端/客户立刻看到离线
            if self.chat_enabled:
                client.will_set(
                    agent_status_topic(),
                    json.dumps(self._status_payload(False), ensure_ascii=False),
                    qos=1, retain=True,
                )
            client.reconnect_delay_set(min_delay=3, max_delay=30)
            client.enable_logger()
            if self.cfg["MQTT_TLS"] == "1":
                client.tls_set()
            client.connect_async(self.cfg["MQTT_HOST"], int(self.cfg["MQTT_PORT"]), 60)
            client.loop_start()
            self.mqtt_client = client
            log().info("MQTT 连接中 %s:%s user=%s tls=%s",
                       self.cfg["MQTT_HOST"], self.cfg["MQTT_PORT"],
                       self.cfg["MQTT_USER"], self.cfg["MQTT_TLS"])
        except Exception as exc:  # noqa: BLE001
            self.mqtt_error = str(exc)
            log().error("MQTT 初始化失败: %s", exc)

    def shutdown(self):
        """退出前补发 retained 离线状态，再正常断开。

        MQTT 规定客户端正常 DISCONNECT 时 broker 会丢弃遗嘱，
        所以 Ctrl+C / SIGTERM 这类正常退出必须显式发布 online:false。
        """
        bus = getattr(self, "request_bus", None)
        if bus is not None:
            bus.fail_all("agent_shutdown", "agent 退出")
        scheduler = getattr(self, "task_scheduler", None)
        if scheduler is not None:
            try:
                scheduler.stop()
            except Exception as exc:  # noqa: BLE001
                log().warning("停止任务调度器失败: %s", exc)
            self.task_scheduler = None
        store = getattr(self, "task_store", None)
        if store is not None:
            try:
                store.close()
            except Exception as exc:  # noqa: BLE001
                log().warning("关闭任务库失败: %s", exc)
            self.task_store = None
        client = self.mqtt_client
        if client is None:
            return
        if self.chat_enabled and self.mqtt_connected:
            try:
                info = client.publish(
                    agent_status_topic(),
                    json.dumps(self._status_payload(False), ensure_ascii=False),
                    qos=1, retain=True)
                info.wait_for_publish(timeout=3)
                log().info("已发布离线状态 %s", agent_status_topic())
            except Exception as exc:  # noqa: BLE001
                log().warning("发布离线状态失败: %s", exc)
        try:
            client.disconnect()
            client.loop_stop()
        except Exception as exc:  # noqa: BLE001
            log().warning("断开 MQTT 失败: %s", exc)

    def _status_payload(self, online=True):
        return {
            "agent_id": agent_identity(), "agent_name": agent_name(),
            "tenant": self.tenant,
            "online": bool(online),
            "version_code": AGENT_VERSION_CODE, "version_name": AGENT_VERSION,
            "ts": int(time.time()),
            "capabilities": {
                "chat": True,
                "actions": ["move", "rotate", "stop", "status"],
                "min_speed_mps": float(self.cfg["MIN_SPEED"]),
                "max_speed_mps": float(self.cfg["MAX_SPEED"]),
                "max_distance_m": float(self.cfg["MAX_DISTANCE"]),
                "max_angle_deg": float(self.cfg["MAX_ANGLE"]),
                "llm": self.cfg["LLM_ENABLED"] == "1",
            },
        }

    def _subscriptions(self):
        """agent 需要订阅的主题。

        request / response 均按资源层级用通配符覆盖，新增同层功能时无需改订阅；
        新增更深层级时在这里补一条模式。
        """
        prefix = "walle/{}/device/+".format(self.tenant)
        subs = [
            (prefix + "/online", 1),
            (prefix + "/alarm/report", 1),         # 电池低压 / 前方障碍 / 媒体中断
            (prefix + "/+/+/response", 1),         # 2 层资源：baseState/query/response、stream/start/response、control/acquire/response
            (prefix + "/+/+/+/response", 1),       # 3 层：record/list/query/response、car/motor/set/response
            (prefix + "/+/+/+/+/response", 1),     # 4 层：car/servo/status/query/response
        ]
        # agent 自身的请求按层级用通配符覆盖，新增同层功能无需改订阅：
        #   +/request      → chat/request
        #   +/+/request    → baseState/query、config/modify、chat/stop、task/create
        #   +/+/+/request  → task/list/query
        agent_prefix = "walle/{}/agent/{}".format(self.tenant, agent_identity())
        subs.append((agent_prefix + "/+/request", 1))
        subs.append((agent_prefix + "/+/+/request", 1))
        subs.append((agent_prefix + "/+/+/+/request", 1))
        return subs

    @staticmethod
    def _debug_subscriptions(subscriptions):
        """WALLE_AGENT_DEBUG=1 时，在启动/重连后打印实际订阅主题。"""
        if not DEBUG_TRAFFIC:
            return
        print("walle-agent subscriptions:", flush=True)
        for topic, qos in subscriptions:
            print("  SUB {} qos={}".format(topic, qos), flush=True)

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        ok = (reason_code == 0) or (getattr(reason_code, "value", reason_code) == 0)
        if ok:
            self.mqtt_connected = True
            self.mqtt_error = None
            # 通配到租户下所有设备：一次订阅覆盖多设备，避免按设备动态订阅/退订的竞态。
            # 代价是也会收到同租户其他设备的消息，按 topic 中的 device 分流即可。
            subscriptions = self._subscriptions()
            client.subscribe(subscriptions)
            self._debug_subscriptions(subscriptions)
            if self.chat_enabled:
                self._publish_topic(agent_status_topic(),
                                    self._status_payload(True), retain=True)
            _announce_ready(self)
            log().info("MQTT 已连接，订阅 %s", subscriptions)
        else:
            self.mqtt_connected = False
            self.mqtt_error = "connect_rejected:{}".format(reason_code)
            _trace("IN", "CONNECT_REJECTED", {"reason": str(reason_code)}, 0, False)
            log().error("MQTT 连接被拒绝: %s", reason_code)

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        self.mqtt_connected = False
        bus = getattr(self, "request_bus", None)
        if bus is not None:
            bus.fail_all("mqtt_disconnected", "MQTT 连接断开")
        log().warning("MQTT 断开: %s", reason_code)

    def _on_message(self, client, userdata, msg):
        _trace("IN", msg.topic, msg.payload, msg.qos, msg.retain)
        # agent 自身请求：订阅用通配符覆盖，这里按资源名分流
        agent_prefix = "walle/{}/agent/{}/".format(self.tenant, agent_identity())
        if msg.topic.startswith(agent_prefix):
            resource = msg.topic[len(agent_prefix):]
            if resource == "chat/request":
                if self.chat_enabled:
                    self._on_chat_message(msg)
                else:
                    log().debug("忽略 chat/request：chat 未启用")
            elif resource == "baseState/query/request":
                self._on_agent_state_request(msg)
            elif resource == "config/modify/request":
                self._on_agent_config_request(msg)
            elif resource == "chat/stop/request":
                self._on_agent_stop_request(msg)
            elif resource.startswith("task/") and resource.endswith("/request"):
                self._on_task_request(msg, resource)
            else:
                log().debug("忽略未处理的 agent 主题: %s", msg.topic)
            return
        device = self._topic_device(msg.topic)
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception:  # noqa: BLE001
            payload = msg.payload.decode("utf-8", errors="replace")[:200]
        if not device:
            log().warning("忽略无法解析设备段的主题: %s", msg.topic)
            return
        if isinstance(payload, dict) and payload.get("error_code") == "control_denied":
            with self.control_lock:
                self.control_held.pop(device, None)
            # 运动帧的 -5 由 _on_motor_response 统一处理，避免 5Hz 重复告警
            if not msg.topic.endswith("/car/motor/set/response"):
                log().warning("设备拒绝控制 device=%s request_id=%s holder=%s",
                              device, payload.get("request_id"),
                              (payload.get("control_holder") or {}).get("client_id") or "-")
        item = self._dev(device)
        record = {"topic": msg.topic, "payload": payload,
                  "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
        item["last_result"] = record
        decoded = None
        if msg.topic.endswith("/response"):
            decoded = decode_result(payload)
            item["last_decode"] = decoded
        # 一对一 response 交给 RequestBus 按 request_id 唤醒等待者；
        # car/motor/set 是 5Hz 持续帧，不进入总线。
        if (msg.topic.endswith("/response")
                and not msg.topic.endswith("/car/motor/set/response")):
            self.request_bus.on_message(device, msg.topic, payload)
        if msg.topic.endswith("/car/motor/set/response"):
            self._on_motor_response(device, payload, decoded)
        self._update_device_cache(item, msg.topic, payload)
        if msg.topic.endswith("/alarm/report"):
            self._handle_alarm(device, payload)
        if msg.topic.endswith("/discovery/query/response"):
            self._record_discovery(device, payload)
        if (isinstance(payload, dict) and "online" in msg.topic
                and payload.get("online") is False):
            self.request_bus.fail_device(device, "device_offline", "设备离线")
        if msg.topic.endswith("/car/motor/set/response"):
            log().debug("MQTT 收到 %s %s", msg.topic,
                        json.dumps(payload, ensure_ascii=False)[:500])
        else:
            log().info("MQTT 收到 %s %s", msg.topic,
                       json.dumps(payload, ensure_ascii=False)[:500])

    def _on_motor_response(self, device, payload, decoded):
        """运动帧应答：0 忽略；-3/-5/-8 立即中止本次运动。

        只处理当前运动会话的 request_id，避免迟到的旧应答影响下一次运动。
        """
        if not decoded or decoded.get("error") not in (
                "control_denied", "safety", "unavailable"):
            return
        rid = (str(payload.get("request_id") or "")
               if isinstance(payload, dict) else "")
        with self.state_lock:
            session = self.sessions.get(device)
            if session is None:
                return
            if not session.request_id or (
                    rid and rid != session.request_id):
                return
            if session.abort.is_set():
                return
            session.abort.set()
            self.last_motion_error = {
                "device": device,
                "request_id": rid,
                "result": decoded.get("result"),
                "error": decoded.get("error"),
                "cause": decoded.get("cause"),
                "holder": decoded.get("holder"),
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        holder = decoded.get("holder") or {}
        log().warning(
            "运动被设备拒绝，停止发送 device=%s result=%s cause=%s holder=%s",
            device, decoded.get("result"), decoded.get("cause"),
            holder.get("client_name") or holder.get("client_id") or "-")

    # ---- agent 基本状态 ----
    def _on_agent_state_request(self, msg):
        payload = self._decode_payload(msg)
        if not isinstance(payload, dict):
            payload = {}
        # CPU 使用率需要约 0.2s 采样，放线程里避免阻塞 MQTT 收包
        threading.Thread(target=self._serve_agent_state, args=(payload,),
                         daemon=True, name="agent-state").start()

    def _serve_agent_state(self, payload):
        request_id = str(payload.get("request_id") or "")
        response = {
            "result": 0,
            "cause": "ok",
            "request_id": request_id,
            "agent_id": agent_identity(),
            "agent_name": agent_name(),
            "version_code": AGENT_VERSION_CODE,
            "version_name": AGENT_VERSION,
            "tenant": self.tenant,
        }
        try:
            response.update(machine_state())
        except Exception as exc:  # noqa: BLE001
            log().warning("采集机器状态失败: %s", exc)
            response.update({"result": -6, "cause": "采集机器状态失败: %s" % exc})
        response["timestamp"] = int(time.time())
        response["datetime"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._publish_topic(agent_base_state_topic("response"), response)

    # ---- agent 配置修改（当前仅支持 agent_name）----
    def _on_agent_config_request(self, msg):
        payload = self._decode_payload(msg)
        if not isinstance(payload, dict):
            payload = {}
        request_id = str(payload.get("request_id") or "")
        # 发起方身份：仅用于审计日志，不参与名称校验与应答字段
        client_id = str(payload.get("client_id") or "").strip()
        client_name = str(payload.get("client_name") or "").strip()
        response = {
            "result": 0,
            "cause": "ok",
            "request_id": request_id,
            "agent_id": agent_identity(),
            "agent_name": agent_name(),
            "tenant": self.tenant,
        }
        if "agent_name" not in payload:
            response.update({"result": -1, "cause": "请求里没有 agent_name"})
        else:
            name, reason = normalize_agent_name(payload.get("agent_name"))
            if name is None:
                response.update({"result": -2, "cause": reason})
            else:
                try:
                    set_agent_name(name)
                    response["agent_name"] = name
                except OSError as exc:
                    response.update({"result": -6,
                                     "cause": "保存 agent_name 失败: %s" % exc})
        log_line = ("config modify request_id=%s client_id=%s client_name=%s "
                    "result=%s cause=%s agent_name=%s")
        log_args = (request_id or "-", client_id or "-", client_name or "-",
                    response["result"], response["cause"], response["agent_name"])
        if response["result"] == 0:
            log().info(log_line, *log_args)
        else:
            log().warning(log_line, *log_args)
        response["timestamp"] = int(time.time())
        response["datetime"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._publish_topic(agent_config_topic("response"), response)
        if response["result"] == 0 and self.chat_enabled:
            # 名称变化后立即重发 retained online，控制台无需等重连即可看到新名字
            self._publish_topic(agent_status_topic(),
                                self._status_payload(True), retain=True)

    # ---- agent 会话停止 ----
    def _on_agent_stop_request(self, msg):
        payload = self._decode_payload(msg)
        if not isinstance(payload, dict):
            payload = {}
        # 停止流程要等控制权应答，不能阻塞 MQTT 收包线程
        threading.Thread(target=self._serve_agent_stop, args=(payload,),
                         daemon=True, name="agent-stop").start()

    def _serve_agent_stop(self, payload):
        request_id = str(payload.get("request_id") or "").strip()
        client_id = str(payload.get("client_id") or "").strip()
        client_name = str(payload.get("client_name") or "").strip()
        device = str(payload.get("device") or "").strip()
        response = {
            "result": 0,
            "cause": "ok",
            "request_id": request_id,
            "agent_id": agent_identity(),
            "tenant": self.tenant,
            "device": None,
            "stopped": False,
            "released": False,
        }
        if not request_id:
            response.update({"result": -1, "cause": "请求里没有 request_id"})
        else:
            res = self.stop_session(client_name or client_id or "unknown",
                                    device or None)
            if not res.get("ok"):
                error = res.get("error")
                if error == "mqtt_not_connected":
                    response.update({"result": -3, "cause": "MQTT 未连接"})
                else:
                    response.update({"result": -6,
                                     "cause": res.get("cause")
                                              or res.get("note") or "停止失败"})
            else:
                publish = res.get("result") or {}
                if publish and publish.get("published") is False:
                    response.update({"result": -3,
                                     "cause": "零速指令未下发：MQTT 未连接",
                                     "device": res.get("device"),
                                     "stopped": bool(res.get("stopped")),
                                     "released": bool(res.get("released"))})
                else:
                    response.update({
                        "device": res.get("device"),
                        "stopped": bool(res.get("stopped")),
                        "released": bool(res.get("released")),
                        "cause": res.get("cause") or res.get("note") or "ok",
                    })
        response["timestamp"] = int(time.time())
        response["datetime"] = time.strftime("%Y-%m-%d %H:%M:%S")
        log_line = ("agent stop request_id=%s client_id=%s client_name=%s "
                    "device=%s result=%s stopped=%s released=%s")
        log_args = (request_id or "-", client_id or "-", client_name or "-",
                    response.get("device") or "-", response["result"],
                    response.get("stopped"), response.get("released"))
        if response["result"] == 0:
            log().info(log_line, *log_args)
        else:
            log().warning(log_line, *log_args)
        self._publish_topic(agent_stop_topic("response"), response)

    # ---- 任务管理（M3-04）----
    TASK_REQUEST_ACTIONS = {
        "task/list/query/request": "list/query",
        "task/create/request": "create",
        "task/cancel/request": "cancel",
        "task/pause/request": "pause",
        "task/resume/request": "resume",
    }
    TASK_ERROR_RESULTS = {
        "bad_param": -2,
        "parameter_error": -2,
        "no_future": -2,
        "ambiguous": -2,
        "unsupported": -3,
        "capability_unsupported": -3,
        "mqtt_not_connected": -3,
        "not_found": -3,
        "capability_unknown": -6,
    }

    def _on_task_request(self, msg, resource):
        payload = self._decode_payload(msg)
        if not isinstance(payload, dict):
            payload = {}
        # 创建任务可能要查 runState，放线程里避免阻塞 MQTT 收包
        threading.Thread(target=self._serve_task_request,
                         args=(resource, payload), daemon=True,
                         name="agent-task").start()

    @staticmethod
    def _task_response(payload, result=0, cause="ok", **extra):
        response = {
            "result": result,
            "cause": cause,
            "request_id": str(payload.get("request_id") or "").strip(),
            "agent_id": agent_identity(),
            "tenant": server_tenant(),
            "timestamp": int(time.time()),
            "datetime": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        response.update(extra)
        return response

    def _publish_task_response(self, action, response):
        self._publish_topic(agent_task_topic(action, "response"), response)

    def _serve_task_request(self, resource, payload):
        action = self.TASK_REQUEST_ACTIONS.get(resource)
        if action is None:
            return
        if not str(payload.get("request_id") or "").strip():
            self._publish_task_response(
                action, self._task_response(payload, -1,
                                            "请求里没有 request_id"))
            return
        if action == "list/query":
            out = self.task_list(payload.get("device") or None)
            self._publish_task_response(
                action, self._task_response(
                    payload, 0, "ok",
                    device=out.get("device"),
                    tasks=out.get("tasks") or [],
                    count=out.get("count") or 0))
            return
        if action == "create":
            out = self.task_create(
                payload.get("name"), payload.get("device"),
                payload.get("schedule"), payload.get("actions"),
                payload.get("timezone"), enabled=payload.get("enabled", True))
        elif action == "cancel":
            out = self.task_cancel(payload.get("task_id"),
                                   payload.get("task_name"))
        elif action == "pause":
            out = self.task_pause(payload.get("task_id"),
                                  payload.get("task_name"))
        else:
            out = self.task_resume(payload.get("task_id"),
                                   payload.get("task_name"))
        if not out.get("ok"):
            result = self.TASK_ERROR_RESULTS.get(out.get("error"), -6)
            self._publish_task_response(
                action, self._task_response(
                    payload, result,
                    out.get("cause") or out.get("error") or "任务操作失败",
                    task_id=out.get("task_id"),
                    task=out.get("task")))
            return
        extra = {}
        for key in ("task", "task_id", "first_run_at", "first_phase",
                    "next_run_at", "task_name"):
            if out.get(key) is not None:
                extra[key] = out[key]
        self._publish_task_response(
            action, self._task_response(payload, 0, "ok", **extra))

    # ---- 对话层（用户 ↔ agent）----
    @staticmethod
    def _decode_payload(msg):
        try:
            return json.loads(msg.payload.decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {"text": msg.payload.decode("utf-8", errors="replace")[:200]}

    def _publish_topic(self, topic, payload, retain=False):
        """按完整 topic 发布（对话/状态），与设备 _publish 分开。"""
        if not (self.mqtt_client and self.mqtt_connected):
            log().warning("MQTT 未连接，丢弃 %s", topic)
            return {"published": False, "error": "mqtt_not_connected", "topic": topic}
        _trace("OUT", topic, payload, 1, retain)
        info = self.mqtt_client.publish(topic, json.dumps(payload, ensure_ascii=False),
                                        qos=1, retain=retain)
        log().info("publish %s %s rc=%s", topic,
                   json.dumps(payload, ensure_ascii=False)[:500], info.rc)
        return {"published": info.rc == 0, "topic": topic, "rc": info.rc}

    def _on_chat_message(self, msg):
        payload = self._decode_payload(msg)
        if not isinstance(payload, dict):
            payload = {"text": str(payload)}
        log().info("chat request payload=%s",
                   json.dumps(payload, ensure_ascii=False)[:500])
        # 在独立线程里执行：意图解析/运动下发可能耗时数秒，不能阻塞 MQTT 收包线程
        threading.Thread(target=self._run_chat_request,
                         args=(payload,), daemon=True).start()

    def _run_chat_request(self, payload):
        self.chat_requests += 1
        request_id = str(payload.get("request_id") or "").strip()
        client_id = str(payload.get("client_id") or "").strip()
        client_name = str(payload.get("client_name") or "").strip()
        device = str(payload.get("device") or "").strip()
        text = str(payload.get("text") or "").strip()
        response = {"request_id": request_id, "agent_id": agent_identity(),
                    "device": device, "ts": int(time.time())}
        self.progress_local.request_id = request_id or None
        try:
            missing = None
            is_discover = any(w in _normalize(text) for w in DISCOVER_WORDS)
            normalized_text = _normalize(text)
            no_device_ok = (
                is_discover
                or ("agent" in normalized_text
                    and any(w in normalized_text
                            for w in ("状态", "status", "状况")))
                or any(w in normalized_text
                       for w in ("你的状态", "你自己状态", "自身状态"))
                or any(w in normalized_text
                       for w in ("服务健康", "服务状态", "健康检查", "health"))
                or any(w in normalized_text
                       for w in ("告警", "报警", "alarm"))
                or any(w in normalized_text
                       for w in ("查看任务", "任务列表", "列出任务",
                                 "有哪些任务", "我的任务", "取消任务",
                                 "删除任务", "暂停任务", "恢复任务",
                                 "继续任务"))
            )
            if not client_id:
                missing = "client_id"
            elif not client_name:
                missing = "client_name"
            elif not request_id:
                missing = "request_id"
            elif not device and not no_device_ok:
                missing = "device"
            elif not text:
                missing = "text"
            if missing:
                reply = {
                    "client_id": "请求里没有 client_id。",
                    "client_name": "请求里没有 client_name。",
                    "request_id": "请求里没有 request_id。",
                    "device": "请求里没有指定设备（device 字段为空）。",
                    "text": "请求里没有文本内容。",
                }[missing]
                result = {"ok": False, "error": "invalid", "reply": reply}
            else:
                result = handle_chat(client_name, device, text)
            response["ok"] = bool(result.get("ok"))
            response["reply"] = result.get("reply")
            for key in ("intent", "warning", "error", "action", "duration_sec"):
                if result.get(key) is not None:
                    response[key] = result[key]
            data = _chat_data(result)
            if data:
                response["data"] = data
            raw = _chat_device_result(result)
            if raw is not None:
                response["result"] = raw
        except Exception as exc:  # noqa: BLE001
            self.chat_errors += 1
            response.update({"ok": False, "reply": "agent 内部错误：{}".format(exc),
                             "error": "agent_exception"})
            log().error("chat request %s 处理失败: %s", request_id, exc)
        finally:
            self.progress_local.request_id = None
        self.chat_last = {"request_id": request_id, "client_id": client_id,
                          "client_name": client_name, "device": device,
                          "text": text, "ok": response["ok"],
                          "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
        self._publish_topic(chat_topic("response"), response)

    # ---- 会话进度上报（长动作/任务使用，§4.25）----
    PROGRESS_STAGES = ("accepted", "started", "progress", "waiting",
                       "completed", "failed", "stopped")

    def report_progress(self, request_id, stage, device=None,
                        progress=None, message=None, cause=None):
        """上报会话关键进度节点（主动上报，不 retained）。"""
        request_id = str(request_id or "").strip()
        stage = str(stage or "").strip()
        if not request_id or stage not in self.PROGRESS_STAGES:
            log().warning("忽略非法进度上报 request_id=%s stage=%s",
                          request_id or "-", stage or "-")
            return {"published": False, "error": "invalid_progress"}
        payload = {
            "agent_id": agent_identity(),
            "request_id": request_id,
            "stage": stage,
            "timestamp": int(time.time()),
            "datetime": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if device:
            payload["device"] = str(device)
        if progress is not None:
            try:
                payload["progress"] = max(0, min(100, int(progress)))
            except (TypeError, ValueError):
                pass
        if message:
            payload["message"] = str(message)
        if cause:
            payload["cause"] = str(cause)
        return self._publish_topic(agent_progress_topic(), payload)

    def _chat_request_id(self):
        """当前线程处理的 chat request_id；非对话线程返回 None。"""
        return getattr(self.progress_local, "request_id", None)

    def _report_action_progress(self, stage, device=None, message=None,
                                progress=None, cause=None):
        """长动作上报进度；没有对话上下文（如测试/直调）时静默跳过。"""
        request_id = self._chat_request_id()
        if not request_id:
            return None
        return self.report_progress(request_id, stage, device=device,
                                    progress=progress, message=message,
                                    cause=cause)

    def task_db(self):
        """任务库（M3-01）：首次访问时创建，默认 walle-agent/data/agent.db。"""
        if self.task_store is None:
            with self.task_store_lock:
                if self.task_store is None:
                    self.task_store = TaskStore(self.cfg.get("TASK_DB_PATH"))
        return self.task_store

    # ---- 任务管理（M3-04）----
    @staticmethod
    def _task_public(task):
        """对外任务对象：只暴露 task_id（内部主键 id 不进入协议）。"""
        item = dict(task)
        item["task_id"] = item.pop("id", None)
        return item

    @staticmethod
    def _task_default_name(schedule):
        kind = schedule.get("type")
        if kind == "daily_window":
            prefix = ("工作日" if schedule.get("weekdays") == [0, 1, 2, 3, 4]
                      else "每天")
            return "{} {}–{} 录像".format(
                prefix, schedule["start"], schedule["stop"])
        if kind == "once":
            return "{} 录像".format(schedule["at"])
        if kind == "cron":
            return "定时录像（{}）".format(schedule["expr"])
        return "每隔 {} 秒录像".format(schedule.get("seconds"))

    @staticmethod
    def _validate_task_actions(schedule, actions):
        if not isinstance(actions, (list, tuple)) or not actions:
            raise ValueError("actions 不能为空")
        clean = []
        for item in actions:
            if not isinstance(item, dict):
                raise ValueError("actions 元素必须是对象")
            phase = str(item.get("phase") or "").strip()
            tool = str(item.get("tool") or "").strip()
            if phase not in TASK_PHASE_TOOLS:
                raise ValueError("phase 只能是 start/stop")
            expected = TASK_PHASE_TOOLS[phase]
            if tool != expected:
                raise ValueError("{} 阶段的动作必须是 {}".format(phase, expected))
            clean.append({"phase": phase, "tool": expected})
        if schedule.get("type") == "daily_window":
            if {item["phase"] for item in clean} != {"start", "stop"}:
                raise ValueError("daily_window 需要 start 和 stop 两个动作")
        elif len(clean) != 1:
            raise ValueError("该任务类型只支持一个动作阶段")
        return clean

    def task_list(self, device=None, enabled=None):
        store = self.task_db()
        tasks = store.list_tasks(enabled=enabled)
        if device:
            tasks = [task for task in tasks if task.get("device") == device]
        for task in tasks:
            runs = store.list_runs(task["id"], limit=1)
            task["last_run"] = runs[0] if runs else None
        tasks = [self._task_public(task) for task in tasks]
        return {"ok": True, "action": "task_list", "device": device or None,
                "tasks": tasks, "count": len(tasks)}

    def task_create(self, name, device, schedule, actions, timezone=None,
                    enabled=True):
        device = str(device or "").strip()
        name = str(name or "").strip()
        if not device:
            return {"ok": False, "action": "task_create",
                    "error": "bad_param", "cause": "缺少设备 device"}
        try:
            schedule = task_schedule.validate_schedule(schedule)
            actions = self._validate_task_actions(schedule, actions)
        except ValueError as exc:
            return {"ok": False, "action": "task_create",
                    "error": "bad_param", "cause": str(exc)}
        preflight = self.preflight_recording_task(device)
        if not preflight.get("ok"):
            return {"ok": False, "action": "task_create",
                    "error": preflight.get("error") or "capability_unknown",
                    "cause": preflight.get("cause") or "无法确认设备录像能力",
                    "capabilities": preflight.get("capabilities")}
        timezone = str(timezone or self.cfg.get("TASK_TIMEZONE")
                       or task_schedule.DEFAULT_TIMEZONE)
        name = name or self._task_default_name(schedule)
        draft = {"id": "pending", "name": name, "device": device,
                 "timezone": timezone, "schedule": schedule,
                 "actions": actions, "next_run_at": None}
        nxt = task_schedule.next_occurrence(draft)
        if nxt is None:
            return {"ok": False, "action": "task_create",
                    "error": "no_future", "cause": "该计划没有下一次执行时间"}
        task = self.task_db().create_task(
            name, device, schedule, actions, timezone=timezone,
            enabled=enabled, next_run_at=nxt["at"])
        return {"ok": True, "action": "task_create", "task": self._task_public(task),
                "task_id": task["id"], "first_run_at": nxt["at"],
                "first_phase": nxt["phase"], "next_run_at": nxt["at"]}

    def _resolve_task(self, task_id=None, task_name=None):
        store = self.task_db()
        task_id = str(task_id or "").strip()
        if task_id:
            task = store.get_task(task_id)
            if task is None:
                return None, {"ok": False, "error": "not_found",
                              "cause": "任务不存在：{}".format(task_id)}
            return task, None
        name = str(task_name or "").strip()
        if not name:
            return None, {"ok": False, "error": "bad_param",
                          "cause": "请提供任务名称或 task_id"}
        tasks = store.list_tasks()
        matches = [task for task in tasks if task["name"] == name]
        if not matches:
            matches = [task for task in tasks if name in task["name"]]
        if not matches:
            return None, {"ok": False, "error": "not_found",
                          "cause": "没有找到任务：{}".format(name)}
        if len(matches) > 1:
            return None, {"ok": False, "error": "ambiguous",
                          "cause": "匹配到多个任务，请使用 task_id"}
        return matches[0], None

    def task_cancel(self, task_id=None, task_name=None):
        task, error = self._resolve_task(task_id, task_name)
        if error:
            error.update({"action": "task_cancel"})
            return error
        deleted = self.task_db().delete_task(task["id"])
        if not deleted:
            return {"ok": False, "action": "task_cancel",
                    "error": "not_found", "cause": "任务不存在"}
        return {"ok": True, "action": "task_cancel", "task_id": task["id"],
                "task_name": task["name"], "task": self._task_public(task)}

    def task_pause(self, task_id=None, task_name=None):
        task, error = self._resolve_task(task_id, task_name)
        if error:
            error.update({"action": "task_pause"})
            return error
        if task.get("enabled"):
            task = self.task_db().update_task(task["id"], enabled=False)
        return {"ok": True, "action": "task_pause",
                "task": self._task_public(task),
                "task_id": task["id"], "task_name": task["name"]}

    def task_resume(self, task_id=None, task_name=None):
        task, error = self._resolve_task(task_id, task_name)
        if error:
            error.update({"action": "task_resume"})
            return error
        if task.get("enabled"):
            return {"ok": True, "action": "task_resume",
                    "task": self._task_public(task),
                    "task_id": task["id"], "task_name": task["name"],
                    "next_run_at": task.get("next_run_at")}
        nxt = task_schedule.next_occurrence(task)
        if nxt is None:
            return {"ok": False, "action": "task_resume",
                    "error": "no_future", "cause": "该计划没有下一次执行时间",
                    "task_id": task["id"]}
        task = self.task_db().update_task(
            task["id"], enabled=True, next_run_at=nxt["at"])
        return {"ok": True, "action": "task_resume",
                "task": self._task_public(task),
                "task_id": task["id"], "task_name": task["name"],
                "next_run_at": nxt["at"], "first_run_at": nxt["at"]}

    # ---- 任务执行（M3-05）----
    TASK_RETRYABLE_ERRORS = {"timeout", "failed", "control_failed",
                             "mqtt_not_connected"}
    TASK_TERMINAL_ERRORS = {"no_space", "unavailable", "unsupported",
                            "capability_unsupported", "control_occupied",
                            "control_denied", "resource_busy",
                            "parameter_error", "bad_param", "not_found"}

    def start_task_scheduler(self):
        """启动任务调度线程；由 main() 调用，测试可单独调用。"""
        with self.task_scheduler_lock:
            if self.task_scheduler is None:
                mode = str(self.cfg.get("TASK_CONTROL_MODE") or "shared").lower()
                if mode != "shared":
                    log().warning(
                        "TASK_CONTROL_MODE=%s 暂未实现，按 shared 执行", mode)
                try:
                    self.reconcile_tasks()
                except Exception as exc:  # noqa: BLE001 - 对账失败不阻止调度
                    log().exception("任务启动对账失败: %s", exc)
                self.task_scheduler = TaskScheduler(
                    self.task_db(), self._dispatch_task, tick_sec=1.0,
                    timezone=self.cfg.get("TASK_TIMEZONE"))
            self.task_scheduler.start()
            return self.task_scheduler

    def _publish_agent_alarm(self, alarm_type, metric, message, detail=None,
                             level="warning", event="alarm"):
        """agent 任务告警；统一使用 alarm_source_* 字段（§4.7）。"""
        payload = {
            "alarm_source_type": "agent",
            "alarm_source_id": agent_identity(),
            "alarm_source_name": agent_name(),
            "ip": _local_ip(),
            "event": event,
            "level": level,
            "metric": metric,
            "alarm_type": alarm_type,
            "message": message,
            "timestamp": int(time.time()),
            "datetime": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if detail:
            payload["detail"] = detail
        return self._publish_topic(agent_alarm_topic(), payload)

    def _publish_task_event(self, task, stage, run_id=None, phase=None,
                            progress=None, message=None, cause=None,
                            result=None, device=None):
        """任务事件上报（§4.27）：started/completed/failed/skipped。"""
        task = task or {}
        payload = {
            "agent_id": agent_identity(),
            "task_id": task.get("id"),
            "stage": stage,
            "timestamp": int(time.time()),
            "datetime": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if run_id:
            payload["run_id"] = run_id
        device = device or task.get("device")
        if device:
            payload["device"] = device
        if phase:
            payload["phase"] = phase
        if progress is not None:
            payload["progress"] = progress
        if message:
            payload["message"] = message
        if cause:
            payload["cause"] = cause
        if result is not None:
            payload["result"] = result
        return self._publish_topic(agent_task_report_topic(), payload)

    @staticmethod
    def _task_outcome(out):
        """把动作结果转换为事件 stage 与 cause。"""
        if out.get("ok"):
            return "completed", None
        error = out.get("error") or "failed"
        cause = out.get("cause") or error
        if error in ("control_occupied", "control_denied"):
            return "skipped", cause
        return "failed", cause

    @staticmethod
    def _task_event_message(task, phase, out, stage, cause):
        task = task or {}
        name = task.get("name") or task.get("id") or "任务"
        action = "开始录像" if phase == "start" else "停止录像"
        if stage == "completed":
            text = "任务「{}」{}成功".format(name, action)
            if out.get("filename"):
                text += "：{}".format(out["filename"])
            elif out.get("already_recording"):
                text += "（已在录制）"
            elif out.get("already_stopped"):
                text += "（当前未录制）"
            return text
        if stage == "skipped":
            return "任务「{}」被跳过：{}".format(name, cause)
        return "任务「{}」{}失败：{}".format(name, action, cause)

    def _dispatch_task(self, task, scheduled_at):
        threading.Thread(target=self._run_task, args=(task, scheduled_at),
                         daemon=True, name="agent-task-run").start()
        return {"dispatched": True, "task_id": task.get("id")}

    def _task_retry_max(self):
        try:
            return max(0, int(self.cfg.get("TASK_RETRY_MAX", "2")))
        except (TypeError, ValueError):
            return 2

    def _task_retry_backoff(self):
        try:
            return max(0.0, float(self.cfg.get("TASK_RETRY_BACKOFF_SEC", "2")))
        except (TypeError, ValueError):
            return 2.0

    def _task_action_with_retry(self, device, phase):
        """执行一次录像开始/停止，按 M3-08 规则重试。

        `-4`（已在录/未录制）已在 record_start/record_stop 里归一为成功；
        只有 timeout/failed/control_failed/mqtt_not_connected 会按
        `TASK_RETRY_MAX` 与 `TASK_RETRY_BACKOFF_SEC` 线性退避重试。
        """
        attempts = 0
        max_retries = self._task_retry_max()
        while True:
            try:
                out = (self.record_start(device) if phase == "start"
                       else self.record_stop(device))
            except Exception as exc:  # noqa: BLE001 - 任务线程不能因单次异常退出
                log().exception("任务执行异常 device=%s phase=%s",
                                device, phase)
                out = {"ok": False, "error": "agent_exception",
                       "cause": str(exc)}
            if out.get("ok"):
                return out
            error = out.get("error")
            if (error in self.TASK_TERMINAL_ERRORS
                    or error not in self.TASK_RETRYABLE_ERRORS):
                return out
            if attempts >= max_retries:
                return out
            attempts += 1
            log().warning(
                "任务动作失败，第 %s 次重试 device=%s phase=%s error=%s",
                attempts, device, phase, error)
            time.sleep(self._task_retry_backoff() * attempts)

    def _run_task(self, task, scheduled_at):
        """执行一个到期阶段，记录 task_runs 并推进 next_run_at。"""
        task_id = task.get("id")
        store = self.task_db()
        fresh = store.get_task(task_id)
        if not fresh or not fresh.get("enabled"):
            return
        if fresh.get("next_run_at") != scheduled_at:
            return
        phase = task_schedule.phase_for_slot(fresh, scheduled_at) or "unknown"
        if store.get_run_by_slot(task_id, phase, scheduled_at):
            return
        started_at = time.strftime("%Y-%m-%d %H:%M:%S")
        run = store.record_run(task_id, phase, scheduled_at=scheduled_at,
                               started_at=started_at)
        if not run or run.get("started_at") != started_at:
            return  # 并发/重复分发，已有线程在处理
        run_id = run["run_id"]
        if phase not in ("start", "stop"):
            self._finish_task_run(store, run_id, result=None,
                                  cause="无法确定任务阶段",
                                  error="phase_unknown")
            self._publish_task_event(
                fresh, "skipped", run_id=run_id,
                message="任务「{}」阶段无法识别，已跳过".format(
                    fresh.get("name") or task_id),
                cause="phase_unknown")
            self._advance_task(store, fresh)
            return

        self._publish_task_event(
            fresh, "started", run_id=run_id, phase=phase, progress=0,
            message="任务「{}」开始执行：{}".format(
                fresh.get("name") or task_id,
                "开始录像" if phase == "start" else "停止录像"))

        out = self._task_action_with_retry(fresh["device"], phase)
        error = None if out.get("ok") else (out.get("error") or "failed")
        cause = out.get("cause") or ("ok" if out.get("ok") else error)
        holder = out.get("control_holder") or {}
        if holder:
            who = (holder.get("client_name") or holder.get("client_id")
                   or "其他客户端")
            cause = "{}（控制权：{}）".format(cause, who)
            log().warning("任务控制权被占用 task_id=%s phase=%s holder=%s",
                          task_id, phase, who)
        if error in ("control_occupied", "control_denied"):
            who = (holder.get("client_name") or holder.get("client_id")
                   or "其他客户端")
            detail = {"device": fresh.get("device"), "task_id": task_id,
                      "phase": phase}
            if holder:
                detail["holder"] = holder
            self._publish_agent_alarm(
                "control_occupied", "control",
                "设备控制权被 {} 占用，任务「{}」未执行".format(
                    who, fresh.get("name") or task_id),
                detail)
        if not out.get("ok"):
            log().warning("任务执行失败 task_id=%s phase=%s error=%s cause=%s",
                          task_id, phase, error, cause)
        self._finish_task_run(store, run_id, request_id=out.get("request_id"),
                              result=out.get("result"), cause=cause,
                              error=error)
        stage, event_cause = self._task_outcome(out)
        if stage != "completed":
            event_cause = cause  # 带上 holder 等补充信息
        self._publish_task_event(
            fresh, stage, run_id=run_id, phase=phase,
            progress=100 if stage == "completed" else None,
            message=self._task_event_message(
                fresh, phase, out, stage, event_cause),
            cause=event_cause, result=out.get("result"))
        self._advance_task(store, fresh)

    @staticmethod
    def _finish_task_run(store, run_id, **fields):
        fields["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            store.update_run(run_id, **fields)
        except Exception as exc:  # noqa: BLE001
            log().warning("更新任务执行记录失败 run_id=%s: %s", run_id, exc)

    def _advance_task(self, store, task):
        nxt = task_schedule.advance_task(store, task)
        if nxt is None:
            store.update_task(task["id"], enabled=False)
        return nxt

    # ---- 重启对账（M3-07）----

    def _task_missed_grace(self):
        try:
            return max(0, int(self.cfg.get("TASK_MISSED_GRACE_SEC", "300")))
        except (TypeError, ValueError):
            return 300

    def reconcile_tasks(self, now=None):
        """启动时重算 next_run_at，并按 runState 补录像开始/停止（M3-07）。

        - `next_run_at` 已错过且在 `TASK_MISSED_GRACE_SEC` 内：立即补执行；
        - 超过宽限、或 daily_window 的开始窗口已结束：记 `missed` 并跳过；
        - 其余启用任务重算 `next_run_at`；
        - 按设备对账：窗口内未录补启动；窗口外仍在录且由任务启动时补停止。
        """
        store = self.task_db()
        base_now = task_schedule.now_in_timezone(
            self.cfg.get("TASK_TIMEZONE"), now)
        grace = self._task_missed_grace()
        summary = {"grace_sec": grace, "tasks": [], "devices": []}
        devices = []
        for task in store.list_tasks(enabled=True):
            if task["device"] not in devices:
                devices.append(task["device"])
            try:
                summary["tasks"].append(
                    self._reconcile_task(task, base_now, grace))
            except Exception as exc:  # noqa: BLE001 - 单个任务异常不能中断对账
                log().exception("任务对账失败 task_id=%s", task.get("id"))
                summary["tasks"].append({
                    "task_id": task.get("id"), "action": "error",
                    "reason": str(exc)})
        for device in devices:
            try:
                summary["devices"].append(
                    self._reconcile_device_recording(device, base_now))
            except Exception as exc:  # noqa: BLE001
                log().exception("设备录像对账失败 device=%s", device)
                summary["devices"].append({
                    "device": device, "action": "error", "reason": str(exc)})
        log().info("任务启动对账完成 %s",
                   json.dumps(summary, ensure_ascii=False))
        return summary

    def _reconcile_task(self, task, base_now, grace):
        """重算单个任务的 next_run_at，必要时补执行错过的触发点。"""
        store = self.task_db()
        current = task_schedule.now_in_timezone(task.get("timezone"), base_now)
        now_text = current.strftime(task_schedule.TIME_FORMAT)
        stored = str(task.get("next_run_at") or "").strip()
        item = {"task_id": task["id"], "device": task["device"],
                "previous": stored or None, "action": "keep",
                "next_run_at": stored or None}
        scheduled = None
        if stored:
            try:
                scheduled = task_schedule.parse_time(
                    stored, task.get("timezone"))
            except ValueError:
                scheduled = None
        if scheduled is not None and scheduled <= current:
            late = int((current - scheduled).total_seconds())
            phase = task_schedule.phase_for_slot(task, stored)
            reason = None
            if late > grace:
                reason = "错过触发点 {} 秒，超过宽限 {} 秒".format(late, grace)
            elif phase not in ("start", "stop"):
                reason = "无法识别触发阶段"
            elif (phase == "start"
                  and task_schedule.in_daily_window(task, current) is False):
                reason = "录像窗口已结束"
            if reason:
                run = self._record_missed_run(
                    task, stored, phase, reason, now_text)
                if run is not None:
                    self._publish_task_event(
                        task, "skipped", run_id=run.get("run_id"),
                        phase=phase,
                        message="任务「{}」错过触发：{}".format(
                            task.get("name") or task.get("id"), reason),
                        cause=reason)
                nxt = self._advance_task(store, task)
                item.update({
                    "action": "missed", "reason": reason,
                    "next_run_at": nxt["at"] if nxt else None,
                })
                log().warning("任务错过触发 task_id=%s slot=%s：%s",
                              task.get("id"), stored, reason)
                return item
            if store.get_run_by_slot(task["id"], phase, stored):
                # 该 slot 已执行过但 next_run_at 未推进（进程在执行中退出），补推进
                self._advance_task(store, task)
            else:
                self._run_task(task, stored)
            fresh = store.get_task(task["id"])
            item.update({
                "action": "catch_up", "late_sec": late, "phase": phase,
                "next_run_at": fresh.get("next_run_at") if fresh else None,
            })
            return item
        nxt = task_schedule.next_occurrence(task, current)
        if nxt is None:
            store.update_task(task["id"], enabled=False)
            item.update({"action": "disabled", "next_run_at": None})
        elif nxt["at"] != stored:
            store.update_task(task["id"], next_run_at=nxt["at"])
            item.update({"action": "rescheduled", "next_run_at": nxt["at"]})
        return item

    def _record_missed_run(self, task, scheduled_at, phase, reason, now_text):
        """记录错过的触发点；同一 slot 已执行过时保留原结果。"""
        store = self.task_db()
        phase = phase or "unknown"
        if store.get_run_by_slot(task["id"], phase, scheduled_at):
            return None
        run = store.record_run(task["id"], phase, scheduled_at=scheduled_at,
                               started_at=now_text)
        if not run or run.get("started_at") != now_text:
            return None
        store.update_run(run["run_id"], finished_at=now_text,
                         error="missed", cause=reason)
        return run

    def _begin_reconcile_run(self, task, phase, base_now):
        """为启动对账创建 task_runs 记录，返回 (run_id, now_text)。"""
        if task is None:
            return None, None
        store = self.task_db()
        now_text = task_schedule.now_in_timezone(
            task.get("timezone"), base_now).strftime(task_schedule.TIME_FORMAT)
        if store.get_run_by_slot(task["id"], phase, now_text):
            return None, now_text
        run = store.record_run(task["id"], phase, scheduled_at=now_text,
                               started_at=now_text)
        return (run or {}).get("run_id"), now_text

    def _finish_reconcile_run(self, run_id, out, now_text):
        """补全启动对账的执行记录。"""
        if not run_id:
            return
        store = self.task_db()
        error = None if out.get("ok") else (out.get("error") or "failed")
        cause = out.get("cause") or ("ok" if out.get("ok") else error)
        store.update_run(
            run_id, finished_at=now_text,
            request_id=out.get("request_id"), result=out.get("result"),
            cause="启动对账：{}".format(cause), error=error)

    def _publish_reconcile_result(self, task, phase, run_id, out):
        stage, event_cause = self._task_outcome(out)
        self._publish_task_event(
            task, stage, run_id=run_id, phase=phase,
            progress=100 if stage == "completed" else None,
            message=self._task_event_message(
                task, phase, out, stage, event_cause),
            cause=event_cause, result=out.get("result"))

    def _reconcile_device_recording(self, device, base_now):
        """按 runState 对本设备做录像补启动/补停止。"""
        store = self.task_db()
        tasks = [
            task for task in store.list_tasks(enabled=True)
            if task.get("device") == device
            and (task.get("schedule") or {}).get("type") == "daily_window"
        ]
        if not tasks:
            return {"device": device, "action": "skip",
                    "reason": "no_window_task"}
        active = None
        for task in tasks:
            if task_schedule.in_daily_window(task, base_now):
                active = task
                break
        desired = active is not None
        state = self.device_run_state(device)
        if not state.get("ok"):
            return {"device": device, "desired": desired, "action": "skip",
                    "reason": state.get("error") or "run_state_unavailable"}
        group = (state.get("summary") or {}).get("recording") or {}
        actual = bool(group.get("recording"))
        item = {"device": device, "desired": desired, "recording": actual,
                "action": "none"}
        if desired and not actual:
            run_id, now_text = self._begin_reconcile_run(
                active, "start", base_now)
            self._publish_task_event(
                active, "started", run_id=run_id, phase="start", progress=0,
                message="任务「{}」启动对账：开始录像".format(
                    active.get("name") or active.get("id")))
            out = self._task_action_with_retry(device, "start")
            self._finish_reconcile_run(run_id, out, now_text)
            self._publish_reconcile_result(active, "start", run_id, out)
            self._advance_task(store, active)
            item.update({"action": "start", "ok": bool(out.get("ok")),
                         "cause": out.get("cause") or out.get("error")})
        elif not desired and actual:
            latest = self._latest_successful_run(store, device)
            if not latest or latest.get("phase") != "start":
                item.update({"action": "keep",
                             "reason": "recording_not_owned"})
            else:
                owner = store.get_task(latest.get("task_id"))
                run_id = now_text = None
                if owner is not None:
                    run_id, now_text = self._begin_reconcile_run(
                        owner, "stop", base_now)
                    self._publish_task_event(
                        owner, "started", run_id=run_id, phase="stop",
                        progress=0,
                        message="任务「{}」启动对账：停止录像".format(
                            owner.get("name") or owner.get("id")))
                out = self._task_action_with_retry(device, "stop")
                if owner is not None:
                    self._finish_reconcile_run(run_id, out, now_text)
                    self._publish_reconcile_result(
                        owner, "stop", run_id, out)
                    self._advance_task(store, owner)
                item.update({"action": "stop",
                             "ok": bool(out.get("ok")),
                             "cause": out.get("cause") or out.get("error")})
        return item

    @staticmethod
    def _latest_successful_run(store, device):
        """设备最近一次成功的任务执行（error 为空且 result 为 0/-4）。"""
        latest = None
        latest_stamp = ""
        for task in store.list_tasks():
            if task.get("device") != device:
                continue
            for run in store.list_runs(task["id"], limit=20):
                if run.get("error") or run.get("result") not in (0, -4):
                    continue
                stamp = (run.get("finished_at") or run.get("started_at")
                         or run.get("scheduled_at") or "")
                if latest is None or stamp > latest_stamp:
                    latest, latest_stamp = run, stamp
                break
        return latest

    def chat_status(self):
        return {
            "enabled": self.chat_enabled,
            "agent": agent_identity(),
            "request_topic": chat_topic("request"),
            "response_topic": chat_topic("response"),
            "status_topic": agent_status_topic(),
            "requests": self.chat_requests,
            "errors": self.chat_errors,
            "last": self.chat_last,
        }

    def publish_motor(self, device, payload):
        """运动帧专用：只 publish，不等 PUBACK，避免阻塞 5Hz 发送循环。

        发送结果以设备 response 为准；成功不写 INFO 日志（5Hz 会刷屏）。
        """
        topic = topic_for(device, "car/motor/set/request")
        body = dict(payload)
        body["client_id"] = agent_identity()
        body["client_name"] = agent_name()
        item = self._dev(device)
        record = {
            "topic": topic,
            "payload": body,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if not (self.mqtt_client and self.mqtt_connected):
            item["last_publish"] = dict(record, published=False,
                                        error="mqtt_not_connected")
            log().warning("MQTT 未连接，丢弃 %s", topic)
            return {"published": False, "error": "mqtt_not_connected",
                    "topic": topic, "payload": body}
        _trace("OUT", topic, body, 1, False)
        info = self.mqtt_client.publish(topic, json.dumps(body, ensure_ascii=False),
                                        qos=1)
        item["last_publish"] = dict(record, published=info.rc == 0, rc=info.rc)
        log().debug("publish_motor %s rc=%s", topic, info.rc)
        return {"published": info.rc == 0, "topic": topic,
                "payload": body, "rc": info.rc}

    def _publish(self, device, resource, payload):
        topic = topic_for(device, resource)
        body = payload
        # §3.5：agent 作为独立控制端，所有 device .../request 与 .../cmd 携带自身身份
        if isinstance(payload, dict) and (resource.endswith("/request")
                                          or resource.endswith("/cmd")):
            body = dict(payload)
            body["client_id"] = agent_identity()
            body["client_name"] = agent_name()
        item = self._dev(device)
        record = {
            "topic": topic,
            "payload": body,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if not (self.mqtt_client and self.mqtt_connected):
            item["last_publish"] = dict(record, published=False,
                                        error="mqtt_not_connected")
            log().warning("MQTT 未连接，丢弃 %s %s", topic,
                          json.dumps(body, ensure_ascii=False))
            return {"published": False, "error": "mqtt_not_connected",
                    "topic": topic, "payload": body}
        _trace("OUT", topic, body, 1, False)
        info = self.mqtt_client.publish(topic, json.dumps(body, ensure_ascii=False),
                                        qos=1)
        try:
            info.wait_for_publish(timeout=2)
        except Exception:  # noqa: BLE001
            pass
        item["last_publish"] = dict(record, published=info.rc == 0, rc=info.rc)
        log().info("publish %s %s rc=%s", topic,
                   json.dumps(body, ensure_ascii=False), info.rc)
        return {"published": info.rc == 0, "topic": topic,
                "payload": body, "rc": info.rc}

    # ---- 设备控制权（§4.19）----
    def _control_request(self, device, action, timeout=5.0):
        """向设备发 control/{acquire|release}/request 并等待对应 response。"""
        request_id = new_request_id(action)
        res = self.request_bus.request(
            device, "control/{}/request".format(action),
            {"request_id": request_id}, timeout=timeout)
        if not res.get("received"):
            error = res.get("error")
            if error == "publish_failed":
                error = ((res.get("publish") or {}).get("error")
                         or "control_publish_failed")
            elif error == "timeout":
                error = "control_timeout"
            return {"ok": False, "error": error, "request_id": request_id,
                    "publish": res.get("publish")}
        return {"ok": True, "request_id": request_id,
                "response": res.get("payload")}

    def _acquire_control(self, device):
        with self.control_lock:
            held = self.control_held.get(device)
            if held:
                return {"ok": True, "action": "already_held",
                        "control_holder": held.get("control_holder")}
        res = self._control_request(device, "acquire")
        if not res.get("ok"):
            error = res.get("error")
            if error == "mqtt_not_connected":
                return res
            return {"ok": False, "error": "control_failed", "cause": error}
        data = res.get("response") or {}
        decoded = decode_result(data)
        action = data.get("action")
        holder = decoded.get("holder") or data.get("control_holder")
        if decoded["ok"]:
            with self.control_lock:
                self.control_held[device] = {
                    "device": device,
                    "client_id": agent_identity(),
                    "control_holder": holder,
                }
            log().info("已获取设备控制权 device=%s action=%s holder=%s",
                       device, action, (holder or {}).get("client_id") or "-")
            return {"ok": True, "action": action, "control_holder": holder}
        if decoded["error"] == "control_denied" or action == "occupied":
            log().warning("设备控制权被占用 device=%s holder=%s", device,
                          (holder or {}).get("client_id") or "-")
            return {"ok": False, "error": "control_occupied", "action": action,
                    "control_holder": holder}
        return {"ok": False, "error": "control_failed", "action": action,
                "cause": decoded.get("cause")}

    def _release_control(self, device):
        with self.control_lock:
            held = self.control_held.get(device)
            if not held:
                return {"ok": True, "skipped": True}
        res = self._control_request(device, "release")
        with self.control_lock:
            self.control_held.pop(device, None)
        if not res.get("ok"):
            log().warning("释放设备控制权失败 device=%s error=%s",
                          device, res.get("error"))
            return res
        data = res.get("response") or {}
        decoded = decode_result(data)
        action = data.get("action")
        if decoded["ok"]:
            log().info("已释放设备控制权 device=%s action=%s", device, action)
            return {"ok": True, "action": action}
        log().warning("释放设备控制权未成功 device=%s action=%s cause=%s",
                      device, action, decoded.get("cause"))
        return {"ok": False, "error": "control_failed", "action": action,
                "control_holder": decoded.get("holder"),
                "cause": decoded.get("cause")}

    # ---- 每设备资源锁 ----
    def _resource_lock_for(self, device, name):
        with self.resource_lock:
            locks = self.resource_locks.setdefault(device, {})
            return locks.setdefault(name, threading.Lock())

    def _acquire_resource(self, device, name):
        lock = self._resource_lock_for(device, name)
        return lock if lock.acquire(False) else None

    @staticmethod
    def _release_resource(lock):
        if lock is None:
            return
        try:
            lock.release()
        except RuntimeError:
            pass

    # ---- 指令执行 ----
    def _drive_payload(self, intent, request_id):
        if intent["action"] == "stop":
            move = {"forward": 0, "back": 0, "left": 0, "right": 0}
            rotate = {"left": False, "right": False}
        elif intent["action"] == "rotate":
            move = {"forward": 0, "back": 0, "left": 0, "right": 0}
            rotate = {"left": intent["direction"] == "left",
                      "right": intent["direction"] == "right"}
        else:
            speed = float(intent["speed_mps"])
            move = {"forward": 0, "back": 0, "left": 0, "right": 0}
            key = {"forward": "forward", "backward": "back",
                   "left": "left", "right": "right"}[intent["direction"]]
            move[key] = speed
            rotate = {"left": False, "right": False}
        return {"request_id": request_id,
                "move": move, "rotate": rotate}

    def _duration(self, intent):
        if intent["action"] == "move":
            return float(intent["distance_m"]) / float(intent["speed_mps"])
        if intent["action"] == "rotate":
            import math
            return math.radians(float(intent["angle_deg"])) / float(self.cfg["DEFAULT_ANGULAR"])
        return 0.0

    def _run_motion(self, session):
        device = session.device
        intent = session.intent
        client_name = session.client_name
        request_id = session.request_id
        duration = self._duration(intent)
        payload = self._drive_payload(intent, request_id)
        hz = max(1.0, float(self.cfg["SEND_HZ"]))
        period = 1.0 / hz
        t0 = time.monotonic()
        log().info("motion start client_name=%s device=%s intent=%s duration=%.2fs",
                   client_name, device, json.dumps(intent, ensure_ascii=False), duration)
        with self.state_lock:
            self.last_motion_error = None
        try:
            while not session.abort.is_set():
                if time.monotonic() - t0 >= duration:
                    break
                self.publish_motor(device, payload)
                time.sleep(period)
        finally:
            with self.state_lock:
                stop_owns_cleanup = session.stop_owns_cleanup
            if stop_owns_cleanup:
                # stop() 会统一发送零速并释放租约，这里只清理本地状态
                log().info("motion cleanup delegated to stop() device=%s", device)
            else:
                zero = self._drive_payload({"action": "stop"}, request_id)
                try:
                    for _ in range(3):
                        self._publish(device, "car/motor/set/request", zero)
                        time.sleep(0.05)
                finally:
                    release = self._release_control(device)
                    log().info("motion release control device=%s result=%s",
                               device, json.dumps(release, ensure_ascii=False))
            with self.state_lock:
                if self.sessions.get(device) is session:
                    self.sessions.pop(device, None)
            self._release_resource(session.resource_lock)
            log().info("motion end client_name=%s device=%s intent=%s elapsed=%.2fs",
                       client_name, device, json.dumps(intent, ensure_ascii=False),
                       time.monotonic() - t0)

    def execute(self, device, intent, client_name):
        if intent["action"] == "stop":
            return self.stop(client_name, device)
        if intent["action"] == "status":
            return self.device_status(device)
        if intent["action"] == "agent_status":
            return self.agent_status()
        if intent["action"] == "service_health":
            return self.service_health(device)
        if intent["action"] == "device_base_state":
            return self.device_base_state(device)
        if intent["action"] == "device_run_state":
            return self.device_run_state(device)
        if intent["action"] == "device_discover":
            return self.discover_devices()
        if intent["action"] == "alarm_status":
            return self.recent_alarm(device or None)
        if intent["action"] == "task_list":
            return self.task_list(device or None)
        if intent["action"] == "task_create":
            return self.task_create(
                intent.get("name"), device, intent.get("schedule"),
                intent.get("actions"))
        if intent["action"] == "task_cancel":
            return self.task_cancel(intent.get("task_id"),
                                    intent.get("task_name"))
        if intent["action"] == "task_pause":
            return self.task_pause(intent.get("task_id"),
                                   intent.get("task_name"))
        if intent["action"] == "task_resume":
            return self.task_resume(intent.get("task_id"),
                                    intent.get("task_name"))
        if intent["action"] == "record_start":
            return self.record_start(device, intent.get("segment_sec") or 0)
        if intent["action"] == "record_stop":
            return self.record_stop(device)
        if intent["action"] == "record_list":
            return self.record_list(device)
        if intent["action"] == "record_delete":
            # 规则层已识别；实际删除在 M5-01 接入
            return {"ok": False, "action": "record_delete", "device": device,
                    "error": "unsupported",
                    "cause": "录像删除功能尚未实现（M5-01）"}
        if intent["action"] in ("record_play_start", "record_play_stop"):
            return {"ok": False, "action": intent["action"], "device": device,
                    "error": "unsupported",
                    "cause": "录像播放功能尚未实现（M5-02）"}
        if intent["action"] == "stream_start":
            return self.stream_start(device)
        if intent["action"] == "stream_stop":
            return self.stream_stop(device)
        if intent["action"] == "servo_status":
            return self.servo_status(device)
        if intent["action"] == "servo_set":
            return self.servo_set(device, intent.get("servos") or [],
                                  intent.get("duration", 1.0))
        if intent["action"] == "servo_center":
            return self.servo_center(device)
        if intent["action"] == "servo_relative":
            return self.servo_relative(device, intent.get("servo_id"),
                                       intent.get("delta"),
                                       intent.get("duration", 1.0))

        if intent["action"] in ("move", "rotate"):
            preflight = self.preflight_features(device, ("motor",))
            if not preflight.get("ok"):
                return {
                    "ok": False,
                    "accepted": False,
                    "action": intent["action"],
                    "error": preflight.get("error"),
                    "cause": preflight.get("cause"),
                    "capabilities": preflight.get("capabilities"),
                }

        duration = self._duration(intent)
        if not (self.mqtt_client and self.mqtt_connected):
            return {"ok": False, "accepted": False, "action": intent["action"],
                    "error": "mqtt_not_connected",
                    "note": "MQTT 未连接，未下发指令"}

        motion_lock = self._acquire_resource(device, "motion")
        if motion_lock is None:
            return {"ok": False, "accepted": False, "action": intent["action"],
                    "duration_sec": round(duration, 2),
                    "error": "resource_busy", "device": device,
                    "note": "该设备正在执行上一条运动指令"}
        try:
            with self.state_lock:
                existing = self.sessions.get(device)
                if (existing is not None and existing.thread is not None
                        and existing.thread.is_alive()):
                    return {"ok": False, "accepted": False,
                            "action": intent["action"],
                            "duration_sec": round(duration, 2),
                            "error": "busy", "active_device": device,
                            "note": "该设备正在执行上一条指令"}
            control = self._acquire_control(device)
            if not control.get("ok"):
                return {"ok": False, "accepted": False, "action": intent["action"],
                        "error": control.get("error"),
                        "control_holder": control.get("control_holder"),
                        "cause": control.get("cause")}
            session = MotionSession(device, intent, client_name)
            session.request_id = new_request_id(
                "rotate" if intent["action"] == "rotate" else "move")
            session.resource_lock = motion_lock
            with self.state_lock:
                self.sessions[device] = session
                session.thread = threading.Thread(
                    target=self._run_motion, args=(session,), daemon=True)
                session.thread.start()
            motion_lock = None  # 所有权移交给 _run_motion.finally
        finally:
            self._release_resource(motion_lock)
        return {"ok": True, "accepted": True, "action": intent["action"],
                "device": device, "duration_sec": round(duration, 2),
                "started": True}

    def _stop_device(self, device, client_name=None):
        with self.state_lock:
            session = self.sessions.get(device)
            thread = session.thread if session is not None else None
            should_abort = bool(thread is not None and thread.is_alive())
        with self.control_lock:
            held_before = device in self.control_held
        if not should_abort and not held_before:
            return {"ok": True, "action": "stop", "device": device,
                    "stopped": False, "released": False,
                    "cause": "no_session", "mqtt": self.mqtt_status()}
        if should_abort:
            # 先标记由 stop() 收尾，再 abort；_run_motion.finally 会跳过零速/释放
            with self.state_lock:
                session.stop_owns_cleanup = True
            session.abort.set()
            if thread is not None:
                thread.join(timeout=2.0)
        try:
            zero = self._drive_payload({"action": "stop"},
                                       new_request_id("stop"))
            res = None
            for _ in range(3):
                res = self._publish(device, "car/motor/set/request", zero)
                time.sleep(0.05)
            release = self._release_control(device)
            if release.get("skipped"):
                released = held_before
            else:
                released = bool(release.get("ok"))
            log().info(
                "stop requested by %s device=%s stopped=%s released=%s "
                "result=%s release=%s",
                client_name, device, should_abort, released,
                json.dumps(res, ensure_ascii=False),
                json.dumps(release, ensure_ascii=False))
            return {"ok": True, "action": "stop", "device": device,
                    "stopped": should_abort, "released": released,
                    "result": res, "control": release,
                    "mqtt": self.mqtt_status()}
        finally:
            if should_abort and session is not None and (
                    thread is None or not thread.is_alive()):
                with self.state_lock:
                    # 正常情况下 worker 已 join 完成；极端超时则保留标记，
                    # 让它退出时不要再补发一组零速。
                    session.stop_owns_cleanup = False

    def stop(self, client_name=None, device=None):
        """停止 agent 自己的会话：指定 device 只停该设备，不传则停全部。

        只停自己的运动线程/租约；没有会话时幂等返回 no_session。
        """
        if device:
            return self._stop_device(device, client_name)
        with self.state_lock:
            devices = list(self.sessions.keys())
        with self.control_lock:
            for held_device in self.control_held:
                if held_device not in devices:
                    devices.append(held_device)
        if not devices:
            return {"ok": True, "action": "stop", "device": None,
                    "stopped": False, "released": False,
                    "cause": "no_session", "mqtt": self.mqtt_status()}
        results = [self._stop_device(target, client_name) for target in devices]
        out = dict(results[0])
        if len(results) > 1:
            out["device"] = None
            out["devices"] = results
            out["stopped"] = any(item.get("stopped") for item in results)
            out["released"] = any(item.get("released") for item in results)
            out["cause"] = ("ok" if (out["stopped"] or out["released"])
                            else "no_session")
        return out

    def stop_session(self, client_name=None, device=None):
        """优雅停止 agent 自己的会话：中止运动线程 → 零速 → 释放控制权。

        没有自己的会话且未持有控制权时幂等返回 no_session（stopped=False）。
        """
        res = self.stop(client_name, device)
        out = {
            "ok": bool(res.get("ok")),
            "stopped": bool(res.get("stopped")),
            "released": bool(res.get("released")),
            "device": res.get("device"),
        }
        for key in ("error", "cause", "control_holder", "result"):
            if res.get(key) is not None:
                out[key] = res[key]
        return out

    def link_test(self, username=None, device=None):
        """非运动链路验证：向指定设备发 baseState/query/request，等 baseState/query/response。"""
        if not (self.mqtt_client and self.mqtt_connected):
            return {"ok": False, "error": "mqtt_not_connected",
                    "mqtt": self.mqtt_status()}
        if not device:
            return {"ok": False, "error": "device_missing",
                    "message": "请先在页面选择设备"}
        rid = new_request_id("link")
        res = self.request_bus.request(
            device, "baseState/query/request", {"request_id": rid},
            timeout=6.0)
        received = bool(res.get("received"))
        publish = res.get("publish") or {}
        result = None
        if received:
            result = {
                "topic": res.get("response_topic"),
                "payload": res.get("payload"),
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        out = {
            "ok": received,
            "request_id": rid,
            "device": device,
            "published": res.get("published"),
            "topic": res.get("request_topic") or publish.get("topic"),
            "payload": publish.get("payload"),
            "result": result,
            "decoded": res.get("decoded") if received else None,
            "mqtt": self.mqtt_status(),
        }
        if not received:
            error = res.get("error")
            out["error"] = ("no_result_within_6s"
                            if error == "timeout" else error)
        log().info("link test user=%s device=%s ok=%s result=%s", username,
                   device, received, json.dumps(result, ensure_ascii=False))
        return out

    def device_base_state(self, device, timeout=6.0):
        """查询设备 baseState 并解析 CPU/内存/磁盘/网络/USB 摘要（M1-03）。"""
        if not (self.mqtt_client and self.mqtt_connected):
            return {"ok": False, "action": "device_base_state",
                    "device": device, "error": "mqtt_not_connected",
                    "cause": "MQTT 未连接"}
        res = self.request_bus.request(
            device, "baseState/query/request", {}, timeout=timeout)
        if not res.get("received"):
            return {"ok": False, "action": "device_base_state",
                    "device": device, "request_id": res.get("request_id"),
                    "error": res.get("error"), "cause": res.get("cause")}
        payload = res.get("payload")
        if not isinstance(payload, dict):
            return {"ok": False, "action": "device_base_state",
                    "device": device, "request_id": res.get("request_id"),
                    "error": "invalid_response",
                    "cause": "设备状态应答不是 JSON 对象"}
        summary = self._base_state_summary(payload)
        return {
            "ok": True,
            "action": "device_base_state",
            "device": device,
            "request_id": res.get("request_id"),
            "baseState": payload,
            "summary": summary,
            "reply": self._base_state_reply(summary),
        }

    @staticmethod
    def _base_state_summary(payload):
        system = payload.get("system")
        system = system if isinstance(system, dict) else {}
        cpu = payload.get("cpu")
        cpu = cpu if isinstance(cpu, dict) else {}
        memory = payload.get("memory")
        memory = memory if isinstance(memory, dict) else {}
        disk = payload.get("disk")
        disk = disk if isinstance(disk, dict) else {}
        network = payload.get("network")
        network = network if isinstance(network, dict) else {}
        usb = payload.get("usb")
        usb = usb if isinstance(usb, dict) else {}
        usb_count = usb.get("count")
        if usb_count is None and isinstance(usb.get("devices"), list):
            usb_count = len(usb["devices"])
        return {
            "device_id": payload.get("device_id"),
            "hostname": payload.get("hostname"),
            "ip": payload.get("ip"),
            "uptime_seconds": payload.get("uptime_seconds"),
            "os": system.get("os") or system.get("os_version"),
            "kernel": system.get("kernel"),
            "cpu_percent": cpu.get("percent"),
            "cpu_count": cpu.get("count_logical") or cpu.get("count_physical"),
            "cpu_temperature_c": cpu.get("temperature_c"),
            "memory_percent": memory.get("percent"),
            "memory_used_bytes": memory.get("used_bytes"),
            "memory_total_bytes": memory.get("total_bytes"),
            "disk_percent": disk.get("percent"),
            "disk_free_bytes": disk.get("free_bytes"),
            "disk_total_bytes": disk.get("total_bytes"),
            "network_rx_bps": network.get("total_rx_bytes_per_sec"),
            "network_tx_bps": network.get("total_tx_bytes_per_sec"),
            "usb_count": usb_count,
        }

    @staticmethod
    def _base_state_reply(summary):
        parts = []
        if summary.get("ip"):
            parts.append("IP {}".format(summary["ip"]))
        if summary.get("cpu_percent") is not None:
            text = "CPU {:.1f}%".format(float(summary["cpu_percent"]))
            extra = []
            if summary.get("cpu_count"):
                extra.append("{} 核".format(summary["cpu_count"]))
            if summary.get("cpu_temperature_c") is not None:
                extra.append("{:.1f}℃".format(
                    float(summary["cpu_temperature_c"])))
            if extra:
                text += "（{}）".format("，".join(extra))
            parts.append(text)
        if summary.get("memory_percent") is not None:
            used = _human_bytes(summary.get("memory_used_bytes"))
            total = _human_bytes(summary.get("memory_total_bytes"))
            text = "内存 {:.1f}%".format(float(summary["memory_percent"]))
            if used and total:
                text += "（{}/{}）".format(used, total)
            parts.append(text)
        if summary.get("disk_percent") is not None:
            free = _human_bytes(summary.get("disk_free_bytes"))
            text = "磁盘 {:.1f}%".format(float(summary["disk_percent"]))
            if free:
                text += "（剩余 {}）".format(free)
            parts.append(text)
        rx = _human_bytes(summary.get("network_rx_bps"))
        tx = _human_bytes(summary.get("network_tx_bps"))
        if rx or tx:
            parts.append("网络 ↓{}/s ↑{}/s".format(rx or "-", tx or "-"))
        if summary.get("usb_count") is not None:
            parts.append("USB {} 个".format(summary["usb_count"]))
        uptime = _human_duration(summary.get("uptime_seconds"))
        if uptime:
            parts.append("运行 {}".format(uptime))
        return "；".join(parts) or "设备状态已返回，但缺少可展示字段。"

    def device_run_state(self, device, timeout=6.0):
        """查询设备 runState 并解析录像/推流/电量/声呐/控制权（M1-04）。"""
        if not (self.mqtt_client and self.mqtt_connected):
            return {"ok": False, "action": "device_run_state",
                    "device": device, "error": "mqtt_not_connected",
                    "cause": "MQTT 未连接"}
        res = self.request_bus.request(
            device, "runState/query/request", {}, timeout=timeout)
        if not res.get("received"):
            return {"ok": False, "action": "device_run_state",
                    "device": device, "request_id": res.get("request_id"),
                    "error": res.get("error"), "cause": res.get("cause")}
        payload = res.get("payload")
        if not isinstance(payload, dict):
            return {"ok": False, "action": "device_run_state",
                    "device": device, "request_id": res.get("request_id"),
                    "error": "invalid_response",
                    "cause": "设备运行状态应答不是 JSON 对象"}

        groups = {}
        unsupported = []
        for name in _RUN_GROUP_DEFAULTS:
            group = self._normalize_run_group(name, payload.get(name))
            if not isinstance(group, dict):
                groups[name] = None
                continue
            if group.get("is_supported") is False:
                # is_supported=false：该分组不展示，也不允许后续控制
                groups[name] = None
                unsupported.append(name)
            else:
                groups[name] = group

        summary = {
            "device_id": payload.get("device_id"),
            "hostname": payload.get("hostname"),
            "ip": payload.get("ip"),
            "recording": groups["recording"],
            "stream": groups["stream"],
            "phone_audio": groups["phone_audio"],
            "motor": groups["motor"],
            "servo": groups["servo"],
            "battery": groups["battery"],
            "sonar": groups["sonar"],
            "control_holder": payload.get("control_holder"),
            "unsupported": unsupported,
        }
        return {
            "ok": True,
            "action": "device_run_state",
            "device": device,
            "request_id": res.get("request_id"),
            "runState": payload,
            "summary": summary,
            "reply": self._run_state_reply(summary),
        }

    def device_supports(self, device, feature):
        """查询缓存中某能力分组是否 is_supported；未知返回 None。"""
        state = self.device_state(device)
        if not state:
            return None
        group = state.get(feature)
        if not isinstance(group, dict):
            return None
        return group.get("is_supported")

    def capabilities(self, device):
        """从 DeviceRegistry 缓存读取各功能 is_supported（M1-07）。"""
        state = self.device_state(device)
        caps = {}
        for feature in CAPABILITY_FEATURES:
            group = state.get(feature) if state else None
            caps[feature] = (group.get("is_supported")
                             if isinstance(group, dict) else None)
        return caps

    def preflight_features(self, device, features, refresh=False):
        """控制/任务前的能力预检；未知能力默认放行，明确 false 才拒绝。"""
        caps = self.capabilities(device)
        if refresh and any(caps.get(feature) is None for feature in features):
            self.device_run_state(device)
            caps = self.capabilities(device)
        unsupported = [feature for feature in features
                       if caps.get(feature) is False]
        unknown = [feature for feature in features
                   if caps.get(feature) is None]
        if unsupported:
            return {
                "ok": False,
                "error": "unsupported",
                "cause": "设备不支持：{}".format("、".join(unsupported)),
                "unsupported": unsupported,
                "unknown": unknown,
                "capabilities": caps,
            }
        return {
            "ok": True,
            "unsupported": [],
            "unknown": unknown,
            "capabilities": caps,
        }

    def preflight_recording_task(self, device):
        """创建录像任务前的预检；不支持或无法确认时拒绝（M1-07）。"""
        result = self.preflight_features(device, ("recording",), refresh=True)
        caps = result.get("capabilities") or {}
        if caps.get("recording") is False:
            return {
                "ok": False,
                "error": "capability_unsupported",
                "cause": "设备不支持录像（recording.is_supported=false），不能创建录像任务",
                "capabilities": caps,
            }
        if caps.get("recording") is None:
            return {
                "ok": False,
                "error": "capability_unknown",
                "cause": "无法确认设备录像能力（runState 无有效 recording.is_supported），不能创建录像任务",
                "capabilities": caps,
            }
        return {"ok": True, "capabilities": caps}

    def record_start(self, device, segment_sec=0):
        """开始录像：控制类请求，acquire → record/start → release（M2-01）。"""
        self._report_action_progress("accepted", device, "开始录像请求已接受")
        preflight = self.preflight_features(device, ("recording",))
        if not preflight.get("ok"):
            self._report_action_progress("failed", device, preflight.get("cause"),
                                         cause=preflight.get("error"))
            return {"ok": False, "action": "record_start", "device": device,
                    "error": preflight.get("error"),
                    "cause": preflight.get("cause"),
                    "capabilities": preflight.get("capabilities")}
        if not (self.mqtt_client and self.mqtt_connected):
            self._report_action_progress("failed", device, "MQTT 未连接",
                                         cause="mqtt_not_connected")
            return {"ok": False, "action": "record_start", "device": device,
                    "error": "mqtt_not_connected", "cause": "MQTT 未连接"}
        try:
            segment = max(0, min(int(segment_sec or 0), 86400))
        except (TypeError, ValueError):
            segment = 0
        control = self._acquire_control(device)
        if not control.get("ok"):
            self._report_action_progress("failed", device, control.get("cause"),
                                         cause=control.get("error"))
            return {"ok": False, "action": "record_start", "device": device,
                    "error": control.get("error"),
                    "cause": control.get("cause"),
                    "control_holder": control.get("control_holder")}
        self._report_action_progress("waiting", device, "等待设备录像应答")
        try:
            res = self.request_bus.request(
                device, "record/start/request",
                {"segment_sec": segment}, timeout=12.0)
        finally:
            release = self._release_control(device)

        if not res.get("received"):
            out = {"ok": False, "action": "record_start", "device": device,
                   "request_id": res.get("request_id"),
                   "error": res.get("error") or "timeout",
                   "cause": res.get("cause") or "等待录像应答超时",
                   "control": release}
            self._report_action_progress("failed", device, out["cause"],
                                         cause=out["error"])
            return out
        payload = res.get("payload") or {}
        decoded = res.get("decoded") or decode_result(payload)
        result = decoded.get("result")
        base = {
            "action": "record_start",
            "device": device,
            "request_id": res.get("request_id"),
            "result": result,
            "payload": payload,
            "control": release,
        }
        if result == 0:
            base.update({
                "ok": True, "recording": True, "already_recording": False,
                "filename": payload.get("filename"),
                "started_at": payload.get("started_at"),
                "play_url": payload.get("play_url"),
                "cause": decoded.get("cause") or "ok",
            })
        elif result == -4:
            base.update({
                "ok": True, "recording": True, "already_recording": True,
                "filename": payload.get("filename"),
                "play_url": payload.get("play_url"),
                "cause": decoded.get("cause") or "已在录制",
            })
        elif result == -7:
            base.update({
                "ok": False, "recording": False, "error": "no_space",
                "cause": decoded.get("cause") or "磁盘空间不足",
            })
        else:
            base.update({
                "ok": False, "recording": False,
                "error": decoded.get("error") or "failed",
                "cause": decoded.get("cause") or "录像启动失败",
            })
        if base.get("ok"):
            self._report_action_progress(
                "completed", device,
                "录像已开始：{}".format(base.get("filename") or "-"),
                progress=100)
        else:
            self._report_action_progress("failed", device, base.get("cause"),
                                         cause=base.get("error"))
        return base

    def record_stop(self, device):
        """停止录像：-4（当前未录制）视为已停止，幂等成功（M2-01）。"""
        preflight = self.preflight_features(device, ("recording",))
        if not preflight.get("ok"):
            return {"ok": False, "action": "record_stop", "device": device,
                    "error": preflight.get("error"),
                    "cause": preflight.get("cause"),
                    "capabilities": preflight.get("capabilities")}
        if not (self.mqtt_client and self.mqtt_connected):
            return {"ok": False, "action": "record_stop", "device": device,
                    "error": "mqtt_not_connected", "cause": "MQTT 未连接"}
        control = self._acquire_control(device)
        if not control.get("ok"):
            return {"ok": False, "action": "record_stop", "device": device,
                    "error": control.get("error"),
                    "cause": control.get("cause"),
                    "control_holder": control.get("control_holder")}
        try:
            res = self.request_bus.request(
                device, "record/stop/request", {}, timeout=12.0)
        finally:
            release = self._release_control(device)

        if not res.get("received"):
            return {"ok": False, "action": "record_stop", "device": device,
                    "request_id": res.get("request_id"),
                    "error": res.get("error") or "timeout",
                    "cause": res.get("cause") or "等待录像应答超时",
                    "control": release}
        payload = res.get("payload") or {}
        decoded = res.get("decoded") or decode_result(payload)
        result = decoded.get("result")
        base = {
            "action": "record_stop",
            "device": device,
            "request_id": res.get("request_id"),
            "result": result,
            "payload": payload,
            "control": release,
        }
        if result == 0:
            base.update({
                "ok": True, "recording": False, "already_stopped": False,
                "filename": payload.get("filename"),
                "cause": decoded.get("cause") or "ok",
            })
        elif result == -4:
            base.update({
                "ok": True, "recording": False, "already_stopped": True,
                "cause": decoded.get("cause") or "当前未录制",
            })
        else:
            base.update({
                "ok": False,
                "error": decoded.get("error") or "failed",
                "cause": decoded.get("cause") or "停止录像失败",
            })
        return base

    def record_list(self, device):
        """查询录像列表：只读查询，不申请控制权（M2-01）。"""
        if not (self.mqtt_client and self.mqtt_connected):
            return {"ok": False, "action": "record_list", "device": device,
                    "error": "mqtt_not_connected", "cause": "MQTT 未连接"}
        res = self.request_bus.request(
            device, "record/list/query/request", {}, timeout=12.0)
        if not res.get("received"):
            return {"ok": False, "action": "record_list", "device": device,
                    "request_id": res.get("request_id"),
                    "error": res.get("error") or "timeout",
                    "cause": res.get("cause") or "等待录像列表超时"}
        payload = res.get("payload") or {}
        decoded = res.get("decoded") or decode_result(payload)
        result = decoded.get("result")
        records = payload.get("records")
        if not isinstance(records, list):
            records = []
        out = {
            "ok": result == 0,
            "action": "record_list",
            "device": device,
            "request_id": res.get("request_id"),
            "result": result,
            "records": records,
            "count": len(records),
            "used_bytes": payload.get("used_bytes"),
            "limit_bytes": payload.get("limit_bytes"),
            "cause": decoded.get("cause"),
            "payload": payload,
        }
        if result != 0:
            out["error"] = decoded.get("error") or "failed"
            out["cause"] = decoded.get("cause") or "查询录像列表失败"
        return out

    def servo_status(self, device, ids=None, include_offset=False):
        """查询舵机位置：只读查询，不申请控制权（M2-03）。"""
        if ids is None:
            ids = [1, 2]
        if not isinstance(ids, (list, tuple)) or not ids:
            return {"ok": False, "action": "servo_status", "device": device,
                    "error": "parameter_error", "cause": "ids 不能为空"}
        clean_ids = []
        for servo_id in ids:
            if (isinstance(servo_id, bool)
                    or not isinstance(servo_id, int)
                    or servo_id not in (1, 2, 3, 4)):
                return {"ok": False, "action": "servo_status",
                        "device": device, "error": "parameter_error",
                        "cause": "ids 只能是 1-4"}
            if servo_id in clean_ids:
                return {"ok": False, "action": "servo_status",
                        "device": device, "error": "parameter_error",
                        "cause": "ids 不能重复"}
            clean_ids.append(servo_id)
        self._report_action_progress("accepted", device, "查询舵机状态请求已接受")
        if not (self.mqtt_client and self.mqtt_connected):
            self._report_action_progress("failed", device, "MQTT 未连接",
                                         cause="mqtt_not_connected")
            return {"ok": False, "action": "servo_status", "device": device,
                    "error": "mqtt_not_connected", "cause": "MQTT 未连接"}
        timeout = 10.0 if include_offset else 6.0
        self._report_action_progress("waiting", device, "等待设备舵机状态")
        res = self.request_bus.request(
            device, "car/servo/status/query/request",
            {"ids": clean_ids, "include_offset": bool(include_offset)},
            timeout=timeout)
        if not res.get("received"):
            out = {"ok": False, "action": "servo_status", "device": device,
                   "request_id": res.get("request_id"),
                   "error": res.get("error") or "timeout",
                   "cause": res.get("cause") or "等待舵机状态超时"}
            self._report_action_progress("failed", device, out["cause"],
                                         cause=out["error"])
            return out
        payload = res.get("payload") or {}
        decoded = res.get("decoded") or decode_result(payload)
        result = decoded.get("result")
        servos = payload.get("servos")
        if not isinstance(servos, list):
            servos = []
        out = {
            "ok": result == 0,
            "partial": result == -3 and any(
                item.get("available") for item in servos
                if isinstance(item, dict)),
            "action": "servo_status",
            "device": device,
            "request_id": res.get("request_id"),
            "result": result,
            "servos": servos,
            "include_offset": bool(include_offset),
            "cause": decoded.get("cause"),
            "error": (decoded.get("error")
                      if result not in (0, -3) else None),
            "payload": payload,
        }
        if out["ok"] or out["partial"]:
            self._report_action_progress(
                "completed", device,
                "舵机状态查询完成" if out["ok"] else "部分舵机读取失败",
                progress=100)
        else:
            self._report_action_progress("failed", device, out.get("cause"),
                                         cause=out.get("error"))
        return out

    def servo_set(self, device, servos, duration=1.0):
        """按绝对位置设置舵机；越界项由设备放入 skipped（M2-03）。"""
        preflight = self.preflight_features(device, ("servo",))
        if not preflight.get("ok"):
            return {"ok": False, "action": "servo_set", "device": device,
                    "error": preflight.get("error"),
                    "cause": preflight.get("cause"),
                    "capabilities": preflight.get("capabilities")}
        if not (self.mqtt_client and self.mqtt_connected):
            return {"ok": False, "action": "servo_set", "device": device,
                    "error": "mqtt_not_connected", "cause": "MQTT 未连接"}
        if not isinstance(servos, (list, tuple)) or not servos:
            return {"ok": False, "action": "servo_set", "device": device,
                    "error": "parameter_error", "cause": "servos 不能为空"}
        clean = []
        for item in servos:
            if not isinstance(item, dict):
                return {"ok": False, "action": "servo_set", "device": device,
                        "error": "parameter_error",
                        "cause": "servos 元素必须是对象"}
            servo_id = item.get("id")
            position = item.get("position")
            if (isinstance(servo_id, bool) or not isinstance(servo_id, int)
                    or servo_id not in (1, 2, 3, 4)):
                return {"ok": False, "action": "servo_set",
                        "device": device, "error": "parameter_error",
                        "cause": "舵机 id 只能是 1-4"}
            if isinstance(position, bool) or not isinstance(position, int):
                return {"ok": False, "action": "servo_set",
                        "device": device, "error": "parameter_error",
                        "cause": "舵机 position 必须是整数"}
            clean.append({"id": servo_id, "position": position})
        try:
            duration = float(duration)
        except (TypeError, ValueError):
            duration = -1.0
        if duration < 0.1 or duration > 5.0:
            return {"ok": False, "action": "servo_set", "device": device,
                    "error": "parameter_error",
                    "cause": "duration 必须在 0.1-5.0 秒"}
        servo_lock = self._acquire_resource(device, "servo")
        if servo_lock is None:
            return {"ok": False, "action": "servo_set", "device": device,
                    "error": "resource_busy",
                    "cause": "该设备正在执行其他舵机操作"}
        control = self._acquire_control(device)
        if not control.get("ok"):
            self._release_resource(servo_lock)
            return {"ok": False, "action": "servo_set", "device": device,
                    "error": control.get("error"),
                    "cause": control.get("cause"),
                    "control_holder": control.get("control_holder")}
        try:
            res = self.request_bus.request(
                device, "car/servo/set/request",
                {"servos": clean, "duration": duration}, timeout=10.0)
        finally:
            release = self._release_control(device)
            self._release_resource(servo_lock)

        if not res.get("received"):
            return {"ok": False, "action": "servo_set", "device": device,
                    "request_id": res.get("request_id"),
                    "error": res.get("error") or "timeout",
                    "cause": res.get("cause") or "等待舵机设置应答超时",
                    "control": release}
        payload = res.get("payload") or {}
        decoded = res.get("decoded") or decode_result(payload)
        result = decoded.get("result")
        applied = payload.get("applied")
        skipped = payload.get("skipped")
        if not isinstance(applied, list):
            applied = []
        if not isinstance(skipped, list):
            skipped = []
        out = {
            "ok": result == 0,
            "action": "servo_set",
            "device": device,
            "request_id": res.get("request_id"),
            "result": result,
            "duration": payload.get("duration", duration),
            "applied": applied,
            "skipped": skipped,
            "cause": payload.get("cause") or decoded.get("cause"),
            "control": release,
            "payload": payload,
        }
        if result != 0:
            out["error"] = decoded.get("error") or "failed"
            if result == -5:
                out["error"] = "control_denied"
                out["control_holder"] = payload.get("control_holder")
        return out

    def servo_center(self, device):
        result = self.servo_set(
            device, [{"id": 1, "position": SERVO_CENTER},
                     {"id": 2, "position": SERVO_CENTER}], 1.0)
        result["action"] = "servo_center"
        return result

    def servo_relative(self, device, servo_id, delta, duration=1.0):
        """相对语义：先查当前位置，再按 delta 计算绝对位置并设置（M2-03）。"""
        if (isinstance(servo_id, bool) or not isinstance(servo_id, int)
                or servo_id not in (1, 2, 3, 4)):
            return {"ok": False, "action": "servo_relative",
                    "device": device, "error": "parameter_error",
                    "cause": "舵机 id 只能是 1-4"}
        try:
            delta = int(delta)
        except (TypeError, ValueError):
            return {"ok": False, "action": "servo_relative",
                    "device": device, "error": "parameter_error",
                    "cause": "delta 必须是整数"}
        status = self.servo_status(device, ids=[servo_id])
        if not (status.get("ok") or status.get("partial")):
            result = dict(status)
            result["action"] = "servo_relative"
            result["servo_id"] = servo_id
            return result
        current = None
        for item in status.get("servos") or []:
            if isinstance(item, dict) and item.get("id") == servo_id:
                current = item.get("position")
                break
        if current is None:
            return {"ok": False, "action": "servo_relative",
                    "device": device, "error": "servo_unavailable",
                    "cause": "无法读取舵机当前位置",
                    "servo_id": servo_id}
        target = max(SERVO_MIN, min(SERVO_MAX, int(current) + delta))
        result = self.servo_set(
            device, [{"id": servo_id, "position": target}], duration)
        result["action"] = "servo_relative"
        result["servo_id"] = servo_id
        result["servo_name"] = SERVO_NAMES.get(servo_id, "ID{}".format(servo_id))
        result["previous_position"] = int(current)
        result["target"] = target
        result["delta"] = delta
        return result

    def stream_start(self, device):
        """开始推流：设备无参数，返回 room_number/camera_device/action（M2-02）。"""
        self._report_action_progress("accepted", device, "开始推流请求已接受")
        preflight = self.preflight_features(device, ("stream",))
        if not preflight.get("ok"):
            self._report_action_progress("failed", device, preflight.get("cause"),
                                         cause=preflight.get("error"))
            return {"ok": False, "action": "stream_start", "device": device,
                    "error": preflight.get("error"),
                    "cause": preflight.get("cause"),
                    "capabilities": preflight.get("capabilities")}
        if not (self.mqtt_client and self.mqtt_connected):
            self._report_action_progress("failed", device, "MQTT 未连接",
                                         cause="mqtt_not_connected")
            return {"ok": False, "action": "stream_start", "device": device,
                    "error": "mqtt_not_connected", "cause": "MQTT 未连接"}
        control = self._acquire_control(device)
        if not control.get("ok"):
            self._report_action_progress("failed", device, control.get("cause"),
                                         cause=control.get("error"))
            return {"ok": False, "action": "stream_start", "device": device,
                    "error": control.get("error"),
                    "cause": control.get("cause"),
                    "control_holder": control.get("control_holder")}
        self._report_action_progress("waiting", device, "等待设备推流应答")
        try:
            res = self.request_bus.request(
                device, "stream/start/request", {}, timeout=20.0)
        finally:
            release = self._release_control(device)

        if not res.get("received"):
            out = {"ok": False, "action": "stream_start", "device": device,
                   "request_id": res.get("request_id"),
                   "error": res.get("error") or "timeout",
                   "cause": res.get("cause") or "等待推流应答超时",
                   "control": release}
            self._report_action_progress("failed", device, out["cause"],
                                         cause=out["error"])
            return out
        payload = res.get("payload") or {}
        decoded = res.get("decoded") or decode_result(payload)
        result = decoded.get("result")
        device_action = payload.get("action")
        base = {
            "action": "stream_start",
            "device": device,
            "request_id": res.get("request_id"),
            "result": result,
            "payload": payload,
            "control": release,
        }
        if result == 0:
            base.update({
                "ok": True,
                "streaming": True,
                "already_streaming": device_action == "already_streaming",
                "device_action": device_action or "started",
                "room_number": payload.get("room_number"),
                "camera_device": payload.get("camera_device"),
                "started_at": payload.get("started_at"),
                "pid": payload.get("pid"),
                "cause": decoded.get("cause") or "ok",
            })
        elif result == -3:
            base.update({
                "ok": False, "streaming": False, "error": "unavailable",
                "cause": payload.get("cause") or decoded.get("cause")
                         or "设备没有推流能力或摄像头",
            })
        else:
            base.update({
                "ok": False, "streaming": False,
                "error": decoded.get("error") or "failed",
                "cause": payload.get("cause") or decoded.get("cause")
                         or "推流启动失败",
            })
        if base.get("ok"):
            self._report_action_progress(
                "completed", device,
                "推流已开始，房间号：{}".format(base.get("room_number") or "-"),
                progress=100)
        else:
            self._report_action_progress("failed", device, base.get("cause"),
                                         cause=base.get("error"))
        return base

    def stream_stop(self, device, room_number=None):
        """停止推流：可带 room_number；-4 区分“本来没推流”和房间号不一致。"""
        preflight = self.preflight_features(device, ("stream",))
        if not preflight.get("ok"):
            return {"ok": False, "action": "stream_stop", "device": device,
                    "error": preflight.get("error"),
                    "cause": preflight.get("cause"),
                    "capabilities": preflight.get("capabilities")}
        if not (self.mqtt_client and self.mqtt_connected):
            return {"ok": False, "action": "stream_stop", "device": device,
                    "error": "mqtt_not_connected", "cause": "MQTT 未连接"}
        room = str(room_number or "").strip()
        control = self._acquire_control(device)
        if not control.get("ok"):
            return {"ok": False, "action": "stream_stop", "device": device,
                    "error": control.get("error"),
                    "cause": control.get("cause"),
                    "control_holder": control.get("control_holder")}
        body = {"room_number": room} if room else {}
        try:
            res = self.request_bus.request(
                device, "stream/stop/request", body, timeout=12.0)
        finally:
            release = self._release_control(device)

        if not res.get("received"):
            return {"ok": False, "action": "stream_stop", "device": device,
                    "request_id": res.get("request_id"),
                    "error": res.get("error") or "timeout",
                    "cause": res.get("cause") or "等待停止推流应答超时",
                    "control": release}
        payload = res.get("payload") or {}
        decoded = res.get("decoded") or decode_result(payload)
        result = decoded.get("result")
        current_room = payload.get("room_number")
        base = {
            "action": "stream_stop",
            "device": device,
            "request_id": res.get("request_id"),
            "result": result,
            "payload": payload,
            "control": release,
        }
        if result == 0:
            base.update({
                "ok": True, "streaming": False,
                "stopped": bool(payload.get("stopped", True)),
                "already_stopped": False,
                "room_number": current_room or room or None,
                "cause": decoded.get("cause") or "ok",
            })
        elif result == -4:
            if room and current_room and current_room != room:
                base.update({
                    "ok": False, "streaming": True, "stopped": False,
                    "error": "room_mismatch",
                    "room_number": current_room,
                    "cause": "当前推流房间号为 {}，与请求的 {} 不一致".format(
                        current_room, room),
                })
            else:
                base.update({
                    "ok": True, "streaming": False, "stopped": False,
                    "already_stopped": True,
                    "room_number": current_room or room or None,
                    "cause": decoded.get("cause") or "当前没有推流",
                })
        else:
            base.update({
                "ok": False,
                "error": decoded.get("error") or "failed",
                "cause": payload.get("cause") or decoded.get("cause")
                         or "停止推流失败",
            })
        return base

    def device_status(self, device):
        """「状态」默认查设备 baseState + runState（M1-06）。"""
        base = self.device_base_state(device)
        run = self.device_run_state(device)
        parts = []
        if base.get("ok"):
            if base.get("reply"):
                parts.append(base["reply"])
        else:
            parts.append("设备状态查询失败：{}".format(
                base.get("cause") or base.get("error") or "未知错误"))
        if run.get("ok"):
            if run.get("reply"):
                parts.append(run["reply"])
        else:
            parts.append("运行状态查询失败：{}".format(
                run.get("cause") or run.get("error") or "未知错误"))
        return {
            "ok": bool(base.get("ok") or run.get("ok")),
            "action": "status",
            "device": device,
            "baseState": base,
            "runState": run,
            "summary": {
                "base": base.get("summary"),
                "run": run.get("summary"),
            },
            "reply": "；".join(parts),
        }

    def agent_status(self):
        """「agent 状态」：返回 agent 所在机器的状态（M1-06）。"""
        try:
            state = machine_state()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "action": "agent_status",
                    "error": "state_failed",
                    "cause": "采集机器状态失败: {}".format(exc)}
        cpu = state.get("cpu") or {}
        memory = state.get("memory") or {}
        disk = state.get("disk") or {}
        network = state.get("network") or {}
        summary = {
            "hostname": state.get("hostname"),
            "ip": state.get("ip"),
            "os": state.get("os"),
            "cpu_percent": cpu.get("usage_percent"),
            "cpu_count": cpu.get("cores"),
            "cpu_temperature_c": cpu.get("temperature_c"),
            "memory_percent": memory.get("usage_percent"),
            "memory_used_bytes": memory.get("used_bytes"),
            "memory_total_bytes": memory.get("total_bytes"),
            "disk_percent": disk.get("usage_percent"),
            "disk_free_bytes": disk.get("free_bytes"),
            "disk_total_bytes": disk.get("total_bytes"),
            "network_rx_bps": network.get("rx_bytes_per_sec"),
            "network_tx_bps": network.get("tx_bytes_per_sec"),
            "uptime_seconds": state.get("uptime_sec"),
        }
        return {
            "ok": True,
            "action": "agent_status",
            "agent_id": agent_identity(),
            "agent_name": agent_name(),
            "state": state,
            "summary": summary,
            "reply": "agent 机器状态：" + self._base_state_reply(summary),
        }

    def service_health(self, device=None):
        """「服务健康」：返回 agent 自身健康与 MQTT/设备概况（M1-06）。"""
        health = self.health(device)
        mqtt = health.get("mqtt") or {}
        devices = health.get("devices") or []
        online = sum(1 for item in devices if item.get("online"))
        chat = health.get("chat") or {}
        parts = [
            "MQTT " + ("已连接" if mqtt.get("connected") else "未连接"),
            "在线设备 {}/{}".format(online, len(devices)),
            "运动 " + ("是" if health.get("moving") else "否"),
            "chat 请求 {}/错误 {}".format(
                chat.get("requests", 0), chat.get("errors", 0)),
            "LLM " + ("开启" if health.get("llm_enabled") else "关闭"),
        ]
        return {
            "ok": bool(health.get("ok", True)),
            "action": "service_health",
            "device": device,
            "health": health,
            "reply": "服务健康：" + "；".join(parts),
        }

    def discover_devices(self, timeout=3.0):
        """租户级 discovery：retained online 为主，3s 内 discovery 应答为辅。"""
        if not (self.mqtt_client and self.mqtt_connected):
            return {"ok": False, "action": "device_discover",
                    "error": "mqtt_not_connected", "cause": "MQTT 未连接"}
        wait = max(0.05, float(timeout))
        with self._discovery_lock:
            if self._discovery_running:
                return {"ok": False, "action": "device_discover",
                        "error": "busy", "cause": "已有设备发现正在进行"}
            self._discovery_running = True
            self._discovery_results = {}
            self._discovery_until = time.monotonic() + wait

        started = time.monotonic()
        topic = "walle/{}/discovery/query/request".format(self.tenant)
        try:
            publish = self._publish_topic(topic, {
                "client_id": agent_identity(),
                "client_name": agent_name(),
            })
            if not publish.get("published"):
                return {"ok": False, "action": "device_discover",
                        "error": "publish_failed",
                        "cause": "设备发现请求发布失败",
                        "topic": topic, "publish": publish}
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline:
                time.sleep(0.05)
            with self._discovery_lock:
                discovered = copy.deepcopy(self._discovery_results)
        finally:
            with self._discovery_lock:
                self._discovery_running = False
                self._discovery_until = 0.0

        with self.dev_lock:
            cache = copy.deepcopy(self.devices)

        merged = {}
        for device, item in cache.items():
            retained_online = item.get("online") is not None
            if not retained_online and device not in discovered:
                continue
            if retained_online and device in discovered:
                source = "both"
            elif retained_online:
                source = "online"
            else:
                source = "discovery"
            merged[device] = {
                "device": device,
                "device_id": item.get("device_id"),
                "hostname": item.get("hostname"),
                "ip": item.get("ip"),
                "online": item.get("online"),
                "model": item.get("model"),
                "device_type": item.get("device_type"),
                "os_description": item.get("os_description"),
                "version_name": item.get("version_name"),
                "source": source,
            }

        for device, record in discovered.items():
            payload = record.get("payload") or {}
            entry = merged.get(device)
            if entry is None:
                entry = {
                    "device": device, "device_id": None,
                    "hostname": None, "ip": None, "online": None,
                    "model": None, "device_type": None,
                    "os_description": None, "version_name": None,
                    "source": "discovery",
                }
                merged[device] = entry
            for key in ("device_id", "hostname", "ip", "model",
                        "device_type", "os_description", "version_name"):
                if payload.get(key) is not None:
                    entry[key] = payload[key]
            if entry.get("online") is None:
                entry["online"] = payload.get("online")

        devices = sorted(
            merged.values(),
            key=lambda item: (item.get("online") is not True,
                              item.get("device") or ""))
        names = [item.get("hostname") or item.get("device")
                 for item in devices]
        reply = ("发现 {} 台设备：{}".format(len(devices), "、".join(names))
                 if devices else "未发现设备。")
        return {
            "ok": True,
            "action": "device_discover",
            "devices": devices,
            "count": len(devices),
            "topic": topic,
            "published": True,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "reply": reply,
        }

    @staticmethod
    def _run_state_reply(summary):
        parts = []
        recording = summary.get("recording")
        if isinstance(recording, dict):
            if recording.get("recording"):
                filename = recording.get("filename")
                parts.append("正在录像" + (
                    "（{}）".format(filename) if filename else ""))
            else:
                parts.append("未录像")
        stream = summary.get("stream")
        if isinstance(stream, dict):
            if stream.get("streaming"):
                room = stream.get("room_number")
                parts.append("正在推流" + (
                    "（房间 {}）".format(room) if room else ""))
            else:
                parts.append("未推流")
        phone = summary.get("phone_audio")
        if isinstance(phone, dict):
            parts.append("正在播放手机音频" if phone.get("playing")
                         else "未播放手机音频")
        motor = summary.get("motor")
        if isinstance(motor, dict):
            parts.append("运动中" if motor.get("moving") else "小车静止")
        servo = summary.get("servo")
        if isinstance(servo, dict):
            parts.append("舵机运动中" if servo.get("moving") else "舵机静止")
        battery = summary.get("battery")
        if isinstance(battery, dict):
            if (battery.get("available")
                    and battery.get("voltage_v") is not None):
                parts.append("电量 {:.2f}V".format(
                    float(battery["voltage_v"])))
            else:
                parts.append("电量 无效")
        sonar = summary.get("sonar")
        if isinstance(sonar, dict):
            if (sonar.get("available")
                    and sonar.get("distance_cm") is not None):
                parts.append("前方 {:.0f}cm".format(
                    float(sonar["distance_cm"])))
            else:
                parts.append("前方距离 无效")
        holder = summary.get("control_holder")
        if isinstance(holder, dict) and (
                holder.get("client_id") or holder.get("client_name")):
            who = holder.get("client_name") or holder.get("client_id")
            parts.append("控制权：{}".format(who))
        else:
            parts.append("控制权：空闲")
        return "；".join(parts) or "设备运行状态已返回，但没有可展示的信息。"

    def mqtt_status(self):
        return {
            "configured": self.mqtt_configured,
            "connected": self.mqtt_connected,
            "host": "{}:{}".format(self.cfg["MQTT_HOST"], self.cfg["MQTT_PORT"]),
            "user": self.cfg["MQTT_USER"] or None,
            "tenant": self.tenant,
            "tls": self.cfg["MQTT_TLS"] == "1",
            "client_id": mqtt_client_id(),
            "error": self.mqtt_error,
            "chat": self.chat_status(),
        }

    def health(self, device=None):
        with self.state_lock:
            active_sessions = [
                session for session in self.sessions.values()
                if session.thread is not None and session.thread.is_alive()
            ]
            moving = bool(active_sessions)
            active_devices = [session.device for session in active_sessions]
            active = active_devices[0] if len(active_devices) == 1 else None
            last_motion_error = self.last_motion_error
        with self.control_lock:
            control_devices = list(self.control_held.keys())
        with self.dev_lock:
            devices = [{
                "device": key,
                "online": bool(item.get("online")),
                "online_ts": item.get("online_ts"),
                "device_id": item.get("device_id"),
                "hostname": item.get("hostname"),
                "timestamp": item.get("timestamp"),
            } for key, item in self.devices.items()]
        out = {
            "ok": True,
            "mqtt": self.mqtt_status(),
            "tenant": self.tenant,
            "agent": agent_identity(),
            "name": agent_name(),
            "chat": self.chat_status(),
            "devices": devices,
            "moving": moving,
            "active_device": active,
            "active_devices": active_devices,
            "control_devices": control_devices,
            "last_motion_error": last_motion_error,
            "last_alarm": self.last_alarm_notice,
            "limits": {
                "min_speed_mps": float(self.cfg.get("MIN_SPEED", "0.5")),
                "max_speed_mps": float(self.cfg["MAX_SPEED"]),
                "max_distance_m": float(self.cfg["MAX_DISTANCE"]),
                "max_angle_deg": float(self.cfg["MAX_ANGLE"]),
                "send_hz": float(self.cfg["SEND_HZ"]),
            },
            "llm_enabled": self.cfg["LLM_ENABLED"] == "1",
            "log": os.path.join(REPO_ROOT, "log", "car_agent.log"),
        }
        if device:
            item = self._dev(device)
            out.update({
                "device": device,
                "last_publish": item.get("last_publish"),
                "last_result": item.get("last_result"),
                "last_decode": item.get("last_decode"),
                "device_state": self.device_state(device),
            })
        return out


def _get_agent():
    global _AGENT
    with _INIT_LOCK:
        if _AGENT is None:
            _AGENT = Agent(load_config())
        return _AGENT


# ---------------- 可选 LLM 兜底 ----------------

_LLM_SYSTEM = (
    "你是小车控制意图解析器。只输出 JSON，不要解释。"
    "允许的动作：move(forward/backward/left/right, distance_m, speed_mps)、"
    "rotate(left/right, angle_deg)、stop、status、agent_status、"
    "service_health、alarm_status、record_start(segment_sec)、record_stop、"
    "record_list、stream_start、stream_stop、servo_status、servo_set(servos, "
    "duration)、servo_center、servo_relative(servo_id, delta)、"
    "device_base_state、device_run_state、device_discover。"
    "无法确定时输出 {\"action\":\"unknown\"}。"
)


def _llm_parse(text):
    cfg = load_config()
    if cfg["LLM_ENABLED"] != "1" or not cfg["LLM_BASE_URL"] or not cfg["LLM_API_KEY"]:
        return None
    url = cfg["LLM_BASE_URL"].rstrip("/") + "/chat/completions"
    body = {
        "model": cfg["LLM_MODEL"],
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _LLM_SYSTEM},
            {"role": "user", "content": text},
        ],
    }
    req = urlrequest.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + cfg["LLM_API_KEY"]})
    try:
        with urlrequest.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        intent = json.loads(content)
        if intent.get("action") in ("move", "rotate", "stop", "status",
                                    "agent_status", "service_health",
                                    "alarm_status", "record_start",
                                    "record_stop", "record_list",
                                    "stream_start", "stream_stop",
                                    "servo_status", "servo_set",
                                    "servo_center", "servo_relative",
                                    "device_base_state", "device_run_state",
                                    "device_discover"):
            return intent
    except (urlerror.URLError, KeyError, ValueError, TimeoutError) as exc:
        log().warning("LLM 解析失败: %s", exc)
    return None


# ---------------- 对外接口 ----------------

def _reply_text(intent, result):
    action = intent["action"]
    if action == "move":
        names = {"forward": "前进", "backward": "后退",
                 "left": "左移", "right": "右移"}
        text = "已解析：{} {:.2f} 米，速度 {:.2f} m/s，预计 {:.1f} 秒。".format(
            names.get(intent["direction"], intent["direction"]),
            float(intent["distance_m"]), float(intent["speed_mps"]),
            float(result.get("duration_sec") or 0))
    elif action == "rotate":
        text = "已解析：{}转 {:.0f} 度，预计 {:.1f} 秒。".format(
            "左" if intent["direction"] == "left" else "右",
            float(intent["angle_deg"]), float(result.get("duration_sec") or 0))
    elif action == "stop":
        if result.get("cause") == "no_session":
            return "当前没有 agent 正在执行的会话，未下发停止。"
        inner = result.get("result") or {}
        if inner.get("published"):
            return "已下发停止（零速）。"
        if inner.get("error") == "mqtt_not_connected":
            return "停止未下发：MQTT 未连接。"
        return "停止未下发。"
    elif action == "device_discover":
        if result.get("ok"):
            return result.get("reply") or "设备发现完成。"
        error = result.get("error")
        if error == "mqtt_not_connected":
            return "设备发现失败：MQTT 未连接。"
        return "设备发现失败：{}。".format(
            result.get("cause") or error or "未知错误")
    elif action == "device_run_state":
        if result.get("ok"):
            return result.get("reply") or "设备运行状态查询完成。"
        error = result.get("error")
        if error == "timeout":
            return "设备运行状态查询超时（6 秒无应答）。"
        if error == "mqtt_not_connected":
            return "设备运行状态查询失败：MQTT 未连接。"
        if error == "device_offline":
            return "设备运行状态查询失败：设备离线。"
        return "设备运行状态查询失败：{}。".format(
            result.get("cause") or error or "未知错误")
    elif action == "stream_start":
        if result.get("ok"):
            room = result.get("room_number") or "未知"
            if result.get("already_streaming"):
                return "已在推流，房间号：{}（未重启推流）。".format(room)
            return "已开始推流，房间号：{}。".format(room)
        if result.get("error") == "unavailable":
            return "开始推流失败：设备没有推流能力或摄像头（{}）。".format(
                result.get("cause") or "")
        if result.get("error") == "mqtt_not_connected":
            return "开始推流失败：MQTT 未连接。"
        if result.get("error") == "control_occupied":
            holder = result.get("control_holder") or {}
            who = holder.get("client_name") or holder.get("client_id") or "其他客户端"
            return "开始推流失败：设备控制权已被 {} 占用。".format(who)
        return "开始推流失败：{}。".format(
            result.get("cause") or result.get("error") or "未知错误")
    elif action == "stream_stop":
        if result.get("ok"):
            if result.get("already_stopped"):
                return "当前没有在推流（已视为停止）。"
            room = result.get("room_number")
            return "已停止推流{}。".format(
                "（房间 {}）".format(room) if room else "")
        if result.get("error") == "room_mismatch":
            return "停止推流失败：{}。".format(result.get("cause") or "房间号不一致")
        if result.get("error") == "mqtt_not_connected":
            return "停止推流失败：MQTT 未连接。"
        if result.get("error") == "control_occupied":
            holder = result.get("control_holder") or {}
            who = holder.get("client_name") or holder.get("client_id") or "其他客户端"
            return "停止推流失败：设备控制权已被 {} 占用。".format(who)
        return "停止推流失败：{}。".format(
            result.get("cause") or result.get("error") or "未知错误")
    elif action == "servo_status":
        servos = result.get("servos") or []
        if not servos and not result.get("ok"):
            return "查询舵机状态失败：{}。".format(_servo_error_text(result))
        parts = []
        for item in servos:
            if not isinstance(item, dict):
                continue
            servo_id = item.get("id")
            name = SERVO_NAMES.get(servo_id, "ID{}".format(servo_id))
            if item.get("available") and item.get("position") is not None:
                text = "ID{}（{}）={}".format(servo_id, name, item["position"])
                if item.get("offset") is not None:
                    text += "，offset={}".format(item["offset"])
            else:
                text = "ID{}（{}）不可用（{}）".format(
                    servo_id, name, item.get("error") or "读取失败")
            parts.append(text)
        prefix = "舵机状态：" if result.get("ok") else "部分舵机读取失败："
        return prefix + "；".join(parts) + "。"
    elif action == "servo_set":
        applied = result.get("applied") or []
        skipped = result.get("skipped") or []
        if result.get("ok"):
            applied_text = "、".join(
                "ID{}={}".format(item.get("id"), item.get("position"))
                for item in applied if isinstance(item, dict))
            reply = "舵机已设置：{}".format(applied_text or "无")
            if skipped:
                skipped_text = "、".join(
                    "ID{}={}（{}）".format(
                        item.get("id"), item.get("position"),
                        item.get("reason") or "越界")
                    for item in skipped if isinstance(item, dict))
                reply += "；已跳过：{}".format(skipped_text)
            return reply + "。"
        reply = "设置舵机失败：{}".format(_servo_error_text(result))
        if skipped:
            reply += "；已跳过：{}".format("、".join(
                "ID{}={}（{}）".format(
                    item.get("id"), item.get("position"),
                    item.get("reason") or "越界")
                for item in skipped if isinstance(item, dict)))
        return reply + "。"
    elif action == "servo_center":
        if result.get("ok"):
            return "舵机已回中（ID1={}，ID2={}）。".format(
                SERVO_CENTER, SERVO_CENTER)
        return "舵机回中失败：{}。".format(_servo_error_text(result))
    elif action == "servo_relative":
        name = result.get("servo_name") or "舵机"
        if result.get("ok"):
            delta = result.get("delta") or 0
            if result.get("servo_id") == 1:
                direction = "抬高" if delta > 0 else "降低"
            else:
                direction = "向右" if delta > 0 else "向左"
            return "已{}：ID{}（{}）从 {} 到 {}。".format(
                direction, result.get("servo_id"), name,
                result.get("previous_position"), result.get("target"))
        return "舵机调整失败：{}。".format(_servo_error_text(result))
    elif action == "device_base_state":
        if result.get("ok"):
            return result.get("reply") or "设备状态查询完成。"
        error = result.get("error")
        if error == "timeout":
            return "设备状态查询超时（6 秒无应答）。"
        if error == "mqtt_not_connected":
            return "设备状态查询失败：MQTT 未连接。"
        if error == "device_offline":
            return "设备状态查询失败：设备离线。"
        return "设备状态查询失败：{}。".format(
            result.get("cause") or error or "未知错误")
    elif action == "status":
        return result.get("reply") or "设备状态查询完成。"
    elif action == "agent_status":
        if result.get("ok"):
            return result.get("reply") or "agent 状态查询完成。"
        return "agent 状态查询失败：{}。".format(
            result.get("cause") or result.get("error") or "未知错误")
    elif action == "service_health":
        if result.get("ok"):
            return result.get("reply") or "服务健康查询完成。"
        return "服务健康查询失败：{}。".format(
            result.get("cause") or result.get("error") or "未知错误")
    elif action == "alarm_status":
        return result.get("reply") or "暂无告警。"
    elif action == "task_list":
        if not result.get("ok"):
            return "查询任务失败：{}。".format(
                result.get("cause") or result.get("error") or "未知错误")
        tasks = result.get("tasks") or []
        if not tasks:
            return "暂无任务。"
        parts = []
        for task in tasks[:5]:
            parts.append("{}（{}，下次 {}）".format(
                task.get("name") or task.get("id"),
                "启用" if task.get("enabled") else "已暂停",
                task.get("next_run_at") or "无"))
        suffix = " 等 {} 个".format(len(tasks)) if len(tasks) > 5 else ""
        return "共 {} 个任务：{}{}。".format(
            len(tasks), "；".join(parts), suffix)
    elif action == "task_create":
        if result.get("ok"):
            task = result.get("task") or {}
            return "已创建任务 {}（{}），首次执行 {}。".format(
                task.get("name") or "录像任务", result.get("task_id"),
                result.get("first_run_at"))
        return "创建任务失败：{}。".format(
            result.get("cause") or result.get("error") or "未知错误")
    elif action == "task_cancel":
        if result.get("ok"):
            return "已取消任务 {}（{}）。".format(
                result.get("task_name") or "", result.get("task_id"))
        return "取消任务失败：{}。".format(
            result.get("cause") or result.get("error") or "未知错误")
    elif action == "task_pause":
        if result.get("ok"):
            return "已暂停任务 {}，恢复后重新计算下次执行时间。".format(
                result.get("task_name") or result.get("task_id"))
        return "暂停任务失败：{}。".format(
            result.get("cause") or result.get("error") or "未知错误")
    elif action == "task_resume":
        if result.get("ok"):
            return "已恢复任务 {}，下次执行 {}。".format(
                result.get("task_name") or result.get("task_id"),
                result.get("next_run_at") or "无")
        return "恢复任务失败：{}。".format(
            result.get("cause") or result.get("error") or "未知错误")
    elif action == "record_start":
        if result.get("ok"):
            if result.get("already_recording"):
                return "已经在录像：{}".format(
                    result.get("filename") or "当前文件")
            return "已开始录像：{}".format(result.get("filename") or "")
        if result.get("error") == "no_space":
            return "开始录像失败：磁盘空间不足（{}）。".format(
                result.get("cause") or "")
        if result.get("error") == "mqtt_not_connected":
            return "开始录像失败：MQTT 未连接。"
        if result.get("error") == "control_occupied":
            holder = result.get("control_holder") or {}
            who = holder.get("client_name") or holder.get("client_id") or "其他客户端"
            return "开始录像失败：设备控制权已被 {} 占用。".format(who)
        return "开始录像失败：{}。".format(
            result.get("cause") or result.get("error") or "未知错误")
    elif action == "record_stop":
        if result.get("ok"):
            if result.get("already_stopped"):
                return "当前未在录像（已视为停止）。"
            return "已停止录像：{}".format(result.get("filename") or "")
        if result.get("error") == "mqtt_not_connected":
            return "停止录像失败：MQTT 未连接。"
        if result.get("error") == "control_occupied":
            holder = result.get("control_holder") or {}
            who = holder.get("client_name") or holder.get("client_id") or "其他客户端"
            return "停止录像失败：设备控制权已被 {} 占用。".format(who)
        return "停止录像失败：{}。".format(
            result.get("cause") or result.get("error") or "未知错误")
    elif action == "record_list":
        if result.get("ok"):
            records = result.get("records") or []
            if not records:
                return "暂无录像。"
            names = "、".join(
                str(item.get("filename")) for item in records[:5])
            suffix = (" 等 {} 个".format(len(records))
                      if len(records) > 5 else "")
            return "共 {} 个录像：{}{}；已用 {} / 上限 {}。".format(
                len(records), names, suffix,
                _human_bytes(result.get("used_bytes")) or "?",
                _human_bytes(result.get("limit_bytes")) or "?")
        return "查询录像列表失败：{}。".format(
            result.get("cause") or result.get("error") or "未知错误")
    elif action in ("record_delete", "record_play_start", "record_play_stop"):
        return result.get("cause") or "该录像功能尚未实现。"
    else:
        return "已解析。"

    if result.get("accepted"):
        text += " 已开始执行。"
    elif result.get("error") == "mqtt_not_connected":
        text += " MQTT 未连接，指令未下发。"
    elif result.get("error") == "busy":
        text += " 小车正在执行上一条指令。"
    elif result.get("error") == "control_occupied":
        holder = result.get("control_holder") or {}
        who = holder.get("client_name") or holder.get("client_id") or "其他客户端"
        text += " 设备控制权已被 {} 占用，未执行。".format(who)
    elif result.get("error") in ("control_failed", "control_timeout"):
        text += " 获取设备控制权失败，未执行。"
    return text


# chat/response 结构化字段（M2-06）：data 给 Web 展示/播放，result 保留设备原始应答。
CHAT_DEVICE_RESULT_LIMIT = 4096  # 字节，超过后只回摘要
CHAT_RECORD_LIMIT = 50           # data.records 最多条数，避免报文过大
CHAT_RECORD_FIELDS = ("filename", "size_bytes", "created_at",
                      "duration_sec", "play_url")
CHAT_RESULT_SUMMARY_FIELDS = (
    "result", "cause", "request_id", "action", "room_number", "filename",
    "started_at", "play_url", "used_bytes", "limit_bytes", "device_id",
    "hostname", "ip", "timestamp", "datetime",
)


def _chat_data(result):
    """把动作结果整理成 Web 可直接使用的 data（§4.20）。"""
    action = result.get("action")
    data = {}
    if action in ("stream_start", "stream_stop"):
        room = result.get("room_number")
        if room:
            data["room_number"] = str(room)
    if action in ("record_start", "record_stop"):
        recording = {}
        for key in ("recording", "filename", "started_at", "play_url"):
            if result.get(key) is not None:
                recording[key] = result[key]
        if recording:
            data["recording"] = recording
    if action == "record_list":
        records = result.get("records") or []
        data["records"] = [
            {key: item[key] for key in CHAT_RECORD_FIELDS if key in item}
            for item in records[:CHAT_RECORD_LIMIT] if isinstance(item, dict)
        ]
        if len(records) > CHAT_RECORD_LIMIT:
            data["records_total"] = len(records)
    if action == "servo_status":
        data["servos"] = result.get("servos") or []
    elif action in ("servo_set", "servo_center", "servo_relative"):
        if "applied" in result or "skipped" in result:
            data["servos"] = {
                "applied": result.get("applied") or [],
                "skipped": result.get("skipped") or [],
            }
    if result.get("task") is not None:
        data["task"] = result["task"]
    return data


def _chat_device_result(result):
    """设备原始应答；超过 CHAT_DEVICE_RESULT_LIMIT 时返回摘要。"""
    payload = result.get("payload")
    if not isinstance(payload, dict) or not payload:
        return None
    size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    if size <= CHAT_DEVICE_RESULT_LIMIT:
        return payload
    summary = {key: payload[key] for key in CHAT_RESULT_SUMMARY_FIELDS
               if key in payload}
    if isinstance(payload.get("records"), list):
        summary["records_count"] = len(payload["records"])
    if isinstance(payload.get("servos"), list):
        summary["servos_count"] = len(payload["servos"])
    return {"truncated": True, "size_bytes": size, "summary": summary}


def handle_chat(client_name, device, text):
    agent = _get_agent()
    intent, err = parse_intent(text)
    log().info("chat client_name=%s device=%s text=%r intent=%s err=%s",
               client_name, device, text, intent, err)
    if intent is None:
        intent = _llm_parse(text)
        if intent is not None:
            err = None
    if intent is None:
        return {"ok": False, "device": device,
                "reply": err or "未识别该指令。",
                "mqtt": agent.mqtt_status()}

    intent, warning = validate_intent(intent, agent.cfg)
    if intent is None:
        return {"ok": False, "device": device,
                "reply": warning, "mqtt": agent.mqtt_status()}

    result = agent.execute(device, intent, client_name)
    result["intent"] = intent
    result["device"] = device
    result["mqtt"] = agent.mqtt_status()
    if warning:
        result["warning"] = warning
    result["reply"] = _reply_text(intent, result)
    return result


def health(device=None):
    return _get_agent().health(device)


def stop(username=None, device=None):
    return _get_agent().stop(username, device)


def link_test(username=None, device=None):
    return _get_agent().link_test(username, device)

def _raise_keyboard_interrupt(_signum, _frame):
    raise KeyboardInterrupt


def main():
    """纯 MQTT 常驻进程：不监听任何端口。"""
    print("walle-agent version: {} ({})".format(
        AGENT_VERSION, AGENT_VERSION_DATE or "unknown"), flush=True)
    # 启动即初始化/读取展示名，确保 etc/.run/agent-name 存在
    agent_name()
    agent = _get_agent()
    deadline = time.time() + 10
    while time.time() < deadline and not agent.mqtt_connected:
        time.sleep(0.2)
    if not agent.mqtt_connected:
        print("walle-agent 启动 10s 内未连上 MQTT，请检查配置与 broker；进程将继续重连",
              file=sys.stderr, flush=True)
    try:
        agent.start_task_scheduler()
        print("task scheduler started", flush=True)
    except Exception as exc:  # noqa: BLE001 - 调度器失败不应拖垮主进程
        print("task scheduler start failed: {}".format(exc),
              file=sys.stderr, flush=True)
    # SIGTERM（systemctl stop / kill）也要走正常退出流程，补发离线状态
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
    signal.signal(signal.SIGINT, _raise_keyboard_interrupt)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        agent.shutdown()


if __name__ == "__main__":
    main()
