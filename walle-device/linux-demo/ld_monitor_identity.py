#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设备身份信息、online 报文与 discovery 应答。"""
import logging
import platform
import random
import threading
import time

import psutil

import ld_config as config
from ld_config import *
from ld_protocol import *
from ld_utils import *

class MonitorIdentityMixin:
    """设备身份、online/LWT 与 discovery 应答。"""
    # ---------- identity 设备身份信息 ----------

    def refresh_identity_cache(self):
        """监控线程刷新完整身份（含磁盘扫描），供 online/LWT 复用。"""
        try:
            info = self.build_device_info(include_disk=True)
        except Exception:
            logging.exception("刷新设备身份缓存失败")
            return
        with self.identity_lock:
            self.identity_cache = info

    def _online_info(self):
        with self.identity_lock:
            cached = dict(self.identity_cache) if self.identity_cache else None
        if cached:
            return cached
        # 首次上线尚无缓存：只做轻量采集，绝不扫描磁盘/网络挂载
        info = self.build_device_info(include_disk=False)
        with self.identity_lock:
            self.identity_cache = info
        return info

    def build_device_info(self, include_disk=True):
        """组装设备身份信息：型号、主机名、IP、系统版本、内核、运行时长等
        （不含 interfaces / mqtt / monitor，保持报文精简）。

        include_disk=False 时跳过 disk_mounts()，用于 online/LWT 等不能卡在
        网络挂载上的路径；此时磁盘总量为 null。
        """
        vm = psutil.virtual_memory()
        boot = psutil.boot_time()
        uptime = int(time.time() - boot)
        try:
            freq = psutil.cpu_freq()
        except (OSError, RuntimeError, SystemError):
            freq = None
        mac = self.mac
        if include_disk:
            mounts = disk_mounts()
            disk_total = sum(m["total_bytes"] for m in mounts)
        else:
            disk_total = None
        model = get_pi_model()

        return {
            "device_id": config.DEVICE_ID,
            "uuid": config.DEVICE_UUID,
            "version_code": VERSION_CODE,
            "version_name": VERSION_NAME,
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
            "disk_total_human": human_size(disk_total) if disk_total is not None else None,
            "uptime_seconds": uptime,
            "uptime_human": human_duration(uptime),
            "boot_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(boot)),
            "timestamp": now_ts(),
            "datetime": now_str(),
        }

    def build_online_payload(self, online=True):
        """online（retained/LWT）只发最小身份：设备号、版本、型号、主机名、IP、运行时长。

        完整身份（MAC/序列号/磁盘/系统详情）只通过 discovery/query 返回，避免订阅该
        租户主题的客户端长期读取敏感信息。使用身份缓存，避免磁盘扫描。
        """
        info = self._online_info()
        return {
            "device_id": info["device_id"],
            "uuid": info["uuid"],
            "online": bool(online),
            "version_code": info.get("version_code"),
            "version_name": info.get("version_name"),
            "device_type": info.get("device_type"),
            "model": info.get("model"),
            "hostname": info.get("hostname"),
            "ip": info.get("ip"),
            "uptime_seconds": info.get("uptime_seconds"),
            "timestamp": now_ts(),
            "datetime": now_str(),
        }

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
        # 抖动：N 台设备同时收到广播时错开回包，避免瞬间灌满 App 与 broker。
        # 用 Timer 延迟发布，处理函数立即返回，不占用查询线程。
        delay = random.uniform(0, DISCOVERY_JITTER_SEC) if DISCOVERY_JITTER_SEC > 0 else 0
        if delay > 0:
            timer = threading.Timer(delay, self._publish_discovery_result, args=(rid,))
            timer.daemon = True
            timer.start()
            return
        self._publish_discovery_result(rid)

    def _publish_discovery_result(self, rid):
        try:
            info = self.build_device_info()
            info["request_id"] = rid
            self.publish_json(RES_DISCOVERY_RESULT, info)
            logging.info("已响应设备身份查询 -> %s（request_id=%s，型号=%s 序列号=%s 主机名=%s）",
                         device_topic(RES_DISCOVERY_RESULT), rid or "-", info.get("model"),
                         info.get("serial"), info.get("hostname"))
        except Exception:
            logging.exception("上报 %s 失败", device_topic(RES_DISCOVERY_RESULT))
