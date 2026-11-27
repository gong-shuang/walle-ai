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
    def __init__(self, agent=None):
        self.agent = agent
        self.published = []

    def _respond(self, topic, body, response):
        response = dict(response)
        response["request_id"] = body.get("request_id")
        response_topic = topic.rsplit("/", 1)[0] + "/response"
        device = topic.split("/")[3]
        self.agent.request_bus.on_message(device, response_topic, response)

    def publish(self, topic, payload, qos=0, retain=False):
        body = json.loads(payload) if isinstance(payload, str) else payload
        self.published.append({"topic": topic, "payload": body})
        if self.agent is not None:
            if topic.endswith("/control/acquire/request"):
                self._respond(topic, body, {
                    "result": 0, "action": "granted",
                    "control_holder": {"client_id": "agent-test",
                                       "client_name": "Test Agent"},
                })
            elif topic.endswith("/control/release/request"):
                self._respond(topic, body, {
                    "result": 0, "action": "released",
                    "control_holder": None,
                })
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
    instance.mqtt_client = FakeMqttClient(instance)
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


def _call_with_response(agent, call, request_suffix, response_topic,
                        response_payload):
    results = []

    def worker():
        results.append(call())

    thread = threading.Thread(target=worker)
    thread.start()
    assert _wait_for(lambda: any(
        item["topic"].endswith(request_suffix)
        for item in agent.mqtt_client.published))
    request = [
        item for item in agent.mqtt_client.published
        if item["topic"].endswith(request_suffix)
    ][-1]["payload"]
    payload = dict(response_payload)
    payload["request_id"] = request["request_id"]
    agent._on_message(None, None, _message(response_topic, payload))
    thread.join(timeout=2)
    assert results
    return results[0]


def _run_state_payload():
    return {
        "device_id": "dev-id-1", "hostname": "walle-pc",
        "ip": "192.168.1.10",
        "stream": {
            "is_supported": True, "streaming": True, "room_number": "room-1",
        },
        "phone_audio": {
            "is_supported": True, "playing": False, "room_number": None,
        },
        "recording": {
            "is_supported": True, "recording": True, "filename": "a.mp4",
        },
        "motor": {"is_supported": True, "moving": False},
        "servo": {"is_supported": True, "moving": False},
        "battery": {
            "is_supported": True, "voltage_v": 8.0, "available": True,
        },
        "sonar": {
            "is_supported": True, "distance_cm": 120.0, "available": True,
        },
        "control_holder": {"client_id": "web-a", "client_name": "Web A"},
    }


def test_device_run_state_summary(agent):
    out = _call_with_response(
        agent, lambda: agent.device_run_state("dev-1"),
        "/runState/query/request",
        "walle/test/device/dev-1/runState/query/response",
        _run_state_payload())

    assert out["ok"] is True
    summary = out["summary"]
    assert summary["recording"]["recording"] is True
    assert summary["stream"]["room_number"] == "room-1"
    assert summary["battery"]["voltage_v"] == 8.0
    assert summary["sonar"]["distance_cm"] == 120.0
    assert summary["control_holder"]["client_id"] == "web-a"
    assert summary["unsupported"] == []
    assert "正在录像" in out["reply"]
    assert "正在推流" in out["reply"]
    assert "电量 8.00V" in out["reply"]
    assert "前方 120cm" in out["reply"]
    assert "控制权：Web A" in out["reply"]
    assert agent.device_supports("dev-1", "battery") is True


def test_device_run_state_is_supported_false_is_hidden(agent):
    payload = _run_state_payload()
    payload["battery"] = {
        "is_supported": False, "voltage_v": 8.0, "available": True,
    }
    payload["stream"] = {
        "is_supported": False, "streaming": True, "room_number": "room-1",
    }
    out = _call_with_response(
        agent, lambda: agent.device_run_state("dev-1"),
        "/runState/query/request",
        "walle/test/device/dev-1/runState/query/response", payload)

    assert out["summary"]["battery"] is None
    assert out["summary"]["stream"] is None
    assert set(out["summary"]["unsupported"]) == {"battery", "stream"}
    assert "电量" not in out["reply"]
    assert "推流" not in out["reply"]
    assert agent.device_supports("dev-1", "battery") is False


def test_device_run_state_available_false_shows_invalid(agent):
    payload = _run_state_payload()
    payload["battery"]["available"] = False
    payload["sonar"]["available"] = False
    out = _call_with_response(
        agent, lambda: agent.device_run_state("dev-1"),
        "/runState/query/request",
        "walle/test/device/dev-1/runState/query/response", payload)

    assert out["summary"]["battery"]["voltage_v"] is None
    assert out["summary"]["sonar"]["distance_cm"] is None
    assert "电量 无效" in out["reply"]
    assert "前方距离 无效" in out["reply"]


def test_parse_intent_routes_run_state():
    for text in ("在录像吗", "在推流吗", "电量多少", "前方多远",
                 "谁持有控制权", "运行状态"):
        intent, error = car_agent.parse_intent(text)
        assert error is None
        assert intent == {"action": "device_run_state"}, text

    intent, _ = car_agent.parse_intent("状态")
    assert intent == {"action": "status"}
    intent, _ = car_agent.parse_intent("设备状态")
    assert intent == {"action": "device_base_state"}


def test_discover_devices_merges_online_and_discovery(agent):
    agent._on_message(None, None, _message(
        "walle/test/device/dev-1/online",
        {"online": True, "device_id": "id-1", "hostname": "dev-one",
         "ip": "192.168.1.11"}))
    results = []

    def worker():
        results.append(agent.discover_devices(timeout=0.5))

    thread = threading.Thread(target=worker)
    thread.start()
    assert _wait_for(lambda: any(
        item["topic"].endswith("/discovery/query/request")
        for item in agent.mqtt_client.published))
    request = [
        item for item in agent.mqtt_client.published
        if item["topic"].endswith("/discovery/query/request")
    ][-1]
    assert request["topic"] == "walle/test/discovery/query/request"
    assert request["payload"]["client_id"] == "agent-test"
    assert request["payload"]["client_name"] == "Test Agent"

    agent._on_message(None, None, _message(
        "walle/test/device/dev-1/discovery/query/response",
        {"online": True, "device_id": "id-1", "hostname": "dev-one",
         "model": "Walle-1"}))
    agent._on_message(None, None, _message(
        "walle/test/device/dev-2/discovery/query/response",
        {"online": True, "device_id": "id-2", "hostname": "dev-two",
         "model": "Walle-2"}))
    thread.join(timeout=2)

    assert results
    out = results[0]
    assert out["ok"] is True
    assert out["count"] == 2
    by_device = {item["device"]: item for item in out["devices"]}
    assert by_device["dev-1"]["source"] == "both"
    assert by_device["dev-1"]["online"] is True
    assert by_device["dev-1"]["model"] == "Walle-1"
    assert by_device["dev-2"]["source"] == "discovery"
    assert by_device["dev-2"]["online"] is True
    assert by_device["dev-2"]["hostname"] == "dev-two"
    assert "2 台设备" in out["reply"]


def test_discover_no_devices_is_ok(agent):
    out = agent.discover_devices(timeout=0.05)
    assert out["ok"] is True
    assert out["count"] == 0
    assert out["devices"] == []
    assert out["reply"] == "未发现设备。"


