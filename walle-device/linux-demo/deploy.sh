#!/bin/bash
# linux-demo 部署脚本：检查配置 -> 安装 /etc/walle-ai.conf -> 渲染并启用 systemd 服务
#
# 前置：已按 README「首次部署」安装系统依赖、创建 venv 并安装 requirements.txt。
# 用法：./deploy.sh [--venv PATH] [--no-start]
#   --venv PATH  指定 venv 根目录（其下有 bin/python）；也可用环境变量 WALLE_VENV
#   --no-start   只安装并 enable，不立即启动/重启服务
# 脚本可重复执行：每次都会覆盖 /etc/walle-ai.conf 与 systemd 单元。
set -euo pipefail

cd "$(dirname "$0")"
walle_home="$(pwd)"
conf_file="$walle_home/walle-ai.conf"

usage() {
    sed -n '2,8p' "$walle_home/deploy.sh" | sed 's/^# \{0,1\}//'
}

if [[ "$(id -u)" -eq 0 ]]; then
    echo "请用部署用户执行（脚本内部会调用 sudo）：./deploy.sh" >&2
    exit 1
fi

# 部署用户：sudo 场景取 SUDO_USER，普通执行取当前用户
target_user="${SUDO_USER:-$(id -un)}"
target_group="$(id -gn "$target_user")"
target_home="$(getent passwd "$target_user" | cut -d: -f6)"
if [[ -z "$target_home" ]]; then
    echo "无法获取用户 $target_user 的主目录" >&2
    exit 1
fi

venv="${WALLE_VENV:-$target_home/venv}"
no_start=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --venv)     venv="$2"; shift 2 ;;
        --venv=*)   venv="${1#*=}"; shift ;;
        --no-start) no_start=1; shift ;;
        -h|--help)  usage; exit 0 ;;
        *)          echo "未知参数：$1（--help 查看用法）" >&2; exit 1 ;;
    esac
done

echo "[1/5] 检查 Python 解释器"
if [[ ! -x "$venv/bin/python" ]]; then
    echo "  未找到 $venv/bin/python" >&2
    echo "  请先按 README 创建 venv 并安装依赖，或用 --venv / WALLE_VENV 指定实际路径" >&2
    exit 1
fi
echo "  $venv/bin/python"

echo "[2/5] 检查配置文件"
if [[ ! -f "$conf_file" ]]; then
    echo "  未找到 $conf_file，请先复制模板：cp walle-ai.conf.example walle-ai.conf" >&2
    exit 1
fi
echo "  $conf_file"

echo "[3/5] 配置自检（main.py --check，不连接 Broker）"
if ! "$venv/bin/python" "$walle_home/main.py" --env-file "$conf_file" --check; then
    echo "  配置检查失败，请修改 $conf_file 后重试" >&2
    exit 1
fi

echo "[4/5] 安装 /etc/walle-ai.conf（root:root 600）"
sudo install -m 600 -o root -g root "$conf_file" /etc/walle-ai.conf

echo "[5/5] 渲染并启用 walle-ai.service"
unit_tmp="$(mktemp)"
trap 'rm -f "$unit_tmp"' EXIT
sed -e "s|@WALLE_USER@|$target_user|g" \
    -e "s|@WALLE_GROUP@|$target_group|g" \
    -e "s|@WALLE_HOME@|$walle_home|g" \
    -e "s|@WALLE_VENV@|$venv|g" \
    "$walle_home/walle-ai.service.example" > "$unit_tmp"
sudo install -m 644 -o root -g root "$unit_tmp" /etc/systemd/system/walle-ai.service
sudo systemctl daemon-reload
sudo systemctl enable walle-ai.service
if [[ "$no_start" -eq 1 ]]; then
    echo "  已安装并 enable（--no-start，未启动）"
else
    sudo systemctl restart walle-ai.service
fi

echo "部署完成"
echo "  状态：systemctl status walle-ai"
echo "  日志：journalctl -u walle-ai -f"
