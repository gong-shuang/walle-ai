from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    speech_node = Node(
        package='walle_audio',
        executable='speech',
        output='screen',
    )

    return LaunchDescription([
        speech_node
    ])
