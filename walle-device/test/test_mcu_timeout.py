#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0-3.3 MCU 通信超时停车验证脚本。

危险：脚本会真实驱动电机。运行前必须把车轮架空！

用法：
    python3 test/test_mcu_timeout.py --speed 20 --pulse 0.3 --observe 2.0

流程：
    1. 以 20Hz 发送低占空比速度，持续 --pulse 秒；
    2. 停止发送并开始计时；
    3. 人工观察车轮是否在 --timeout 秒内自动停转。

若车轮没有自动停止，说明当前 MCU 固件尚未实现通信超时停车（P0-3.3）。
"""
import argparse
import sys
import time
from pathlib import Path

SDK_ROOT = (Path(__file__).resolve().parents[1]
            / 'ros2_ws/src/car_control/ros_robot_controller')
sys.path.insert(0, str(SDK_ROOT))

from ros_robot_controller.ros_robot_controller_sdk import Board  # noqa: E402

MOTOR_IDS = (1, 2, 3, 4)


def zero_speed(board):
    """尽最大努力让四轮停转。"""
    for _ in range(3):
        try:
            board.set_motor_speed([[i, 0] for i in MOTOR_IDS])
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.05)


def main():
    parser = argparse.ArgumentParser(
        description='P0-3.3 固件通信超时停车验证（必须架空车轮）')
    parser.add_argument('--device', default='/dev/rrc', help='串口设备')
    parser.add_argument('--speed', type=float, default=20.0,
                        help='测试占空比，范围 -100~100，默认 20')
    parser.add_argument('--pulse', type=float, default=0.3,
                        help='持续发送时长（秒），默认 0.3')
    parser.add_argument('--timeout', type=float, default=0.5,
                        help='期望的固件超时停车时间（秒），默认 0.5')
    parser.add_argument('--observe', type=float, default=2.0,
                        help='停止发送后的观察时长（秒），默认 2.0')
    args = parser.parse_args()

    if not -100 <= args.speed <= 100:
        parser.error('--speed 必须在 -100~100 之间')

    print('=' * 60)
    print(' 危险：本脚本会真实驱动电机，请务必把车轮架空！')
    print(f' 设备={args.device} 占空比={args.speed} 发送={args.pulse}s '
          f'期望超时={args.timeout}s')
    print('=' * 60)
    if input('确认车轮已架空，输入 LIFTED 继续：').strip() != 'LIFTED':
        print('已取消')
        return 1

    try:
        board = Board(device=args.device)
    except Exception as e:  # noqa: BLE001
        print(f'打开串口失败（{args.device}）: {e}')
        return 3
    board.enable_reception(True)
    speed = [[i, args.speed] for i in MOTOR_IDS]
    try:
        print(f'开始以 20Hz 发送 {args.pulse}s ...')
        end = time.monotonic() + args.pulse
        while time.monotonic() < end:
            board.set_motor_duty(speed)
            time.sleep(0.05)

        print('停止发送，开始观察（Ctrl-C 可随时中断并停车）')
        time.sleep(args.observe)
        answer = input(
            f'车轮是否在 {args.timeout}s 内自动停转？[y/N]：').strip().lower()
    except KeyboardInterrupt:
        print('\n已中断')
        answer = ''
    finally:
        zero_speed(board)

    if answer == 'y':
        print('PASS：固件已具备通信超时停车能力')
        return 0
    print('FAIL：未观察到自动停车，需按 '
          'doc/mcu-comm-timeout-requirement.md 修改固件')
    return 2


if __name__ == '__main__':
    sys.exit(main())
