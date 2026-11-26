#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MQTT 主题命名、订阅清单与请求/应答映射。"""
import hashlib

import ld_config as config
from ld_config import *

def _is_ascii_alnum(ch):
    return ("a" <= ch <= "z") or ("A" <= ch <= "Z") or ("0" <= ch <= "9")


def sanitize_segment(raw):
    """主题段清洗：必须与 Android 端 TopicNames.sanitize 完全一致。

    只保留 [A-Za-z0-9_-]；`.` `:` `@` 空格 → `_`；其余字符丢弃；空值 → unknown；
    超过 64 字符时保留前 40 位 + 短哈希（正常配置不会触发）。
    """
    if raw is None:
        return "unknown"
    raw = str(raw)
    out = []
    for ch in raw:
        if _is_ascii_alnum(ch) or ch in "_-":
            out.append(ch)
        elif ch in ".:@ ":
            out.append("_")
    seg = "".join(out)
    if not seg:
        return "unknown"
    if len(raw) > 64:
        return seg[:40] + "-" + hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]
    return seg


def device_topic_base(tenant=None, device=None):
    """walle/{tenant}/device/{device}"""
    t = sanitize_segment(config.MQTT_TENANT if tenant is None else tenant)
    d = sanitize_segment(config.DEVICE_ID if device is None else device)
    return f"{TOPIC_ROOT}/{t}/{DEVICE_SEGMENT}/{d}"


def device_topic(resource):
    return device_topic_base() + "/" + resource


def tenant_topic(suffix):
    return f"{TOPIC_ROOT}/{sanitize_segment(config.MQTT_TENANT)}/{suffix}"


def client_online_topic(client_id):
    """walle/{tenant}/client/{client_id}/online（客户端在线状态）。"""
    return (f"{TOPIC_ROOT}/{sanitize_segment(config.MQTT_TENANT)}/"
            f"{CLIENT_SEGMENT}/{sanitize_segment(client_id)}/online")


def agent_online_topic(agent_id):
    """walle/{tenant}/agent/{agent_id}/online（agent 在线状态）。"""
    return (f"{TOPIC_ROOT}/{sanitize_segment(config.MQTT_TENANT)}/"
            f"{AGENT_SEGMENT}/{sanitize_segment(agent_id)}/online")


# 请求 → 应答 的对应关系（也用于自检，保证命名成对）
REQUEST_TO_RESULT = {
    REQ_DISCOVERY: RES_DISCOVERY_RESULT,
    REQ_STATE: RES_STATE_RESULT,
    REQ_CAPABILITY: RES_CAPABILITY_RESULT,
    REQ_VIDEO_START: RES_VIDEO_START_RESULT,
    REQ_VIDEO_STOP: RES_VIDEO_STOP_RESULT,
    REQ_PHONE_AUDIO_SEND: RES_PHONE_AUDIO_SEND_RESULT,
    REQ_PHONE_AUDIO_STOP: RES_PHONE_AUDIO_STOP_RESULT,
    REQ_CONTROL_ACQUIRE: RES_CONTROL_ACQUIRE,
    REQ_CONTROL_RELEASE: RES_CONTROL_RELEASE,
}

# 请求资源 → 处理函数名
REQUEST_HANDLERS = {
    REQ_STATE: "handle_query_status",
    REQ_CAPABILITY: "handle_capability_query",
    REQ_VIDEO_START: "handle_video_request",
    REQ_VIDEO_STOP: "handle_video_stop",
    REQ_PHONE_AUDIO_SEND: "handle_phone_audio_send",
    REQ_PHONE_AUDIO_STOP: "handle_phone_audio_stop",
    REQ_CONTROL_ACQUIRE: "handle_control_acquire",
    REQ_CONTROL_RELEASE: "handle_control_release",
}


# 查询类资源走 query_pool，其余动作类走 action_pool；discovery 单独判断
QUERY_RESOURCES = {REQ_STATE, REQ_CAPABILITY}

# 只读查询：始终放行；持有者发送会续租，非持有者不会。
QUERY_REQUESTS = frozenset({REQ_STATE, REQ_CAPABILITY})

# 控制权管理本身不走租约校验（否则无法申请/释放）。
CONTROL_EXEMPT_REQUESTS = frozenset({REQ_CONTROL_ACQUIRE, REQ_CONTROL_RELEASE})


def subscriptions():
    """本设备要订阅的主题（只有请求类/持续控制 + 租户内发现广播）。

    用按层数的 `+` 模式只匹配以 /request 或 /cmd 结尾的资源，不订
    walle/{t}/device/{d}/#：MQTT 3.1.1 会把客户端自己发布的报文也投递回来，
    订全子树会收到自己发的 response/alarm，白白浪费带宽。

    当前层级：3 层（baseState/query/request 等）、4 层（phone/audio/*/request、
    car/motor/set/request 等）、5 层（car/servo/status/query/request）；
    持续控制为 4 层（car/servo/set/cmd）。
    新增不同层数的请求/控制主题时，需要在这里补一条对应模式。
    """
    base = device_topic_base()
    return [
        (f"{base}/+/+/request", MQTT_QOS),       # baseState/query/request、video/start/request、record/*/request
        (f"{base}/+/+/+/request", MQTT_QOS),     # phone/audio/*/request、record/list|play/*/request、car/motor/set/request
        (f"{base}/+/+/+/+/request", MQTT_QOS),   # car/servo/status/query/request
        (f"{base}/+/+/+/cmd", MQTT_QOS),         # car/servo/set/cmd
        (tenant_topic(REQ_DISCOVERY), MQTT_QOS),
        (tenant_topic("client/+/online"), MQTT_QOS),   # App/Web 客户端在线状态
        (tenant_topic("agent/+/online"), MQTT_QOS),    # agent 在线状态
    ]