def test_parse_intent_routes_device_discover():
    for text in ("刷新设备", "发现设备", "设备列表", "有哪些设备"):
        intent, error = car_agent.parse_intent(text)
        assert error is None
        assert intent == {"action": "device_discover"}, text
    intent, _ = car_agent.parse_intent("设备状态")
    assert intent == {"action": "device_base_state"}
    intent, _ = car_agent.parse_intent("状态")
    assert intent == {"action": "status"}


def test_chat_refresh_devices_without_selected_device(agent, monkeypatch):
    monkeypatch.setattr(car_agent, "_AGENT", agent)
    monkeypatch.setattr(agent, "discover_devices", lambda: {
        "ok": True, "action": "device_discover", "devices": [], "count": 0,
        "reply": "未发现设备。",
    })

    agent._run_chat_request({
        "request_id": "chat-1", "client_id": "web-a",
        "client_name": "Web A", "device": "", "text": "刷新设备",
    })

    responses = [
        item for item in agent.mqtt_client.published
        if item["topic"].endswith("/chat/response")
    ]
    assert responses
    payload = responses[-1]["payload"]
    assert payload["ok"] is True
    assert payload["reply"] == "未发现设备。"


def test_parse_intent_status_targets():
    intent, error = car_agent.parse_intent("状态")
    assert error is None
    assert intent == {"action": "status"}

    intent, _ = car_agent.parse_intent("agent 状态")
    assert intent == {"action": "agent_status"}

    intent, _ = car_agent.parse_intent("服务健康")
    assert intent == {"action": "service_health"}

    intent, _ = car_agent.parse_intent("设备状态")
    assert intent == {"action": "device_base_state"}

    intent, _ = car_agent.parse_intent("运行状态")
    assert intent == {"action": "device_run_state"}


def test_execute_status_combines_device_base_and_run(agent, monkeypatch):
    monkeypatch.setattr(agent, "device_base_state", lambda device: {
        "ok": True, "reply": "IP 192.168.1.10；CPU 10.0%",
        "summary": {"ip": "192.168.1.10"},
    })
    monkeypatch.setattr(agent, "device_run_state", lambda device: {
        "ok": True, "reply": "未录像；电量 8.00V",
        "summary": {"battery": {"voltage_v": 8.0}},
    })

    out = agent.execute("dev-1", {"action": "status"}, "Web A")

    assert out["ok"] is True
    assert out["action"] == "status"
    assert out["baseState"]["ok"] is True
    assert out["runState"]["ok"] is True
    assert "CPU 10.0%" in out["reply"]
    assert "电量 8.00V" in out["reply"]


def test_execute_status_returns_partial_when_one_side_fails(agent, monkeypatch):
    monkeypatch.setattr(agent, "device_base_state", lambda device: {
        "ok": True, "reply": "IP 192.168.1.10",
        "summary": {},
    })
    monkeypatch.setattr(agent, "device_run_state", lambda device: {
        "ok": False, "error": "timeout", "cause": "等待应答超时",
    })

    out = agent.execute("dev-1", {"action": "status"}, "Web A")

    assert out["ok"] is True
    assert "IP 192.168.1.10" in out["reply"]
    assert "运行状态查询失败" in out["reply"]


def test_agent_status_returns_machine_state(agent, monkeypatch):
    monkeypatch.setattr(car_agent, "machine_state", lambda: {
        "hostname": "agent-pc", "ip": "127.0.0.1",
        "os": {"name": "Ubuntu"},
        "cpu": {"usage_percent": 5.0, "cores": 8, "temperature_c": 40.0},
        "memory": {"usage_percent": 20.0, "used_bytes": 2 * 1024 ** 3,
                   "total_bytes": 8 * 1024 ** 3},
        "disk": {"usage_percent": 30.0, "free_bytes": 50 * 1024 ** 3},
        "network": {"rx_bytes_per_sec": 100, "tx_bytes_per_sec": 50},
        "uptime_sec": 123,
    })

    out = agent.agent_status()

    assert out["ok"] is True
    assert out["summary"]["cpu_percent"] == 5.0
    assert out["summary"]["memory_percent"] == 20.0
    assert out["summary"]["ip"] == "127.0.0.1"
    assert "agent 机器状态" in out["reply"]
    assert "CPU 5.0%" in out["reply"]
    assert "运行 2分" in out["reply"]


def test_service_health_returns_summary(agent, monkeypatch):
    monkeypatch.setattr(agent, "health", lambda device=None: {
        "ok": True,
        "mqtt": {"connected": True},
        "devices": [{"online": True}, {"online": False}],
        "moving": False,
        "chat": {"requests": 2, "errors": 0},
        "llm_enabled": False,
    })

    out = agent.service_health("dev-1")

    assert out["ok"] is True
    assert "MQTT 已连接" in out["reply"]
    assert "在线设备 1/2" in out["reply"]
    assert "chat 请求 2/错误 0" in out["reply"]
    assert "LLM 关闭" in out["reply"]


def test_chat_agent_status_without_selected_device(agent, monkeypatch):
    monkeypatch.setattr(car_agent, "_AGENT", agent)
    monkeypatch.setattr(agent, "agent_status", lambda: {
        "ok": True, "action": "agent_status",
        "reply": "agent 机器状态：CPU 5.0%",
    })

    agent._run_chat_request({
        "request_id": "chat-2", "client_id": "web-a",
        "client_name": "Web A", "device": "", "text": "agent 状态",
    })

    responses = [
        item for item in agent.mqtt_client.published
        if item["topic"].endswith("/chat/response")
    ]
    assert responses
    payload = responses[-1]["payload"]
    assert payload["ok"] is True
    assert payload["reply"] == "agent 机器状态：CPU 5.0%"


def _chat_response(agent):
    agent._run_chat_request({
        "request_id": "chat-data", "client_id": "web-a",
        "client_name": "Web A", "device": "dev-1", "text": "测试",
    })
    responses = [
        item for item in agent.mqtt_client.published
        if item["topic"].endswith("/chat/response")
    ]
    assert responses
    return responses[-1]["payload"]


def test_chat_response_stream_start_carries_room_number(agent, monkeypatch):
    monkeypatch.setattr(car_agent, "handle_chat", lambda *args: {
        "ok": True, "action": "stream_start", "room_number": "room-123",
        "reply": "已开始推流，房间号：room-123。",
        "payload": {"result": 0, "room_number": "room-123"},
    })

    payload = _chat_response(agent)

    assert payload["data"]["room_number"] == "room-123"
    assert payload["result"] == {"result": 0, "room_number": "room-123"}


def test_chat_response_record_list_carries_filenames(agent, monkeypatch):
    monkeypatch.setattr(car_agent, "handle_chat", lambda *args: {
        "ok": True, "action": "record_list", "count": 1,
        "reply": "共 1 个录像：rec_20261127_080000.fmp4。",
        "records": [{
            "filename": "rec_20261127_080000.fmp4", "size_bytes": 1024,
            "created_at": "2026-11-27 08:00:00", "duration_sec": 60,
            "play_url": "http://192.168.1.10:8088/recordings/rec_20261127_080000.fmp4",
            "extra": "drop-me",
        }],
    })

    payload = _chat_response(agent)

    assert payload["data"]["records"] == [{
        "filename": "rec_20261127_080000.fmp4", "size_bytes": 1024,
        "created_at": "2026-11-27 08:00:00", "duration_sec": 60,
        "play_url": "http://192.168.1.10:8088/recordings/rec_20261127_080000.fmp4",
    }]
    assert "result" not in payload  # 设备原始应答缺失时不伪造


