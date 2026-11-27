#!/usr/bin/env python3
"""P1-5.2：MQTT 协议 request_id 解析与主题命名测试。"""

import json

import pytest
from paho.mqtt.client import topic_matches_sub

import net_mqtt_bridge.agent as agent
import net_mqtt_bridge.config as config
from net_mqtt_bridge.agent import RpiMonitor


@pytest.fixture
def valid_mqtt_config(monkeypatch):
    values = {
        "MQTT_HOST": "broker.local",
        "MQTT_PORT": 8883,
        "_MQTT_PORT_RAW": "8883",
        "MQTT_USER": "device",
        "MQTT_PASSWORD": "secret",
        "MQTT_TENANT": "tenant",
        "MQTT_TLS": "on",
        "MQTT_CA_FILE": "",
        "WALLE_RUNTIME_ENV": "prod",
    }
    for name, value in values.items():
        monkeypatch.setattr(config, name, value)


def test_generate_device_uuid_is_removed():
    assert not hasattr(agent, "generate_device_uuid")
    assert not hasattr(config, "DEVICE_UUID")


@pytest.mark.parametrize("data,expected", [
    ({"request_id": "abc"}, "abc"),
    ({"request_id": 123}, "123"),
    ({"request_id": ""}, ""),
    ({"request_id": None}, ""),
    ({}, ""),
    (None, ""),
])
def test_rid_defaults_to_empty_string(data, expected):
    assert RpiMonitor._rid(data) == expected


def test_parse_request_accepts_json_object_without_uuid():
    payload = json.dumps({"request_id": "req-1"})
    data = RpiMonitor.parse_request(payload, "baseState/query/request")
    assert data["request_id"] == "req-1"


@pytest.mark.parametrize("payload", [
    "",
    "   ",
    "not-json",
    json.dumps([]),
    json.dumps({}),
])
def test_parse_request_rejects_invalid_payload(payload):
    assert RpiMonitor.parse_request(payload, "baseState/query/request") is None


def test_video_request_keeps_request_id():
    monitor = object.__new__(RpiMonitor)
    req = monitor._video_req({"request_id": "req-2"})
    assert req == {"request_id": "req-2"}


def test_subscriptions_cover_all_requests_with_layer_patterns():
    topics = [topic for topic, _qos in agent.subscriptions()]
    base = agent.device_topic_base()
    discovery = agent.tenant_topic(agent.REQ_DISCOVERY)

    assert topics == [
        base + "/+/+/request",
        base + "/+/+/+/request",
        base + "/+/+/+/+/request",
        discovery,
        agent.tenant_topic("client/+/online"),
        agent.tenant_topic("agent/+/online"),
    ]
    # 只订请求/持续控制，不订 # 整棵子树，避免收回自己发布的 response/alarm
    assert all("#" not in topic for topic in topics)

    for req in config.REQUEST_TO_RESULT:
        full = discovery if req == config.REQ_DISCOVERY else base + "/" + req
        assert any(topic_matches_sub(topic, full) for topic in topics), \
            f"{req} 没有被任何订阅模式覆盖"
    assert not any(topic_matches_sub(topic, base + "/" + config.RES_STATE_RESULT)
                   for topic in topics)
    assert not any(topic_matches_sub(topic, base + "/" + config.RES_ALARM)
                   for topic in topics)


def test_device_topic_base_sanitizes_segments():
    assert agent.device_topic_base(
        "user@example.com", "dev:1") == "walle/user_example_com/device/dev_1"
    assert agent.device_topic_base("", "") == "walle/unknown/device/unknown"


def test_client_presence_online_and_offline():
    import threading

    monitor = object.__new__(RpiMonitor)
    monitor.client_presence = {}
    monitor.client_presence_lock = threading.Lock()
    topic = agent.client_online_topic("web-abc12345")

    monitor._handle_client_presence(topic, json.dumps({
        "client_id": "web-abc12345",
        "client_name": "Web",
        "version_code": 3,
        "version_name": "0.1.3",
        "online": True,
        "timestamp": 1763000000,
    }))
    assert monitor.client_is_online("web-abc12345") is True

    monitor._handle_client_presence(topic, json.dumps({
        "client_id": "web-abc12345",
        "client_name": "Web",
        "online": False,
        "timestamp": 1763000100,
    }))
    assert monitor.client_is_online("web-abc12345") is False


def test_client_presence_rejects_id_mismatch():
    import threading

    monitor = object.__new__(RpiMonitor)
    monitor.client_presence = {}
    monitor.client_presence_lock = threading.Lock()
    topic = agent.client_online_topic("web-abc12345")

    monitor._handle_client_presence(topic, json.dumps({
        "client_id": "web-other999",
        "online": True,
    }))

    assert monitor.client_presence == {}


def test_agent_presence_online_and_offline():
    import threading

    monitor = object.__new__(RpiMonitor)
    monitor.agent_presence = {}
    monitor.agent_presence_lock = threading.Lock()
    topic = agent.agent_online_topic("agent-b851fdde")

    monitor._handle_agent_presence(topic, json.dumps({
        "agent_id": "agent-b851fdde",
        "agent_name": "小橙车",
        "version_code": 5,
        "version_name": "0.2.0",
        "online": True,
        "ts": 1795449995,
    }))
    assert monitor.agent_is_online("agent-b851fdde") is True

    monitor._handle_agent_presence(topic, json.dumps({
        "agent_id": "agent-b851fdde",
        "agent_name": "小橙车",
        "online": False,
    }))
    assert monitor.agent_is_online("agent-b851fdde") is False


