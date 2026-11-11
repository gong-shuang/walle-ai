#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""部署前自检测试：协议配对、Python 依赖与连接检查的成功/失败路径。"""
import contextlib
import io
import socket
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import paho.mqtt.client as mqtt  # noqa: E402

import ld_selfcheck as selfcheck  # noqa: E402


class FakeMqttClient:
    """最小 paho 客户端替身：按调用顺序触发 CONNACK/SUBACK/PUBACK/回环消息。"""

    def __init__(self, deliver=True, granted=1):
        self.deliver = deliver
        self.granted = granted
        self.on_connect = self.on_subscribe = None
        self.on_publish = self.on_message = None

    def username_pw_set(self, *args, **kwargs):
        pass

    def tls_set_context(self, *args, **kwargs):
        pass

    def connect_async(self, *args, **kwargs):
        pass

    def loop_start(self):
        threading.Timer(
            0.01, lambda: self.on_connect(self, None, {}, 0, None)).start()

    def loop_stop(self):
        pass

    def disconnect(self):
        pass

    def subscribe(self, topic, qos=0):
        threading.Timer(
            0.01,
            lambda: self.on_subscribe(self, None, 1, [self.granted], None)).start()
        return mqtt.MQTT_ERR_SUCCESS, 1

    def publish(self, topic, payload, qos=0):
        body = payload if isinstance(payload, bytes) else str(payload).encode()

        def _ack():
            self.on_publish(self, None, 2, 0, None)
            if self.deliver:
                msg = SimpleNamespace(topic=topic, payload=body)
                threading.Timer(
                    0.01, lambda: self.on_message(self, None, msg)).start()

        threading.Timer(0.01, _ack).start()
        return SimpleNamespace(rc=mqtt.MQTT_ERR_SUCCESS)


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

    def _run_connect(self, fake):
        """固定配置与网络，返回 (结果, 输出)。"""
        out = io.StringIO()
        with mock.patch.object(selfcheck.config, "MQTT_HOST", "broker.test"), \
                mock.patch.object(selfcheck.config, "MQTT_PORT", 8883), \
                mock.patch.object(selfcheck.config, "MQTT_TLS", "off"), \
                mock.patch.object(selfcheck.config, "MQTT_USER", "dev1"), \
                mock.patch.object(selfcheck.config, "MQTT_PASSWORD", "pw"), \
                mock.patch.object(selfcheck.config, "MQTT_TENANT", "home"), \
                mock.patch.object(selfcheck.config, "DEVICE_ID", "dev1"), \
                mock.patch.object(selfcheck.mqtt, "Client", return_value=fake), \
                mock.patch.object(selfcheck.socket, "getaddrinfo",
                                  return_value=[(2, 1, 6, "", ("1.2.3.4", 8883))]), \
                mock.patch.object(selfcheck.socket, "create_connection"), \
                contextlib.redirect_stdout(out):
            ok = selfcheck.check_connect(timeout=0.3)
        return ok, out.getvalue()

    def test_connect_roundtrip_ok(self):
        ok, output = self._run_connect(FakeMqttClient())
        self.assertTrue(ok, output)
        self.assertIn("walle/home/dev1-check/v1/selfcheck", output)
        self.assertIn("回环消息：成功", output)

    def test_connect_without_delivery_fails(self):
        ok, output = self._run_connect(FakeMqttClient(deliver=False))
        self.assertFalse(ok)
        self.assertIn("回环消息：未收到", output)

    def test_connect_suback_rejected(self):
        ok, output = self._run_connect(
            FakeMqttClient(granted=SimpleNamespace(is_failure=True)))
        self.assertFalse(ok)
        self.assertIn("订阅回执：被拒绝", output)


if __name__ == "__main__":
    unittest.main()
