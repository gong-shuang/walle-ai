#!/bin/bash
# 香橙派5 主控板 udev 规则安装脚本
# 仅安装香橙派串口映射 + 摄像头规则

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "===== 安装香橙派5主控板 udev 规则 ====="
echo ""

# 先清理旧的同名规则（避免重复）
sudo rm -f /etc/udev/rules.d/99-walle-rpi.rules
sudo rm -f /etc/udev/rules.d/99-walle-orangepi.rules
sudo rm -f /etc/udev/rules.d/99-walle-camera.rules

# 拷贝香橙派串口规则 + 摄像头规则
sudo cp "$SCRIPT_DIR/99-walle-orangepi.rules" /etc/udev/rules.d/
sudo cp "$SCRIPT_DIR/99-walle-camera.rules" /etc/udev/rules.d/

echo "已安装规则文件:"
ls -la /etc/udev/rules.d/99-walle-orangepi.rules
ls -la /etc/udev/rules.d/99-walle-camera.rules

echo ""
echo "重载并重启 udev 服务"
sudo service udev reload
sudo service udev restart

echo ""
echo "===== 完成 ====="
