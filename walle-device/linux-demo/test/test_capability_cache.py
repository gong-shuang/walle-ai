#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""capability 缓存测试：查询只读缓存、pending 占位、探测失败保留旧摄像头列表。"""
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ld_config as config  # noqa: E402
import ld_monitor_capability as cap_mod  # noqa: E402
import main  # noqa: E402


def _payload(cameras, error=None):
    return {
        "device_id": "dev",
        "capabilities": ["system_status"] + (["camera_video_capture"] if cameras else []),
        "camera_count": len(cameras),
        "cameras": cameras,
        "summary": {"total_cameras": len(cameras), "total_formats": 0,
                    "total_resolutions": 0, "audio_sources": 0, "audio_sinks": 0,
                    "probe_cost_ms": 1},
        "probe": {"tool": "v4l2-ctl + pactl", "error": error, "audio_error": None},
    }


class CapabilityCacheTest(unittest.TestCase):
    def setUp(self):
        self.mon = main.RpiMonitor()

    def test_pending_when_cache_only(self):
        with mock.patch.object(cap_mod, "CAPABILITY_CACHE_ONLY", True):
            t0 = time.time()
            payload = self.mon.build_capability()
            cost = time.time() - t0
        self.assertLess(cost, 0.5)
        self.assertTrue(payload["probe"]["pending"])
        self.assertEqual(payload["cache"]["source"], "pending")
        self.assertEqual(payload["capabilities"], ["system_status"])

    def test_cache_hit(self):
        self.mon.capability_cache = {"probed_at": time.time() - 5,
                                     "payload": _payload([{"node": "/dev/video0"}])}
        self.mon.capability_ready.set()
        payload = self.mon.build_capability()
        self.assertEqual(payload["cache"]["source"], "cache")
        self.assertGreaterEqual(payload["cache"]["age_sec"], 5)
        self.assertEqual(payload["cache"]["ttl_sec"], config.CAPABILITY_CACHE_TTL)
        self.assertEqual(payload["camera_count"], 1)

    def test_probe_failure_keeps_cameras(self):
        cam = {"node": "/dev/video0", "name": "cam0", "format_count": 1,
               "resolution_count": 1, "max_resolution": "640x480", "formats": []}
        self.mon.capability_cache = {"probed_at": time.time(),
                                     "payload": _payload([cam])}
        failed = _payload([], error="v4l2-ctl --list-devices 执行超时")
        with mock.patch.object(self.mon, "_probe_capability", return_value=failed):
            self.mon._refresh_capability_cache()
        cached = self.mon.capability_cache["payload"]
        self.assertEqual(cached["cameras"], [cam])
        self.assertEqual(cached["camera_count"], 1)
        self.assertIn("camera_video_capture", cached["capabilities"])

    def _probe_with(self, rtmp_ready):
        capture = {"available": False, "device": None, "sources": [], "reason": "无麦克风"}
        playback = {"available": False, "device": None, "volume_percent": None,
                    "muted": None, "sinks": [], "reason": "无扬声器"}
        with mock.patch.object(self.mon, "cameras_cached", return_value=([], None)), \
                mock.patch.object(cap_mod, "probe_audio_capture", return_value=capture), \
                mock.patch.object(cap_mod, "probe_audio_playback", return_value=playback), \
                mock.patch.object(cap_mod, "VIDEO_ENABLE", True), \
                mock.patch.object(cap_mod, "dependency_status", return_value=[
                    {"name": "ffmpeg", "path": "/usr/bin/ffmpeg", "ok": True,
                     "impact": ""}]), \
                mock.patch.object(cap_mod, "rtmp_url_ready", return_value=rtmp_ready):
            return self.mon._probe_capability()

    def test_rtmp_push_hidden_when_url_not_ready(self):
        payload = self._probe_with((False, "WHIP_SECRET 未配置"))
        self.assertNotIn("rtmp_push", payload["capabilities"])
        self.assertEqual(payload["probe"]["video_error"], "WHIP_SECRET 未配置")

    def test_rtmp_push_visible_when_ready(self):
        payload = self._probe_with((True, None))
        self.assertIn("rtmp_push", payload["capabilities"])
        self.assertIsNone(payload["probe"]["video_error"])


if __name__ == "__main__":
    unittest.main()
