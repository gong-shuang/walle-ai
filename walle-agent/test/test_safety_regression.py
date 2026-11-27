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
from types import SimpleNamespace

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

    def _respond(self, topic, body, response):
        response = dict(response)
        response["request_id"] = body.get("request_id")
        response_topic = topic.rsplit("/", 1)[0] + "/response"
        device = topic.split("/")[3]
        self.agent.request_bus.on_message(device, response_topic, response)

    def publish(self, topic, payload, qos=0, retain=False):
        body = json.loads(payload) if isinstance(payload, str) else payload
        self.published.append({
            "topic": topic, "payload": body, "qos": qos, "retain": retain,
        })
        if topic.endswith("/control/acquire/request"):
            self._respond(topic, body, self.acquire_response)
        elif topic.endswith("/control/release/request"):
            self._respond(topic, body, self.release_response)
        return _PublishInfo()


def _motor_requests(client):
    return [
        item for item in client.published
        if item["topic"].endswith("/car/motor/set/request")
    ]


def _motor_requests_for(agent, device):
    return [
        item for item in _motor_requests(agent.mqtt_client)
        if "/device/{}/".format(device) in item["topic"]
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
        device: {
            "device": device,
            "client_id": "agent-test",
            "control_holder": {"client_id": "agent-test"},
        }
    }
    started = threading.Event()
    session = car_agent.MotionSession(
        device, {"action": "move", "direction": "forward"}, "agent-test")
    session.request_id = "agent-move-test"

    def fake_worker():
        started.set()
        session.abort.wait(5)

    worker = threading.Thread(target=fake_worker, daemon=True)
    session.thread = worker
    worker.start()
    assert started.wait(1)
    with agent.state_lock:
        agent.sessions[device] = session

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
    assert agent.control_held == {}

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
    assert agent.control_held == {}

    responses = [
        item for item in agent.mqtt_client.published
        if item["topic"].endswith("/chat/stop/response")
    ]
    assert responses[-1]["payload"]["stopped"] is True
    assert responses[-1]["payload"]["released"] is True


def test_real_motion_normal_completion_still_zeroes_and_releases(agent):
    """正常跑完（非 chat/stop）仍由 _run_motion.finally 发零速并释放租约。"""
    device = _start_real_motion(agent, distance_m=0.01)
    session = agent.sessions.get(device)
    assert session is not None
    worker = session.thread
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
    assert agent.control_held == {}


def test_real_motion_motor_error_still_worker_cleanup(agent):
    """-8 等设备拒绝导致的中止不走 stop()，仍由运动线程发零速并释放租约。"""
    device = _start_real_motion(agent)
    session = agent.sessions.get(device)
    assert session is not None
    request_id = session.request_id
    assert request_id

    agent._on_motor_response(
        device,
        {"request_id": request_id, "result": -8, "cause": "前方有障碍物"},
        {"error": "safety", "result": -8, "cause": "前方有障碍物"})

    worker = session.thread
    assert worker is not None
    worker.join(timeout=3.0)
    assert not worker.is_alive()
    assert agent.last_motion_error["result"] == -8

    zero_requests = _zero_requests(agent)
    assert len(zero_requests) == 3
    assert all(item["payload"].get("request_id") == request_id
               for item in zero_requests)
    assert len(_release_requests(agent)) == 1
    assert agent.control_held == {}


@pytest.mark.parametrize("result,error,cause,holder", [
    (-5, "control_denied", "控制权被占用",
     {"client_id": "web-1", "client_name": "Web A"}),
    (-3, "unavailable", "底盘控制链路不可用", None),
])
def test_real_motion_motor_denied_or_unavailable_still_cleanup(
        agent, result, error, cause, holder):
    """-5/-3 与 -8 同一分支：中止运动、补 3 次零速、释放租约。"""
    device = _start_real_motion(agent)
    session = agent.sessions.get(device)
    assert session is not None
    request_id = session.request_id
    assert request_id

    payload = {"request_id": request_id, "result": result, "cause": cause}
    decoded = {"error": error, "result": result, "cause": cause}
    if holder:
        payload["control_holder"] = holder
        decoded["holder"] = holder
    agent._on_motor_response(device, payload, decoded)

    worker = session.thread
    assert worker is not None
    worker.join(timeout=3.0)
    assert not worker.is_alive()
    assert agent.last_motion_error["result"] == result
    assert agent.last_motion_error["error"] == error

    zero_requests = _zero_requests(agent)
    assert len(zero_requests) == 3
    assert all(item["payload"].get("request_id") == request_id
               for item in zero_requests)
    assert len(_release_requests(agent)) == 1
    assert agent.control_held == {}


