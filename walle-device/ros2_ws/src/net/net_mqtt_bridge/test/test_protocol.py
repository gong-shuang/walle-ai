#!/usr/bin/env python3
"""P1-5.2：MQTT 协议 uuid / request_id 校验与主题命名测试。"""

import json
import uuid as uuidlib

import pytest

import net_mqtt_bridge.agent as agent
import net_mqtt_bridge.config as config
from net_mqtt_bridge.agent import RpiMonitor, generate_device_uuid

FIXED_UUID = "0f8fad5b-d9cb-469f-a165-70867728950e"


def test_generate_device_uuid_is_deterministic():
    first = generate_device_uuid("aa:bb:cc:dd:ee:ff", "192.168.1.10", ts=1700000000)
    second = generate_device_uuid("aa:bb:cc:dd:ee:ff", "192.168.1.10", ts=1700000000)
    assert first == second
    assert uuidlib.UUID(first).version == 5


def test_generate_device_uuid_changes_with_ip_or_time():
    base = generate_device_uuid("aa:bb:cc:dd:ee:ff", "192.168.1.10", ts=1700000000)
    assert base != generate_device_uuid("aa:bb:cc:dd:ee:ff", "192.168.1.11", ts=1700000000)
    assert base != generate_device_uuid("aa:bb:cc:dd:ee:ff", "192.168.1.10", ts=1700000001)


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


@pytest.fixture
def device_uuid(monkeypatch):
    monkeypatch.setattr(config, "DEVICE_UUID", FIXED_UUID)
    return FIXED_UUID


def test_verify_uuid_accepts_matching_uuid(device_uuid):
    payload = json.dumps({"uuid": device_uuid, "request_id": "req-1"})
    ok, data = RpiMonitor.verify_uuid(payload, "state/query")
    assert ok is True
    assert data["request_id"] == "req-1"


def test_verify_uuid_is_case_insensitive(device_uuid):
    payload = json.dumps({"uuid": device_uuid.upper()})
    ok, _ = RpiMonitor.verify_uuid(payload, "state/query")
    assert ok is True


@pytest.mark.parametrize("payload", [
    "",
    "   ",
    "not-json",
    json.dumps([]),
    json.dumps({}),
    json.dumps({"uuid": ""}),
    json.dumps({"uuid": "00000000-0000-0000-0000-000000000000"}),
])
def test_verify_uuid_rejects_invalid_payload(device_uuid, payload):
    ok, data = RpiMonitor.verify_uuid(payload, "state/query")
    assert ok is False
    assert data is None


def test_video_request_keeps_request_id():
    monitor = object.__new__(RpiMonitor)
    req = monitor._video_req(
        {"request_id": "req-2"}, "/dev/video0", "room1")
    assert req == {
        "video_device": "/dev/video0",
        "room_number": "room1",
        "request_id": "req-2",
    }


def test_subscriptions_are_device_scoped_without_wildcards():
    topics = [topic for topic, _qos in agent.subscriptions()]
    base = agent.device_topic_base()
    discovery = agent.tenant_topic(agent.REQ_DISCOVERY)

    assert len(topics) == len(set(topics))
    assert all("#" not in topic and "+" not in topic for topic in topics)
    assert all(topic.startswith(base + "/") or topic == discovery
               for topic in topics)
    assert base + "/" + agent.REQ_STATE in topics
    assert base + "/" + agent.REQ_VIDEO_START in topics
    assert discovery in topics


def test_device_topic_base_sanitizes_segments():
    assert agent.device_topic_base(
        "user@example.com", "dev:1") == "walle/user_example_com/dev_1/v1"
    assert agent.device_topic_base("", "") == "walle/unknown/unknown/v1"
