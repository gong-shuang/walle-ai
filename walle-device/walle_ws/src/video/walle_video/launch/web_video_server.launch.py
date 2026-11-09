from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    # 端口使用 web_video_server 默认值 8080。
    # 注意：include 的 launch 参数是全局的，不要在这里声明 port，
    # 否则会和 walle_net 里 rosbridge 的 port(9090) 冲突。
    web_video_server_node = Node(
        package='web_video_server',
        executable='web_video_server',
        output='screen',
    )

    return LaunchDescription([web_video_server_node])
