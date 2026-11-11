#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""启动成功判定测试：ffmpeg 错误识别与播放器就绪等待。"""
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ld_media as media  # noqa: E402
from ld_monitor_phone import MonitorPhoneMixin  # noqa: E402


class FakeProc:
    def __init__(self, code=None):
        self.code = code

    def poll(self):
        return self.code


class StartupReadyTest(unittest.TestCase):
    def test_ffmpeg_auth_error_detected(self):
        tail = "rtmp://h: Server returned 401 Unauthorized"
        self.assertIn("401", media.ffmpeg_startup_error(tail))

    def test_ffmpeg_benign_output(self):
        self.assertIsNone(media.ffmpeg_startup_error("frame= 10 fps=30 q=28.0 size=1kB"))

    def test_wait_ready(self):
        evt = threading.Event()
        self.assertEqual(MonitorPhoneMixin._wait_process_ready(FakeProc(), evt, 0.2), "timeout")
        evt.set()
        self.assertEqual(MonitorPhoneMixin._wait_process_ready(FakeProc(), evt, 0.2), "ready")
        self.assertEqual(
            MonitorPhoneMixin._wait_process_ready(FakeProc(1), threading.Event(), 0.2), "exit")

    def test_wait_ready_returns_before_timeout(self):
        evt = threading.Event()
        threading.Timer(0.05, evt.set).start()
        t0 = time.time()
        state = MonitorPhoneMixin._wait_process_ready(FakeProc(), evt, 2.0)
        self.assertEqual(state, "ready")
        self.assertLess(time.time() - t0, 1.0)


if __name__ == "__main__":
    unittest.main()