def test_chat_response_servo_set_carries_applied_skipped(agent, monkeypatch):
    monkeypatch.setattr(car_agent, "handle_chat", lambda *args: {
        "ok": True, "action": "servo_set",
        "reply": "舵机已设置：ID1=1500；已跳过：ID2=3000（越界）。",
        "applied": [{"id": 1, "position": 1500}],
        "skipped": [{"id": 2, "position": 3000, "reason": "out_of_range"}],
        "payload": {"result": 0, "applied": [{"id": 1, "position": 1500}],
                    "skipped": [{"id": 2, "position": 3000,
                                 "reason": "out_of_range"}]},
    })

    payload = _chat_response(agent)

    assert payload["data"]["servos"]["applied"] == [{"id": 1, "position": 1500}]
    assert payload["data"]["servos"]["skipped"][0]["reason"] == "out_of_range"


def test_chat_response_truncates_large_device_result(agent, monkeypatch):
    monkeypatch.setattr(car_agent, "handle_chat", lambda *args: {
        "ok": True, "action": "record_list", "records": [],
        "reply": "暂无录像。",
        "payload": {"result": 0, "cause": "ok", "records": [],
                    "blob": "x" * 6000},
    })

    payload = _chat_response(agent)

    assert payload["result"]["truncated"] is True
    assert payload["result"]["summary"]["result"] == 0
    assert payload["result"]["summary"]["records_count"] == 0


def _progress_messages(agent):
    return [item["payload"] for item in agent.mqtt_client.published
            if item["topic"].endswith("/chat/progress/report")]


def _fake_bus(monkeypatch, agent, by_resource):
    def fake(device, resource, payload=None, **kwargs):
        data = by_resource.get(resource, {"result": 0})
        return {"received": True, "request_id": "agent-progress-test",
                "payload": data, "decoded": car_agent.decode_result(data)}
    monkeypatch.setattr(agent.request_bus, "request", fake)


def test_stream_start_reports_chat_progress(agent, monkeypatch):
    monkeypatch.setattr(agent, "preflight_features", lambda *args: {"ok": True})
    _fake_bus(monkeypatch, agent, {
        "stream/start/request": {"result": 0, "room_number": "room-42"},
    })
    agent.progress_local.request_id = "web-progress-1"
    try:
        out = agent.stream_start("dev-1")
    finally:
        agent.progress_local.request_id = None

    assert out["ok"] is True
    progress = _progress_messages(agent)
    assert [item["stage"] for item in progress] == [
        "accepted", "waiting", "completed"]
    assert all(item["request_id"] == "web-progress-1" for item in progress)
    assert all(item["device"] == "dev-1" for item in progress)
    assert "room-42" in progress[-1]["message"]


def test_record_start_reports_chat_progress(agent, monkeypatch):
    monkeypatch.setattr(agent, "preflight_features", lambda *args: {"ok": True})
    _fake_bus(monkeypatch, agent, {
        "record/start/request": {
            "result": 0, "filename": "rec_20261127_080000.fmp4"},
    })
    agent.progress_local.request_id = "web-progress-2"
    try:
        out = agent.record_start("dev-1")
    finally:
        agent.progress_local.request_id = None

    assert out["ok"] is True
    progress = _progress_messages(agent)
    assert [item["stage"] for item in progress] == [
        "accepted", "waiting", "completed"]
    assert "rec_20261127_080000.fmp4" in progress[-1]["message"]


def test_servo_status_reports_failed_progress(agent, monkeypatch):
    _fake_bus(monkeypatch, agent, {
        "car/servo/status/query/request": {
            "result": -3, "cause": "服务不可用"},
    })
    agent.progress_local.request_id = "web-progress-3"
    try:
        out = agent.servo_status("dev-1")
    finally:
        agent.progress_local.request_id = None

    assert out["ok"] is False
    progress = _progress_messages(agent)
    assert [item["stage"] for item in progress] == [
        "accepted", "waiting", "failed"]
    assert progress[-1]["message"] == "服务不可用"


def test_chat_request_sets_and_clears_progress_context(agent, monkeypatch):
    seen = {}

    def fake_handle_chat(client_name, device, text):
        seen["request_id"] = agent._chat_request_id()
        return {"ok": True, "reply": "ok", "action": "status"}

    monkeypatch.setattr(car_agent, "handle_chat", fake_handle_chat)
    agent._run_chat_request({
        "request_id": "web-ctx-1", "client_id": "web-a",
        "client_name": "Web A", "device": "dev-1", "text": "状态",
    })

    assert seen["request_id"] == "web-ctx-1"
    assert agent._chat_request_id() is None


def test_capabilities_cached_from_run_state(agent):
    payload = _run_state_payload()
    payload["recording"]["is_supported"] = False
    payload["battery"]["is_supported"] = False
    agent._on_message(None, None, _message(
        "walle/test/device/dev-1/runState/query/response", payload))

    caps = agent.capabilities("dev-1")

    assert caps["recording"] is False
    assert caps["battery"] is False
    assert caps["stream"] is True
    assert caps["motor"] is True
    assert caps["sonar"] is True


def test_recording_task_preflight_rejects_unsupported(agent):
    payload = _run_state_payload()
    payload["recording"]["is_supported"] = False
    agent._on_message(None, None, _message(
        "walle/test/device/dev-1/runState/query/response", payload))

    out = agent.preflight_recording_task("dev-1")

    assert out["ok"] is False
    assert out["error"] == "capability_unsupported"
    assert "recording.is_supported=false" in out["cause"]
    assert not [
        item for item in agent.mqtt_client.published
        if item["topic"].endswith("/runState/query/request")
    ]


def test_recording_task_preflight_allows_supported(agent):
    agent._on_message(None, None, _message(
        "walle/test/device/dev-1/runState/query/response",
        _run_state_payload()))

    out = agent.preflight_recording_task("dev-1")

    assert out["ok"] is True
    assert out["capabilities"]["recording"] is True


def test_recording_task_preflight_rejects_unknown(agent, monkeypatch):
    monkeypatch.setattr(agent, "device_run_state", lambda device: {
        "ok": False, "error": "timeout", "cause": "等待应答超时"})

    out = agent.preflight_recording_task("dev-1")

    assert out["ok"] is False
    assert out["error"] == "capability_unknown"


def test_motor_preflight_blocks_unsupported_without_publish(agent):
    payload = _run_state_payload()
    payload["motor"]["is_supported"] = False
    agent._on_message(None, None, _message(
        "walle/test/device/dev-1/runState/query/response", payload))

    out = agent.execute(
        "dev-1",
        {"action": "move", "direction": "forward",
         "speed_mps": 0.5, "distance_m": 1.0},
        "Web A")

    assert out["ok"] is False
    assert out["accepted"] is False
    assert out["error"] == "unsupported"
    assert "motor" in out["cause"]
    assert not [
        item for item in agent.mqtt_client.published
        if item["topic"].endswith("/car/motor/set/request")
        or item["topic"].endswith("/control/acquire/request")
    ]


