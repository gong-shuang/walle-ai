#!/bin/bash
# 安装 Walle 的配置文件：sudo ./auto.sh
set -e

cd "$(dirname "$0")"

env_file="walle-ai.env"
if [[ ! -f "$env_file" ]]; then
    echo "未找到 $env_file，请先复制模板并填写：cp walle-ai.env.example walle-ai.env" >&2
    exit 1
fi

# 属主 root，属组 gs；文件 640，其他用户不可读
sudo install -m 640 -o root -g gs "$env_file" /etc/walle-ai.env
echo "finish"
