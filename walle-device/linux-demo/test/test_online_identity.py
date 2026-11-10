#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""online/LWT 身份报文测试：不得在连接路径上扫描磁盘/网络挂载。"""
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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

        with mock.patch.object(main, "disk_mounts", slow_disk):
            t0 = time.time()
            payload = self.mon.build_online_payload(True)
            cost = time.time() - t0
        self.assertLess(cost, 0.3)
        self.assertEqual(calls, [], "online 报文不应触发 disk_mounts")
        self.assertIsNone(payload["disk_total_bytes"])
        self.assertTrue(payload["online"])

    def test_refresh_identity_cache_is_reused(self):
        with mock.patch.object(main, "disk_mounts",
                               lambda: [{"total_bytes": 1000}]):
            self.mon.refresh_identity_cache()
        payload = self.mon.build_online_payload(True)
        self.assertEqual(payload["disk_total_bytes"], 1000)
        self.assertEqual(payload["disk_total_human"], main.human_size(1000))


if __name__ == "__main__":
    unittest.main()
