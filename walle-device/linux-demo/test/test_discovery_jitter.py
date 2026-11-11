#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""discovery 抖动测试：处理函数立即返回，延迟发布由 Timer 完成。"""
import json
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ld_monitor_identity as identity_mod  # noqa: E402
import main  # noqa: E402


class DiscoveryJitterTest(unittest.TestCase):
    def test_handler_returns_before_publish(self):
        mon = main.RpiMonitor()
        mon.build_online_payload = lambda online=True: {
            "device_id": "d", "uuid": "u", "online": online,
            "model": "m", "hostname": "h"}
        published = threading.Event()
        mon.publish_json = lambda *_a, **_k: published.set()

        with mock.patch.object(identity_mod, "DISCOVERY_JITTER_SEC", 0.2):
            t0 = time.time()
            mon.handle_device_query(json.dumps({"request_id": "r1"}))
            cost = time.time() - t0
            self.assertLess(cost, 0.05)          # 不再 sleep 在查询线程里
            self.assertFalse(published.is_set())  # 尚未到抖动时间
            self.assertTrue(published.wait(1))    # 定时器随后发布

    def test_result_payload_matches_online(self):
        mon = main.RpiMonitor()
        mon.build_online_payload = lambda online=True: {"device_id": "d", "online": online}
        captured = {}
        mon.publish_json = lambda topic, payload, **kwargs: captured.update(
            topic=topic, payload=payload)
        mon._publish_discovery_result("r1")
        self.assertEqual(captured["payload"], {"device_id": "d", "online": True})


if __name__ == "__main__":
    unittest.main()