def test_alarm_updates_registry_and_notice(agent):
    agent._on_message(None, None, _message(
        "walle/test/device/dev-1/alarm/report", {
            "event": "alarm", "level": "warning", "metric": "battery",
            "alarm_type": "battery_low", "message": "电池电压过低：当前 6.5V",
            "detail": {"value": 6.5, "unit": "V", "threshold": 7.0},
        }))
    agent._on_message(None, None, _message(
        "walle/test/device/dev-1/alarm/report", {
            "event": "alarm", "level": "warning", "metric": "obstacle",
            "alarm_type": "obstacle_near", "message": "前方 25cm 有障碍物",
            "detail": {"distance_cm": 25.0},
        }))
    agent._on_message(None, None, _message(
        "walle/test/device/dev-1/alarm/report", {
            "event": "alarm", "level": "warning", "metric": "stream",
            "alarm_type": "video_lost", "message": "推流中断",
        }))

    state = agent.device_state("dev-1")
    assert state["last_alarm"]["alarm_type"] == "video_lost"
    assert state["battery"]["voltage_v"] == 6.5
    assert state["sonar"]["distance_cm"] == 25.0
    assert state["stream"]["streaming"] is False
    assert agent.last_alarm_notice["alarm_type"] == "video_lost"
    assert agent.health("dev-1")["last_alarm"]["message"] == "推流中断"


def _start_fake_motion(agent, direction="forward"):
    session = car_agent.MotionSession(
        "dev-1", {"action": "move", "direction": direction}, "test")
    session.request_id = "agent-move-fake"
    worker = threading.Thread(target=session.abort.wait, args=(5,), daemon=True)
    session.thread = worker
    worker.start()
    with agent.state_lock:
        agent.sessions["dev-1"] = session
    return session


def test_obstacle_alarm_stops_forward_motion(agent, monkeypatch):
    calls = []
    monkeypatch.setattr(agent, "stop", lambda client_name=None, device=None: (
        calls.append((client_name, device)) or {"ok": True, "stopped": True}))
    session = _start_fake_motion(agent, "forward")

    agent._on_message(None, None, _message(
        "walle/test/device/dev-1/alarm/report", {
            "event": "alarm", "level": "warning", "metric": "obstacle",
            "alarm_type": "obstacle_near", "message": "前方 25cm 有障碍物",
            "detail": {"distance_cm": 25.0},
        }))

    assert _wait_for(lambda: bool(calls)
                     and agent.last_alarm_notice.get("action") == "stopped")
    assert calls[0] == ("device_alarm", "dev-1")
    assert agent.last_alarm_notice["action"] == "stopped"
    session.abort.set()
    session.thread.join(timeout=2)


def test_obstacle_alarm_does_not_stop_reverse_motion(agent, monkeypatch):
    calls = []
    monkeypatch.setattr(agent, "stop", lambda client_name=None, device=None: (
        calls.append((client_name, device)) or {"ok": True}))
    session = _start_fake_motion(agent, "backward")

    agent._on_message(None, None, _message(
        "walle/test/device/dev-1/alarm/report", {
            "event": "alarm", "level": "warning", "metric": "obstacle",
            "alarm_type": "obstacle_near", "message": "前方 25cm 有障碍物",
            "detail": {"distance_cm": 25.0},
        }))

    assert not _wait_for(lambda: bool(calls), timeout=0.2)
    assert calls == []
    assert agent.last_alarm_notice["alarm_type"] == "obstacle_near"
    session.abort.set()
    session.thread.join(timeout=2)


def test_battery_critical_alarm_stops_motion(agent, monkeypatch):
    calls = []
    monkeypatch.setattr(agent, "stop", lambda client_name=None, device=None: (
        calls.append((client_name, device)) or {"ok": True, "stopped": True}))
    session = _start_fake_motion(agent, "backward")

    agent._on_message(None, None, _message(
        "walle/test/device/dev-1/alarm/report", {
            "event": "alarm", "level": "critical", "metric": "battery",
            "alarm_type": "battery_low", "message": "电池电压极低：当前 6.0V",
            "detail": {"value": 6.0, "unit": "V", "threshold": 6.6},
        }))

    assert _wait_for(lambda: bool(calls))
    assert calls[0] == ("device_alarm", "dev-1")
    session.abort.set()
    session.thread.join(timeout=2)


def test_recent_alarm_and_intent(agent):
    agent._on_message(None, None, _message(
        "walle/test/device/dev-1/alarm/report", {
            "event": "alarm", "level": "warning", "metric": "battery",
            "alarm_type": "battery_low", "message": "电池电压过低",
            "detail": {"value": 6.5, "unit": "V", "threshold": 7.0},
        }))

    out = agent.recent_alarm("dev-1")
    assert out["ok"] is True
    assert "电池电压过低" in out["reply"]

    intent, error = car_agent.parse_intent("最新告警")
    assert error is None
    assert intent == {"action": "alarm_status"}


def _published(agent, suffix):
    return [
        item for item in agent.mqtt_client.published
        if item["topic"].endswith(suffix)
    ]


def test_record_start_success_acquires_and_releases(agent):
    out = _call_with_response(
        agent, lambda: agent.record_start("dev-1", 1800),
        "/record/start/request",
        "walle/test/device/dev-1/record/start/response", {
            "result": 0, "cause": "ok",
            "filename": "rec_test.fmp4",
            "started_at": "2026-11-27 14:00:00",
            "play_url": "http://192.168.1.10:8088/recordings/rec_test.fmp4",
        })

    assert out["ok"] is True
    assert out["recording"] is True
    assert out["filename"] == "rec_test.fmp4"
    assert out["control"]["ok"] is True
    assert agent.control_held == {}
    request = _published(agent, "/record/start/request")[-1]["payload"]
    assert request["segment_sec"] == 1800
    assert len(_published(agent, "/control/acquire/request")) == 1
    assert len(_published(agent, "/control/release/request")) == 1


def test_record_start_disk_full_releases_control(agent):
    out = _call_with_response(
        agent, lambda: agent.record_start("dev-1"),
        "/record/start/request",
        "walle/test/device/dev-1/record/start/response", {
            "result": -7, "cause": "磁盘空间不足",
        })

    assert out["ok"] is False
    assert out["error"] == "no_space"
    assert "磁盘空间不足" in out["cause"]
    assert agent.control_held == {}
    assert len(_published(agent, "/control/release/request")) == 1


def test_record_stop_already_stopped_is_idempotent(agent):
    out = _call_with_response(
        agent, lambda: agent.record_stop("dev-1"),
        "/record/stop/request",
        "walle/test/device/dev-1/record/stop/response", {
            "result": -4, "cause": "当前未录制",
        })

    assert out["ok"] is True
    assert out["already_stopped"] is True
    assert out["recording"] is False
    assert agent.control_held == {}


def test_record_list_is_query_without_lease(agent):
    out = _call_with_response(
        agent, lambda: agent.record_list("dev-1"),
        "/record/list/query/request",
        "walle/test/device/dev-1/record/list/query/response", {
            "result": 0, "cause": "ok",
            "records": [
                {"filename": "a.fmp4", "size_bytes": 100,
                 "created_at": "2026-11-27 10:00:00",
                 "duration_sec": 10, "play_url": "http://x/a.fmp4"},
                {"filename": "b.fmp4", "size_bytes": 200,
                 "created_at": "2026-11-27 11:00:00",
                 "duration_sec": 20, "play_url": "http://x/b.fmp4"},
            ],
            "used_bytes": 300, "limit_bytes": 1000,
        })

    assert out["ok"] is True
    assert out["count"] == 2
    assert out["records"][0]["filename"] == "a.fmp4"
    assert out["used_bytes"] == 300
    assert out["limit_bytes"] == 1000
    assert _published(agent, "/control/acquire/request") == []
    assert _published(agent, "/control/release/request") == []


