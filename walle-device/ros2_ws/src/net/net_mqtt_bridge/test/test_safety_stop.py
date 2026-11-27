#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M0-08：设备侧安全停止回归（500ms 死区 / 障碍 -8 立即停车）。

不启动 ROS 节点，直接调用 NetMqttBridge 的纯逻辑方法，避免依赖话题与硬件。
"""

import threading
import time
import types

import net_mqtt_bridge.agent as agent_mod
import net_mqtt_bridge.car_motor as car_motor
from net_mqtt_bridge.node import NetMqttBridge


class _Logger:
    def __init__(self):
        self.warnings = []

    def warn(self, message):
        self.warnings.append(message)

    def info(self, message):
        pass

    def debug(self, message):
        pass


def _bare_bridge():
    bridge = object.__new__(NetMqttBridge)
    bridge._shutting_down = False
    bridge._car_lock = threading.Lock()
    bridge._car_state = (0.0, 0.0, 0.0)
    bridge._car_last_valid = 0.0
    bridge._car_moving = False
    bridge._agent_mod = agent_mod
    bridge.agent = types.SimpleNamespace(motor_moving=False)
    bridge._logger = _Logger()
    bridge._published = []
    bridge._cmd_vel_pub = types.SimpleNamespace(get_subscription_count=lambda: 1)
    bridge._publish_cmd_vel = lambda x, y, z: bridge._published.append((x, y, z))
    bridge.get_logger = lambda: bridge._logger
    return bridge


def test_deadman_stops_after_500ms_without_command():
    """agent 断线后超过 500ms 无新命令：设备自动发布零速。"""
    bridge = _bare_bridge()
    bridge._car_moving = True
    bridge.agent.motor_moving = True
    bridge._car_state = (0.6, 0.0, 0.0)
    bridge._car_last_valid = (
        time.monotonic() - agent_mod.CAR_MOTOR_DEADMAN_SEC - 0.05)

    bridge._car_tick()

    assert bridge._published == [(0.0, 0.0, 0.0)]
    assert bridge._car_moving is False
    assert bridge.agent.motor_moving is False
    assert bridge._car_state == (0.0, 0.0, 0.0)
    assert bridge._logger.warnings


def test_deadman_republishes_before_timeout():
    """未超过 500ms 时继续重发当前速度，不误停车。"""
    bridge = _bare_bridge()
    bridge._car_moving = True
    bridge.agent.motor_moving = True
    bridge._car_state = (0.6, 0.0, 0.0)
    bridge._car_last_valid = time.monotonic()

    bridge._car_tick()

    assert bridge._published == [(0.6, 0.0, 0.0)]
    assert bridge._car_moving is True
    assert bridge._car_state == (0.6, 0.0, 0.0)


def test_obstacle_minus_8_stops_car_immediately():
    """前方障碍导致 -8：不再下发前进速度，立即发布零速。"""
    bridge = _bare_bridge()
    bridge._car_moving = True
    bridge.agent.motor_moving = True
    bridge._car_state = (0.6, 0.0, 0.0)
    bridge._car_last_valid = time.monotonic()
    bridge.results = []

    bridge.agent.parse_request = lambda payload, topic: payload
    bridge.agent._rid = lambda data: data.get("request_id")
    bridge.agent.forward_blocked = lambda: True
    bridge.agent._publish_car_motor_result = (
        lambda rid, result, cause, twist=None:
        bridge.results.append((rid, result, cause, twist)))

    bridge._handle_car_motor_set({
        "request_id": "m1",
        "move": {"forward": 0.6, "back": 0.0, "left": 0.0, "right": 0.0},
        "rotate": {"left": False, "right": False},
    })

    assert bridge.results == [(
        "m1", car_motor.RESULT_OBSTACLE, car_motor.CAUSE_OBSTACLE, None)]
    assert bridge._published == [(0.0, 0.0, 0.0)]
    assert bridge._car_moving is False
    assert bridge.agent.motor_moving is False
