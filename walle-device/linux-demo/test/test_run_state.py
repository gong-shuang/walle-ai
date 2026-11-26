#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runState/query/response：只返回运行状态，不再包含能力枚举字段。"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402

REMOVED_FIELDS = ("cache", "capability", "capabilities", "camera_count",
                  "cameras", "audio", "summary", "probe")


class _AliveProc:
    def poll(self):
        return None


class RunStateTest(unittest.TestCase):
    def setUp(self):
        self.mon = main.RpiMonitor()
        self.mon.publish_json = mock.Mock()

    def _query(self):
        self.mon.handle_capability_query(
            json.dumps({"request_id": "r1"}))
        return self.mon.publish_json.call_args

    def test_response_contains_only_runtime_groups(self):
        topic, data = self._query().args

        self.assertEqual(topic, main.RES_CAPABILITY_RESULT)
        self.assertEqual(data["request_id"], "r1")
        self.assertEqual(set(data), {
            "device_id", "hostname", "ip", "timestamp", "datetime",
            "request_id", "stream", "phone_audio", "recording", "motor", "servo",
            "battery", "sonar", "control_holder",
        })
        self.assertIsNone(data["control_holder"])
        for field in REMOVED_FIELDS:
            self.assertNotIn(field, data)

    def test_runtime_groups_reflect_device_state(self):
        self.mon._video_status = lambda: {
            "streaming": True, "camera_device": "/dev/video0", "room_number": "room-1",
            "audio": {"available": True, "device": "alsa_input.usb-mic"},
            "pid": 123, "started_at": 1700000000, "uptime_sec": 12,
        }
        self.mon.audio_proc = _AliveProc()
        self.mon.audio_meta = {"room_number": "audio-1"}

        _, data = self._query().args

        self.assertEqual(data["stream"], {
            "is_supported": True, "streaming": True, "room_number": "room-1",
            "camera_device": "/dev/video0", "audio_input": "alsa_input.usb-mic",
            "started_at": 1700000000, "uptime_sec": 12,
        })
        self.assertEqual(data["phone_audio"], {
            "is_supported": True, "playing": True, "room_number": "audio-1"})
        self.assertEqual(data["recording"], {
            "is_supported": False, "recording": False, "filename": None,
            "camera_device": None, "audio_input": None})
        self.assertEqual(data["motor"], {"is_supported": False, "moving": False})
        self.assertEqual(data["servo"], {"is_supported": False, "moving": False})
        self.assertEqual(data["battery"], {
            "is_supported": False, "voltage_v": None, "available": False})
        self.assertEqual(data["sonar"], {
            "is_supported": False, "distance_cm": None, "available": False})

    def test_invalid_payload_is_ignored(self):
        self.mon.handle_capability_query("not-json")
        self.mon.publish_json.assert_not_called()


if __name__ == "__main__":
    unittest.main()
