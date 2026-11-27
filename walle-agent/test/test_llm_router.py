#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M4-02/M4-03：工具 schema、校验与本地模型客户端测试。"""

import io
import json
from urllib import error as urlerror

import pytest

import car_agent
import llm_router
import task_schedule

EXPECTED_TOOLS = {
    "move", "rotate", "stop",
    "status", "device_base_state", "device_run_state", "device_discover",
    "agent_status", "service_health", "alarm_status",
    "record_start", "record_stop", "record_list", "record_delete",
    "record_play_start", "record_play_stop",
    "stream_start", "stream_stop",
    "servo_status", "servo_set", "servo_center", "servo_relative",
    "task_list", "task_create", "task_cancel", "task_pause", "task_resume",
}


@pytest.fixture(autouse=True)
def _reset_llm_circuit():
    llm_router.reset_circuit()
    yield
    llm_router.reset_circuit()


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


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_tools_cover_all_chat_actions():
    assert set(llm_router.TOOLS) == EXPECTED_TOOLS


def test_every_tool_has_description_and_schema():
    for name, tool in llm_router.TOOLS.items():
        assert tool["description"], name
        params = tool["parameters"]
        assert params["type"] == "object", name
        assert params["additionalProperties"] is False, name
        for key, spec in params["properties"].items():
            assert spec.get("type") or spec.get("anyOf"), (name, key)
            if "enum" in spec:
                assert spec["enum"], (name, key)
    schemas = llm_router.tool_schemas()
    assert {item["function"]["name"] for item in schemas} == EXPECTED_TOOLS
    for item in schemas:
        assert item["type"] == "function"
        assert item["function"]["parameters"]["type"] == "object"


def test_schedule_types_match_task_schedule():
    schedule = llm_router.TOOLS["task_create"]["parameters"]["properties"][
        "schedule"]
    assert set(schedule["properties"]["type"]["enum"]) == set(
        task_schedule.SCHEDULE_TYPES)


def test_validate_move_and_rotate():
    assert llm_router.validate_tool_call(
        "move", {"direction": "forward", "distance_m": 3}) == {
            "action": "move", "direction": "forward", "distance_m": 3}
    assert llm_router.validate_tool_call(
        "rotate", {"direction": "left", "angle_deg": 90}) == {
            "action": "rotate", "direction": "left", "angle_deg": 90}


def test_validate_rejects_bad_tool_or_args():
    with pytest.raises(llm_router.SchemaError):
        llm_router.validate_tool_call("open_door", {})
    with pytest.raises(llm_router.SchemaError):
        llm_router.validate_tool_call(
            "move", {"direction": "up", "distance_m": 3})
    with pytest.raises(llm_router.SchemaError):
        llm_router.validate_tool_call("move", {"direction": "forward"})
    with pytest.raises(llm_router.SchemaError):
        llm_router.validate_tool_call(
            "move", {"direction": "forward", "distance_m": "3"})
    with pytest.raises(llm_router.SchemaError):
        llm_router.validate_tool_call(
            "move", {"direction": "forward", "distance_m": 3,
                     "topic": "walle/x"})


def test_validate_record_target():
    assert llm_router.validate_tool_call(
        "record_delete", {"latest": True})["latest"] is True
    assert llm_router.validate_tool_call(
        "record_delete", {"filename": "a.mp4"})["filename"] == "a.mp4"
    with pytest.raises(llm_router.SchemaError):
        llm_router.validate_tool_call("record_delete", {})
    with pytest.raises(llm_router.SchemaError):
        llm_router.validate_tool_call("record_play_start", {})


