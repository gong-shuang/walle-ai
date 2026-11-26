#!/usr/bin/env python3
"""控制权租约：acquire/release、fail-open、TTL、离线 grace 与控制拦截。"""

import json

import net_mqtt_bridge.agent as agent
from net_mqtt_bridge.agent import RpiMonitor
from net_mqtt_bridge.control import ControlLease


def test_acquire_granted_then_occupied_then_renewed():
    lease = ControlLease(ttl_sec=900, offline_grace_sec=10)

    result, action, holder = lease.acquire("web-a", "Web A", now=1000)
    assert (result, action) == (0, "granted")
    assert holder["client_id"] == "web-a"
    assert holder["remaining_sec"] == 900

    result, action, holder = lease.acquire("android-b", "Android B", now=1010)
    assert (result, action) == (1, "occupied")
    assert holder["client_id"] == "web-a"

    result, action, holder = lease.acquire("web-a", "Web A", now=1020)
    assert (result, action) == (0, "renewed")
    assert holder["remaining_sec"] == 900


def test_touch_fail_open_and_holder_match():
    lease = ControlLease(ttl_sec=900, offline_grace_sec=10)
    # 没有 holder：fail-open，任何人都可以控制
    assert lease.touch("web-a", now=1000) == (True, False)

    lease.acquire("web-a", "Web A", now=1000)
    assert lease.touch("web-a", now=1100) == (True, True)
    assert lease.touch("android-b", now=1100) == (False, False)
    # 持有者请求会续租，剩余时间回到满额
    assert lease.snapshot(now=1100)["remaining_sec"] == 900


def test_release_only_by_holder():
    lease = ControlLease(ttl_sec=900, offline_grace_sec=10)
    lease.acquire("web-a", "Web A", now=1000)

    assert lease.release("android-b", now=1010)[:2] == (1, "not_holder")
    assert lease.release("web-a", now=1020)[:2] == (0, "released")
    assert lease.snapshot(now=1020) is None
    assert lease.release("web-a", now=1030)[:2] == (0, "already_free")


def test_offline_grace_cancel_and_ttl_expiry():
    lease = ControlLease(ttl_sec=900, offline_grace_sec=10)
    lease.acquire("web-a", "Web A", now=1000)
    lease.on_presence("web-a", False, now=1100)
    assert lease.tick(now=1105) is None            # grace 内不释放
    lease.on_presence("web-a", True, now=1106)     # 恢复上线，取消释放
    assert lease.tick(now=1109) is None

    lease.on_presence("web-a", False, now=1200)
    assert lease.tick(now=1211) == "holder_offline"
    assert lease.snapshot(now=1211) is None

    lease.acquire("web-a", "Web A", now=2000)
    assert lease.tick(now=2900) == "expired"
    assert lease.snapshot(now=2901) is None


def _bare_monitor():
    """构造一个不走 __init__ 的实例，只填租约拦截需要的属性。"""
    monitor = object.__new__(RpiMonitor)
    monitor.lan_ip = "192.168.1.10"
    monitor.control_lease = ControlLease(ttl_sec=900, offline_grace_sec=10)
    monitor._last_control_denied_log = 0.0
    monitor.published = []
    monitor.publish_json = lambda resource, data, retain=False: monitor.published.append(
        (resource, data))
    monitor.called = []
    monitor.handle_video_request = lambda payload: monitor.called.append(payload)
    monitor.handle_query_status = lambda payload: monitor.called.append(payload)
    monitor.query_pool = monitor.action_pool = monitor.servo_pool = None
    monitor._query_slots = monitor._action_slots = monitor._servo_slots = None
    monitor._submit = lambda pool, slots, handler, payload, topic: handler(payload)
    return monitor


def test_query_allowed_when_other_client_holds_lease():
    monitor = _bare_monitor()
    monitor.control_lease.acquire("web-a", "Web A")

    monitor._handle_request(
        agent.device_topic(agent.REQ_STATE),
        json.dumps({"request_id": "q1", "client_id": "android-b"}))

    assert len(monitor.called) == 1
    assert monitor.published == []


def test_control_denied_when_other_client_holds_lease():
    monitor = _bare_monitor()
    monitor.control_lease.acquire("web-a", "Web A")

    monitor._handle_request(
        agent.device_topic(agent.REQ_VIDEO_START),
        json.dumps({"request_id": "v1", "client_id": "android-b"}))

    assert monitor.called == []
    resource, data = monitor.published[-1]
    assert resource == agent.RES_VIDEO_START_RESULT
    assert data["result"] == -5
    assert data["error_code"] == "control_denied"
    assert data["cause"] == "设备控制权限已被别人占用"
    assert data["control_holder"]["client_id"] == "web-a"


def test_car_motor_control_denied_uses_minus_five():
    monitor = _bare_monitor()
    monitor.control_lease.acquire("web-a", "Web A")

    monitor._handle_request(
        agent.device_topic(agent.REQ_CAR_MOTOR_SET),
        json.dumps({"request_id": "m1", "client_id": "android-b"}))

    assert monitor.called == []
    resource, data = monitor.published[-1]
    assert resource == agent.RES_CAR_MOTOR_SET_RESULT
    assert data["result"] == -5
    assert data["error_code"] == "control_denied"
    assert data["cause"] == "设备控制权限已被别人占用"


def test_car_servo_set_control_denied_uses_minus_five():
    monitor = _bare_monitor()
    monitor.control_lease.acquire("web-a", "Web A")

    monitor._handle_request(
        agent.device_topic(agent.REQ_CAR_SERVO_SET),
        json.dumps({"request_id": "s1", "client_id": "android-b"}))

    assert monitor.called == []
    resource, data = monitor.published[-1]
    assert resource == agent.RES_CAR_SERVO_SET_RESULT
    assert data["result"] == -5
    assert data["error_code"] == "control_denied"


def test_control_fail_open_and_holder_allowed():
    monitor = _bare_monitor()
    payload = json.dumps({"request_id": "v1", "client_id": "web-a"})

    monitor._handle_request(agent.device_topic(agent.REQ_VIDEO_START), payload)
    assert len(monitor.called) == 1                 # 无 holder：fail-open
    assert monitor.published == []

    monitor.called.clear()
    monitor.control_lease.acquire("web-a", "Web A")
    monitor._handle_request(agent.device_topic(agent.REQ_VIDEO_START), payload)
    assert len(monitor.called) == 1                 # 持有者：放行


def test_acquire_and_release_handlers_publish_result():
    monitor = _bare_monitor()
    monitor._handle_request(
        agent.device_topic(agent.REQ_CONTROL_ACQUIRE),
        json.dumps({"request_id": "a1", "client_id": "web-a",
                    "client_name": "Web A"}))

    resource, data = monitor.published[-1]
    assert resource == agent.RES_CONTROL_ACQUIRE
    assert (data["result"], data["action"], data["cause"]) == (0, "granted", "ok")
    assert data["lease_ttl_sec"] == 900
    assert data["control_holder"]["client_id"] == "web-a"

    monitor._handle_request(
        agent.device_topic(agent.REQ_CONTROL_RELEASE),
        json.dumps({"request_id": "r1", "client_id": "web-a"}))

    resource, data = monitor.published[-1]
    assert resource == agent.RES_CONTROL_RELEASE
    assert (data["result"], data["action"]) == (0, "released")
    assert data["control_holder"] is None
