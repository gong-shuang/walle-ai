#!/bin/bash
# 树莓派主控板 udev 规则安装脚本
# 仅安装树莓派串口映射规则（摄像头直接使用 /dev/videoN，由用户按实际设备配置）

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "===== 安装树莓派主控板 udev 规则 ====="
echo ""

# 先清理旧的同名规则（避免重复）
sudo rm -f /etc/udev/rules.d/99-walle-rpi.rules
sudo rm -f /etc/udev/rules.d/99-walle-orangepi.rules
# 清理旧版摄像头符号链接规则（已废弃，改为直接使用 /dev/videoN）
sudo rm -f /etc/udev/rules.d/99-walle-camera.rules

# 拷贝树莓派串口规则
sudo cp "$SCRIPT_DIR/99-walle-rpi.rules" /etc/udev/rules.d/

echo "已安装规则文件:"
ls -la /etc/udev/rules.d/99-walle-rpi.rules

echo ""
echo "重载并重启 udev 服务"
sudo service udev reload
sudo service udev restart

echo ""
echo "===== 完成 ====="
