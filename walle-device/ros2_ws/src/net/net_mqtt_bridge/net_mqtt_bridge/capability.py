#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runState/query/request：推流/手机音频/录像/电机/舵机/电池/声呐运行状态应答。"""

import logging
import time

from . import config
from .identity import now_str, now_ts
from .record_player import (
    CAMERA_DEVICE, RECORD_AUDIO_INPUT, RECORD_ENABLED,
)


# is_supported 的默认值：YAML 未注入 support_* 时（单测/独立运行）按全部支持处理
DEFAULT_SUPPORT = {
    "stream": True,
    "phone_audio": True,
    "recording": True,
    "motor": True,
    "servo": True,
    "battery": True,
    "sonar": True,
}


class CapabilityMixin:
    def _support_flags(self):
        """功能支持开关：来自 net_mqtt_bridge.yaml 的 support_*（节点注入）。"""
        flags = dict(DEFAULT_SUPPORT)
        flags.update(getattr(self, "support", None) or {})
        return flags

    def build_runtime_state(self):
        """采集各功能分组的实时运行状态。

        is_supported 来自 YAML 的 support_* 开关；recording 还需
        walle_camera.conf 的 record.enabled 同时为 true（取 AND）。
        is_supported=false 时，该分组其余字段一律为 null / false。
        """
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

        support = self._support_flags()
        stream_ok = bool(support["stream"])
        phone_ok = bool(support["phone_audio"])
        rec_ok = bool(support["recording"] and RECORD_ENABLED)
        motor_ok = bool(support["motor"])
        servo_ok = bool(support["servo"])
        battery_ok = bool(support["battery"])
        sonar_ok = bool(support["sonar"])

        audio_input = config.VIDEO_AUDIO_DEVICE or "default"
        if isinstance(video.get("audio"), dict):
            audio_input = video["audio"].get("device") or audio_input

        return {
            "stream": {
                "is_supported": stream_ok,
                "streaming": bool(video.get("streaming")) if stream_ok else False,
                "room_number": video.get("room_number") if stream_ok else None,
                "camera_device": (
                    video.get("camera_device") or CAMERA_DEVICE) if stream_ok else None,
                "audio_input": audio_input if stream_ok else None,
                "started_at": video.get("started_at") if stream_ok else None,
                "uptime_sec": video.get("uptime_sec") if stream_ok else None,
            },
            "phone_audio": {
                "is_supported": phone_ok,
                "playing": phone_playing if phone_ok else False,
                "room_number": (
                    meta.get("room_number") if phone_playing else None)
                if phone_ok else None,
            },
            "recording": {
                "is_supported": rec_ok,
                "recording": bool(rec.get("recording")) if rec_ok else False,
                "filename": rec.get("filename") if rec_ok else None,
                "camera_device": CAMERA_DEVICE if rec_ok else None,
                "audio_input": RECORD_AUDIO_INPUT if rec_ok else None,
            },
            "motor": {
                "is_supported": motor_ok,
                "moving": bool(getattr(self, "motor_moving", False)) if motor_ok else False,
            },
            "servo": {
                "is_supported": servo_ok,
                "moving": (time.monotonic()
                           < float(getattr(self, "servo_moving_until", 0.0) or 0.0))
                if servo_ok else False,
            },
            "battery": {
                "is_supported": battery_ok,
                "voltage_v": battery.get("voltage_v") if battery_ok else None,
                "available": bool(battery.get("available")) if battery_ok else False,
            },
            "sonar": {
                "is_supported": sonar_ok,
                "distance_cm": sonar.get("distance_cm") if sonar_ok else None,
                "available": bool(sonar.get("available")) if sonar_ok else False,
            },
            "control_holder": self.control_holder_snapshot(),
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
                "是" if status["stream"]["streaming"] else "否",
                "是" if status["phone_audio"]["playing"] else "否",
                "是" if status["recording"]["recording"] else "否",
                "是" if status["motor"]["moving"] else "否",
                "是" if status["servo"]["moving"] else "否")
        except Exception:
            logging.exception("上报 runState/query/response 失败")
