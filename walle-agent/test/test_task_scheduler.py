# -*- coding: utf-8 -*-
"""M3-02：任务调度线程测试。"""

import threading
from datetime import datetime, timezone as dt_timezone

import task_store
from task_scheduler import TaskScheduler


def _store(tmp_path):
    return task_store.TaskStore(str(tmp_path / "agent.db"))


def _task(store, next_run_at, enabled=True):
    return store.create_task(
        "任务", "dev-1", {"type": "once"}, [],
        enabled=enabled, next_run_at=next_run_at)


def test_tick_dispatches_due_task_once(tmp_path):
    store = _store(tmp_path)
    task = _task(store, "2026-01-01 00:00:00")
    calls = []
    scheduler = TaskScheduler(
        store, lambda item, slot: calls.append((item["id"], slot)),
        now=lambda: datetime(2026, 1, 1, 0, 0, 5))
    try:
        assert scheduler.tick() == [task["id"]]
        assert scheduler.tick() == []
        assert calls == [(task["id"], "2026-01-01 00:00:00")]
    finally:
        scheduler.stop()
        store.close()


def test_tick_skips_future_disabled_and_empty_next_run(tmp_path):
    store = _store(tmp_path)
    _task(store, "2026-01-01 00:00:10")
    _task(store, "2026-01-01 00:00:00", enabled=False)
    store.create_task("无下次时间", "dev-1", {"type": "once"}, [])
    calls = []
    scheduler = TaskScheduler(
        store, lambda item, slot: calls.append(item["id"]),
        now=lambda: datetime(2026, 1, 1, 0, 0, 5))
    try:
        assert scheduler.tick() == []
        assert calls == []
    finally:
        scheduler.stop()
        store.close()


def test_next_run_change_fires_again(tmp_path):
    store = _store(tmp_path)
    task = _task(store, "2026-01-01 00:00:00")
    current = {"now": datetime(2026, 1, 1, 0, 0, 5)}
    calls = []
    scheduler = TaskScheduler(
        store, lambda item, slot: calls.append(slot),
        now=lambda: current["now"])
    try:
        assert scheduler.tick() == [task["id"]]
        store.update_task(task["id"], next_run_at="2026-01-01 00:00:10")
        current["now"] = datetime(2026, 1, 1, 0, 0, 10)
        assert scheduler.tick() == [task["id"]]
        assert calls == ["2026-01-01 00:00:00", "2026-01-01 00:00:10"]
    finally:
        scheduler.stop()
        store.close()


def test_timezone_uses_configured_zone(tmp_path):
    store = _store(tmp_path)
    task = _task(store, "2026-01-01 08:00:00")
    calls = []
    scheduler = TaskScheduler(
        store, lambda item, slot: calls.append(item["id"]),
        timezone="Asia/Shanghai",
        now=lambda: datetime(2026, 1, 1, 0, 0, 0, tzinfo=dt_timezone.utc))
    try:
        assert scheduler.now_str() == "2026-01-01 08:00:00"
        assert scheduler.tick() == [task["id"]]
        assert calls == [task["id"]]
    finally:
        scheduler.stop()
        store.close()


def test_scheduler_thread_fires_and_stops(tmp_path):
    store = _store(tmp_path)
    _task(store, "2026-01-01 00:00:00")
    fired = threading.Event()
    scheduler = TaskScheduler(
        store, lambda item, slot: fired.set(), tick_sec=0.01,
        now=lambda: datetime(2026, 1, 1, 0, 0, 5))
    try:
        assert scheduler.start() is True
        assert scheduler.start() is False
        assert fired.wait(1.0)
    finally:
        scheduler.stop()
        store.close()
    assert scheduler.running is False


def test_dispatch_exception_does_not_repeat(tmp_path):
    store = _store(tmp_path)
    task = _task(store, "2026-01-01 00:00:00")

    def boom(item, slot):
        raise RuntimeError("boom")

    scheduler = TaskScheduler(
        store, boom, now=lambda: datetime(2026, 1, 1, 0, 0, 5))
    try:
        assert scheduler.tick() == [task["id"]]
        assert scheduler.tick() == []
    finally:
        scheduler.stop()
        store.close()
