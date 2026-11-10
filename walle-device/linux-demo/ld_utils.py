#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通用工具：时间/容量格式化、网络与设备信息、USB/磁盘扫描、设备标识。"""
import hashlib
import json
import logging
import os
import platform
import re
import socket
import subprocess
import time
import uuid
from pathlib import Path

import psutil

import ld_config as config
from ld_config import *

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


def _norm_mac(mac):
    """MAC 归一化（小写、去空格），用于比对"还是不是同一块网卡"。"""
    return (mac or "").strip().lower()


def _default_device_id_file():
    """标识文件路径优先级：

    1. `DEVICE_ID_FILE` 显式指定；
    2. 安装目录已存在的 `.device-id`（升级兼容，原地保留）；
    3. `/var/lib/walle-ai/device-id`（部署脚本创建、属主为运行用户）；
    4. 安装目录 `.device-id`（目录可写时）；
    5. `~/.config/walle-ai/device-id`（兜底）。
    """
    if config.DEVICE_ID_FILE:
        return config.DEVICE_ID_FILE
    here = os.path.dirname(os.path.abspath(__file__))
    beside = os.path.join(here, ".device-id")
    if os.path.isfile(beside):
        return beside
    system = "/var/lib/walle-ai/device-id"
    if os.access(os.path.dirname(system), os.W_OK):
        return system
    if os.access(here, os.W_OK):
        return beside
    return os.path.join(os.path.expanduser("~"), ".config", "walle-ai", "device-id")


def _read_device_state(path):
    """读取标识文件；不存在/损坏都返回 {}（损坏时打警告，随后会被重建）。"""
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
        return state if isinstance(state, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logging.warning("设备标识文件不可用（%s）：%s；将重新生成", path, exc)
        return {}


def _write_device_state(path, state):
    """原子写入标识文件；失败只打警告，不影响本次运行。"""
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
        return True
    except OSError as exc:
        logging.warning("设备标识写入失败（%s）：%s；本次仅在内存中生效，重启后可能变化", path, exc)
        return False


def generate_device_id(mac, cpu_cores, mem_total_bytes):
    """
    按硬件指纹生成设备唯一标识：MAC + CPU 核心数 + 内存 GiB → md5 → 前 16 位十六进制。

    注意内存**必须按 GiB 取整**：MemTotal 在两次启动之间会浮动几 KiB（内核保留页、
    启动参数、内存训练），直接进哈希会让同一台硬件算出不同的 device_id。
    """
    mem_gib = int(round(mem_total_bytes / (1024 ** 3))) if mem_total_bytes else 0
    raw = f"{mac}|{cpu_cores}|{mem_gib}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def resolve_device_id(mac, cpu_cores, mem_total, persist=True):
    """得到本机 device_id：**落盘优先**，没有（或换了机器）才按硬件指纹生成。

    为什么要落盘：device_id 同时是 MQTT client_id、主题段和 ACL 的 ${clientid}，
    一旦变化就是"换了个新设备"——新主题无人订阅、旧主题上的 retained online 变成
    幽灵卡片。而硬件指纹本身并不完全稳定，所以只把它当作**首次生成**的种子。

    复用条件：文件里有 device_id，且当前 MAC 在文件记录的 MAC 集合里（支持多网卡
    切换顺序）。MAC 从没见过 → 认为换了硬件（或克隆镜像到新机器）→ 重新生成。

    @param persist False 时只读不写（--check 用，避免带副作用）
    @return (device_id, 来源说明)
    """
    path = _default_device_id_file()
    state = _read_device_state(path)
    stored = str(state.get("device_id") or "").strip()
    macs = {_norm_mac(m) for m in (state.get("macs") or []) if str(m).strip()}
    cur = _norm_mac(mac)

    if stored and (not cur or cur in macs):
        source = f"复用 {path}"
        if cur and cur not in macs:            # 同一台机器新增了网卡
            macs.add(cur)
            if persist:
                _write_device_state(path, {**state, "macs": sorted(macs)})
        return stored, source

    if stored:
        logging.warning("MAC 与已保存的标识不匹配（当前 %s，历史 %s），按新设备重新生成",
                        cur or "-", ", ".join(sorted(macs)) or "-")

    new_id = generate_device_id(mac, cpu_cores, mem_total)
    if not persist:
        return new_id, "按硬件指纹计算（--check 不写入文件）"

    history = [str(x) for x in (state.get("previous") or []) if str(x).strip()]
    if stored and stored != new_id:
        history = (history + [stored])[-10:]
        logging.warning("device_id 由 %s 变为 %s：旧主题上的 retained online 需要清理"
                        "（App 侧会按 MAC 归并成同一台设备）", stored, new_id)
    ok = _write_device_state(path, {
        "device_id": new_id,
        "macs": sorted(macs | ({cur} if cur else set())),
        "previous": history,
        "fingerprint": {"mac": mac, "cpu_cores": cpu_cores, "mem_bytes": mem_total,
                        "mem_gib": int(round((mem_total or 0) / (1024 ** 3)))},
        "created_at": now_str(),
    })
    return new_id, (f"新生成并写入 {path}" if ok else "新生成（未能落盘）")


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