def test_agent_presence_rejects_id_mismatch():
    import threading

    monitor = object.__new__(RpiMonitor)
    monitor.agent_presence = {}
    monitor.agent_presence_lock = threading.Lock()
    topic = agent.agent_online_topic("agent-b851fdde")

    monitor._handle_agent_presence(topic, json.dumps({
        "agent_id": "agent-other999",
        "online": True,
    }))
    assert monitor.agent_presence == {}


def test_car_motor_topics_and_realtime_dispatch_registered():
    assert config.REQ_CAR_MOTOR_SET == "car/motor/set/request"
    assert (config.REQUEST_TO_RESULT[config.REQ_CAR_MOTOR_SET]
            == config.RES_CAR_MOTOR_SET_RESULT)
    assert (config.REQUEST_HANDLERS[config.REQ_CAR_MOTOR_SET]
            == "handle_car_motor_set")
    assert config.REQ_CAR_MOTOR_SET in config.REALTIME_REQUESTS


def test_car_servo_status_topics_and_dispatch_registered():
    assert (config.REQUEST_TO_RESULT[config.REQ_CAR_SERVO_STATUS]
            == config.RES_CAR_SERVO_STATUS_RESULT)
    assert (config.REQUEST_HANDLERS[config.REQ_CAR_SERVO_STATUS]
            == "handle_car_servo_status")
    assert config.REQ_CAR_SERVO_STATUS in config.SERVO_REQUESTS


def test_car_servo_set_topics_and_realtime_dispatch_registered():
    assert config.REQ_CAR_SERVO_SET == "car/servo/set/request"
    assert (config.REQUEST_TO_RESULT[config.REQ_CAR_SERVO_SET]
            == config.RES_CAR_SERVO_SET_RESULT)
    assert (config.REQUEST_HANDLERS[config.REQ_CAR_SERVO_SET]
            == "handle_car_servo_set")
    assert config.REQ_CAR_SERVO_SET in config.REALTIME_REQUESTS


def test_topic_naming_convention():
    """请求-应答对：请求 /request、应答 /response；持续控制 /cmd；上报 /report。"""
    for req, res in config.REQUEST_TO_RESULT.items():
        assert req.endswith("/request"), f"请求主题缺少 /request: {req}"
        assert res.endswith("/response"), f"应答主题缺少 /response: {res}"
    assert not [req for req in config.REQUEST_TO_RESULT if req.endswith("/cmd")]
    assert config.RES_ONLINE == "online"
    assert config.RES_ALARM.endswith("/report")


def test_debug_allows_plaintext_mqtt_on_1883(valid_mqtt_config, monkeypatch):
    monkeypatch.setattr(config, "WALLE_RUNTIME_ENV", "debug")
    monkeypatch.setattr(config, "MQTT_PORT", 1883)
    monkeypatch.setattr(config, "_MQTT_PORT_RAW", "1883")
    monkeypatch.setattr(config, "MQTT_TLS", "off")

    assert config.validate_mqtt_config() == []
    assert config.tls_enabled() is False


@pytest.mark.parametrize("mqtt_tls", ["on", "off"])
def test_prod_rejects_mqtt_port_1883(valid_mqtt_config, monkeypatch, mqtt_tls):
    monkeypatch.setattr(config, "MQTT_PORT", 1883)
    monkeypatch.setattr(config, "_MQTT_PORT_RAW", "1883")
    monkeypatch.setattr(config, "MQTT_TLS", mqtt_tls)

    problems = config.validate_mqtt_config()
    assert any("MQTT_PORT=1883" in problem for problem in problems)


def test_prod_rejects_plaintext_mqtt(valid_mqtt_config, monkeypatch):
    monkeypatch.setattr(config, "MQTT_TLS", "off")

    problems = config.validate_mqtt_config()
    assert any("MQTT_TLS=off" in problem for problem in problems)


def test_alarm_event_uses_unified_source_fields(monkeypatch):
    monitor = object.__new__(RpiMonitor)
    monitor.lan_ip = "192.168.1.10"
    captured = {}
    monkeypatch.setattr(
        monitor, "publish_json",
        lambda resource, payload: captured.update(
            resource=resource, payload=payload))
    monkeypatch.setattr(config, "DEVICE_ID", "dev-1")
    monkeypatch.setattr(config, "HOSTNAME", "pi-1")

    monitor._event("alarm", "warning", "battery", "battery_low",
                   "电池电压过低", {"value": 6.8})

    payload = captured["payload"]
    assert captured["resource"] == config.RES_ALARM
    assert payload["alarm_source_type"] == "device"
    assert payload["alarm_source_id"] == "dev-1"
    assert payload["alarm_source_name"] == "pi-1"
    assert payload["ip"] == "192.168.1.10"
    assert "device_id" not in payload
    assert "hostname" not in payload
