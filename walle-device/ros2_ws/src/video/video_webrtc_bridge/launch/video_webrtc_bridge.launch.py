import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    package_path = get_package_share_directory('video_webrtc_bridge')
    params_file = LaunchConfiguration('params_file')
    env_file = LaunchConfiguration('env_file')
    respawn = LaunchConfiguration('respawn')
    camera_owner = LaunchConfiguration('camera_owner')
    camera_device = LaunchConfiguration('camera_device')
    shm_socket = LaunchConfiguration('shm_socket')

    return LaunchDescription([
        DeclareLaunchArgument(
            'params_file',
            default_value=os.path.join(
                package_path, 'config', 'video_webrtc_bridge.yaml'),
            description='video_webrtc_bridge parameter file'),
        DeclareLaunchArgument(
            'env_file',
            default_value='/etc/walle-ai.conf',
            description='env file providing MEDIA_SERVER_HOST / MEDIA_SERVER_SECRET'),
        DeclareLaunchArgument(
            'respawn', default_value='true',
            description='restart the node if it exits'),
        DeclareLaunchArgument(
            'camera_owner', default_value='false',
            description='GStreamer owns the camera and publishes /image_raw'),
        DeclareLaunchArgument(
            'camera_device', default_value='/dev/video0',
            description='Camera device node used when camera_owner=true'),
        DeclareLaunchArgument(
            'shm_socket', default_value='',
            description='Read H.264 from walle_camera shmsink when non-empty'),

        Node(
            package='video_webrtc_bridge',
            executable='video_webrtc_bridge',
            name='video_webrtc_bridge',
            output='screen',
            emulate_tty=True,
            parameters=[
                params_file,
                {
                    'env_file': env_file,
                    'camera_owner': ParameterValue(camera_owner, value_type=bool),
                    'camera_device': camera_device,
                    'shm_socket': shm_socket,
                },
            ],
            respawn=respawn,
            respawn_delay=5.0,
        ),
    ])
