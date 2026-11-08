#!/bin/bash
# Walle 环境配置（只设置环境变量，不启动节点）
# 用法：source ~/walle-device/env_walle.sh
# 注意：不要与 env_TurboPi.sh 在同一个终端里混用，两个工作空间存在同名包。

# 必须用 source 执行，否则 export 的变量不会留在当前终端
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    echo "请使用 source 运行: source ${BASH_SOURCE[0]}" >&2
    exit 1
fi

# 以脚本所在目录为基准，不依赖当前工作目录
current_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export TURBOPI_HOME="$current_dir"

# 已加载且 venv 仍是当前路径才跳过，避免重复 source 导致 PATH / PYTHONPATH 越叠越长
if [[ -n "${WALLE_ENV_LOADED:-}" && "${VIRTUAL_ENV:-}" == "$HOME/venv" ]]; then
    return 0
fi

# 已加载其它环境则终止，避免两个工作空间的同名包互相覆盖
if [[ -n "${TURBOPI_ENV_LOADED:-}" ]]; then
    echo -e "\033[31m[错误] 当前终端已加载 TurboPi 环境(ros2_ws)，不能混用 Walle 环境(walle_ws)。\033[0m" >&2
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

export VERSION="|V0.0.1|2026-11-7|"

# 注释下面，就是生产环境
export WALLE_RUNTIME_ENV=debug

# 机器类型：Walle
export MACHINE_TYPE=Walle

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

source "$HOME/venv/bin/activate"
source /opt/ros/jazzy/setup.bash
source "$current_dir/walle_ws/install/setup.bash"
# source "$current_dir/third_party_ros2/third_party_ws/install/setup.bash"

# ROS 节点入口脚本 shebang 固定为 /usr/bin/python3，仅 activate venv 节点看不到其中的包，
# 需把 venv 的 site-packages 显式加入 PYTHONPATH
export PYTHONPATH="$PYTHONPATH:$HOME/venv/lib/python3.12/site-packages"

# colcon_cd：在任意目录下用 colcon_cd <包名> 直达对应源码目录
if [ -f /usr/share/colcon_cd/function/colcon_cd.sh ]; then
    export _colcon_cd_root="$current_dir/walle_ws"
    source /usr/share/colcon_cd/function/colcon_cd.sh
fi


# OS 版本从 /etc/os-release 读取，随系统变化（放在子 shell 中加载，避免污染当前环境变量）
os_pretty="$(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME")"
# ROS_DISTRO 由上面的 /opt/ros/*/setup.bash 设置，随实际安装的 ROS 发行版变化
ros_distro="${ROS_DISTRO:-unknown}"


echo -e "\033[32m----------------------------------------------
当前环境是ROS2|The current environment is ROS2 (Walle)
----------------------------------------------\033[0m"
echo -e "MACHINE: \033[32m$MACHINE_TYPE\033[0m"
echo -e "RUNTIME: \033[32m$WALLE_RUNTIME_ENV\033[0m"
echo -e "OS:      \033[32m${os_pretty:-Unknown}\033[0m"
echo -e "ROS2:    \033[32m${ros_distro^}\033[0m"
echo -e "ROS_DOMAIN_ID: \033[32m$ROS_DOMAIN_ID\033[0m"
echo -e "ASR_LANGUAGE: \033[32m$ASR_LANGUAGE\033[0m"
echo -e "WORKSPACE: \033[32m$current_dir/walle_ws\033[0m"
echo -e "VERSION: \033[32m$VERSION\033[0m"
