#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""协议与配置单元测试：主题、请求解析、房间号、脱敏、设备标识、配置校验。"""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ld_config as config  # noqa: E402
import main  # noqa: E402

CONFIG_BASE = dict(
    WALLE_RUNTIME_ENV="prod",
    MQTT_HOST="broker.test",
    MQTT_PORT=8883,
    MQTT_USER="user",
    MQTT_PASSWORD="pass",
    MQTT_TENANT="tenant",
    MQTT_TLS="on",
    MQTT_CA_FILE="",
    CAMERA_DEVICE="",
    VIDEO_ENABLE=False,
    PHONE_AUDIO_ENABLE=False,
)


class TopicTest(unittest.TestCase):
    def test_sanitize_segment(self):
        self.assertEqual(main.sanitize_segment("user.name@example"), "user_name_example")
        self.assertEqual(main.sanitize_segment("a:b c"), "a_b_c")
        self.assertEqual(main.sanitize_segment(""), "unknown")
        self.assertEqual(main.sanitize_segment(None), "unknown")
        long_seg = main.sanitize_segment("a" * 65)
        self.assertEqual(long_seg[40], "-")
        self.assertEqual(len(long_seg), 49)

    def test_device_topic_and_subscriptions(self):
        with mock.patch.multiple(config, MQTT_TENANT="t.1", DEVICE_ID="dev-1"):
            self.assertEqual(main.device_topic("baseState/query/request"),
                             "walle/t_1/device/dev-1/baseState/query/request")
            self.assertEqual(main.tenant_topic("discovery/query/request"),
                             "walle/t_1/discovery/query/request")
            topics = [topic for topic, _qos in main.subscriptions()]
        self.assertEqual(len(topics), 5)
        self.assertIn("walle/t_1/discovery/query/request", topics)
        self.assertIn("walle/t_1/device/dev-1/+/+/request", topics)
        self.assertIn("walle/t_1/device/dev-1/+/+/+/request", topics)
        self.assertIn("walle/t_1/device/dev-1/+/+/+/+/request", topics)
        self.assertIn("walle/t_1/device/dev-1/+/+/+/cmd", topics)

    def test_request_result_pairs(self):
        for req, res in main.REQUEST_TO_RESULT.items():
            self.assertTrue(req.endswith("/request"), f"{req} 的请求命名不合约定")
            self.assertTrue(res.endswith("/response"), f"{req} 的应答 {res} 命名不合约定")
        for req in main.REQUEST_HANDLERS:
            self.assertIn(req, main.REQUEST_TO_RESULT)
        # discovery 是租户级广播，单独走 handle_device_query，不在 REQUEST_HANDLERS
        self.assertNotIn(main.REQ_DISCOVERY, main.REQUEST_HANDLERS)


class RequestValidationTest(unittest.TestCase):
    def test_parse_request(self):
        data = main.RpiMonitor.parse_request(
            json.dumps({"request_id": "r1"}), "topic")
        self.assertEqual(data["request_id"], "r1")
        self.assertIsNone(main.RpiMonitor.parse_request("", "topic"))
        self.assertIsNone(main.RpiMonitor.parse_request("not-json", "topic"))
        self.assertIsNone(main.RpiMonitor.parse_request(json.dumps([]), "topic"))

    def test_room_pattern(self):
        self.assertIsNotNone(re.match(main.VIDEO_ROOM_PATTERN, "room-1_A"))
        for bad in ("", "../etc/passwd", "room/1", "a" * 65, "房间"):
            self.assertIsNone(re.match(main.VIDEO_ROOM_PATTERN, bad), bad)


class MaskTest(unittest.TestCase):
    def test_mask_rtmp_url(self):
        self.assertEqual(
            main.mask_rtmp_url("rtmp://h/live/r?secret=abc&x=1"),
            "rtmp://h/live/r?secret=***&x=1")

    def test_mask_payload(self):
        raw = json.dumps({
            "room_number": "room-1",
            "password": "pw",
            "nested": {"token": "tk"},
            "url": "https://h/whep?stream=a&token=tk",
        })
        masked = json.loads(main.mask_payload(raw))
        self.assertEqual(masked["room_number"], "***")
        self.assertEqual(masked["password"], "***")
        self.assertEqual(masked["nested"]["token"], "***")
        self.assertIn("token=***", masked["url"])
        self.assertLessEqual(len(main.mask_payload(json.dumps({"x": "y" * 900}))), 500)


class DeviceIdTest(unittest.TestCase):
    def test_persist_and_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".device-id"
            with mock.patch.object(config, "DEVICE_ID_FILE", str(path)):
                first, _ = main.resolve_device_id("aa:bb:cc", 4, 8 * 1024 ** 3)
                self.assertTrue(path.exists())
                second, source = main.resolve_device_id("aa:bb:cc", 4, 8 * 1024 ** 3)
                self.assertEqual(first, second)
                self.assertIn("复用", source)
                third, _ = main.resolve_device_id("dd:ee:ff", 4, 8 * 1024 ** 3)
                self.assertNotEqual(first, third)
                state = json.loads(path.read_text(encoding="utf-8"))
                self.assertIn(first, state["previous"])


class ValidateConfigTest(unittest.TestCase):
    def test_valid(self):
        with mock.patch.multiple(config, **CONFIG_BASE):
            self.assertEqual(main.validate_config(), [])

    def test_prod_plaintext_port_rejected(self):
        with mock.patch.multiple(config, **{**CONFIG_BASE, "MQTT_PORT": 1883}):
            self.assertTrue(any("1883" in err for err in main.validate_config()))

    def test_tls_off_only_in_debug(self):
        with mock.patch.multiple(config, **{**CONFIG_BASE, "MQTT_TLS": "off"}):
            self.assertTrue(any("MQTT_TLS=off" in err for err in main.validate_config()))
        with mock.patch.multiple(
                config, **{**CONFIG_BASE, "MQTT_TLS": "off", "WALLE_RUNTIME_ENV": "debug"}):
            self.assertEqual(main.validate_config(), [])

    def test_missing_required(self):
        with mock.patch.multiple(config, **{
                **CONFIG_BASE, "MQTT_HOST": "", "MQTT_USER": "",
                "MQTT_PASSWORD": "", "MQTT_TENANT": ""}):
            self.assertEqual(len(main.validate_config()), 4)


if __name__ == "__main__":
    unittest.main()
