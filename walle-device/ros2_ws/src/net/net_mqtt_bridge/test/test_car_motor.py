#!/usr/bin/env python3
"""car/motor/drive 报文校验与轮速预算测试。"""

import pytest

import net_mqtt_bridge.car_motor as car_motor
import net_mqtt_bridge.config as config


def _payload(**overrides):
    payload = {
        "move": {"forward": 0.0, "back": 0.0, "left": 0.0, "right": 0.0},
        "rotate": {"left": False, "right": False},
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("key,expected", [
    ("forward", (0.6, 0.0, 0.0)),
    ("back", (-0.6, 0.0, 0.0)),
    ("left", (0.0, 0.6, 0.0)),
    ("right", (0.0, -0.6, 0.0)),
])
def test_single_direction_maps_to_cmd_vel(key, expected):
    data = _payload(move={key: 0.6})
    result, cause, twist = car_motor.parse_drive(data)
    assert (result, cause) == (car_motor.RESULT_OK, car_motor.CAUSE_OK)
    assert twist == expected


def test_all_zero_is_stop():
    result, cause, twist = car_motor.parse_drive(_payload())
    assert result == car_motor.RESULT_OK
    assert cause == car_motor.CAUSE_OK
    assert twist == (0.0, 0.0, 0.0)


@pytest.mark.parametrize("left,right,angular", [
    (True, False, config.CAR_MOTOR_ANGULAR_SPEED),
    (False, True, -config.CAR_MOTOR_ANGULAR_SPEED),
    (False, False, 0.0),
])
def test_rotate_maps_to_angular_z(left, right, angular):
    data = _payload(rotate={"left": left, "right": right})
    result, _cause, twist = car_motor.parse_drive(data)
    assert result == car_motor.RESULT_OK
    assert twist == (0.0, 0.0, angular)


@pytest.mark.parametrize("data", [
    {"rotate": {"left": False, "right": False}},
    {"move": {"forward": 0.0}},
    {},
])
def test_missing_move_or_rotate_is_invalid(data):
    result, cause, twist = car_motor.parse_drive(data)
    assert (result, cause, twist) == (
        car_motor.RESULT_INVALID, car_motor.CAUSE_INVALID, None)


@pytest.mark.parametrize("data", [
    _payload(move={"forward": 0.6, "left": 0.6}),
    _payload(move={"forward": 0.6, "back": 0.6}),
    _payload(move={"left": 0.6, "right": 0.6}),
    _payload(move={"forward": 0.4}),
    _payload(move={"forward": 0.49}),
    _payload(move={"forward": 1.01}),
    _payload(move={"forward": -0.6}),
    _payload(move={"forward": True}),
    _payload(move={"forward": "0.6"}),
    _payload(rotate={"left": True, "right": True}),
    _payload(rotate={"left": 1, "right": 0}),
    _payload(move=None),
    _payload(rotate=None),
])
def test_bad_values_are_command_errors(data):
    result, cause, twist = car_motor.parse_drive(data)
    assert (result, cause, twist) == (
        car_motor.RESULT_ERROR, car_motor.CAUSE_ERROR, None)


def test_missing_subfields_default_to_zero_or_false():
    data = {"move": {"forward": 0.6}, "rotate": {"left": True}}
    result, _cause, twist = car_motor.parse_drive(data)
    assert result == car_motor.RESULT_OK
    assert twist == (0.6, 0.0, config.CAR_MOTOR_ANGULAR_SPEED)


def test_unknown_fields_are_ignored():
    data = _payload(move={"forward": 0.6, "unused": 1.0},
                    rotate={"left": False, "right": False, "extra": True})
    result, _cause, twist = car_motor.parse_drive(data)
    assert result == car_motor.RESULT_OK
    assert twist == (0.6, 0.0, 0.0)


def test_rotation_scales_linear_speed_to_wheel_budget():
    data = _payload(move={"forward": 1.0}, rotate={"left": True, "right": False})
    result, _cause, twist = car_motor.parse_drive(data)
    assert result == car_motor.RESULT_OK
    assert twist == (config.CAR_MOTOR_LINEAR_LIMIT_WITH_ROTATE, 0.0,
                     config.CAR_MOTOR_ANGULAR_SPEED)
    assert twist[0] == pytest.approx(0.653)


def test_rotation_keeps_linear_speed_within_budget():
    data = _payload(move={"forward": 0.6}, rotate={"left": True, "right": False})
    result, _cause, twist = car_motor.parse_drive(data)
    assert result == car_motor.RESULT_OK
    assert twist == (0.6, 0.0, config.CAR_MOTOR_ANGULAR_SPEED)


@pytest.mark.parametrize("value", [0.5, 1.0])
def test_boundary_speeds_are_valid(value):
    result, _cause, twist = car_motor.parse_drive(_payload(move={"forward": value}))
    assert result == car_motor.RESULT_OK
    assert twist == (value, 0.0, 0.0)


def test_result_payload_echoes_request_id_and_uses_car_topic():
    class Dummy(car_motor.CarMotorMixin):
        lan_ip = "192.168.1.10"

        def __init__(self):
            self.topic = ""
            self.payload = None

        def publish_json(self, topic, payload):
            self.topic = topic
            self.payload = payload

    dummy = Dummy()
    dummy._publish_car_motor_result("req-1", car_motor.RESULT_OK, car_motor.CAUSE_OK)
    assert dummy.topic == config.RES_CAR_MOTOR_RESULT
    assert dummy.payload["request_id"] == "req-1"
    assert dummy.payload["result"] == car_motor.RESULT_OK
    assert dummy.payload["cause"] == car_motor.CAUSE_OK
