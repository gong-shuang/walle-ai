#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""car/servo/* 舵机控制：状态查询与绝对位置设置的校验、应答封装。

协议约定见 doc/MQTT_protocol_convention.md §4.11/§4.12：
  - 状态查询可选 ids（默认 [1,2]）、include_offset（默认 false）；
  - 位置设置 servos[] + duration，越界项只跳过该舵机；
  - 结果 0 成功 / -1 命令无效 / -2 命令错误 / -3 舵机控制链路不可用；
  - 状态查询返回 servos[]；位置设置返回 applied/skipped/duration。
"""

import logging
import threading
import time

from . import config
from .car_motor import CAUSE_ERROR, CAUSE_INVALID, CAUSE_LINK_DOWN, CAUSE_OK
from .identity import now_str, now_ts

# 舵机协议（§4.11/§4.12）结果码，与 car/motor 一致使用负数；
# -4 保留给 car/motor 的前方障碍，舵机控制权拒绝由通用逻辑回执 -5
RESULT_OK = 0
RESULT_INVALID = -1
RESULT_ERROR = -2
RESULT_LINK_DOWN = -3


def parse_status_query(data):
    """校验状态查询报文。

    返回 (result, cause, req)：
      - req = {"ids": [...], "include_offset": bool}，仅 result=0 时非 None；
      - ids 为空 -> result=-1；
      - ids 重复/越界、字段类型错误 -> result=-2。
    """
    if not isinstance(data, dict):
        return RESULT_INVALID, CAUSE_INVALID, None

    ids = data.get("ids", list(config.CAR_SERVO_IDS))
    if not isinstance(ids, list):
        return RESULT_ERROR, CAUSE_ERROR, None
    if not ids:
        return RESULT_INVALID, CAUSE_INVALID, None

    parsed = []
    for raw in ids:
        if isinstance(raw, bool) or not isinstance(raw, int):
            return RESULT_ERROR, CAUSE_ERROR, None
        if raw < config.CAR_SERVO_ID_MIN or raw > config.CAR_SERVO_ID_MAX:
            return RESULT_ERROR, CAUSE_ERROR, None
        if raw in parsed:
            return RESULT_ERROR, CAUSE_ERROR, None
        parsed.append(raw)

    include_offset = data.get("include_offset", False)
    if not isinstance(include_offset, bool):
        return RESULT_ERROR, CAUSE_ERROR, None

    return RESULT_OK, CAUSE_OK, {"ids": parsed, "include_offset": include_offset}


def parse_set_command(data):
    """校验舵机绝对位置设置报文。

    返回 (result, cause, cmd)：
      - cmd = {"duration": float, "applied": [...], "skipped": [...]}；
      - servos 为空 -> result=-1，applied 为空；
      - 全部 position 越界 -> result=-2，applied 为空；
      - 部分越界 -> result=0，只执行 applied，skipped 记录越界项；
      - 类型/ID/重复/duration 错误 -> result=-2。
    """
    if not isinstance(data, dict):
        return RESULT_ERROR, CAUSE_ERROR, None

    servos = data.get("servos")
    if not isinstance(servos, list):
        return RESULT_ERROR, CAUSE_ERROR, None
    if not servos:
        return RESULT_INVALID, CAUSE_INVALID, {
            "duration": config.CAR_SERVO_DURATION_DEFAULT,
            "applied": [],
            "skipped": [],
        }
    if len(servos) > 4:
        return RESULT_ERROR, CAUSE_ERROR, None

    duration = data.get("duration", config.CAR_SERVO_DURATION_DEFAULT)
    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        return RESULT_ERROR, CAUSE_ERROR, None
    duration = float(duration)
    if not (config.CAR_SERVO_DURATION_MIN <= duration <= config.CAR_SERVO_DURATION_MAX):
        return RESULT_ERROR, CAUSE_ERROR, None

    applied = []
    skipped = []
    seen = set()
    for item in servos:
        if not isinstance(item, dict):
            return RESULT_ERROR, CAUSE_ERROR, None
        servo_id = item.get("id")
        position = item.get("position")
        if (isinstance(servo_id, bool) or not isinstance(servo_id, int)
                or servo_id < config.CAR_SERVO_ID_MIN
                or servo_id > config.CAR_SERVO_ID_MAX):
            return RESULT_ERROR, CAUSE_ERROR, None
        if servo_id in seen:
            return RESULT_ERROR, CAUSE_ERROR, None
        seen.add(servo_id)
        if isinstance(position, bool) or not isinstance(position, int):
            return RESULT_ERROR, CAUSE_ERROR, None
        if position < config.CAR_SERVO_POSITION_MIN or position > config.CAR_SERVO_POSITION_MAX:
            skipped.append({
                "id": servo_id,
                "position": position,
                "reason": "超出范围 %d–%d" % (
                    config.CAR_SERVO_POSITION_MIN, config.CAR_SERVO_POSITION_MAX),
            })
        else:
            applied.append({"id": servo_id, "position": position})

    if not applied:
        return RESULT_ERROR, CAUSE_ERROR, {
            "duration": duration,
            "applied": [],
            "skipped": skipped,
        }
    return RESULT_OK, CAUSE_OK, {
        "duration": duration,
        "applied": applied,
        "skipped": skipped,
    }


class CarServoMixin:
    """结果封装 + 非 ROS 环境的兜底处理。

    真正的 get_state 服务调用在 node.py 中覆盖 handle_car_servo_status；
    独立运行（linux-demo）只返回链路不可用。
    """

    def _publish_car_servo_status_result(self, request_id, result_code, cause, servos):
        payload = {
            "device_id": config.DEVICE_ID,
            "hostname": config.HOSTNAME,
            "ip": self.lan_ip,
            "request_id": str(request_id or ""),
            "result": result_code,
            "cause": cause,
            "servos": servos,
            "timestamp": now_ts(),
            "datetime": now_str(),
        }
        self.publish_json(config.RES_CAR_SERVO_STATUS_RESULT, payload)
        if result_code == RESULT_OK:
            logging.debug("car/servo/status/query/request 结果 -> request_id=%s result=0",
                          payload["request_id"] or "-")
        else:
            logging.warning("car/servo/status/query/request 结果 -> request_id=%s result=%s cause=%s",
                            payload["request_id"] or "-", result_code, cause)
        return payload

    def handle_car_servo_status(self, payload):
        """独立运行时没有 ROS get_state 服务，直接返回链路不可用。"""
        data = self.parse_request(payload, config.REQ_CAR_SERVO_STATUS)
        if data is None:
            return
        rid = self._rid(data)
        result, cause, req = parse_status_query(data)
        if result != RESULT_OK:
            self._publish_car_servo_status_result(rid, result, cause, [])
            return
        servos = [
            {
                "id": servo_id,
                "position": None,
                "offset": None,
                "available": False,
                "error": "服务不可用",
            }
            for servo_id in req["ids"]
        ]
        self._publish_car_servo_status_result(
            rid, RESULT_LINK_DOWN, CAUSE_LINK_DOWN, servos)

    def _publish_car_servo_set_result(self, request_id, result_code, cause,
                                      cmd=None):
        """发布 car/servo/set/response：每条请求一条，携带实际执行项。"""
        applied = []
        skipped = []
        duration = None
        if cmd:
            duration = cmd.get("duration")
            skipped = cmd.get("skipped", [])
            if result_code == RESULT_OK:
                applied = cmd.get("applied", [])
        payload = {
            "device_id": config.DEVICE_ID,
            "hostname": config.HOSTNAME,
            "ip": self.lan_ip,
            "request_id": str(request_id or ""),
            "result": result_code,
            "cause": cause,
            "duration": duration,
            "applied": applied,
            "skipped": skipped,
            "timestamp": now_ts(),
            "datetime": now_str(),
        }
        self.publish_json(config.RES_CAR_SERVO_SET_RESULT, payload)
        if result_code == RESULT_OK:
            logging.debug(
                "car/servo/set/request 结果 -> request_id=%s result=0 applied=%s",
                payload["request_id"] or "-", applied)
        else:
            logging.warning(
                "car/servo/set/request 结果 -> request_id=%s result=%s cause=%s",
                payload["request_id"] or "-", result_code, cause)
        return payload

    def handle_car_servo_set(self, payload):
        """独立运行时没有 ROS set_state 发布者，回执舵机控制链路不可用。"""
        data = self.parse_request(payload, config.REQ_CAR_SERVO_SET)
        if data is None:
            return
        rid = self._rid(data)
        result, cause, cmd = parse_set_command(data)
        if result != RESULT_OK:
            self._publish_car_servo_set_result(rid, result, cause, cmd)
            return
        logging.warning(
            "car/servo/set/request request_id=%s 未执行：舵机控制链路不可用（独立运行模式）",
            rid or "-")
        self._publish_car_servo_set_result(
            rid, RESULT_LINK_DOWN, CAUSE_LINK_DOWN, cmd)


class CarServoStatusNodeMixin:
    """ROS 节点侧实现：调用 ros_robot_controller 的 get_state 服务。

    放在本模块是为了让 node.py 保持在 500 行以内；ROS 消息延迟导入，
    这样纯校验单测不需要 ROS 环境也能 import 本模块。
    """

    def setup_car_servo_status(self, service_name):
        from ros_robot_controller_msgs.srv import GetPWMServoState
        self._servo_client = self.create_client(GetPWMServoState, service_name)

    def _call_servo_state(self, request):
        """调用 get_state 服务，超时返回 None。"""
        if not self._servo_client.service_is_ready():
            try:
                self._servo_client.wait_for_service(timeout_sec=2.0)
            except Exception:  # noqa: BLE001
                pass
        if not self._servo_client.service_is_ready():
            return None

        future = self._servo_client.call_async(request)
        done = threading.Event()
        box = {}

        def _done(fut):
            try:
                box['response'] = fut.result()
            except Exception as exc:  # noqa: BLE001
                box['error'] = exc
            done.set()

        future.add_done_callback(_done)
        reads = sum(1 + (1 if int(cmd.get_offset) else 0) for cmd in request.cmd)
        timeout = max(3.0, self._agent_mod.CAR_SERVO_READ_TIMEOUT_SEC * reads
                      + self._agent_mod.CAR_SERVO_QUERY_GRACE_SEC)
        if not done.wait(timeout):
            future.cancel()
            return None
        return box.get('response')

    def _handle_car_servo_status(self, payload):
        """car/servo/status/query/request：读取 PWM 舵机位置/偏移并返回状态。"""
        if self._shutting_down:
            return
        data = self.agent.parse_request(
            payload, self._agent_mod.REQ_CAR_SERVO_STATUS)
        if data is None:
            return

        rid = self.agent._rid(data)
        result, cause, query = parse_status_query(data)
        if result != RESULT_OK:
            self.agent._publish_car_servo_status_result(rid, result, cause, [])
            return

        from ros_robot_controller_msgs.msg import GetPWMServoCmd
        from ros_robot_controller_msgs.srv import GetPWMServoState

        ids = query["ids"]
        include_offset = query["include_offset"]
        req = GetPWMServoState.Request()
        for servo_id in ids:
            cmd = GetPWMServoCmd()
            cmd.id = int(servo_id)
            cmd.get_position = 1
            cmd.get_offset = 1 if include_offset else 0
            req.cmd.append(cmd)

        resp = self._call_servo_state(req)
        servos = []
        all_ok = resp is not None and bool(resp.success)
        for index, servo_id in enumerate(ids):
            entry = {
                "id": servo_id,
                "position": None,
                "offset": None,
                "available": False,
                "error": None,
            }
            state = None
            if resp is not None and index < len(resp.state):
                state = resp.state[index]
            if state is not None and state.position:
                entry["position"] = int(state.position[0])
                entry["available"] = True
            if include_offset:
                if state is not None and state.offset:
                    entry["offset"] = int(state.offset[0])
                else:
                    entry["available"] = False
            if not entry["available"]:
                entry["error"] = "服务不可用" if resp is None else "读取超时"
                all_ok = False
            servos.append(entry)

        if all_ok:
            self.agent._publish_car_servo_status_result(
                rid, RESULT_OK, CAUSE_OK, servos)
        else:
            self.agent._publish_car_servo_status_result(
                rid, RESULT_LINK_DOWN, CAUSE_LINK_DOWN, servos)
        self.get_logger().debug(
            'car/servo/status/query/request request_id=%s ids=%s include_offset=%s all_ok=%s'
            % (rid or '-', ids, include_offset, all_ok))

    def setup_car_servo_set(self, topic):
        from ros_robot_controller_msgs.msg import SetPWMServoState
        self._servo_set_pub = self.create_publisher(SetPWMServoState, topic, 10)

    def _handle_car_servo_set(self, payload):
        """car/servo/set/request：按绝对位置设置 PWM 舵机并发布应答。"""
        if self._shutting_down:
            return
        data = self.agent.parse_request(
            payload, self._agent_mod.REQ_CAR_SERVO_SET)
        if data is None:
            return

        rid = self.agent._rid(data)
        result, cause, cmd = parse_set_command(data)
        if result != RESULT_OK:
            self.get_logger().warn(
                'car/servo/set/request request_id=%s 未执行：result=%s cause=%s'
                % (rid or '-', result, cause))
            self.agent._publish_car_servo_set_result(rid, result, cause, cmd)
            return

        try:
            subscribers = self._servo_set_pub.get_subscription_count()
        except Exception:  # noqa: BLE001
            subscribers = 1
        if subscribers == 0:
            self.get_logger().warn(
                'car/servo/set/request request_id=%s 未执行：舵机控制链路不可用'
                % (rid or '-'))
            self.agent._publish_car_servo_set_result(
                rid, RESULT_LINK_DOWN, CAUSE_LINK_DOWN, cmd)
            return

        from ros_robot_controller_msgs.msg import PWMServoState, SetPWMServoState
        msg = SetPWMServoState()
        msg.duration = float(cmd["duration"])
        for item in cmd["applied"]:
            state = PWMServoState()
            state.id = [int(item["id"])]
            state.position = [int(item["position"])]
            msg.state.append(state)
        try:
            self._servo_set_pub.publish(msg)
        except Exception:  # noqa: BLE001
            self.get_logger().warn(
                'car/servo/set/request request_id=%s 发布失败：舵机控制链路不可用'
                % (rid or '-'))
            self.agent._publish_car_servo_set_result(
                rid, RESULT_LINK_DOWN, CAUSE_LINK_DOWN, cmd)
            return

        self.get_logger().debug(
            'car/servo/set/request request_id=%s applied=%s skipped=%s duration=%.2f'
            % (rid or '-', cmd["applied"], cmd["skipped"], cmd["duration"]))
        self.agent.servo_moving_until = time.monotonic() + float(cmd["duration"])
        self.agent._publish_car_servo_set_result(rid, RESULT_OK, CAUSE_OK, cmd)