def test_record_start_unsupported_blocks_before_control(agent):
    payload = _run_state_payload()
    payload["recording"]["is_supported"] = False
    agent._on_message(None, None, _message(
        "walle/test/device/dev-1/runState/query/response", payload))

    out = agent.record_start("dev-1")

    assert out["ok"] is False
    assert out["error"] == "unsupported"
    assert agent.control_held == {}
    assert _published(agent, "/record/start/request") == []
    assert _published(agent, "/control/acquire/request") == []


def test_parse_intent_routes_record_commands():
    intent, error = car_agent.parse_intent("开始录像")
    assert error is None
    assert intent == {"action": "record_start", "segment_sec": 0}

    intent, _ = car_agent.parse_intent("开始录像30分钟")
    assert intent == {"action": "record_start", "segment_sec": 1800}

    intent, _ = car_agent.parse_intent("停止录像")
    assert intent == {"action": "record_stop"}

    intent, _ = car_agent.parse_intent("查看录像")
    assert intent == {"action": "record_list"}

    intent, _ = car_agent.parse_intent("在录像吗")
    assert intent == {"action": "device_run_state"}


def test_stream_start_success_returns_room(agent):
    out = _call_with_response(
        agent, lambda: agent.stream_start("dev-1"),
        "/stream/start/request",
        "walle/test/device/dev-1/stream/start/response", {
            "result": 0, "cause": "ok", "action": "started",
            "room_number": "ab12cd34", "camera_device": "/dev/video0",
            "pid": 123, "started_at": "2026-11-27 14:00:00",
        })

    assert out["ok"] is True
    assert out["streaming"] is True
    assert out["already_streaming"] is False
    assert out["room_number"] == "ab12cd34"
    assert out["camera_device"] == "/dev/video0"
    assert out["control"]["ok"] is True
    assert agent.control_held == {}
    assert len(_published(agent, "/control/acquire/request")) == 1
    assert len(_published(agent, "/control/release/request")) == 1


def test_stream_start_already_streaming_reuses_room(agent):
    out = _call_with_response(
        agent, lambda: agent.stream_start("dev-1"),
        "/stream/start/request",
        "walle/test/device/dev-1/stream/start/response", {
            "result": 0, "cause": "ok", "action": "already_streaming",
            "room_number": "ab12cd34", "camera_device": "/dev/video0",
        })

    assert out["ok"] is True
    assert out["already_streaming"] is True
    assert out["room_number"] == "ab12cd34"
    assert out["device_action"] == "already_streaming"
    assert agent.control_held == {}


def test_stream_start_without_camera_reports_unavailable(agent):
    out = _call_with_response(
        agent, lambda: agent.stream_start("dev-1"),
        "/stream/start/request",
        "walle/test/device/dev-1/stream/start/response", {
            "result": -3, "cause": "此设备没有摄像头",
            "action": "failed", "camera_device": None,
        })

    assert out["ok"] is False
    assert out["error"] == "unavailable"
    assert "没有摄像头" in out["cause"]
    assert agent.control_held == {}


def test_stream_start_unsupported_blocks_before_control(agent):
    payload = _run_state_payload()
    payload["stream"]["is_supported"] = False
    agent._on_message(None, None, _message(
        "walle/test/device/dev-1/runState/query/response", payload))

    out = agent.stream_start("dev-1")

    assert out["ok"] is False
    assert out["error"] == "unsupported"
    assert agent.control_held == {}
    assert _published(agent, "/stream/start/request") == []
    assert _published(agent, "/control/acquire/request") == []


def test_stream_stop_success(agent):
    out = _call_with_response(
        agent, lambda: agent.stream_stop("dev-1"),
        "/stream/stop/request",
        "walle/test/device/dev-1/stream/stop/response", {
            "result": 0, "cause": "ok", "stopped": True,
            "room_number": "ab12cd34", "camera_device": "/dev/video0",
        })

    assert out["ok"] is True
    assert out["stopped"] is True
    assert out["room_number"] == "ab12cd34"
    assert out["control"]["ok"] is True
    assert agent.control_held == {}


def test_stream_stop_already_stopped(agent):
    out = _call_with_response(
        agent, lambda: agent.stream_stop("dev-1"),
        "/stream/stop/request",
        "walle/test/device/dev-1/stream/stop/response", {
            "result": -4, "cause": "当前没有推流", "room_number": None,
        })

    assert out["ok"] is True
    assert out["already_stopped"] is True
    assert out["stopped"] is False
    assert agent.control_held == {}


def test_stream_stop_room_mismatch_is_error(agent):
    out = _call_with_response(
        agent, lambda: agent.stream_stop("dev-1", "room-a"),
        "/stream/stop/request",
        "walle/test/device/dev-1/stream/stop/response", {
            "result": -4, "cause": "当前推流房间号为 room-b",
            "room_number": "room-b",
        })

    assert out["ok"] is False
    assert out["error"] == "room_mismatch"
    assert "room-b" in out["cause"]
    assert agent.control_held == {}


def test_parse_intent_routes_stream_commands():
    intent, error = car_agent.parse_intent("开始推流")
    assert error is None
    assert intent == {"action": "stream_start"}

    intent, _ = car_agent.parse_intent("停止推流")
    assert intent == {"action": "stream_stop"}

    intent, _ = car_agent.parse_intent("开始直播")
    assert intent == {"action": "stream_start"}

    intent, _ = car_agent.parse_intent("在推流吗")
    assert intent == {"action": "device_run_state"}


def test_servo_status_is_query_without_lease(agent):
    out = _call_with_response(
        agent, lambda: agent.servo_status("dev-1"),
        "/car/servo/status/query/request",
        "walle/test/device/dev-1/car/servo/status/query/response", {
            "result": 0, "cause": "ok",
            "servos": [
                {"id": 1, "position": 1500, "offset": None,
                 "available": True, "error": None},
                {"id": 2, "position": 1480, "offset": None,
                 "available": True, "error": None},
            ],
        })

    assert out["ok"] is True
    assert out["servos"][0]["position"] == 1500
    assert out["servos"][1]["position"] == 1480
    assert _published(agent, "/control/acquire/request") == []
    assert _published(agent, "/control/release/request") == []
    request = _published(agent, "/car/servo/status/query/request")[-1]["payload"]
    assert request["ids"] == [1, 2]
    assert request["include_offset"] is False


def test_servo_status_include_offset_uses_long_timeout(agent):
    out = _call_with_response(
        agent, lambda: agent.servo_status(
            "dev-1", ids=[1], include_offset=True),
        "/car/servo/status/query/request",
        "walle/test/device/dev-1/car/servo/status/query/response", {
            "result": 0, "cause": "ok",
            "servos": [{"id": 1, "position": 1500, "offset": 20,
                        "available": True, "error": None}],
        })

    assert out["ok"] is True
    assert out["servos"][0]["offset"] == 20
    request = _published(agent, "/car/servo/status/query/request")[-1]["payload"]
    assert request["ids"] == [1]
    assert request["include_offset"] is True


