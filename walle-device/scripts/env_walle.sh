#!/bin/bash
# Walle 环境配置（只设置环境变量，不启动节点）
# 用法：source ~/walle-device/scripts/env_walle.sh
# 注意：不要与 env_TurboPi.sh 在同一个终端里混用，两个工作空间存在同名包。

# 必须用 source 执行，否则 export 的变量不会留在当前终端
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    echo "请使用 source 运行: source ${BASH_SOURCE[0]}" >&2
    exit 1
fi

# 以脚本所在目录为基准，不依赖当前工作目录
current_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # walle-device/scripts
walle_home="$(cd "$current_dir/.." && pwd)"                  # walle-device
export TURBOPI_HOME="$walle_home"

# venv 目录：默认 $HOME/venv；实际路径不同时用 WALLE_VENV 指定
venv_path="${WALLE_VENV:-$HOME/venv}"

# 已加载且 venv 仍是当前路径才跳过，避免重复 source 导致 PATH / PYTHONPATH 越叠越长
if [[ -n "${WALLE_ENV_LOADED:-}" && "${VIRTUAL_ENV:-}" == "$venv_path" ]]; then
    return 0
fi

# 已加载其它环境则终止，避免两个工作空间的同名包互相覆盖
if [[ -n "${TURBOPI_ENV_LOADED:-}" ]]; then
    echo -e "\033[31m[错误] 当前终端已加载 TurboPi 环境，不能混用 Walle 环境(walle-device/ros2_ws)。\033[0m" >&2
    echo -e "\033[31m       请新开一个干净终端后再 source env_walle.sh。\033[0m" >&2
    return 1
fi

# 未带本环境标记但已有 ROS2 环境（如手动 source 过 /opt/ros），同样视为不匹配
if [[ -z "${WALLE_ENV_LOADED:-}" ]] && { [[ -n "${ROS_DISTRO:-}" ]] || [[ -n "${AMENT_PREFIX_PATH:-}" ]]; }; then
    echo -e "\033[31m[错误] 当前终端已存在其它 ROS2 环境，不能混用 Walle 环境。\033[0m" >&2
    echo -e "\033[31m       请新开一个干净终端后再 source env_walle.sh。\033[0m" >&2
    return 1
fi

export WALLE_ENV_LOADED=1

# 版本号唯一来源：设备端根目录 VERSION（linux-demo 与 ROS2 共用）
# （WALLE_VERSION_NUM / WALLE_VERSION_NAME / WALLE_VERSION_DATE）
source "$walle_home/VERSION"
export VERSION="|V${WALLE_VERSION_NAME}|${WALLE_VERSION_DATE}|"

# 注释下面，就是生产环境
export WALLE_RUNTIME_ENV=debug

# 机器类型：Walle
export MACHINE_TYPE=Walle

# 主控板型号：RaspberryPi5 / OrangePi5
# 可通过环境变量 MAIN_CONTROL_BOARD 覆盖；默认 RaspberryPi5
export MAIN_CONTROL_BOARD="${MAIN_CONTROL_BOARD:-RaspberryPi5}"

# 根据主控板型号自动选择 I2C 总线：
#   RaspberryPi5 -> i2c-1 (总线 1)
#   OrangePi5    -> i2c-2 (总线 2)
case "$MAIN_CONTROL_BOARD" in
    OrangePi5)
        export WALLE_I2C_BUS=2
        ;;
    RaspberryPi5|*)
        export WALLE_I2C_BUS=1
        ;;
esac

# 语言设置，主要涉及语音部分：Chinese/English
export ASR_LANGUAGE=Chinese

# 运行环境：prod（生产，默认）/ debug（调试）
# debug 会自动开启 rosbridge(9090) 等调试能力；线上保持 prod
# 临时切换：WALLE_RUNTIME_ENV=debug source env_walle.sh
export WALLE_RUNTIME_ENV="${WALLE_RUNTIME_ENV:-prod}"

# 走 install 目录（package share），不再依赖源码目录硬编码路径
export need_compile=True

# 与 TurboPi 使用不同 Domain，避免两台车在同一局域网内互相串话题/节点
export ROS_DOMAIN_ID=1

# 所有节点都在本机：仅在本机内发现，避免同局域网多台 Walle 互相串话题/节点。
# Jazzy 推荐 ROS_AUTOMATIC_DISCOVERY_RANGE（ROS_LOCALHOST_ONLY 已弃用）。
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST

if [[ ! -f "$venv_path/bin/activate" ]]; then
    echo -e "\033[31m[错误] 未找到 venv：$venv_path（可用 WALLE_VENV 指定实际路径）\033[0m" >&2
    return 1
fi
source "$venv_path/bin/activate"
source /opt/ros/jazzy/setup.bash
source "$walle_home/ros2_ws/install/setup.bash"
# source "$current_dir/third_party_ros2/third_party_ws/install/setup.bash"

# ROS 节点入口脚本 shebang 固定为 /usr/bin/python3，仅 activate venv 节点看不到其中的包，
# 需把 venv 的 site-packages 显式加入 PYTHONPATH（按实际 Python 版本取，兼容小版本变化）
venv_site="$(python3 -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
export PYTHONPATH="$PYTHONPATH:$venv_site"

# colcon_cd：在任意目录下用 colcon_cd <包名> 直达对应源码目录
if [ -f /usr/share/colcon_cd/function/colcon_cd.sh ]; then
    export _colcon_cd_root="$walle_home/ros2_ws"
    source /usr/share/colcon_cd/function/colcon_cd.sh
fi


# OS 版本从 /etc/os-release 读取，随系统变化（放在子 shell 中加载，避免污染当前环境变量）
os_pretty="$(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME")"
# ROS_DISTRO 由上面的 /opt/ros/*/setup.bash 设置，随实际安装的 ROS 发行版变化
ros_distro="${ROS_DISTRO:-unknown}"


echo -e "\033[32m----------------------------------------------
当前环境是ROS2|The current environment is ROS2
----------------------------------------------\033[0m"
echo -e "MACHINE: \033[32m$MACHINE_TYPE\033[0m"
echo -e "BOARD:   \033[32m$MAIN_CONTROL_BOARD\033[0m (I2C bus $WALLE_I2C_BUS)"
echo -e "RUNTIME: \033[32m$WALLE_RUNTIME_ENV\033[0m"
echo -e "OS:      \033[32m${os_pretty:-Unknown}\033[0m"
echo -e "ROS2:    \033[32m${ros_distro^}\033[0m"
echo -e "ROS_DOMAIN_ID: \033[32m$ROS_DOMAIN_ID\033[0m"
echo -e "ROS_DISCOVERY: \033[32m$ROS_AUTOMATIC_DISCOVERY_RANGE\033[0m"
echo -e "ASR_LANGUAGE: \033[32m$ASR_LANGUAGE\033[0m"
echo -e "WORKSPACE: \033[32m$walle_home/ros2_ws\033[0m"
echo -e "VENV:    \033[32m$venv_path\033[0m"
echo -e "VERSION: \033[32m$VERSION\033[0m"
echo -e "\033[32m----------------------------------------------\033[0m"
