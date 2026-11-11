#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MQTT 回调、请求分发与设备身份/状态查询。"""

import json
import logging
import platform
import random
import time

import psutil

from . import config
from .identity import (get_cpu_model, get_device_serial, get_os_description,
                        get_os_pretty_name, get_pi_model, human_duration,
                        human_size, now_str, now_ts)
from .monitor import disk_mounts, get_cpu_temp


class ProtocolMixin:
    def _will_message(self):
        """遗嘱消息：掉线时由 broker 代发 offline 到 walle/{t}/{d}/v1/online。"""
        return config.device_topic(config.RES_ONLINE), self.build_online_payload(False)

    # ---------- MQTT 回调 ----------

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            logging.info("MQTT 连接成功: %s:%s（tenant=%s device=%s）",
                         config.MQTT_HOST, config.MQTT_PORT,
                         config.sanitize_segment(config.MQTT_TENANT),
                         config.sanitize_segment(config.DEVICE_ID))
            subs = config.subscriptions()
            client.subscribe(subs)
            # 上线通知（retained；掉线时由遗嘱发 offline）
            self.publish_json(config.RES_ONLINE, self.build_online_payload(True), retain=True)
            logging.info("已订阅 %d 个主题；当前 USB 设备 %d 个作为基线，不产生历史告警",
                         len(subs), len(self.usb_state))
            for t, _ in subs:
                logging.info("  · %s", t)
        else:
            logging.error("MQTT 连接失败，原因码: %s", reason_code)

    def on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        logging.warning("MQTT 连接断开（reason_code=%s），paho 将自动重连", reason_code)

    @staticmethod
    def _redact_payload(payload):
        """脱敏后的报文，仅用于 DEBUG 日志。"""
        sensitive = {"password", "secret", "whip_secret", "token", "key",
                     "api_key", "room", "room_no", "room_number"}
        try:
            data = json.loads(payload)
        except Exception:
            return payload[:120]

        def redact(obj):
            if isinstance(obj, dict):
                return {k: ("***" if str(k).lower() in sensitive else redact(v))
                        for k, v in obj.items()}
            if isinstance(obj, list):
                return [redact(v) for v in obj]
            return obj

        return json.dumps(redact(data), ensure_ascii=False)[:300]

    def on_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8", errors="replace")
        except Exception:
            payload = ""
        # 默认只记录主题与长度；DEBUG 级别才输出脱敏后的报文
        logging.info("收到消息 %s（%d 字节）", msg.topic, len(payload))
        logging.debug("消息内容 %s: %s", msg.topic, self._redact_payload(payload))
        if msg.topic.startswith(config.TOPIC_ROOT + "/"):
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

        参数 resource 用模块顶部的 RES_* 常量，例如 RES_ALARM / RES_STATE_RESULT。
        """
        self._publish_one(config.device_topic(resource), data, retain)

    def _publish_one(self, topic, data, retain=False):
        info = self.client.publish(
            topic, json.dumps(data, ensure_ascii=False), qos=config.MQTT_QOS, retain=retain
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
        if topic == config.tenant_topic(config.REQ_DISCOVERY):
            self._submit(self.query_pool, self._query_slots,
                         self.handle_device_query, payload, topic)
            return

        parts = topic.split("/", 4)
        if len(parts) < 5 or parts[3] != config.PROTOCOL_VERSION:
            logging.warning("忽略无法解析的主题: %s", topic)
            return
        resource = parts[4]
        handler_name = config.REQUEST_HANDLERS.get(resource)
        if not handler_name:
            logging.debug("忽略非请求类主题: %s", topic)
            return
        handler = getattr(self, handler_name)
        if resource in config.ACTION_REQUESTS:
            self._submit(self.action_pool, self._action_slots, handler, payload, topic)
        else:
            self._submit(self.query_pool, self._query_slots, handler, payload, topic)

    def _submit(self, pool, slots, handler, payload, topic):
        """把请求提交到指定线程池；执行中 + 排队超过上限时丢弃并计数（P2-1.4）。

        MQTT 回调线程只做投递，绝不执行处理函数；采样线程（monitor_loop）
        独立于这里的两个池，因此请求洪峰不会阻塞监控采样。
        """
        if not slots.acquire(blocking=False):
            self._note_dropped(topic)
            return

        def _run():
            try:
                handler(payload)
            finally:
                slots.release()

        try:
            pool.submit(_run)
        except RuntimeError:          # 退出阶段池已关闭
            slots.release()
            logging.debug("请求池已关闭，忽略 %s", topic)

    def _note_dropped(self, topic):
        with self._dropped_lock:
            self.dropped_requests += 1
            total = self.dropped_requests
            now = time.monotonic()
            if now - self._last_drop_log < 5.0:
                return
            self._last_drop_log = now
        logging.warning("请求队列已满，丢弃 %s（累计 %d 个）", topic, total)

    def _event(self, event, level, metric, alarm_type, message, detail=None):
        """统一告警报文封装"""
        payload = {
            "device_id": config.DEVICE_ID,
            "uuid": config.DEVICE_UUID,
            "hostname": config.HOSTNAME,
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
        self.publish_json(config.RES_ALARM, payload)
        logging.warning("[%s] %s %s", level, alarm_type, message)

    # ---------- 告警状态机（带回滞） ----------

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
            "version_code": config.VERSION_CODE,
            "version_name": config.VERSION_NAME,
            "os_description": get_os_description(),  # os-release 的 PRETTY_NAME
            "device_type": ("raspberrypi"
                            if model and "raspberry pi" in model.lower()
                            else "embedded-linux"),
            "vendor": "Raspberry Pi Foundation" if model else None,
            "model": model,
            "serial": get_device_serial(),
            "hostname": config.HOSTNAME,
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
        """online（retained/LWT）只发最小身份，字段与 linux-demo 保持一致。

        完整身份（MAC/序列号/磁盘/系统详情）只通过 discovery/query 返回，避免订阅该
        租户主题的客户端长期读取敏感信息；这里跳过磁盘扫描，避免阻塞连接路径。
        """
        info = self.build_device_info(include_disk=False)
        return {
            "device_id": info["device_id"],
            "uuid": info["uuid"],
            "online": bool(online),
            "version_code": info.get("version_code"),
            "version_name": info.get("version_name"),
            "os_description": info.get("os_description"),
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
        if last > 0 and now - last < config.DISCOVERY_MIN_INTERVAL_SEC:
            logging.info("忽略重复的设备身份查询（%.1fs 内已应答过，request_id=%s）",
                         now - last, rid or "-")
            return
        self._last_discovery_at = now
        # 抖动：N 台设备同时收到广播时错开回包，避免瞬间灌满 App 与 broker
        if config.DISCOVERY_JITTER_SEC > 0:
            time.sleep(random.uniform(0, config.DISCOVERY_JITTER_SEC))
        try:
            info = self.build_device_info()
            info["request_id"] = rid
            self.publish_json(config.RES_DISCOVERY_RESULT, info)
            logging.info("已响应设备身份查询 -> %s（request_id=%s，型号=%s 序列号=%s 主机名=%s）",
                         config.device_topic(config.RES_DISCOVERY_RESULT), rid or "-", info.get("model"),
                         info.get("serial"), info.get("hostname"))
        except Exception:
            logging.exception("上报 %s 失败", config.device_topic(config.RES_DISCOVERY_RESULT))

    # ---------- capability/query 设备能力查询（摄像头/麦克风/音响等外设） ----------

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
        if not req_uuid or req_uuid != config.DEVICE_UUID.lower():
            logging.warning("忽略 %s：uuid 不匹配（收到=%s，本地=%s）",
                            topic, req_uuid or "<空>", config.DEVICE_UUID)
            return False, None
        return True, data

    def handle_query_status(self, payload):
        ok, data = self.verify_uuid(payload, config.REQ_STATE)
        if not ok:
            return
        rid = self._rid(data)
        try:
            status = self.build_status()
            status["request_id"] = rid
            self.publish_json(config.RES_STATE_RESULT, status)
            logging.info("已响应状态查询 -> %s（request_id=%s）",
                         config.device_topic(config.RES_STATE_RESULT), rid or "-")
        except Exception:
            logging.exception("采集/上报 %s 失败", config.device_topic(config.RES_STATE_RESULT))

    # ---------- 周期监控主循环 ----------
