#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M1-01 RequestBus 单元测试：不连 MQTT，用假 publish/on_message 驱动。"""

import threading

from walle_protocol import REQUEST_TO_RESPONSE, RequestBus


def _topic(device, resource):
    return "walle/test/device/{}/{}".format(device, resource)


def _make_bus(responder=None, **kwargs):
    bus = None

    def publish(device, resource, payload):
        if responder is not None:
            responder(bus, device, resource, payload)
        return {"published": True, "topic": _topic(device, resource),
                "payload": payload}

    kwargs.setdefault("default_timeout", 0.2)
    bus = RequestBus(publish, _topic, **kwargs)
    return bus


def _respond(bus, device, resource, payload, result=0, cause="ok", **extra):
    response = {"request_id": payload["request_id"],
                "result": result, "cause": cause}
    response.update(extra)
    return bus.on_message(
        device, _topic(device, REQUEST_TO_RESPONSE[resource]), response)


def test_request_returns_matching_response():
    bus = _make_bus(lambda b, device, resource, payload: _respond(
        b, device, resource, payload))

    res = bus.request("dev-1", "baseState/query/request")

    assert res["received"] is True
    assert res["ok"] is True
    assert res["request_id"].startswith("agent-baseState-")
    assert res["payload"]["result"] == 0
    assert res["request_topic"] == _topic("dev-1", "baseState/query/request")
    assert res["response_topic"] == _topic("dev-1", "baseState/query/response")
    assert res["published"] is True


def test_timeout_clears_pending_and_late_response_is_dropped():
    bus = _make_bus(None, default_timeout=0.05)

    res = bus.request("dev-1", "runState/query/request",
                      {"request_id": "rid-timeout"})

    assert res["received"] is False
    assert res["error"] == "timeout"
    assert bus.stats()["pending"] == 0
    assert bus.on_message(
        "dev-1", _topic("dev-1", "runState/query/response"),
        {"request_id": "rid-timeout", "result": 0}) is False
    assert bus.stats().get("late", 0) == 1


def test_duplicate_response_only_first_wins():
    def responder(bus, device, resource, payload):
        topic = _topic(device, REQUEST_TO_RESPONSE[resource])
        bus.on_message(device, topic, {
            "request_id": payload["request_id"], "result": 0, "cause": "first"})
        bus.on_message(device, topic, {
            "request_id": payload["request_id"], "result": 0, "cause": "second"})

    bus = _make_bus(responder)
    res = bus.request("dev-1", "baseState/query/request")

    assert res["received"] is True
    assert res["payload"]["cause"] == "first"
    assert bus.stats().get("late", 0) == 1


def test_wrong_device_and_topic_do_not_wake_waiter():
    def responder(bus, device, resource, payload):
        response = {"request_id": payload["request_id"], "result": 0}
        bus.on_message("other-dev", _topic(
            "other-dev", REQUEST_TO_RESPONSE[resource]), response)
        bus.on_message(device, _topic(
            device, "wrong/resource/response"), response)

    bus = _make_bus(responder, default_timeout=0.05)
    res = bus.request("dev-1", "baseState/query/request")

    assert res["received"] is False
    assert res["error"] == "timeout"
    assert bus.stats().get("mismatch", 0) == 2


def test_control_denied_is_business_failure_with_holder():
    def responder(bus, device, resource, payload):
        _respond(bus, device, resource, payload, result=-5,
                 control_holder={"client_id": "web-a"})

    bus = _make_bus(responder)
    res = bus.request("dev-1", "stream/start/request")

    assert res["received"] is True
    assert res["ok"] is False
    assert res["error"] == "control_denied"
    assert res["result"] == -5
    assert res["decoded"]["holder"]["client_id"] == "web-a"


def test_device_offline_fails_fast_without_publish():
    calls = []

    def publish(device, resource, payload):
        calls.append((device, resource, payload))
        return {"published": True}

    bus = RequestBus(publish, _topic, is_online=lambda device: False)
    res = bus.request("dev-1", "baseState/query/request")

    assert res["received"] is False
    assert res["error"] == "device_offline"
    assert calls == []
    assert bus.stats()["pending"] == 0


def test_pending_fails_when_device_goes_offline():
    def responder(bus, device, resource, payload):
        bus.fail_device(device, "device_offline", "设备离线")

    bus = _make_bus(responder)
    res = bus.request("dev-1", "baseState/query/request")

    assert res["received"] is False
    assert res["error"] == "device_offline"
    assert bus.stats()["pending"] == 0


def test_publish_failure_returns_immediately_and_clears_pending():
    def publish(device, resource, payload):
        return {"published": False, "error": "mqtt_not_connected"}

    bus = RequestBus(publish, _topic)
    res = bus.request("dev-1", "baseState/query/request")

    assert res["received"] is False
    assert res["error"] == "publish_failed"
    assert res["publish"]["error"] == "mqtt_not_connected"
    assert bus.stats()["pending"] == 0


def test_fail_all_fails_pending_requests():
    def responder(bus, device, resource, payload):
        bus.fail_all("mqtt_disconnected", "MQTT 断开")

    bus = _make_bus(responder)
    res = bus.request("dev-1", "runState/query/request")

    assert res["received"] is False
    assert res["error"] == "mqtt_disconnected"
    assert bus.stats()["pending"] == 0


def test_concurrent_requests_do_not_cross_talk():
    results = []

    def responder(bus, device, resource, payload):
        _respond(bus, device, resource, payload)

    bus = _make_bus(responder, default_timeout=1.0)

    def worker(index):
        results.append(bus.request(
            "dev-{}".format(index), "baseState/query/request"))

    threads = [threading.Thread(target=worker, args=(index,)) for index in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert len(results) == 2
    assert all(item["received"] and item["ok"] for item in results)
    assert {item["device"] for item in results} == {"dev-1", "dev-2"}
    assert len({item["request_id"] for item in results}) == 2


def test_unknown_resource_is_rejected_without_publish():
    calls = []

    def publish(device, resource, payload):
        calls.append(resource)
        return {"published": True}

    bus = RequestBus(publish, _topic)
    res = bus.request("dev-1", "unknown/request")

    assert res["received"] is False
    assert res["error"] == "unknown_resource"
    assert calls == []


def test_publish_exception_returns_publish_failed():
    def publish(device, resource, payload):
        raise RuntimeError("boom")

    bus = RequestBus(publish, _topic)
    res = bus.request("dev-1", "baseState/query/request")

    assert res["received"] is False
    assert res["error"] == "publish_failed"
    assert "publish_exception" in res["publish"]["error"]
    assert bus.stats()["pending"] == 0


def test_cancel_threads_only_wakes_matching_owner():
    bus = _make_bus(None, default_timeout=2.0)
    results = {}

    def worker():
        results["res"] = bus.request("dev-1", "runState/query/request")

    thread = threading.Thread(target=worker)
    thread.start()
    for _ in range(100):
        if bus.stats().get("pending") == 1:
            break
        threading.Event().wait(0.01)
    assert bus.stats().get("pending") == 1

    # 主线程不在目标集合内，不应误伤工作线程的请求
    assert bus.cancel_threads([threading.get_ident()]) == 0
    assert bus.stats().get("pending") == 1

    assert bus.cancel_threads([thread.ident], "cancelled",
                              "会话已中止") == 1
    thread.join(timeout=1)

    assert results["res"]["received"] is False
    assert results["res"]["error"] == "cancelled"
    assert results["res"]["cause"] == "会话已中止"
    assert bus.stats()["pending"] == 0
