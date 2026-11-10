import os
from ament_index_python.packages import get_package_share_directory
from launch.substitutions import LaunchConfiguration
from launch.actions import DeclareLaunchArgument
from launch import LaunchDescription, LaunchService
from launch.actions import IncludeLaunchDescription, OpaqueFunction
from launch.launch_description_sources import PythonLaunchDescriptionSource

def launch_setup(context):
    mode = LaunchConfiguration('mode', default=1)
    mode_arg = DeclareLaunchArgument('mode', default_value=mode)
    awake_method = LaunchConfiguration('awake_method', default='alsa')
    awake_method_arg = DeclareLaunchArgument('awake_method', default_value=awake_method)
    mic_device = LaunchConfiguration('mic_device', default='1')
    mic_device_arg = DeclareLaunchArgument('mic_device', default_value=mic_device)
    asr_channel = LaunchConfiguration('asr_channel', default='2')
    asr_channel_arg = DeclareLaunchArgument('asr_channel', default_value=asr_channel)

    vocal_detect_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(get_package_share_directory('large_models'), 'launch/vocal_detect.launch.py')),
        launch_arguments={
            'mode': mode,
            'awake_method': awake_method,
            'mic_device': mic_device,
            'asr_channel': asr_channel,
        }.items(),
    )

    agent_process_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(get_package_share_directory('large_models'), 'launch/agent_process.launch.py')),
    )

    tts_node_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(get_package_share_directory('large_models'), 'launch/tts_node.launch.py')),
    )

    return [
        mode_arg,
        awake_method_arg,
        mic_device_arg,
        asr_channel_arg,
        vocal_detect_launch,
        agent_process_launch,
        tts_node_launch,
    ]

def generate_launch_description():
    return LaunchDescription([
        OpaqueFunction(function = launch_setup)
    ])

if __name__ == '__main__':
    # 创建一个LaunchDescription对象(create a LaunchDescription object)
    ld = generate_launch_description()

    ls = LaunchService()
    ls.include_launch_description(ld)
    ls.run()