def test_servo_set_success_acquires_and_releases(agent):
    out = _call_with_response(
        agent, lambda: agent.servo_set(
            "dev-1", [{"id": 1, "position": 1400},
                      {"id": 2, "position": 1600}], 1.0),
        "/car/servo/set/request",
        "walle/test/device/dev-1/car/servo/set/response", {
            "result": 0, "cause": "ok", "duration": 1.0,
            "applied": [{"id": 1, "position": 1400},
                        {"id": 2, "position": 1600}],
            "skipped": [],
        })

    assert out["ok"] is True
    assert out["applied"][0] == {"id": 1, "position": 1400}
    assert out["skipped"] == []
    assert out["control"]["ok"] is True
    assert agent.control_held == {}
    request = _published(agent, "/car/servo/set/request")[-1]["payload"]
    assert request["servos"] == [{"id": 1, "position": 1400},
                                 {"id": 2, "position": 1600}]
    assert request["duration"] == 1.0
    assert len(_published(agent, "/control/acquire/request")) == 1
    assert len(_published(agent, "/control/release/request")) == 1


def test_servo_set_reports_skipped_out_of_range(agent):
    out = _call_with_response(
        agent, lambda: agent.servo_set(
            "dev-1", [{"id": 1, "position": 1400},
                      {"id": 2, "position": 2300}], 1.0),
        "/car/servo/set/request",
        "walle/test/device/dev-1/car/servo/set/response", {
            "result": 0, "cause": "ok", "duration": 1.0,
            "applied": [{"id": 1, "position": 1400}],
            "skipped": [{"id": 2, "position": 2300,
                         "reason": "超出 800-2200"}],
        })

    assert out["ok"] is True
    assert out["applied"] == [{"id": 1, "position": 1400}]
    assert out["skipped"][0]["id"] == 2
    assert "800-2200" in out["skipped"][0]["reason"]


def test_servo_center_sets_both_to_1500(agent):
    out = _call_with_response(
        agent, lambda: agent.servo_center("dev-1"),
        "/car/servo/set/request",
        "walle/test/device/dev-1/car/servo/set/response", {
            "result": 0, "cause": "ok", "duration": 1.0,
            "applied": [{"id": 1, "position": 1500},
                        {"id": 2, "position": 1500}],
            "skipped": [],
        })

    assert out["ok"] is True
    assert out["action"] == "servo_center"
    request = _published(agent, "/car/servo/set/request")[-1]["payload"]
    assert request["servos"] == [{"id": 1, "position": 1500},
                                 {"id": 2, "position": 1500}]


def test_servo_raise_queries_then_sets(agent):
    results = []
    worker = threading.Thread(
        target=lambda: results.append(
            agent.servo_relative("dev-1", 1, car_agent.SERVO_STEP)),
        daemon=True)
    worker.start()

    assert _wait_for(lambda: bool(
        _published(agent, "/car/servo/status/query/request")))
    status_request = _published(
        agent, "/car/servo/status/query/request")[-1]["payload"]
    agent._on_message(None, None, _message(
        "walle/test/device/dev-1/car/servo/status/query/response", {
            "request_id": status_request["request_id"],
            "result": 0, "cause": "ok",
            "servos": [{"id": 1, "position": 1500, "offset": None,
                        "available": True, "error": None}],
        }))

    assert _wait_for(lambda: bool(_published(agent, "/car/servo/set/request")))
    set_request = _published(agent, "/car/servo/set/request")[-1]["payload"]
    agent._on_message(None, None, _message(
        "walle/test/device/dev-1/car/servo/set/response", {
            "request_id": set_request["request_id"],
            "result": 0, "cause": "ok", "duration": 1.0,
            "applied": [{"id": 1, "position": 1600}], "skipped": [],
        }))
    worker.join(timeout=2)

    out = results[0]
    assert out["ok"] is True
    assert out["previous_position"] == 1500
    assert out["target"] == 1600
    assert out["delta"] == 100
    assert set_request["servos"] == [{"id": 1, "position": 1600}]
    assert agent.control_held == {}


def test_servo_set_unsupported_blocks_before_control(agent):
    payload = _run_state_payload()
    payload["servo"]["is_supported"] = False
    agent._on_message(None, None, _message(
        "walle/test/device/dev-1/runState/query/response", payload))

    out = agent.servo_set("dev-1", [{"id": 1, "position": 1500}])

    assert out["ok"] is False
    assert out["error"] == "unsupported"
    assert agent.control_held == {}
    assert _published(agent, "/car/servo/set/request") == []
    assert _published(agent, "/control/acquire/request") == []


def test_parse_intent_routes_servo_commands():
    intent, error = car_agent.parse_intent("舵机回中")
    assert error is None
    assert intent == {"action": "servo_center"}

    intent, _ = car_agent.parse_intent("抬高一点")
    assert intent == {"action": "servo_relative", "servo_id": 1,
                      "delta": car_agent.SERVO_STEP}

    intent, _ = car_agent.parse_intent("舵机状态")
    assert intent == {"action": "servo_status"}

    intent, _ = car_agent.parse_intent("舵机1到1400")
    assert intent == {"action": "servo_set", "duration": 1.0,
                      "servos": [{"id": 1, "position": 1400}]}


# ---------------- M2-08 动作层契约：payload / 结果解码 / 幂等 / 错误码 ----------------

def test_record_start_already_recording_is_idempotent(agent):
    out = _call_with_response(
        agent, lambda: agent.record_start("dev-1"),
        "/record/start/request",
        "walle/test/device/dev-1/record/start/response", {
            "result": -4, "cause": "已在录制",
            "filename": "rec_existing.fmp4",
        })

    assert out["ok"] is True
    assert out["recording"] is True
    assert out["already_recording"] is True
    assert out["filename"] == "rec_existing.fmp4"
    assert agent.control_held == {}


def test_record_start_timeout_reports_error_and_releases(agent, monkeypatch):
    monkeypatch.setattr(agent, "_acquire_control", lambda device: {"ok": True})
    monkeypatch.setattr(agent, "_release_control", lambda device: {"ok": True})
    monkeypatch.setattr(agent.request_bus, "request", lambda *args, **kwargs: {
        "received": False, "error": "timeout", "cause": "等待录像应答超时",
    })

    out = agent.record_start("dev-1")

    assert out["ok"] is False
    assert out["error"] == "timeout"
    assert out["cause"] == "等待录像应答超时"
    assert out["control"]["ok"] is True


def test_record_stop_success_returns_filename(agent):
    out = _call_with_response(
        agent, lambda: agent.record_stop("dev-1"),
        "/record/stop/request",
        "walle/test/device/dev-1/record/stop/response", {
            "result": 0, "cause": "ok", "filename": "rec_test.fmp4",
        })

    assert out["ok"] is True
    assert out["recording"] is False
    assert out["filename"] == "rec_test.fmp4"
    request = _published(agent, "/record/stop/request")[-1]["payload"]
    assert "request_id" in request
    assert agent.control_held == {}


def test_record_list_failure_keeps_empty_records(agent):
    out = _call_with_response(
        agent, lambda: agent.record_list("dev-1"),
        "/record/list/query/request",
        "walle/test/device/dev-1/record/list/query/response", {
            "result": -6, "cause": "查询录像列表失败",
        })

    assert out["ok"] is False
    assert out["error"] == "failed"
    assert out["records"] == []
    assert out["count"] == 0


def test_stream_stop_payload_carries_room_number(agent):
    out = _call_with_response(
        agent, lambda: agent.stream_stop("dev-1", "room-a"),
        "/stream/stop/request",
        "walle/test/device/dev-1/stream/stop/response", {
            "result": 0, "cause": "ok", "stopped": True,
            "room_number": "room-a",
        })

    assert out["ok"] is True
    assert out["stopped"] is True
    request = _published(agent, "/stream/stop/request")[-1]["payload"]
    assert request["room_number"] == "room-a"
    assert agent.control_held == {}


