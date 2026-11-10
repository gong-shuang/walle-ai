#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地请求注入：模拟 App 向设备发送请求，便于没有手机时联调。

默认只发只读查询（state/query、capability/query），不会触发推流或播放；
video/* 与 phone/audio/* 动作必须显式用 --action 指定。--dry-run 只打印
主题与报文，不连接 Broker；因此可以先确认报文再实际发送。

示例：
    # 只打印主题与报文，不连接
    python3 inject_request.py --dry-run --action video/start --room-number demo
    # 查询状态与能力，并打印设备返回的结果
    python3 inject_request.py
    # 触发推流（会真正占用摄像头），用完手动停止
    python3 inject_request.py --action video/start --room-number demo
    python3 inject_request.py --action video/stop

设备 uuid 默认从 retained `online` 读取（要求设备在线）；也可 --uuid 指定。
严格 ACL 环境下请改用 App 账号：--username home --password '***'（服务端 ACL
按 `walle/{tenant}/#` 授权，设备账号只能访问自己 clientId 的子树）。

配置与 main.py 相同：自动加载 /etc/walle-ai.conf 或同级 walle-ai.conf，
也可用 --env-file 指定；已存在的环境变量优先。
"""
import argparse
import json
import secrets
import ssl
import sys
import threading

import psutil
import paho.mqtt.client as mqtt

import ld_config as config
from ld_config import *          # noqa: F401,F403
from ld_protocol import *        # noqa: F401,F403
from ld_utils import *           # noqa: F401,F403

ACTION_MAP = {
    "state/query": REQ_STATE,
    "capability/query": REQ_CAPABILITY,
    "video/start": REQ_VIDEO_START,
    "video/stop": REQ_VIDEO_STOP,
    "phone/audio/send": REQ_PHONE_AUDIO_SEND,
    "phone/audio/stop": REQ_PHONE_AUDIO_STOP,
}
ACTION_CHOICES = tuple(ACTION_MAP)
DEFAULT_ACTIONS = ("state/query", "capability/query")
# 这些动作会真正占用摄像头/声卡，必须显式指定
DEVICE_ACTIONS = (REQ_VIDEO_START, REQ_PHONE_AUDIO_SEND)


def _new_rid():
    return f"inject-{secrets.token_hex(4)}"


def build_requests(actions, uuid, room_number="", video_device="", request_id=""):
    """构造 [(请求主题, 报文, 应答主题)]；uuid 为空时用占位符（dry-run/预览）。"""
    items = []
    for action in actions:
        payload = {"uuid": uuid or "<device_uuid>",
                   "request_id": request_id or _new_rid()}
        if action == REQ_VIDEO_START:
            payload.update({"video_device": video_device, "room_number": room_number})
        elif action == REQ_PHONE_AUDIO_SEND:
            payload["room_number"] = room_number
        elif action in (REQ_VIDEO_STOP, REQ_PHONE_AUDIO_STOP) and room_number:
            payload["room_number"] = room_number
        items.append((device_topic(action), payload,
                      device_topic(REQUEST_TO_RESULT[action])))
    return items


def print_requests(items):
    for topic, payload, result_topic in items:
        print(f"[请求] {topic}")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print(f"[应答] {result_topic}")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="模拟 App 向 linux-demo 设备发送 MQTT 请求（默认只读查询）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    parser.add_argument("--action", action="append", choices=ACTION_CHOICES,
                        help="可重复；默认只发 state/query 与 capability/query")
    parser.add_argument("--room-number", default="", help="房间号（video/start、phone/audio/send 必填）")
    parser.add_argument("--video-device", default="",
                        help="摄像头节点（默认 CAMERA_DEVICE 或 /dev/video0）")
    parser.add_argument("--device-id", default="", help="目标设备号（默认按本机硬件指纹计算）")
    parser.add_argument("--uuid", default="", help="设备 uuid（默认从 retained online 读取）")
    parser.add_argument("--request-id", default="", help="请求 id（默认自动生成）")
    parser.add_argument("--username", default=config.MQTT_USER, help="覆盖 MQTT 用户名（如 App 账号）")
    parser.add_argument("--password", default=config.MQTT_PASSWORD, help="覆盖 MQTT 密码")
    parser.add_argument("--watch", type=float, default=5.0, help="发布后等待并打印结果的秒数（0=立即退出）")
    parser.add_argument("--timeout", type=float, default=5.0, help="连接/读取 uuid 的超时秒数")
    parser.add_argument("--env-file", default=None, help="配置文件路径（与 main.py 相同）")
    parser.add_argument("--dry-run", action="store_true", help="只打印主题与报文，不连接 Broker")
    return parser.parse_args(argv)


def _resolve_device_id(explicit):
    if explicit:
        return explicit
    mac = pick_primary_mac(get_lan_ip())
    device_id, _source = resolve_device_id(
        mac, get_cpu_core_count(), psutil.virtual_memory().total, persist=False)
    return device_id


def _make_client(username, password):
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id=f"inject-{secrets.token_hex(4)}", clean_session=True)
    client.username_pw_set(username, password)
    if config.MQTT_TLS == "on":
        context = ssl.create_default_context(cafile=config.MQTT_CA_FILE or None)
        client.tls_set_context(context)
    return client


def _connect(client, timeout):
    connected = threading.Event()
    state = {}

    def _on_connect(_client, _userdata, _flags, reason_code, _properties):
        state["rc"] = reason_code
        connected.set()

    client.on_connect = _on_connect
    client.connect_async(config.MQTT_HOST, config.MQTT_PORT, config.MQTT_KEEPALIVE)
    client.loop_start()
    if not connected.wait(timeout):
        raise TimeoutError("连接 MQTT 超时")
    rc = state.get("rc")
    if rc != 0:
        raise ConnectionError(f"MQTT 登录失败：{rc}（检查用户名/密码，或用 --username/--password）")


def _fetch_uuid(client, timeout):
    """从 retained online 报文读取设备 uuid（设备需在线过一次）。"""
    topic = device_topic(RES_ONLINE)
    got = threading.Event()
    box = {}

    def _on_message(_client, _userdata, msg):
        if msg.topic != topic:
            return
        try:
            data = json.loads(msg.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if isinstance(data, dict) and data.get("uuid"):
            box["uuid"] = str(data["uuid"])
            got.set()

    client.on_message = _on_message
    client.subscribe(topic, qos=MQTT_QOS)
    if not got.wait(timeout):
        raise TimeoutError(
            f"{timeout:g}s 内未从 {topic} 读到 uuid；"
            "请确认设备在线，或用 --uuid 显式指定")
    client.unsubscribe(topic)
    return box["uuid"]


def main(argv=None):
    args = parse_args(argv)
    actions = [ACTION_MAP[name] for name in (args.action or DEFAULT_ACTIONS)]

    if not MQTT_TENANT:
        print("错误：MQTT_TENANT 未配置", file=sys.stderr)
        return 2
    if not args.dry_run:
        missing = [name for name, value in (
            ("MQTT_HOST", config.MQTT_HOST), ("MQTT_PORT", config.MQTT_PORT),
            ("MQTT_USERNAME/--username", args.username),
            ("MQTT_PASSWORD/--password", args.password)) if not value]
        if missing:
            print(f"错误：缺少 {', '.join(missing)}", file=sys.stderr)
            return 2

    config.DEVICE_ID = _resolve_device_id(args.device_id)
    video_device = args.video_device or CAMERA_DEVICE or "/dev/video0"
    need_room = [name for name, topic in ACTION_MAP.items()
                 if topic in actions and topic in DEVICE_ACTIONS]
    if need_room and not args.room_number:
        print(f"错误：{', '.join(need_room)} 需要 --room-number", file=sys.stderr)
        return 2

    if args.dry_run:
        print_requests(build_requests(actions, args.uuid, args.room_number,
                                      video_device, args.request_id))
        return 0

    print(f"目标设备：{device_topic_base()}  uuid={args.uuid or '（从 online 读取）'}")
    client = _make_client(args.username, args.password)
    received = {}
    try:
        _connect(client, args.timeout)
        uuid = args.uuid or _fetch_uuid(client, args.timeout)
        items = build_requests(actions, uuid, args.room_number,
                               video_device, args.request_id)
        expected = {payload["request_id"]: result_topic
                    for _topic, payload, result_topic in items}
        result_topics = set(expected.values())

        def _on_message(_client, _userdata, msg):
            if msg.topic not in result_topics:
                return
            text = msg.payload.decode("utf-8", "replace")
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = {}
            rid = str(data.get("request_id") or "")
            if rid in expected:
                received[rid] = text
            print(f"\n[结果] {msg.topic}")
            print(text)

        client.on_message = _on_message
        for result_topic in result_topics:
            client.subscribe(result_topic, qos=MQTT_QOS)

        for topic, payload, result_topic in items:
            print(f"\n[发送] {topic}")
            print(json.dumps(payload, ensure_ascii=False))
            info = client.publish(topic, json.dumps(payload, ensure_ascii=False),
                                  qos=MQTT_QOS)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                print(f"  发布失败：rc={info.rc}", file=sys.stderr)
                return 1

        if args.watch <= 0:
            print("\n已发送（--watch 0，不等待结果）")
            return 0
        threading.Event().wait(args.watch)
        missing = [rid for rid in expected if rid not in received]
        if missing:
            print(f"\n未收到结果：{', '.join(missing)}"
                  "（设备离线、uuid 不匹配或 ACL 拒绝）", file=sys.stderr)
            return 1
        print("\n全部请求均收到结果")
        return 0
    except (OSError, TimeoutError, ConnectionError, ssl.SSLError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    finally:
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    sys.exit(main())
