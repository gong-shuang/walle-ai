#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runState/query/request：推流/手机音频/录像/电机/舵机/电池/声呐运行状态应答。"""

import logging
import time

from . import config
from .identity import now_str, now_ts
from .record import RECORD_ENABLED


class CapabilityMixin:
    def build_runtime_state(self):
        """采集推流/手机音频/录像/电机/舵机/电池/声呐的实时运行状态。"""
        video_fn = getattr(self, "_video_status", None)
        try:
            video = video_fn() if callable(video_fn) else {}
        except Exception:
            video = {}
        proc = getattr(self, "audio_proc", None)
        meta = dict(getattr(self, "audio_meta", {}) or {})
        phone_playing = bool(proc is not None and proc.poll() is None)
        rec = getattr(self, "recording_state", None) or {}
        sensor_fn = getattr(self, "sensor_status", None)
        try:
            sensors = sensor_fn() if callable(sensor_fn) else {}
        except Exception:
            sensors = {}
        battery = sensors.get("battery") or {"voltage_v": None, "available": False}
        sonar = sensors.get("sonar") or {"distance_cm": None, "available": False}
        return {
            "video": {
                "streaming": bool(video.get("streaming")),
                "video_device": video.get("video_device"),
                "room_number": video.get("room_number"),
                "audio": video.get("audio"),
                "pid": video.get("pid"),
                "started_at": video.get("started_at"),
                "uptime_sec": video.get("uptime_sec"),
            },
            "phone_audio": {
                "playing": phone_playing,
                "room_number": meta.get("room_number") if phone_playing else None,
            },
            "recording": {
                "enable": RECORD_ENABLED,
                "recording": bool(rec.get("recording")),
                "filename": rec.get("filename"),
            },
            "motor": {"moving": bool(getattr(self, "motor_moving", False))},
            "servo": {
                "moving": time.monotonic()
                < float(getattr(self, "servo_moving_until", 0.0) or 0.0),
            },
            "battery": battery,
            "sonar": sonar,
        }

    def handle_capability_query(self, payload):
        data = self.parse_request(payload, config.REQ_CAPABILITY)
        if data is None:
            return
        rid = self._rid(data)
        try:
            status = {
                "device_id": config.DEVICE_ID,
                "hostname": config.HOSTNAME,
                "ip": self.lan_ip,
                "timestamp": now_ts(),
                "datetime": now_str(),
                "request_id": rid,
            }
            status.update(self.build_runtime_state())
            self.publish_json(config.RES_CAPABILITY_RESULT, status)
            logging.info(
                "已响应运行状态查询 -> %s（request_id=%s，推流 %s，手机音频 %s，录像 %s，"
                "电机 %s，舵机 %s）",
                config.device_topic(config.RES_CAPABILITY_RESULT), rid or "-",
                "是" if status["video"]["streaming"] else "否",
                "是" if status["phone_audio"]["playing"] else "否",
                "是" if status["recording"]["recording"] else "否",
                "是" if status["motor"]["moving"] else "否",
                "是" if status["servo"]["moving"] else "否")
        except Exception:
            logging.exception("上报 runState/query/response 失败")
