from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


# rosbridge 话题白名单：只放行调试页（web/car_ctl_by_bridge.html）实际使用的安全话题。
# 底盘统一走 /cmd_vel，经 mecanum 节点限幅、加速度平滑和看门狗，不允许绕过它直控电机。
# 低层接口 /ros_robot_controller/internal/set_motor_speeds（电机占空比）与
# /ros_robot_controller/internal/enable_reception（传感器上报开关）不在白名单内。
# 注意：rosbridge 的 glob 是白名单语义；新增条目必须是精确话题名，
# 禁止使用 /ros_robot_controller/set_* 这类会连带匹配 set_motor_speeds 的通配。
ROSBridge_ALLOWED_TOPICS = (
    '/cmd_vel',
    '/ros_robot_controller/battery',
    '/ros_robot_controller/set_buzzer',
    '/ros_robot_controller/set_led',
    '/ros_robot_controller/set_oled',
    '/ros_robot_controller/set_rgb',
    '/ros_robot_controller/pwm_servo/set_state',
    '/sonar_controller/get_distance',
    '/sonar_controller/set_rgb',
)


def _topics_glob(topics):
    """rosbridge 的 topics_glob 参数格式：['/a', '/b']。"""
    return '[' + ', '.join("'%s'" % topic for topic in topics) + ']'


def generate_launch_description():
    # 参数名加 rosbridge_ 前缀：include 的 launch 参数是全局的，
    # 避免通用的 port/address 泄漏给其它被 include 的 launch 文件。
    rosbridge_port = LaunchConfiguration('rosbridge_port', default='9090')
    rosbridge_address = LaunchConfiguration('rosbridge_address', default='')
    rosbridge_topics_glob = LaunchConfiguration(
        'rosbridge_topics_glob', default=_topics_glob(ROSBridge_ALLOWED_TOPICS))

    declare_port = DeclareLaunchArgument(
        'rosbridge_port', default_value=rosbridge_port,
        description='rosbridge websocket port')
    declare_address = DeclareLaunchArgument(
        'rosbridge_address', default_value=rosbridge_address,
        description='rosbridge bind address, empty means all interfaces')
    declare_topics_glob = DeclareLaunchArgument(
        'rosbridge_topics_glob', default_value=rosbridge_topics_glob,
        description='Topic allowlist for rosbridge publish/subscribe, '
                    "format \"['/topic_a', '/topic_b']\"; empty string means no restriction")

    # 不走 rosbridge_websocket_launch.xml：XML 的 <param value=...> 会把 glob 字符串
    # 按 YAML 解析成字符串数组，而 rosbridge 要求该参数是字符串。这里用 ParameterValue
    # 显式指定类型，避免启动时 InvalidParameterTypeException。
    rosbridge_node = Node(
        package='rosbridge_server',
        executable='rosbridge_websocket',
        name='rosbridge_websocket',
        output='screen',
        parameters=[{
            'port': ParameterValue(rosbridge_port, value_type=int),
            'address': ParameterValue(rosbridge_address, value_type=str),
            # 同时作用于 rosbridge 的 publish/advertise 与 subscribe
            'topics_glob': ParameterValue(rosbridge_topics_glob, value_type=str),
        }],
    )

    # rosapi 用于话题/服务查询，需与 rosbridge 使用同一份白名单
    rosapi_node = Node(
        package='rosapi',
        executable='rosapi_node',
        name='rosapi',
        output='screen',
        parameters=[{
            'topics_glob': ParameterValue(rosbridge_topics_glob, value_type=str),
        }],
    )

    return LaunchDescription([
        declare_port,
        declare_address,
        declare_topics_glob,
        rosbridge_node,
        rosapi_node,
    ])
