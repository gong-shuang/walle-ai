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
    """walle/{tenant}/{device}/v1"""
    t = sanitize_segment(config.MQTT_TENANT if tenant is None else tenant)
    d = sanitize_segment(config.DEVICE_ID if device is None else device)
    return f"{TOPIC_ROOT}/{t}/{d}/{PROTOCOL_VERSION}"


def device_topic(resource):
    return device_topic_base() + "/" + resource


def tenant_topic(suffix):
    return f"{TOPIC_ROOT}/{sanitize_segment(config.MQTT_TENANT)}/{PROTOCOL_VERSION}/{suffix}"


# 请求 → 应答 的对应关系（也用于自检，保证命名成对）
REQUEST_TO_RESULT = {
    REQ_DISCOVERY: RES_DISCOVERY_RESULT,
    REQ_STATE: RES_STATE_RESULT,
    REQ_CAPABILITY: RES_CAPABILITY_RESULT,
    REQ_VIDEO_START: RES_VIDEO_START_RESULT,
    REQ_VIDEO_STOP: RES_VIDEO_STOP_RESULT,
    REQ_PHONE_AUDIO_SEND: RES_PHONE_AUDIO_SEND_RESULT,
    REQ_PHONE_AUDIO_STOP: RES_PHONE_AUDIO_STOP_RESULT,
}

# 请求资源 → 处理函数名
REQUEST_HANDLERS = {
    REQ_STATE: "handle_query_status",
    REQ_CAPABILITY: "handle_capability_query",
    REQ_VIDEO_START: "handle_video_request",
    REQ_VIDEO_STOP: "handle_video_stop",
    REQ_PHONE_AUDIO_SEND: "handle_phone_audio_send",
    REQ_PHONE_AUDIO_STOP: "handle_phone_audio_stop",
}


# 查询类资源走 query_pool，其余动作类走 action_pool；discovery 单独判断
QUERY_RESOURCES = {REQ_STATE, REQ_CAPABILITY}


def subscriptions():
    """本设备要订阅的主题（只有请求类 + 租户内发现广播）。

    不订 walle/{t}/{d}/v1/#：MQTT 3.1.1 会把客户端自己发布的报文也投递回来，
    订全子树会收到自己发的 state/alarm，白白浪费带宽。
    """
    base = device_topic_base()
    return [
        (f"{base}/{REQ_STATE}", MQTT_QOS),
        (f"{base}/{REQ_CAPABILITY}", MQTT_QOS),
        (f"{base}/{REQ_VIDEO_START}", MQTT_QOS),
        (f"{base}/{REQ_VIDEO_STOP}", MQTT_QOS),
        (f"{base}/{REQ_PHONE_AUDIO_SEND}", MQTT_QOS),
        (f"{base}/{REQ_PHONE_AUDIO_STOP}", MQTT_QOS),
        (tenant_topic(REQ_DISCOVERY), MQTT_QOS),
    ]
