import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    package_path = get_package_share_directory('record_video')
    params_file = LaunchConfiguration('params_file')
    camera_device = LaunchConfiguration('camera_device')
    width = LaunchConfiguration('width')
    height = LaunchConfiguration('height')
    framerate = LaunchConfiguration('framerate')
    storage_path = LaunchConfiguration('storage_path')
    storage_limit_gb = LaunchConfiguration('storage_limit_gb')
    segment_duration_sec = LaunchConfiguration('segment_duration_sec')
    auto_record = LaunchConfiguration('auto_record')
    http_port = LaunchConfiguration('http_port')

    return LaunchDescription([
        DeclareLaunchArgument(
            'params_file',
            default_value=os.path.join(
                package_path, 'config', 'record_video.yaml'),
            description='record_video parameter file'),
        DeclareLaunchArgument(
            'camera_device', default_value='/dev/video0',
            description='Camera device node publishing /image_raw'),
        DeclareLaunchArgument(
            'width', default_value='1280',
            description='Image width'),
        DeclareLaunchArgument(
            'height', default_value='720',
            description='Image height'),
        DeclareLaunchArgument(
            'framerate', default_value='30.0',
            description='Camera capture framerate'),
        DeclareLaunchArgument(
            'storage_path', default_value='',
            description='Recording storage directory'),
        DeclareLaunchArgument(
            'storage_limit_gb', default_value='16.0',
            description='Storage quota in GB'),
        DeclareLaunchArgument(
            'segment_duration_sec', default_value='3600',
            description='Single recording segment duration in seconds'),
        DeclareLaunchArgument(
            'auto_record', default_value='true',
            description='Start recording automatically after startup is ready'),
        DeclareLaunchArgument(
            'http_port', default_value='8088',
            description='HTTP playback server port'),

        Node(
            package='record_video',
            executable='record_video_node',
            name='record_video_node',
            output='screen',
            emulate_tty=True,
            parameters=[
                params_file,
                {
                    'camera_device': camera_device,
                    'width': ParameterValue(width, value_type=int),
                    'height': ParameterValue(height, value_type=int),
                    'framerate': ParameterValue(framerate, value_type=float),
                    'storage_path': storage_path,
                    'storage_limit_gb':
                        ParameterValue(storage_limit_gb, value_type=float),
                    'segment_duration_sec':
                        ParameterValue(segment_duration_sec, value_type=int),
                    'auto_record': ParameterValue(auto_record, value_type=bool),
                    'http_port': ParameterValue(http_port, value_type=int),
                },
            ],
            respawn=True,
            respawn_delay=5.0,
        ),
    ])
