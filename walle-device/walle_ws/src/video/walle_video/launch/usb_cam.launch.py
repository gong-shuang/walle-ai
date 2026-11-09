import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    video_package_path = get_package_share_directory('walle_video')

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

    return LaunchDescription([camera_node])
