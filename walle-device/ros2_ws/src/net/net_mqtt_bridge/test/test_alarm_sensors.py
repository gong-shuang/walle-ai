#!/usr/bin/env python3
"""V1.6：电池低压 / 前方障碍告警状态机测试（复用 alarm 主题）。"""

import time

import pytest

import net_mqtt_bridge.alarm_sensors as alarm_sensors
from net_mqtt_bridge.alarm_sensors import SensorAlarmMixin
from net_mqtt_bridge.identity import human_mbps
from net_mqtt_bridge.monitor import MonitorMixin


class DummyMonitor(SensorAlarmMixin):
    """只跑告警状态机，不连 MQTT、不读系统状态。"""

    def __init__(self):
        self.init_alarm_sensors()
        self.events = []

    def _event(self, event, level, metric, alarm_type, message, detail=None):
        self.events.append({
            "event": event,
            "level": level,
            "metric": metric,
            "alarm_type": alarm_type,
            "message": message,
            "detail": detail,
        })


@pytest.fixture
def monitor():
    return DummyMonitor()


@pytest.fixture(autouse=True)
def defaults(monkeypatch):
    monkeypatch.setattr(alarm_sensors, "THRESH_BATTERY_V", 7.0)
    monkeypatch.setattr(alarm_sensors, "RECOVER_BATTERY_V", 7.4)
    monkeypatch.setattr(alarm_sensors, "THRESH_BATTERY_CRITICAL_V", 6.6)
    monkeypatch.setattr(alarm_sensors, "THRESH_OBSTACLE_CM", 30.0)
    monkeypatch.setattr(alarm_sensors, "RECOVER_OBSTACLE_CM", 50.0)
    monkeypatch.setattr(alarm_sensors, "ALARM_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(alarm_sensors, "BATTERY_CONFIRM_COUNT", 2)
    monkeypatch.setattr(alarm_sensors, "BATTERY_SAMPLE_COUNT", 3)


def _feed_battery(monitor, voltage, times=1):
    for _ in range(times):
        monitor.update_battery_voltage(voltage)
        monitor.check_battery()


def _raise_battery_alarm(monitor, voltage=6.8):
    _feed_battery(monitor, voltage, times=2)
    assert len(monitor.events) == 1


def test_battery_alarm_needs_two_confirmed_samples(monitor):
    _feed_battery(monitor, 6.8)
    assert monitor.events == []

    _feed_battery(monitor, 6.8)
    assert len(monitor.events) == 1
    event = monitor.events[0]
    assert (event["event"], event["level"], event["metric"],
            event["alarm_type"]) == (
        "alarm", "warning", "battery", "battery_low")
    assert event["detail"] == {"value": 6.8, "unit": "V", "threshold": 7.0}


def test_battery_alarm_is_not_repeated_while_active(monitor):
    _feed_battery(monitor, 6.8, times=6)
    assert len(monitor.events) == 1


def test_battery_escalates_to_critical_after_confirmation(monitor):
    _raise_battery_alarm(monitor)

    _feed_battery(monitor, 6.0)
    assert len(monitor.events) == 1, "单次跌落不升级"

    _feed_battery(monitor, 6.0)
    assert len(monitor.events) == 2
    assert monitor.events[1]["level"] == "critical"
    assert monitor.events[1]["detail"]["threshold"] == 6.6


def test_battery_recover_needs_two_cycles_above_recover_line(monitor):
    _raise_battery_alarm(monitor)

    # 7.0V 在阈值(7.0)与恢复线(7.4)之间：回滞带内保持告警
    _feed_battery(monitor, 7.0, times=6)
    assert len(monitor.events) == 1

    _feed_battery(monitor, 7.8, times=2)
    assert len(monitor.events) == 1, "恢复也要连续 2 次确认"

    _feed_battery(monitor, 7.8)
    assert len(monitor.events) == 2
    assert monitor.events[1]["event"] == "recover"
    assert monitor.events[1]["detail"]["threshold"] == 7.4


def test_battery_data_goes_stale(monitor):
    _feed_battery(monitor, 6.8)           # 只够 1 次，还没告警
    assert monitor.events == []

    # 模拟电池 data 断开：越过 stale 窗口后状态清零
    monitor._battery_last_update -= alarm_sensors.BATTERY_STALE_SEC + 1
    monitor.check_battery()

    _feed_battery(monitor, 6.8)           # 重新从第 1 次计数
    assert monitor.events == []


@pytest.mark.parametrize("moving,expected_level", [
    (False, "info"),
    (True, "warning"),
])
def test_obstacle_level_depends_on_car_motion(monitor, moving, expected_level):
    monitor.car_moving_fn = lambda: moving
    monitor.update_obstacle_distance(200)

    assert len(monitor.events) == 1
    assert monitor.events[0]["level"] == expected_level
    assert monitor.events[0]["alarm_type"] == "obstacle_near"
    assert monitor.events[0]["detail"] == {
        "distance_cm": 20.0, "threshold_cm": 30.0,
        "sensor": "sonar", "direction": "front",
    }


def test_obstacle_is_edge_triggered_with_hysteresis(monitor):
    monitor.update_obstacle_distance(250)
    assert len(monitor.events) == 1

    monitor.update_obstacle_distance(200)   # 持续靠近不重复上报
    assert len(monitor.events) == 1

    monitor.update_obstacle_distance(450)   # 回滞带内不恢复
    assert len(monitor.events) == 1

    monitor.update_obstacle_distance(600)
    assert len(monitor.events) == 2
    assert monitor.events[1]["event"] == "recover"
    assert monitor.events[1]["detail"]["threshold_cm"] == 50.0


def test_obstacle_events_are_rate_limited(monitor, monkeypatch):
    monkeypatch.setattr(alarm_sensors, "ALARM_MIN_INTERVAL", 100.0)
    monitor._obstacle_last_event = time.monotonic() - 200.0

    monitor.update_obstacle_distance(200)   # 允许第一条
    monitor.update_obstacle_distance(800)   # 100s 内的恢复被限流
    assert len(monitor.events) == 1


def test_invalid_sonar_reading_is_ignored(monitor):
    monitor.update_obstacle_distance(-1)
    monitor.update_obstacle_distance("bad")
    assert monitor.events == []


def test_forward_blocked_uses_fresh_near_reading(monitor):
    assert monitor.forward_blocked() is False
    monitor.update_obstacle_distance(300)      # 恰好 30cm，按阈值拦截
    assert monitor.forward_blocked() is True
    monitor.update_obstacle_distance(600)
    assert monitor.forward_blocked() is False


def test_forward_blocked_ignores_stale_reading(monitor):
    monitor.update_obstacle_distance(200)
    monitor._obstacle_last_update -= alarm_sensors.SONAR_STALE_SEC + 1
    assert monitor.forward_blocked() is False


def test_sensor_status_reflects_latest_samples(monitor):
    monitor.update_battery_voltage(7.62)
    monitor.update_obstacle_distance(245)

    status = monitor.sensor_status()
    assert status["battery"] == {"voltage_v": 7.62, "available": True}
    assert status["sonar"] == {"distance_cm": 24.5, "available": True}


def test_sensor_status_marks_stale_data_unavailable(monitor):
    monitor.update_battery_voltage(7.6)
    monitor.update_obstacle_distance(300)
    monitor._battery_last_update -= alarm_sensors.SENSOR_FRESH_SEC + 1
    monitor._obstacle_last_update -= alarm_sensors.SENSOR_FRESH_SEC + 1

    status = monitor.sensor_status()
    assert status["battery"] == {"voltage_v": None, "available": False}
    assert status["sonar"] == {"distance_cm": None, "available": False}


def test_threshold_message_can_use_human_units():
    class Dummy(MonitorMixin):
        def __init__(self):
            self.active_alarms = set()
            self.events = []

        def _event(self, event, level, metric, alarm_type, message, detail=None):
            self.events.append((event, message, detail))

    monitor = Dummy()
    monitor.check_threshold(
        "network_tx_high", 26877623.9, 375000.0, "network", "network_tx_high",
        "B/s", "网络出站带宽",
        human_value="215.0 Mbps", human_threshold="3.0 Mbps")

    event, message, detail = monitor.events[0]
    assert event == "alarm"
    assert "215.0 Mbps" in message and "3.0 Mbps" in message
    assert "26877623.9" not in message, "message 不应再使用原始 B/s 数值"
    assert detail["unit"] == "B/s", "detail 仍保留原始单位"


def test_human_mbps_converts_decimal_megabits():
    assert human_mbps(375000) == "3.0 Mbps"
    assert human_mbps(0) == "0.0 Mbps"
    assert human_mbps(None) is None
