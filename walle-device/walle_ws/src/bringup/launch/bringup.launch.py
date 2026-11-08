import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (DeclareLaunchArgument, IncludeLaunchDescription,
                            OpaqueFunction, TimerAction)
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def _default_enable_rosbridge():
    """按 WALLE_RUNTIME_ENV 决定 rosbridge 默认值：debug 开启，prod/未设置关闭。"""
    runtime_env = os.environ.get('WALLE_RUNTIME_ENV', 'prod').strip().lower()
    return 'true' if runtime_env == 'debug' else 'false'


def launch_setup(context):
    # 获取包的路径
    controller_package_path = get_package_share_directory('controller')
    large_models_package_path = get_package_share_directory('large_models')
    sonar_controller_package_path = get_package_share_directory('sonar_controller')
    walle_audio_package_path = get_package_share_directory('walle_audio')
    walle_net_package_path = get_package_share_directory('walle_net')
    walle_video_package_path = get_package_share_directory('walle_video')
    net_mqtt_bridge_package_path = get_package_share_directory('net_mqtt_bridge')
    video_webrtc_bridge_package_path = get_package_share_directory('video_webrtc_bridge')

    sonar_enabled = LaunchConfiguration('sonar', default='true')
    # rosbridge 仅用于开发调试（web 测试页 / 快速定位）
    # 默认值由 env_walle.sh 的 WALLE_RUNTIME_ENV 控制：debug=true，prod=false
    enable_rosbridge = LaunchConfiguration(
        'enable_rosbridge', default=_default_enable_rosbridge())

    # 启动 controller 的 launch 文件（ros_robot_controller + mecanum_chassis_node）
    controller_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(controller_package_path, 'launch/controller.launch.py')),
    )

    # 启动 rosbridge_server / rosapi（walle_net 仅做启动编排）
    # 仅调试时开启：ros2 launch bringup bringup.launch.py enable_rosbridge:=true
    net_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(walle_net_package_path, 'launch/net.launch.py')),
        condition=IfCondition(enable_rosbridge),
    )

    # 启动 web_video_server 进程
    web_video_server_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(walle_video_package_path, 'launch/web_video_server.launch.py')),
    )

    # 启动 MQTT -> ROS2 桥（唯一 MQTT 客户端）
    net_mqtt_bridge_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(net_mqtt_bridge_package_path,
                         'launch/net_mqtt_bridge.launch.py')),
    )

    # 启动摄像头相关节点
    usb_cam_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(walle_video_package_path, 'launch/usb_cam.launch.py')),
    )

    # 启动 /image_raw -> RTMP/WebRTC 推流桥
    video_webrtc_bridge_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(video_webrtc_bridge_package_path,
                         'launch/video_webrtc_bridge.launch.py')),
    )

    # 启动声呐节点（默认启用，可通过 sonar:=false 关闭）
    sonar_controller_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(sonar_controller_package_path, 'launch/sonar_controller_node.launch.py')),
        condition=IfCondition(sonar_enabled),
    )

    # 启动大模型相关节点（vocal_detect / agent_process / tts_node）
    large_models_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(large_models_package_path, 'launch/start.launch.py')),
    )

    # 启动语音盒子控制节点（IIC 语音盒子 -> 底盘 / 大模型 ASR）
    speech_recognition_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(walle_audio_package_path, 'launch/speech.launch.py')),
    )

    startup_check_node = Node(
        package='bringup',
        executable='startup_check',
        output='screen',
    )

    delayed_startup_check = TimerAction(
        period=10.0,  # 10秒后执行 startup_check_node
        actions=[startup_check_node]
    )

    return [
        net_launch,
        web_video_server_launch,
        controller_launch,
        net_mqtt_bridge_launch,
        usb_cam_launch,
        video_webrtc_bridge_launch,
        sonar_controller_launch,
        large_models_launch,
        speech_recognition_launch,
        delayed_startup_check
    ]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument(
            'sonar', default_value='true',
            description='Start sonar controller node'),
        DeclareLaunchArgument(
            'enable_rosbridge', default_value=_default_enable_rosbridge(),
            description='Start rosbridge websocket server (default: WALLE_RUNTIME_ENV=debug)'),
        OpaqueFunction(function=launch_setup)
    ])
