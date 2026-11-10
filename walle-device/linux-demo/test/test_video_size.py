#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""推流分辨率选择测试：优先配置值，不支持时自动降级。"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ld_camera as camera  # noqa: E402
import ld_media as media  # noqa: E402


def _proc(stdout):
    return type("P", (), {"returncode": 0, "stdout": stdout, "stderr": ""})()


class VideoSizeTest(unittest.TestCase):
    def _sizes(self, stdout):
        with mock.patch.object(camera, "_v4l2", return_value=_proc(stdout)):
            return camera.camera_sizes("/dev/video0")

    def test_parse_sizes(self):
        sizes, err = self._sizes(
            "Size: Discrete 640x480\nSize: Discrete 1280x720\nSize: Discrete 640x480\n")
        self.assertIsNone(err)
        self.assertEqual(sizes, ["640x480", "1280x720"])

    def test_pick_preferred(self):
        with mock.patch.object(camera, "camera_sizes",
                               return_value=(["640x480", "1280x720"], None)):
            size, note = camera.pick_video_size("/dev/video0", "640x480")
        self.assertEqual(size, "640x480")
        self.assertIsNone(note)

    def test_pick_falls_back_to_smaller(self):
        with mock.patch.object(camera, "camera_sizes",
                               return_value=(["320x240", "640x480", "1280x720"], None)):
            size, note = camera.pick_video_size("/dev/video0", "1920x1080")
        self.assertEqual(size, "1280x720")
        self.assertIn("自动改用 1280x720", note)

    def test_pick_falls_back_up(self):
        with mock.patch.object(camera, "camera_sizes",
                               return_value=(["1280x720", "1920x1080"], None)):
            size, note = camera.pick_video_size("/dev/video0", "640x480")
        self.assertEqual(size, "1280x720")
        self.assertIn("自动改用", note)

    def test_pick_error(self):
        with mock.patch.object(camera, "camera_sizes",
                               return_value=([], "v4l2-ctl 探测超时")):
            size, note = camera.pick_video_size("/dev/video0")
        self.assertIsNone(size)
        self.assertIn("超时", note)

    def test_ffmpeg_cmd_uses_size(self):
        cmd = media.build_ffmpeg_cmd("/dev/video0", "r", video_size="1280x720")
        idx = cmd.index("-video_size")
        self.assertEqual(cmd[idx + 1], "1280x720")


if __name__ == "__main__":
    unittest.main()
