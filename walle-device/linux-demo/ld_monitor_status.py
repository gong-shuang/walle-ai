#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""状态采集、state/query 应答、周期监控循环与进程启动/退出。"""
import logging
import os
import platform
import threading
import time

import psutil
import paho.mqtt.client as mqtt

import ld_config as config
from ld_config import *
from ld_protocol import *
from ld_utils import *

class MonitorStatusMixin:
    """状态采集、state/query、监控循环与进程生命周期。"""
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
        snapshot = None
        with self.snapshot_lock:
            if self.status_snapshot:
                snapshot = dict(self.status_snapshot)
        if snapshot:
            cores = snapshot["cores"]
            per_iface = snapshot["per_iface"]
            total_rx = snapshot["total_rx"]
            total_tx = snapshot["total_tx"]
            interval = snapshot["interval"]
            sample_age = max(0.0, time.time() - snapshot["sampled_at"])
        else:
            # 监控循环还没产出第一份快照（刚启动）：现场补采一次，尽量短
            cores, per_iface, total_rx, total_tx, interval = self.sample_once(0.2)
            sample_age = 0.0

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
        with self.snapshot_lock:
            usb = dict(self.usb_state)   # 复用 check_usb() 的结果，避免请求线程再扫一遍

        return {
            "device_id": config.DEVICE_ID,
            "uuid": config.DEVICE_UUID,
            "hostname": HOSTNAME,
            "version_code": VERSION_CODE,
            "version_name": VERSION_NAME,
            "ip": self.lan_ip,
            "timestamp": now_ts(),
            "datetime": now_str(),
            "uptime_seconds": int(time.time() - psutil.boot_time()),
            "sample_age_sec": round(sample_age, 2),
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
        if not req_uuid or req_uuid != config.DEVICE_UUID.lower():
            logging.warning("忽略 %s：uuid 不匹配（收到=%s，本地=%s）",
                            topic, req_uuid or "<空>", config.DEVICE_UUID)
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

    # ---------- 周期监控主循环 ----------

    def _enforce_stream_leases(self):
        """到期回收：最长推流/播放时长、MQTT 长时间断连后释放外设。"""
        now = time.time()
        with self.video_lock:
            vproc, vmeta = self.video_proc, dict(self.video_meta)
        if (VIDEO_MAX_DURATION_SEC > 0 and vproc and vproc.poll() is None
                and vmeta.get("started_ts")
                and now - vmeta["started_ts"] >= VIDEO_MAX_DURATION_SEC):
            self._stop_video_stream(f"达到最长推流时长 {VIDEO_MAX_DURATION_SEC}s")
            self._event("alarm", "info", "video", "video_timeout",
                        f"推流达到最长时长 {VIDEO_MAX_DURATION_SEC}s，已自动停止",
                        {"room_number": vmeta.get("room_number"),
                         "max_duration_sec": VIDEO_MAX_DURATION_SEC,
                         "started_at": vmeta.get("started_at")})

        with self.audio_lock:
            aproc, ameta = self.audio_proc, dict(self.audio_meta)
        if (PHONE_AUDIO_MAX_DURATION_SEC > 0 and aproc and aproc.poll() is None
                and ameta.get("started_ts")
                and now - ameta["started_ts"] >= PHONE_AUDIO_MAX_DURATION_SEC):
            self._stop_phone_audio(f"达到最长播放时长 {PHONE_AUDIO_MAX_DURATION_SEC}s")
            self._event("alarm", "info", "audio", "phone_audio_timeout",
                        f"手机音频达到最长时长 {PHONE_AUDIO_MAX_DURATION_SEC}s，已自动停止",
                        {"room_number": ameta.get("room_number"),
                         "max_duration_sec": PHONE_AUDIO_MAX_DURATION_SEC,
                         "started_at": ameta.get("started_at")})

        if (MQTT_DISCONNECT_STOP_SEC > 0 and self.disconnected_at
                and now - self.disconnected_at >= MQTT_DISCONNECT_STOP_SEC):
            stopped_video = self._stop_video_stream("MQTT 断连超时")
            stopped_audio = self._stop_phone_audio("MQTT 断连超时")
            if stopped_video or stopped_audio:
                logging.warning("MQTT 已断连 %.0fs，已停止推流/手机音频",
                                now - self.disconnected_at)
            self.disconnected_at = None

    def monitor_loop(self):
        # CPU 百分比首次打点
        with self.sample_lock:
            psutil.cpu_percent(percpu=True)
        prev_net = net_snapshot()
        prev_t = time.time()
        last_identity_refresh = 0.0

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
            prev_net, per_iface, rx_rate, tx_rate = calc_net_rates(prev_net, elapsed)
            with self.snapshot_lock:
                self.status_snapshot = {
                    "sampled_at": now,
                    "cores": cores,
                    "per_iface": per_iface,
                    "total_rx": rx_rate,
                    "total_tx": tx_rate,
                    "interval": elapsed,
                }
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

            # 3) 推流/手机音频的租约与断连回收
            try:
                self._enforce_stream_leases()
            except Exception:
                logging.exception("流租约检查异常")

            # 4) 最近一个周期丢弃的请求汇总
            with self.dropped_lock:
                recent = {k: self.dropped_counts[k] - self._dropped_reported[k]
                          for k in self.dropped_counts}
                totals = dict(self.dropped_counts)
                self._dropped_reported = dict(self.dropped_counts)
            if recent["query"] or recent["action"]:
                logging.warning(
                    "最近一个周期丢弃请求：query %d、action %d（累计 query %d、action %d）",
                    recent["query"], recent["action"], totals["query"], totals["action"])

            # 5) 定时刷新身份缓存（含磁盘扫描，只在监控线程里做）
            if IDENTITY_CACHE_TTL > 0 and now - last_identity_refresh >= IDENTITY_CACHE_TTL:
                self.refresh_identity_cache()
                last_identity_refresh = now

    # ---------- 启动 ----------

    def run(self):
        errors = validate_config()
        if errors:
            raise SystemExit("配置错误，拒绝启动: " + "; ".join(errors))
        logging.info("设备 %s (%s) 启动监控，采样周期 %ss", config.DEVICE_ID, self.lan_ip, MONITOR_INTERVAL)
        logging.info("协议 v1: tenant=%s device=%s broker=%s:%s",
                     sanitize_segment(config.MQTT_TENANT), sanitize_segment(config.DEVICE_ID),
                     MQTT_HOST, MQTT_PORT)
        for warning in dependency_warnings():
            logging.warning("依赖缺失: %s", warning)
        hint = audio_session_hint()
        if hint:
            logging.warning("%s", hint)
        threading.Thread(target=self._capability_refresh_loop, daemon=True,
                         name="capability-probe").start()
        if sanitize_segment(config.MQTT_TENANT) == sanitize_segment(MQTT_USER):
            logging.warning(
                "租户与 MQTT 用户名相同（%s）。如果手机 App 用的是另一个账号登录，"
                "请设置 config.MQTT_TENANT=<App 的登录用户名>，"
                "否则 App 订阅 walle/<App用户名>/# 时收不到本设备的报文。",
                sanitize_segment(config.MQTT_TENANT))
        # clean_session=True：每次连接都重新订阅，改了请求主题名也不会残留旧订阅
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id=config.DEVICE_ID, clean_session=True)
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
        self.client.on_connect_fail = self.on_connect_fail
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message
        self.client.reconnect_delay_set(min_delay=3, max_delay=30)
        # TLS：生产环境强制开启，validate_config 已拒绝明文配置
        if MQTT_TLS == "on":
            import ssl
            try:
                if MQTT_CA_FILE:
                    ctx = ssl.create_default_context(cafile=MQTT_CA_FILE)
                else:
                    ctx = ssl.create_default_context()
            except OSError as exc:
                raise SystemExit(f"加载 MQTT_CA_FILE 失败: {exc}")
            self.client.tls_set_context(ctx)
            logging.info("启用 TLS 连接 %s:%s（CA=%s）",
                         MQTT_HOST, MQTT_PORT, MQTT_CA_FILE or "system")
        else:
            logging.warning("调试模式：使用明文 MQTT 连接 %s:%s（WALLE_RUNTIME_ENV=%s）",
                            MQTT_HOST, MQTT_PORT, WALLE_RUNTIME_ENV)
        # connect_async + loop_start：Broker 不可达时由 paho 按 reconnect_delay_set
        # （3s → 30s）在后台持续重试，进程不会快速退出形成 systemd crash-loop；
        # 连上后在 on_connect 中订阅并打印 STARTUP READY。
        self.client.connect_async(MQTT_HOST, MQTT_PORT, MQTT_KEEPALIVE)
        self.client.loop_start()
        logging.info("正在连接 Broker（异步，失败自动重试）...")

        try:
            self.monitor_loop()
        finally:
            logging.info("正在退出，停止推流、发送 offline 并断开连接...")
            self._stop_video_stream("程序退出")
            self._stop_phone_audio("程序退出")
            try:
                # 正常退出时把 online 置为 offline，并等 PUBACK 后再断开；
                # 避免 publish 后立即 disconnect 导致 retained offline 没发出去。
                self.publish_json(RES_ONLINE, self.build_online_payload(False),
                                  retain=True, wait_timeout=2.0)
            except Exception:
                pass
            self.query_pool.shutdown(wait=False, cancel_futures=True)
            self.action_pool.shutdown(wait=False, cancel_futures=True)
            self.client.loop_stop()
            self.client.disconnect()
