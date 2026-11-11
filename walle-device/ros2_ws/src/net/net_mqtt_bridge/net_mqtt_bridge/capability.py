#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""capability/query：外设能力探测与后台缓存。"""

import copy
import logging
import time

from . import config
from .audio import probe_audio_capture, probe_audio_playback
from .identity import now_str, now_ts
from .media import list_cameras


class CapabilityMixin:
    def build_capability(self):
        """采集本机外设能力（摄像头 + 麦克风 + 音响），生成 capability/result 报文"""
        t0 = time.time()
        cameras, cam_error = ([], "摄像头能力探测已关闭（CAM_ENABLE=False）")
        if config.CAM_ENABLE:
            cameras, cam_error = list_cameras()
        capture = probe_audio_capture()
        playback = probe_audio_playback()
        cost_ms = int((time.time() - t0) * 1000)

        total_formats = sum(c["format_count"] for c in cameras)
        total_resolutions = sum(c["resolution_count"] for c in cameras)

        # 能力清单如实反映探测结果：App 按它显隐功能入口
        capabilities = ["system_status"]
        if cameras:
            capabilities.append("camera_video_capture")
        if config.VIDEO_ENABLE:
            capabilities.append("rtmp_push")
        if capture["available"]:
            capabilities.append("audio_capture")
        if playback["available"]:
            capabilities.append("audio_playback")

        return {
            "device_id": config.DEVICE_ID,
            "uuid": config.DEVICE_UUID,
            "hostname": config.HOSTNAME,
            "ip": self.lan_ip,
            "capability": "device",
            "capabilities": capabilities,
            "camera_count": len(cameras),
            "cameras": cameras,
            "audio": {
                "capture": capture,
                "playback": playback,
            },
            "summary": {
                "total_cameras": len(cameras),
                "total_formats": total_formats,
                "total_resolutions": total_resolutions,
                "audio_sources": len(capture["sources"]),
                "audio_sinks": len(playback["sinks"]),
                "probe_cost_ms": cost_ms,
            },
            "probe": {
                "tool": "v4l2-ctl + pactl",
                "error": cam_error,          # None 表示摄像头探测正常
                "audio_error": capture["reason"] or playback["reason"],
            },
            "timestamp": now_ts(),
            "datetime": now_str(),
        }

    def refresh_capability(self):
        """
        探测一次外设能力并写入缓存（P2-1.2）。

        由后台线程调用，绝不在 MQTT 请求线程里跑 v4l2/pactl；同一时刻只允许
        一个探测，重复调用直接返回 False。探测失败时保留上一次缓存。
        """
        with self.capability_lock:
            if self.capability_refreshing:
                return False
            self.capability_refreshing = True
        try:
            result = self.build_capability()
            with self.capability_lock:
                self.capability_result = result
                self.capability_at = time.monotonic()
            self.capability_ready.set()
            logging.info("能力缓存已刷新（摄像头 %d 个，耗时 %dms）",
                         result.get("camera_count", 0),
                         result.get("summary", {}).get("probe_cost_ms", 0))
            return True
        except Exception:
            logging.exception("能力探测失败，保留上一次缓存")
            return False
        finally:
            with self.capability_lock:
                self.capability_refreshing = False

    def capability_snapshot(self):
        """返回 (缓存副本, 缓存年龄秒)；从未探测成功时返回 (None, None)。"""
        with self.capability_lock:
            result = self.capability_result
            at = self.capability_at
        if result is None:
            return None, None
        return copy.deepcopy(result), max(time.monotonic() - at, 0.0)

    def _capability_loop(self):
        """后台能力缓存：启动后立即探测一次，之后按 CAPABILITY_CACHE_TTL 刷新。"""
        while not self.stop_event.is_set():
            self.refresh_capability()
            if self.stop_event.wait(max(config.CAPABILITY_CACHE_TTL, 1.0)):
                break

    def _capability_pending(self, reason):
        """首次探测尚未完成时的占位应答：不阻塞查询，提示调用方稍后重试。"""
        empty_capture = {"available": False, "device": None, "sources": [], "reason": reason}
        empty_playback = {"available": False, "device": None, "sinks": [],
                          "volume_percent": None, "muted": None, "reason": reason}
        return {
            "device_id": config.DEVICE_ID,
            "uuid": config.DEVICE_UUID,
            "hostname": config.HOSTNAME,
            "ip": self.lan_ip,
            "capability": "device",
            "capabilities": ["system_status"],
            "camera_count": 0,
            "cameras": [],
            "audio": {"capture": empty_capture, "playback": empty_playback},
            "summary": {
                "total_cameras": 0,
                "total_formats": 0,
                "total_resolutions": 0,
                "audio_sources": 0,
                "audio_sinks": 0,
                "probe_cost_ms": 0,
            },
            "probe": {
                "tool": "v4l2-ctl + pactl",
                "error": reason,
                "audio_error": reason,
                "pending": True,
            },
            "timestamp": now_ts(),
            "datetime": now_str(),
        }

    def handle_capability_query(self, payload):
        # 该主题必须校验 uuid，不匹配/格式非法直接忽略
        ok, data = self.verify_uuid(payload, config.REQ_CAPABILITY)
        if not ok:
            return
        rid = self._rid(data)
        try:
            result, age = self.capability_snapshot()
            if result is None and not config.CAPABILITY_CACHE_ONLY:
                # 首次查询：后台探测已在跑，这里只做有上限的等待，不在请求线程探测
                deadline = time.monotonic() + max(config.CAPABILITY_FIRST_WAIT, 0.0)
                while time.monotonic() < deadline:
                    self.capability_ready.wait(0.1)
                    result, age = self.capability_snapshot()
                    if result is not None:
                        break
            if result is None:
                # 只读缓存模式或首次探测未完成：立刻回占位报文
                reason = ("能力探测尚未完成（CAPABILITY_CACHE_ONLY=True，查询只读缓存）"
                          if config.CAPABILITY_CACHE_ONLY else "能力探测进行中，请稍后重新查询")
                result = self._capability_pending(reason)
                result["cache"] = {"source": "pending", "age_sec": None,
                                   "ttl_sec": config.CAPABILITY_CACHE_TTL}
                result["request_id"] = rid
                self.publish_json(config.RES_CAPABILITY_RESULT, result)
                logging.info("能力探测尚未完成，返回占位报文 -> %s（request_id=%s）",
                             config.device_topic(config.RES_CAPABILITY_RESULT), rid or "-")
                return
            result["cache"] = {"source": "cache", "age_sec": round(age, 1),
                               "ttl_sec": config.CAPABILITY_CACHE_TTL}
            result["request_id"] = rid
            self.publish_json(config.RES_CAPABILITY_RESULT, result)
            audio = result["audio"]
            logging.info("已响应能力查询 -> %s（request_id=%s，摄像头 %d 个，麦克风%s，音响%s，缓存 %.1fs，探测耗时 %dms%s）",
                         config.device_topic(config.RES_CAPABILITY_RESULT), rid or "-", result["camera_count"],
                         "" if audio["capture"]["available"] else "：无",
                         "" if audio["playback"]["available"] else "：无",
                         age, result["summary"]["probe_cost_ms"],
                         f"，探测异常: {result['probe'].get('error')}" if result["probe"].get("error") else "")
        except Exception:
            logging.exception("上报 capability/result 失败")

    # ---------- video/start 推流 ----------
