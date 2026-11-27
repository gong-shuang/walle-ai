#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M1-02 DeviceRegistry：设备状态缓存、过期与 is_supported/available 处理。"""

import threading

import car_agent


def _agent():
    """不启动 MQTT/线程，只构造缓存需要的属性。"""
    instance = object.__new__(car_agent.Agent)
    instance.devices = {}
    instance.dev_lock = threading.Lock()
    return instance


def _update(agent, device, resource, payload, now):
    item = agent._dev(device)
    agent._update_device_cache(
        item, "walle/test/device/{}/{}".format(device, resource),
        payload, now=now)


def _run_state():
    return {
        "device_id": "dev-id-1",
        "hostname": "walle-pc",
        "stream": {
            "is_supported": True, "streaming": True, "room_number": "room-1",
            "camera_device": "/dev/video0", "audio_input": "default",
            "started_at": "2026-11-27 10:00:00", "uptime_sec": 12.0,
        },
        "phone_audio": {
            "is_supported": True, "playing": False, "room_number": None,
        },
        "recording": {
            "is_supported": True, "recording": False, "filename": None,
            "camera_device": "/dev/video0", "audio_input": "default",
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


def test_online_base_and_run_state_are_cached():
    agent = _agent()
    _update(agent, "dev-1", "online", {
        "online": True, "device_id": "dev-id-1", "hostname": "walle-pc",
        "timestamp": 1000,
    }, now=100)
    _update(agent, "dev-1", "baseState/query/response", {
        "device_id": "dev-id-1", "cpu": {"percent": 10},
        "memory": {"percent": 20},
    }, now=101)
    _update(agent, "dev-1", "runState/query/response", _run_state(), now=102)

    state = agent.device_state("dev-1", now=103)

    assert state["online"] is True
    assert state["online_ts"] == 100
    assert state["device_id"] == "dev-id-1"
    assert state["baseState"]["cpu"]["percent"] == 10
    assert state["runState"]["stream"]["streaming"] is True
    assert state["stream"]["room_number"] == "room-1"
    assert state["recording"]["recording"] is False
    assert state["battery"]["voltage_v"] == 8.0
    assert state["sonar"]["distance_cm"] == 120.0
    assert state["control_holder"]["client_id"] == "web-a"


def test_is_supported_false_clears_group_values():
    agent = _agent()
    run = _run_state()
    run["battery"] = {
        "is_supported": False, "voltage_v": 8.0, "available": True,
    }
    run["stream"] = {
        "is_supported": False, "streaming": True, "room_number": "room-1",
    }
    _update(agent, "dev-1", "runState/query/response", run, now=100)

    state = agent.device_state("dev-1", now=101)

    assert state["battery"]["is_supported"] is False
    assert state["battery"]["voltage_v"] is None
    assert state["battery"]["available"] is False
    assert state["stream"]["is_supported"] is False
    assert state["stream"]["streaming"] is False
    assert state["stream"]["room_number"] is None


def test_available_false_clears_sensor_values():
    agent = _agent()
    run = _run_state()
    run["battery"] = {
        "is_supported": True, "voltage_v": 8.0, "available": False,
    }
    run["sonar"] = {
        "is_supported": True, "distance_cm": 120.0, "available": False,
    }
    _update(agent, "dev-1", "runState/query/response", run, now=100)

    state = agent.device_state("dev-1", now=101)

    assert state["battery"]["available"] is False
    assert state["battery"]["voltage_v"] is None
    assert state["sonar"]["available"] is False
    assert state["sonar"]["distance_cm"] is None


def test_state_expires_after_ttl():
    agent = _agent()
    _update(agent, "dev-1", "online", {"online": True}, now=100)
    _update(agent, "dev-1", "baseState/query/response", {"cpu": {}}, now=100)
    _update(agent, "dev-1", "runState/query/response", _run_state(), now=100)

    fresh = agent.device_state("dev-1", now=101)
    expired = agent.device_state(
        "dev-1", now=100 + car_agent.DEVICE_STATE_TTL_SEC + 1)

    assert fresh["runState"] is not None
    assert fresh["battery"]["voltage_v"] == 8.0
    assert expired["online"] is True
    assert expired["baseState"] is None
    assert expired["runState"] is None
    assert expired["battery"] is None
    assert expired["control_holder"] is None


def test_offline_and_restart_clear_state():
    agent = _agent()
    _update(agent, "dev-1", "online", {
        "online": True, "device_id": "dev-id-1"}, now=100)
    _update(agent, "dev-1", "runState/query/response", _run_state(), now=101)

    _update(agent, "dev-1", "online", {"online": False}, now=102)
    offline = agent.device_state("dev-1", now=103)
    assert offline["online"] is False
    assert offline["runState"] is None
    assert offline["battery"] is None
    assert offline["control_holder"] is None

    _update(agent, "dev-1", "online", {"online": True}, now=104)
    restarted = agent.device_state("dev-1", now=105)
    assert restarted["online"] is True
    assert restarted["baseState"] is None
    assert restarted["runState"] is None


def test_device_id_change_resets_cached_state():
    agent = _agent()
    _update(agent, "dev-1", "online", {
        "online": True, "device_id": "dev-id-1"}, now=100)
    _update(agent, "dev-1", "runState/query/response", _run_state(), now=101)
    assert agent.device_state("dev-1", now=102)["runState"] is not None

    _update(agent, "dev-1", "online", {
        "online": True, "device_id": "dev-id-2"}, now=103)
    state = agent.device_state("dev-1", now=104)

    assert state["device_id"] == "dev-id-2"
    assert state["runState"] is None
    assert state["battery"] is None


def test_alarm_and_control_holder_are_cached():
    agent = _agent()
    _update(agent, "dev-1", "online", {"online": True}, now=100)
    _update(agent, "dev-1", "runState/query/response", _run_state(), now=101)
    _update(agent, "dev-1", "alarm/report", {
        "event": "alarm", "level": "warning", "alarm_type": "battery_low",
        "message": "电池电压过低", "timestamp": 123,
    }, now=102)

    state = agent.device_state("dev-1", now=103)
    assert state["last_alarm"]["alarm_type"] == "battery_low"
    assert state["control_holder"]["client_id"] == "web-a"

    _update(agent, "dev-1", "control/release/response", {
        "result": 0, "action": "released", "control_holder": None,
    }, now=104)
    assert agent.device_state("dev-1", now=105)["control_holder"] is None

    _update(agent, "dev-1", "control/acquire/response", {
        "result": 0, "action": "granted",
        "control_holder": {"client_id": "agent-test"},
    }, now=106)
    assert agent.device_state("dev-1", now=107)["control_holder"][
        "client_id"] == "agent-test"


def test_unknown_device_returns_none():
    assert _agent().device_state("missing", now=100) is None
