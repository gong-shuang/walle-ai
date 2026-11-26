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
import glob
import json
import logging
import os
import platform
import random
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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(BASE_DIR)
VENDOR_DIR = os.path.join(BASE_DIR, "vendor")
if os.path.isdir(VENDOR_DIR) and VENDOR_DIR not in sys.path:
    sys.path.insert(0, VENDOR_DIR)

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
}

AGENT_VERSION_FILE = os.path.join(REPO_ROOT, "VERSION")


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


def parse_intent(text):
    """返回 (intent, error)。intent 为 None 时 error 给出可读原因。"""
    t = _normalize(text)
    if not t:
        return None, "请输入指令，例如：前进3米、左转90度、停止。"

    if re.fullmatch(r"(停止|停|急停|刹车|别动|stop)", t) or "急停" in t:
        return {"action": "stop"}, None

    motion_words = ("前进", "向前", "往前", "前行", "后退", "向后", "往后", "倒车",
                    "左移", "右移", "左转", "右转")
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
    if intent["action"] in ("stop", "status"):
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
        self.link_rid = None
        self.link_dev = None
        self.link_result = None
        self.link_event = threading.Event()
        self.worker = None
        self.active_device = None
        self.abort = threading.Event()
        self.state_lock = threading.Lock()
        # 串行化“检查 busy + acquire + 启动 worker”，避免多客户端同时下发
        self.motion_lock = threading.Lock()
        # 设备控制权：pending request_id -> {event, result}；control_held 记录当前持有的设备
        self.control_pending = {}
        self.control_pending_lock = threading.Lock()
        self.control_held = None
        self.control_lock = threading.Lock()
        self.chat_enabled = cfg.get("CHAT_MQTT_ENABLED", "1") == "1"
        self.chat_requests = 0
        self.chat_errors = 0
        self.chat_last = None
        self._connect_mqtt()

    def _dev(self, device):
        with self.dev_lock:
            item = self.devices.get(device)
            if item is None:
                item = {
                    "last_result": None,
                    "last_publish": None,
                    "online": None,
                    "online_ts": None,
                }
                self.devices[device] = item
            return item

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

        response 按资源层级用通配符覆盖，新增同层功能时无需改订阅；
        新增更深层级时在这里补一条模式。
        """
        prefix = "walle/{}/device/+".format(self.tenant)
        subs = [
            (prefix + "/online", 1),
            (prefix + "/+/response", 1),           # 1 层资源：xxx/response
            (prefix + "/+/+/response", 1),         # 2 层：baseState/query/response、stream/start/response、control/acquire/response
            (prefix + "/+/+/+/response", 1),       # 3 层：record/list/query/response
            (prefix + "/+/+/+/+/response", 1),     # 4 层：car/servo/status/query/response
        ]
        # agent 自身的基本状态查询、配置修改与会话停止（不依赖 chat 是否开启）
        subs.append((agent_base_state_topic("request"), 1))
        subs.append((agent_config_topic("request"), 1))
        subs.append((agent_stop_topic("request"), 1))
        if self.chat_enabled:
            subs.append((chat_topic("request"), 1))
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
        log().warning("MQTT 断开: %s", reason_code)

    def _on_message(self, client, userdata, msg):
        _trace("IN", msg.topic, msg.payload, msg.qos, msg.retain)
        # 对话层消息不是设备主题，先分流，避免被 _topic_device 当成非法设备丢弃
        if self.chat_enabled and msg.topic == chat_topic("request"):
            self._on_chat_message(msg)
            return
        if msg.topic == agent_base_state_topic("request"):
            self._on_agent_state_request(msg)
            return
        if msg.topic == agent_config_topic("request"):
            self._on_agent_config_request(msg)
            return
        if msg.topic == agent_stop_topic("request"):
            self._on_agent_stop_request(msg)
            return
        device = self._topic_device(msg.topic)
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception:  # noqa: BLE001
            payload = msg.payload.decode("utf-8", errors="replace")[:200]
        if not device:
            log().warning("忽略无法解析设备段的主题: %s", msg.topic)
            return
        if (msg.topic.endswith("/control/acquire/response")
                or msg.topic.endswith("/control/release/response")):
            self._on_control_response(payload)
            return
        if isinstance(payload, dict) and payload.get("error_code") == "control_denied":
            log().warning("设备拒绝控制 device=%s request_id=%s holder=%s",
                          device, payload.get("request_id"),
                          (payload.get("control_holder") or {}).get("client_id") or "-")
            with self.control_lock:
                if self.control_held and self.control_held.get("device") == device:
                    self.control_held = None
        item = self._dev(device)
        record = {"topic": msg.topic, "payload": payload,
                  "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
        item["last_result"] = record
        if isinstance(payload, dict) and "online" in msg.topic:
            item["online"] = payload
            item["online_ts"] = time.time()
        if (isinstance(payload, dict) and self.link_rid
                and payload.get("request_id") == self.link_rid
                and (self.link_dev is None or self.link_dev == device)):
            self.link_result = record
            self.link_event.set()
        log().info("MQTT 收到 %s %s", msg.topic,
                   json.dumps(payload, ensure_ascii=False)[:500])

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
                if error == "control_occupied":
                    response.update({"result": -5,
                                     "cause": "设备控制权限已被别人占用"})
                elif error == "mqtt_not_connected":
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
                        "cause": res.get("note") or "ok",
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
        try:
            missing = None
            if not client_id:
                missing = "client_id"
            elif not client_name:
                missing = "client_name"
            elif not request_id:
                missing = "request_id"
            elif not device:
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
        except Exception as exc:  # noqa: BLE001
            self.chat_errors += 1
            response.update({"ok": False, "reply": "agent 内部错误：{}".format(exc),
                             "error": "agent_exception"})
            log().error("chat request %s 处理失败: %s", request_id, exc)
        self.chat_last = {"request_id": request_id, "client_id": client_id,
                          "client_name": client_name, "device": device,
                          "text": text, "ok": response["ok"],
                          "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
        self._publish_topic(chat_topic("response"), response)

    # ---- 会话进度上报（供长会话/LLM 任务使用，当前短会话不调用）----
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
    def _on_control_response(self, payload):
        if not isinstance(payload, dict):
            log().warning("忽略控制权应答：不是 JSON 对象")
            return
        request_id = str(payload.get("request_id") or "")
        with self.control_pending_lock:
            item = self.control_pending.get(request_id)
        if item is None:
            log().info("收到未匹配的控制权应答 request_id=%s", request_id or "-")
            return
        item["result"] = payload
        item["event"].set()

    def _control_request(self, device, action, timeout=5.0):
        """向设备发 control/{acquire|release}/request 并等待对应 response。"""
        request_id = "agent-{}-{}".format(action, random.randint(100000, 999999))
        event = threading.Event()
        with self.control_pending_lock:
            self.control_pending[request_id] = {"event": event, "result": None}
        res = self._publish(device, "control/{}/request".format(action),
                            {"request_id": request_id})
        if not res.get("published"):
            with self.control_pending_lock:
                self.control_pending.pop(request_id, None)
            return {"ok": False,
                    "error": res.get("error") or "control_publish_failed",
                    "request_id": request_id, "publish": res}
        got = event.wait(timeout)
        with self.control_pending_lock:
            item = self.control_pending.pop(request_id, None)
        if not got or not item or not item.get("result"):
            return {"ok": False, "error": "control_timeout",
                    "request_id": request_id, "timeout": timeout}
        return {"ok": True, "request_id": request_id,
                "response": item["result"]}

    @staticmethod
    def _control_result(data):
        try:
            return int((data or {}).get("result"))
        except (TypeError, ValueError):
            return -1

    def _acquire_control(self, device):
        with self.control_lock:
            if self.control_held and self.control_held.get("device") == device:
                return {"ok": True, "action": "already_held",
                        "control_holder": self.control_held.get("control_holder")}
            if self.control_held:
                # 单 agent 同时只持有一台设备的租约，避免覆盖另一台设备
                return {"ok": False, "error": "control_failed",
                        "cause": "agent_holds_other_device"}
        res = self._control_request(device, "acquire")
        if not res.get("ok"):
            error = res.get("error")
            if error == "mqtt_not_connected":
                return res
            return {"ok": False, "error": "control_failed", "cause": error}
        data = res.get("response") or {}
        result = self._control_result(data)
        action = data.get("action")
        holder = data.get("control_holder")
        if result == 0:
            with self.control_lock:
                self.control_held = {"device": device,
                                     "client_id": agent_identity(),
                                     "control_holder": holder}
            log().info("已获取设备控制权 device=%s action=%s holder=%s",
                       device, action, (holder or {}).get("client_id") or "-")
            return {"ok": True, "action": action, "control_holder": holder}
        if result == -5 or action == "occupied":
            log().warning("设备控制权被占用 device=%s holder=%s", device,
                          (holder or {}).get("client_id") or "-")
            return {"ok": False, "error": "control_occupied", "action": action,
                    "control_holder": holder}
        return {"ok": False, "error": "control_failed", "action": action,
                "cause": data.get("cause")}

    def _release_control(self, device):
        with self.control_lock:
            held = self.control_held
            if not held or held.get("device") != device:
                return {"ok": True, "skipped": True}
        res = self._control_request(device, "release")
        with self.control_lock:
            if self.control_held and self.control_held.get("device") == device:
                self.control_held = None
        if not res.get("ok"):
            log().warning("释放设备控制权失败 device=%s error=%s",
                          device, res.get("error"))
            return res
        data = res.get("response") or {}
        result = self._control_result(data)
        action = data.get("action")
        if result == 0:
            log().info("已释放设备控制权 device=%s action=%s", device, action)
            return {"ok": True, "action": action}
        log().warning("释放设备控制权未成功 device=%s action=%s", device, action)
        return {"ok": False, "error": "control_failed", "action": action,
                "control_holder": data.get("control_holder")}

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

    def _run_motion(self, device, intent, client_name):
        duration = self._duration(intent)
        who = sanitize_segment(client_name or agent_identity())[:20] or "agent"
        request_id = "agent-{}-{}".format(who, random.randint(100000, 999999))
        payload = self._drive_payload(intent, request_id)
        hz = max(1.0, float(self.cfg["SEND_HZ"]))
        period = 1.0 / hz
        t0 = time.monotonic()
        log().info("motion start client_name=%s device=%s intent=%s duration=%.2fs",
                   client_name, device, json.dumps(intent, ensure_ascii=False), duration)
        try:
            while not self.abort.is_set():
                if time.monotonic() - t0 >= duration:
                    break
                self._publish(device, "car/motor/set/request", payload)
                time.sleep(period)
        finally:
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
                if self.active_device == device:
                    self.active_device = None
            log().info("motion end client_name=%s device=%s intent=%s elapsed=%.2fs",
                       client_name, device, json.dumps(intent, ensure_ascii=False),
                       time.monotonic() - t0)

    def execute(self, device, intent, client_name):
        if intent["action"] == "stop":
            return self.stop(client_name, device)
        if intent["action"] == "status":
            return {"ok": True, "action": "status", "device": device,
                    "status": self.health(device)}

        duration = self._duration(intent)
        if not (self.mqtt_client and self.mqtt_connected):
            return {"ok": False, "accepted": False, "action": intent["action"],
                    "error": "mqtt_not_connected",
                    "note": "MQTT 未连接，未下发指令"}

        with self.motion_lock:
            with self.state_lock:
                if self.worker is not None and self.worker.is_alive():
                    return {"ok": False, "accepted": False, "action": intent["action"],
                            "duration_sec": round(duration, 2), "error": "busy",
                            "active_device": self.active_device,
                            "note": "小车正在执行上一条指令"}
            control = self._acquire_control(device)
            if not control.get("ok"):
                return {"ok": False, "accepted": False, "action": intent["action"],
                        "error": control.get("error"),
                        "control_holder": control.get("control_holder"),
                        "cause": control.get("cause")}
            with self.state_lock:
                self.abort.clear()
                self.active_device = device
                self.worker = threading.Thread(target=self._run_motion,
                                               args=(device, intent, client_name),
                                               daemon=True)
                self.worker.start()
        return {"ok": True, "accepted": True, "action": intent["action"],
                "device": device, "duration_sec": round(duration, 2),
                "started": True}

    def stop(self, client_name=None, device=None):
        with self.motion_lock:
            with self.state_lock:
                target = device or self.active_device
                worker = self.worker
                should_abort = bool(worker is not None and worker.is_alive()
                                    and (device is None or device == self.active_device))
                if should_abort:
                    self.abort.set()
            if should_abort and worker is not None:
                worker.join(timeout=2.0)
            if not target:
                return {"ok": False, "action": "stop", "error": "no_device",
                        "note": "没有正在运动或已选中的设备", "mqtt": self.mqtt_status()}
            control = self._acquire_control(target)
            if not control.get("ok"):
                return {"ok": False, "action": "stop", "device": target,
                        "error": control.get("error"),
                        "control_holder": control.get("control_holder"),
                        "cause": control.get("cause"), "mqtt": self.mqtt_status()}
            zero = self._drive_payload({"action": "stop"},
                                       "agent-stop-{}".format(random.randint(1000, 9999)))
            res = self._publish(target, "car/motor/set/request", zero)
            release = self._release_control(target)
            log().info("stop requested by %s device=%s result=%s release=%s",
                       client_name, target, json.dumps(res, ensure_ascii=False),
                       json.dumps(release, ensure_ascii=False))
            return {"ok": True, "action": "stop", "device": target,
                    "result": res, "control": release, "mqtt": self.mqtt_status()}

    def stop_session(self, client_name=None, device=None):
        """优雅停止当前会话：中止运动线程 → 零速 → 释放控制权。

        没有进行中的会话且未持有控制权时幂等返回成功（stopped=False）。
        """
        with self.state_lock:
            active = self.active_device
            worker = self.worker
        had_session = bool(worker is not None and worker.is_alive())
        with self.control_lock:
            held = (self.control_held or {}).get("device")
        target = device or active or held
        if not target:
            return {"ok": True, "stopped": False, "released": False,
                    "device": None, "note": "no_active_session"}
        res = self.stop(client_name, target)
        control = res.get("control") or {}
        out = {
            "ok": bool(res.get("ok")),
            "stopped": bool(had_session and (device is None or device == active)),
            "released": bool(control.get("ok") and not control.get("skipped")),
            "device": res.get("device") or target,
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
        rid = "agent-link-{}".format(random.randint(100000, 999999))
        self.link_rid = rid
        self.link_dev = device
        self.link_result = None
        self.link_event.clear()
        res = self._publish(device, "baseState/query/request",
                            {"request_id": rid})
        got = self.link_event.wait(timeout=6.0)
        out = {
            "ok": bool(got),
            "request_id": rid,
            "device": device,
            "published": res.get("published"),
            "topic": res.get("topic"),
            "payload": res.get("payload"),
            "result": self.link_result,
            "mqtt": self.mqtt_status(),
        }
        if not got:
            out["error"] = "no_result_within_6s"
        self.link_rid = None
        self.link_dev = None
        log().info("link test user=%s device=%s ok=%s result=%s", username, device, got,
                   json.dumps(self.link_result, ensure_ascii=False))
        return out

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
            moving = self.worker is not None and self.worker.is_alive()
            active = self.active_device
        with self.dev_lock:
            devices = [{
                "device": key,
                "online": bool(item.get("online")),
                "online_ts": item.get("online_ts"),
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
    "rotate(left/right, angle_deg)、stop、status。"
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
        if intent.get("action") in ("move", "rotate", "stop", "status"):
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
        if result.get("error") == "no_device":
            return "没有可停止的设备，请先选择设备。"
        if result.get("error") == "control_occupied":
            holder = result.get("control_holder") or {}
            who = holder.get("client_name") or holder.get("client_id") or "其他客户端"
            return "设备控制权已被 {} 占用，停止未下发。".format(who)
        if result.get("error") in ("control_failed", "control_timeout"):
            return "获取设备控制权失败，停止未下发。"
        inner = result.get("result") or {}
        if inner.get("published"):
            return "已下发停止（零速）。"
        if inner.get("error") == "mqtt_not_connected":
            return "停止未下发：MQTT 未连接。"
        return "停止未下发。"
    elif action == "status":
        return "状态查询完成。"
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
