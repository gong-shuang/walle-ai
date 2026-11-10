#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""部署前自检测试：协议配对、Python 依赖与连接检查的失败路径。"""
import socket
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ld_selfcheck as selfcheck  # noqa: E402


class SelfCheckTest(unittest.TestCase):
    def test_topic_pairs_ok(self):
        self.assertEqual(selfcheck.topic_pair_errors(), [])

    def test_python_dependencies(self):
        status = {item["module"]: item["ok"]
                  for item in selfcheck.python_dependency_status()}
        self.assertTrue(status["paho.mqtt"])
        self.assertTrue(status["psutil"])

    def test_dns_failure_returns_false(self):
        with mock.patch.object(selfcheck.socket, "getaddrinfo",
                               side_effect=socket.gaierror("no such host")):
            self.assertFalse(selfcheck.check_connect(timeout=0.1))


if __name__ == "__main__":
    unittest.main()
