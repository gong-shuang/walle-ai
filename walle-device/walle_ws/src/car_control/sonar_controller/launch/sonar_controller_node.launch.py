from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    sonar_controller_node = Node(
        package='sonar_controller',
        executable='sonar_controller',
        output='screen',
    )

    return LaunchDescription([
        sonar_controller_node
    ])
