import importlib.util
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (DeclareLaunchArgument, ExecuteProcess,
                            IncludeLaunchDescription, LogInfo, OpaqueFunction,
                            TimerAction)
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


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


def _platform_default():
    """运行平台：robot（真机）/ pc（PC 模拟），与 env_walle.sh 的 WALLE_PLATFORM 一致。"""
    value = os.environ.get('WALLE_PLATFORM', 'robot').strip().lower()
    return value if value in ('robot', 'pc') else 'robot'


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
    platform = LaunchConfiguration('platform').perform(context).strip().lower()
    if platform not in ('robot', 'pc'):
        platform = 'robot'

    def _switch(name):
        """开关默认 auto：真机开启，PC 关闭；显式 true/false 优先。"""
        raw = LaunchConfiguration(name).perform(context).strip().lower()
        if raw in ('', 'auto'):
            return platform == 'robot'
        return raw in ('1', 'true', 'yes', 'on')

    enable_ai = _switch('enable_ai')
    enable_speech = _switch('enable_speech')

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

    # 真机：启动 controller 的 launch 文件（ros_robot_controller + mecanum_chassis_node）
    # PC：跳过真实底盘节点，由 pc_robot 模拟电机/舵机/电池/声呐
    controller_launch = None
    pc_robot_launch = None
    pc_robot_package_path = None
    if platform == 'robot':
        controller_launch = IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                os.path.join(controller_package_path, 'launch/controller.launch.py')),
        )
    else:
        pc_robot_package_path = get_package_share_directory('pc_robot')
        pc_robot_launch = IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                os.path.join(pc_robot_package_path, 'launch/pc_robot.launch.py')),
        )

    # 启动 rosbridge_websocket / rosapi（walle_net 仅做启动编排）
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
    # PC 模式使用 pc_robot 包里的 support_* 覆盖文件，bridge 源码保持不变
    bridge_launch_args = {}
    if platform == 'pc':
        bridge_launch_args['params_file'] = os.path.join(
            pc_robot_package_path, 'config', 'net_mqtt_bridge_pc.yaml')
    net_mqtt_bridge_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(net_mqtt_bridge_package_path,
                         'launch/net_mqtt_bridge.launch.py')),
        launch_arguments=bridge_launch_args.items(),
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
        cam = camera_conf.get('camera', {})
        if camera_device_arg != cam.get('device'):
            camera_conf['camera'] = dict(cam, device=camera_device_arg)
    cam = camera_conf.get('camera', {}) if camera_conf else {}
    device = (cam.get('device') if camera_conf is not None
              else camera_device_arg or '/dev/video0')
    camera_available = camera_conf is not None and os.path.exists(device)
    # 真机保持原行为；PC 只有检测到摄像头才启动 camera / video / record
    start_video_nodes = (platform == 'robot') or camera_available
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

    # 摄像头固定由 usb_cam 采集并发布 /image_raw。
    usb_cam_launch = None
    if camera_conf is not None:
        usb_cam_launch = IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                os.path.join(walle_video_package_path, 'launch/usb_cam.launch.py')),
            launch_arguments={
                'camera_device': device,
                'camera_format': cam.get('format', 'MJPG'),
                'camera_width': str(cam.get('width', 1280)),
                'camera_height': str(cam.get('height', 720)),
                'camera_framerate': str(cam.get('framerate', 30)),
            }.items(),
        )

    # 启动 /image_raw -> RTMP/WebRTC 推流桥
    video_webrtc_bridge_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(video_webrtc_bridge_package_path,
                         'launch/video_webrtc_bridge.launch.py')),
        launch_arguments={
            'camera_device': device,
            'framerate': str(cam.get('framerate', 30.0)) if camera_conf else '30.0',
        }.items(),
    )

    # 启动本地录像节点（订阅 /image_raw -> fMP4 落盘）
    record_video_package_path = get_package_share_directory('record_video')
    rec = camera_conf.get('record', {}) if camera_conf else {}
    record_video_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(record_video_package_path,
                         'launch/record_video.launch.py')),
        launch_arguments={
            'camera_device': device,
            'width': str(cam.get('width', 1280)),
            'height': str(cam.get('height', 720)),
            'framerate': str(cam.get('framerate', 30.0)),
            'storage_path': rec.get('storage_path', ''),
            'storage_limit_gb': str(rec.get('storage_limit_gb', 16.0)),
            'segment_duration_sec': str(rec.get('segment_duration_sec', 3600)),
            'auto_record': str(rec.get('auto_record', True)).lower(),
            'audio_enabled': str(rec.get('audio_enabled', True)).lower(),
            'audio_backend': str(rec.get('audio_backend', 'pulse')),
            'audio_input': str(rec.get('audio_input', 'default')),
            'audio_sample_rate': str(rec.get('audio_sample_rate', 44100)),
            'audio_channels': str(rec.get('audio_channels', 1)),
            'audio_bitrate': str(rec.get('audio_bitrate', '64k')),
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
        parameters=[{
            'platform': platform,
            'camera_enabled': bool(start_video_nodes and usb_cam_launch is not None),
        }],
    )

    stage2_actions = [
        config_log,
        LogInfo(msg='[bringup] 阶段 2/4：%s'
                % ('启动 usb_cam 与声呐' if platform == 'robot'
                   else '检查摄像头')),
    ]
    if usb_cam_launch is not None:
        stage2_actions.append(usb_cam_launch)
    elif platform == 'robot':
        stage2_actions.append(LogInfo(
            msg='[bringup] 摄像头未启动：请运行 '
                'walle-device/scripts/auto_gen_camera_conf.sh 生成配置后重启'))
    if platform == 'robot':
        stage2_actions.append(sonar_controller_launch)
    if platform == 'pc' and not camera_available:
        stage2_actions.append(LogInfo(
            msg='[bringup] PC 模式未检测到摄像头：跳过 usb_cam / 视频桥 / 录像'))
    start_camera = TimerAction(period=3.0, actions=stage2_actions)

    stage3_actions = [
        LogInfo(msg='[bringup] 阶段 3/4：启动视频桥、录像、语音与大模型'),
    ]
    if start_video_nodes:
        stage3_actions.extend([video_webrtc_bridge_launch, record_video_launch])
    else:
        stage3_actions.append(LogInfo(
            msg='[bringup] PC 模式：未启用摄像头，跳过 video_webrtc_bridge 与 record_video'))
    if enable_speech:
        stage3_actions.append(speech_recognition_launch)
    else:
        stage3_actions.append(LogInfo(
            msg='[bringup] 已跳过语音盒子节点（enable_speech:=true 可开启）'))
    if enable_ai:
        stage3_actions.append(large_models_launch)
    else:
        stage3_actions.append(LogInfo(
            msg='[bringup] 已跳过 large_models（enable_ai:=true 可开启）'))
    start_services = TimerAction(period=6.0, actions=stage3_actions)

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

    # 启动时把 PulseAudio 默认输出音量固定为 70%。
    # 注意：speech.set_volume() 操作的是 ALSA 硬件 mixer，而本机 aplay 的
    # default 走 PulseAudio，实际音量由 sink 决定，所以必须用 pactl 设置。
    # Pulse 未就绪时重试 3 次，失败也不阻塞 bringup。
    set_output_volume = ExecuteProcess(
        cmd=['/bin/bash', '-c',
             'for i in 1 2 3; do '
             'pactl set-sink-volume @DEFAULT_SINK@ 70% && exit 0; '
             'sleep 1; done; exit 0'],
        name='set_output_volume',
        output='screen',
    )

    stage1_actions = [
        version_log,
        set_output_volume,
        LogInfo(msg='[bringup] 平台：%s' % platform),
        LogInfo(msg='[bringup] 阶段 1/4：%s'
                % ('启动底盘与调试服务' if platform == 'robot'
                   else '启动 PC 模拟节点与调试服务')),
        net_launch,
        web_video_server_launch,
    ]
    if platform == 'pc':
        stage1_actions.append(pc_robot_launch)
    else:
        stage1_actions.append(controller_launch)
    return stage1_actions + [start_camera, start_services, start_net]


def generate_launch_description():
    platform_default = _platform_default()
    return LaunchDescription([
        DeclareLaunchArgument(
            'platform',
            default_value=platform_default,
            description='robot = real device, pc = simulated device'),
        DeclareLaunchArgument(
            'enable_ai',
            default_value='auto',
            description='Start large_models nodes: auto (robot=true/pc=false), true, false'),
        DeclareLaunchArgument(
            'enable_speech',
            default_value='auto',
            description='Start speech (I2C voice box) node: auto (robot=true/pc=false), true, false'),
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
                        'etc, or WALLE_CAMERA_CONF)'),
        DeclareLaunchArgument(
            'camera_device', default_value='',
            description='Temporary override for camera device in walle_camera.conf'),
        OpaqueFunction(function=launch_setup)
    ])
