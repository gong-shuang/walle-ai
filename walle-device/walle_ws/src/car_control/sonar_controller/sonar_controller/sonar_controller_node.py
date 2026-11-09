#!/usr/bin/env python3
# encoding: utf-8
# @Author: liuyuan
# @Date: 2025/01/20
# sonar ros2 package

import math
import time
import rclpy
import signal
import threading
from rclpy.node import Node
from std_srvs.srv import Trigger
from std_msgs.msg import Int32
from std_msgs.msg import UInt16, Bool

from sdk.iic_device_sdk import Sonar
from ros_robot_controller_msgs.msg import RGBStates


class SonarController(Node):

    def __init__(self, name):
        rclpy.init()
        super().__init__(name)
        self.sonar = Sonar()
        self.running = True
        
        #self.sonar.setPixelColor(0, (0, 0, 0))
        #self.sonar.setPixelColor(1, (0, 0, 0))
        
        
        self.distance_pub = self.create_publisher(Int32, '~/get_distance', 1)
        self.create_subscription(RGBStates, '~/set_rgb', self.set_rgb_states, 10)
        
        threading.Thread(target=self.pub_callback, daemon=True).start()
        
    
    
    def pub_callback(self):
        failures = 0
        while self.running:
            # 没有节点订阅距离时进入低功耗等待，避免空转占满 CPU
            if self.distance_pub.get_subscription_count() == 0:
                time.sleep(0.2)
                continue
            try:
                data = self.sonar.getDistance()
            except Exception as exc:  # noqa: BLE001
                failures += 1
                # 失败时发布 -1，让下游知道当前距离无效，而不是继续使用旧值
                msg = Int32()
                msg.data = -1
                self.distance_pub.publish(msg)
                if failures == 1 or failures % 50 == 0:
                    self.get_logger().warn(
                        f'声呐 I2C 读取失败（连续 {failures} 次）：{exc}')
                time.sleep(0.2)
                continue
            if failures:
                self.get_logger().info(f'声呐 I2C 恢复正常（此前连续失败 {failures} 次）')
                failures = 0
            msg = Int32()
            msg.data = data
            self.distance_pub.publish(msg)
            time.sleep(0.05)
    
    
    def set_rgb_states(self, msg):
        try:
            pixels = []
            for state in msg.states:
                pixels = (state.red, state.green, state.blue)
                self.sonar.setPixelColor(state.index, pixels)
        except Exception as exc:  # noqa: BLE001
            self.get_logger().warn(f'声呐 RGB 写入失败：{exc}')


def main():
    node = SonarController('sonar_controller')
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        node.destroy_node()
        try:
            node.sonar.setPixelColor(0, (0, 0, 0))
            node.sonar.setPixelColor(1, (0, 0, 0))
        except Exception:  # noqa: BLE001
            pass
        rclpy.shutdown()
        print('shutdown')
    finally:
        print('shutdown finish')

if __name__ == '__main__':
    main()

