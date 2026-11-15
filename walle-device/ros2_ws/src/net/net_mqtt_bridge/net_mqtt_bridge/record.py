#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""录像（record/*）结果报文封装与节点侧服务调用。

包含两个 mixin：
  - RecordMixin：挂载在 agent（RpiMonitor）上，负责 MQTT 结果报文封装；
  - RecordNodeMixin：挂载在 net_mqtt_bridge 节点上，负责调用 record_video_node
    的 ROS2 服务并把结果通过 RecordMixin 发布出去。

真正的录像实现在 ROS 包 `record_video`（record_video_node 订阅 /image_raw 并落盘
fMP4）。
"""

import logging
import threading

from . import config
from .identity import now_str, now_ts


class RecordMixin:
    """agent 侧：录像 MQTT 结果报文封装。"""

    def _publish_record_start_result(self, req, result_code, cause, extra=None):
        payload = {
            "device_id": config.DEVICE_ID,
            "uuid": config.DEVICE_UUID,
            "hostname": config.HOSTNAME,
            "ip": self.lan_ip,
            "request_id": self._rid(req),
            "result": result_code,
            "cause": cause,
            "timestamp": now_ts(),
            "datetime": now_str(),
        }
        if extra:
            payload.update(extra)
        self.publish_json(config.RES_RECORD_START_RESULT, payload)
        logging.info("录像开始结果 -> %s: request_id=%s result=%s cause=%s",
                     config.device_topic(config.RES_RECORD_START_RESULT),
                     payload["request_id"] or "-", result_code, cause)
        return payload

    def _publish_record_stop_result(self, req, result_code, cause, extra=None):
        payload = {
            "device_id": config.DEVICE_ID,
            "uuid": config.DEVICE_UUID,
            "hostname": config.HOSTNAME,
            "ip": self.lan_ip,
            "request_id": self._rid(req),
            "result": result_code,
            "cause": cause,
            "timestamp": now_ts(),
            "datetime": now_str(),
        }
        if extra:
            payload.update(extra)
        self.publish_json(config.RES_RECORD_STOP_RESULT, payload)
        logging.info("录像停止结果 -> %s: request_id=%s result=%s cause=%s",
                     config.device_topic(config.RES_RECORD_STOP_RESULT),
                     payload["request_id"] or "-", result_code, cause)
        return payload

    def _publish_record_list_result(self, req, result_code, cause, extra=None):
        payload = {
            "device_id": config.DEVICE_ID,
            "uuid": config.DEVICE_UUID,
            "hostname": config.HOSTNAME,
            "ip": self.lan_ip,
            "request_id": self._rid(req),
            "result": result_code,
            "cause": cause,
            "timestamp": now_ts(),
            "datetime": now_str(),
        }
        if extra:
            payload.update(extra)
        self.publish_json(config.RES_RECORD_LIST_RESULT, payload)
        logging.info("录像列表结果 -> %s: request_id=%s result=%s cause=%s count=%s",
                     config.device_topic(config.RES_RECORD_LIST_RESULT),
                     payload["request_id"] or "-", result_code, cause,
                     len(extra.get("records", [])) if extra else 0)
        return payload

    def _publish_record_delete_result(self, req, result_code, cause, extra=None):
        payload = {
            "device_id": config.DEVICE_ID,
            "uuid": config.DEVICE_UUID,
            "hostname": config.HOSTNAME,
            "ip": self.lan_ip,
            "request_id": self._rid(req),
            "result": result_code,
            "cause": cause,
            "timestamp": now_ts(),
            "datetime": now_str(),
        }
        if extra:
            payload.update(extra)
        self.publish_json(config.RES_RECORD_DELETE_RESULT, payload)
        logging.info("录像删除结果 -> %s: request_id=%s result=%s cause=%s",
                     config.device_topic(config.RES_RECORD_DELETE_RESULT),
                     payload["request_id"] or "-", result_code, cause)
        return payload


class RecordNodeMixin:
    """net_mqtt_bridge 节点侧：调用 record_video_node 的 ROS2 服务。

    放在本模块是为了让 node.py 保持在 500 行以内；ROS 消息延迟导入，
    这样纯校验单测不需要 ROS 环境也能 import 本模块。
    """

    def setup_record_clients(self):
        """声明录像参数并创建三个服务的客户端，供 __init__ 调用。"""
        from interfaces.srv import RecordControl, RecordDelete, RecordList
        self.declare_parameter('record_control_service', '/record_video/control')
        self.declare_parameter('record_list_service', '/record_video/list')
        self.declare_parameter('record_delete_service', '/record_video/delete')
        self.declare_parameter('record_call_timeout', 10.0)
        self.declare_parameter('record_http_port', 8088)
        self._record_control_client = self.create_client(
            RecordControl,
            self.get_parameter('record_control_service').value)
        self._record_list_client = self.create_client(
            RecordList,
            self.get_parameter('record_list_service').value)
        self._record_delete_client = self.create_client(
            RecordDelete,
            self.get_parameter('record_delete_service').value)
        self._record_call_timeout = float(
            self.get_parameter('record_call_timeout').value)
        self._record_http_port = int(
            self.get_parameter('record_http_port').value)

    def _call_record(self, client, request):
        """通用录像服务调用：等待服务就绪 + 异步调用 + 超时。"""
        if not client.service_is_ready():
            try:
                client.wait_for_service(timeout_sec=2.0)
            except Exception:  # noqa: BLE001
                pass
        if not client.service_is_ready():
            return None
        future = client.call_async(request)
        done = threading.Event()
        box = {}

        def _done(fut):
            try:
                box['response'] = fut.result()
            except Exception as exc:  # noqa: BLE001
                box['error'] = exc
            done.set()

        future.add_done_callback(_done)
        if not done.wait(self._record_call_timeout):
            future.cancel()
            return None
        return box.get('response')

    def _record_play_url(self, filename):
        """生成录像文件的 HTTP 播放地址。"""
        lan_ip = getattr(self.agent, 'lan_ip', None) or '127.0.0.1'
        return "http://%s:%d/recordings/%s" % (lan_ip, self._record_http_port, filename)

    def _handle_record_start(self, payload):
        from interfaces.srv import RecordControl
        ok, data = self.agent.verify_uuid(
            payload, self._agent_mod.REQ_RECORD_START)
        if not ok:
            return
        req = data
        srv_req = RecordControl.Request()
        srv_req.action = "start"
        srv_req.filename = str(data.get('filename') or '').strip()
        try:
            srv_req.segment_sec = int(data.get('segment_sec') or 0)
        except (TypeError, ValueError):
            srv_req.segment_sec = 0

        resp = self._call_record(self._record_control_client, srv_req)
        if resp is None:
            self.agent._publish_record_start_result(
                req, -4, '执行失败',
                {'error': '录像服务不可用（record_video_node 未启动或未就绪）'})
            return
        extra = {
            'filename': resp.filename or None,
            'started_at': resp.started_at or None,
            'play_url': self._record_play_url(resp.filename) if resp.filename else None,
        }
        if resp.started_ts > 0:
            extra['started_ts'] = float(resp.started_ts)
        if resp.result != 0:
            extra['error'] = resp.cause
        self.agent._publish_record_start_result(
            req, int(resp.result), resp.cause or 'ok', extra)

    def _handle_record_stop(self, payload):
        from interfaces.srv import RecordControl
        ok, data = self.agent.verify_uuid(
            payload, self._agent_mod.REQ_RECORD_STOP)
        if not ok:
            return
        req = data
        srv_req = RecordControl.Request()
        srv_req.action = "stop"
        srv_req.filename = str(data.get('filename') or '').strip()

        resp = self._call_record(self._record_control_client, srv_req)
        if resp is None:
            self.agent._publish_record_stop_result(
                req, -4, '执行失败',
                {'error': '录像服务不可用（record_video_node 未启动或未就绪）'})
            return
        extra = {'filename': resp.filename or None}
        if resp.result != 0:
            extra['error'] = resp.cause
        self.agent._publish_record_stop_result(
            req, int(resp.result), resp.cause or 'ok', extra)

    def _handle_record_list(self, payload):
        from interfaces.srv import RecordList
        ok, data = self.agent.verify_uuid(
            payload, self._agent_mod.REQ_RECORD_LIST)
        if not ok:
            return
        req = data
        srv_req = RecordList.Request()
        resp = self._call_record(self._record_list_client, srv_req)
        if resp is None:
            self.agent._publish_record_list_result(
                req, -4, '执行失败',
                {'error': '录像服务不可用（record_video_node 未启动或未就绪）',
                 'records': [], 'used_bytes': 0, 'limit_bytes': 0})
            return
        records = []
        for info in resp.records:
            records.append({
                'filename': info.filename,
                'size_bytes': int(info.size_bytes),
                'created_at': info.created_at,
                'duration_sec': int(info.duration_sec),
                'recording': bool(info.recording),
                'play_url': self._record_play_url(info.filename),
            })
        extra = {
            'records': records,
            'used_bytes': int(resp.used_bytes),
            'limit_bytes': int(resp.limit_bytes),
        }
        self.agent._publish_record_list_result(
            req, int(resp.result), resp.cause or 'ok', extra)

    def _handle_record_delete(self, payload):
        from interfaces.srv import RecordDelete
        ok, data = self.agent.verify_uuid(
            payload, self._agent_mod.REQ_RECORD_DELETE)
        if not ok:
            return
        req = data
        filename = str(data.get('filename') or '').strip()
        if not filename:
            self.agent._publish_record_delete_result(
                req, -3, '非法路径', {'error': 'filename 不能为空', 'filename': None})
            return
        srv_req = RecordDelete.Request()
        srv_req.filename = filename
        resp = self._call_record(self._record_delete_client, srv_req)
        if resp is None:
            self.agent._publish_record_delete_result(
                req, -4, '执行失败',
                {'error': '录像服务不可用（record_video_node 未启动或未就绪）',
                 'filename': filename})
            return
        extra = {'filename': resp.filename or filename}
        if resp.result != 0:
            extra['error'] = resp.cause
        self.agent._publish_record_delete_result(
            req, int(resp.result), resp.cause or 'ok', extra)

