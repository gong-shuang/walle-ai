#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""系统监控采样、阈值告警与 state/query 快照。"""

import logging
import os
import platform
import time
from pathlib import Path

import psutil

from . import config
from .identity import (get_pi_model, human_rate, human_size, now_str, now_ts)


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
    if not config.USB_SYSFS.exists():
        return devices
    for dev_path in sorted(config.USB_SYSFS.iterdir()):
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
        if part.fstype in config.SKIP_FSTYPE or part.mountpoint.startswith(("/snap/", "/proc", "/sys")):
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


class MonitorMixin:
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
        elif key in self.active_alarms and value < threshold * config.RECOVER_RATIO:
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

    def _status_sample(self):
        """取 monitor_loop 最近一次采样；尚未产出时返回 None。"""
        with self.status_sample_lock:
            return self.status_sample

    def _update_status_sample(self, cores, per_iface, total_rx, total_tx, interval):
        """monitor_loop 每轮写入 state/query 快照（P2-1.1）。"""
        sample = (cores, per_iface, total_rx, total_tx, interval, time.monotonic())
        with self.status_sample_lock:
            self.status_sample = sample

    def build_status(self):
        """
        state/query 的状态快照。

        P2-1.1：优先复用 monitor_loop 最近一次采样结果，查询路径不再阻塞采样；
        监控尚未产出第一份快照（启动 1s 内或自检脚本）时才现场采样兜底。
        """
        sample = self._status_sample()
        if sample is None:
            cores, per_iface, total_rx, total_tx, interval = self.sample_once(0.2)
            sampled_at = time.monotonic()
        else:
            cores, per_iface, total_rx, total_tx, interval, sampled_at = sample

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
        # USB 列表由 monitor_loop 的 check_usb 每轮刷新，查询直接复用（≤1 个采样周期）
        usb = dict(self.usb_state)

        return {
            "device_id": config.DEVICE_ID,
            "uuid": config.DEVICE_UUID,
            "hostname": config.HOSTNAME,
            "version_code": config.VERSION_CODE,
            "version_name": config.VERSION_NAME,
            "ip": self.lan_ip,
            "timestamp": now_ts(),
            "datetime": now_str(),
            "uptime_seconds": int(time.time() - psutil.boot_time()),
            # CPU/网络数据距采样时刻的秒数（其余分组为应答时实时读取）
            "sample_age_sec": round(max(time.monotonic() - sampled_at, 0.0), 1),
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
                    "cpu_percent": config.THRESH_CPU,
                    "memory_percent": config.THRESH_MEM,
                    "disk_percent": config.THRESH_DISK,
                    "temperature_c": config.THRESH_TEMP,
                    "network_bytes_per_sec": config.THRESH_NET_BPS,
                    "network_rate_human": human_rate(config.THRESH_NET_BPS),
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

    def monitor_loop(self):
        # CPU 百分比首次打点
        with self.sample_lock:
            psutil.cpu_percent(percpu=True)
        prev_net = net_snapshot()
        prev_t = time.time()
        first = True

        while not self.stop_event.is_set():
            # 首次采样提前到 1s，让 state/query 尽快命中缓存
            wait = config.STATUS_PRIME_INTERVAL if first else config.MONITOR_INTERVAL
            first = False
            if self.stop_event.wait(wait):
                break
            now = time.time()
            elapsed = max(now - prev_t, 0.1)

            with self.sample_lock:
                cores = psutil.cpu_percent(percpu=True)
            cpu = round(sum(cores) / len(cores), 1)
            mem = psutil.virtual_memory()
            mounts = disk_mounts()
            temp = get_cpu_temp()
            prev_net, ifaces, rx_rate, tx_rate = calc_net_rates(prev_net, elapsed)
            prev_t = now
            # 先更新查询快照，再做阈值/USB 判断（查询路径直接复用本次采样）
            self._update_status_sample(cores, ifaces, rx_rate, tx_rate, elapsed)

            # 1) 阈值类告警
            self.check_threshold("cpu_high", cpu, config.THRESH_CPU, "cpu", "cpu_high",
                                 "%", "CPU 使用率", {"per_core_percent": cores})
            self.check_threshold("memory_high", mem.percent, config.THRESH_MEM, "memory", "memory_high",
                                 "%", "内存使用率",
                                 {"used_human": human_size(mem.used),
                                  "total_human": human_size(mem.total)})
            if temp is not None:
                self.check_threshold("temperature_high", temp, config.THRESH_TEMP, "temperature",
                                     "temperature_high", "℃", "CPU 温度")
            for mnt in mounts:
                key = f"disk_high:{mnt['mount']}"
                self.check_threshold(key, mnt["percent"], config.THRESH_DISK, "disk", "disk_high",
                                     "%", f"磁盘 {mnt['mount']} 使用率",
                                     {"mount": mnt["mount"], "device": mnt["device"],
                                      "free_human": mnt["free_human"]})
            self.check_threshold("network_rx_high", rx_rate, config.THRESH_NET_BPS, "network",
                                 "network_rx_high", "B/s", "网络入站带宽",
                                 {"rate_human": human_rate(rx_rate),
                                  "threshold_human": human_rate(config.THRESH_NET_BPS)})
            self.check_threshold("network_tx_high", tx_rate, config.THRESH_NET_BPS, "network",
                                 "network_tx_high", "B/s", "网络出站带宽",
                                 {"rate_human": human_rate(tx_rate),
                                  "threshold_human": human_rate(config.THRESH_NET_BPS)})

            # 2) USB 热插拔
            try:
                self.check_usb()
            except Exception:
                logging.exception("USB 扫描异常")

    # ---------- 启动 ----------