def test_servo_set_control_denied_returns_holder(agent):
    out = _call_with_response(
        agent, lambda: agent.servo_set(
            "dev-1", [{"id": 1, "position": 1500}], 0.5),
        "/car/servo/set/request",
        "walle/test/device/dev-1/car/servo/set/response", {
            "result": -5, "cause": "控制权被占用",
            "control_holder": {"client_id": "web-1",
                               "client_name": "Web A"},
        })

    assert out["ok"] is False
    assert out["error"] == "control_denied"
    assert out["control_holder"]["client_id"] == "web-1"
    request = _published(agent, "/car/servo/set/request")[-1]["payload"]
    assert request["duration"] == 0.5
    assert agent.control_held == {}


def test_servo_status_partial_keeps_available_results(agent):
    out = _call_with_response(
        agent, lambda: agent.servo_status("dev-1", ids=[1, 2]),
        "/car/servo/status/query/request",
        "walle/test/device/dev-1/car/servo/status/query/response", {
            "result": -3, "cause": "部分舵机不可用",
            "servos": [
                {"id": 1, "position": 1500, "available": True,
                 "error": None},
                {"id": 2, "position": None, "available": False,
                 "error": "read_failed"},
            ],
        })

    assert out["ok"] is False
    assert out["partial"] is True
    assert [item["id"] for item in out["servos"]] == [1, 2]
    assert _published(agent, "/control/acquire/request") == []


def test_chat_response_record_start_carries_recording(agent, monkeypatch):
    monkeypatch.setattr(car_agent, "handle_chat", lambda *args: {
        "ok": True, "action": "record_start", "recording": True,
        "filename": "rec_test.fmp4", "started_at": "2026-11-27 14:00:00",
        "reply": "已开始录像：rec_test.fmp4",
        "payload": {"result": 0, "filename": "rec_test.fmp4"},
    })

    payload = _chat_response(agent)

    assert payload["data"]["recording"]["recording"] is True
    assert payload["data"]["recording"]["filename"] == "rec_test.fmp4"


def test_chat_response_servo_status_carries_servos(agent, monkeypatch):
    servos = [{"id": 1, "position": 1500, "available": True, "error": None}]
    monkeypatch.setattr(car_agent, "handle_chat", lambda *args: {
        "ok": True, "action": "servo_status", "servos": servos,
        "reply": "舵机状态：ID1（上下）=1500。",
        "payload": {"result": 0, "servos": servos},
    })

    payload = _chat_response(agent)

    assert payload["data"]["servos"] == servos


# ---------------- M3-04 任务管理 ----------------

def _use_task_store(agent, tmp_path):
    store = car_agent.TaskStore(str(tmp_path / "agent.db"))
    agent.task_store = store
    agent.cfg["TASK_DB_PATH"] = str(tmp_path / "agent.db")
    return store


def _task_create_intent(at="2030-01-01 08:00:00"):
    return {
        "action": "task_create", "name": "测试任务",
        "schedule": {"type": "once", "at": at},
        "actions": [{"phase": "start", "tool": "record.start"}],
    }


def test_task_create_computes_next_run_and_persists(agent, tmp_path, monkeypatch):
    _use_task_store(agent, tmp_path)
    monkeypatch.setattr(agent, "preflight_recording_task",
                        lambda device: {"ok": True})

    intent = _task_create_intent()
    out = agent.task_create(intent["name"], "dev-1", intent["schedule"],
                            intent["actions"])

    assert out["ok"] is True
    assert out["first_run_at"] == "2030-01-01 08:00:00"
    assert out["task"]["enabled"] is True
    assert out["task"]["schedule"]["type"] == "once"
    stored = agent.task_db().get_task(out["task_id"])
    assert stored["next_run_at"] == "2030-01-01 08:00:00"


def test_task_create_rejects_unsupported_recording(agent, tmp_path, monkeypatch):
    _use_task_store(agent, tmp_path)
    monkeypatch.setattr(agent, "preflight_recording_task", lambda device: {
        "ok": False, "error": "capability_unsupported",
        "cause": "设备不支持录像",
    })

    out = agent.task_create(
        "测试任务", "dev-1", {"type": "once", "at": "2030-01-01 08:00:00"},
        [{"phase": "start", "tool": "record.start"}])

    assert out["ok"] is False
    assert out["error"] == "capability_unsupported"
    assert agent.task_db().list_tasks() == []


def test_task_create_requires_start_stop_for_daily_window(agent, tmp_path,
                                                         monkeypatch):
    _use_task_store(agent, tmp_path)
    monkeypatch.setattr(agent, "preflight_recording_task",
                        lambda device: {"ok": True})

    out = agent.task_create(
        "窗口任务", "dev-1",
        {"type": "daily_window", "start": "08:00", "stop": "21:00"},
        [{"phase": "start", "tool": "record.start"}])

    assert out["ok"] is False
    assert out["error"] == "bad_param"
    assert "start 和 stop" in out["cause"]


def test_task_list_includes_last_run(agent, tmp_path, monkeypatch):
    store = _use_task_store(agent, tmp_path)
    monkeypatch.setattr(agent, "preflight_recording_task",
                        lambda device: {"ok": True})
    created = agent.task_create(
        "测试任务", "dev-1", {"type": "once", "at": "2030-01-01 08:00:00"},
        [{"phase": "start", "tool": "record.start"}])
    store.record_run(created["task_id"], "start",
                     scheduled_at="2030-01-01 08:00:00", result=0,
                     finished_at="2030-01-01 08:00:01")

    out = agent.task_list("dev-1")

    assert out["count"] == 1
    assert out["tasks"][0]["last_run"]["phase"] == "start"
    assert out["tasks"][0]["last_run"]["result"] == 0


def test_task_pause_resume_cancel(agent, tmp_path, monkeypatch):
    _use_task_store(agent, tmp_path)
    monkeypatch.setattr(agent, "preflight_recording_task",
                        lambda device: {"ok": True})
    created = agent.task_create(
        "测试任务", "dev-1", {"type": "interval", "seconds": 3600},
        [{"phase": "start", "tool": "record.start"}])
    task_id = created["task_id"]

    paused = agent.task_pause(task_id)
    assert paused["ok"] is True
    assert paused["task"]["enabled"] is False

    resumed = agent.task_resume(task_id)
    assert resumed["ok"] is True
    assert resumed["task"]["enabled"] is True
    assert resumed["next_run_at"]

    cancelled = agent.task_cancel(task_id)
    assert cancelled["ok"] is True
    assert agent.task_db().get_task(task_id) is None


def test_mqtt_task_create_and_list(agent, tmp_path, monkeypatch):
    _use_task_store(agent, tmp_path)
    monkeypatch.setattr(agent, "preflight_recording_task",
                        lambda device: {"ok": True})
    agent._on_message(None, None, _message(
        "walle/test/agent/agent-test/task/create/request", {
            "request_id": "web-task-1",
            "device": "dev-1",
            "name": "测试任务",
            "schedule": {"type": "once", "at": "2030-01-01 08:00:00"},
            "actions": [{"phase": "start", "tool": "record.start"}],
        }))
    assert _wait_for(lambda: bool(
        _published(agent, "/task/create/response")))
    created = _published(agent, "/task/create/response")[-1]["payload"]
    assert created["result"] == 0
    assert created["task_id"].startswith("task-")
    assert created["next_run_at"] == "2030-01-01 08:00:00"

    agent._on_message(None, None, _message(
        "walle/test/agent/agent-test/task/list/query/request", {
            "request_id": "web-task-2", "device": "dev-1"}))
    assert _wait_for(lambda: bool(
        _published(agent, "/task/list/query/response")))
    listed = _published(agent, "/task/list/query/response")[-1]["payload"]
    assert listed["result"] == 0
    assert listed["count"] == 1
    assert listed["tasks"][0]["task_id"] == created["task_id"]


