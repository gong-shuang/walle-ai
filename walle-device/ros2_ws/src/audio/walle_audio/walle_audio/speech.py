#!/usr/bin/env python3
# encoding: utf-8
# 语音盒子控制节点：
#   1. 读取 IIC 语音模块（0x34）的识别结果
#   2. 前进/后退/左转/右转/停止 -> 发布 /cmd_vel
#   3. 开始/结束语音识别 -> 切换 vocal_detect 的 ASR 模式
#   4. 把 agent_process/result 转发给 tts_node/tts_text 播放

import os
import time

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from geometry_msgs.msg import Twist
from std_msgs.msg import String, Bool
from std_srvs.srv import SetBool, Trigger

from large_models_msgs.srv import SetInt32
from sdk.iic_device_sdk import SpeechModule


class SpeechControlNode(Node):
    def __init__(self, name):
        super().__init__(name)

        # I2C 总线默认值从环境变量读取：RaspberryPi5=1, OrangePi5=2
        default_i2c_bus = int(os.environ.get('WALLE_I2C_BUS', 1))
        self.declare_parameter('i2c_bus', default_i2c_bus)
        self.declare_parameter('speech_address', 0x34)
        self.declare_parameter('poll_period', 0.05)
        self.declare_parameter('linear_speed', 0.4)
        self.declare_parameter('angular_speed', 2.5)
        self.declare_parameter('command_timeout', 10.0)
        self.declare_parameter('asr_on_mode', 3)
        self.declare_parameter('asr_off_mode', 1)

        i2c_bus = int(self.get_parameter('i2c_bus').value)
        speech_address = int(self.get_parameter('speech_address').value)
        poll_period = float(self.get_parameter('poll_period').value)
        self.linear_speed = float(self.get_parameter('linear_speed').value)
        self.angular_speed = float(self.get_parameter('angular_speed').value)
        self.command_timeout = float(self.get_parameter('command_timeout').value)
        self.asr_on_mode = int(self.get_parameter('asr_on_mode').value)
        self.asr_off_mode = int(self.get_parameter('asr_off_mode').value)

        self.voice = SpeechModule(address=speech_address, bus=i2c_bus)
        self.command = 'stop'
        self.command_stamp = time.time()
        self.last_result = 0
        self.asr_enabled = False

        self.cmd_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.tts_pub = self.create_publisher(String, '/tts_node/tts_text', 10)
        self.create_subscription(String, '/agent_process/result', self.result_callback, 10)
        self.create_subscription(Bool, '/tts_node/play_finish', self.play_finish_callback, 10)
        self.mode_client = self.create_client(SetInt32, '/vocal_detect/set_mode')
        self.wakeup_client = self.create_client(SetBool, '/vocal_detect/enable_wakeup')

        self.create_service(SetBool, '~/enable_asr', self.enable_asr_srv)
        self.create_service(Trigger, '~/init_finish', self.get_node_state)

        self.create_timer(poll_period, self.poll_callback)
        self.create_timer(1.0, self.watchdog_callback)
        # 运动期间以 10Hz 重发当前指令，配合底盘 cmd_vel 看门狗（P0-3.1）
        self.create_timer(0.1, self.stream_cmd_vel)
        self.publish_twist()  # 启动时先发一次停止，保证底盘处于安全状态

        self.get_logger().info('speech recognition node started, voice module address 0x%02X' % speech_address)

    def get_node_state(self, request, response):
        response.success = True
        return response

    def poll_callback(self):
        result = self.voice.read_result()
        if result == 0:
            self.last_result = 0
            return
        if result == self.last_result:
            return
        self.last_result = result
        self.handle_result(result)

    def handle_result(self, result):
        self.get_logger().info('voice result: %d' % result)
        if result == SpeechModule.RESULT_GO:
            self.set_command('forward')
        elif result == SpeechModule.RESULT_BACK:
            self.set_command('back')
        elif result == SpeechModule.RESULT_LEFT:
            self.set_command('left')
        elif result == SpeechModule.RESULT_RIGHT:
            self.set_command('right')
        elif result == SpeechModule.RESULT_STOP:
            self.set_command('stop')
        elif result == SpeechModule.RESULT_ASR_START:
            self.set_asr(True)
        elif result == SpeechModule.RESULT_ASR_STOP:
            self.set_asr(False)
        else:
            self.get_logger().info('unknown voice result: %d' % result)

    def set_command(self, command):
        if command == self.command:
            self.get_logger().info('duplicate voice command: %s, ignored' % command)
            return
        self.command = command
        self.command_stamp = time.time()
        self.get_logger().info('chassis command: %s' % command)
        self.publish_twist()

    def publish_twist(self):
        twist = Twist()
        if self.command == 'forward':
            twist.linear.x = self.linear_speed
        elif self.command == 'back':
            twist.linear.x = -self.linear_speed
        elif self.command == 'left':
            twist.angular.z = self.angular_speed
        elif self.command == 'right':
            twist.angular.z = -self.angular_speed
        self.cmd_pub.publish(twist)

    def stream_cmd_vel(self):
        """运动期间以 10Hz 重发当前指令，避免底盘看门狗误触发。"""
        if self.command != 'stop':
            self.publish_twist()

    def watchdog_callback(self):
        if self.command != 'stop' and self.command_timeout > 0 and \
                time.time() - self.command_stamp > self.command_timeout:
            self.get_logger().warn('voice command timeout, stop chassis')
            self.command = 'stop'
            self.command_stamp = time.time()
            self.publish_twist()

    def set_asr(self, enable):
        if not enable and not self.asr_enabled:
            self.get_logger().info('large model ASR already off, ignored')
            return
        if enable and self.asr_enabled:
            self.rearm_asr()
            return
        mode = self.asr_on_mode if enable else self.asr_off_mode
        if not self.mode_client.service_is_ready():
            self.get_logger().warn(
                'vocal_detect/set_mode is not available, start large_models first')
            return
        request = SetInt32.Request()
        request.data = mode
        self.mode_client.call_async(request)
        self.asr_enabled = enable
        self.get_logger().info('large model ASR %s, mode=%d' % ('on' if enable else 'off', mode))

    def rearm_asr(self):
        if not self.wakeup_client.service_is_ready():
            self.get_logger().warn(
                'vocal_detect/enable_wakeup is not available, start large_models first')
            return
        request = SetBool.Request()
        request.data = True
        self.wakeup_client.call_async(request)
        self.get_logger().info('large model ASR re-armed')

    def play_finish_callback(self, msg):
        if msg.data and self.asr_enabled:
            self.rearm_asr()

    def enable_asr_srv(self, request, response):
        self.set_asr(request.data)
        response.success = True
        response.message = 'asr %s' % ('on' if request.data else 'off')
        return response

    def result_callback(self, msg):
        text = msg.data.strip()
        if not text:
            return
        tts_msg = String()
        tts_msg.data = text
        self.tts_pub.publish(tts_msg)
        self.get_logger().info('large model reply -> tts: %s' % text)


def main():
    rclpy.init()
    node = SpeechControlNode('speech_recognition')
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.voice.close()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
