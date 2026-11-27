# -*- coding: utf-8 -*-
"""M3-01：任务与执行记录的 SQLite 存储测试。"""

import os
import sqlite3

import car_agent
import task_store


def _store(tmp_path):
    return task_store.TaskStore(str(tmp_path / "agent.db"))


def test_schema_has_required_columns(tmp_path):
    path = str(tmp_path / "agent.db")
    store = task_store.TaskStore(path)
    try:
        conn = sqlite3.connect(path)
        try:
            task_columns = {row[1] for row in conn.execute(
                "PRAGMA table_info(tasks)")}
            run_columns = {row[1] for row in conn.execute(
                "PRAGMA table_info(task_runs)")}
            journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            conn.close()
    finally:
        store.close()

    assert task_columns == {
        "id", "name", "device", "timezone", "schedule_json",
        "actions_json", "enabled", "created_at", "updated_at", "next_run_at",
    }
    assert run_columns == {
        "run_id", "task_id", "phase", "scheduled_at", "started_at",
        "finished_at", "request_id", "result", "cause", "error",
    }
    assert journal_mode.lower() == "wal"


def test_task_survives_reopen(tmp_path):
    path = str(tmp_path / "agent.db")
    store = task_store.TaskStore(path)
    task = store.create_task(
        "每天 8 点录像", "dev-1",
        {"type": "daily", "time": "08:00"},
        [{"phase": "start", "tool": "record.start"},
         {"phase": "stop", "tool": "record.stop"}],
        next_run_at="2026-11-28 08:00:00")
    assert task["id"].startswith("task-")
    assert task["enabled"] is True
    assert task["schedule"] == {"type": "daily", "time": "08:00"}
    store.close()

    reopened = task_store.TaskStore(path)
    try:
        loaded = reopened.get_task(task["id"])
        assert loaded is not None
        assert loaded["name"] == "每天 8 点录像"
        assert loaded["device"] == "dev-1"
        assert loaded["timezone"] == "Asia/Shanghai"
        assert loaded["actions"][0]["tool"] == "record.start"
        assert loaded["next_run_at"] == "2026-11-28 08:00:00"
        assert [item["id"] for item in reopened.list_tasks()] == [task["id"]]
    finally:
        reopened.close()


def test_update_and_delete_cascades_runs(tmp_path):
    store = _store(tmp_path)
    try:
        task = store.create_task("原名称", "dev-1", {"type": "once"}, [])
        updated = store.update_task(
            task["id"], name="新名称", enabled=False,
            next_run_at="2026-12-01 08:00:00")
        assert updated["name"] == "新名称"
        assert updated["enabled"] is False
        assert updated["next_run_at"] == "2026-12-01 08:00:00"

        run = store.record_run(task["id"], "start",
                               scheduled_at="2026-11-28 08:00:00", result=0)
        assert run["task_id"] == task["id"]

        assert store.delete_task(task["id"]) is True
        assert store.get_task(task["id"]) is None
        assert store.list_runs(task["id"]) == []
        assert store.delete_task(task["id"]) is False
    finally:
        store.close()


def test_run_slot_is_unique(tmp_path):
    store = _store(tmp_path)
    try:
        task = store.create_task("任务", "dev-1", {"type": "once"}, [])
        first = store.record_run(
            task["id"], "start", scheduled_at="2026-11-28 08:00:00",
            started_at="2026-11-28 08:00:01")
        second = store.record_run(
            task["id"], "start", scheduled_at="2026-11-28 08:00:00",
            started_at="2026-11-28 08:00:05")

        assert second["run_id"] == first["run_id"]
        assert second["started_at"] == first["started_at"]
        assert len(store.list_runs(task["id"])) == 1
    finally:
        store.close()


def test_default_db_path_is_agent_data_dir():
    assert task_store.DEFAULT_DB_PATH.endswith(
        os.path.join("walle-agent", "data", "agent.db"))


def test_agent_task_db_is_lazy_and_uses_config(tmp_path, monkeypatch):
    monkeypatch.setattr(car_agent, "log", lambda: type(
        "Silent", (), {"info": lambda *a, **k: None,
                       "warning": lambda *a, **k: None,
                       "error": lambda *a, **k: None})())
    monkeypatch.setattr(car_agent, "server_tenant", lambda: "test")

    cfg = dict(car_agent.DEFAULTS)
    cfg.update({"MQTT_USER": "", "MQTT_PASSWORD": "", "TENANT": "test",
                "TASK_DB_PATH": str(tmp_path / "agent.db")})
    agent = car_agent.Agent(cfg)
    try:
        assert agent.task_store is None
        db_path = str(tmp_path / "agent.db")
        store = agent.task_db()
        assert store.path == db_path
        task = store.create_task("任务", "dev-1", {"type": "once"}, [])
        assert agent.task_db().get_task(task["id"])["name"] == "任务"
    finally:
        agent.shutdown()
    assert agent.task_store is None
