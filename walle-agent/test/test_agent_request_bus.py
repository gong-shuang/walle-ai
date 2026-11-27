#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M1-01 RequestBus 与 Agent 的集成测试：真实 _on_message 路由。"""

import json
import threading
import time
import types

import pytest

import car_agent


class _SilentLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass

    def debug(self, *args, **kwargs):
        pass

    def exception(self, *args, **kwargs):
        pass


class _PublishInfo:
    rc = 0

    def wait_for_publish(self, timeout=None):
        return True


class FakeMqttClient:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, qos=0, retain=False):
        body = json.loads(payload) if isinstance(payload, str) else payload
        self.published.append({"topic": topic, "payload": body})
        return _PublishInfo()


def _message(topic, payload):
    return types.SimpleNamespace(
        topic=topic, payload=json.dumps(payload).encode("utf-8"),
        qos=1, retain=False)


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def agent(monkeypatch):
    monkeypatch.setattr(car_agent, "log", lambda: _SilentLogger())
    monkeypatch.setattr(car_agent, "server_tenant", lambda: "test")
    monkeypatch.setattr(car_agent, "agent_identity", lambda: "agent-test")
    monkeypatch.setattr(car_agent, "agent_name", lambda: "Test Agent")

    cfg = dict(car_agent.DEFAULTS)
    cfg.update({"MQTT_USER": "", "MQTT_PASSWORD": "", "TENANT": "test"})
    instance = car_agent.Agent(cfg)
    instance.mqtt_client = FakeMqttClient()
    instance.mqtt_connected = True
    instance.mqtt_configured = True
    return instance


def test_on_message_wakes_request_bus(agent):
    results = []

    def worker():
        results.append(agent.request_bus.request(
            "dev-1", "baseState/query/request",
            {"request_id": "rid-integration"}, timeout=1.0))

    thread = threading.Thread(target=worker)
    thread.start()
    assert _wait_for(lambda: agent.request_bus.stats().get("pending") == 1)

    agent._on_message(None, None, _message(
        "walle/test/device/dev-1/baseState/query/response",
        {"request_id": "rid-integration", "result": 0, "cause": "ok"}))
    thread.join(timeout=2)

    assert len(results) == 1
    assert results[0]["received"] is True
    assert results[0]["ok"] is True
    assert results[0]["payload"]["request_id"] == "rid-integration"
    assert agent.request_bus.stats()["pending"] == 0


def test_on_message_online_false_fails_pending(agent):
    results = []

    def worker():
        results.append(agent.request_bus.request(
            "dev-1", "runState/query/request",
            {"request_id": "rid-offline"}, timeout=2.0))

    thread = threading.Thread(target=worker)
    thread.start()
    assert _wait_for(lambda: agent.request_bus.stats().get("pending") == 1)

    agent._on_message(None, None, _message(
        "walle/test/device/dev-1/online", {"online": False}))
    thread.join(timeout=2)

    assert len(results) == 1
    assert results[0]["received"] is False
    assert results[0]["error"] == "device_offline"


def test_link_test_uses_request_bus(agent):
    results = []

    def worker():
        results.append(agent.link_test("web-a", "dev-1"))

    thread = threading.Thread(target=worker)
    thread.start()
    assert _wait_for(lambda: any(
        item["topic"].endswith("/baseState/query/request")
        for item in agent.mqtt_client.published))

    request = [
        item for item in agent.mqtt_client.published
        if item["topic"].endswith("/baseState/query/request")
    ][-1]["payload"]
    agent._on_message(None, None, _message(
        "walle/test/device/dev-1/baseState/query/response",
        {"request_id": request["request_id"], "result": 0, "cause": "ok"}))
    thread.join(timeout=2)

    assert len(results) == 1
    out = results[0]
    assert out["ok"] is True
    assert out["request_id"] == request["request_id"]
    assert out["result"]["payload"]["result"] == 0
    assert out["decoded"]["ok"] is True


