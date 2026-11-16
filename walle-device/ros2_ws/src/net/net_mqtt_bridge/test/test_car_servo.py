#!/usr/bin/env python3
"""car/servo/status/query 报文校验与结果封装测试。"""

import pytest

import net_mqtt_bridge.car_servo as car_servo
import net_mqtt_bridge.config as config


def test_default_ids_and_offset():
    result, cause, req = car_servo.parse_status_query({})
    assert (result, cause) == (car_servo.RESULT_OK, car_servo.CAUSE_OK)
    assert req == {"ids": [1, 2], "include_offset": False}


def test_custom_ids_and_offset():
    result, _cause, req = car_servo.parse_status_query(
        {"ids": [2], "include_offset": True})
    assert result == car_servo.RESULT_OK
    assert req == {"ids": [2], "include_offset": True}


@pytest.mark.parametrize("data", [
    {"ids": []},
    {"ids": [0]},
    {"ids": [5]},
    {"ids": [1, 1]},
])
def test_invalid_ids(data):
    result, cause, req = car_servo.parse_status_query(data)
    assert (result, cause, req) == (
        car_servo.RESULT_INVALID, car_servo.CAUSE_INVALID, None)


@pytest.mark.parametrize("data", [
    {"ids": "1"},
    {"ids": [True]},
    {"ids": [1.5]},
    {"ids": [1, "2"]},
    {"include_offset": 1},
    {"include_offset": "false"},
])
def test_type_errors(data):
    result, cause, req = car_servo.parse_status_query(data)
    assert (result, cause, req) == (
        car_servo.RESULT_ERROR, car_servo.CAUSE_ERROR, None)


def test_result_payload_contains_servos_and_request_id():
    class Dummy(car_servo.CarServoMixin):
        lan_ip = "192.168.1.10"

        def __init__(self):
            self.topic = ""
            self.payload = None

        def publish_json(self, topic, payload):
            self.topic = topic
            self.payload = payload

    dummy = Dummy()
    servos = [
        {"id": 1, "position": 1500, "offset": None,
         "available": True, "error": None},
        {"id": 2, "position": None, "offset": None,
         "available": False, "error": "读取超时"},
    ]
    dummy._publish_car_servo_status_result("req-1", 3, "舵机控制链路不可用", servos)
    assert dummy.topic == config.RES_CAR_SERVO_STATUS_RESULT
    assert dummy.payload["request_id"] == "req-1"
    assert dummy.payload["result"] == 3
    assert dummy.payload["servos"] == servos


def test_set_command_both_valid():
    result, cause, cmd = car_servo.parse_set_command({
        "servos": [
            {"id": 1, "position": 1400},
            {"id": 2, "position": 1600},
        ],
    })
    assert (result, cause) == (car_servo.RESULT_OK, car_servo.CAUSE_OK)
    assert cmd["duration"] == config.CAR_SERVO_DURATION_DEFAULT
    assert cmd["applied"] == [
        {"id": 1, "position": 1400},
        {"id": 2, "position": 1600},
    ]
    assert cmd["skipped"] == []


def test_set_command_one_out_of_range_executes_the_other():
    result, cause, cmd = car_servo.parse_set_command({
        "servos": [
            {"id": 1, "position": 2400},
            {"id": 2, "position": 1600},
        ],
        "duration": 0.8,
    })
    assert (result, cause) == (car_servo.RESULT_OK, car_servo.CAUSE_OK)
    assert cmd["duration"] == 0.8
    assert cmd["applied"] == [{"id": 2, "position": 1600}]
    assert cmd["skipped"][0]["id"] == 1
    assert "超出范围" in cmd["skipped"][0]["reason"]


def test_set_command_all_out_of_range_is_invalid():
    result, cause, cmd = car_servo.parse_set_command({
        "servos": [
            {"id": 1, "position": 700},
            {"id": 2, "position": 2300},
        ],
    })
    assert (result, cause) == (car_servo.RESULT_INVALID, car_servo.CAUSE_INVALID)
    assert cmd["applied"] == []
    assert len(cmd["skipped"]) == 2


@pytest.mark.parametrize("data", [
    {},
    {"servos": []},
    {"servos": "1"},
    {"servos": [None]},
    {"servos": [{"id": 1, "position": 1500}] * 5},
    {"servos": [{"id": 5, "position": 1500}]},
    {"servos": [{"id": 1, "position": 1500}, {"id": 1, "position": 1600}]},
    {"servos": [{"id": 1, "position": "1500"}]},
    {"servos": [{"id": 1, "position": True}]},
    {"servos": [{"id": 1, "position": 1500}], "duration": 0},
    {"servos": [{"id": 1, "position": 1500}], "duration": 6},
    {"servos": [{"id": 1, "position": 1500}], "duration": "1"},
])
def test_set_command_errors(data):
    result, cause, cmd = car_servo.parse_set_command(data)
    assert (result, cause) in (
        (car_servo.RESULT_INVALID, car_servo.CAUSE_INVALID),
        (car_servo.RESULT_ERROR, car_servo.CAUSE_ERROR),
    )
    assert cmd is None or cmd["applied"] == []


def test_set_result_payload_contains_applied_and_skipped():
    class Dummy(car_servo.CarServoMixin):
        lan_ip = "192.168.1.10"

        def __init__(self):
            self.topic = ""
            self.payload = None

        def publish_json(self, topic, payload):
            self.topic = topic
            self.payload = payload

    dummy = Dummy()
    dummy._publish_car_servo_set_result(
        "req-2", 0, "ok", 1.0,
        [{"id": 1, "position": 1400}],
        [{"id": 2, "position": 2400, "reason": "超出范围 800–2200"}])
    assert dummy.topic == config.RES_CAR_SERVO_SET_RESULT
    assert dummy.payload["request_id"] == "req-2"
    assert dummy.payload["applied"][0]["position"] == 1400
    assert dummy.payload["skipped"][0]["id"] == 2
