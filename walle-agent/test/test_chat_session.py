#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M4-06：多轮会话缓存测试。"""

import chat_session


def test_store_keeps_max_turns_and_isolates_keys():
    store = chat_session.ConversationStore(max_turns=3, ttl_sec=60)
    for index in range(5):
        store.append("web", "dev-1", "t{}".format(index),
                     intent={"action": "stop"},
                     outcome={"summary": "r{}".format(index)})

    turns = store.turns("web", "dev-1")
    assert [turn["text"] for turn in turns] == ["t2", "t3", "t4"]
    assert store.turns("web", "dev-2") == []
    assert store.turns("other", "dev-1") == []


def test_store_expires_sessions():
    now = [1000.0]
    store = chat_session.ConversationStore(
        max_turns=8, ttl_sec=10, now=lambda: now[0])
    store.append("web", "dev-1", "hi", intent={"action": "stop"})

    assert store.turns("web", "dev-1")

    now[0] += 11
    assert store.turns("web", "dev-1") == []


def test_context_contains_user_intent_and_summary():
    store = chat_session.ConversationStore()
    store.append(
        "web", "dev-1", "前进3米",
        intent={"action": "move", "direction": "forward", "distance_m": 3},
        outcome={"summary": "已前进 3 米"})

    text = store.context("web", "dev-1")

    assert "用户：前进3米" in text
    assert '"action": "move"' in text
    assert "结果：已前进 3 米" in text


def test_last_can_filter_by_action():
    store = chat_session.ConversationStore()
    store.append("web", "dev-1", "查看录像",
                 intent={"action": "record_list"})
    store.append("web", "dev-1", "停", intent={"action": "stop"})

    assert store.last("web", "dev-1")["intent"]["action"] == "stop"
    assert store.last("web", "dev-1", action="record_list")[
        "intent"]["action"] == "record_list"
    assert store.last("web", "dev-1", action="move") is None
