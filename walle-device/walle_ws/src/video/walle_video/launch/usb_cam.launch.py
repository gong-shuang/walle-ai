import os
import subprocess

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import ExecuteProcess, LogInfo, OpaqueFunction
from launch_ros.actions import Node

CAMERA_DEVICE = '/dev/walle-camera'
REQUIRED_FORMAT = 'MJPG'
REQUIRED_WIDTH = 640
REQUIRED_HEIGHT = 480


def _is_usb_camera(device):
    """确认设备指向 USB 摄像头节点，而不是 ISP/编解码节点。"""
    node = os.path.basename(os.path.realpath(device))
    sys_device = os.path.join('/sys/class/video4linux', node, 'device')
    return os.path.islink(sys_device) and 'usb' in os.path.realpath(sys_device).lower()


def _preflight(device):
    """启动前校验设备存在、是 USB 摄像头且支持配置的分辨率/格式。"""
    if not os.path.exists(device):
        return False, f'{device} 不存在（请安装 99-walle-camera.rules 后重插摄像头）'
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
    if REQUIRED_FORMAT not in out:
        return False, f'不支持 {REQUIRED_FORMAT} 格式'
    if f'{REQUIRED_WIDTH}x{REQUIRED_HEIGHT}' not in out:
        return False, f'不支持 {REQUIRED_WIDTH}x{REQUIRED_HEIGHT} 分辨率'
    return True, 'ok'


def launch_setup(context):
    video_package_path = get_package_share_directory('walle_video')

    ok, reason = _preflight(CAMERA_DEVICE)
    if not ok:
        # 不启动 usb_cam，避免退回扫描其他 /dev/videoN 而误开 ISP/编解码节点
        return [LogInfo(msg=f'[usb_cam] 前置检查失败：{reason}；跳过启动')]

    # kernel 6.8 的 UVC 控制名与 usb_cam 0.8.1 不一致，usb_cam 的设置会失败并
    # 打印 "unknown control"（已知告警，画面不受影响）。这里用 v4l2-ctl 按新
    # 名称显式开启自动白平衡/自动曝光/连续自动对焦，保证功能符合预期。
    camera_controls = ExecuteProcess(
        cmd=[
            'v4l2-ctl', '-d', CAMERA_DEVICE,
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
        parameters=[os.path.join(video_package_path, 'config', 'usb_cam_param.yaml')],
        remappings=[],
        respawn=True,
        respawn_delay=5.0,
    )

    return [camera_controls, camera_node]


def generate_launch_description():
    return LaunchDescription([OpaqueFunction(function=launch_setup)])
