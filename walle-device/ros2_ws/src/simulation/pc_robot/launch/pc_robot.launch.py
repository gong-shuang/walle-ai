import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    package_path = get_package_share_directory('pc_robot')
    params_file = LaunchConfiguration('params_file')

    return LaunchDescription([
        DeclareLaunchArgument(
            'params_file',
            default_value=os.path.join(
                package_path, 'config', 'pc_robot.yaml'),
            description='pc_robot parameter file'),

        Node(
            package='pc_robot',
            executable='pc_robot',
            name='pc_robot',
            output='screen',
            emulate_tty=True,
            parameters=[params_file],
        ),
    ])
