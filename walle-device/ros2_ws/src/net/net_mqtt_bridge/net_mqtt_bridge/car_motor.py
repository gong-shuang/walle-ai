#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""car/motor/set/cmd 持续控制命令：报文校验与结果码。

协议约定见 doc/MQTT_protocol_convention.md §4.10（该主题为持续控制，无 MQTT 应答）：
  - move 四个方向只允许一个非 0，取值 0 或 [0.5, 1.0] m/s；
  - rotate 只接受布尔值，left/right 互斥；都为 false 表示停止旋转；
  - 同时旋转时，线速度上限压到 1.0 - k*2.5 = 0.653 m/s，超出按比例缩放；
  - 前方有障碍（声呐有效距离 ≤ 阈值）时只允许后退/转向，forward 非 0 → 拒绝执行；
  - 结果码 0 成功 / 1 命令无效 / 2 命令错误 / 3 底盘控制链路不可用 / 4 前方有障碍。
"""

import logging
import math

from . import config

RESULT_OK = 0
RESULT_INVALID = 1
RESULT_ERROR = 2
RESULT_LINK_DOWN = 3
RESULT_OBSTACLE = 4

CAUSE_OK = "ok"
CAUSE_INVALID = "命令无效"
CAUSE_ERROR = "命令错误"
CAUSE_LINK_DOWN = "底盘控制链路不可用"
CAUSE_OBSTACLE = "前方有障碍物，不可前进"

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


def parse_drive(data, forward_blocked=False):
    """校验并归一化一条 car/motor/set/cmd 报文。

    返回 (result, cause, twist)：
      - twist 为 (linear_x, linear_y, angular_z)，仅 result=0 时非 None；
      - 缺失 move/rotate 整个对象 -> result=1（命令无效）；
      - 字段类型/取值/互斥不合法 -> result=2（命令错误）；
      - forward_blocked=True 且命令要求前进 -> result=4（前方有障碍）。
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

    # 前方障碍联锁：只拦前进（linear_x > 0），后退/转向/横移仍允许
    if forward_blocked and linear_x > 0:
        return RESULT_OBSTACLE, CAUSE_OBSTACLE, None

    return RESULT_OK, CAUSE_OK, (linear_x, linear_y, angular_z)


class CarMotorMixin:
    """非 ROS 环境的兜底处理。

    真正的 /cmd_vel 发布、10Hz 重发和 500ms 死区在 node.py 中覆盖
    handle_car_motor_set；独立运行（linux-demo）只记录链路不可用。
    该主题是持续控制命令，按协议不发布 MQTT 应答。
    """

    def handle_car_motor_set(self, payload):
        """独立运行时没有 /cmd_vel 发布者，只记录链路不可用。"""
        data = self.parse_request(payload, config.REQ_CAR_MOTOR_SET)
        if data is None:
            return
        logging.warning(
            "car/motor/set/cmd request_id=%s 未执行：底盘控制链路不可用（独立运行模式）",
            self._rid(data) or "-")
