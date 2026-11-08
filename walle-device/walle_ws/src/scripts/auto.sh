#!/bin/bash
# 安装 Walle 的配置文件：sudo ./auto.sh
set -e

cd "$(dirname "$0")"

# 属主 root，属组 gs；文件 640，其他用户不可读
sudo install -m 640 -o root -g gs walle-ai.env /etc/walle-ai.env
echo "finish"
