#!/bin/bash
# Walle 生产部署：检查配置 -> 安装 /etc/walle-ai.conf -> 安装并启用 systemd 服务
# 用法：./deploy.sh
set -e

cd "$(dirname "$0")"
script_dir="$(pwd)"
# walle-device 根目录（scripts 的上一级）
walle_home="$(cd "$script_dir/.." && pwd)"

conf_file="$script_dir/walle-ai.conf"

get_conf() {
    # 取最后一个 KEY=value 的值，去掉首尾空白与引号
    grep -E "^[[:space:]]*(export[[:space:]]+)?$1=" "$conf_file" \
        | tail -n 1 \
        | cut -d= -f2- \
        | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' \
              -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
}

echo "[1/4] 检查配置文件 $conf_file"
if [[ ! -f "$conf_file" ]]; then
    echo "  未找到 $conf_file，请先复制模板：cp walle-ai.conf.example walle-ai.conf" >&2
    exit 1
fi

missing=0
for key in MQTT_HOST MQTT_USERNAME MQTT_PASSWORD MQTT_TENANT WHIP_SECRET; do
    value="$(get_conf "$key")"
    if [[ -z "$value" || "$value" == "xxx" || "$value" == "xxxx" || "$value" == "xxxxx" ]]; then
        echo "  $key 未配置或仍是模板占位符" >&2
        missing=1
    fi
done
if [[ "$missing" -ne 0 ]]; then
    echo "  请补全 $conf_file 后重试" >&2
    exit 1
fi
echo "  必填项检查通过：MQTT_HOST / MQTT_USERNAME / MQTT_PASSWORD / MQTT_TENANT / WHIP_SECRET"

echo "[2/4] 安装配置到 /etc/walle-ai.conf（root:root 600）"
sudo install -m 600 -o root -g root "$conf_file" /etc/walle-ai.conf

echo "[3/4] 安装 walle-ai.service"
target_user="${SUDO_USER:-$(id -un)}"
target_group="$(id -gn "$target_user")"
target_home="$(getent passwd "$target_user" | cut -d: -f6)"
if [[ -z "$target_home" ]]; then
    echo "  无法获取用户 $target_user 的主目录" >&2
    exit 1
fi

unit_tmp="$(mktemp)"
trap 'rm -f "$unit_tmp"' EXIT
sed -e "s|@WALLE_USER@|$target_user|g" \
    -e "s|@WALLE_GROUP@|$target_group|g" \
    -e "s|@WALLE_USER_HOME@|$target_home|g" \
    -e "s|@WALLE_HOME@|$walle_home|g" \
    walle-ai.service.in > "$unit_tmp"
sudo install -m 644 -o root -g root "$unit_tmp" /etc/systemd/system/walle-ai.service

echo "[4/4] 启用 walle-ai.service"
sudo systemctl daemon-reload
sudo systemctl enable walle-ai.service

echo "部署完成"
echo "  启动：sudo systemctl start walle-ai"
echo "  日志：journalctl -u walle-ai -f"