def test_validate_nested_servo_and_task():
    servo = {"servos": [{"id": 1, "position": 1500}], "duration": 1.0}
    assert llm_router.validate_tool_call("servo_set", servo)["servos"]
    with pytest.raises(llm_router.SchemaError):
        llm_router.validate_tool_call(
            "servo_set", {"servos": [{"id": 9, "position": 1500}]})

    task = {
        "name": "每天录像",
        "schedule": {"type": "daily_window", "start": "08:00",
                     "stop": "21:00"},
        "actions": [{"phase": "start", "tool": "record.start"},
                    {"phase": "stop", "tool": "record.stop"}],
    }
    out = llm_router.validate_tool_call("task_create", task)
    assert out["action"] == "task_create"
    assert out["schedule"]["type"] == "daily_window"

    with pytest.raises(llm_router.SchemaError):
        llm_router.validate_tool_call(
            "task_create", {"schedule": {"type": "monthly"},
                            "actions": [{"phase": "start",
                                         "tool": "record.start"}]})
    with pytest.raises(llm_router.SchemaError):
        llm_router.validate_tool_call(
            "task_create", {"schedule": {"type": "once",
                                         "at": "2030-01-01 08:00:00"}})
    with pytest.raises(llm_router.SchemaError):
        llm_router.validate_tool_call("task_cancel", {})


def test_validate_intent_accepts_function_call_shape():
    payload = {
        "function": {
            "name": "move",
            "arguments": json.dumps({"direction": "forward",
                                     "distance_m": 3}),
        }
    }
    intent, error = llm_router.validate_intent(payload)
    assert error is None
    assert intent == {"action": "move", "direction": "forward",
                      "distance_m": 3}


def test_validate_intent_rejects_topic_payload_and_unknown():
    intent, error = llm_router.validate_intent(
        {"action": "record_start", "payload": {"x": 1}})
    assert intent is None
    assert "topic/payload" in error

    intent, error = llm_router.validate_intent({"action": "open_door"})
    assert intent is None
    assert "不支持的工具" in error

    intent, error = llm_router.validate_intent(
        {"action": "move", "direction": "up", "distance_m": 3})
    assert intent is None
    assert "direction" in error


def test_validate_intent_accepts_explicit_unknown():
    intent, error = llm_router.validate_intent({"action": "unknown"})
    assert error is None
    assert intent == {"action": "unknown"}


def _llm_config():
    cfg = dict(car_agent.DEFAULTS)
    cfg.update({
        "LLM_ENABLED": "1",
        "LLM_LOCAL_BASE_URL": "http://llm/v1",
        "LLM_LOCAL_MODEL": "qwen",
        "LLM_TIMEOUT_SEC": "1",
        "LLM_RETRY_BACKOFF_SEC": "0",
    })
    return cfg


def _mock_llm(monkeypatch, content):
    monkeypatch.setattr(car_agent, "log", lambda: _SilentLogger())
    monkeypatch.setattr(car_agent, "load_config", _llm_config)
    monkeypatch.setattr(
        llm_router.urlrequest, "urlopen",
        lambda req, timeout=8: _FakeResponse({
            "choices": [{"message": {"content": json.dumps(content)}}]}))


def test_llm_parse_returns_validated_intent(monkeypatch):
    _mock_llm(monkeypatch, {"action": "move", "direction": "forward",
                            "distance_m": 3})

    assert car_agent._llm_parse("往前走三米") == {
        "action": "move", "direction": "forward", "distance_m": 3}


def test_llm_parse_returns_unknown(monkeypatch):
    _mock_llm(monkeypatch, {"action": "unknown"})
    assert car_agent._llm_parse("讲个笑话") == {"action": "unknown"}


def test_llm_parse_rejects_invalid_schema(monkeypatch):
    _mock_llm(monkeypatch, {"action": "open_door"})
    assert car_agent._llm_parse("开个门") is None

    _mock_llm(monkeypatch, {"action": "move", "direction": "up",
                            "distance_m": 3})
    assert car_agent._llm_parse("往上走三米") is None


# ---------------- M4-03 本地模型客户端 ----------------

def test_local_config_falls_back_and_has_no_key(monkeypatch):
    cfg = _llm_config()
    cfg["LLM_LOCAL_BASE_URL"] = ""
    cfg["LLM_BASE_URL"] = "http://ollama:11434/v1"
    cfg["LLM_LOCAL_MODEL"] = ""
    cfg["LLM_MODEL"] = "qwen2.5"
    cfg["LLM_LOCAL_API_KEY"] = ""
    cfg["LLM_API_KEY"] = ""

    conf = llm_router.local_config(cfg)
    assert conf["base_url"] == "http://ollama:11434/v1"
    assert conf["model"] == "qwen2.5"
    assert conf["api_key"] == ""
    assert conf["timeout"] == 1.0


