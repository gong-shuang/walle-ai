#!/usr/bin/env python3
# encoding: utf-8

import time

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile
from std_msgs.msg import String
from ros_robot_controller_msgs.msg import BuzzerState, SetPWMServoState, PWMServoState


# 冷启动必须就绪的节点；缺少任一节点即判定启动超时
REQUIRED_NODES = (
    'ros_robot_controller',
    'mecanum_chassis_node',
    'usb_cam',
    'video_webrtc_bridge',
    'net_mqtt_bridge',
)
# 可选节点（声呐可关闭、AI 节点可能未启用），只告警不判失败
OPTIONAL_NODES = (
    'sonar_controller',
    'speech_recognition',
    'vocal_detect',
    'agent_process',
    'tts_node',
)
STARTUP_TIMEOUT = 30.0


class StartupCheckNode(Node):
    def __init__(self):
        super().__init__('startup_check_node')
        self.buzzer_pub = self.create_publisher(BuzzerState, 'ros_robot_controller/set_buzzer', 1)
        self.pwm_pub = self.create_publisher(
            SetPWMServoState, 'ros_robot_controller/pwm_servo/set_state', 10)
        # 健康状态用 transient local，晚加入的订阅者也能读到最近一次结果
        health_qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.health_pub = self.create_publisher(String, '/bringup/startup_status', health_qos)
        self.deadline = time.monotonic() + STARTUP_TIMEOUT
        self.done = False
        self.timer = self.create_timer(1.0, self.check_ready)
        self.get_logger().info(
            'startup_check：等待关键节点就绪（最多 %.0fs）' % STARTUP_TIMEOUT)

    def check_ready(self):
        if self.done:
            return
        nodes = {name.lstrip('/') for name in self.get_node_names()}
        missing = [name for name in REQUIRED_NODES if name not in nodes]
        if missing and time.monotonic() < self.deadline:
            self.get_logger().info('startup_check：等待 %s' % ', '.join(missing))
            return

        self.done = True
        self.timer.cancel()
        optional_missing = [name for name in OPTIONAL_NODES if name not in nodes]
        if missing:
            status = 'timeout: missing=' + ','.join(missing)
            self.get_logger().error(
                'STARTUP TIMEOUT：关键节点未就绪：%s' % ', '.join(missing))
        else:
            status = 'ready'
            self.get_logger().info('STARTUP READY：关键节点已全部就绪')
        if optional_missing:
            self.get_logger().warn(
                'startup_check：可选节点未就绪：%s' % ', '.join(optional_missing))

        self.health_pub.publish(String(data=status))
        self.announce()
        rclpy.shutdown()

    def announce(self):
        msg = BuzzerState()
        msg.freq = 1900
        msg.on_time = 0.2
        msg.off_time = 0.01
        msg.repeat = 1
        self.buzzer_pub.publish(msg)
        self.get_logger().info(f'Buzzer state published: {msg}')
        self.pwm_controller([1, 1500], [2, 1500])

    def pwm_controller(self, *position_data):
        pwm_list = []
        msg = SetPWMServoState()
        msg.duration = 0.2
        for position in position_data:
            pos = PWMServoState()
            pos.id = [position[0]]
            pos.position = [int(position[1])]
            pwm_list.append(pos)
        msg.state = pwm_list
        self.pwm_pub.publish(msg)


def main():
    rclpy.init()
    node = StartupCheckNode()
    rclpy.spin(node)


if __name__ == '__main__':
    main()
