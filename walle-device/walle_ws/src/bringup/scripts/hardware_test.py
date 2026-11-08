#!/usr/bin/env python3
# encoding: utf-8

import sys
import time
import rclpy
from rclpy.node import Node
from ros_robot_controller_msgs.msg import (
    SetPWMServoState, PWMServoState, 
    MotorSpeedControl, MotorsSpeedControl,
    RGBState, RGBStates
)

class HardwareTestNode(Node):
    def __init__(self, name):
        super().__init__(name, allow_undeclared_parameters=True,
                         automatically_declare_parameters_from_overrides=True)

        self.servo_pub = self.create_publisher(SetPWMServoState, 'ros_robot_controller/pwm_servo/set_state', 10)
        self.motor_pub = self.create_publisher(MotorsSpeedControl, 'ros_robot_controller/set_motor_speeds', 10)
        self.board_rgb_pub = self.create_publisher(RGBStates, 'ros_robot_controller/set_rgb', 10)
        self.sonar_rgb_pub = self.create_publisher(RGBStates, 'sonar_controller/set_rgb', 10)

        time.sleep(1.0)

        for _ in range(3):
            self.set_all_rgb(255, 0, 0)
            time.sleep(0.3)
            self.set_all_rgb(0, 0, 0)
            time.sleep(0.3)

        self.set_all_rgb(0, 255, 0)
        time.sleep(0.5)
        
        self.pwm_servo_sync([1, 2], [1200, 1200])
        time.sleep(0.5)
        self.pwm_servo_sync([1, 2], [1800, 1800])
        time.sleep(0.5)
        self.pwm_servo_sync([1, 2], [1500, 1500])
        time.sleep(0.5)

        self.motors_sync(45)
        time.sleep(0.5)
        self.motors_sync(0)
        time.sleep(0.5)

        self.set_all_rgb(0, 0, 0)
        time.sleep(0.1)
        sys.exit(0)

    def set_all_rgb(self, r, g, b):
        board_msg = RGBStates()
        for i in [1, 2, 3]:
            s = RGBState()
            s.index, s.red, s.green, s.blue = i, int(r), int(g), int(b)
            board_msg.states.append(s)
        self.board_rgb_pub.publish(board_msg)

        sonar_msg = RGBStates()
        for i in [0, 1]:
            s = RGBState()
            s.index, s.red, s.green, s.blue = i, int(r), int(g), int(b)
            sonar_msg.states.append(s)
        self.sonar_rgb_pub.publish(sonar_msg)

    def motors_sync(self, speed):
        msg = MotorsSpeedControl()
        for i in range(1, 5):
            m = MotorSpeedControl()
            m.id, m.speed = i, float(speed)
            msg.data.append(m)
        self.motor_pub.publish(msg)

    def pwm_servo_sync(self, ids, positions):
        msg = SetPWMServoState()
        for i, p in zip(ids, positions):
            s = PWMServoState()
            s.id, s.position = [i], [int(p)]
            msg.state.append(s)
        msg.duration = 0.2
        self.servo_pub.publish(msg)

def main():
    rclpy.init()
    node = HardwareTestNode('hardware_test_node')
    try:
        rclpy.spin(node)
    except SystemExit:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == "__main__":
    main()