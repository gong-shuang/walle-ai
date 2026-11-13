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
    camera_format = LaunchConfiguration('camera_format')
    camera_width = LaunchConfiguration('camera_width')
    camera_height = LaunchConfiguration('camera_height')
    framerate = LaunchConfiguration('framerate')
    ros_image_fps = LaunchConfiguration('ros_image_fps')
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
            'camera_format', default_value='MJPG',
            description='Camera capture format: MJPG | YUYV'),
        DeclareLaunchArgument(
            'camera_width', default_value='640',
            description='Camera capture width'),
        DeclareLaunchArgument(
            'camera_height', default_value='480',
            description='Camera capture height'),
        DeclareLaunchArgument(
            'framerate', default_value='30.0',
            description='Camera capture framerate'),
        DeclareLaunchArgument(
            'ros_image_fps', default_value='10.0',
            description='/image_raw target fps when camera_owner=true'),
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
                    'camera_format': camera_format,
                    'camera_width': ParameterValue(camera_width, value_type=int),
                    'camera_height': ParameterValue(camera_height, value_type=int),
                    'framerate': ParameterValue(framerate, value_type=float),
                    'ros_image_fps': ParameterValue(
                        ros_image_fps, value_type=float),
                    'shm_socket': shm_socket,
                },
            ],
            respawn=respawn,
            respawn_delay=5.0,
        ),
    ])
