#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MQTT 回调、请求分发与设备身份/状态查询。"""

import json
import logging
import platform
import random
import time

import psutil
import paho.mqtt.client as mqtt

from . import config
from .identity import (get_cpu_model, get_device_serial, get_os_description,
                        get_os_pretty_name, get_pi_model, human_duration,
                        human_size, now_str, now_ts)
from .monitor import disk_mounts, get_cpu_temp


class ProtocolMixin:
    def _will_message(self):
        """遗嘱消息：掉线时由 broker 代发 offline 到 walle/{t}/device/{d}/online。"""
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
    def _is_client_presence_topic(topic):
        """是否为 walle/{tenant}/client/{client_id}/online。"""
        parts = str(topic or "").split("/")
        return (len(parts) == 5 and parts[0] == config.TOPIC_ROOT
                and parts[1] == config.sanitize_segment(config.MQTT_TENANT)
                and parts[2] == config.CLIENT_SEGMENT
                and parts[4] == "online")

    def _handle_client_presence(self, topic, payload):
        """记录 App/Web 客户端在线状态；只做记录，不回复。"""
        data = self._safe_json(payload)
        if not data:
            logging.warning("忽略 %s：报文为空或不是 JSON 对象", topic)
            return
        client_id = str(data.get("client_id") or "").strip()
        topic_client_id = topic.split("/")[3]
        if not client_id or client_id != topic_client_id:
            logging.warning("忽略 %s：client_id 与主题不一致（收到=%s）",
                            topic, client_id or "<空>")
            return
        online = data.get("online") is True
        record = {
            "client_id": client_id,
            "client_name": str(data.get("client_name") or "")[:64],
            "version_code": data.get("version_code"),
            "version_name": str(data.get("version_name") or "")[:32],
            "online": online,
            "timestamp": data.get("timestamp") or now_ts(),
            "last_seen": now_ts(),
        }
        with self.client_presence_lock:
            self.client_presence[client_id] = record
        if hasattr(self, "control_lease"):
            self.control_lease.on_presence(client_id, online, time.time())
        if online:
            logging.info("客户端上线: %s（%s，版本 %s/%s）", client_id,
                         record["client_name"] or "-",
                         record["version_name"] or "-", record["version_code"])
        else:
            logging.info("客户端离线: %s（%s）", client_id,
                         record["client_name"] or "-")

    def client_is_online(self, client_id):
        """指定客户端当前是否在线（供后续控制权租约使用）。"""
        with self.client_presence_lock:
            item = self.client_presence.get(str(client_id or ""))
        return bool(item and item.get("online"))

    @staticmethod
    def _is_agent_online_topic(topic):
        """是否为 walle/{tenant}/agent/{agent_id}/online。"""
        parts = str(topic or "").split("/")
        return (len(parts) == 5 and parts[0] == config.TOPIC_ROOT
                and parts[1] == config.sanitize_segment(config.MQTT_TENANT)
                and parts[2] == config.AGENT_SEGMENT
                and parts[4] == "online")

    def _handle_agent_presence(self, topic, payload):
        """记录 agent 在线状态；只做记录，不回复。"""
        data = self._safe_json(payload)
        if not data:
            logging.warning("忽略 %s：报文为空或不是 JSON 对象", topic)
            return
        agent_id = str(data.get("agent_id") or "").strip()
        topic_agent_id = topic.split("/")[3]
        if not agent_id or agent_id != topic_agent_id:
            logging.warning("忽略 %s：agent_id 与主题不一致（收到=%s）",
                            topic, agent_id or "<空>")
            return
        online = data.get("online") is True
        record = {
            "agent_id": agent_id,
            "agent_name": str(data.get("agent_name") or "")[:64],
            "version_code": data.get("version_code"),
            "version_name": str(data.get("version_name") or "")[:32],
            "online": online,
            "ts": data.get("ts") or now_ts(),
            "last_seen": now_ts(),
        }
        with self.agent_presence_lock:
            self.agent_presence[agent_id] = record
        # agent online 同时作为控制权 presence：掉线后按 §4.19 grace 释放租约
        if hasattr(self, "control_lease"):
            self.control_lease.on_presence(agent_id, online, time.time())
        if online:
            logging.info("agent 上线: %s（%s，版本 %s/%s）", agent_id,
                         record["agent_name"] or "-",
                         record["version_name"] or "-", record["version_code"])
        else:
            logging.info("agent 离线: %s（%s）", agent_id,
                         record["agent_name"] or "-")

    def agent_is_online(self, agent_id):
        """指定 agent 当前是否在线。"""
        with self.agent_presence_lock:
            item = self.agent_presence.get(str(agent_id or ""))
        return bool(item and item.get("online"))

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
        if config.MQTT_DEBUG_JSON:
            try:
                _rx_data = json.loads(payload) if payload else None
                _rx_text = json.dumps(_rx_data, ensure_ascii=False, indent=2) if _rx_data is not None else payload
            except (json.JSONDecodeError, TypeError, ValueError):
                _rx_text = payload
            logging.info("[MQTT-RX] %s\n%s", msg.topic, _rx_text)
        if self._is_client_presence_topic(msg.topic):
            self._handle_client_presence(msg.topic, payload)
            return
        if self._is_agent_online_topic(msg.topic):
            self._handle_agent_presence(msg.topic, payload)
            return
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

    @classmethod
    def parse_request(cls, payload, topic):
        """解析请求体；空/非法 JSON 或非对象返回 None（只记日志，不回复）。"""
        data = cls._safe_json(payload)
        if not data:
            logging.warning("忽略 %s：报文为空或不是 JSON 对象", topic)
            return None
        return data

    def _video_req(self, data):
        """推流请求的归一化副本：V1.11 起应答只回显 request_id（房间号由设备生成）。"""
        return {
            "request_id": self._rid(data),
        }

    def publish_json(self, resource, data, retain=False):
        """按资源名发布到 walle/{tenant}/device/{device}/{resource}。

        参数 resource 用模块顶部的 RES_* 常量，例如 RES_ALARM / RES_STATE_RESULT。
        """
        self._publish_one(config.device_topic(resource), data, retain)

    def _publish_one(self, topic, data, retain=False):
        if self.client is None:
            # 启动/重连窗口：agent 线程还没建好客户端，丢弃本次上报（不阻塞传感器回调）
            logging.warning("MQTT 客户端未就绪，丢弃上报: %s", topic)
            return
        text = json.dumps(data, ensure_ascii=False)
        if config.MQTT_DEBUG_JSON:
            try:
                pretty = json.dumps(data, ensure_ascii=False, indent=2)
            except (TypeError, ValueError):
                pretty = text
            logging.info("[MQTT-TX] %s\n%s", topic, pretty)
        info = self.client.publish(topic, text, qos=config.MQTT_QOS, retain=retain)
        # paho 的 publish() 出错只体现在 rc 上（不抛异常），不检查就会静默丢报文
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            logging.error("报文发布失败 %s（rc=%s：%s）", topic, info.rc,
                          mqtt.error_string(info.rc))
        logging.debug("publish -> %s mid=%s rc=%s", topic, info.mid, info.rc)

    def _handle_request(self, topic, payload):
        """把请求分发到对应处理函数。

        注意两种主题层级不同：
          · 设备级请求：walle/{tenant}/device/{device}/{resource}   （至少 5 段）
          · 发现广播  ：walle/{tenant}/discovery/query/request       （5 段，没有 device 段）
        发现主题先按完整主题特判；其余主题第 3 段必须是固定的 device。
        """
        if topic == config.tenant_topic(config.REQ_DISCOVERY):
            self._submit(self.query_pool, self._query_slots,
                         self.handle_device_query, payload, topic)
            return

        parts = topic.split("/", 4)
        if len(parts) < 5 or parts[2] != config.DEVICE_SEGMENT:
            logging.warning("忽略无法解析的主题: %s", topic)
            return
        resource = parts[4]
        handler_name = config.REQUEST_HANDLERS.get(resource)
        if not handler_name:
            logging.debug("忽略非请求类主题: %s", topic)
            return
        handler = getattr(self, handler_name)
        # 控制权租约：查询始终放行（持有者续租）；控制类必须匹配当前 holder，
        # 没有 holder 时 fail-open，所有人都可以执行。
        if resource not in config.CONTROL_EXEMPT_REQUESTS:
            data = self._safe_json(payload)
            client_id = str(data.get("client_id") or "")
            allowed, _is_holder = self.control_lease.touch(client_id, time.time())
            if resource not in config.QUERY_REQUESTS and not allowed:
                self.publish_control_denied(resource, data)
                return
        # 实时控制类请求（车控）只做校验 + 发布，直接在 MQTT 回调线程执行；
        # 排进查询/动作队列会被慢查询拖到几百毫秒，200ms 的控制周期就失效了。
        if resource in config.REALTIME_REQUESTS:
            try:
                handler(payload)
            except Exception:
                logging.exception("实时请求处理失败: %s", topic)
            return
        if resource in config.ACTION_REQUESTS:
            self._submit(self.action_pool, self._action_slots, handler, payload, topic)
        elif resource in config.SERVO_REQUESTS:
            self._submit(self.servo_pool, self._servo_slots, handler, payload, topic)
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
            except Exception:
                # 线程池里的异常不会冒泡（Future 无人取结果），必须自己记日志，
                # 否则处理函数出错时表现为"设备收到了请求但没有任何应答"。
                logging.exception("处理请求失败: %s", topic)
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
            "alarm_source_type": "device",
            "alarm_source_id": config.DEVICE_ID,
            "alarm_source_name": config.HOSTNAME,
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
        """online（retained/LWT）只发最小身份，字段精简以降低常驻报文体积。

        完整身份（MAC/序列号/磁盘/系统详情）只通过 discovery/query/response 返回，避免订阅该
        租户主题的客户端长期读取敏感信息；这里跳过磁盘扫描，避免阻塞连接路径。
        """
        info = self.build_device_info(include_disk=False)
        return {
            "device_id": info["device_id"],
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
            # 与 online 保持完全一致：列表页只展示最小身份，设备详情走 baseState/query/request
            payload = self.build_online_payload(True)
            self.publish_json(config.RES_DISCOVERY_RESULT, payload)
            logging.info("已响应设备发现查询 -> %s（request_id=%s，型号=%s 主机名=%s）",
                         config.device_topic(config.RES_DISCOVERY_RESULT), rid or "-",
                         payload.get("model"), payload.get("hostname"))
        except Exception:
            logging.exception("上报 %s 失败", config.device_topic(config.RES_DISCOVERY_RESULT))

    # ---------- runState/query/request 运行状态查询（能力 + 实时状态） ----------

    def handle_query_status(self, payload):
        data = self.parse_request(payload, config.REQ_STATE)
        if data is None:
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
