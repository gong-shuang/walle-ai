#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""car/motor/drive 持续控制命令：报文校验与结果封装。

协议约定见 doc/MQTT_protocol_convention.md §4.10：
  - move 四个方向只允许一个非 0，取值 0 或 [0.5, 1.0] m/s；
  - rotate 只接受布尔值，left/right 互斥；都为 false 表示停止旋转；
  - 同时旋转时，线速度上限压到 1.0 - k*2.5 = 0.653 m/s，超出按比例缩放；
  - 结果 0 成功 / 1 命令无效 / 2 命令错误 / 3 底盘控制链路不可用。
"""

import logging
import math

from . import config
from .identity import now_str, now_ts

RESULT_OK = 0
RESULT_INVALID = 1
RESULT_ERROR = 2
RESULT_LINK_DOWN = 3

CAUSE_OK = "ok"
CAUSE_INVALID = "命令无效"
CAUSE_ERROR = "命令错误"
CAUSE_LINK_DOWN = "底盘控制链路不可用"

_MISSING = object()
_MOVE_KEYS = ("forward", "back", "left", "right")


def _number(value):
    """把 JSON 数值转成 float；bool/字符串/NaN/Inf 都视为命令错误。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("不是数值")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("不是有限数值")
    return value


def parse_drive(data):
    """校验并归一化一条 car/motor/drive 报文。

    返回 (result, cause, twist)：
      - twist 为 (linear_x, linear_y, angular_z)，仅 result=0 时非 None；
      - 缺失 move/rotate 整个对象 -> result=1（命令无效）；
      - 字段类型/取值/互斥不合法 -> result=2（命令错误）。
    """
    if not isinstance(data, dict):
        return RESULT_INVALID, CAUSE_INVALID, None

    move = data.get("move", _MISSING)
    rotate = data.get("rotate", _MISSING)
    if move is _MISSING or rotate is _MISSING:
        return RESULT_INVALID, CAUSE_INVALID, None
    if not isinstance(move, dict) or not isinstance(rotate, dict):
        return RESULT_ERROR, CAUSE_ERROR, None

    try:
        values = {}
        for key in _MOVE_KEYS:
            raw = move.get(key, 0.0)
            value = _number(raw)
            if value != 0.0 and not (
                    config.CAR_MOTOR_LINEAR_MIN <= value <= config.CAR_MOTOR_LINEAR_MAX):
                return RESULT_ERROR, CAUSE_ERROR, None
            values[key] = value
    except ValueError:
        return RESULT_ERROR, CAUSE_ERROR, None

    if sum(1 for value in values.values() if value != 0.0) > 1:
        return RESULT_ERROR, CAUSE_ERROR, None

    linear_x = values["forward"] - values["back"]
    linear_y = values["left"] - values["right"]

    left = rotate.get("left", False)
    right = rotate.get("right", False)
    if not isinstance(left, bool) or not isinstance(right, bool):
        return RESULT_ERROR, CAUSE_ERROR, None
    if left and right:
        return RESULT_ERROR, CAUSE_ERROR, None

    if left:
        angular_z = config.CAR_MOTOR_ANGULAR_SPEED
    elif right:
        angular_z = -config.CAR_MOTOR_ANGULAR_SPEED
    else:
        angular_z = 0.0

    # 麦轮轮速预算：|linear| + k*|angular| <= 1.0。
    # 协议固定角速度 2.5，所以旋转时线速度上限为 0.653，超出时等比缩放。
    linear = math.hypot(linear_x, linear_y)
    if angular_z != 0.0 and linear > config.CAR_MOTOR_LINEAR_LIMIT_WITH_ROTATE:
        scale = config.CAR_MOTOR_LINEAR_LIMIT_WITH_ROTATE / linear
        linear_x *= scale
        linear_y *= scale

    return RESULT_OK, CAUSE_OK, (linear_x, linear_y, angular_z)


class CarMotorMixin:
    """结果封装 + 非 ROS 环境的兜底处理。

    真正的 /cmd_vel 发布、10Hz 重发和 500ms 死区在 node.py 中覆盖
    handle_car_motor_drive；独立运行（linux-demo）只返回链路不可用。
    """

    def _publish_car_motor_result(self, request_id, result_code, cause):
        payload = {
            "device_id": config.DEVICE_ID,
            "uuid": config.DEVICE_UUID,
            "hostname": config.HOSTNAME,
            "ip": self.lan_ip,
            "request_id": str(request_id or ""),
            "result": result_code,
            "cause": cause,
            "timestamp": now_ts(),
            "datetime": now_str(),
        }
        self.publish_json(config.RES_CAR_MOTOR_RESULT, payload)
        if result_code == RESULT_OK:
            logging.debug("car/motor/drive 结果 -> %s: request_id=%s result=0 cause=ok",
                          config.device_topic(config.RES_CAR_MOTOR_RESULT),
                          payload["request_id"] or "-")
        else:
            logging.warning("car/motor/drive 结果 -> %s: request_id=%s result=%s cause=%s",
                            config.device_topic(config.RES_CAR_MOTOR_RESULT),
                            payload["request_id"] or "-", result_code, cause)
        return payload

    def handle_car_motor_drive(self, payload):
        """独立运行时没有 /cmd_vel 发布者，直接返回链路不可用。"""
        ok, data = self.verify_uuid(payload, config.REQ_CAR_MOTOR_DRIVE)
        if not ok:
            return
        self._publish_car_motor_result(
            self._rid(data), RESULT_LINK_DOWN, CAUSE_LINK_DOWN)
