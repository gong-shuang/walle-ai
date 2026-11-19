#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""电池低压 / 前方障碍告警（V1.6，复用 alarm 主题）。

设备端只负责发布，不落盘（告警历史由服务端数据库保存）。
阈值放在本模块而不是 config.py，是为了让 config.py 保持在 500 行以内；
读取环境变量的风格与 config.py 一致。
"""

import os
import threading
import time

from . import config

# ---- 阈值与防抖（均可用 /etc/walle-ai.conf 覆盖）----
# 默认按 2 节 18650 串联（2S，满电 8.4V）：3.5V/节低压、3.7V/节恢复、3.3V/节极低
THRESH_BATTERY_V = float(os.environ.get("THRESH_BATTERY_V", "7.0"))           # 低压阈值（V）
RECOVER_BATTERY_V = float(os.environ.get("RECOVER_BATTERY_V", "7.4"))         # 低压恢复线（V）
THRESH_BATTERY_CRITICAL_V = float(os.environ.get("THRESH_BATTERY_CRITICAL_V", "6.6"))  # 极低电压（V）
THRESH_OBSTACLE_CM = float(os.environ.get("THRESH_OBSTACLE_CM", "30"))        # 前方障碍阈值（cm）
RECOVER_OBSTACLE_CM = float(os.environ.get("RECOVER_OBSTACLE_CM", "50"))      # 障碍恢复线（cm）
ALARM_MIN_INTERVAL = float(os.environ.get("ALARM_MIN_INTERVAL", "3"))         # 同一 alarm_type 最小间隔（s）
BATTERY_SAMPLE_COUNT = 3                 # 判定用采样点数（每个 MONITOR_INTERVAL 取一个点）
BATTERY_CONFIRM_COUNT = 2                # 连续越限 / 恢复多少次才发事件
BATTERY_STALE_SEC = max(config.MONITOR_INTERVAL * 3, 15)  # 超过该时间无电池数据则跳过判定

DEFAULT_BATTERY_TOPIC = "/ros_robot_controller/battery"
DEFAULT_OBSTACLE_TOPIC = "/sonar_controller/get_distance"


class SensorAlarmMixin:
    """电池/障碍告警状态机；由 RpiMonitor 组合，单测可直接使用。"""

    def init_alarm_sensors(self):
        self._sensor_lock = threading.Lock()
        self._battery_latest_voltage = None
        self._battery_last_update = 0.0
        self._battery_samples = []
        self._battery_level = None          # None / warning / critical
        self._battery_bad_streak = 0
        self._battery_good_streak = 0
        self._battery_critical_streak = 0
        self._obstacle_distance_cm = None
        self._obstacle_active = False
        self._obstacle_last_event = 0.0
        # 由 ROS 节点注入：返回底盘当前是否在运动（影响障碍告警级别）
        self.car_moving_fn = None

    def update_battery_voltage(self, voltage):
        """接收电池采样（V）。ROS 节点在电池回调里调用。"""
        try:
            value = float(voltage)
        except (TypeError, ValueError):
            return
        if value <= 0:
            return
        with self._sensor_lock:
            self._battery_latest_voltage = value
            self._battery_last_update = time.monotonic()

    def check_battery(self):
        """按 monitor_loop 周期判定电池低压：≤阈值告警，≥恢复线才 recover。

        取最近 BATTERY_SAMPLE_COUNT 个点的均值，连续 BATTERY_CONFIRM_COUNT 次
        满足条件才发事件，避免电机启动瞬间的电压跌落误报。
        """
        payload = None
        with self._sensor_lock:
            if self._battery_latest_voltage is None:
                return
            if time.monotonic() - self._battery_last_update > BATTERY_STALE_SEC:
                self._battery_samples.clear()
                self._battery_bad_streak = 0
                self._battery_good_streak = 0
                self._battery_critical_streak = 0
                return
            self._battery_samples.append(self._battery_latest_voltage)
            del self._battery_samples[:-BATTERY_SAMPLE_COUNT]
            avg = sum(self._battery_samples) / len(self._battery_samples)

            desired = None
            if avg <= THRESH_BATTERY_CRITICAL_V:
                desired = "critical"
            elif avg <= THRESH_BATTERY_V:
                desired = "warning"
            self._battery_critical_streak = (
                self._battery_critical_streak + 1 if desired == "critical" else 0)

            if desired is None:
                self._battery_bad_streak = 0
                if self._battery_level is None or avg < RECOVER_BATTERY_V:
                    self._battery_good_streak = 0
                    return
                self._battery_good_streak += 1
                if self._battery_good_streak < BATTERY_CONFIRM_COUNT:
                    return
                self._battery_level = None
                self._battery_good_streak = 0
                payload = (
                    "recover", "info", "battery", "battery_low",
                    "电池电压已恢复：当前 %.1fV（恢复线 %gV）"
                    % (avg, RECOVER_BATTERY_V),
                    {"value": round(avg, 2), "unit": "V",
                     "threshold": RECOVER_BATTERY_V},
                )
            else:
                self._battery_good_streak = 0
                self._battery_bad_streak += 1
                if self._battery_level is None:
                    if self._battery_bad_streak < BATTERY_CONFIRM_COUNT:
                        return
                elif self._battery_level == "warning" and desired == "critical":
                    if self._battery_critical_streak < BATTERY_CONFIRM_COUNT:
                        return
                else:
                    return
                self._battery_level = desired
                threshold = (THRESH_BATTERY_CRITICAL_V
                             if desired == "critical" else THRESH_BATTERY_V)
                label = "极低" if desired == "critical" else "过低"
                payload = (
                    "alarm", desired, "battery", "battery_low",
                    "电池电压%s：当前 %.1fV（阈值 %gV）" % (label, avg, threshold),
                    {"value": round(avg, 2), "unit": "V", "threshold": threshold},
                )
        if payload:
            self._event(*payload)

    def update_obstacle_distance(self, distance_mm):
        """接收声呐距离（mm，-1 表示无效）：边沿触发 + 限频。

        只在跨越阈值时发 alarm / recover；车静止时降为 info，避免路人经过刷 warning。
        """
        try:
            mm = float(distance_mm)
        except (TypeError, ValueError):
            return
        if mm < 0:
            return
        cm = round(mm / 10.0, 1)
        payload = None
        with self._sensor_lock:
            self._obstacle_distance_cm = cm
            now = time.monotonic()
            if now - self._obstacle_last_event < ALARM_MIN_INTERVAL:
                return
            if not self._obstacle_active and cm <= THRESH_OBSTACLE_CM:
                self._obstacle_active = True
                self._obstacle_last_event = now
                moving = bool(self.car_moving_fn and self.car_moving_fn())
                payload = (
                    "alarm", "warning" if moving else "info",
                    "obstacle", "obstacle_near",
                    "前方 %gcm 有障碍物（阈值 %gcm）" % (cm, THRESH_OBSTACLE_CM),
                    {"distance_cm": cm, "threshold_cm": THRESH_OBSTACLE_CM,
                     "sensor": "sonar", "direction": "front"},
                )
            elif self._obstacle_active and cm >= RECOVER_OBSTACLE_CM:
                self._obstacle_active = False
                self._obstacle_last_event = now
                payload = (
                    "recover", "info", "obstacle", "obstacle_near",
                    "前方障碍物已离开：当前 %gcm（恢复线 %gcm）"
                    % (cm, RECOVER_OBSTACLE_CM),
                    {"distance_cm": cm, "threshold_cm": RECOVER_OBSTACLE_CM,
                     "sensor": "sonar", "direction": "front"},
                )
        if payload:
            self._event(*payload)


class SensorAlarmNodeMixin:
    """ROS 节点侧：声明话题参数、订阅电池与声呐、注入底盘运动状态。"""

    def setup_sensor_alarms(self):
        # ROS 消息类型延迟导入：本模块同时被纯 Python 单测导入
        from std_msgs.msg import Int32, UInt16

        self.declare_parameter('battery_topic', DEFAULT_BATTERY_TOPIC)
        self.declare_parameter('obstacle_topic', DEFAULT_OBSTACLE_TOPIC)
        self.agent.car_moving_fn = self._is_car_moving
        self.create_subscription(
            UInt16, self.get_parameter('battery_topic').value,
            self._on_battery, 10)
        self.create_subscription(
            Int32, self.get_parameter('obstacle_topic').value,
            self._on_obstacle, 10)

    def _is_car_moving(self):
        """底盘是否处于有效运动命令内（障碍告警据此区分 warning / info）。"""
        with self._car_lock:
            return (self._car_moving
                    and time.monotonic() - self._car_last_valid
                    <= self._agent_mod.CAR_MOTOR_DEADMAN_SEC)

    def _on_battery(self, msg):
        """电池原始值单位 mV，换算成 V 后交给告警状态机。"""
        self.agent.update_battery_voltage(float(msg.data) / 1000.0)

    def _on_obstacle(self, msg):
        """声呐原始值单位 mm，-1 表示读取失败（由状态机忽略）。"""
        self.agent.update_obstacle_distance(int(msg.data))
