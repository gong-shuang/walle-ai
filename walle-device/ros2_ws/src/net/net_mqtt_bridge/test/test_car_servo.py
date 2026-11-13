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
