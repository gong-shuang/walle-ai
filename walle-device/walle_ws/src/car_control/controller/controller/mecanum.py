import math
import time

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from ros_robot_controller_msgs.msg import MotorSpeedControl, MotorsSpeedControl  

class MecanumChassis(Node):
    def __init__(self, wheelbase=0.1368, track_width=0.1410, wheel_diameter=0.065, max_linear_speed=1.0, max_angular_speed=3.0):
        super().__init__('mecanum_chassis')

        # 麦克纳姆轮的物理参数
        self.wheelbase = wheelbase
        self.track_width = track_width
        self.wheel_diameter = wheel_diameter

        # 速度与加速度限幅（全部可通过 ROS 参数覆盖）
        self.max_linear_speed = max(0.01, float(self.declare_parameter(
            'max_linear_speed', max_linear_speed).value))     # 最大线速度 (m/s)
        self.max_angular_speed = max(0.01, float(self.declare_parameter(
            'max_angular_speed', max_angular_speed).value))   # 最大角速度 (rad/s)
        self.max_linear_accel = max(0.01, float(self.declare_parameter(
            'max_linear_accel', 1.0).value))                  # 线加速度上限 (m/s²)
        self.max_angular_accel = max(0.01, float(self.declare_parameter(
            'max_angular_accel', 3.0).value))                 # 角加速度上限 (rad/s²)
        self._slew_linear_x = 0.0
        self._slew_linear_y = 0.0
        self._slew_angular_z = 0.0
        self._last_slew_time = None

        self.create_subscription(Twist, '/cmd_vel', self.cmd_vel_callback, 10)

        # 低层电机占空比接口：mecanum 是唯一合法发布者，话题名带 internal/ 前缀，
        # 且不在 rosbridge 白名单内，避免外部绕过 /cmd_vel 直接控制电机。
        self.publisher = self.create_publisher(MotorsSpeedControl, '/ros_robot_controller/internal/set_motor_speeds', 10)

        # cmd_vel 看门狗：超过该时长未收到新指令就主动停车（默认 1.5s，可通过参数覆盖）
        self.declare_parameter('cmd_vel_timeout', 1.5)
        self.cmd_vel_timeout = max(0.1, float(self.get_parameter('cmd_vel_timeout').value))
        self._last_cmd_time = None
        self._moving = False
        self.create_timer(0.1, self.watchdog_callback)

    def speed_convert(self, speed, max_speed):
        """
        将速度从 m/s 转换为电机速度，并映射到 -100 到 100 范围。
        :param speed: 速度（m/s）
        :param max_speed: 最大速度（m/s）
        :return: 电机控制速度 (-100 到 100)
        """
        # 按比例将速度转换为电机控制速度
        motor_speed = (speed / max_speed) * 100
        motor_speed = max(-100, min(100, motor_speed))  # 限制在 -100 到 100 之间
        return motor_speed
    # def speed_convert(self, speed, max_speed, angular_speed_factor=10.0):
    #     """
    #     将速度从 m/s 转换为电机速度，并映射到 -100 到 100 范围。
    #     :param speed: 速度（m/s）或角速度（rad/s）
    #     :param max_speed: 最大速度（m/s）或角速度
    #     :param angular_speed_factor: 角速度的比例因子，控制角速度的映射范围
    #     :return: 电机控制速度 (-100 到 100)
    #     """
    #     # 对角速度的映射进行调整
    #     if speed == 0:
    #         motor_speed = 0
    #     else:
    #         if abs(speed) <= max_speed:
    #             motor_speed = (speed / max_speed) * 100
    #         else:
    #             motor_speed = angular_speed_factor * speed
    #             motor_speed = max(-100, min(100, motor_speed))  # 限制在 -100 到 100 之间
        
    #     return motor_speed


    @staticmethod
    def _clamp(value, limit):
        """把速度限制在 ±limit 内。"""
        return max(-limit, min(limit, value))

    @staticmethod
    def _approach(current, target, max_delta):
        """按最大步长逼近目标值，用于加速度限幅。"""
        if target > current:
            return min(target, current + max_delta)
        return max(target, current - max_delta)

    def cmd_vel_callback(self, msg: Twist):
        """
        订阅/cmd_vel话题，先限幅与加速度平滑，再计算四个电机速度。
        :param msg: 订阅的Twist消息
        """
        now = time.monotonic()
        dt = 0.0 if self._last_slew_time is None else min(
            0.5, max(0.0, now - self._last_slew_time))
        self._last_slew_time = now

        # 1) 速度限幅
        target_x = self._clamp(msg.linear.x, self.max_linear_speed)
        target_y = self._clamp(msg.linear.y, self.max_linear_speed)
        target_z = self._clamp(msg.angular.z, self.max_angular_speed)

        # 2) 加速度限幅（按实际 dt 平滑，避免阶跃/急停急起）
        self._slew_linear_x = self._approach(
            self._slew_linear_x, target_x, self.max_linear_accel * dt)
        self._slew_linear_y = self._approach(
            self._slew_linear_y, target_y, self.max_linear_accel * dt)
        self._slew_angular_z = self._approach(
            self._slew_angular_z, target_z, self.max_angular_accel * dt)

        self._last_cmd_time = now
        self._moving = any(abs(v) > 1e-6 for v in (
            self._slew_linear_x, self._slew_linear_y, self._slew_angular_z))

        # 3) 计算并发布电机速度
        motor_msg = self.set_velocity(
            self._slew_linear_x, self._slew_linear_y, self._slew_angular_z)
        self.publisher.publish(motor_msg)

    def watchdog_callback(self):
        """cmd_vel 看门狗：链路中断后主动发布零速度，避免底盘持续运动。"""
        if not self._moving or self._last_cmd_time is None:
            return
        if time.monotonic() - self._last_cmd_time <= self.cmd_vel_timeout:
            return
        # 安全停车不经过加速度斜坡，立即下发零速度
        self._moving = False
        self._slew_linear_x = 0.0
        self._slew_linear_y = 0.0
        self._slew_angular_z = 0.0
        self._last_slew_time = None
        self.get_logger().warn(
            'cmd_vel 已 %.1fs 未更新，触发看门狗停车' % self.cmd_vel_timeout)
        self.publisher.publish(self.set_velocity(0.0, 0.0, 0.0))

    def set_velocity(self, linear_x, linear_y, angular_z):
        """
        根据线性速度和角速度计算每个电机的控制速度
        :param linear_x: x方向线性速度（m/s）
        :param linear_y: y方向线性速度（m/s）
        :param angular_z: 角速度（rad/s）
        :return: MotorsSpeedControl 消息，包含四个电机的速度信息
        """
        # 计算四个电机的速度
        motor1 = (linear_x - linear_y - angular_z * (self.wheelbase + self.track_width) / 2)
        motor2 = (linear_x + linear_y - angular_z * (self.wheelbase + self.track_width) / 2)
        motor3 = (linear_x + linear_y + angular_z * (self.wheelbase + self.track_width) / 2)
        motor4 = (linear_x - linear_y + angular_z * (self.wheelbase + self.track_width) / 2)

        # 映射电机速度到 -100 到 100 范围
        motor_speeds = [self.speed_convert(v, self.max_linear_speed) for v in [-motor1, motor3, -motor2, motor4]]

        msg = MotorsSpeedControl()
        msg.data = []
        for i in range(4):
            motor_msg = MotorSpeedControl()
            motor_msg.id = i + 1  # 电机编号
            motor_msg.speed = float(motor_speeds[i])  # 电机速度
            msg.data.append(motor_msg)

        return msg
    # def set_velocity(self, linear_x, linear_y, angular_z):
    #     """
    #     根据线性速度和角速度计算每个电机的控制速度
    #     :param linear_x: x方向线性速度（m/s）
    #     :param linear_y: y方向线性速度（m/s）
    #     :param angular_z: 角速度（rad/s）
    #     :return: MotorsSpeedControl 消息，包含四个电机的速度信息
    #     """
    #     motor1 = (linear_x - linear_y - angular_z * (self.wheelbase + self.track_width) / 2)
    #     motor2 = (linear_x + linear_y - angular_z * (self.wheelbase + self.track_width) / 2)
    #     motor3 = (linear_x + linear_y + angular_z * (self.wheelbase + self.track_width) / 2)
    #     motor4 = (linear_x - linear_y + angular_z * (self.wheelbase + self.track_width) / 2)

    #     # 使用调整过的speed_convert
    #     motor_speeds = [self.speed_convert(v, self.max_linear_speed, angular_speed_factor=10.0) for v in [-motor1, motor2, -motor3, motor4]]

    #     msg = MotorsSpeedControl()
    #     msg.data = []

    #     for i in range(4):
    #         motor_msg = MotorSpeedControl()
    #         motor_msg.id = i + 1  # 电机编号
    #         motor_msg.speed = float(motor_speeds[i])  # 电机速度
    #         msg.data.append(motor_msg)

    #     return msg

def main(args=None):
    # 初始化rclpy库
    rclpy.init(args=args)

    # 创建MecanumChassis节点
    mecanum_chassis = MecanumChassis()

    # 保持节点运行
    rclpy.spin(mecanum_chassis)

    # 销毁节点
    mecanum_chassis.destroy_node()

    # 关闭rclpy库
    rclpy.shutdown()


if __name__ == '__main__':
    main()
