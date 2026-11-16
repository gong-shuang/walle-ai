#!/usr/bin/env python3
"""Mecanum kinematics and Twist limiting unit tests (P1-5.2).

不启动 rclpy：用 object.__new__ 绕过 Node 初始化，只验证纯运动学与限幅逻辑。
"""

from controller import mecanum as mecanum_module
from controller.mecanum import MecanumChassis
from geometry_msgs.msg import Twist
import pytest

WHEELBASE = 0.1368
TRACK_WIDTH = 0.1410
K = (WHEELBASE + TRACK_WIDTH) / 2      # 自转力臂
MAX_LINEAR = 1.0
MAX_ANGULAR = 3.0


class _FakeClock:
    """Manually advanced clock for deterministic acceleration tests."""

    def __init__(self, now=1000.0):
        self.now = now

    def monotonic(self):
        return self.now


class _FakePublisher:
    def __init__(self):
        self.messages = []

    def publish(self, msg):
        self.messages.append(msg)


class _FakeLogger:
    def warn(self, *_args, **_kwargs):
        pass


@pytest.fixture
def chassis(monkeypatch):
    """构造只带运动学状态的底盘对象（不初始化 ROS 节点）."""
    chassis = object.__new__(MecanumChassis)
    chassis.wheelbase = WHEELBASE
    chassis.track_width = TRACK_WIDTH
    chassis.max_linear_speed = MAX_LINEAR
    chassis.max_angular_speed = MAX_ANGULAR
    chassis.max_linear_accel = 1.0
    chassis.max_angular_accel = 1.0
    chassis._slew_linear_x = 0.0
    chassis._slew_linear_y = 0.0
    chassis._slew_angular_z = 0.0
    chassis._last_slew_time = None
    chassis._last_cmd_time = None
    chassis._moving = False
    chassis.cmd_vel_timeout = 1.0
    chassis.publisher = _FakePublisher()
    chassis.get_logger = lambda: _FakeLogger()
    monkeypatch.setattr(mecanum_module, 'time', _FakeClock())
    return chassis


def _speeds(msg):
    return [motor.speed for motor in msg.data]


@pytest.mark.parametrize('speed,max_speed,expected', [
    (0.5, 1.0, 50.0),
    (0.1, 0.5, 20.0),
    (0.0, 1.0, 0.0),
    (2.0, 1.0, 100.0),
    (-2.0, 1.0, -100.0),
])
def test_speed_convert_maps_and_clamps(speed, max_speed, expected):
    assert MecanumChassis.speed_convert(None, speed, max_speed) == pytest.approx(expected)


@pytest.mark.parametrize('value,limit,expected', [
    (5.0, 1.0, 1.0),
    (-5.0, 1.0, -1.0),
    (0.3, 1.0, 0.3),
    (0.0, 1.0, 0.0),
])
def test_clamp(value, limit, expected):
    assert MecanumChassis._clamp(value, limit) == pytest.approx(expected)


@pytest.mark.parametrize('current,target,delta,expected', [
    (0.0, 1.0, 0.2, 0.2),
    (0.0, 1.0, 5.0, 1.0),
    (1.0, 0.0, 0.2, 0.8),
    (0.9, 1.0, 0.2, 1.0),
])
def test_approach(current, target, delta, expected):
    assert MecanumChassis._approach(current, target, delta) == pytest.approx(expected)


def test_set_velocity_forward(chassis):
    msg = chassis.set_velocity(0.5, 0.0, 0.0)
    assert [motor.id for motor in msg.data] == [1, 2, 3, 4]
    assert _speeds(msg) == pytest.approx([-50.0, 50.0, -50.0, 50.0])


def test_set_velocity_strafe_left(chassis):
    msg = chassis.set_velocity(0.0, 0.5, 0.0)
    assert _speeds(msg) == pytest.approx([50.0, 50.0, -50.0, -50.0])


def test_set_velocity_rotate(chassis):
    msg = chassis.set_velocity(0.0, 0.0, 1.0)
    expected = K * 100.0
    assert _speeds(msg) == pytest.approx([expected] * 4)


def test_set_velocity_combines_and_clamps(chassis):
    msg = chassis.set_velocity(1.0, 1.0, 3.0)
    expected = [
        (1.0 - 1.0 + 3.0 * K) * 100.0,      # 电机 1
        100.0,                              # 电机 2 超出 ±100，被限幅
        -100.0,                             # 电机 3 超出 ±100，被限幅
        (1.0 - 1.0 + 3.0 * K) * 100.0,      # 电机 4
    ]
    assert _speeds(msg) == pytest.approx(expected)


def test_set_velocity_zero(chassis):
    assert _speeds(chassis.set_velocity(0.0, 0.0, 0.0)) == pytest.approx([0.0] * 4)


def test_cmd_vel_callback_ramps_acceleration(chassis):
    msg = Twist()
    msg.linear.x = 0.5

    # 第一帧 dt=0，从 0 起步，不允许阶跃
    chassis.cmd_vel_callback(msg)
    assert _speeds(chassis.publisher.messages[-1]) == pytest.approx([0.0] * 4)

    # 100ms 后按 1.0m/s² 只允许加速到 0.1m/s
    mecanum_module.time.now += 0.1
    chassis.cmd_vel_callback(msg)
    assert _speeds(chassis.publisher.messages[-1]) == pytest.approx(
        [-10.0, 10.0, -10.0, 10.0], abs=1e-3)


def test_cmd_vel_zero_accel_is_immediate(chassis):
    """加速度参数为 0 时关闭斜坡：第一帧就达到目标速度。"""
    chassis.max_linear_accel = 0.0
    chassis.max_angular_accel = 0.0
    msg = Twist()
    msg.linear.x = 0.5
    chassis.cmd_vel_callback(msg)
    assert _speeds(chassis.publisher.messages[-1]) == pytest.approx(
        [-50.0, 50.0, -50.0, 50.0], abs=1e-3)


def test_cmd_vel_callback_clamps_twist(chassis):
    chassis.max_linear_speed = 0.5
    chassis.max_angular_speed = 0.5
    chassis.max_linear_accel = 100.0
    chassis.max_angular_accel = 1000.0
    chassis._last_slew_time = mecanum_module.time.now - 0.5   # 本帧允许加速到目标值

    msg = Twist()
    msg.linear.x = 10.0
    msg.linear.y = 10.0
    msg.angular.z = 10.0
    chassis.cmd_vel_callback(msg)

    # linear.x / linear.y 限到 0.5，angular.z 限到 0.5；
    # 电机速度按 max_linear_speed=0.5 归一化到 ±100
    expected = [K * 100.0, 100.0, -100.0, K * 100.0]
    assert _speeds(chassis.publisher.messages[-1]) == pytest.approx(expected)
    assert all(-100.0 <= speed <= 100.0
               for speed in _speeds(chassis.publisher.messages[-1]))


def test_watchdog_stops_after_cmd_vel_timeout(chassis):
    msg = Twist()
    msg.linear.x = 0.5
    chassis.max_linear_accel = 100.0
    chassis._last_slew_time = mecanum_module.time.now - 0.5
    chassis.cmd_vel_callback(msg)
    assert chassis._moving

    # 超时前不动
    mecanum_module.time.now += 0.5
    published = len(chassis.publisher.messages)
    chassis.watchdog_callback()
    assert len(chassis.publisher.messages) == published

    # 超过 cmd_vel_timeout 后立即下发零速度
    mecanum_module.time.now += 1.0
    chassis.watchdog_callback()
    assert _speeds(chassis.publisher.messages[-1]) == pytest.approx([0.0] * 4)
    assert not chassis._moving
