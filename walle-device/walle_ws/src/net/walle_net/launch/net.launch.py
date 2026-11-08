import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import AnyLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration


def generate_launch_description():
    # 参数名加 rosbridge_ 前缀：include 的 launch 参数是全局的，
    # 避免通用的 port/address 泄漏给其它被 include 的 launch 文件。
    rosbridge_port = LaunchConfiguration('rosbridge_port', default='9090')
    rosbridge_address = LaunchConfiguration('rosbridge_address', default='')

    declare_port = DeclareLaunchArgument(
        'rosbridge_port', default_value=rosbridge_port,
        description='rosbridge websocket port')
    declare_address = DeclareLaunchArgument(
        'rosbridge_address', default_value=rosbridge_address,
        description='rosbridge bind address, empty means all interfaces')

    # rosbridge_websocket_launch.xml 内部会自动启动 rosapi 节点，这里不再重复启动
    rosbridge_launch = IncludeLaunchDescription(
        AnyLaunchDescriptionSource(
            os.path.join(
                get_package_share_directory('rosbridge_server'),
                'launch/rosbridge_websocket_launch.xml')),
        launch_arguments={
            'port': rosbridge_port,
            'address': rosbridge_address,
        }.items(),
    )

    return LaunchDescription([
        declare_port,
        declare_address,
        rosbridge_launch,
    ])
