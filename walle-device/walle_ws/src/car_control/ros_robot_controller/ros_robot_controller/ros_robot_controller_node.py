#!/usr/bin/env python3
# encoding: utf-8
# @Author: Aiden
# @Date: 2023/08/28
# stm32 ros2 package
import math
import time
import rclpy
import signal
import threading
import yaml 
import os
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor
from rclpy.node import Node
from std_srvs.srv import Trigger
from sensor_msgs.msg import Imu, Joy
from std_msgs.msg import UInt16, Bool
from ament_index_python.packages import get_package_share_directory
from ros_robot_controller.ros_robot_controller_sdk import Board, PacketReportKeyEvents
from ros_robot_controller_msgs.srv import GetBusServoState, GetPWMServoState
from ros_robot_controller_msgs.msg import ButtonState, BuzzerState, MotorsState, BusServoState, LedState,SetBusServoState, ServosPosition, SetPWMServoState, Sbus, OLEDState,RGBStates,PWMServoState,MotorsSpeedControl, MotorSpeedControl

class RosRobotController(Node):
    gravity = 9.80665

    def __init__(self, name):
        super().__init__(name)
        self.board = Board()
        self.board.enable_reception()
        # 传感器上报开关（Bool 回调修改）；不能用 self.enable_reception 存状态，
        # 否则会把方法名覆盖成 bool（P2-2.4）
        self._reception_enabled = True
        self.running = True
        # 安全停车相关：串口写/读失败、控制器断连时尽最大努力下发零速度
        self._stop_lock = threading.Lock()
        self._controller_timeout = 1.0
        self._controller_ok = True
        # 串口写操作放在可重入回调组：串口卡顿时不阻塞状态查询等回调
        self._io_group = ReentrantCallbackGroup()

        self.declare_parameter('imu_frame', 'imu_link')
        self.declare_parameter('init_finish', False)
        self.declare_parameter('servo_config', '')
        self.IMU_FRAME = self.get_parameter('imu_frame').value

        self.imu_pub = self.create_publisher(Imu, '~/imu_raw', 1)
        self.joy_pub = self.create_publisher(Joy, '~/joy', 1)
        self.sbus_pub = self.create_publisher(Sbus, '~/sbus', 1)
        self.button_pub = self.create_publisher(ButtonState, '~/button', 1)
        self.battery_pub = self.create_publisher(UInt16, '~/battery', 1)
        self.create_subscription(LedState, '~/set_led', self.set_led_state, 5, callback_group=self._io_group)
        self.create_subscription(BuzzerState, '~/set_buzzer', self.set_buzzer_state, 5, callback_group=self._io_group)
        self.create_subscription(OLEDState, '~/set_oled', self.set_oled_state, 5, callback_group=self._io_group)
        # 低层直控接口：仅供本机 mecanum 节点内部使用，不对外/rosbridge 暴露
        # （rosbridge 白名单见 walle_net/launch/net.launch.py 的 ROSBridge_ALLOWED_TOPICS）
        self.create_subscription(MotorsSpeedControl, '~/internal/set_motor_speeds', self.set_motor_speeds_callback, 10, callback_group=self._io_group)
        self.create_subscription(Bool, '~/internal/enable_reception', self.enable_reception, 1, callback_group=self._io_group)
        self.create_subscription(SetBusServoState, '~/bus_servo/set_state', self.set_bus_servo_state, 10, callback_group=self._io_group)
        self.create_subscription(ServosPosition, '~/bus_servo/set_position', self.set_bus_servo_position, 10, callback_group=self._io_group)
        self.create_subscription(SetPWMServoState, '~/pwm_servo/set_state', self.set_pwm_servo_state, 10, callback_group=self._io_group)
        self.create_service(GetBusServoState, '~/bus_servo/get_state', self.get_bus_servo_state)
        self.create_service(GetPWMServoState, '~/pwm_servo/get_state', self.get_pwm_servo_state)
        self.create_subscription(RGBStates, '~/set_rgb', self.set_rgb_states, 10, callback_group=self._io_group)

        # 加载并设置舵机偏移量从 YAML 文件
        self.load_servo_offsets()

        # self.board.pwm_servo_set_offset(1, 0)  gs del 2027-11-8
        self.board.set_motor_speed([[1, 0], [2, 0], [3, 0], [4, 0]])
        self.clock = self.get_clock()
        threading.Thread(target=self.pub_callback, daemon=True).start()
        self.create_service(Trigger, '~/init_finish', self.get_node_state)
        self.get_logger().info('\033[1;32m%s\033[0m' % 'start')
    
    
    def load_servo_offsets(self):
        """
        从 YAML 文件中读取舵机偏差设置。
        """
        config_path = self.get_parameter('servo_config').value
        if not config_path:
            config_path = os.path.join(
                get_package_share_directory('ros_robot_controller'),
                'config', 'servo_config.yaml')
        try:
            with open(config_path, 'r') as file:
                config = yaml.safe_load(file)

            # 确保config是字典
            if not isinstance(config, dict):
                self.get_logger().error(f"YAML 配置文件格式错误: {config_path}，应为字典格式。")
                return

            # 遍历ID1到ID4并设置偏移量
            for servo_id in range(1, 5):
                offset = config.get(servo_id, 0)  # 如果未找到，默认偏移量为0
                try:
                    self.board.pwm_servo_set_offset(servo_id, offset)
                    self.get_logger().info(f"已设置舵机 {servo_id} 的偏移量为 {offset}")
                except Exception as e:
                    self.get_logger().error(f"设置舵机 {servo_id} 偏移量时出错: {e}")

        except FileNotFoundError:
            self.get_logger().error(f"配置文件未找到: {config_path}")
        except yaml.YAMLError as e:
            self.get_logger().error(f"解析 YAML 文件时出错: {e}")
        except Exception as e:
            self.get_logger().error(f"读取配置文件时出错: {e}")
            
    def get_node_state(self, request, response):
        response.success = True
        return response

    def pub_callback(self):
        last_rx = time.monotonic()
        rx_seen = False
        while self.running:
            if not self._reception_enabled:
                time.sleep(0.02)
                continue
            try:
                now = time.monotonic()
                # 电池遥测约 10Hz，作为主心跳；有 IMU 的底盘也计入
                got = self.pub_battery_data(self.battery_pub)
                got = self.pub_imu_data(self.imu_pub) or got
                self.pub_button_data(self.button_pub)
                self.pub_joy_data(self.joy_pub)
                self.pub_sbus_data(self.sbus_pub)

                if got:
                    last_rx = now
                    rx_seen = True
                    if not self._controller_ok:
                        self._controller_ok = True
                        self.get_logger().info('控制器数据恢复')
                elif rx_seen and self._controller_ok and \
                        now - last_rx > self._controller_timeout:
                    # 已建立过通信，但遥测连续超时，视为控制器断连，先尝试停车
                    self._controller_ok = False
                    self.stop_motors('控制器数据中断')
            except Exception as e:  # noqa: BLE001
                self._controller_ok = False
                self.get_logger().error(f'控制器串口读取异常: {e}')
                self.stop_motors('控制器串口异常')
            time.sleep(0.02)
        if rclpy.ok():
            rclpy.shutdown()

    def enable_reception(self, msg):
        self.get_logger().info('\033[1;32m%s\033[0m' % ('enable_reception ' + str(msg.data)))
        self._reception_enabled = bool(msg.data)
        self.board.enable_reception(self._reception_enabled)

    def set_led_state(self, msg):
        self.board.set_led(msg.on_time, msg.off_time, msg.repeat, msg.id)

    def set_buzzer_state(self, msg):
        self.board.set_buzzer(msg.freq, msg.on_time, msg.off_time, msg.repeat)
    
    def set_rgb_states(self, msg):
        pixels = []
        for state in msg.states:
            pixels.append((state.index, state.red, state.green, state.blue))
        self.board.set_rgb(pixels)


    def set_oled_state(self, msg):
        self.board.set_oled_text(int(msg.index), msg.text)

    def set_motor_speeds_callback(self, msg):
        # 初始化一个空的列表，用于存储电机速度控制数据
        data = []
        
        # 遍历消息中的每个 MotorSpeedControl
        for motor in msg.data:
            motor_id = motor.id  # 获取电机的标识符
            speed = motor.speed  # 获取电机的速度
            
            # 将电机的标识符和速度存储到 data 列表中
            data.append([motor_id, speed])

        # 调用 Board 对象的方法来设置电机的速度；写失败时尝试安全停车
        try:
            self.board.set_motor_duty(data)
        except Exception as e:  # noqa: BLE001
            self.get_logger().error(f'串口写失败: {e}')
            self.stop_motors('串口写失败')

    def stop_motors(self, reason=''):
        """尽最大努力让底盘停转；串口不可用时只记录，不抛异常。"""
        with self._stop_lock:
            try:
                self.board.set_motor_speed([[1, 0], [2, 0], [3, 0], [4, 0]])
                msg = f'{reason}，已下发零速度停车' if reason else '已下发零速度停车'
                try:
                    self.get_logger().warn(msg)
                except Exception:  # rclpy 已关闭时退回 stdout
                    print(msg)
            except Exception as e:  # noqa: BLE001
                msg = f'安全停车失败（{reason}）: {e}'
                try:
                    self.get_logger().error(msg)
                except Exception:  # rclpy 已关闭时退回 stdout
                    print(msg)

    def set_pwm_servo_state(self, msg):
        data = []
        for i in msg.state:
            if i.id and i.position:
                data.extend([[i.id[0], i.position[0]]])
            if i.id and i.offset:
                self.board.pwm_servo_set_offset(i.id[0], i.offset[0])

        if data != []:
            self.board.pwm_servo_set_position(msg.duration, data)

    def get_pwm_servo_state(self, msg):
        states = []
        for i in msg.cmd:
            data = PWMServoState()
            if i.get_position:
                state = self.board.pwm_servo_read_position(i.id)
                if state is not None:
                    data.position = state
            if i.get_offset:
                state = self.board.pwm_servo_read_offset(i.id)
                if state is not None:
                    data.offset = state
            states.append(data)
        return [True, states]

    def set_bus_servo_position(self, msg):
        data = []
        for i in msg.position:
            data.extend([[i.id, i.position]])
        if data:
            self.board.bus_servo_set_position(msg.duration, data)

    def set_bus_servo_state(self, msg):
        data = []
        servo_id = []
        for i in msg.state:
            if i.present_id:
                if i.present_id[0]:
                    if i.target_id:
                        if i.target_id[0]:
                            self.board.bus_servo_set_id(i.present_id[1], i.target_id[1])
                    if i.position:
                        if i.position[0]:
                            data.extend([[i.present_id[1], i.position[1]]])
                    if i.offset:
                        if i.offset[0]:
                            self.board.bus_servo_set_offset(i.present_id[1], i.offset[1])
                    if i.position_limit:
                        if i.position_limit[0]:
                            self.board.bus_servo_set_angle_limit(i.present_id[1], i.position_limit[1:])
                    if i.voltage_limit:
                        if i.voltage_limit[0]:
                            self.board.bus_servo_set_vin_limit(i.present_id[1], i.voltage_limit[1:])
                    if i.max_temperature_limit:
                        if i.max_temperature_limit[0]:
                            self.board.bus_servo_set_temp_limit(i.present_id[1], i.max_temperature_limit[1])
                    if i.enable_torque:
                        if i.enable_torque[0]:
                            self.board.bus_servo_enable_torque(i.present_id[1], i.enable_torque[1])
                    if i.save_offset:
                        if i.save_offset[0]:
                            self.board.bus_servo_save_offset(i.present_id[1])
                    if i.stop:
                        if i.stop[0]:
                            servo_id.append(i.present_id[1])
        if data != []:
            self.board.bus_servo_set_position(msg.duration, data)
        if servo_id != []:    
            self.board.bus_servo_stop(servo_id)

    def get_bus_servo_state(self, request, response):
        states = []
        for i in request.cmd:
            data = BusServoState()
            if i.get_id:
                state = self.board.bus_servo_read_id(i.id)
                if state is not None:
                    i.id = state[0]
                    data.present_id = state
            if i.get_position:
                state = self.board.bus_servo_read_position(i.id)
                if state is not None:
                    data.position = state
            if i.get_offset:
                state = self.board.bus_servo_read_offset(i.id)
                if state is not None:
                    data.offset = state
            if i.get_voltage:
                state = self.board.bus_servo_read_voltage(i.id)
                if state is not None:
                    data.voltage = state
            if i.get_temperature:
                state = self.board.bus_servo_read_temp(i.id)
                if state is not None:
                    data.temperature = state
            if i.get_position_limit:
                state = self.board.bus_servo_read_angle_limit(i.id)
                if state is not None:
                    data.position_limit = state
            if i.get_voltage_limit:
                state = self.board.bus_servo_read_vin_limit(i.id)
                if state is not None:
                    data.voltage_limit = state
            if i.get_max_temperature_limit:
                state = self.board.bus_servo_read_temp_limit(i.id)
                if state is not None:
                    data.max_temperature_limit = state
            if i.get_torque_state:
                state = self.board.bus_servo_read_torque(i.id)
                if state is not None:
                    data.enable_torque = state
            states.append(data)
        response.state = states
        response.success = True
        return response

    def pub_battery_data(self, pub):
        data = self.board.get_battery()
        if data is None:
            return False
        msg = UInt16()
        msg.data = data
        pub.publish(msg)
        return True

    def pub_button_data(self, pub):
        data = self.board.get_button()
        if data is not None:
            key_id, key_event = data
            state_map = {
                PacketReportKeyEvents.KEY_EVENT_PRESSED: 1,
                PacketReportKeyEvents.KEY_EVENT_LONGPRESS: 2,
                PacketReportKeyEvents.KEY_EVENT_LONGPRESS_REPEAT: 3,
                PacketReportKeyEvents.KEY_EVENT_RELEASE_FROM_LP: 4,
                PacketReportKeyEvents.KEY_EVENT_RELEASE_FROM_SP: 0,
                PacketReportKeyEvents.KEY_EVENT_CLICK: 5,
                PacketReportKeyEvents.KEY_EVENT_DOUBLE_CLICK: 6,
                PacketReportKeyEvents.KEY_EVENT_TRIPLE_CLICK: 7,
            }
            state = state_map.get(key_event, -1)

            if state != -1:
                msg = ButtonState()
                msg.id = key_id
                msg.state = state
                pub.publish(msg)
            else:
                self.get_logger().error(f"Unhandled button event: {key_event}")


    def pub_joy_data(self, pub):
        data = self.board.get_gamepad()
        if data is not None:
            msg = Joy()
            msg.axes = data[0]
            msg.buttons = data[1]
            msg.header.stamp = self.clock.now().to_msg()
            pub.publish(msg)

    def pub_sbus_data(self, pub):
        data = self.board.get_sbus()
        if data is not None:
            msg = Sbus()
            msg.channel = data
            msg.header.stamp = self.clock.now().to_msg()
            pub.publish(msg)

    def pub_imu_data(self, pub):
        data = self.board.get_imu()
        if data is None:
            return False
        ax, ay, az, gx, gy, gz = data
        msg = Imu()
        msg.header.frame_id = self.IMU_FRAME
        msg.header.stamp = self.clock.now().to_msg()

        msg.orientation.w = 0.0
        msg.orientation.x = 0.0
        msg.orientation.y = 0.0
        msg.orientation.z = 0.0

        msg.linear_acceleration.x = ax * self.gravity
        msg.linear_acceleration.y = ay * self.gravity
        msg.linear_acceleration.z = az * self.gravity

        msg.angular_velocity.x = math.radians(gx)
        msg.angular_velocity.y = math.radians(gy)
        msg.angular_velocity.z = math.radians(gz)

        msg.orientation_covariance = [0.01, 0.0, 0.0, 0.0, 0.01, 0.0, 0.0, 0.0, 0.01]
        msg.angular_velocity_covariance = [0.01, 0.0, 0.0, 0.0, 0.01, 0.0, 0.0, 0.0, 0.01]
        msg.linear_acceleration_covariance = [0.0004, 0.0, 0.0, 0.0, 0.0004, 0.0, 0.0, 0.0, 0.004]
        pub.publish(msg)
        return True

def main():
    rclpy.init()
    node = RosRobotController('ros_robot_controller')

    def handle_signal(signum, _frame):
        node.get_logger().warn(f'收到信号 {signum}，准备安全停车')
        node.running = False
        try:
            rclpy.shutdown()
        except Exception:  # 已关闭时忽略
            pass

    # systemd 默认发 SIGTERM；必须显式接管，否则不会执行停车逻辑
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        executor = MultiThreadedExecutor()
        executor.add_node(node)
        executor.spin()
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.running = False
        node.stop_motors('进程退出')
        node.destroy_node()
        try:
            rclpy.shutdown()
        except Exception:  # 已关闭时忽略
            pass
        print('shutdown finish')

if __name__ == '__main__':
    main()



