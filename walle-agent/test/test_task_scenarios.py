#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M3-10：调度端到端场景测试（可注入时钟，不等待真实时间）。"""

import json
from datetime import datetime

import pytest

import car_agent
import task_schedule
from task_scheduler import TaskScheduler


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
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, qos=0, retain=False):
        try:
            body = json.loads(payload) if isinstance(payload, str) else payload
        except (TypeError, ValueError):
            body = payload
        self.published.append({"topic": topic, "payload": body})
        return _PublishInfo()


class FakeClock:
    """可手动拨动的假时钟。"""

    def __init__(self, text):
        self.value = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")

    def __call__(self):
        return self.value

    def set(self, text):
        self.value = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")


@pytest.fixture
def agent(monkeypatch, tmp_path):
    monkeypatch.setattr(car_agent, "log", lambda: _SilentLogger())
    monkeypatch.setattr(car_agent, "server_tenant", lambda: "test")
    monkeypatch.setattr(car_agent, "agent_identity", lambda: "agent-test")
    monkeypatch.setattr(car_agent, "agent_name", lambda: "Test Agent")

    cfg = dict(car_agent.DEFAULTS)
    cfg.update({
        "MQTT_USER": "", "MQTT_PASSWORD": "", "TENANT": "test",
        "TASK_DB_PATH": str(tmp_path / "agent.db"),
        "TASK_RETRY_MAX": "0", "TASK_RETRY_BACKOFF_SEC": "0",
    })
    instance = car_agent.Agent(cfg)
    instance.mqtt_client = FakeMqttClient()
    instance.mqtt_connected = True
    instance.mqtt_configured = True
    return instance


@pytest.fixture
def clock(monkeypatch):
    clock = FakeClock("2026-11-27 07:59:00")
    real_advance = task_schedule.advance_task

    def advance_with_clock(store, task, now=None):
        return real_advance(store, task, now=clock())

    monkeypatch.setattr(car_agent.task_schedule, "advance_task",
                        advance_with_clock)
    return clock


def _scheduler(agent, clock):
    """真实 TaskScheduler + 真实 _run_task，时钟由测试控制。"""
    return TaskScheduler(
        agent.task_db(), lambda task, slot: agent._run_task(task, slot),
        now=clock)


def _window_task(store, start="08:00", stop="21:00",
                 next_run_at="2026-11-27 08:00:00"):
    return store.create_task(
        "录像窗口", "dev-1",
        {"type": "daily_window", "start": start, "stop": stop},
        [{"phase": "start", "tool": "record.start"},
         {"phase": "stop", "tool": "record.stop"}],
        next_run_at=next_run_at)


def _success(action):
    def call(device):
        return {"ok": True, "action": action, "result": 0, "cause": "ok"}

    return call


def test_daily_window_start_stop_on_fake_clock(agent, clock, monkeypatch):
    store = agent.task_db()
    task = _window_task(store)
    starts, stops = [], []
    monkeypatch.setattr(agent, "record_start",
                        lambda device: starts.append(device) or {
                            "ok": True, "action": "record_start", "result": 0,
                            "cause": "ok"})
    monkeypatch.setattr(agent, "record_stop",
                        lambda device: stops.append(device) or {
                            "ok": True, "action": "record_stop", "result": 0,
                            "cause": "ok"})
    scheduler = _scheduler(agent, clock)

    assert scheduler.tick() == []          # 08:00 之前不触发

    clock.set("2026-11-27 08:00:01")
    assert scheduler.tick() == [task["id"]]
    assert starts == ["dev-1"]
    assert store.get_task(task["id"])["next_run_at"] == (
        "2026-11-27 21:00:00")
    assert scheduler.tick() == []          # 同一 slot 不重复

    clock.set("2026-11-27 21:00:01")
    assert scheduler.tick() == [task["id"]]
    assert stops == ["dev-1"]
    assert store.get_task(task["id"])["next_run_at"] == (
        "2026-11-28 08:00:00")


def test_once_task_runs_once_and_disables(agent, clock, monkeypatch):
    store = agent.task_db()
    task = store.create_task(
        "一次性录像", "dev-1",
        {"type": "once", "at": "2026-11-27 09:00:00"},
        [{"phase": "start", "tool": "record.start"}],
        next_run_at="2026-11-27 09:00:00")
    calls = []
    monkeypatch.setattr(agent, "record_start",
                        lambda device: calls.append(device) or {
                            "ok": True, "action": "record_start", "result": 0,
                            "cause": "ok"})
    scheduler = _scheduler(agent, clock)

    clock.set("2026-11-27 09:00:01")
    assert scheduler.tick() == [task["id"]]

    fresh = store.get_task(task["id"])
    assert calls == ["dev-1"]
    assert fresh["enabled"] is False
    assert fresh["next_run_at"] is None
    assert scheduler.tick() == []


