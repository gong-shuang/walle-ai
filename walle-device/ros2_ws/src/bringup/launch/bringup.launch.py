import importlib.util
import os
import subprocess

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (DeclareLaunchArgument, IncludeLaunchDescription,
                            LogInfo, OpaqueFunction, TimerAction)
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def _gst_mpp_available():
    """RK3588 等平台：GStreamer 具备 mppjpegdec 时由视频桥独占摄像头。"""
    try:
        return subprocess.run(
            ['gst-inspect-1.0', 'mppjpegdec'],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=3).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _debug_default():
    """调试类节点的默认开关：WALLE_RUNTIME_ENV=debug 为 true，prod/未设置为 false。"""
    runtime_env = os.environ.get('WALLE_RUNTIME_ENV', 'prod').strip().lower()
    return 'true' if runtime_env == 'debug' else 'false'


def _version_banner():
    """版本横幅：读取 env_walle.sh 从 VERSION 导出的变量。"""
    return 'Walle 版本 %s（build %s，%s）' % (
        os.environ.get('WALLE_VERSION_NAME', 'unknown'),
        os.environ.get('WALLE_VERSION_NUM', 'unknown'),
        os.environ.get('WALLE_VERSION_DATE', 'unknown'))


def _camera_conf_module():
    """按文件加载 camera_conf.py（symlink-install 下 bringup 包不可直接 import）。"""
    path = os.path.join(get_package_share_directory('bringup'),
                        'camera_conf.py')
    spec = importlib.util.spec_from_file_location('walle_camera_conf', path)
    if spec is None or spec.loader is None:
        raise RuntimeError('无法加载 %s' % path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    # rosbridge / web_video_server 仅用于开发调试（web 测试页 / 快速定位 / 8080 预览）
    # 默认值由 env_walle.sh 的 WALLE_RUNTIME_ENV 控制：debug=true，prod=false
    enable_rosbridge = LaunchConfiguration(
        'enable_rosbridge', default=_debug_default())
    enable_web_video_server = LaunchConfiguration(
        'enable_web_video_server', default=_debug_default())

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

    # 启动 web_video_server 进程（仅调试：浏览器查看 /stream、/image_raw）
    web_video_server_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(walle_video_package_path, 'launch/web_video_server.launch.py')),
        condition=IfCondition(enable_web_video_server),
    )

    # 启动 MQTT -> ROS2 桥（唯一 MQTT 客户端）
    net_mqtt_bridge_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(net_mqtt_bridge_package_path,
                         'launch/net_mqtt_bridge.launch.py')),
    )

    # 摄像头参数唯一来源：scripts/auto_gen_camera_conf.sh 生成的 JSON。
    # camera_device:= 仅用于临时覆盖，正常部署不再手改 YAML。
    camera_conf_arg = LaunchConfiguration('camera_conf').perform(context).strip()
    camera_device_arg = LaunchConfiguration('camera_device').perform(
        context).strip()
    try:
        camera_conf_api = _camera_conf_module()
    except Exception as exc:  # noqa: BLE001
        camera_conf_api = None
        camera_conf, camera_conf_path = None, None
        camera_conf_error = '加载 camera_conf.py 失败：%s' % exc
    if camera_conf_api is not None:
        camera_conf, camera_conf_path, camera_conf_error = \
            camera_conf_api.load_camera_conf(camera_conf_arg or None)
    if camera_conf is not None and camera_device_arg:
        if camera_device_arg != camera_conf['device']:
            camera_conf = dict(camera_conf, device=camera_device_arg)
    device = (camera_conf['device'] if camera_conf is not None
              else camera_device_arg or '/dev/video0')
    if camera_conf is not None:
        note = ''
        if not os.path.exists(device):
            note = '；警告：设备节点 %s 当前不存在（插好摄像头后重启）' % device
        config_log = LogInfo(
            msg='[bringup] 摄像头配置：%s（%s）%s'
                % (camera_conf_path,
                   camera_conf_api.describe_camera_conf(camera_conf), note))
    else:
        config_log = LogInfo(
            msg='[bringup] 摄像头配置不可用：%s' % camera_conf_error)

    # 摄像头后端：auto 时 RK3588（有 mppjpegdec）默认使用 walle_camera +
    # 共享内存 H.264；其他平台仍用 usb_cam；也可显式指定 gst / shm / usb_cam。
    backend = LaunchConfiguration('camera_backend').perform(context).strip().lower()
    if backend not in ('auto', 'gst', 'shm', 'usb_cam'):
        backend = 'auto'
    if backend == 'auto':
        backend = 'shm' if _gst_mpp_available() else 'usb_cam'
    if camera_conf is None:
        backend = 'none'
    camera_owner = 'true' if backend == 'gst' else 'false'
    h264_tcp = '/tmp/walle-video.sock' if backend == 'shm' else ''
    camera_format = camera_conf['format'] if camera_conf else 'MJPG'
    camera_width = str(camera_conf['width']) if camera_conf else '640'
    camera_height = str(camera_conf['height']) if camera_conf else '480'
    camera_framerate = str(camera_conf['framerate']) if camera_conf else '30.0'

    usb_cam_launch = None
    if backend == 'usb_cam':
        usb_cam_launch = IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                os.path.join(walle_video_package_path, 'launch/usb_cam.launch.py')),
            launch_arguments={
                'camera_device': device,
                'camera_format': camera_format,
                'camera_width': camera_width,
                'camera_height': camera_height,
                'camera_framerate': camera_framerate,
            }.items(),
        )

    camera_shm_launch = None
    if backend == 'shm':
        camera_shm_launch = Node(
            package='video_webrtc_bridge',
            executable='walle_camera',
            name='walle_camera',
            output='screen',
            parameters=[{
                'camera_device': device,
                'camera_format': camera_format,
                'camera_width': camera_conf['width'],
                'camera_height': camera_conf['height'],
                'framerate': camera_conf['framerate'],
                'ros_image_fps': 10.0,
                'video_bitrate': '1000k',
                'video_gop': 30,
                'shm_socket': h264_tcp,
                'image_topic': '/image_raw',
            }],
            respawn=True,
            respawn_delay=5.0,
        )

    # 启动 /image_raw -> RTMP/WebRTC 推流桥
    video_webrtc_bridge_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(video_webrtc_bridge_package_path,
                         'launch/video_webrtc_bridge.launch.py')),
        launch_arguments={
            'camera_owner': camera_owner,
            'camera_device': device,
            'shm_socket': h264_tcp,
            'camera_format': camera_format,
            'camera_width': camera_width,
            'camera_height': camera_height,
            'framerate': camera_framerate,
            'ros_image_fps': '10.0',
        }.items(),
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

    camera_actions = [
        config_log,
        LogInfo(msg='[bringup] 阶段 2/4：启动摄像头（%s）与声呐' % backend),
        sonar_controller_launch,
    ]
    if backend == 'none':
        camera_actions.append(LogInfo(
            msg='[bringup] 摄像头未启动：请运行 '
                'walle-device/scripts/auto_gen_camera_conf.sh 生成配置后重启'))
    if usb_cam_launch is not None:
        camera_actions.append(usb_cam_launch)
    if camera_shm_launch is not None:
        camera_actions.append(camera_shm_launch)
    start_camera = TimerAction(period=3.0, actions=camera_actions)
    start_services = TimerAction(
        period=6.0,
        actions=[
            LogInfo(msg='[bringup] 阶段 3/4：启动视频桥、语音与大模型'),
            video_webrtc_bridge_launch,
            speech_recognition_launch,
            large_models_launch,
        ],
    )
    start_net = TimerAction(
        period=9.0,
        actions=[
            LogInfo(msg='[bringup] 阶段 4/4：启动 MQTT 桥与启动自检'),
            net_mqtt_bridge_launch,
            startup_check_node,
        ],
    )

    # 启动时统一打印版本（来自 env_walle.sh 导出的 WALLE_VERSION_*）
    version_log = LogInfo(msg=_version_banner())

    return [
        version_log,
        LogInfo(msg='[bringup] 阶段 1/4：启动底盘与调试服务'),
        net_launch,
        web_video_server_launch,
        controller_launch,
        start_camera,
        start_services,
        start_net,
    ]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument(
            'sonar', default_value='true',
            description='Start sonar controller node'),
        DeclareLaunchArgument(
            'enable_rosbridge', default_value=_debug_default(),
            description='Start rosbridge websocket server (default: WALLE_RUNTIME_ENV=debug)'),
        DeclareLaunchArgument(
            'enable_web_video_server', default_value=_debug_default(),
            description='Start web_video_server on 8080 (default: WALLE_RUNTIME_ENV=debug)'),
        DeclareLaunchArgument(
            'camera_conf', default_value='',
            description='Path to walle_camera.conf (default: /etc/walle-ai or '
                        'scripts/etc, or WALLE_CAMERA_CONF)'),
        DeclareLaunchArgument(
            'camera_device', default_value='',
            description='Temporary override for camera device in walle_camera.conf'),
        DeclareLaunchArgument(
            'camera_backend', default_value='auto',
            description='Camera backend: auto | gst | shm | usb_cam'),
        OpaqueFunction(function=launch_setup)
    ])
