#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""部署前自检辅助：Python 依赖、协议配对与可选的 MQTT 连通性验证。"""
import importlib.util
import socket
import ssl
import threading

import paho.mqtt.client as mqtt

import ld_config as config
from ld_protocol import REQUEST_HANDLERS, REQUEST_TO_RESULT, REQ_DISCOVERY

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
    """DNS/TCP/TLS/MQTT CONNECT 验证；不订阅、不发布。返回是否成功。"""
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

    connected = threading.Event()
    result = {}

    def _on_connect(client, userdata, flags, reason_code, properties):
        result["rc"] = reason_code
        connected.set()

    # 独立 clientId：避免把正在运行的服务踢下线（服务在跑时请先 stop 再执行 --connect）
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id=f"{config.DEVICE_ID}-check", clean_session=True)
    client.username_pw_set(config.MQTT_USER, config.MQTT_PASSWORD)
    if tls_ctx is not None:
        client.tls_set_context(tls_ctx)
    client.on_connect = _on_connect
    try:
        client.connect_async(host, port, config.MQTT_KEEPALIVE)
        client.loop_start()
        if not connected.wait(timeout):
            print("  MQTT 登录：超时未收到 CONNACK")
            return False
        rc = result.get("rc")
        if rc != 0:
            print(f"  MQTT 登录失败：{rc}")
            return False
        print("  MQTT 登录：成功（未订阅、未发布）")
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
