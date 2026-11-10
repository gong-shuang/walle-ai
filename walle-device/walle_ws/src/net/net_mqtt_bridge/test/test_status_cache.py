#!/usr/bin/env python3
"""P2-1.1：state/query 复用监控采样快照的测试。"""

import json
import threading
import time

import pytest

import net_mqtt_bridge.agent as agent
import net_mqtt_bridge.config as config
import net_mqtt_bridge.monitor as monitor_mod
from net_mqtt_bridge.agent import RpiMonitor

FIXED_UUID = "0f8fad5b-d9cb-469f-a165-70867728950e"


def _bare_monitor():
    """构造一个不走 __init__ 的实例，只填本组用例需要的属性。"""
    monitor = object.__new__(RpiMonitor)
    monitor.sample_lock = threading.Lock()
    monitor.status_sample = None
    monitor.status_sample_lock = threading.Lock()
    monitor.active_alarms = set()
    monitor.usb_state = {}
    monitor.lan_ip = "192.168.1.10"
    monitor.video_lock = threading.Lock()
    monitor.video_proc = None
    monitor.video_meta = {}
    return monitor


@pytest.fixture
def fixed_uuid(monkeypatch):
    monkeypatch.setattr(config, "DEVICE_UUID", FIXED_UUID)
    return FIXED_UUID


def test_build_status_reuses_monitor_sample(monkeypatch):
    monitor = _bare_monitor()
    monitor.status_sample = (
        [10.0, 20.0],
        {"eth0": {"rx_bytes_per_sec": 1.0, "tx_bytes_per_sec": 2.0,
                  "rx_rate": "1 B/s", "tx_rate": "2 B/s",
                  "rx_total_bytes": 10, "tx_total_bytes": 20}},
        100.0, 50.0, 5.0, time.monotonic(),
    )
    monkeypatch.setattr(monitor_mod, "disk_mounts", lambda: [])

    def _no_live_sample(*_args, **_kwargs):
        raise AssertionError("命中缓存时不应现场采样")

    monkeypatch.setattr(monitor, "sample_once", _no_live_sample)

    status = monitor.build_status()
    assert status["cpu"]["percent"] == 15.0
    assert status["cpu"]["sample_interval_sec"] == 5.0
    assert status["network"]["total_rx_bytes_per_sec"] == 100.0
    assert status["network"]["total_tx_bytes_per_sec"] == 50.0
    assert status["sample_age_sec"] >= 0.0
    assert status["video"]["streaming"] is False


def test_build_status_falls_back_to_live_sample(monkeypatch):
    monitor = _bare_monitor()
    monkeypatch.setattr(monitor_mod, "disk_mounts", lambda: [])
    calls = []

    def _live(interval):
        calls.append(interval)
        return [1.0], {}, 1.0, 2.0, interval

    monkeypatch.setattr(monitor, "sample_once", _live)
    status = monitor.build_status()
    assert calls == [0.2]
    assert status["cpu"]["percent"] == 1.0


def test_handle_query_status_responds_under_100ms(fixed_uuid, monkeypatch):
    monitor = _bare_monitor()
    monitor.status_sample = ([5.0], {}, 0.0, 0.0, 5.0, time.monotonic())
    monkeypatch.setattr(monitor_mod, "disk_mounts", lambda: [])
    published = []
    monitor.publish_json = lambda resource, data, retain=False: published.append(
        (resource, data))

    start = time.monotonic()
    monitor.handle_query_status(json.dumps({"uuid": FIXED_UUID, "request_id": "r1"}))
    cost = time.monotonic() - start

    assert cost < 0.1
    resource, data = published[0]
    assert resource == agent.RES_STATE_RESULT
    assert data["request_id"] == "r1"
    assert data["cpu"]["per_core_percent"] == [5.0]
