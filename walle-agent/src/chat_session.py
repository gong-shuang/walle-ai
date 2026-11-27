# -*- coding: utf-8 -*-
"""M4-06：按 client_id + device 保存最近几轮对话（内存 + TTL）。"""

import json
import threading
import time


class ConversationStore:
    """轻量多轮会话缓存；进程重启后不保留。"""

    def __init__(self, max_turns=8, ttl_sec=1800, now=None):
        self.max_turns = max(1, int(max_turns))
        self.ttl_sec = max(1.0, float(ttl_sec))
        self._now = now or time.time
        self._lock = threading.RLock()
        self._data = {}

    @staticmethod
    def key(client_id, device):
        return "{}|{}".format(str(client_id or "").strip(),
                              str(device or "").strip())

    def clear_expired(self):
        now = self._now()
        with self._lock:
            expired = [key for key, item in self._data.items()
                       if now - item["updated"] > self.ttl_sec]
            for key in expired:
                self._data.pop(key, None)
        return len(expired)

    def append(self, client_id, device, user_text, intent=None,
               outcome=None):
        key = self.key(client_id, device)
        now = self._now()
        turn = {
            "ts": now,
            "text": str(user_text or ""),
            "intent": intent,
            "outcome": outcome or {},
        }
        with self._lock:
            self.clear_expired()
            item = self._data.setdefault(key, {"turns": []})
            item["turns"].append(turn)
            if len(item["turns"]) > self.max_turns:
                item["turns"] = item["turns"][-self.max_turns:]
            item["updated"] = now
        return turn

    def turns(self, client_id, device, limit=None):
        key = self.key(client_id, device)
        with self._lock:
            self.clear_expired()
            item = self._data.get(key)
            turns = list(item["turns"]) if item else []
        return turns[-limit:] if limit else turns

    def last(self, client_id, device, action=None, limit=None):
        for turn in reversed(self.turns(client_id, device, limit)):
            intent = turn.get("intent") or {}
            if action is None or intent.get("action") == action:
                return turn
        return None

    def context(self, client_id, device, limit=6):
        """给 LLM 的紧凑历史文本。"""
        lines = []
        for turn in self.turns(client_id, device, limit):
            text = (turn.get("text") or "").strip()
            if text:
                lines.append("用户：" + text[:80])
            intent = turn.get("intent") or {}
            if intent.get("action"):
                lines.append("执行：" + json.dumps(
                    intent, ensure_ascii=False)[:160])
            outcome = turn.get("outcome") or {}
            summary = outcome.get("summary")
            if summary:
                lines.append("结果：" + str(summary)[:160])
        return "\n".join(lines)

    def clear(self):
        with self._lock:
            self._data.clear()