def test_interval_task_advances_from_anchor(agent, clock, monkeypatch):
    store = agent.task_db()
    task = store.create_task(
        "间隔录像", "dev-1", {"type": "interval", "seconds": 7200},
        [{"phase": "start", "tool": "record.start"}],
        next_run_at="2026-11-27 08:00:00")
    calls = []
    monkeypatch.setattr(agent, "record_start",
                        lambda device: calls.append(device) or {
                            "ok": True, "action": "record_start", "result": 0,
                            "cause": "ok"})
    scheduler = _scheduler(agent, clock)

    clock.set("2026-11-27 08:00:01")
    assert scheduler.tick() == [task["id"]]
    assert store.get_task(task["id"])["next_run_at"] == (
        "2026-11-27 10:00:00")

    clock.set("2026-11-27 10:00:01")
    assert scheduler.tick() == [task["id"]]
    assert len(calls) == 2
    assert store.get_task(task["id"])["next_run_at"] == (
        "2026-11-27 12:00:00")


def test_cron_task_runs_next_day(agent, clock, monkeypatch):
    store = agent.task_db()
    task = store.create_task(
        "cron 录像", "dev-1", {"type": "cron", "expr": "0 8 * * *"},
        [{"phase": "start", "tool": "record.start"}],
        next_run_at="2026-11-27 08:00:00")
    calls = []
    monkeypatch.setattr(agent, "record_start",
                        lambda device: calls.append(device) or {
                            "ok": True, "action": "record_start", "result": 0,
                            "cause": "ok"})
    scheduler = _scheduler(agent, clock)

    clock.set("2026-11-27 08:00:01")
    assert scheduler.tick() == [task["id"]]
    assert store.get_task(task["id"])["next_run_at"] == (
        "2026-11-28 08:00:00")

    clock.set("2026-11-28 08:00:01")
    assert scheduler.tick() == [task["id"]]
    assert len(calls) == 2


def test_restart_missed_slot_then_scheduler_recovers(agent, clock,
                                                     monkeypatch):
    store = agent.task_db()
    task = _window_task(store, next_run_at="2026-11-26 08:00:00")
    # 模拟进程重启：关闭连接，由 Agent 懒加载重新打开同一个库
    store.close()
    agent.task_store = None
    monkeypatch.setattr(agent, "device_run_state", lambda device: {
        "ok": True, "summary": {"recording": {"recording": False}}})
    calls = []
    monkeypatch.setattr(agent, "record_start",
                        lambda device: calls.append(device) or {
                            "ok": True, "action": "record_start", "result": 0,
                            "cause": "ok"})
    monkeypatch.setattr(agent, "record_stop", _success("record_stop"))

    out = agent.reconcile_tasks(now=clock())

    assert out["tasks"][0]["action"] == "missed"
    assert calls == []                     # 错过超过宽限，不补执行
    fresh_store = agent.task_db()
    assert fresh_store.get_task(task["id"])["next_run_at"] == (
        "2026-11-27 08:00:00")

    scheduler = _scheduler(agent, clock)
    clock.set("2026-11-27 08:00:01")
    assert scheduler.tick() == [task["id"]]
    assert calls == ["dev-1"]


def test_no_space_failure_does_not_retry_and_advances(agent, clock,
                                                      monkeypatch):
    store = agent.task_db()
    task = _window_task(store)
    calls = []

    def no_space(device):
        calls.append(device)
        return {"ok": False, "action": "record_start", "result": -7,
                "error": "no_space", "cause": "磁盘空间不足"}

    monkeypatch.setattr(agent, "record_start", no_space)
    scheduler = _scheduler(agent, clock)

    clock.set("2026-11-27 08:00:01")
    assert scheduler.tick() == [task["id"]]

    assert calls == ["dev-1"]              # 不重试
    run = store.list_runs(task["id"])[0]
    assert run["error"] == "no_space"
    assert run["result"] == -7
    assert store.get_task(task["id"])["next_run_at"] == (
        "2026-11-27 21:00:00")


def test_control_denied_skips_alarms_and_advances(agent, clock, monkeypatch):
    store = agent.task_db()
    task = _window_task(store)
    monkeypatch.setattr(agent, "record_start", lambda device: {
        "ok": False, "action": "record_start", "result": -5,
        "error": "control_denied", "cause": "控制权被占用",
        "control_holder": {"client_id": "web-1", "client_name": "Web A"}})
    scheduler = _scheduler(agent, clock)

    clock.set("2026-11-27 08:00:01")
    assert scheduler.tick() == [task["id"]]

    alarms = [item for item in agent.mqtt_client.published
              if item["topic"].endswith("/alarm/report")]
    assert len(alarms) == 1
    assert alarms[0]["payload"]["alarm_type"] == "control_occupied"
    events = [item["payload"] for item in agent.mqtt_client.published
              if item["topic"].endswith("/task/report")]
    assert [event["stage"] for event in events] == ["started", "skipped"]
    assert store.get_task(task["id"])["next_run_at"] == (
        "2026-11-27 21:00:00")
