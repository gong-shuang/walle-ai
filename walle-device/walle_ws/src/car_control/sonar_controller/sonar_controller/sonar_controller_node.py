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
        while self.running:
            # 没有节点订阅距离时进入低功耗等待，避免空转占满 CPU
            if self.distance_pub.get_subscription_count() == 0:
                time.sleep(0.2)
                continue
            data = self.sonar.getDistance()
            msg = Int32()
            msg.data = data
            self.distance_pub.publish(msg)
            time.sleep(0.05)
    
    
    def set_rgb_states(self, msg):
        pixels = []
        for state in msg.states:
            pixels = (state.red, state.green, state.blue)
            self.sonar.setPixelColor(state.index, pixels)


def main():
    node = SonarController('sonar_controller')
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        node.destroy_node()
        node.sonar.setPixelColor(0, (0, 0, 0))
        node.sonar.setPixelColor(1, (0, 0, 0))        
        rclpy.shutdown()
        print('shutdown')
    finally:
        print('shutdown finish')

if __name__ == '__main__':
    main()

