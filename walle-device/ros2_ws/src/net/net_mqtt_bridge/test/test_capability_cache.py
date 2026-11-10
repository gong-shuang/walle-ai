#!/usr/bin/env python3
"""P2-1.2：capability 查询后台缓存与只读缓存模式的测试。"""

import json
import threading
import time

import pytest

import net_mqtt_bridge.agent as agent
import net_mqtt_bridge.config as config
from net_mqtt_bridge.agent import RpiMonitor

FIXED_UUID = "0f8fad5b-d9cb-469f-a165-70867728950e"


def _bare_monitor():
    """构造一个不走 __init__ 的实例，只填本组用例需要的属性。"""
    monitor = object.__new__(RpiMonitor)
    monitor.lan_ip = "192.168.1.10"
    monitor.capability_lock = threading.Lock()
    monitor.capability_result = None
    monitor.capability_at = 0.0
    monitor.capability_refreshing = False
    monitor.capability_ready = threading.Event()
    return monitor


def _capability_payload(camera_count=1):
    return {
        "device_id": "dev-1",
        "uuid": FIXED_UUID,
        "hostname": "walle",
        "ip": "192.168.1.10",
        "capability": "device",
        "capabilities": ["system_status", "camera_video_capture"],
        "camera_count": camera_count,
        "cameras": [{"node": "/dev/video0"}] if camera_count else [],
        "audio": {
            "capture": {"available": True, "device": "default",
                        "sources": ["mic"], "reason": None},
            "playback": {"available": True, "device": "sink", "sinks": ["sink"],
                         "volume_percent": 50, "muted": False, "reason": None},
        },
        "summary": {
            "total_cameras": camera_count,
            "total_formats": 1,
            "total_resolutions": 1,
            "audio_sources": 1,
            "audio_sinks": 1,
            "probe_cost_ms": 5,
        },
        "probe": {"tool": "v4l2-ctl + pactl", "error": None, "audio_error": None},
        "timestamp": 1,
        "datetime": "2026-11-10 00:00:00",
    }


@pytest.fixture
def fixed_uuid(monkeypatch):
    monkeypatch.setattr(config, "DEVICE_UUID", FIXED_UUID)
    return FIXED_UUID


def test_capability_query_serves_cache_without_probing(fixed_uuid, monkeypatch):
    monitor = _bare_monitor()
    monitor.capability_result = _capability_payload(camera_count=2)
    monitor.capability_at = time.monotonic()
    monkeypatch.setattr(config, "CAPABILITY_CACHE_ONLY", False)
    called = []

    def _build():
        called.append(1)
        return _capability_payload()

    monkeypatch.setattr(monitor, "build_capability", _build)
    published = []
    monitor.publish_json = lambda resource, data, retain=False: published.append(
        (resource, data))

    start = time.monotonic()
    monitor.handle_capability_query(json.dumps({"uuid": FIXED_UUID, "request_id": "c1"}))
    cost = time.monotonic() - start

    assert cost < 0.1
    assert called == []
    resource, data = published[0]
    assert resource == agent.RES_CAPABILITY_RESULT
    assert data["request_id"] == "c1"
    assert data["camera_count"] == 2
    assert data["cache"]["source"] == "cache"
    assert data["cache"]["age_sec"] >= 0.0


def test_capability_cache_only_returns_pending(fixed_uuid, monkeypatch):
    monitor = _bare_monitor()
    monkeypatch.setattr(config, "CAPABILITY_CACHE_ONLY", True)
    monkeypatch.setattr(config, "CAPABILITY_FIRST_WAIT", 5.0)
    published = []
    monitor.publish_json = lambda resource, data, retain=False: published.append(
        (resource, data))

    start = time.monotonic()
    monitor.handle_capability_query(json.dumps({"uuid": FIXED_UUID}))
    cost = time.monotonic() - start

    assert cost < 0.1
    _, data = published[0]
    assert data["cache"]["source"] == "pending"
    assert data["probe"]["pending"] is True
    assert data["capabilities"] == ["system_status"]


def test_first_query_waits_for_background_probe(fixed_uuid, monkeypatch):
    monitor = _bare_monitor()
    monkeypatch.setattr(config, "CAPABILITY_CACHE_ONLY", False)
    monkeypatch.setattr(config, "CAPABILITY_FIRST_WAIT", 2.0)
    published = []
    monitor.publish_json = lambda resource, data, retain=False: published.append(
        (resource, data))

    def _probe():
        time.sleep(0.1)
        monitor.capability_result = _capability_payload(camera_count=3)
        monitor.capability_at = time.monotonic()
        monitor.capability_ready.set()

    worker = threading.Thread(target=_probe)
    worker.start()
    start = time.monotonic()
    monitor.handle_capability_query(json.dumps({"uuid": FIXED_UUID}))
    cost = time.monotonic() - start
    worker.join()

    assert cost < 1.0
    _, data = published[0]
    assert data["camera_count"] == 3
    assert data["cache"]["source"] == "cache"


def test_refresh_capability_caches_result(monkeypatch):
    monitor = _bare_monitor()
    calls = []

    def _build():
        calls.append(1)
        return _capability_payload(camera_count=2)

    monkeypatch.setattr(monitor, "build_capability", _build)

    assert monitor.refresh_capability() is True
    assert calls == [1]
    result, age = monitor.capability_snapshot()
    assert result["camera_count"] == 2
    assert age >= 0.0
    assert monitor.capability_ready.is_set()
    assert monitor.capability_refreshing is False


def test_refresh_failure_keeps_old_cache(monkeypatch):
    monitor = _bare_monitor()
    monitor.capability_result = _capability_payload(camera_count=1)
    monitor.capability_at = time.monotonic()

    def _boom():
        raise RuntimeError("v4l2 探测失败")

    monkeypatch.setattr(monitor, "build_capability", _boom)

    assert monitor.refresh_capability() is False
    result, _ = monitor.capability_snapshot()
    assert result["camera_count"] == 1
    assert monitor.capability_refreshing is False
