import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    package_path = get_package_share_directory('video_webrtc_bridge')
    params_file = LaunchConfiguration('params_file')
    env_file = LaunchConfiguration('env_file')
    respawn = LaunchConfiguration('respawn')

    return LaunchDescription([
        DeclareLaunchArgument(
            'params_file',
            default_value=os.path.join(
                package_path, 'config', 'video_webrtc_bridge.yaml'),
            description='video_webrtc_bridge parameter file'),
        DeclareLaunchArgument(
            'env_file',
            default_value='/etc/walle-ai.env',
            description='env file providing WHIP_SECRET'),
        DeclareLaunchArgument(
            'respawn', default_value='true',
            description='restart the node if it exits'),

        Node(
            package='video_webrtc_bridge',
            executable='video_webrtc_bridge',
            name='video_webrtc_bridge',
            output='screen',
            emulate_tty=True,
            parameters=[
                params_file,
                {'env_file': env_file},
            ],
            respawn=respawn,
            respawn_delay=5.0,
        ),
    ])
