from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, LogInfo
from launch_ros.actions import Node
from launch.substitutions import LaunchConfiguration

def generate_launch_description():
    # imu_frame = LaunchConfiguration('imu_frame', default='imu_link')
    # imu_frame_arg = DeclareLaunchArgument('imu_frame', default_value=imu_frame)

    # ros_robot_controller node
    ros_robot_controller_node = Node(
        package='ros_robot_controller',
        executable='ros_robot_controller',
        output='screen',
        # parameters=[{'imu_frame': imu_frame}]
        parameters=[],
        respawn=True,
        respawn_delay=2.0,
    )

    # mecanum_chassis node
    mecanum_chassis_node = Node(
        package='controller',
        executable='mecanum',
        name='mecanum_chassis_node',
        output='screen',
        parameters=[{
            # 加速度斜坡：0 = 关闭（收到 cmd_vel 立即达到目标速度）
            'max_linear_accel': 0.0,
            'max_angular_accel': 0.0,
        }],
        remappings=[],
        respawn=True,
        respawn_delay=5.0,
    )

    return LaunchDescription([
        # imu_frame_arg,
        ros_robot_controller_node,
        mecanum_chassis_node
    ])
