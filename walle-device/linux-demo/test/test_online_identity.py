#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""online/LWT 身份报文测试：不得在连接路径上扫描磁盘/网络挂载。"""
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ld_monitor_identity as identity_mod  # noqa: E402
import main  # noqa: E402


class OnlineIdentityTest(unittest.TestCase):
    def setUp(self):
        self.mon = main.RpiMonitor()

    def test_online_payload_skips_disk_scan(self):
        calls = []

        def slow_disk():
            calls.append(1)
            time.sleep(2)
            return []

        with mock.patch.object(identity_mod, "disk_mounts", slow_disk):
            t0 = time.time()
            payload = self.mon.build_online_payload(True)
            cost = time.time() - t0
        self.assertLess(cost, 0.3)
        self.assertEqual(calls, [], "online 报文不应触发 disk_mounts")
        self.assertNotIn("disk_total_bytes", payload)
        self.assertNotIn("mac", payload)
        self.assertIn("ip", payload)
        self.assertIn("os_description", payload)
        self.assertIsInstance(payload["os_description"], str)
        self.assertTrue(payload["online"])

    def test_refresh_identity_cache_is_reused(self):
        with mock.patch.object(identity_mod, "disk_mounts",
                               lambda: [{"total_bytes": 1000}]):
            self.mon.refresh_identity_cache()
        cached_info = self.mon._online_info()
        self.assertEqual(cached_info["disk_total_bytes"], 1000)
        self.assertEqual(cached_info["disk_total_human"], main.human_size(1000))
        payload = self.mon.build_online_payload(True)
        self.assertNotIn("disk_total_bytes", payload)


if __name__ == "__main__":
    unittest.main()
