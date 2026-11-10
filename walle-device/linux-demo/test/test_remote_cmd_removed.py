#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""远程命令能力移除回归测试：任何回流都应让用例失败。"""
import inspect
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402

LINUX_DEMO = Path(__file__).resolve().parent.parent


class RemoteCmdRemovedTest(unittest.TestCase):
    def test_no_remote_command_topics(self):
        topics = [topic for topic, _qos in main.subscriptions()]
        self.assertEqual(len(topics), 7)
        for topic in topics:
            self.assertNotIn("cmd/exec", topic)
            self.assertNotIn("cmd/result", topic)

    def test_no_remote_command_handler(self):
        self.assertNotIn("handle_cmd_exec", main.REQUEST_HANDLERS)
        self.assertNotIn("remote_cmd", inspect.getsource(main.RpiMonitor.build_capability))

    def test_sources_have_no_command_execution(self):
        for path in sorted(LINUX_DEMO.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for token in ("cmd/exec", "cmd/result", "remote_cmd", "handle_cmd_exec", "shell=True"):
                self.assertNotIn(token, text, f"{path.name} 仍包含 {token}")


if __name__ == "__main__":
    unittest.main()
