#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""控制权租约：单实例 holder、15 分钟 TTL、客户端离线 grace 释放。"""

import logging
import threading
import time

from . import config
from .car_motor import RESULT_CONTROL_DENIED
from .identity import now_str, now_ts


class ControlLease:
    """纯内存租约；设备重启即清空。线程安全。"""

    def __init__(self, ttl_sec=900, offline_grace_sec=10):
        self.ttl_sec = max(1, int(ttl_sec))
        self.offline_grace_sec = max(0, int(offline_grace_sec))
        self._lock = threading.Lock()
        self._holder = None
        self._offline_release_at = None

    @staticmethod
    def _holder_view(holder, now):
        return {
            "client_id": holder["client_id"],
            "client_name": holder["client_name"],
            "acquired_at": holder["acquired_at"],
            "expires_at": holder["expires_at"],
            "remaining_sec": max(0, int(holder["expires_at"] - now)),
        }

    def _drop_if_stale(self, now):
        """过期或离线 grace 到期时清空；返回释放原因或 None。"""
        holder = self._holder
        if holder is None:
            return None
        if (self._offline_release_at is not None
                and now >= self._offline_release_at):
            self._holder = None
            self._offline_release_at = None
            return "holder_offline"
        if now >= holder["expires_at"]:
            self._holder = None
            self._offline_release_at = None
            return "expired"
        return None

    def acquire(self, client_id, client_name, now=None):
        """返回 (result, action, holder_view)。result: 0 成功 / 1 被占用 / 2 参数错误。"""
        now = time.time() if now is None else now
        with self._lock:
            self._drop_if_stale(now)
            if self._holder is None:
                self._holder = {
                    "client_id": client_id,
                    "client_name": client_name,
                    "acquired_at": int(now),
                    "expires_at": now + self.ttl_sec,
                }
                self._offline_release_at = None
                return 0, "granted", self._holder_view(self._holder, now)
            if self._holder["client_id"] == client_id:
                self._holder["client_name"] = client_name
                self._holder["expires_at"] = now + self.ttl_sec
                self._offline_release_at = None
                return 0, "renewed", self._holder_view(self._holder, now)
            return 1, "occupied", self._holder_view(self._holder, now)

    def release(self, client_id, now=None):
        """返回 (result, action, holder_view)。result: 0 成功/已空闲，1 非持有者。"""
        now = time.time() if now is None else now
        with self._lock:
            self._drop_if_stale(now)
            if self._holder is None:
                return 0, "already_free", None
            if self._holder["client_id"] == client_id:
                self._holder = None
                self._offline_release_at = None
                return 0, "released", None
            return 1, "not_holder", self._holder_view(self._holder, now)

    def touch(self, client_id, now=None):
        """控制/查询前调用。

        返回 (allowed, is_holder)：
          - 无 holder：fail-open，允许执行；
          - holder 匹配：续租并允许；
          - 其他：拒绝。
        """
        now = time.time() if now is None else now
        with self._lock:
            self._drop_if_stale(now)
            if self._holder is None:
                return True, False
            if self._holder["client_id"] == client_id:
                self._holder["expires_at"] = now + self.ttl_sec
                self._offline_release_at = None
                return True, True
            return False, False

    def on_presence(self, client_id, online, now=None):
        """客户端在线状态变化：holder 离线时安排 grace 释放，上线时取消。"""
        now = time.time() if now is None else now
        with self._lock:
            self._drop_if_stale(now)
            if self._holder is None or self._holder["client_id"] != client_id:
                return
            if online:
                self._offline_release_at = None
            else:
                self._offline_release_at = now + self.offline_grace_sec

    def tick(self, now=None):
        """monitor loop 调用；真正释放时返回原因。"""
        now = time.time() if now is None else now
        with self._lock:
            return self._drop_if_stale(now)

    def snapshot(self, now=None):
        now = time.time() if now is None else now
        with self._lock:
            self._drop_if_stale(now)
            if self._holder is None:
                return None
            return self._holder_view(self._holder, now)


