#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地请求注入脚本测试：报文构造、默认动作与 dry-run。"""
import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import inject_request as inject  # noqa: E402


class InjectRequestTest(unittest.TestCase):
    def test_build_query_payload(self):
        with mock.patch.object(inject.config, "MQTT_TENANT", "home"), \
                mock.patch.object(inject.config, "DEVICE_ID", "dev1"):
            items = inject.build_requests(["state/query", "capability/query"],
                                          "uuid-1", request_id="rid-1")
        self.assertEqual([item[0] for item in items],
                         ["walle/home/dev1/v1/state/query",
                          "walle/home/dev1/v1/capability/query"])
        self.assertEqual([item[2] for item in items],
                         ["walle/home/dev1/v1/state/result",
                          "walle/home/dev1/v1/capability/result"])
        self.assertEqual(items[0][1], {"uuid": "uuid-1", "request_id": "rid-1"})

    def test_build_video_start_payload(self):
        topic, payload, result = inject.build_requests(
            [inject.REQ_VIDEO_START], "uuid-1", room_number="demo",
            video_device="/dev/video0", request_id="rid-2")[0]
        self.assertIn("/video/start/request", topic)
        self.assertIn("/video/start/result", result)
        self.assertEqual(payload["room_number"], "demo")
        self.assertEqual(payload["video_device"], "/dev/video0")
        self.assertEqual(payload["request_id"], "rid-2")

    def test_dry_run_prints_without_broker(self):
        out = io.StringIO()
        with mock.patch.object(inject, "MQTT_TENANT", "home"), \
                mock.patch.object(inject.config, "MQTT_TENANT", "home"), \
                contextlib.redirect_stdout(out):
            rc = inject.main(["--dry-run", "--device-id", "dev1",
                              "--action", "video/start", "--room-number", "demo"])
        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("walle/home/dev1/v1/video/start/request", text)
        self.assertIn("<device_uuid>", text)

    def test_missing_room_number_rejected(self):
        err = io.StringIO()
        with mock.patch.object(inject, "MQTT_TENANT", "home"), \
                contextlib.redirect_stderr(err):
            rc = inject.main(["--dry-run", "--device-id", "dev1",
                              "--action", "video/start"])
        self.assertEqual(rc, 2)
        self.assertIn("需要 --room-number", err.getvalue())


if __name__ == "__main__":
    unittest.main()
