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
    # ---------- 摄像头节点缓存（video/start 复用） ----------

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
        """采集推流/手机音频/录像/电机/舵机/电池/声呐的实时运行状态。"""
        video_fn = getattr(self, "_video_status", None)
        video = video_fn() if callable(video_fn) else {}
        proc = getattr(self, "audio_proc", None)
        meta = dict(getattr(self, "audio_meta", {}) or {})
        playing = bool(proc is not None and proc.poll() is None)
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
                "playing": playing,
                "room_number": meta.get("room_number") if playing else None,
            },
            "recording": {"enable": False, "recording": False, "filename": None},
            "motor": {"moving": False},
            "servo": {"moving": False},
            "battery": {"voltage_v": None, "available": False},
            "sonar": {"distance_cm": None, "available": False},
        }

    def handle_capability_query(self, payload):
        # 该主题必须校验 uuid，不匹配/格式非法直接忽略
        ok, data = self.verify_uuid(payload, REQ_CAPABILITY)
        if not ok:
            return
        rid = self._rid(data)
        try:
            status = {
                "device_id": config.DEVICE_ID,
                "uuid": config.DEVICE_UUID,
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
                "是" if status["video"]["streaming"] else "否",
                "是" if status["phone_audio"]["playing"] else "否",
                "是" if status["recording"]["recording"] else "否",
                "是" if status["motor"]["moving"] else "否",
                "是" if status["servo"]["moving"] else "否")
        except Exception:
            logging.exception("上报 runState/query/response 失败")
