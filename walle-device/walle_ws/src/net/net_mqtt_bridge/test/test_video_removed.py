#!/usr/bin/env python3
"""P2-2.2：agent 侧旧 ffmpeg 推流实现已删除，只保留 video_webrtc_bridge。"""

import json
from pathlib import Path

import pytest

import net_mqtt_bridge
import net_mqtt_bridge.config as config
from net_mqtt_bridge.agent import RpiMonitor

FIXED_UUID = "0f8fad5b-d9cb-469f-a165-70867728950e"


@pytest.fixture
def fixed_uuid(monkeypatch):
    monkeypatch.setattr(config, "DEVICE_UUID", FIXED_UUID)
    return FIXED_UUID


def _bare_monitor():
    monitor = object.__new__(RpiMonitor)
    monitor.lan_ip = "192.168.1.10"
    published = []
    monitor.publish_json = lambda resource, data, retain=False: published.append(
        (resource, data))
    return monitor, published


def test_agent_package_has_no_video_encoder_pipeline():
    package_dir = Path(net_mqtt_bridge.__file__).parent
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(package_dir.glob("*.py")))
    assert "libx264" not in source
    assert '"v4l2"' not in source
    assert "build_ffmpeg_cmd" not in source
    assert "build_rtmp_url" not in source
    assert "_start_video_stream" not in source
    assert "_video_watch" not in source


def test_standalone_video_start_points_to_ros_bridge(fixed_uuid):
    monitor, published = _bare_monitor()
    payload = json.dumps({
        "uuid": FIXED_UUID, "request_id": "v1",
        "video_device": "/dev/video0", "room_number": "room1",
    })
    monitor.handle_video_request(payload)

    resource, data = published[0]
    assert resource == config.RES_VIDEO_START_RESULT
    assert data["request_id"] == "v1"
    assert data["result"] == -2
    assert "video_webrtc_bridge" in data["error"]


def test_standalone_video_stop_reports_no_stream(fixed_uuid):
    monitor, published = _bare_monitor()
    payload = json.dumps({
        "uuid": FIXED_UUID, "request_id": "v2", "room_number": "room1",
    })
    monitor.handle_video_stop(payload)

    resource, data = published[0]
    assert resource == config.RES_VIDEO_STOP_RESULT
    assert data["request_id"] == "v2"
    assert data["result"] == -1
    assert data["stopped"] is False
    assert "video_webrtc_bridge" in data["error"]
