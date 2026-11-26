#!/usr/bin/env python3
"""car/motor/set/request 报文校验、轮速预算与应答测试。"""

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


def test_obstacle_blocks_forward():
    result, cause, twist = car_motor.parse_drive(
        _payload(move={"forward": 0.6}), forward_blocked=True)
    assert (result, cause, twist) == (
        car_motor.RESULT_OBSTACLE, car_motor.CAUSE_OBSTACLE, None)


def test_obstacle_blocks_forward_with_rotation():
    data = _payload(move={"forward": 0.6}, rotate={"left": True, "right": False})
    result, cause, twist = car_motor.parse_drive(data, forward_blocked=True)
    assert (result, cause, twist) == (
        car_motor.RESULT_OBSTACLE, car_motor.CAUSE_OBSTACLE, None)


@pytest.mark.parametrize("data", [
    _payload(move={"back": 0.6}),
    _payload(move={"left": 0.6}),
    _payload(move={"right": 0.6}),
    _payload(rotate={"left": True, "right": False}),
    _payload(),
])
def test_obstacle_allows_back_strafe_rotate_and_stop(data):
    result, cause, twist = car_motor.parse_drive(data, forward_blocked=True)
    assert (result, cause) == (car_motor.RESULT_OK, car_motor.CAUSE_OK)
    assert twist is not None


@pytest.mark.parametrize("value", [0.5, 1.0])
def test_boundary_speeds_are_valid(value):
    result, _cause, twist = car_motor.parse_drive(_payload(move={"forward": value}))
    assert result == car_motor.RESULT_OK
    assert twist == (value, 0.0, 0.0)


def test_topic_is_request_response_pair():
    assert config.REQ_CAR_MOTOR_SET == "car/motor/set/request"
    assert config.RES_CAR_MOTOR_SET_RESULT == "car/motor/set/response"
    assert (config.REQUEST_TO_RESULT[config.REQ_CAR_MOTOR_SET]
            == config.RES_CAR_MOTOR_SET_RESULT)


def test_result_codes_are_negative_protocol_values():
    assert (car_motor.RESULT_OK, car_motor.RESULT_INVALID,
            car_motor.RESULT_ERROR, car_motor.RESULT_LINK_DOWN,
            car_motor.RESULT_OBSTACLE, car_motor.RESULT_CONTROL_DENIED) == (
                0, -1, -2, -3, -4, -5)


def test_result_payload_reports_applied_cmd_vel_and_common_fields():
    class _Stub(car_motor.CarMotorMixin):
        lan_ip = "192.168.1.10"

        def publish_json(self, resource, data):
            self.published = (resource, data)

    stub = _Stub()
    payload = stub._publish_car_motor_result(
        "rid-1", car_motor.RESULT_OK, car_motor.CAUSE_OK, (0.653, 0.0, 2.5))
    resource, sent = stub.published
    assert resource == "car/motor/set/response"
    assert sent == payload
    assert payload["request_id"] == "rid-1"
    assert payload["result"] == 0
    assert payload["cause"] == "ok"
    assert payload["cmd_vel"] == {
        "linear_x": 0.653, "linear_y": 0.0, "angular_z": 2.5}
    assert payload["device_id"] == config.DEVICE_ID
    assert payload["timestamp"] and payload["datetime"]


def test_result_payload_has_no_cmd_vel_when_not_applied():
    class _Stub(car_motor.CarMotorMixin):
        def publish_json(self, resource, data):
            self.published = (resource, data)

    stub = _Stub()
    payload = stub._publish_car_motor_result(
        "rid-2", car_motor.RESULT_OBSTACLE, car_motor.CAUSE_OBSTACLE)
    assert payload["result"] == car_motor.RESULT_OBSTACLE
    assert payload["cmd_vel"] is None