def test_mqtt_task_create_requires_request_id(agent, tmp_path):
    _use_task_store(agent, tmp_path)
    agent._on_message(None, None, _message(
        "walle/test/agent/agent-test/task/create/request", {
            "device": "dev-1",
        }))
    assert _wait_for(lambda: bool(
        _published(agent, "/task/create/response")))
    payload = _published(agent, "/task/create/response")[-1]["payload"]
    assert payload["result"] == -1
    assert "request_id" in payload["cause"]


def test_parse_intent_routes_task_commands():
    intent, _ = car_agent.parse_intent("查看任务")
    assert intent == {"action": "task_list"}

    intent, _ = car_agent.parse_intent("取消任务 task-1a2b3c4d")
    assert intent == {"action": "task_cancel", "task_id": "task-1a2b3c4d"}

    intent, _ = car_agent.parse_intent("每天8点开始录像，晚上9点停止")
    assert intent["action"] == "task_create"
    assert intent["schedule"] == {
        "type": "daily_window", "start": "08:00", "stop": "21:00"}
    assert intent["actions"] == [
        {"phase": "start", "tool": "record.start"},
        {"phase": "stop", "tool": "record.stop"},
    ]

    intent, _ = car_agent.parse_intent("明天早上8点开始录像")
    assert intent["schedule"]["type"] == "once"

    intent, _ = car_agent.parse_intent("每隔2小时录像")
    assert intent["schedule"] == {"type": "interval", "seconds": 7200}


# ---------------- M3-05 任务执行 ----------------

def _window_task(store, next_run_at="2026-11-27 08:00:00"):
    return store.create_task(
        "录像窗口", "dev-1",
        {"type": "daily_window", "start": "08:00", "stop": "21:00"},
        [{"phase": "start", "tool": "record.start"},
         {"phase": "stop", "tool": "record.stop"}],
        next_run_at=next_run_at)


def test_task_run_start_records_run_and_advances(agent, tmp_path, monkeypatch):
    store = _use_task_store(agent, tmp_path)
    task = _window_task(store)
    monkeypatch.setattr(agent, "record_start", lambda device: {
        "ok": True, "action": "record_start", "result": 0,
        "request_id": "agent-record-1", "filename": "rec.fmp4",
        "cause": "ok"})
    advanced = []
    monkeypatch.setattr(agent, "_advance_task",
                        lambda store, fresh: advanced.append(fresh["id"]))

    agent._run_task(task, "2026-11-27 08:00:00")

    runs = store.list_runs(task["id"])
    assert len(runs) == 1
    assert runs[0]["phase"] == "start"
    assert runs[0]["result"] == 0
    assert runs[0]["request_id"] == "agent-record-1"
    assert runs[0]["finished_at"]
    assert advanced == [task["id"]]


def test_task_run_stop_minus_4_is_success(agent, tmp_path, monkeypatch):
    store = _use_task_store(agent, tmp_path)
    task = _window_task(store, next_run_at="2026-11-27 21:00:00")
    monkeypatch.setattr(agent, "record_stop", lambda device: {
        "ok": True, "action": "record_stop", "result": -4,
        "already_stopped": True, "cause": "当前未录制"})

    agent._run_task(task, "2026-11-27 21:00:00")

    run = store.list_runs(task["id"])[0]
    assert run["phase"] == "stop"
    assert run["result"] == -4
    assert run["error"] is None
    assert run["cause"] == "当前未录制"


def test_task_run_no_space_does_not_retry(agent, tmp_path, monkeypatch):
    store = _use_task_store(agent, tmp_path)
    task = _window_task(store)
    calls = []

    def fail(device):
        calls.append(device)
        return {"ok": False, "action": "record_start", "result": -7,
                "error": "no_space", "cause": "磁盘空间不足"}

    monkeypatch.setattr(agent, "record_start", fail)
    agent._run_task(task, "2026-11-27 08:00:00")

    assert calls == ["dev-1"]
    run = store.list_runs(task["id"])[0]
    assert run["error"] == "no_space"
    assert run["result"] == -7


def test_task_run_retries_on_failed(agent, tmp_path, monkeypatch):
    store = _use_task_store(agent, tmp_path)
    agent.cfg["TASK_RETRY_MAX"] = "1"
    agent.cfg["TASK_RETRY_BACKOFF_SEC"] = "0"
    task = _window_task(store)
    calls = []

    def flaky(device):
        calls.append(device)
        if len(calls) == 1:
            return {"ok": False, "action": "record_start", "result": -6,
                    "error": "failed", "cause": "执行失败"}
        return {"ok": True, "action": "record_start", "result": 0,
                "request_id": "agent-record-2", "cause": "ok"}

    monkeypatch.setattr(agent, "record_start", flaky)
    agent._run_task(task, "2026-11-27 08:00:00")

    assert len(calls) == 2
    run = store.list_runs(task["id"])[0]
    assert run["error"] is None
    assert run["request_id"] == "agent-record-2"


def test_task_run_control_denied_records_holder(agent, tmp_path, monkeypatch):
    store = _use_task_store(agent, tmp_path)
    task = _window_task(store)
    calls = []

    def denied(device):
        calls.append(device)
        return {"ok": False, "action": "record_start", "result": -5,
                "error": "control_denied", "cause": "控制权被占用",
                "control_holder": {"client_id": "web-1",
                                   "client_name": "Web A"}}

    monkeypatch.setattr(agent, "record_start", denied)
    agent._run_task(task, "2026-11-27 08:00:00")

    assert len(calls) == 1
    run = store.list_runs(task["id"])[0]
    assert run["error"] == "control_denied"
    assert "Web A" in run["cause"]


def test_task_run_skips_disabled_task(agent, tmp_path, monkeypatch):
    store = _use_task_store(agent, tmp_path)
    task = _window_task(store)
    store.update_task(task["id"], enabled=False)
    calls = []
    monkeypatch.setattr(agent, "record_start",
                        lambda device: calls.append(device) or {"ok": True})

    agent._run_task(task, "2026-11-27 08:00:00")

    assert calls == []
    assert store.list_runs(task["id"]) == []


def test_task_run_does_not_repeat_same_slot(agent, tmp_path, monkeypatch):
    store = _use_task_store(agent, tmp_path)
    task = _window_task(store)
    calls = []
    monkeypatch.setattr(agent, "record_start", lambda device: calls.append(
        device) or {"ok": True, "action": "record_start", "result": 0,
                    "cause": "ok"})

    agent._run_task(task, "2026-11-27 08:00:00")
    agent._run_task(task, "2026-11-27 08:00:00")

    assert calls == ["dev-1"]
    assert len(store.list_runs(task["id"])) == 1


def test_start_task_scheduler_starts_and_stops(agent, tmp_path):
    _use_task_store(agent, tmp_path)

    scheduler = agent.start_task_scheduler()

    assert scheduler.running is True
    agent.shutdown()
    assert agent.task_scheduler is None
    assert scheduler.running is False
