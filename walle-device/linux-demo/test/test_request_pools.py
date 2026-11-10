#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""请求分池测试：动作池占满时查询仍能执行，溢出请求被丢弃并计数。"""
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402


class RequestPoolTest(unittest.TestCase):
    def setUp(self):
        self.mon = main.RpiMonitor()
        self.mon.action_slots = threading.BoundedSemaphore(1)

    def tearDown(self):
        self.mon.query_pool.shutdown(wait=False, cancel_futures=True)
        self.mon.action_pool.shutdown(wait=False, cancel_futures=True)

    def test_action_overflow_does_not_block_query(self):
        release = threading.Event()
        self.assertTrue(self.mon._submit_request(
            "action", lambda _payload: release.wait(2), None))
        # 动作槽已占满：第二个动作被丢弃并计数
        self.assertFalse(self.mon._submit_request("action", lambda _payload: None, None))
        self.assertEqual(self.mon.dropped_counts["action"], 1)
        # 查询池独立，仍能立即执行
        ran = threading.Event()
        self.assertTrue(self.mon._submit_request("query", lambda _payload: ran.set(), None))
        self.assertTrue(ran.wait(1))
        self.assertEqual(self.mon.dropped_counts["query"], 0)
        release.set()


if __name__ == "__main__":
    unittest.main()
