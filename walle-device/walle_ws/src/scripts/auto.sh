#!/bin/bash
# 安装 Walle 大模型密钥文件：sudo ./auto.sh

cd "$(dirname "$0")"

sudo mkdir -p /etc/walle
sudo cp walle-ai.env /etc/walle/walle-ai.env
sudo chown root:gs /etc/walle                 # 目录属组必须是 gs，否则 gs 无法进入
sudo chown root:gs /etc/walle/walle-ai.env    # 属主 root，属组 gs
sudo chmod 750 /etc/walle                     # 目录权限 rwxr-x---
sudo chmod 640 /etc/walle/walle-ai.env        # 文件权限 rw-r-----
echo "finish "
