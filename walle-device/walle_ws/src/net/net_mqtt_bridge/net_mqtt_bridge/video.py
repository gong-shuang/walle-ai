#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""video/start|stop 结果报文与独立运行占位。

真正的推流实现在 ROS 包 `video_webrtc_bridge`（node.py 会把
`handle_video_request` / `handle_video_stop` / `_stop_video_stream` 覆盖为服务调用）。
本模块只保留结果报文封装，agent 侧不再维护第二套 ffmpeg 推流逻辑（P2-2.2）。
"""

import logging

from . import config
from .identity import now_str, now_ts


class VideoMixin:
    def _publish_video_result(self, req, result_code, cause, extra=None):
        """统一推流结果报文：result 固定语义 -1/0/-2，细节放附加字段"""
        payload = {
            "device_id": config.DEVICE_ID,
            "uuid": config.DEVICE_UUID,
            "hostname": config.HOSTNAME,
            "ip": self.lan_ip,
            "video_device": req.get("video_device"),
            "room_number": req.get("room_number"),
            "request_id": self._rid(req),
            "result": result_code,
            "cause": cause,
            "timestamp": now_ts(),
            "datetime": now_str(),
        }
        if extra:
            payload.update(extra)
        self.publish_json(config.RES_VIDEO_START_RESULT, payload)
        logging.info("推流结果 -> %s: request_id=%s result=%s cause=%s%s",
                     config.device_topic(config.RES_VIDEO_START_RESULT),
                     payload["request_id"] or "-", result_code, cause,
                     f"（{extra['error']}）" if extra and extra.get("error") else "")
        return payload

    def _publish_video_stop_result(self, request_id, result_code, cause, extra=None):
        """统一停止推流结果报文：result -1 当前没有设备在推流 / 0 ok"""
        payload = {
            "device_id": config.DEVICE_ID,
            "uuid": config.DEVICE_UUID,
            "hostname": config.HOSTNAME,
            "ip": self.lan_ip,
            "request_id": str(request_id or ""),
            "result": result_code,
            "cause": cause,
            "timestamp": now_ts(),
            "datetime": now_str(),
        }
        if extra:
            payload.update(extra)
        self.publish_json(config.RES_VIDEO_STOP_RESULT, payload)
        logging.info("停止推流结果 -> %s: request_id=%s result=%s cause=%s%s",
                     config.device_topic(config.RES_VIDEO_STOP_RESULT),
                     payload["request_id"] or "-", result_code, cause,
                     f"（{extra['error']}）" if extra and extra.get("error") else "")
        return payload

    def _stop_video_stream(self, reason=""):
        """本进程不直接管理推流；ROS 模式下由 node.py 覆盖为服务调用。"""
        logging.debug("忽略停止推流（%s）：推流由 video_webrtc_bridge 管理", reason or "-")
        return False

    def handle_video_request(self, payload):
        """独立运行占位：明确告知推流由 video_webrtc_bridge 节点提供。"""
        ok, data = self.verify_uuid(payload, config.REQ_VIDEO_START)
        if not ok:
            return
        video_device = str(data.get("video_device") or "").strip()
        room_number = str(data.get("room_number") or "").strip()
        req = self._video_req(data, video_device, room_number)
        self._publish_video_result(
            req, -2, "命令执行失败",
            {"error": "当前进程不提供推流；请通过 ROS 节点 video_webrtc_bridge 推流"})

    def handle_video_stop(self, payload):
        """独立运行占位：没有本地推流可停。"""
        ok, data = self.verify_uuid(payload, config.REQ_VIDEO_STOP)
        if not ok:
            return
        rid = self._rid(data)
        req_room = str(data.get("room_number") or "").strip()
        self._publish_video_stop_result(
            rid, -1, "当前没有设备在推流",
            {"room_number": req_room or None, "stopped": False,
             "error": "当前进程不提供推流；请通过 ROS 节点 video_webrtc_bridge 推流"})
