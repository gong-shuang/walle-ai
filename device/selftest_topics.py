#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主题契约自检（不需要连 MQTT，也不需要装 paho/psutil）。

校验：
  1. sanitize_segment 与 Android 端 TopicNames.sanitize 行为一致；
  2. walle/{tenant}/{device}/{resource} 拼装正确；
  3. 订阅清单只含「请求类」主题，且不含通配子树；
  4. 请求资源 → 处理函数全部存在；
  5. 发布走 v2 命名空间；
  6. 旧的 v1 主题常量已彻底删除。

用法：
    python3 selftest_topics.py
    python3 selftest_topics.py --tenant home --device 666feb85d43410b2

退出码 0 = 全通过，1 = 有断言失败。
"""

import argparse
import importlib.util
import json
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _install_stubs():
    """用桩模块导入 rpi_monitor，避免依赖 paho/psutil。"""
    paho = types.ModuleType("paho")
    paho_mqtt = types.ModuleType("paho.mqtt")
    client_mod = types.ModuleType("paho.mqtt.client")

    class _FakeClient:
        CallbackAPIVersion = types.SimpleNamespace(VERSION2=2)

        def __init__(self, *a, **kw):
            pass

    client_mod.Client = _FakeClient
    client_mod.CallbackAPIVersion = _FakeClient.CallbackAPIVersion
    paho_mqtt.client = client_mod
    paho.mqtt = paho_mqtt

    ps = types.ModuleType("psutil")
    ps.virtual_memory = lambda: types.SimpleNamespace(total=4 * 1024 ** 3)
    ps.cpu_count = lambda logical=True: 4

    sys.modules.update({
        "paho": paho,
        "paho.mqtt": paho_mqtt,
        "paho.mqtt.client": client_mod,
        "psutil": ps,
    })


def load_module():
    _install_stubs()
    spec = importlib.util.spec_from_file_location("rpi_monitor", HERE / "rpi_monitor.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# 与 Android TopicNames.sanitize 的对照表（改任何一边都要同步这里）
SANITIZE_CASES = [
    ("home", "home"),
    ("RaspPi5", "RaspPi5"),
    ("user@example.com", "user_example_com"),
    ("RPi 5", "RPi_5"),
    ("a.b:c@d e", "a_b_c_d_e"),
    ("x/y+z#", "xyz"),
    ("", "unknown"),
    (None, "unknown"),
    ("中文租户", "unknown"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", default="home", help="租户名")
    ap.add_argument("--device", default="666feb85d43410b2", help="设备 device_id")
    args = ap.parse_args()

    m = load_module()
    m.MQTT_TENANT = args.tenant
    m.DEVICE_ID = args.device

    failures = []

    def check(name, actual, expected):
        ok = actual == expected
        print(f"  {'OK ' if ok else 'ERR'} {name}: {actual!r}"
              + ("" if ok else f" != 期望 {expected!r}"))
        if not ok:
            failures.append(name)

    print("== 1. sanitize_segment（必须与 Android TopicNames 一致）==")
    for raw, want in SANITIZE_CASES:
        check(f"sanitize({raw!r})", m.sanitize_segment(raw), want)

    base = f"walle/{args.tenant}/{args.device}"
    print("\n== 2. 主题拼装 ==")
    for const, resource in [
        (m.RES_ONLINE, "online"),
        (m.RES_ALARM, "alarm"),
        (m.RES_DISCOVERY_RESULT, "discovery/result"),
        (m.RES_STATE_RESULT, "state/result"),
        (m.RES_CMD_RESULT, "cmd/result"),
        (m.RES_CAPABILITY_RESULT, "camera/capability/result"),
        (m.RES_VIDEO_START_RESULT, "video/start/result"),
        (m.RES_VIDEO_STOP_RESULT, "video/stop/result"),
    ]:
        check(f"{const}", const, resource)
        check(f"v2_topic({resource})", m.v2_topic(const), f"{base}/{resource}")

    print("\n== 3. 订阅清单（只含请求类，不含通配）==")
    subs = [t for t, _ in m.subscriptions()]
    for req in (m.REQ_STATE, m.REQ_CMD, m.REQ_CAPABILITY, m.REQ_VIDEO_START, m.REQ_VIDEO_STOP):
        check(f"订阅 {req}", f"{base}/{req}" in subs, True)
    check("订阅 discovery/query", f"walle/{args.tenant}/discovery/query" in subs, True)
    check("不含通配子树", any(s.endswith("/#") for s in subs), False)
    check("订阅数", len(subs), 6)

    print("\n== 4. 命名约定：请求按语义、应答一律 /result ==")
    # 请求命名规则：查询用 /query，命令用 /exec，动作（推流）用 /request
    for name, suffix in [
        ("REQ_DISCOVERY", "/query"),
        ("REQ_STATE", "/query"),
        ("REQ_CAPABILITY", "/query"),
        ("REQ_CMD", "/exec"),
        ("REQ_VIDEO_START", "/request"),
        ("REQ_VIDEO_STOP", "/request"),
    ]:
        value = getattr(m, name, "")
        check(f"{name} 以 {suffix} 结尾", value.endswith(suffix), True)

    # 每个请求都要在 REQUEST_TO_RESULT 里登记
    for name in sorted(n for n in dir(m) if n.startswith("REQ_")):
        check(f"{name} 已登记应答", getattr(m, name) in m.REQUEST_TO_RESULT, True)

    # 应答必须是 /result 结尾，且不能有「没人请求的 /result 主题」
    declared = set(m.REQUEST_TO_RESULT.values())
    for name in sorted(n for n in dir(m) if n.startswith("RES_")):
        value = getattr(m, name)
        if name in ("RES_ONLINE", "RES_ALARM"):
            check(f"{name} 是主动上报（不带 /result）", value.endswith("/result"), False)
            continue
        check(f"{name} 以 /result 结尾", value.endswith("/result"), True)
        check(f"{name} 有对应的请求", value in declared, True)

    print("\n== 5. 请求 -> 处理函数 ==")
    for resource, handler in m.REQUEST_HANDLERS.items():
        check(f"{resource} -> {handler}", callable(getattr(m.RpiMonitor, handler, None)), True)

    print("\n== 6. 发布走 v2 命名空间 ==")

    class FakeInfo:
        mid = 1

    class FakeClient:
        def __init__(self):
            self.sent = []

        def publish(self, topic, payload, qos=0, retain=False):
            self.sent.append((topic, payload, retain))
            return FakeInfo()

    obj = m.RpiMonitor.__new__(m.RpiMonitor)
    obj.client = FakeClient()
    obj.publish_json(m.RES_ALARM, {"device_id": args.device, "event": "alarm"})
    topics = [t for t, _, _ in obj.client.sent]
    check("只发一条", len(topics), 1)
    check("落在命名空间", topics[0], f"{base}/alarm")

    # 命令执行必须回复 cmd/result（并打印应答日志，见 _publish_cmd_result）
    obj.client.sent.clear()
    m.DEVICE_UUID = "uuid-selftest"
    obj.handle_cmd_exec(json.dumps({"uuid": "uuid-selftest", "cmd": "echo hi",
                                    "request_id": "r-selftest"}))
    cmd_topics = [t for t, _, _ in obj.client.sent]
    check("命令回复到 cmd/result", cmd_topics, [f"{base}/cmd/result"])
    cmd_payload = json.loads(obj.client.sent[0][1])
    check("命令结果带 request_id", cmd_payload.get("request_id"), "r-selftest")
    check("命令结果 status", cmd_payload.get("status"), "success")

    print("\n== 7. 所有应答都回显 request_id ==")
    resp = m.RpiMonitor.__new__(m.RpiMonitor)
    resp.client = FakeClient()
    resp.lan_ip = "192.168.1.2"          # _publish_video_* 会写进报文
    m.DEVICE_UUID = "uuid-selftest"
    resp.verify_uuid = lambda payload, topic: (True, json.loads(payload) if payload.strip() else {})
    resp.build_status = lambda: {}
    resp.build_device_info = lambda: {}
    resp.build_capability = lambda: {
        "camera_count": 0,
        "summary": {"total_formats": 0, "probe_cost_ms": 0},
        "probe": {"error": None},
    }

    resp.handle_query_status('{"uuid":"x","request_id":"rid-state"}')
    resp.handle_capability_query('{"uuid":"x","request_id":"rid-cap"}')
    resp.handle_device_query('{"request_id":"rid-disc"}')
    resp._publish_video_result(
        {"video_device": "/dev/video0", "room_number": "123456", "request_id": "rid-vstart"},
        0, "ok")
    resp._publish_video_stop_result("rid-vstop", 0, "ok")

    sent = {t: json.loads(p) for t, p, _ in resp.client.sent}
    for topic, rid in [
        (f"{base}/state/result", "rid-state"),
        (f"{base}/camera/capability/result", "rid-cap"),
        (f"{base}/discovery/result", "rid-disc"),
        (f"{base}/video/start/result", "rid-vstart"),
        (f"{base}/video/stop/result", "rid-vstop"),
    ]:
        check(f"{topic.split('/')[-2]}/{topic.split('/')[-1]} 回显 rid", sent.get(topic, {}).get("request_id"), rid)

    # 回归：视频请求曾经的 bug —— 重建 req 字典时漏掉 request_id，
    # 导致应答里 request_id 为空、App 严格配对后把结果丢弃（表现为「一直转圈」）。
    req = resp._video_req({"request_id": "rid-req"}, "/dev/video0", "123456")
    check("_video_req 保留 request_id", req.get("request_id"), "rid-req")

    resp.client.sent.clear()
    m.VIDEO_ENABLE = False          # 走「推流已关闭」的早退分支，避免真的启动 ffmpeg
    try:
        resp.handle_video_request(
            '{"uuid":"x","video_device":"/dev/video0","room_number":"123456",'
            '"request_id":"rid-video"}')
    finally:
        m.VIDEO_ENABLE = True
    sent_video = {t: json.loads(p) for t, p, _ in resp.client.sent}
    check("视频请求应答回显 rid", sent_video.get(f"{base}/video/start/result", {}).get("request_id"),
          "rid-video")

    print("\n== 8. 请求路由（含租户级 discovery）==")
    routed = []

    class InlinePool:
        def submit(self, fn, *a):
            fn(*a)

    routed_obj = m.RpiMonitor.__new__(m.RpiMonitor)
    routed_obj.pool = InlinePool()
    routed_obj.handle_device_query = lambda p: routed.append(("discovery", p))
    routed_obj.handle_query_status = lambda p: routed.append(("state", p))
    routed_obj.handle_cmd_exec = lambda p: routed.append(("cmd", p))
    routed_obj.handle_video_request = lambda p: routed.append(("video", p))

    # 关键回归：发现广播只有 3 段 walle/{t}/discovery/query，不应被当成 device="discovery"
    routed_obj._handle_request(f"walle/{args.tenant}/discovery/query", "{}")
    check("discovery 被路由到 handle_device_query", routed[-1][0], "discovery")

    routed_obj._handle_request(f"{base}/state/query", '{"uuid":"x"}')
    check("state/query 被路由", routed[-1][0], "state")
    routed_obj._handle_request(f"{base}/cmd/exec", '{"uuid":"x"}')
    check("cmd/exec 被路由", routed[-1][0], "cmd")
    routed_obj._handle_request(f"{base}/video/start/request", '{"uuid":"x"}')
    check("video/start/request 被路由", routed[-1][0], "video")

    print("\n== 9. v1 常量已删除 ==")
    for legacy in ("TOPIC_ALARM", "TOPIC_ONLINE", "TOPIC_CMD_EXEC", "TOPIC_SCHEME",
                   "V1_TO_V2_RESOURCE", "V2_PROTO"):
        check(f"无 {legacy}", hasattr(m, legacy), False)

    print()
    if failures:
        print(f"失败 {len(failures)} 项：{failures}")
        return 1
    print("全部通过：协议 v2 契约与 Android 端一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