def _start_two_motions(agent):
    for device, client_name in (("dev-1", "Web A"), ("dev-2", "Web B")):
        result = agent.execute(
            device,
            {"action": "move", "direction": "forward",
             "speed_mps": 0.5, "distance_m": 20.0},
            client_name)
        assert result["ok"] is True, result


def test_two_devices_move_and_stop_independently(agent):
    _start_two_motions(agent)

    assert set(agent.sessions) == {"dev-1", "dev-2"}
    assert set(agent.control_held) == {"dev-1", "dev-2"}
    assert _wait_for(
        lambda: _motor_requests_for(agent, "dev-1")
        and _motor_requests_for(agent, "dev-2"))
    health = agent.health()
    assert set(health["active_devices"]) == {"dev-1", "dev-2"}
    assert set(health["control_devices"]) == {"dev-1", "dev-2"}

    stop1 = agent.stop("Web A", "dev-1")
    assert stop1["stopped"] is True
    assert "dev-1" not in agent.sessions
    assert "dev-2" in agent.sessions
    assert set(agent.control_held) == {"dev-2"}

    stop2 = agent.stop("Web B", "dev-2")
    assert stop2["stopped"] is True
    assert agent.sessions == {}
    assert agent.control_held == {}


def test_chat_stop_by_device_keeps_other_motion(agent):
    _start_two_motions(agent)

    agent._serve_agent_stop({
        "request_id": "stop-dev-1",
        "client_id": "web-a",
        "client_name": "Web A",
        "device": "dev-1",
    })

    assert "dev-1" not in agent.sessions
    assert "dev-2" in agent.sessions
    assert set(agent.control_held) == {"dev-2"}
    responses = [
        item for item in agent.mqtt_client.published
        if item["topic"].endswith("/chat/stop/response")
    ]
    payload = responses[-1]["payload"]
    assert payload["result"] == 0
    assert payload["device"] == "dev-1"
    assert payload["stopped"] is True
    assert payload["released"] is True

    agent._serve_agent_stop({
        "request_id": "stop-all",
        "client_id": "web-a",
        "client_name": "Web A",
    })
    assert agent.sessions == {}
    assert agent.control_held == {}


def test_camera_resource_lock_is_per_device(agent):
    lock1 = agent._acquire_resource("dev-1", "camera")
    assert lock1 is not None
    try:
        blocked = agent.record_start("dev-1")
        assert blocked["ok"] is False
        assert blocked["error"] == "resource_busy"
        assert "dev-1" not in agent.control_held

        results = []
        worker = threading.Thread(
            target=lambda: results.append(agent.record_start("dev-2")),
            daemon=True)
        worker.start()
        assert _wait_for(lambda: any(
            "/device/dev-2/record/start/request" in item["topic"]
            for item in agent.mqtt_client.published))
        request = [
            item for item in agent.mqtt_client.published
            if "/device/dev-2/record/start/request" in item["topic"]
        ][-1]["payload"]
        agent._on_message(None, None, SimpleNamespace(
            topic="walle/test/device/dev-2/record/start/response",
            qos=1, retain=False,
            payload=json.dumps({
                "request_id": request["request_id"],
                "result": 0, "cause": "ok", "filename": "dev2.fmp4",
            }).encode("utf-8")))
        worker.join(timeout=2)
        assert results[0]["ok"] is True
        assert results[0]["filename"] == "dev2.fmp4"
        assert "dev-2" not in agent.control_held
    finally:
        agent._release_resource(lock1)
