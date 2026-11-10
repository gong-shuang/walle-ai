#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设备能力后台探测、缓存与 capability/query 应答。"""
import json
import logging
import threading
import time

import ld_config as config
from ld_config import *
from ld_protocol import *
from ld_utils import *
from ld_camera import *
from ld_audio import *
from ld_media import rtmp_url_ready

class MonitorCapabilityMixin:
    """能力后台探测、缓存与 capability/query 应答。"""
    # ---------- capability/query 设备能力查询（摄像头/麦克风/音响等外设） ----------

    def cameras_cached(self):
        """摄像头探测结果缓存：capability/query 不再每次都跑 v4l2-ctl；USB 插拔时失效。"""
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
            logging.info("摄像头能力缓存已失效：%s", reason)

    def camera_nodes(self):
        """当前可用采集节点（推流失败时回给 App，便于重新选择）。"""
        cameras, _ = self.cameras_cached()
        return [{"node": c["node"], "name": c["name"]} for c in cameras]

    def _capability_refresh_loop(self):
        """后台能力探测：启动即探测，之后按 TTL 或 USB 变化事件刷新。"""
        while not self.stop_event.is_set():
            self._refresh_capability_cache()
            deadline = time.time() + CAPABILITY_CACHE_TTL
            while not self.stop_event.is_set():
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                if self.capability_refresh.wait(min(remaining, 1.0)):
                    self.capability_refresh.clear()
                    break

    def _refresh_capability_cache(self):
        try:
            payload = self._probe_capability()
        except Exception:
            logging.exception("设备能力探测失败，保留上一次缓存")
            return
        if not self.capability_ready.is_set():
            logging.info("设备能力首次探测完成（耗时 %dms）",
                         payload.get("summary", {}).get("probe_cost_ms", 0))
        with self.capability_lock:
            old = (self.capability_cache or {}).get("payload")
            cam_error = payload.get("probe", {}).get("error")
            if (not payload.get("cameras") and cam_error and old and old.get("cameras")):
                # 探测失败（v4l2 超时/异常）不要把已知摄像头能力清空
                logging.warning("摄像头探测失败（%s），沿用上一次缓存", cam_error)
                old_cams = old["cameras"]
                payload["cameras"] = old_cams
                payload["camera_count"] = len(old_cams)
                payload["summary"]["total_cameras"] = len(old_cams)
                payload["summary"]["total_formats"] = old["summary"].get("total_formats", 0)
                payload["summary"]["total_resolutions"] = old["summary"].get("total_resolutions", 0)
                if "camera_video_capture" not in payload["capabilities"]:
                    payload["capabilities"].append("camera_video_capture")
            self.capability_cache = {"probed_at": time.time(), "payload": payload}
        self.capability_ready.set()

    def build_capability(self):
        """只读缓存；首次探测未完成时最多等待 CAPABILITY_FIRST_WAIT，超时返回 pending 占位。"""
        if not self.capability_ready.is_set() and not CAPABILITY_CACHE_ONLY:
            self.capability_ready.wait(CAPABILITY_FIRST_WAIT)
        with self.capability_lock:
            cache = self.capability_cache
            if cache:
                payload = json.loads(json.dumps(cache["payload"]))   # 深拷贝，避免调用方改缓存
                age = max(0.0, time.time() - cache["probed_at"])
                payload["cache"] = {"source": "cache", "age_sec": round(age, 1),
                                    "ttl_sec": CAPABILITY_CACHE_TTL}
                payload["timestamp"] = now_ts()
                payload["datetime"] = now_str()
                return payload
        return self._pending_capability()

    def _pending_capability(self):
        reason = "能力探测尚未完成，请稍后重新查询"
        return {
            "device_id": config.DEVICE_ID,
            "uuid": config.DEVICE_UUID,
            "hostname": HOSTNAME,
            "ip": self.lan_ip,
            "capability": "device",
            "capabilities": ["system_status"],
            "camera_count": 0,
            "cameras": [],
            "audio": {
                "capture": {"available": False, "device": None,
                            "sources": [], "reason": reason},
                "playback": {"available": False, "device": None, "volume_percent": None,
                             "muted": None, "sinks": [], "reason": reason},
            },
            "summary": {"total_cameras": 0, "total_formats": 0, "total_resolutions": 0,
                        "audio_sources": 0, "audio_sinks": 0, "probe_cost_ms": 0},
            "probe": {"tool": "v4l2-ctl + pactl", "error": None,
                      "audio_error": None, "pending": True},
            "cache": {"source": "pending", "age_sec": 0, "ttl_sec": CAPABILITY_CACHE_TTL},
            "timestamp": now_ts(),
            "datetime": now_str(),
        }

    def _probe_capability(self):
        """实际执行 v4l2-ctl / pactl 探测，生成 capability/result 内容（仅后台线程调用）。"""
        t0 = time.time()
        cameras, cam_error = ([], "摄像头能力探测已关闭（CAM_ENABLE=False）")
        if CAM_ENABLE:
            cameras, cam_error = self.cameras_cached()
        capture = probe_audio_capture()
        playback = probe_audio_playback()
        cost_ms = int((time.time() - t0) * 1000)

        total_formats = sum(c["format_count"] for c in cameras)
        total_resolutions = sum(c["resolution_count"] for c in cameras)

        # 能力清单如实反映探测结果：App 按它显隐功能入口
        deps = dependency_status()
        missing_deps = [d["name"] for d in deps if not d["ok"]]
        ffmpeg_ok = "ffmpeg" not in missing_deps
        capabilities = ["system_status"]
        if cameras:
            capabilities.append("camera_video_capture")
        rtmp_ok, rtmp_reason = rtmp_url_ready() if VIDEO_ENABLE else (False, "视频推流已关闭")
        video_error = None
        if VIDEO_ENABLE:
            if not ffmpeg_ok:
                video_error = "未找到 ffmpeg"
            elif not rtmp_ok:
                video_error = rtmp_reason
        if VIDEO_ENABLE and ffmpeg_ok and rtmp_ok:
            capabilities.append("rtmp_push")
        if capture["available"]:
            capabilities.append("audio_capture")
        if playback["available"]:
            capabilities.append("audio_playback")

        return {
            "device_id": config.DEVICE_ID,
            "uuid": config.DEVICE_UUID,
            "hostname": HOSTNAME,
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
                "missing_dependencies": missing_deps,
                "video_error": video_error,  # 非 None 表示推流能力不可用及原因
            },
            "timestamp": now_ts(),
            "datetime": now_str(),
        }

    def handle_capability_query(self, payload):
        # 该主题必须校验 uuid，不匹配/格式非法直接忽略
        ok, data = self.verify_uuid(payload, REQ_CAPABILITY)
        if not ok:
            return
        rid = self._rid(data)
        try:
            result = self.build_capability()
            result["request_id"] = rid
            self.publish_json(RES_CAPABILITY_RESULT, result)
            audio = result["audio"]
            logging.info("已响应能力查询 -> %s（request_id=%s，摄像头 %d 个，麦克风%s，音响%s，耗时 %dms%s）",
                         device_topic(RES_CAPABILITY_RESULT), rid or "-", result["camera_count"],
                         "" if audio["capture"]["available"] else "：无",
                         "" if audio["playback"]["available"] else "：无",
                         result["summary"]["probe_cost_ms"],
                         f"，探测异常: {result['probe']['error']}" if result["probe"]["error"] else "")
        except Exception:
            logging.exception("上报 capability/result 失败")
