# -*- coding: utf-8 -*-
"""M3-02：任务调度线程。

- 独立 daemon 线程，默认 1 秒 tick；
- 时区默认 Asia/Shanghai，可配置 `TASK_TIMEZONE`；
- 从 TaskStore 取 `enabled=1 AND next_run_at <= now` 的任务，交给注入的 dispatch；
- 同一任务同一个 `next_run_at` 在一个进程内只分发一次；
  持久化去重由 `task_runs(task_id, phase, scheduled_at)` 唯一索引兜底。

本模块只负责“到点触发”，任务类型（daily/once/cron/interval）与动作执行由 M3-03/M3-05 提供。
"""

import logging
import threading
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None

from task_store import DEFAULT_TIMEZONE

LOG = logging.getLogger("walle.task_scheduler")


class TaskScheduler:
    def __init__(self, store, dispatch, tick_sec=1.0, timezone=None,
                 now=None):
        self.store = store
        self.dispatch = dispatch
        try:
            self.tick_sec = max(0.01, float(tick_sec))
        except (TypeError, ValueError):
            self.tick_sec = 1.0
        self.timezone = str(timezone or DEFAULT_TIMEZONE)
        self._zone = self._load_zone(self.timezone)
        self._now = now
        self._stop_event = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        # task_id -> 已分发的 next_run_at，进程内防重复；任务推进 next_run_at 后自然换槽
        self._last_slot = {}

    @staticmethod
    def _load_zone(name):
        if ZoneInfo is None:
            return None
        try:
            return ZoneInfo(str(name))
        except Exception:  # noqa: BLE001 - 非法时区回落到默认
            try:
                return ZoneInfo(DEFAULT_TIMEZONE)
            except Exception:  # noqa: BLE001
                return None

    def now_str(self):
        value = self._now() if self._now is not None else datetime.now(self._zone)
        if value.tzinfo is not None and self._zone is not None:
            value = value.astimezone(self._zone)
        return value.strftime("%Y-%m-%d %H:%M:%S")

    def tick(self):
        """扫一次到期任务，返回本次实际分发的 task_id 列表。"""
        now = self.now_str()
        fired = []
        for task in self.store.list_due(now):
            task_id = task.get("id")
            scheduled_at = task.get("next_run_at")
            with self._lock:
                if self._last_slot.get(task_id) == scheduled_at:
                    continue
                self._last_slot[task_id] = scheduled_at
            try:
                self.dispatch(task, scheduled_at)
            except Exception:  # noqa: BLE001 - 单个任务失败不能拖垮调度线程
                LOG.exception("任务分发失败 task_id=%s scheduled_at=%s",
                              task_id, scheduled_at)
            fired.append(task_id)
        return fired

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._loop, name="walle-task-scheduler", daemon=True)
            self._thread.start()
            return True

    def stop(self, timeout=3.0):
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        self._thread = None

    @property
    def running(self):
        return bool(self._thread is not None and self._thread.is_alive())

    def _loop(self):
        while not self._stop_event.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                LOG.exception("任务调度 tick 失败")
            self._stop_event.wait(self.tick_sec)
