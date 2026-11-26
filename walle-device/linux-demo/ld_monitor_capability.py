#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""摄像头节点缓存与 runState/query/request 运行状态应答。"""
import logging
import time

import ld_config as config
from ld_config import *
from ld_protocol import *
from ld_utils import *
from ld_camera import *

class MonitorCapabilityMixin:
    """摄像头节点缓存与 runState/query/request 运行状态应答。"""
    # ---------- 摄像头节点缓存（stream/start 复用） ----------

    def cameras_cached(self):
        """摄像头探测结果缓存，避免每次推流都跑 v4l2-ctl；USB 插拔时失效。"""
        now = time.time()
        with self._camera_cache_lock:
            if self._camera_cache and self._camera_cache[0] > now:
                return self._camera_cache[1], self._camera_cache[2]
        cameras, error = list_cameras()
        with self._camera_cache_lock:
            self._camera_cache = (time.time() + CAMERA_CACHE_TTL, cameras, error)
        return cameras, error

    def invalidate_camera_cache(self, reason=""):
        with self._camera_cache_lock:
            had_cache = self._camera_cache is not None
            self._camera_cache = None
        if had_cache and reason:
            logging.info("摄像头缓存已失效：%s", reason)

    def camera_nodes(self):
        """当前可用采集节点（推流失败时回给 App，便于重新选择）。"""
        cameras, _ = self.cameras_cached()
        return [{"node": c["node"], "name": c["name"]} for c in cameras]

    # ---------- runState/query/request 运行状态查询 ----------

    def build_runtime_state(self):
        """采集各功能分组的实时运行状态。

        linux-demo 没有底盘/舵机/电池/声呐，也没有录像实现，这些分组的
        is_supported 固定为 false；is_supported=false 时其余字段一律 null / false。
        """
        video_fn = getattr(self, "_video_status", None)
        video = video_fn() if callable(video_fn) else {}
        proc = getattr(self, "audio_proc", None)
        meta = dict(getattr(self, "audio_meta", {}) or {})
        playing = bool(proc is not None and proc.poll() is None)
        audio_input = VIDEO_AUDIO_DEVICE or "default"
        if isinstance(video.get("audio"), dict):
            audio_input = video["audio"].get("device") or audio_input
        return {
            "stream": {
                "is_supported": True,
                "streaming": bool(video.get("streaming")),
                "room_number": video.get("room_number"),
                "camera_device": video.get("camera_device") or (CAMERA_DEVICE or None),
                "audio_input": audio_input,
                "started_at": video.get("started_at"),
                "uptime_sec": video.get("uptime_sec"),
            },
            "phone_audio": {
                "is_supported": True,
                "playing": playing,
                "room_number": meta.get("room_number") if playing else None,
            },
            "recording": {
                "is_supported": False,
                "recording": False,
                "filename": None,
                "camera_device": None,
                "audio_input": None,
            },
            "motor": {"is_supported": False, "moving": False},
            "servo": {"is_supported": False, "moving": False},
            "battery": {"is_supported": False, "voltage_v": None, "available": False},
            "sonar": {"is_supported": False, "distance_cm": None, "available": False},
            "control_holder": self.control_holder_snapshot(),
        }

    def handle_capability_query(self, payload):
        data = self.parse_request(payload, REQ_CAPABILITY)
        if data is None:
            return
        rid = self._rid(data)
        try:
            status = {
                "device_id": config.DEVICE_ID,
                "hostname": HOSTNAME,
                "ip": self.lan_ip,
                "timestamp": now_ts(),
                "datetime": now_str(),
                "request_id": rid,
            }
            status.update(self.build_runtime_state())
            self.publish_json(RES_CAPABILITY_RESULT, status)
            logging.info(
                "已响应运行状态查询 -> %s（request_id=%s，推流 %s，手机音频 %s，录像 %s，"
                "电机 %s，舵机 %s）",
                device_topic(RES_CAPABILITY_RESULT), rid or "-",
                "是" if status["stream"]["streaming"] else "否",
                "是" if status["phone_audio"]["playing"] else "否",
                "是" if status["recording"]["recording"] else "否",
                "是" if status["motor"]["moving"] else "否",
                "是" if status["servo"]["moving"] else "否")
        except Exception:
            logging.exception("上报 runState/query/response 失败")
