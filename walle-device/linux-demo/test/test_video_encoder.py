#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""编码器选择测试：auto/指定/回退与 ffmpeg 参数。"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ld_media as media  # noqa: E402


class EncoderTest(unittest.TestCase):
    def _pick(self, configured, available):
        with mock.patch.object(media, "VIDEO_ENCODER", configured), \
                mock.patch.object(media, "available_video_encoders", return_value=available):
            return media.pick_video_encoder()

    def test_auto_prefers_hardware(self):
        self.assertEqual(self._pick("auto", {"libx264", "h264_v4l2m2m"}),
                         ("h264_v4l2m2m", None))

    def test_auto_falls_back_to_software(self):
        self.assertEqual(self._pick("auto", {"libx264"}), ("libx264", None))

    def test_explicit_unavailable_falls_back(self):
        encoder, note = self._pick("h264_v4l2m2m", {"libx264"})
        self.assertEqual(encoder, "libx264")
        self.assertIn("回退 libx264", note)

    def test_unknown_value_falls_back(self):
        encoder, note = self._pick("nvenc", {"libx264"})
        self.assertEqual(encoder, "libx264")
        self.assertIn("未知 VIDEO_ENCODER", note)

    def test_probe_failure_falls_back(self):
        encoder, note = self._pick("auto", None)
        self.assertEqual(encoder, "libx264")
        self.assertIn("无法枚举", note)

    def test_ffmpeg_cmd_parameters(self):
        sw = media.build_ffmpeg_cmd("/dev/video0", "r", encoder="libx264")
        self.assertIn("-preset", sw)
        self.assertIn("-tune", sw)
        hw = media.build_ffmpeg_cmd("/dev/video0", "r", encoder="h264_v4l2m2m")
        self.assertNotIn("-preset", hw)
        idx = hw.index("-c:v")
        self.assertEqual(hw[idx + 1], "h264_v4l2m2m")


if __name__ == "__main__":
    unittest.main()
