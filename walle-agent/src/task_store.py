# -*- coding: utf-8 -*-
"""M3-01：任务与执行记录的 SQLite 存储。

- `tasks`：任务定义与下次触发时间；
- `task_runs`：每次执行的阶段与结果；
- WAL 模式，默认 `walle-agent/data/agent.db`。

本模块只负责持久化，不触发任务、不发 MQTT；调度逻辑见后续 M3-02。
"""

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None

AGENT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(AGENT_ROOT, "data", "agent.db")
DEFAULT_TIMEZONE = "Asia/Shanghai"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    device TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    schedule_json TEXT NOT NULL,
    actions_json TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    next_run_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_due
    ON tasks(enabled, next_run_at);

CREATE TABLE IF NOT EXISTS task_runs (
    run_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    phase TEXT NOT NULL,
    scheduled_at TEXT,
    started_at TEXT,
    finished_at TEXT,
    request_id TEXT,
    result INTEGER,
    cause TEXT,
    error TEXT,
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_task_runs_task
    ON task_runs(task_id, scheduled_at);

CREATE UNIQUE INDEX IF NOT EXISTS uniq_task_runs_slot
    ON task_runs(task_id, phase, scheduled_at);
"""


class TaskStore:
    """任务库；线程安全，连接由内部锁串行化。"""

    def __init__(self, path=None, timezone=None):
        self.path = self._resolve_path(path)
        self.timezone = str(timezone or DEFAULT_TIMEZONE)
        self._lock = threading.RLock()
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    @staticmethod
    def _resolve_path(path):
        path = str(path or "").strip()
        if not path:
            return DEFAULT_DB_PATH
        if path == ":memory:":
            return path
        path = os.path.expanduser(path)
        if not os.path.isabs(path):
            path = os.path.join(AGENT_ROOT, path)
        return os.path.abspath(path)

    def close(self):
        with self._lock:
            self._conn.close()

    # ---- 任务 ----

    def create_task(self, name, device, schedule, actions, timezone=None,
                    enabled=True, next_run_at=None, task_id=None):
        name = str(name or "").strip()
        device = str(device or "").strip()
        if not name or not device:
            raise ValueError("任务 name 和 device 不能为空")
        timezone = str(timezone or self.timezone)
        task_id = str(task_id or "task-{}".format(uuid.uuid4().hex[:8]))
        now = self._now(timezone)
        with self._lock:
            self._conn.execute(
                "INSERT INTO tasks (id, name, device, timezone, schedule_json,"
                " actions_json, enabled, created_at, updated_at, next_run_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (task_id, name, device, timezone,
                 self._dump(schedule, {}), self._dump(actions, []),
                 1 if enabled else 0, now, now, next_run_at))
            self._conn.commit()
        return self.get_task(task_id)

    def get_task(self, task_id):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (str(task_id),)).fetchone()
        return self._task_row(row) if row else None

    def list_tasks(self, enabled=None):
        sql = "SELECT * FROM tasks"
        params = []
        if enabled is not None:
            sql += " WHERE enabled = ?"
            params.append(1 if enabled else 0)
        sql += " ORDER BY created_at, id"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._task_row(row) for row in rows]

    def update_task(self, task_id, **fields):
        allowed = {"name", "device", "timezone", "schedule", "actions",
                   "enabled", "next_run_at"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError("不支持更新字段：{}".format("、".join(sorted(unknown))))
        task = self.get_task(task_id)
        if task is None:
            return None
        updates = []
        params = []
        for key in ("name", "device", "timezone", "next_run_at"):
            if key in fields:
                updates.append("{} = ?".format(key))
                params.append(fields[key])
        if "schedule" in fields:
            updates.append("schedule_json = ?")
            params.append(self._dump(fields["schedule"], {}))
        if "actions" in fields:
            updates.append("actions_json = ?")
            params.append(self._dump(fields["actions"], []))
        if "enabled" in fields:
            updates.append("enabled = ?")
            params.append(1 if fields["enabled"] else 0)
        timezone = fields.get("timezone") or task.get("timezone")
        updates.append("updated_at = ?")
        params.append(self._now(timezone))
        params.append(str(task_id))
        with self._lock:
            self._conn.execute(
                "UPDATE tasks SET {} WHERE id = ?".format(", ".join(updates)),
                params)
            self._conn.commit()
        return self.get_task(task_id)

    def delete_task(self, task_id):
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM tasks WHERE id = ?", (str(task_id),))
            self._conn.commit()
        return cursor.rowcount > 0

    # ---- 执行记录 ----

    def record_run(self, task_id, phase, scheduled_at=None, started_at=None,
                   finished_at=None, request_id=None, result=None,
                   cause=None, error=None, run_id=None):
        phase = str(phase or "").strip()
        if not phase:
            raise ValueError("运行记录 phase 不能为空")
        run_id = str(run_id or "run-{}".format(uuid.uuid4().hex[:12]))
        with self._lock:
            # (task_id, phase, scheduled_at) 已存在：返回原记录，交给调度层去重
            row = self._conn.execute(
                "SELECT * FROM task_runs WHERE task_id = ? AND phase = ?"
                " AND scheduled_at IS ?",
                (str(task_id), phase, scheduled_at)).fetchone()
            if row is not None:
                return dict(row)
            self._conn.execute(
                "INSERT INTO task_runs (run_id, task_id, phase,"
                " scheduled_at, started_at, finished_at, request_id,"
                " result, cause, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, str(task_id), phase, scheduled_at, started_at,
                 finished_at, request_id, result, cause, error))
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM task_runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def get_run(self, run_id):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM task_runs WHERE run_id = ?",
                (str(run_id),)).fetchone()
        return dict(row) if row else None

    def list_runs(self, task_id, limit=50):
        try:
            limit = max(1, int(limit))
        except (TypeError, ValueError):
            limit = 50
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM task_runs WHERE task_id = ?"
                " ORDER BY COALESCE(finished_at, started_at, scheduled_at)"
                " DESC, rowid DESC LIMIT ?",
                (str(task_id), limit)).fetchall()
        return [dict(row) for row in rows]

    # ---- 内部 ----

    def _zone(self, name):
        if ZoneInfo is None:
            return None
        try:
            return ZoneInfo(str(name or self.timezone))
        except Exception:
            return None

    def _now(self, timezone=None):
        return datetime.now(self._zone(timezone)).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _dump(value, default):
        if value is None:
            value = default
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _load(text, default):
        try:
            return json.loads(text)
        except (TypeError, ValueError):
            return default

    def _task_row(self, row):
        item = dict(row)
        item["enabled"] = bool(item["enabled"])
        item["schedule"] = self._load(item.pop("schedule_json", None), {})
        item["actions"] = self._load(item.pop("actions_json", None), [])
        return item