def test_on_message_updates_device_cache(agent):
    agent._on_message(None, None, _message(
        "walle/test/device/dev-1/online",
        {"online": True, "device_id": "dev-id-1", "hostname": "walle-pc"}))
    agent._on_message(None, None, _message(
        "walle/test/device/dev-1/runState/query/response", {
            "battery": {
                "is_supported": True, "voltage_v": 8.0, "available": True},
            "sonar": {
                "is_supported": True, "distance_cm": 80.0, "available": True},
            "control_holder": {"client_id": "web-a"},
        }))

    state = agent.device_state("dev-1")

    assert state["online"] is True
    assert state["device_id"] == "dev-id-1"
    assert state["battery"]["voltage_v"] == 8.0
    assert state["sonar"]["distance_cm"] == 80.0
    assert state["control_holder"]["client_id"] == "web-a"


def test_device_base_state_summary(agent):
    results = []

    def worker():
        results.append(agent.device_base_state("dev-1"))

    thread = threading.Thread(target=worker)
    thread.start()
    assert _wait_for(lambda: any(
        item["topic"].endswith("/baseState/query/request")
        for item in agent.mqtt_client.published))
    request = [
        item for item in agent.mqtt_client.published
        if item["topic"].endswith("/baseState/query/request")
    ][-1]["payload"]

    agent._on_message(None, None, _message(
        "walle/test/device/dev-1/baseState/query/response", {
            "request_id": request["request_id"],
            "device_id": "dev-id-1", "hostname": "walle-pc",
            "ip": "192.168.1.10", "uptime_seconds": 3661,
            "system": {"os": "Ubuntu 22.04", "kernel": "6.8.0"},
            "cpu": {"percent": 12.5, "count_logical": 4,
                    "temperature_c": 45.0},
            "memory": {"percent": 30.0, "used_bytes": 3 * 1024 ** 3,
                       "total_bytes": 8 * 1024 ** 3},
            "disk": {"percent": 20.0, "free_bytes": 80 * 1024 ** 3,
                     "total_bytes": 100 * 1024 ** 3},
            "network": {"total_rx_bytes_per_sec": 1024,
                        "total_tx_bytes_per_sec": 512},
            "usb": {"count": 2, "devices": [{}, {}]},
        }))
    thread.join(timeout=2)

    assert len(results) == 1
    out = results[0]
    assert out["ok"] is True
    assert out["baseState"]["device_id"] == "dev-id-1"
    summary = out["summary"]
    assert summary["cpu_percent"] == 12.5
    assert summary["cpu_count"] == 4
    assert summary["memory_percent"] == 30.0
    assert summary["disk_percent"] == 20.0
    assert summary["ip"] == "192.168.1.10"
    assert summary["uptime_seconds"] == 3661
    assert summary["usb_count"] == 2
    assert "CPU" in out["reply"]
    assert "内存" in out["reply"]
    assert "磁盘" in out["reply"]
    assert "192.168.1.10" in out["reply"]
    assert "运行" in out["reply"]


def test_device_base_state_partial_payload_is_ok(agent):
    results = []

    def worker():
        results.append(agent.device_base_state("dev-1"))

    thread = threading.Thread(target=worker)
    thread.start()
    assert _wait_for(lambda: any(
        item["topic"].endswith("/baseState/query/request")
        for item in agent.mqtt_client.published))
    request = [
        item for item in agent.mqtt_client.published
        if item["topic"].endswith("/baseState/query/request")
    ][-1]["payload"]
    agent._on_message(None, None, _message(
        "walle/test/device/dev-1/baseState/query/response",
        {"request_id": request["request_id"],
         "device_id": "dev-id-1", "hostname": "walle-pc"}))
    thread.join(timeout=2)

    assert results[0]["ok"] is True
    assert results[0]["summary"]["cpu_percent"] is None
    assert results[0]["summary"]["memory_percent"] is None
    assert results[0]["reply"]


def test_parse_intent_routes_device_status():
    intent, error = car_agent.parse_intent("设备状态")
    assert error is None
    assert intent == {"action": "device_base_state"}

    intent, _ = car_agent.parse_intent("CPU 多少")
    assert intent == {"action": "device_base_state"}

    intent, _ = car_agent.parse_intent("状态")
    assert intent == {"action": "status"}
