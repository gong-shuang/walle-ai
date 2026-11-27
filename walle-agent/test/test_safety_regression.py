#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M0-08：控制权与安全停止回归（agent 侧）。

覆盖：
  - Web 持有设备租约时，agent 运动请求不落地，返回 control_occupied；
  - Web 持有租约时，agent 收到 chat/stop 返回 no_session，不向设备发零速；
  - agent 自己运动时收到 chat/stop 下发 3 次零速并释放租约。
"""

import json
import threading
import time

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
    """记录 publish，并对 control/acquire|release 立即回包。"""

    def __init__(self, agent):
        self.agent = agent
        self.published = []
        self.acquire_response = {
            "result": 0,
            "action": "granted",
            "control_holder": {
                "client_id": "agent-test",
                "client_name": "Test Agent",
            },
        }
        self.release_response = {
            "result": 0,
            "action": "released",
            "control_holder": None,
        }

    def publish(self, topic, payload, qos=0, retain=False):
        body = json.loads(payload) if isinstance(payload, str) else payload
        self.published.append({
            "topic": topic, "payload": body, "qos": qos, "retain": retain,
        })
        if topic.endswith("/control/acquire/request"):
            response = dict(self.acquire_response)
            response["request_id"] = body.get("request_id")
            self.agent._on_control_response(response)
        elif topic.endswith("/control/release/request"):
            response = dict(self.release_response)
            response["request_id"] = body.get("request_id")
            self.agent._on_control_response(response)
        return _PublishInfo()


def _motor_requests(client):
    return [
        item for item in client.published
        if item["topic"].endswith("/car/motor/set/request")
    ]


def _is_zero(payload):
    move = payload.get("move") or {}
    rotate = payload.get("rotate") or {}
    return (
        all(float(move.get(key, 0)) == 0.0
            for key in ("forward", "back", "left", "right"))
        and not rotate.get("left")
        and not rotate.get("right")
    )


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _start_real_motion(agent, distance_m=20.0, device="dev-1"):
    result = agent.execute(
        device,
        {"action": "move", "direction": "forward",
         "speed_mps": 0.5, "distance_m": distance_m},
        "Web A")
    assert result["ok"] is True
    assert _wait_for(
        lambda: any(not _is_zero(item["payload"])
                    for item in _motor_requests(agent.mqtt_client)))
    return device


def _zero_requests(agent):
    return [
        item for item in _motor_requests(agent.mqtt_client)
        if _is_zero(item["payload"])
    ]


def _release_requests(agent):
    return [
        item for item in agent.mqtt_client.published
        if item["topic"].endswith("/control/release/request")
    ]


@pytest.fixture
def agent(monkeypatch):
    monkeypatch.setattr(car_agent, "log", lambda: _SilentLogger())
    monkeypatch.setattr(car_agent, "server_tenant", lambda: "test")
    monkeypatch.setattr(car_agent, "agent_identity", lambda: "agent-test")
    monkeypatch.setattr(car_agent, "agent_name", lambda: "Test Agent")

    cfg = dict(car_agent.DEFAULTS)
    cfg.update({
        "MQTT_USER": "",
        "MQTT_PASSWORD": "",
        "TENANT": "test",
    })
    instance = car_agent.Agent(cfg)
    instance.mqtt_client = FakeMqttClient(instance)
    instance.mqtt_connected = True
    instance.mqtt_configured = True
    return instance


def test_web_holder_agent_move_is_control_occupied(agent):
    """Web 持有租约：agent 的运动请求被设备拒绝，不产生 /cmd_vel 请求。"""
    agent.mqtt_client.acquire_response = {
        "result": 1,
        "action": "occupied",
        "control_holder": {"client_id": "web-a", "client_name": "Web A"},
    }

    result = agent.execute(
        "dev-1",
        {"action": "move", "direction": "forward",
         "speed_mps": 0.5, "distance_m": 0.5},
        "Web A")

    assert result["ok"] is False
    assert result["accepted"] is False
    assert result["error"] == "control_occupied"
    assert result["control_holder"]["client_id"] == "web-a"
    assert _motor_requests(agent.mqtt_client) == []


def test_web_holder_agent_chat_stop_is_no_session_without_zero(agent):
    """Web 持有租约时 agent 没有自己的会话：chat/stop 返回 no_session。"""
    agent.mqtt_client.acquire_response = {
        "result": 1,
        "action": "occupied",
        "control_holder": {"client_id": "web-a", "client_name": "Web A"},
    }

    agent._serve_agent_stop({
        "request_id": "stop-1",
        "client_id": "web-a",
        "client_name": "Web A",
        "device": "dev-1",
    })

    assert _motor_requests(agent.mqtt_client) == []
    assert not [
        item for item in agent.mqtt_client.published
        if item["topic"].endswith("/control/release/request")
    ]
    responses = [
        item for item in agent.mqtt_client.published
        if item["topic"].endswith("/chat/stop/response")
    ]
    assert len(responses) == 1
    payload = responses[0]["payload"]
    assert payload["result"] == 0
    assert payload["cause"] == "no_session"
    assert payload["stopped"] is False
    assert payload["released"] is False


def test_agent_own_motion_chat_stop_sends_zero_and_releases(agent):
    """agent 自己的运动被 chat/stop 中止：3 次零速 + 释放租约。"""
    device = "dev-1"
    agent.control_held = {
        "device": device,
        "client_id": "agent-test",
        "control_holder": {"client_id": "agent-test"},
    }
    started = threading.Event()

    def fake_worker():
        started.set()
        agent.abort.wait(5)

    worker = threading.Thread(target=fake_worker, daemon=True)
    worker.start()
    assert started.wait(1)
    with agent.state_lock:
        agent.worker = worker
        agent.active_device = device

    agent._serve_agent_stop({
        "request_id": "stop-2",
        "client_id": "web-a",
        "client_name": "Web A",
        "device": device,
    })
    worker.join(timeout=2)
    assert not worker.is_alive()

    motor_requests = _motor_requests(agent.mqtt_client)
    assert len(motor_requests) == 3
    assert all(_is_zero(item["payload"]) for item in motor_requests)

    release_requests = [
        item for item in agent.mqtt_client.published
        if item["topic"].endswith("/control/release/request")
    ]
    assert len(release_requests) == 1
    assert agent.control_held is None

    responses = [
        item for item in agent.mqtt_client.published
        if item["topic"].endswith("/chat/stop/response")
    ]
    assert len(responses) == 1
    payload = responses[0]["payload"]
    assert payload["result"] == 0
    assert payload["stopped"] is True
    assert payload["released"] is True


def test_real_motion_chat_stop_sends_only_one_zero_burst(agent):
    """真实 _run_motion 被 chat/stop 中止时，清理由 stop() 接管，只有 3 帧零速。"""
    device = _start_real_motion(agent)

    agent._serve_agent_stop({
        "request_id": "stop-real",
        "client_id": "web-a",
        "client_name": "Web A",
        "device": device,
    })

    zero_requests = _zero_requests(agent)
    assert len(zero_requests) == 3
    assert all(
        str(item["payload"].get("request_id", "")).startswith("agent-stop-")
        for item in zero_requests
    )
    # 运动线程不再补发自己的一组零速
    assert not [
        item for item in zero_requests
        if str(item["payload"].get("request_id", "")).startswith("agent-move-")
    ]
    assert len(_release_requests(agent)) == 1
    assert agent.control_held is None

    responses = [
        item for item in agent.mqtt_client.published
        if item["topic"].endswith("/chat/stop/response")
    ]
    assert responses[-1]["payload"]["stopped"] is True
    assert responses[-1]["payload"]["released"] is True


def test_real_motion_normal_completion_still_zeroes_and_releases(agent):
    """正常跑完（非 chat/stop）仍由 _run_motion.finally 发零速并释放租约。"""
    device = _start_real_motion(agent, distance_m=0.01)
    worker = agent.worker
    assert worker is not None
    worker.join(timeout=3.0)
    assert not worker.is_alive()

    zero_requests = _zero_requests(agent)
    assert len(zero_requests) == 3
    assert all(
        str(item["payload"].get("request_id", "")).startswith("agent-move-")
        for item in zero_requests
    )
    assert len(_release_requests(agent)) == 1
    assert agent.control_held is None


def test_real_motion_motor_error_still_worker_cleanup(agent):
    """-8 等设备拒绝导致的中止不走 stop()，仍由运动线程发零速并释放租约。"""
    device = _start_real_motion(agent)
    with agent.state_lock:
        request_id = agent.motion_rid
    assert request_id

    agent._on_motor_response(
        device,
        {"request_id": request_id, "result": -8, "cause": "前方有障碍物"},
        {"error": "safety", "result": -8, "cause": "前方有障碍物"})

    worker = agent.worker
    assert worker is not None
    worker.join(timeout=3.0)
    assert not worker.is_alive()
    assert agent.last_motion_error["result"] == -8

    zero_requests = _zero_requests(agent)
    assert len(zero_requests) == 3
    assert all(item["payload"].get("request_id") == request_id
               for item in zero_requests)
    assert len(_release_requests(agent)) == 1
    assert agent.control_held is None
