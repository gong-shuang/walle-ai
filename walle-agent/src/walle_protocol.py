#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Walle agent 通用请求-应答总线（M1-01）。

职责边界：
  - 发布前按 request_id 注册 pending；
  - 收到 response 后校验 topic/device，并按 request_id 唤醒等待者；
  - 处理超时、重复/迟到、control_denied、设备离线、MQTT 断开；
  - 不负责控制权租约，也不用于 5Hz 的 car/motor/set 持续帧。

主题与报文格式沿用 doc/MQTT_protocol_convention.md，不引入协议变更。
"""

import logging
import secrets
import threading
import time
from collections import Counter
from dataclasses import dataclass, field

LOG = logging.getLogger(__name__)


# §2.8 统一错误码：result -> (error, 默认 cause)
RESULT_CODES = {
    0: (None, "ok"),
    -1: ("invalid_request", "请求无效"),
    -2: ("bad_param", "参数错误"),
    -3: ("unavailable", "资源不可用"),
    -4: ("conflict", "状态冲突"),
    -5: ("control_denied", "控制权被其他客户端占用"),
    -6: ("failed", "执行失败"),
    -7: ("no_space", "存储空间不足"),
    -8: ("safety", "前方有障碍物"),
}


def decode_result(payload):
    """把设备 response 统一解码为 ok / error / cause / holder。"""
    data = payload if isinstance(payload, dict) else {}
    try:
        result = int(data.get("result"))
    except (TypeError, ValueError):
        return {"ok": False, "result": None, "error": "invalid_response",
                "cause": "应答缺少合法 result", "holder": None}
    error, default_cause = RESULT_CODES.get(result, ("unknown", "未知错误码"))
    cause = str(data.get("cause") or "").strip() or default_cause
    holder = data.get("control_holder") if result == -5 else None
    return {"ok": result == 0, "result": result, "error": error,
            "cause": cause, "holder": holder}


def new_request_id(tool):
    """agent 主动请求的统一编号：agent-{tool}-{8hex}。"""
    return "agent-{}-{}".format(tool, secrets.token_hex(4))


# 一对一请求 -> 应答资源；discovery（一请求多应答）不在此表。
REQUEST_TO_RESPONSE = {
    "baseState/query/request": "baseState/query/response",
    "runState/query/request": "runState/query/response",
    "stream/start/request": "stream/start/response",
    "stream/stop/request": "stream/stop/response",
    "phone/audio/send/request": "phone/audio/send/response",
    "phone/audio/stop/request": "phone/audio/stop/response",
    "car/motor/set/request": "car/motor/set/response",
    "car/servo/status/query/request": "car/servo/status/query/response",
    "car/servo/set/request": "car/servo/set/response",
    "record/start/request": "record/start/response",
    "record/stop/request": "record/stop/response",
    "record/list/query/request": "record/list/query/response",
    "record/delete/request": "record/delete/response",
    "record/play/start/request": "record/play/start/response",
    "record/play/stop/request": "record/play/stop/response",
    "control/acquire/request": "control/acquire/response",
    "control/release/request": "control/release/response",
}


@dataclass
class PendingRequest:
    request_id: str
    device: str
    request_topic: str
    response_topic: str
    event: threading.Event = field(default_factory=threading.Event)
    created_at: float = 0.0
    thread_id: int = 0
    payload: dict = None
    decoded: dict = None
    error: str = None
    cause: str = None


class RequestBus:
    """线程安全的请求-应答总线：一个 request_id 对应一个等待者。"""

    def __init__(self, publish, topic_for, *, default_timeout=6.0,
                 is_online=None, decode=None, request_id_factory=None):
        self._publish = publish
        self._topic_for = topic_for
        self._default_timeout = max(0.01, float(default_timeout))
        self._is_online = is_online or (lambda device: True)
        self._decode = decode or decode_result
        self._new_request_id = request_id_factory or new_request_id
        self._lock = threading.Lock()
        self._pending = {}
        self._stats = Counter()

    def request(self, device, resource, payload=None, *, timeout=None,
                response_resource=None, require_online=True):
        """发布 request 并等待对应 response。

        返回统一结构：
          received=True  表示收到了匹配的 response（即使 result 非 0）；
          ok=True        表示 response 的 result == 0。
        """
        started = time.monotonic()
        response_resource = response_resource or REQUEST_TO_RESPONSE.get(resource)
        if not response_resource:
            return self._failure(
                started, device, resource, None, None, "unknown_resource",
                "没有配置对应的 response 资源")
        if payload is not None and not isinstance(payload, dict):
            return self._failure(
                started, device, resource, None, None, "invalid_payload",
                "payload 必须是 JSON 对象")

        body = dict(payload or {})
        request_id = str(body.get("request_id") or "").strip()
        if not request_id:
            tool = str(resource).split("/", 1)[0] or "request"
            request_id = self._new_request_id(tool)
            body["request_id"] = request_id

        request_topic = self._topic_for(device, resource)
        response_topic = self._topic_for(device, response_resource)

        if require_online and not self._is_online(device):
            with self._lock:
                self._stats["device_offline"] += 1
            return self._failure(
                started, device, resource, request_topic, response_topic,
                "device_offline", "设备离线", request_id=request_id)

        pending = PendingRequest(
            request_id=request_id,
            device=device,
            request_topic=request_topic,
            response_topic=response_topic,
            created_at=started,
            thread_id=threading.get_ident(),
        )
        with self._lock:
            if request_id in self._pending:
                self._stats["duplicate_id"] += 1
                return self._failure(
                    started, device, resource, request_topic, response_topic,
                    "duplicate_request_id", "request_id 已在等待应答",
                    request_id=request_id)
            self._pending[request_id] = pending
            self._stats["pending"] = len(self._pending)

        publish_result = {}
        try:
            try:
                publish_result = self._publish(device, resource, body) or {}
            except Exception as exc:  # noqa: BLE001
                LOG.exception("request publish failed")
                publish_result = {
                    "published": False,
                    "error": "publish_exception: {}".format(exc),
                }
            if not publish_result.get("published"):
                with self._lock:
                    self._pending.pop(request_id, None)
                    self._stats["publish_failed"] += 1
                    self._stats["pending"] = len(self._pending)
                return self._failure(
                    started, device, resource, request_topic, response_topic,
                    "publish_failed", "请求发布失败",
                    request_id=request_id, publish=publish_result)

            wait_timeout = (self._default_timeout if timeout is None
                            else max(0.0, float(timeout)))
            got = pending.event.wait(wait_timeout)
        finally:
            with self._lock:
                self._pending.pop(request_id, None)
                self._stats["pending"] = len(self._pending)

        elapsed_ms = int((time.monotonic() - started) * 1000)
        if not got or pending.error:
            error = pending.error or "timeout"
            cause = pending.cause or (
                "等待应答超时" if error == "timeout" else error)
            with self._lock:
                self._stats[error] += 1
            return self._failure(
                started, device, resource, request_topic, response_topic,
                error, cause, request_id=request_id,
                publish=publish_result, elapsed_ms=elapsed_ms)

        decoded = pending.decoded or self._safe_decode(pending.payload)
        if not decoded.get("ok"):
            with self._lock:
                self._stats["business_error"] += 1
        return {
            "ok": bool(decoded.get("ok")),
            "received": True,
            "request_id": request_id,
            "device": device,
            "request_topic": request_topic,
            "response_topic": response_topic,
            "payload": pending.payload,
            "decoded": decoded,
            "result": decoded.get("result"),
            "cause": decoded.get("cause"),
            "error": decoded.get("error"),
            "published": True,
            "publish": publish_result,
            "elapsed_ms": elapsed_ms,
        }

    def on_message(self, device, topic, payload):
        """由 MQTT 收包线程调用；返回是否命中并唤醒了一个 pending。"""
        if not isinstance(payload, dict):
            return False
        request_id = str(payload.get("request_id") or "").strip()
        if not request_id:
            return False

        with self._lock:
            pending = self._pending.get(request_id)
            if pending is None:
                self._stats["late"] += 1
                return False
            if pending.device != device or pending.response_topic != topic:
                self._stats["mismatch"] += 1
                return False
            # 第一个匹配回包生效；立刻移除，后续重复/迟到回包会被丢弃
            self._pending.pop(request_id, None)
            self._stats["pending"] = len(self._pending)
            self._stats["completed"] += 1
            pending.payload = payload
            pending.decoded = self._safe_decode(payload)
            pending.event.set()
            return True

    def fail_device(self, device, error="device_offline", cause=None):
        """设备离线：立即失败该设备所有 pending，返回失败数量。"""
        with self._lock:
            request_ids = [rid for rid, pending in self._pending.items()
                           if pending.device == device]
            for request_id in request_ids:
                pending = self._pending.pop(request_id)
                pending.error = error
                pending.cause = cause or error
                pending.event.set()
                self._stats["failed"] += 1
            self._stats["pending"] = len(self._pending)
        return len(request_ids)

    def fail_all(self, error="mqtt_disconnected", cause=None):
        """MQTT 断开 / agent 退出：立即失败所有 pending。"""
        with self._lock:
            pending_list = list(self._pending.values())
            self._pending.clear()
            for pending in pending_list:
                pending.error = error
                pending.cause = cause or error
                pending.event.set()
                self._stats["failed"] += 1
            self._stats["pending"] = 0
        return len(pending_list)

    def cancel(self, request_id, error="cancelled", cause=None):
        """主动取消一个请求；返回是否找到。"""
        with self._lock:
            pending = self._pending.pop(request_id, None)
            self._stats["pending"] = len(self._pending)
            if pending is None:
                return False
            pending.error = error
            pending.cause = cause or error
            pending.event.set()
            self._stats["cancelled"] += 1
            return True

    def cancel_threads(self, thread_ids, error="cancelled", cause=None):
        """取消指定线程发出且仍在等待的请求（M4-09 会话中止用）。"""
        targets = set(thread_ids or ())
        if not targets:
            return 0
        with self._lock:
            request_ids = [request_id
                           for request_id, pending in self._pending.items()
                           if pending.thread_id in targets]
            for request_id in request_ids:
                pending = self._pending.pop(request_id)
                pending.error = error
                pending.cause = cause or error
                pending.event.set()
                self._stats["cancelled"] += 1
            self._stats["pending"] = len(self._pending)
        return len(request_ids)

    def stats(self):
        with self._lock:
            data = dict(self._stats)
            data["pending"] = len(self._pending)
            return data

    def _safe_decode(self, payload):
        try:
            return self._decode(payload)
        except Exception:  # noqa: BLE001
            LOG.exception("response decode failed")
            return {"ok": False, "result": None, "error": "invalid_response",
                    "cause": "应答解码失败", "holder": None}

    @staticmethod
    def _failure(started, device, resource, request_topic, response_topic,
                 error, cause, *, request_id=None, publish=None,
                 elapsed_ms=None):
        if elapsed_ms is None:
            elapsed_ms = int((time.monotonic() - started) * 1000)
        return {
            "ok": False,
            "received": False,
            "request_id": request_id or "",
            "device": device,
            "request_topic": request_topic,
            "response_topic": response_topic,
            "resource": resource,
            "payload": None,
            "decoded": None,
            "result": None,
            "cause": cause,
            "error": error,
            "published": bool((publish or {}).get("published")),
            "publish": publish or {},
            "elapsed_ms": elapsed_ms,
        }