class ControlMixin:
    """acquire/release handler、控制拒绝报文与 runState holder 快照。"""

    def _control_common(self, request_id):
        return {
            "device_id": config.DEVICE_ID,
            "hostname": config.HOSTNAME,
            "ip": self.lan_ip,
            "request_id": str(request_id or ""),
            "timestamp": now_ts(),
            "datetime": now_str(),
        }

    def _publish_control_result(self, resource, request_id, result, cause,
                                action, holder):
        payload = self._control_common(request_id)
        payload.update({
            "result": int(result),
            "cause": cause,
            "action": action,
            "lease_ttl_sec": self.control_lease.ttl_sec,
            "control_holder": holder,
        })
        self.publish_json(resource, payload)
        logging.info("控制权结果 -> %s request_id=%s result=%s action=%s holder=%s",
                     config.device_topic(resource), request_id or "-",
                     result, action, (holder or {}).get("client_id") or "-")

    def handle_control_acquire(self, payload):
        data = self.parse_request(payload, config.REQ_CONTROL_ACQUIRE)
        if data is None:
            return
        rid = self._rid(data)
        client_id = str(data.get("client_id") or "").strip()[:64]
        client_name = str(data.get("client_name") or "").strip()[:64]
        if not client_id or not client_name:
            self._publish_control_result(
                config.RES_CONTROL_ACQUIRE, rid, 2,
                "client_id/client_name 缺失或非法", "invalid",
                self.control_holder_snapshot())
            return
        result, action, holder = self.control_lease.acquire(client_id, client_name)
        cause = "ok" if result == 0 else "设备控制权限已被别人占用"
        self._publish_control_result(
            config.RES_CONTROL_ACQUIRE, rid, result, cause, action, holder)

    def handle_control_release(self, payload):
        data = self.parse_request(payload, config.REQ_CONTROL_RELEASE)
        if data is None:
            return
        rid = self._rid(data)
        client_id = str(data.get("client_id") or "").strip()[:64]
        if not client_id:
            self._publish_control_result(
                config.RES_CONTROL_RELEASE, rid, 2,
                "client_id 缺失", "invalid", self.control_holder_snapshot())
            return
        result, action, holder = self.control_lease.release(client_id)
        cause = "ok" if result == 0 else "不是当前控制者"
        self._publish_control_result(
            config.RES_CONTROL_RELEASE, rid, result, cause, action, holder)

    def control_holder_snapshot(self):
        lease = getattr(self, "control_lease", None)
        return lease.snapshot() if lease is not None else None

    def publish_control_denied(self, resource, data):
        """控制请求被拒绝：请求-应答类发通用拒绝报文，cmd 只记限频日志。"""
        rid = self._rid(data)
        holder = self.control_holder_snapshot()
        response = config.REQUEST_TO_RESULT.get(resource)
        if not response:
            now = time.monotonic()
            last = getattr(self, "_last_control_denied_log", 0.0)
            if now - last >= 5.0:
                self._last_control_denied_log = now
                logging.warning(
                    "控制命令被拒绝（无控制权限）: %s request_id=%s holder=%s",
                    resource, rid or "-", (holder or {}).get("client_id") or "-")
            return
        payload = self._control_common(rid)
        payload.update({
            # 车控按 §4.10 用 -5（-4 已表示前方障碍），其它请求-应答仍用 -4
            "result": (RESULT_CONTROL_DENIED
                       if resource == config.REQ_CAR_MOTOR_SET else -4),
            "cause": "设备控制权限已被别人占用",
            "error_code": "control_denied",
            "control_holder": holder,
        })
        self.publish_json(response, payload)
        logging.warning("控制请求被拒绝 -> %s request_id=%s holder=%s",
                        config.device_topic(response), rid or "-",
                        (holder or {}).get("client_id") or "-")
