#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pc_robot：PC 环境下的底盘/传感器模拟节点。

对 net_mqtt_bridge 提供与真机一致的话题/服务，使 bridge 与上层协议无需修改：
  - 订阅 /cmd_vel，维护运动状态并按 cmd_vel_timeout 自动停车；
  - 订阅 /ros_robot_controller/pwm_servo/set_state，保存舵机目标位置/偏移；
  - 提供 /ros_robot_controller/pwm_servo/get_state 服务；
  - 发布 /ros_robot_controller/battery（mV）与 /sonar_controller/get_distance（mm）。

故障注入通过 ROS 参数完成（参数每周期读取，ros2 param set 立即生效）：
  ros2 param set /pc_robot battery_mv 6500
  ros2 param set /pc_robot sonar_distance_mm 250
  ros2 param set /pc_robot sonar_valid false
  ros2 param set /pc_robot battery_enabled false

注意：本节点名不是 ros_robot_controller / sonar_controller，因此以下话题/服务必须
使用绝对名称，不能用 ~/ 私有命名，否则 bridge 找不到。
"""

import logging
import os
import threading
import time

import rclpy
from geometry_msgs.msg import Twist
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import Int32, UInt16

from ros_robot_controller_msgs.msg import PWMServoState, SetPWMServoState
from ros_robot_controller_msgs.srv import GetPWMServoState

BATTERY_TOPIC = '/ros_robot_controller/battery'
SONAR_TOPIC = '/sonar_controller/get_distance'
SERVO_SET_TOPIC = '/ros_robot_controller/pwm_servo/set_state'
SERVO_STATE_SERVICE = '/ros_robot_controller/pwm_servo/get_state'
CMD_VEL_TOPIC = '/cmd_vel'

SERVO_IDS = (1, 2, 3, 4)


class PCRobot(Node):
    """只维护内存状态，不访问串口 / I2C / 摄像头等真实硬件。"""

    def __init__(self):
        super().__init__('pc_robot')

        # ---- 参数（均可在运行时用 ros2 param set 修改，用于故障注入）----
        self.declare_parameter('battery_mv', 8000)
        self.declare_parameter('battery_period', 0.5)
        self.declare_parameter('battery_enabled', True)
        self.declare_parameter('sonar_distance_mm', 1000)
        self.declare_parameter('sonar_period', 0.05)
        self.declare_parameter('sonar_valid', True)
        self.declare_parameter('cmd_vel_timeout', 0.6)
        self.declare_parameter('servo_default_position', 1500)
        self.declare_parameter('servo_min_position', 800)
        self.declare_parameter('servo_max_position', 2200)

        self._lock = threading.Lock()
        self._linear_x = 0.0
        self._linear_y = 0.0
        self._angular_z = 0.0
        self._last_cmd_time = 0.0
        self._moving = False

        default_position = int(self.get_parameter('servo_default_position').value)
        self._servo_positions = {sid: default_position for sid in SERVO_IDS}
        self._servo_offsets = {sid: 0 for sid in SERVO_IDS}

        self._battery_pub = self.create_publisher(UInt16, BATTERY_TOPIC, 10)
        self._sonar_pub = self.create_publisher(Int32, SONAR_TOPIC, 10)

        self.create_subscription(Twist, CMD_VEL_TOPIC, self._on_cmd_vel, 10)
        self.create_subscription(
            SetPWMServoState, SERVO_SET_TOPIC, self._on_servo_set, 10)
        self.create_service(
            GetPWMServoState, SERVO_STATE_SERVICE, self._on_servo_state)

        battery_period = max(0.1, float(self.get_parameter('battery_period').value))
        sonar_period = max(0.01, float(self.get_parameter('sonar_period').value))
        self.create_timer(battery_period, self._publish_battery)
        self.create_timer(sonar_period, self._publish_sonar)
        self.create_timer(0.1, self._cmd_vel_watchdog)

        self.get_logger().info(
            'pc_robot 启动：模拟 /cmd_vel、舵机、电池与声呐（无真实硬件）')
        self.get_logger().info(
            '故障注入：ros2 param set /pc_robot battery_mv 6500；'
            'ros2 param set /pc_robot sonar_distance_mm 250')

    # ------------------------------------------------------------------ /cmd_vel
    def _on_cmd_vel(self, msg):
        """保存最新运动指令；只在运动/停止切换时打日志，避免 10Hz 刷屏。"""
        with self._lock:
            self._linear_x = float(msg.linear.x)
            self._linear_y = float(msg.linear.y)
            self._angular_z = float(msg.angular.z)
            self._last_cmd_time = time.monotonic()
            moving = any(abs(v) > 1e-6 for v in
                         (self._linear_x, self._linear_y, self._angular_z))
            changed = moving != self._moving
            self._moving = moving
        if changed:
            if moving:
                self.get_logger().info(
                    '收到运动指令：linear=(%.2f, %.2f) angular=%.2f'
                    % (self._linear_x, self._linear_y, self._angular_z))
            else:
                self.get_logger().info('收到停止指令')

    def _cmd_vel_watchdog(self):
        """模拟底盘看门狗：命令中断后自动停车（bridge 自身也有 500ms 死区）。"""
        timeout = max(0.1, float(self.get_parameter('cmd_vel_timeout').value))
        with self._lock:
            if not self._moving:
                return
            if time.monotonic() - self._last_cmd_time <= timeout:
                return
            self._linear_x = self._linear_y = self._angular_z = 0.0
            self._moving = False
        self.get_logger().warn(
            '/cmd_vel 超过 %.1fs 未更新，模拟底盘自动停车' % timeout)

    # ------------------------------------------------------------------ 舵机
    def _on_servo_set(self, msg):
        """保存舵机目标位置/偏移；越界位置钳制到 [min, max]。"""
        default_pos = int(self.get_parameter('servo_default_position').value)
        min_pos = int(self.get_parameter('servo_min_position').value)
        max_pos = int(self.get_parameter('servo_max_position').value)
        with self._lock:
            for state in msg.state:
                ids = list(state.id)
                positions = list(state.position)
                offsets = list(state.offset)
                for idx, sid in enumerate(ids):
                    sid = int(sid)
                    if sid not in self._servo_positions:
                        continue
                    pos = int(positions[idx]) if idx < len(positions) else default_pos
                    self._servo_positions[sid] = max(min_pos, min(max_pos, pos))
                    if idx < len(offsets):
                        self._servo_offsets[sid] = int(offsets[idx])
        self.get_logger().debug(
            '舵机设置：%s，duration=%.2f'
            % (dict(self._servo_positions), float(msg.duration)))

    def _on_servo_state(self, request, response):
        """返回模拟舵机状态；bridge 依赖 success=true 且 position 非空。"""
        default_pos = int(self.get_parameter('servo_default_position').value)
        with self._lock:
            for cmd in request.cmd:
                sid = int(cmd.id)
                state = PWMServoState()
                state.id = [sid]
                if cmd.get_position:
                    state.position = [self._servo_positions.get(sid, default_pos)]
                if cmd.get_offset:
                    state.offset = [self._servo_offsets.get(sid, 0)]
                response.state.append(state)
        response.success = True
        return response

    # ------------------------------------------------------------------ 传感器
    def _publish_battery(self):
        if not bool(self.get_parameter('battery_enabled').value):
            return
        mv = max(0, min(65535, int(self.get_parameter('battery_mv').value)))
        msg = UInt16()
        msg.data = mv
        self._battery_pub.publish(msg)

    def _publish_sonar(self):
        msg = Int32()
        if not bool(self.get_parameter('sonar_valid').value):
            msg.data = -1
        else:
            msg.data = max(0, int(self.get_parameter('sonar_distance_mm').value))
        self._sonar_pub.publish(msg)


def main(args=None):
    logging.basicConfig(
        level=logging.INFO,
        format=os.environ.get(
            'WALLE_PY_LOG_FORMAT',
            '%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s'),
        datefmt=os.environ.get('WALLE_PY_LOG_DATEFMT', '%Y-%m-%d %H:%M:%S'),
    )
    rclpy.init(args=args)
    node = None
    executor = None
    try:
        node = PCRobot()
        executor = MultiThreadedExecutor(num_threads=2)
        executor.add_node(node)
        executor.spin()
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        if executor is not None:
            executor.shutdown()
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
