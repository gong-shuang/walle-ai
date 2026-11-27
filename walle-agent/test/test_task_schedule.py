# -*- coding: utf-8 -*-
"""M3-03：daily_window / once / cron / interval 单元测试。"""

from datetime import datetime, timezone as dt_timezone

import pytest

import task_schedule
import task_store


def _task(schedule, actions=None, timezone="Asia/Shanghai", next_run_at=None):
    return {
        "id": "task-1", "name": "任务", "device": "dev-1",
        "timezone": timezone, "schedule": schedule,
        "actions": actions or [{"phase": "start", "tool": "record.start"}],
        "next_run_at": next_run_at,
    }


def test_daily_window_boundaries():
    task = _task({"type": "daily_window", "start": "08:00", "stop": "21:00"})

    assert task_schedule.next_occurrence(
        task, datetime(2026, 11, 27, 7, 0)) == {
            "at": "2026-11-27 08:00:00", "phase": "start"}
    assert task_schedule.next_occurrence(
        task, datetime(2026, 11, 27, 12, 0)) == {
            "at": "2026-11-27 21:00:00", "phase": "stop"}
    assert task_schedule.next_occurrence(
        task, datetime(2026, 11, 27, 22, 0)) == {
            "at": "2026-11-28 08:00:00", "phase": "start"}


def test_daily_window_overnight():
    task = _task({"type": "daily_window", "start": "22:00", "stop": "06:00"})

    assert task_schedule.next_occurrence(
        task, datetime(2026, 11, 27, 12, 0)) == {
            "at": "2026-11-27 22:00:00", "phase": "start"}
    assert task_schedule.next_occurrence(
        task, datetime(2026, 11, 27, 23, 0)) == {
            "at": "2026-11-28 06:00:00", "phase": "stop"}


def test_once_future_and_past():
    task = _task({"type": "once", "at": "2026-11-28 08:00:00"})

    assert task_schedule.next_occurrence(
        task, datetime(2026, 11, 27, 12, 0)) == {
            "at": "2026-11-28 08:00:00", "phase": "start"}
    assert task_schedule.next_occurrence(
        task, datetime(2026, 11, 28, 9, 0)) is None


def test_cron_daily_weekday_and_step():
    daily = _task({"type": "cron", "expr": "0 8 * * *"})
    assert task_schedule.next_occurrence(
        daily, datetime(2026, 11, 27, 7, 30)) == {
            "at": "2026-11-27 08:00:00", "phase": "start"}
    assert task_schedule.next_occurrence(
        daily, datetime(2026, 11, 27, 8, 0)) == {
            "at": "2026-11-28 08:00:00", "phase": "start"}

    weekdays = _task({"type": "cron", "expr": "0 8 * * 1-5"})
    assert task_schedule.next_occurrence(
        weekdays, datetime(2026, 11, 27, 9, 0)) == {
            "at": "2026-11-30 08:00:00", "phase": "start"}

    step = _task({"type": "cron", "expr": "*/15 * * * *"})
    assert task_schedule.next_occurrence(
        step, datetime(2026, 11, 27, 10, 7)) == {
            "at": "2026-11-27 10:15:00", "phase": "start"}


def test_interval_uses_anchor_and_advances():
    task = _task({"type": "interval", "seconds": 7200})

    assert task_schedule.next_occurrence(
        task, datetime(2026, 11, 27, 8, 0)) == {
            "at": "2026-11-27 10:00:00", "phase": "start"}

    task["next_run_at"] = "2026-11-27 08:00:00"
    assert task_schedule.next_occurrence(
        task, datetime(2026, 11, 27, 9, 30)) == {
            "at": "2026-11-27 10:00:00", "phase": "start"}

    task["next_run_at"] = "2026-11-27 10:00:00"
    assert task_schedule.next_occurrence(
        task, datetime(2026, 11, 27, 10, 0)) == {
            "at": "2026-11-27 12:00:00", "phase": "start"}


def test_timezone_aware_now_is_converted():
    task = _task({"type": "once", "at": "2026-11-28 08:00:00"})
    now = datetime(2026, 11, 27, 23, 59, tzinfo=dt_timezone.utc)

    assert task_schedule.next_occurrence(task, now) == {
        "at": "2026-11-28 08:00:00", "phase": "start"}


@pytest.mark.parametrize("schedule", [
    {},
    {"type": "unknown"},
    {"type": "daily_window", "start": "08:00", "stop": "08:00"},
    {"type": "once", "at": "not-a-time"},
    {"type": "cron", "expr": "0 8 * *"},
    {"type": "interval", "seconds": 0},
])
def test_invalid_schedule_is_rejected(schedule):
    with pytest.raises(ValueError):
        task_schedule.validate_schedule(schedule)


def test_advance_task_persists_next_run_at(tmp_path):
    store = task_store.TaskStore(str(tmp_path / "agent.db"))
    try:
        task = store.create_task(
            "录像窗口", "dev-1",
            {"type": "daily_window", "start": "08:00", "stop": "21:00"},
            [{"phase": "start", "tool": "record.start"},
             {"phase": "stop", "tool": "record.stop"}],
            next_run_at="2026-11-27 08:00:00")

        nxt = task_schedule.advance_task(
            store, task, datetime(2026, 11, 27, 8, 0))

        assert nxt == {"at": "2026-11-27 21:00:00", "phase": "stop"}
        assert store.get_task(task["id"])["next_run_at"] == (
            "2026-11-27 21:00:00")
    finally:
        store.close()
