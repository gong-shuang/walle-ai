#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M4-02：工具 schema 与模型输出校验测试。"""

import json

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


def _llm_config():
    cfg = dict(car_agent.DEFAULTS)
    cfg.update({
        "LLM_ENABLED": "1", "LLM_BASE_URL": "http://llm",
        "LLM_API_KEY": "key", "LLM_MODEL": "model",
    })
    return cfg


def _mock_llm(monkeypatch, content):
    monkeypatch.setattr(car_agent, "log", lambda: _SilentLogger())
    monkeypatch.setattr(car_agent, "load_config", _llm_config)
    monkeypatch.setattr(
        car_agent.urlrequest, "urlopen",
        lambda req, timeout=8: _FakeResponse({
            "choices": [{"message": {"content": json.dumps(content)}}]}))


def test_llm_parse_returns_validated_intent(monkeypatch):
    _mock_llm(monkeypatch, {"action": "move", "direction": "forward",
                            "distance_m": 3})

    assert car_agent._llm_parse("往前走三米") == {
        "action": "move", "direction": "forward", "distance_m": 3}


def test_llm_parse_rejects_invalid_schema(monkeypatch):
    _mock_llm(monkeypatch, {"action": "open_door"})
    assert car_agent._llm_parse("开个门") is None

    _mock_llm(monkeypatch, {"action": "move", "direction": "up",
                            "distance_m": 3})
    assert car_agent._llm_parse("往上走三米") is None
