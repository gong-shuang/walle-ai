import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import ExecuteProcess
from launch_ros.actions import Node


def generate_launch_description():
    video_package_path = get_package_share_directory('walle_video')

    # kernel 6.8 的 UVC 控制名与 usb_cam 0.8.1 不一致，usb_cam 的设置会失败并
    # 打印 "unknown control"（已知告警，画面不受影响）。这里用 v4l2-ctl 按新
    # 名称显式开启自动白平衡/自动曝光/连续自动对焦，保证功能符合预期。
    camera_controls = ExecuteProcess(
        cmd=[
            'v4l2-ctl', '-d', '/dev/walle-camera',
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

    return LaunchDescription([camera_controls, camera_node])
