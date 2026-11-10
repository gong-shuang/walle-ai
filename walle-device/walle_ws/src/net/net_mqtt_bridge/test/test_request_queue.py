#!/usr/bin/env python3
"""P2-1.4：查询/动作线程池分离与有界队列测试。"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import net_mqtt_bridge.agent as agent
from net_mqtt_bridge.agent import RpiMonitor


def _bare_monitor(query_workers=1, action_workers=1,
                  query_backlog=1, action_backlog=1):
    monitor = object.__new__(RpiMonitor)
    monitor.query_pool = ThreadPoolExecutor(
        query_workers, thread_name_prefix="t-query")
    monitor.action_pool = ThreadPoolExecutor(
        action_workers, thread_name_prefix="t-action")
    monitor._query_slots = threading.BoundedSemaphore(
        query_workers + query_backlog)
    monitor._action_slots = threading.BoundedSemaphore(
        action_workers + action_backlog)
    monitor.dropped_requests = 0
    monitor._dropped_lock = threading.Lock()
    monitor._last_drop_log = 0.0
    return monitor


def _topic(resource):
    return "%s/%s" % (agent.device_topic_base(), resource)


def test_query_queue_limit_drops_excess():
    monitor = _bare_monitor(query_workers=1, query_backlog=1)
    gate = threading.Event()
    started = []

    def _handler(_payload):
        started.append(1)
        gate.wait(2.0)

    monitor.handle_query_status = _handler
    for _ in range(3):
        monitor._handle_request(_topic(agent.REQ_STATE), "{}")

    # 1 个执行中 + 1 个排队，第 3 个超出上限被丢弃
    assert monitor.dropped_requests == 1
    gate.set()
    monitor.query_pool.shutdown(wait=True)
    assert len(started) == 2


def test_action_pool_does_not_block_queries():
    monitor = _bare_monitor(query_workers=1, action_workers=1)
    gate = threading.Event()
    action_started = threading.Event()
    query_done = threading.Event()

    def _action(_payload):
        action_started.set()
        gate.wait(2.0)

    def _query(_payload):
        query_done.set()

    monitor.handle_video_request = _action
    monitor.handle_query_status = _query
    monitor._handle_request(_topic(agent.REQ_VIDEO_START), "{}")
    assert action_started.wait(1.0)

    monitor._handle_request(_topic(agent.REQ_STATE), "{}")
    assert query_done.wait(1.0)      # 动作线程被占满时查询仍能执行

    gate.set()
    monitor.query_pool.shutdown(wait=True)
    monitor.action_pool.shutdown(wait=True)


def test_monitor_sampling_continues_under_request_flood(monkeypatch):
    monitor = _bare_monitor(query_workers=1, query_backlog=8)
    monitor.stop_event = threading.Event()
    monitor.sample_lock = threading.Lock()
    monitor.status_sample = None
    monitor.status_sample_lock = threading.Lock()
    monitor.active_alarms = set()
    monitor.usb_state = {}

    monkeypatch.setattr(agent, "MONITOR_INTERVAL", 0.02)
    monkeypatch.setattr(agent, "STATUS_PRIME_INTERVAL", 0.02)
    monkeypatch.setattr(agent, "net_snapshot", lambda: {})
    monkeypatch.setattr(
        agent, "calc_net_rates", lambda prev, elapsed: ({}, {}, 0.0, 0.0))
    monkeypatch.setattr(agent, "disk_mounts", lambda: [])
    monkeypatch.setattr(agent, "get_cpu_temp", lambda: None)
    monkeypatch.setattr(agent.psutil, "cpu_percent", lambda percpu=False: [1.0])
    monitor.check_threshold = lambda *a, **k: None
    monitor.check_usb = lambda: {}

    sample_thread = threading.Thread(target=monitor.monitor_loop, daemon=True)
    sample_thread.start()

    # 占满查询池，采样线程仍按自己的周期更新快照
    gate = threading.Event()
    monitor.handle_query_status = lambda payload: gate.wait(2.0)
    for _ in range(4):
        monitor._handle_request(_topic(agent.REQ_STATE), "{}")

    deadline = time.monotonic() + 1.0
    first = None
    while time.monotonic() < deadline:
        first = monitor._status_sample()
        if first is not None:
            break
        time.sleep(0.01)
    assert first is not None

    time.sleep(0.1)
    second = monitor._status_sample()
    assert second is not None
    assert second[5] > first[5]

    monitor.stop_event.set()
    gate.set()
    sample_thread.join(timeout=2.0)
    monitor.query_pool.shutdown(wait=True)
