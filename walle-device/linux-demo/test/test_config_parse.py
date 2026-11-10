#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置解析与媒体参数测试：行内注释、引号转义、推流地址复用。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ld_config as config  # noqa: E402
import ld_media as media  # noqa: E402


class EnvFileParseTest(unittest.TestCase):
    def test_inline_comment_and_quotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.conf"
            path.write_text(
                "WALLE_TEST_A=plain\n"
                "WALLE_TEST_B=value # trailing comment\n"
                "WALLE_TEST_C=\"quoted # kept\"\n"
                "WALLE_TEST_D='single # kept'\n"
                "WALLE_TEST_E=with#hash\n"
                "WALLE_TEST_F=\"esc \\\" quote\"\n",
                encoding="utf-8")
            keys = ["WALLE_TEST_" + ch for ch in "ABCDEF"]
            with mock.patch.dict(os.environ, {}, clear=False):
                for key in keys:
                    os.environ.pop(key, None)
                self.assertTrue(config._load_env_file(str(path)))
                self.assertEqual(os.environ["WALLE_TEST_A"], "plain")
                self.assertEqual(os.environ["WALLE_TEST_B"], "value")
                self.assertEqual(os.environ["WALLE_TEST_C"], "quoted # kept")
                self.assertEqual(os.environ["WALLE_TEST_D"], "single # kept")
                self.assertEqual(os.environ["WALLE_TEST_E"], "with#hash")
                self.assertEqual(os.environ["WALLE_TEST_F"], 'esc " quote')


class PhoneAudioTimeoutConfigTest(unittest.TestCase):
    def test_env_override(self):
        import importlib
        try:
            with mock.patch.dict(os.environ, {
                    "PHONE_AUDIO_FIRST_FRAME_TIMEOUT": "15",
                    "PHONE_AUDIO_STARTUP_GRACE": "4"}):
                importlib.reload(config)
                self.assertEqual(config.PHONE_AUDIO_FIRST_FRAME_TIMEOUT, 15.0)
                self.assertEqual(config.PHONE_AUDIO_STARTUP_GRACE, 4.0)
        finally:
            importlib.reload(config)   # 还原为无覆盖时的默认值，避免影响其它用例


class FfmpegCmdTest(unittest.TestCase):
    def test_reuses_given_rtmp_url(self):
        cmd = media.build_ffmpeg_cmd("/dev/video0", "room1", rtmp_url="rtmp://h/live/x")
        self.assertEqual(cmd[-3:], ["-f", "flv", "rtmp://h/live/x"])

    def test_builds_url_when_absent(self):
        with mock.patch.object(media, "VIDEO_RTMP_URL_TEMPLATE", "rtmp://h/live/{room}"), \
                mock.patch.object(media, "WHIP_SECRET", ""):
            cmd = media.build_ffmpeg_cmd("/dev/video0", "room1")
        self.assertEqual(cmd[-1], "rtmp://h/live/room1")


class LogPathTest(unittest.TestCase):
    def test_relative_path_ignores_cwd(self):
        old = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                path = media.video_log_path()
            finally:
                os.chdir(old)
        base = os.path.dirname(os.path.abspath(media.__file__))
        self.assertTrue(path.startswith(base), path)

    def test_walle_log_dir_override(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"WALLE_LOG_DIR": tmp}):
            path = media.video_log_path()
            self.assertEqual(os.path.dirname(path), os.path.join(os.path.abspath(tmp), "log"))


if __name__ == "__main__":
    unittest.main()