def test_parse_local_accepts_tool_calls(monkeypatch):
    cfg = _llm_config()
    calls = []

    def fake(req, timeout=8):
        body = json.loads(req.data.decode("utf-8"))
        calls.append(body)
        assert "Authorization" not in req.headers
        return _FakeResponse({"choices": [{"message": {"tool_calls": [{
            "function": {
                "name": "record_start",
                "arguments": json.dumps({"segment_sec": 0}),
            }
        }]}}]})

    monkeypatch.setattr(llm_router.urlrequest, "urlopen", fake)

    intent, error = llm_router.parse_local("开始录像", cfg)

    assert error is None
    assert intent == {"action": "record_start", "segment_sec": 0}
    assert "tools" in calls[0]


def test_parse_local_falls_back_to_json_on_tools_400(monkeypatch):
    cfg = _llm_config()
    calls = []

    def fake(req, timeout=8):
        body = json.loads(req.data.decode("utf-8"))
        calls.append(body)
        if "tools" in body:
            raise urlerror.HTTPError(
                req.full_url, 400, "Bad Request", {},
                io.BytesIO(b'{"error":"tools not supported"}'))
        return _FakeResponse({"choices": [{"message": {
            "content": json.dumps({"action": "stop"})}}]})

    monkeypatch.setattr(llm_router.urlrequest, "urlopen", fake)

    intent, error = llm_router.parse_local("停", cfg)

    assert error is None
    assert intent == {"action": "stop"}
    assert "tools" in calls[0]
    assert "response_format" in calls[1]


def test_parse_local_retries_network_error(monkeypatch):
    cfg = _llm_config()
    calls = {"count": 0}

    def fake(req, timeout=8):
        calls["count"] += 1
        if calls["count"] == 1:
            raise urlerror.URLError("boom")
        return _FakeResponse({"choices": [{"message": {
            "content": json.dumps({"action": "stop"})}}]})

    monkeypatch.setattr(llm_router.urlrequest, "urlopen", fake)

    intent, error = llm_router.parse_local("停", cfg)

    assert error is None
    assert intent == {"action": "stop"}
    assert calls["count"] == 2


def test_parse_local_retries_json_without_response_format(monkeypatch):
    cfg = _llm_config()

    def fake(req, timeout=8):
        body = json.loads(req.data.decode("utf-8"))
        if "tools" in body:
            raise urlerror.HTTPError(
                req.full_url, 400, "Bad Request", {},
                io.BytesIO(b'{"error":"tools not supported"}'))
        if "response_format" in body:
            raise urlerror.HTTPError(
                req.full_url, 400, "Bad Request", {},
                io.BytesIO(b'{"error":"response_format not supported"}'))
        return _FakeResponse({"choices": [{"message": {
            "content": json.dumps({"action": "stop"})}}]})

    monkeypatch.setattr(llm_router.urlrequest, "urlopen", fake)

    intent, error = llm_router.parse_local("停", cfg)

    assert error is None
    assert intent == {"action": "stop"}


def test_parse_local_rejects_invalid_tool_call(monkeypatch):
    cfg = _llm_config()

    def fake(req, timeout=8):
        return _FakeResponse({"choices": [{"message": {"tool_calls": [{
            "function": {
                "name": "move",
                "arguments": json.dumps({"direction": "up",
                                         "distance_m": 3}),
            }
        }]}}]})

    monkeypatch.setattr(llm_router.urlrequest, "urlopen", fake)

    intent, error = llm_router.parse_local("往上走", cfg)

    assert intent is None
    assert "direction" in error


