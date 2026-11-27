#!/usr/bin/env python3
# encoding: utf-8

import importlib.util
import os
import threading
import time

import rclpy
from ament_index_python.packages import get_package_share_directory
from rclpy.executors import ExternalShutdownException
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
# 关键话题必须已有发布者；避免节点进程短暂存在但实际不可用（如 usb_cam 反复崩溃）
REQUIRED_TOPICS = ('/image_raw',)
# 可选节点（声呐可关闭、AI 节点可能未启用），只告警不判失败
OPTIONAL_NODES = (
    'sonar_controller',
    'speech_recognition',
    'vocal_detect',
    'agent_process',
    'tts_node',
    'record_video_node',
)
STARTUP_TIMEOUT = 30.0


class StartupCheckNode(Node):
    def __init__(self):
        super().__init__('startup_check_node')

        # 平台由 bringup 传入（robot / pc）；PC 模式只检查模拟节点与可选摄像头
        self.declare_parameter('platform', os.environ.get('WALLE_PLATFORM', 'robot'))
        self.declare_parameter('camera_enabled', True)
        self._platform = str(self.get_parameter('platform').value).strip().lower()
        if self._platform not in ('robot', 'pc'):
            self._platform = 'robot'
        self._camera_enabled = bool(self.get_parameter('camera_enabled').value)

        if self._platform == 'pc':
            self._required_nodes = ['pc_robot', 'net_mqtt_bridge']
            self._required_topics = []
            if self._camera_enabled:
                self._required_nodes.extend(['usb_cam', 'video_webrtc_bridge'])
                self._required_topics.append('/image_raw')
            self._optional_nodes = tuple(
                name for name in OPTIONAL_NODES if name != 'sonar_controller')
        else:
            self._required_nodes = list(REQUIRED_NODES)
            self._required_topics = list(REQUIRED_TOPICS)
            self._optional_nodes = OPTIONAL_NODES

        self.buzzer_pub = self.create_publisher(BuzzerState, 'ros_robot_controller/set_buzzer', 1)
        self.pwm_pub = self.create_publisher(
            SetPWMServoState, 'ros_robot_controller/pwm_servo/set_state', 10)
        # 健康状态用 transient local，晚加入的订阅者也能读到最近一次结果
        health_qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.health_pub = self.create_publisher(String, '/bringup/startup_status', health_qos)
        self.deadline = time.monotonic() + STARTUP_TIMEOUT
        self.done = False
        self.timer = self.create_timer(1.0, self.check_ready)
        self._check_camera_conf()
        self.get_logger().info(
            'startup_check：平台=%s，等待关键节点就绪（最多 %.0fs）'
            % (self._platform, STARTUP_TIMEOUT))

    def _check_camera_conf(self):
        """校验 walle_camera.conf 是否存在且 schema 合法。"""
        if self._platform == 'pc' and not self._camera_enabled:
            self.get_logger().info('PC 模式未启用摄像头，跳过摄像头配置校验')
            return
        try:
            path = os.path.join(
                get_package_share_directory('bringup'), 'camera_conf.py')
            spec = importlib.util.spec_from_file_location(
                'walle_camera_conf', path)
            if spec is None or spec.loader is None:
                raise RuntimeError('无法加载 %s' % path)
            api = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(api)
        except Exception as exc:  # noqa: BLE001
            self.get_logger().error(
                '摄像头配置校验失败：加载 camera_conf.py 出错：%s' % exc)
            return
        conf, conf_path, error = api.load_camera_conf()
        if conf is None:
            self.get_logger().error('摄像头配置校验失败：%s' % error)
            return
        self.get_logger().info(
            '摄像头配置：%s（%s）'
            % (conf_path, api.describe_camera_conf(conf)))
        device = conf.get('camera', {}).get('device', '')
        if device and not os.path.exists(device):
            self.get_logger().warning('摄像头设备 %s 当前不存在' % device)

    def check_ready(self):
        if self.done:
            return
        nodes = {name.lstrip('/') for name in self.get_node_names()}
        missing = [name for name in self._required_nodes if name not in nodes]
        missing_topics = [topic for topic in self._required_topics
                          if self.count_publishers(topic) == 0]
        if (missing or missing_topics) and time.monotonic() < self.deadline:
            waiting = missing + [f'{topic}(无发布者)' for topic in missing_topics]
            self.get_logger().info('startup_check：等待 %s' % ', '.join(waiting))
            return

        self.done = True
        self.timer.cancel()
        optional_missing = [name for name in self._optional_nodes if name not in nodes]
        if missing or missing_topics:
            status = 'timeout: missing=' + ','.join(missing + missing_topics)
            self.get_logger().error(
                'STARTUP TIMEOUT：未就绪：%s'
                % ', '.join(missing + [f'{topic}(无发布者)' for topic in missing_topics]))
        else:
            status = 'ready'
            self.get_logger().info('STARTUP READY：关键节点与话题已全部就绪')
        if optional_missing:
            self.get_logger().warn(
                'startup_check：可选节点未就绪：%s' % ', '.join(optional_missing))

        self.health_pub.publish(String(data=status))
        self.announce()
        # 在定时器回调里直接 rclpy.shutdown() 可能等待 executor 而死锁，
        # 放到独立线程里延迟关闭，让 main 里的 spin 正常返回。
        threading.Thread(target=self._shutdown, daemon=True).start()

    @staticmethod
    def _shutdown():
        time.sleep(0.1)
        try:
            rclpy.shutdown()
        except Exception:  # noqa: BLE001
            pass

    def announce(self):
        if self._platform == 'pc':
            self.get_logger().info('PC 模式：跳过蜂鸣器 / PWM 舵机播报')
            return
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
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
