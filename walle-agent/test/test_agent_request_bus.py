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
