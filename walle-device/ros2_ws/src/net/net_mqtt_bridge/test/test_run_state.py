#!/usr/bin/env python3
"""runState/query/response：只返回运行状态，不再包含能力枚举字段。"""

import json
import time

import net_mqtt_bridge.agent as agent
import net_mqtt_bridge.capability as capability
from net_mqtt_bridge.agent import RpiMonitor

REMOVED_FIELDS = ("cache", "capability", "capabilities", "camera_count",
                  "cameras", "audio", "summary", "probe")


class _AliveProc:
    def poll(self):
        return None


def _bare_monitor():
    """构造一个不走 __init__ 的实例，只填本组用例需要的属性。"""
    monitor = object.__new__(RpiMonitor)
    monitor.lan_ip = "192.168.1.10"
    return monitor


def _query(monitor):
    published = []
    monitor.publish_json = lambda resource, data, retain=False: published.append(
        (resource, data))
    monitor.handle_capability_query(json.dumps({"request_id": "r1"}))
    return published[0]


def test_response_contains_only_runtime_groups():
    monitor = _bare_monitor()
    resource, data = _query(monitor)

    assert resource == agent.RES_CAPABILITY_RESULT
    assert data["request_id"] == "r1"
    assert set(data) == {
        "device_id", "hostname", "ip", "timestamp", "datetime", "request_id",
        "stream", "phone_audio", "recording", "motor", "servo", "battery", "sonar",
        "control_holder",
    }
    assert data["control_holder"] is None
    for field in REMOVED_FIELDS:
        assert field not in data


def test_runtime_groups_reflect_device_state(monkeypatch):
    monkeypatch.setattr(capability, "RECORD_ENABLED", True)
    monkeypatch.setattr(capability, "CAMERA_DEVICE", "/dev/video0")
    monkeypatch.setattr(capability, "RECORD_AUDIO_INPUT", "default")
    monitor = _bare_monitor()
    monitor._video_status = lambda: {
        "streaming": True, "camera_device": "/dev/video0", "room_number": "room-1",
        "audio": {"enabled": True, "device": "alsa_input.usb-mic"},
        "pid": 123, "started_at": 1700000000, "uptime_sec": 12,
    }
    monitor.audio_proc = _AliveProc()
    monitor.audio_meta = {"room_number": "audio-1"}
    monitor.recording_state = {"recording": True, "filename": "rec.mp4"}
    monitor.motor_moving = True
    monitor.servo_moving_until = time.monotonic() + 10
    monitor.sensor_status = lambda: {
        "battery": {"voltage_v": 7.42, "available": True},
        "sonar": {"distance_cm": 35.5, "available": True},
    }

    _, data = _query(monitor)

    assert data["stream"] == {
        "is_supported": True, "streaming": True, "room_number": "room-1",
        "camera_device": "/dev/video0", "audio_input": "alsa_input.usb-mic",
        "started_at": 1700000000, "uptime_sec": 12,
    }
    assert data["phone_audio"] == {
        "is_supported": True, "playing": True, "room_number": "audio-1"}
    assert data["recording"] == {
        "is_supported": True, "recording": True, "filename": "rec.mp4",
        "camera_device": "/dev/video0", "audio_input": "default",
    }
    assert data["motor"] == {"is_supported": True, "moving": True}
    assert data["servo"] == {"is_supported": True, "moving": True}
    assert data["battery"] == {
        "is_supported": True, "voltage_v": 7.42, "available": True}
    assert data["sonar"] == {
        "is_supported": True, "distance_cm": 35.5, "available": True}


def test_recording_disabled_marks_unsupported(monkeypatch):
    monkeypatch.setattr(capability, "RECORD_ENABLED", False)
    monkeypatch.setattr(capability, "CAMERA_DEVICE", "/dev/video0")
    monkeypatch.setattr(capability, "RECORD_AUDIO_INPUT", "default")
    monitor = _bare_monitor()
    monitor.recording_state = {"recording": True, "filename": "rec.mp4"}

    _, data = _query(monitor)

    assert data["recording"] == {
        "is_supported": False, "recording": False, "filename": None,
        "camera_device": None, "audio_input": None,
    }


def test_support_flags_null_unsupported_groups(monkeypatch):
    monkeypatch.setattr(capability, "RECORD_ENABLED", True)
    monitor = _bare_monitor()
    monitor.support = {name: False for name in capability.DEFAULT_SUPPORT}
    monitor._video_status = lambda: {
        "streaming": True, "camera_device": "/dev/video0", "room_number": "room-1",
        "audio": {"device": "alsa_input.usb-mic"},
        "started_at": 1700000000, "uptime_sec": 12,
    }
    monitor.audio_proc = _AliveProc()
    monitor.audio_meta = {"room_number": "audio-1"}
    monitor.recording_state = {"recording": True, "filename": "rec.mp4"}
    monitor.motor_moving = True
    monitor.servo_moving_until = time.monotonic() + 10
    monitor.sensor_status = lambda: {
        "battery": {"voltage_v": 7.42, "available": True},
        "sonar": {"distance_cm": 35.5, "available": True},
    }

    _, data = _query(monitor)

    assert data["stream"] == {
        "is_supported": False, "streaming": False, "room_number": None,
        "camera_device": None, "audio_input": None,
        "started_at": None, "uptime_sec": None,
    }
    assert data["phone_audio"] == {
        "is_supported": False, "playing": False, "room_number": None}
    assert data["recording"] == {
        "is_supported": False, "recording": False, "filename": None,
        "camera_device": None, "audio_input": None,
    }
    assert data["motor"] == {"is_supported": False, "moving": False}
    assert data["servo"] == {"is_supported": False, "moving": False}
    assert data["battery"] == {
        "is_supported": False, "voltage_v": None, "available": False}
    assert data["sonar"] == {
        "is_supported": False, "distance_cm": None, "available": False}


def test_invalid_payload_is_ignored():
    monitor = _bare_monitor()
    published = []
    monitor.publish_json = lambda resource, data, retain=False: published.append(
        (resource, data))

    monitor.handle_capability_query("not-json")

    assert published == []
