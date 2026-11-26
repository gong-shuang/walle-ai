#!/usr/bin/env python3
"""runState/query/response：只返回运行状态，不再包含能力枚举字段。"""

import json
import time

import net_mqtt_bridge.agent as agent
import net_mqtt_bridge.record as record
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
        "video", "phone_audio", "recording", "motor", "servo", "battery", "sonar",
        "control_holder",
    }
    assert data["control_holder"] is None
    for field in REMOVED_FIELDS:
        assert field not in data


def test_runtime_groups_reflect_device_state():
    monitor = _bare_monitor()
    monitor._video_status = lambda: {
        "streaming": True, "camera_device": "/dev/video0", "room_number": "room-1",
        "audio": {"enabled": True}, "pid": 123, "started_at": 1700000000,
        "uptime_sec": 12,
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

    assert data["video"] == {
        "streaming": True, "camera_device": "/dev/video0", "room_number": "room-1",
        "audio": {"enabled": True}, "pid": 123, "started_at": 1700000000,
        "uptime_sec": 12,
    }
    assert data["phone_audio"] == {"playing": True, "room_number": "audio-1"}
    assert data["recording"] == {
        "enable": record.RECORD_ENABLED, "recording": True, "filename": "rec.mp4",
    }
    assert data["motor"] == {"moving": True}
    assert data["servo"] == {"moving": True}
    assert data["battery"] == {"voltage_v": 7.42, "available": True}
    assert data["sonar"] == {"distance_cm": 35.5, "available": True}


def test_invalid_payload_is_ignored():
    monitor = _bare_monitor()
    published = []
    monitor.publish_json = lambda resource, data, retain=False: published.append(
        (resource, data))

    monitor.handle_capability_query("not-json")

    assert published == []
