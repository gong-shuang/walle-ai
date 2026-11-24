#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""控制权租约：acquire/release、fail-open、TTL、离线 grace 与控制拦截。"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ld_control  # noqa: E402
import main  # noqa: E402


class LeaseTest(unittest.TestCase):
    def test_acquire_granted_then_occupied_then_renewed(self):
        lease = ld_control.ControlLease(ttl_sec=900, offline_grace_sec=10)

        result, action, holder = lease.acquire("web-a", "Web A", now=1000)
        self.assertEqual((result, action), (0, "granted"))
        self.assertEqual(holder["client_id"], "web-a")
        self.assertEqual(holder["remaining_sec"], 900)

        result, action, holder = lease.acquire("android-b", "Android B", now=1010)
        self.assertEqual((result, action), (1, "occupied"))
        self.assertEqual(holder["client_id"], "web-a")

        result, action, holder = lease.acquire("web-a", "Web A", now=1020)
        self.assertEqual((result, action), (0, "renewed"))
        self.assertEqual(holder["remaining_sec"], 900)

    def test_touch_fail_open_and_holder_match(self):
        lease = ld_control.ControlLease(ttl_sec=900, offline_grace_sec=10)
        # 没有 holder：fail-open，任何人都可以控制
        self.assertEqual(lease.touch("web-a", now=1000), (True, False))

        lease.acquire("web-a", "Web A", now=1000)
        self.assertEqual(lease.touch("web-a", now=1100), (True, True))
        self.assertEqual(lease.touch("android-b", now=1100), (False, False))
        # 持有者请求会续租，剩余时间回到满额
        self.assertEqual(lease.snapshot(now=1100)["remaining_sec"], 900)

    def test_release_only_by_holder(self):
        lease = ld_control.ControlLease(ttl_sec=900, offline_grace_sec=10)
        lease.acquire("web-a", "Web A", now=1000)

        self.assertEqual(lease.release("android-b", now=1010)[:2], (1, "not_holder"))
        self.assertEqual(lease.release("web-a", now=1020)[:2], (0, "released"))
        self.assertIsNone(lease.snapshot(now=1020))
        self.assertEqual(lease.release("web-a", now=1030)[:2], (0, "already_free"))

    def test_offline_grace_cancel_and_ttl_expiry(self):
        lease = ld_control.ControlLease(ttl_sec=900, offline_grace_sec=10)
        lease.acquire("web-a", "Web A", now=1000)
        lease.on_presence("web-a", False, now=1100)
        self.assertIsNone(lease.tick(now=1105))          # grace 内不释放
        lease.on_presence("web-a", True, now=1106)       # 恢复上线，取消释放
        self.assertIsNone(lease.tick(now=1109))

        lease.on_presence("web-a", False, now=1200)
        self.assertEqual(lease.tick(now=1211), "holder_offline")
        self.assertIsNone(lease.snapshot(now=1211))

        lease.acquire("web-a", "Web A", now=2000)
        self.assertEqual(lease.tick(now=2900), "expired")
        self.assertIsNone(lease.snapshot(now=2901))


class RequestGuardTest(unittest.TestCase):
    def _bare_monitor(self):
        """构造一个不走 __init__ 的实例，只填租约拦截需要的属性。"""
        mon = object.__new__(main.RpiMonitor)
        mon.lan_ip = "192.168.1.10"
        mon.control_lease = ld_control.ControlLease(ttl_sec=900, offline_grace_sec=10)
        mon._last_control_denied_log = 0.0
        mon.published = []
        mon.publish_json = lambda resource, data, retain=False: mon.published.append(
            (resource, data))
        mon.called = []
        mon.handle_video_request = lambda payload: mon.called.append(payload)
        mon.handle_query_status = lambda payload: mon.called.append(payload)
        mon._submit_request = lambda kind, func, payload: func(payload)
        return mon

    def test_query_allowed_when_other_client_holds_lease(self):
        mon = self._bare_monitor()
        mon.control_lease.acquire("web-a", "Web A")

        mon._handle_request(
            main.device_topic(main.REQ_STATE),
            json.dumps({"request_id": "q1", "client_id": "android-b"}))

        self.assertEqual(len(mon.called), 1)
        self.assertEqual(mon.published, [])

    def test_control_denied_when_other_client_holds_lease(self):
        mon = self._bare_monitor()
        mon.control_lease.acquire("web-a", "Web A")

        mon._handle_request(
            main.device_topic(main.REQ_VIDEO_START),
            json.dumps({"request_id": "v1", "client_id": "android-b"}))

        self.assertEqual(mon.called, [])
        resource, data = mon.published[-1]
        self.assertEqual(resource, main.RES_VIDEO_START_RESULT)
        self.assertEqual(data["result"], -4)
        self.assertEqual(data["error_code"], "control_denied")
        self.assertEqual(data["cause"], "设备控制权限已被别人占用")
        self.assertEqual(data["control_holder"]["client_id"], "web-a")

    def test_control_fail_open_and_holder_allowed(self):
        mon = self._bare_monitor()
        payload = json.dumps({"request_id": "v1", "client_id": "web-a"})

        mon._handle_request(main.device_topic(main.REQ_VIDEO_START), payload)
        self.assertEqual(len(mon.called), 1)              # 无 holder：fail-open
        self.assertEqual(mon.published, [])

        mon.called.clear()
        mon.control_lease.acquire("web-a", "Web A")
        mon._handle_request(main.device_topic(main.REQ_VIDEO_START), payload)
        self.assertEqual(len(mon.called), 1)              # 持有者：放行

    def test_acquire_and_release_handlers_publish_result(self):
        mon = self._bare_monitor()
        mon._handle_request(
            main.device_topic(main.REQ_CONTROL_ACQUIRE),
            json.dumps({"request_id": "a1", "client_id": "web-a",
                        "client_name": "Web A"}))

        resource, data = mon.published[-1]
        self.assertEqual(resource, main.RES_CONTROL_ACQUIRE)
        self.assertEqual((data["result"], data["action"], data["cause"]),
                         (0, "granted", "ok"))
        self.assertEqual(data["lease_ttl_sec"], 900)
        self.assertEqual(data["control_holder"]["client_id"], "web-a")

        mon._handle_request(
            main.device_topic(main.REQ_CONTROL_RELEASE),
            json.dumps({"request_id": "r1", "client_id": "web-a"}))

        resource, data = mon.published[-1]
        self.assertEqual(resource, main.RES_CONTROL_RELEASE)
        self.assertEqual((data["result"], data["action"]), (0, "released"))
        self.assertIsNone(data["control_holder"])


if __name__ == "__main__":
    unittest.main()
