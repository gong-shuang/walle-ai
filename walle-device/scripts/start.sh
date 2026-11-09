#!/bin/bash
# 开发环境一键启动（生产请用 sudo systemctl start walle-ai）
# 用法：./start.sh [--conf-file <path>]，默认读取脚本同级的 walle-ai.conf
set -e

script_dir="$(cd "$(dirname "$0")" && pwd)"

if [[ "$(id -u)" -eq 0 ]]; then
    echo "start.sh 仅用于开发环境；生产请用 sudo systemctl start walle-ai" >&2
    exit 1
fi

conf_file="$script_dir/walle-ai.conf"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --conf-file)
            if [[ -z "${2:-}" ]]; then
                echo "--conf-file 需要一个路径" >&2
                exit 1
            fi
            conf_file="$2"
            shift 2
            ;;
        --conf-file=*)
            conf_file="${1#*=}"
            shift
            ;;
        -h|--help)
            echo "用法: $0 [--conf-file <path>]"
            echo "  默认: $script_dir/walle-ai.conf"
            exit 0
            ;;
        *)
            echo "未知参数: $1" >&2
            exit 1
            ;;
    esac
done

if [[ ! -r "$conf_file" ]]; then
    echo "未找到或不可读配置文件 $conf_file" >&2
    echo "请先复制模板：cp $script_dir/walle-ai.conf.example $conf_file" >&2
    exit 1
fi

# 先载入 venv / ROS / 工作空间环境，再载入配置（配置可覆盖运行环境变量）
source "$script_dir/env_walle.sh"

set -a
source "$conf_file"
set +a

exec ros2 launch bringup bringup.launch.py
