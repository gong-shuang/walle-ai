import os
import subprocess

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (DeclareLaunchArgument, ExecuteProcess, LogInfo,
                            OpaqueFunction)
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

# 默认值仅用于单独启动本 launch；bringup 会用 walle_camera.conf 覆盖。
CAMERA_DEVICE = '/dev/video0'
DEFAULT_FORMAT = 'MJPG'
DEFAULT_WIDTH = 640
DEFAULT_HEIGHT = 480
DEFAULT_FRAMERATE = 30.0
PIXEL_FORMATS = {'MJPG': 'mjpeg2rgb', 'YUYV': 'yuyv2rgb'}


def _is_usb_camera(device):
    """确认设备指向 USB 摄像头节点，而不是 ISP/编解码节点。"""
    node = os.path.basename(os.path.realpath(device))
    sys_device = os.path.join('/sys/class/video4linux', node, 'device')
    return os.path.islink(sys_device) and 'usb' in os.path.realpath(sys_device).lower()


def _preflight(device, fmt, width, height):
    """启动前校验设备存在、是 USB 摄像头且支持配置的分辨率/格式。"""
    if not os.path.exists(device):
        return False, f'{device} 不存在（请确认摄像头已插好，或重新运行 auto_gen_camera_conf.sh）'
    if not _is_usb_camera(device):
        return False, f'{device} 不是 USB 摄像头节点（可能误指向 ISP/编解码节点）'
    try:
        proc = subprocess.run(
            ['v4l2-ctl', '-d', device, '--list-formats-ext'],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5)
    except FileNotFoundError:
        return False, '未安装 v4l2-ctl（sudo apt install -y v4l-utils）'
    except subprocess.TimeoutExpired:
        return False, 'v4l2-ctl 探测超时'
    out = proc.stdout or ''
    if proc.returncode != 0:
        return False, f'v4l2-ctl 返回 {proc.returncode}'
    if f"'{fmt}'" not in out:
        return False, f'不支持 {fmt} 格式'
    if f'{width}x{height}' not in out:
        return False, f'不支持 {width}x{height} 分辨率'
    return True, 'ok'


def launch_setup(context):
    video_package_path = get_package_share_directory('walle_video')
    device = LaunchConfiguration('camera_device').perform(context).strip() or CAMERA_DEVICE
    fmt = LaunchConfiguration('camera_format').perform(context).strip().upper()
    if fmt == 'MJPEG':
        fmt = 'MJPG'
    if fmt not in PIXEL_FORMATS:
        return [LogInfo(msg=f'[usb_cam] 不支持的格式 {fmt}（仅 MJPG/YUYV）；跳过启动')]
    try:
        width = int(LaunchConfiguration('camera_width').perform(context))
        height = int(LaunchConfiguration('camera_height').perform(context))
        fps = float(LaunchConfiguration('camera_framerate').perform(context))
    except ValueError:
        return [LogInfo(msg='[usb_cam] 分辨率/帧率配置无效；跳过启动')]

    ok, reason = _preflight(device, fmt, width, height)
    if not ok:
        # 不启动 usb_cam，避免退回扫描其他 /dev/videoN 而误开 ISP/编解码节点
        return [LogInfo(msg=f'[usb_cam] 前置检查失败：{reason}；跳过启动')]

    # kernel 6.8 的 UVC 控制名与 usb_cam 0.8.1 不一致，usb_cam 的设置会失败并
    # 打印 "unknown control"（已知告警，画面不受影响）。这里用 v4l2-ctl 按新
    # 名称显式开启自动白平衡/自动曝光/连续自动对焦，保证功能符合预期。
    camera_controls = ExecuteProcess(
        cmd=[
            'v4l2-ctl', '-d', device,
            '-c', 'white_balance_automatic=1',
            '-c', 'auto_exposure=3',
            '-c', 'focus_automatic_continuous=1',
        ],
        output='screen',
    )

    camera_node = Node(
        package='usb_cam',
        executable='usb_cam_node_exe',
        output='screen',
        name='usb_cam',
        parameters=[
            os.path.join(video_package_path, 'config', 'usb_cam_param.yaml'),
            {
                'video_device': device,
                'pixel_format': PIXEL_FORMATS[fmt],
                'image_width': width,
                'image_height': height,
                'framerate': fps,
            },
        ],
        remappings=[],
        respawn=True,
        respawn_delay=5.0,
    )

    return [camera_controls, camera_node]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument(
            'camera_device', default_value=CAMERA_DEVICE,
            description='Camera device node, e.g. /dev/video0'),
        DeclareLaunchArgument(
            'camera_format', default_value=DEFAULT_FORMAT,
            description='Capture format: MJPG | YUYV'),
        DeclareLaunchArgument(
            'camera_width', default_value=str(DEFAULT_WIDTH),
            description='Capture width'),
        DeclareLaunchArgument(
            'camera_height', default_value=str(DEFAULT_HEIGHT),
            description='Capture height'),
        DeclareLaunchArgument(
            'camera_framerate', default_value=str(DEFAULT_FRAMERATE),
            description='Capture framerate'),
        OpaqueFunction(function=launch_setup),
    ])
