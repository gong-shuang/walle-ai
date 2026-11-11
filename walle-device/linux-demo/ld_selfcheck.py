#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""部署前自检辅助：Python 依赖、协议配对与可选的 MQTT 连通性验证。"""
import importlib.util
import secrets
import socket
import ssl
import threading

import paho.mqtt.client as mqtt

import ld_config as config
from ld_protocol import (REQUEST_HANDLERS, REQUEST_TO_RESULT, REQ_DISCOVERY,
                         device_topic_base, sanitize_segment)

PYTHON_DEPS = (
    ("paho.mqtt", "paho-mqtt", "MQTT 连接"),
    ("psutil", "psutil", "系统监控"),
    ("aiortc", "aiortc", "手机麦上行（WHEP 播放器）"),
    ("sounddevice", "sounddevice", "手机麦上行（声卡输出）"),
)
FATAL_PY_DEPS = ("paho.mqtt", "psutil")


def python_dependency_status():
    """逐项检查 Python 包是否可导入，返回 [{module, package, ok, impact}]。"""
    status = []
    for module, package, impact in PYTHON_DEPS:
        try:
            ok = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            ok = False
        status.append({"module": module, "package": package, "ok": ok, "impact": impact})
    return status


def python_dependency_errors(status):
    """paho/psutil 缺失属于致命错误；aiortc/sounddevice 仅影响手机音频。"""
    errors = [f"缺少 Python 依赖 {item['package']}（{item['impact']}）"
              for item in status if not item["ok"] and item["module"] in FATAL_PY_DEPS]
    warnings = [f"缺少 Python 依赖 {item['package']}（{item['impact']}）"
                for item in status if not item["ok"] and item["module"] not in FATAL_PY_DEPS]
    return errors, warnings


def topic_pair_errors():
    """请求/应答主题配对自检：任何改名或漏配都应在这里报错。"""
    errors = []
    for req, res in REQUEST_TO_RESULT.items():
        if not res.endswith("/result"):
            errors.append(f"应答主题 {res} 未以 /result 结尾")
        if req != REQ_DISCOVERY and req not in REQUEST_HANDLERS:
            errors.append(f"请求主题 {req} 没有处理函数")
    for req in REQUEST_HANDLERS:
        if req not in REQUEST_TO_RESULT:
            errors.append(f"请求主题 {req} 缺少应答映射")
    return errors


def check_connect(timeout=5.0):
    """DNS/TCP/TLS/MQTT 登录/订阅回执/发布回环验证。返回是否全部成功。

    回环验证用一次性 nonce：订阅并发布到本设备账号可访问的临时主题
    ``walle/{tenant}/{device_id}-check/v1/selfcheck``，收到回投说明
    「账号 + ACL（publish/subscribe）+ Broker 转发」整条链路可用。

    临时 clientId 为 ``<device_id>-check``：不会把正在运行的服务踢下线；
    主题按该 clientId 生成，与 service_deploy.md §10 的
    ``walle/+/${clientid}/#`` ACL 规则一致。
    """
    host, port = config.MQTT_HOST, config.MQTT_PORT
    print("\n== 连接检查 ==")
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        print(f"  DNS 解析：{host} -> {len(infos)} 个地址")
    except OSError as exc:
        print(f"  DNS 解析失败：{exc}")
        return False

    tls_ctx = None
    if config.MQTT_TLS == "on":
        try:
            tls_ctx = ssl.create_default_context(cafile=config.MQTT_CA_FILE or None)
        except OSError as exc:
            print(f"  加载 CA 失败：{exc}")
            return False

    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            print(f"  TCP 连接：成功 {host}:{port}")
            if tls_ctx is not None:
                with tls_ctx.wrap_socket(sock, server_hostname=host) as tls:
                    print(f"  TLS：{tls.version()} / {tls.cipher()[0]}")
    except (OSError, ssl.SSLError) as exc:
        print(f"  TCP/TLS 失败：{exc}")
        return False

    check_device = f"{sanitize_segment(config.DEVICE_ID)}-check"
    check_topic = f"{device_topic_base(device=check_device)}/selfcheck"
    nonce = secrets.token_hex(8)
    connected = threading.Event()
    subacked = threading.Event()
    pubacked = threading.Event()
    received = threading.Event()
    state = {}

    def _on_connect(client, userdata, flags, reason_code, properties):
        state["connack"] = reason_code
        connected.set()

    def _on_subscribe(client, userdata, mid, reason_codes, properties):
        state["suback"] = reason_codes[0] if reason_codes else None
        subacked.set()

    def _on_publish(client, userdata, mid, reason_code, properties):
        state["puback"] = reason_code
        pubacked.set()

    def _on_message(client, userdata, msg):
        if (msg.topic == check_topic
                and nonce in msg.payload.decode("utf-8", "replace")):
            received.set()

    # 独立 clientId：避免把正在运行的服务踢下线
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id=check_device, clean_session=True)
    client.username_pw_set(config.MQTT_USER, config.MQTT_PASSWORD)
    if tls_ctx is not None:
        client.tls_set_context(tls_ctx)
    client.on_connect = _on_connect
    client.on_subscribe = _on_subscribe
    client.on_publish = _on_publish
    client.on_message = _on_message
    try:
        client.connect_async(host, port, config.MQTT_KEEPALIVE)
        client.loop_start()
        if not connected.wait(timeout):
            print("  MQTT 登录：超时未收到 CONNACK")
            return False
        rc = state.get("connack")
        if rc != 0:
            print(f"  MQTT 登录失败：{rc}（检查用户名/密码）")
            return False
        print("  MQTT 登录：成功")

        sub_rc, _mid = client.subscribe(check_topic, qos=config.MQTT_QOS)
        if sub_rc != mqtt.MQTT_ERR_SUCCESS:
            print(f"  订阅请求：失败（{sub_rc}）")
            return False
        if not subacked.wait(timeout):
            print(f"  订阅回执：超时（检查 ACL 是否允许订阅 {check_topic}）")
            return False
        granted = state.get("suback")
        if getattr(granted, "is_failure", False):
            print(f"  订阅回执：被拒绝（{granted}，检查 ACL 与授权源顺序）")
            return False
        print(f"  订阅回执：成功 {check_topic}（QoS={granted}）")

        info = client.publish(check_topic, nonce, qos=config.MQTT_QOS)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            print(f"  发布请求：失败（{info.rc}）")
            return False
        if not pubacked.wait(timeout):
            print("  发布回执：超时（检查 ACL 是否允许发布，或 Broker 未响应）")
            return False
        pub_rc = state.get("puback")
        if getattr(pub_rc, "is_failure", False):
            print(f"  发布回执：被拒绝（{pub_rc}，检查 ACL 与授权源顺序）")
            return False
        print("  发布回执：成功（QoS1 PUBACK）")

        if not received.wait(timeout):
            print("  回环消息：未收到（订阅可能被 ACL 拒绝；"
                  "EMQX deny_action=ignore 时 SUBACK 仍可能显示成功）")
            return False
        print("  回环消息：成功（发布 -> Broker -> 订阅）")
        return True
    except OSError as exc:
        print(f"  MQTT 连接失败：{exc}")
        return False
    finally:
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:  # noqa: BLE001
            pass
