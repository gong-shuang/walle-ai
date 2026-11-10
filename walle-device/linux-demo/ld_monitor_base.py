#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RpiMonitor 基础设施：初始化、MQTT 回调、发布与请求分发。"""
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import paho.mqtt.client as mqtt

import ld_config as config
from ld_config import *
from ld_protocol import *
from ld_utils import *
from ld_media import *

class RpiMonitorBase:
    def __init__(self):
        self.client = None
        self.stop_event = threading.Event()
        # 查询与动作分池：推流启动/停止不再挤占 state/capability 查询
        self.query_pool = ThreadPoolExecutor(max_workers=QUERY_WORKERS,
                                             thread_name_prefix="query")
        self.action_pool = ThreadPoolExecutor(max_workers=ACTION_WORKERS,
                                              thread_name_prefix="action")
        self.query_slots = threading.BoundedSemaphore(QUERY_QUEUE_LIMIT)
        self.action_slots = threading.BoundedSemaphore(ACTION_QUEUE_LIMIT)
        self.dropped_counts = {"query": 0, "action": 0}
        self._dropped_reported = {"query": 0, "action": 0}
        self.dropped_lock = threading.Lock()
        # psutil 的 CPU 采样在进程内共享上一次时间点，多线程采样需加锁
        self.sample_lock = threading.Lock()
        self.active_alarms = set()
        self.usb_state = scan_usb_devices()
        self.lan_ip = get_lan_ip()
        self.mac = pick_primary_mac(self.lan_ip)
        # device_id：落盘优先，首次才按硬件指纹（MAC + CPU 核数 + 内存 GiB）生成。
        # 每次重算会因为 MemTotal 浮动而漂移，导致同一台设备换主题、App 出两张卡片。
        self.cpu_cores = get_cpu_core_count()
        self.mem_total = psutil.virtual_memory().total
        config.DEVICE_ID, config.DEVICE_ID_SOURCE = resolve_device_id(
            self.mac, self.cpu_cores, self.mem_total)
        logging.info("设备 device_id: %s（来源：%s；MAC=%s, CPU核心数=%s, 内存总容量=%s字节）",
                     config.DEVICE_ID, config.DEVICE_ID_SOURCE, self.mac, self.cpu_cores, self.mem_total)
        # uuid：程序实例标识（MAC + IP + 启动时间），本次运行不变、下次运行变化
        config.DEVICE_UUID = generate_device_uuid(self.mac, self.lan_ip)
        logging.info("设备 UUID: %s（MAC=%s, IP=%s, 启动时间=%s）",
                     config.DEVICE_UUID, self.mac, self.lan_ip, now_str())
        # 当前推流进程（同一时刻只保留一路推流）
        self.video_proc = None
        self.video_lock = threading.Lock()
        self.video_meta = {}
        # 最近一次非主动停止的推流信息；重新推流成功后发 recover 并清空
        self.video_lost = None
        # 反向音频：手机麦上行后在本地播放的 ffmpeg 进程（与视频互不影响）
        self.audio_proc = None
        self.audio_lock = threading.Lock()
        self.audio_meta = {}
        # 最近一次非主动停止的手机音频播放；重新开始播放后发 recover 并清空
        self.audio_lost = None
        # 摄像头能力缓存：(过期时间, cameras, error)；USB 插拔事件会清空
        self._camera_cache = None
        self._camera_cache_lock = threading.Lock()
        # 上一次应答 discovery 的时间（单调时钟），用于限频
        self._last_discovery_at = 0.0
        # 监控循环产出的最新采样快照，state/query 直接复用（避免请求线程再等 1s）
        self.status_snapshot = None
        self.snapshot_lock = threading.Lock()
        # 设备能力后台探测缓存：查询只读缓存，首次未就绪时等待 CAPABILITY_FIRST_WAIT
        self.capability_cache = None
        self.capability_lock = threading.Lock()
        self.capability_ready = threading.Event()
        self.capability_refresh = threading.Event()
        # 设备身份缓存：online/LWT 只读缓存或轻量采集，不扫描可能卡死的网络挂载
        self.identity_cache = None
        self.identity_lock = threading.Lock()
        self.startup_ready = False   # 首次连上 Broker 并订阅后置位，用于 STARTUP READY
        self.disconnected_at = None  # 断连起始时间；用于长时间断连后回收推流/播放

    def _will_message(self):
        """遗嘱消息：掉线时由 broker 代发 offline 到 walle/{t}/{d}/v1/online。"""
        return device_topic(RES_ONLINE), self.build_online_payload(False)

    # ---------- MQTT 回调 ----------

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            self.disconnected_at = None
            logging.info("MQTT 连接成功: %s:%s（tenant=%s device=%s）",
                         MQTT_HOST, MQTT_PORT, sanitize_segment(config.MQTT_TENANT),
                         sanitize_segment(config.DEVICE_ID))
            subs = subscriptions()
            client.subscribe(subs)
            # 上线通知（retained；掉线时由遗嘱发 offline）
            self.publish_json(RES_ONLINE, self.build_online_payload(True), retain=True)
            logging.info("已订阅 %d 个主题；当前 USB 设备 %d 个作为基线，不产生历史告警",
                         len(subs), len(self.usb_state))
            for t, _ in subs:
                logging.info("  · %s", t)
            if not self.startup_ready:
                self.startup_ready = True
                logging.info("STARTUP READY broker=%s:%s device=%s version=%s",
                             MQTT_HOST, MQTT_PORT, sanitize_segment(config.DEVICE_ID), VERSION_NAME)
        else:
            logging.error("MQTT 连接失败，原因码: %s", reason_code)

    def on_connect_fail(self, client, userdata):
        logging.warning("MQTT 连接 Broker 失败（%s:%s），按重连间隔继续重试",
                        MQTT_HOST, MQTT_PORT)

    def on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        if self.disconnected_at is None:
            self.disconnected_at = time.time()
        logging.warning("MQTT 连接断开（reason_code=%s），paho 将自动重连", reason_code)

    def on_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8", errors="replace")
        except Exception:
            payload = ""
        logging.info("收到消息 %s（%d 字节）", msg.topic, len(msg.payload))
        if logging.getLogger().isEnabledFor(logging.DEBUG):
            logging.debug("收到消息 %s: %s", msg.topic, mask_payload(payload))
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

    def publish_json(self, resource, data, retain=False, wait_timeout=None):
        """按资源名发布到 walle/{tenant}/{device}/v1/{resource}。

        参数 resource 用模块顶部的 RES_* 常量，例如 RES_ALARM / RES_STATE_RESULT。
        返回 False 表示未送达（未连接/队列满等），调用方无需处理，但日志会留下警告。
        """
        return self._publish_one(device_topic(resource), data, retain, wait_timeout)

    def _publish_one(self, topic, data, retain=False, wait_timeout=None):
        info = self.client.publish(
            topic, json.dumps(data, ensure_ascii=False), qos=MQTT_QOS, retain=retain
        )
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            logging.warning("MQTT 发布失败（rc=%s），消息未送达: %s", info.rc, topic)
            return False
        if wait_timeout is not None:
            try:
                info.wait_for_publish(timeout=wait_timeout)
            except (RuntimeError, ValueError) as exc:
                logging.warning("等待 MQTT 发布完成失败（%s）：%s", topic, exc)
                return False
        logging.debug("publish -> %s mid=%s", topic, info.mid)
        return True

    def _handle_request(self, topic, payload):
        """把请求分发到对应处理函数。

        注意两种主题层级不同：
          · 设备级请求：walle/{tenant}/{device}/v1/{resource}   （5 段）
          · 发现广播  ：walle/{tenant}/v1/discovery/query         （4 段，没有 device 段）
        早期实现统一按 4 段切分，导致 discovery 被解析成 device="discovery"、
        resource="query"，匹配不到处理函数而被静默忽略 —— 设备永远不会应答发现。
        """
        if topic == tenant_topic(REQ_DISCOVERY):
            self._submit_request("query", self.handle_device_query, payload)
            return

        parts = topic.split("/", 4)
        if len(parts) < 5 or parts[3] != PROTOCOL_VERSION:
            logging.warning("忽略无法解析的主题: %s", topic)
            return
        resource = parts[4]
        handler_name = REQUEST_HANDLERS.get(resource)
        if handler_name:
            kind = "query" if resource in QUERY_RESOURCES else "action"
            self._submit_request(kind, getattr(self, handler_name), payload)
        else:
            logging.debug("忽略非请求类主题: %s", topic)

    def _submit_request(self, kind, func, payload):
        """把请求投递到对应线程池；超过「执行中 + 排队」上限时丢弃并计数。"""
        pool, slots = ((self.query_pool, self.query_slots) if kind == "query"
                       else (self.action_pool, self.action_slots))
        if not slots.acquire(blocking=False):
            with self.dropped_lock:
                self.dropped_counts[kind] += 1
                total = self.dropped_counts[kind]
            logging.warning("请求队列已满，丢弃 %s 请求（累计 %d）", kind, total)
            return False

        def _run():
            try:
                func(payload)
            except Exception:
                logging.exception("%s 请求处理异常", kind)
            finally:
                slots.release()

        pool.submit(_run)
        return True

    def _event(self, event, level, metric, alarm_type, message, detail=None):
        """统一告警报文封装"""
        payload = {
            "device_id": config.DEVICE_ID,
            "uuid": config.DEVICE_UUID,
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
        if added or removed:
            # 摄像头节点编号可能随插拔变化，立即失效缓存，下一次 capability 重新探测
            self.invalidate_camera_cache("USB 设备变化")
            self.capability_refresh.set()
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