def test_parse_local_disabled_or_unconfigured():
    cfg = _llm_config()
    cfg["LLM_ENABLED"] = "0"
    assert llm_router.parse_local("停", cfg) == (None, "llm_disabled")

    cfg = _llm_config()
    cfg["LLM_LOCAL_BASE_URL"] = ""
    cfg["LLM_BASE_URL"] = ""
    assert llm_router.parse_local("停", cfg) == (None, "llm_not_configured")

    cfg = _llm_config()
    cfg["LLM_LOCAL_MODEL"] = ""
    cfg["LLM_MODEL"] = ""
    assert llm_router.parse_local("停", cfg) == (None, "llm_not_configured")


# ---------------- M4-04 在线模型客户端 ----------------

def _online_config():
    cfg = _llm_config()
    cfg.update({
        "LLM_ONLINE_BASE_URL": "https://api.example.com/v1",
        "LLM_ONLINE_API_KEY": "secret-key",
        "LLM_ONLINE_MODEL": "deepseek-chat",
        "LLM_CIRCUIT_FAILS": "2",
        "LLM_CIRCUIT_COOLDOWN_SEC": "60",
    })
    return cfg


def test_parse_online_tools_success(monkeypatch):
    cfg = _online_config()
    calls = []

    def fake(req, timeout=8):
        body = json.loads(req.data.decode("utf-8"))
        calls.append(body)
        assert req.headers.get("Authorization") == "Bearer secret-key"
        return _FakeResponse({"choices": [{"message": {"tool_calls": [{
            "function": {"name": "stop", "arguments": "{}"}}]}}]})

    monkeypatch.setattr(llm_router.urlrequest, "urlopen", fake)

    intent, error = llm_router.parse_online("停", cfg)

    assert error is None
    assert intent == {"action": "stop"}
    assert "tools" in calls[0]


def test_parse_online_requires_api_key():
    cfg = _online_config()
    cfg["LLM_ONLINE_API_KEY"] = ""
    cfg["LLM_API_KEY"] = ""
    assert llm_router.parse_online("停", cfg) == (
        None, "llm_online_not_configured")


def test_parse_online_circuit_opens_and_recovers(monkeypatch):
    cfg = _online_config()
    cfg["LLM_CIRCUIT_FAILS"] = "1"
    cfg["LLM_RETRY_MAX"] = "0"
    calls = {"fail": 0, "ok": 0}
    now = {"value": 1000.0}

    def offline(req, timeout=8):
        calls["fail"] += 1
        raise urlerror.URLError("offline")

    def online(req, timeout=8):
        calls["ok"] += 1
        return _FakeResponse({"choices": [{"message": {
            "content": json.dumps({"action": "stop"})}}]})

    monkeypatch.setattr(llm_router.urlrequest, "urlopen", offline)
    monkeypatch.setattr(llm_router, "_now", lambda: now["value"])

    intent, error = llm_router.parse_online("复杂的话", cfg)
    assert intent is None
    assert calls["fail"] == 1        # 传输失败后不再尝试 JSON 模式
    assert error

    intent, error = llm_router.parse_online("复杂的话", cfg)
    assert intent is None
    assert error == "llm_online_circuit_open"
    assert calls["fail"] == 1        # 熔断期间不发请求

    now["value"] += 61
    monkeypatch.setattr(llm_router.urlrequest, "urlopen", online)
    intent, error = llm_router.parse_online("停", cfg)

    assert error is None
    assert intent == {"action": "stop"}
    assert calls["ok"] == 1          # 冷却后恢复真实请求


def test_parse_online_does_not_log_api_key(monkeypatch):
    cfg = _online_config()
    cfg["LLM_RETRY_MAX"] = "1"
    cfg["LLM_RETRY_BACKOFF_SEC"] = "0"
    messages = []

    class Recorder:
        def __getattr__(self, name):
            def record(*args, **kwargs):
                messages.append("{} {}".format(args, kwargs))
            return record

    def offline(req, timeout=8):
        raise urlerror.URLError("offline")

    monkeypatch.setattr(llm_router, "LOG", Recorder())
    monkeypatch.setattr(llm_router.urlrequest, "urlopen", offline)

    llm_router.parse_online("复杂的话", cfg)

    assert messages
    assert "secret-key" not in "\n".join(messages)
