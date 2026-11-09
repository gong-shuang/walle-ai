# README

## 项目介绍

### 功能实现

- 底盘控制：麦克纳姆轮运动学解算、速度/加速度限幅、cmd_vel 看门狗、串口异常与进程退出安全停车
- 远程控制：MQTT 接入 App，支持状态查询、视频开关、灯光/蜂鸣器/OLED/舵机等指令
- 音视频：USB 摄像头采集，WebRTC/RTMP 推流，支持双向语音
- 语音与大模型：语音唤醒/识别、LLM 对话、TTS 播报，以及巡线、追踪、避障等演示玩法
- 安全与运维：ROS 本机发现隔离、rosbridge 话题白名单、systemd 常驻、启动自检与版本横幅

### 结构设计

- `walle_ws/src/car_control`：底盘、STM32 控制器、声呐
- `walle_ws/src/video`：摄像头与 WebRTC 推流
- `walle_ws/src/net`：MQTT 桥、rosbridge
- `walle_ws/src/audio`、`walle_ws/src/ai`：语音交互与大模型
- `walle_ws/src/protocol`：自定义 ROS 消息与服务
- `walle_ws/src/bringup`：统一启动、启动自检、版本横幅
- `scripts`：`deploy.sh`、`start.sh`、`env_walle.sh` 与配置/服务模板
- `doc`：部署文档、依赖清单与上线检查清单

### 软硬件要求

硬件：

- 树莓派 5 或同级 ARM64 板卡，系统为 Ubuntu 24.04
- Walle 麦克纳姆轮底盘（STM32 控制器，USB 串口连接）
- USB 摄像头、I2C 声呐、麦克风/扬声器（或语音盒子）

软件：

- ROS 2 Jazzy、Python 3.12、colcon
- ffmpeg（推流）、OpenCV（`python3-opencv`）、`python3-smbus`
- Python 依赖见 [`requirements.txt`](requirements.txt)


## Ubuntu环境配置
本项目在Ubuntu 24.04上运行。
1.烧录ubuntu镜像到SD卡

2.修改系统源为国内源，如阿里云源

备份原始配置文件
```bash
sudo cp /etc/apt/sources.list.d/ubuntu.sources /etc/apt/sources.list.d/ubuntu.sources.bak
```

修改文件：sudo nano /etc/apt/sources.list.d/ubuntu.sources， 替换成下面的内容：
```text
Types: deb
URIs: https://mirrors.aliyun.com/ubuntu-ports/
Suites: noble noble-updates noble-backports
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg

Types: deb
URIs: https://mirrors.aliyun.com/ubuntu-ports/
Suites: noble-security
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
```

执行命令更新系统源：
```bash
sudo apt update
```

3.修改pip源为国内源，如阿里云源
执行命令：
```bash
pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/
pip config set install.trusted-host mirrors.aliyun.com
```

## ROS2环境配置
安装 ros2 Jazzy 版本，和 Ubuntu 版本 24.04 相匹配。
参考ROS2 官方文档：[ROS2 安装](https://docs.ros.org/en/jazzy/Installation/Ubuntu-Install-Debs.html)

### 修改ROS2 源为国内源，如中科大

执行命令：
```bash
# 安装基础依赖
sudo apt install -y curl gnupg lsb-release

# 获取 ROS 2 GPG 密钥
sudo curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key -o /usr/share/keyrings/ros-archive-keyring.gpg

# 添加中科大 ROS 2 软件源
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] https://mirrors.ustc.edu.cn/ros2/ubuntu $(. /etc/os-release && echo $UBUNTU_CODENAME) main" | sudo tee /etc/apt/sources.list.d/ros2.list > /dev/null

# 更新软件包列表
sudo apt update
```

### 安装ros2

安装命令：
```bash
# 安装 ROS 2 Jazzy 版本
sudo apt install -y ros-jazzy-desktop-full
```

配置环境变量：
```bash
echo "source /opt/ros/jazzy/setup.bash" >> ~/.bashrc
source ~/.bashrc
```

验证安装是否成功，终端1执行：
```bash
ros2 run demo_nodes_cpp talker
```

验证安装是否成功，终端2执行：
```bash
ros2 run demo_nodes_py listener
```

### 配置ROS2 的python环境
使用venv环境，这样可以避免与系统python环境冲突。
关键步骤如下：（下面演示的步骤，以 ~/venv 目录为例，根据实际情况修改。）

```bash
# 关键：--system-site-packages 让 venv 继承 ROS 2 的系统包
python3 -m venv ~/venv --system-site-packages

# 阻止 colcon 扫描 venv 目录（venv 不在 walle_ws 内时可省略）
touch ~/venv/COLCON_IGNORE

# 激活 venv 环境
source ~/venv/bin/activate

# 安装系统依赖（OpenCV / I2C / 推流）
sudo apt install -y ffmpeg python3-opencv python3-smbus

# 安装 walle 项目的 Python 依赖
pip install -r ~/walle-device/doc/requirements.txt
```

## 编译与设备准备

### 编译工作空间

```bash
cd ~/walle-device/walle_ws
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install
```

### 安装 udev 规则（底盘串口）

```bash
cd ~/walle-device/walle_ws/src/car_control/ros_robot_controller/scripts
sudo ./create_udev_rules.sh
```

> 当前规则仅放行 `ttyACM0`；P1-3.4 会进一步创建 `/dev/rrc` 并收紧权限。

## 部署 Walle AI

### 前置条件

配置 MQTT 服务器，获得 MQTT_HOST、MQTT_USERNAME、MQTT_PASSWORD、MQTT_TENANT。
配置 WebRTC 服务器，获得 WHIP_SECRET。
配置阿里云 API 密钥，获得 ALIYUN_API_KEY。

### 部署步骤

#### 修改 walle-ai.conf 配置文件

```bash
cd scripts
cp walle-ai.conf.example walle-ai.conf
vi walle-ai.conf  # 打开配置文件，并修改相关参数
```

#### 执行一键部署脚本

```bash
./deploy.sh
```

脚本会：
1. 检查 `walle-ai.conf` 是否存在，以及 MQTT 必填项、`WHIP_SECRET` 是否已填写；
2. 安装为 `/etc/walle-ai.conf`（`root:root 600`）；
3. 安装并启用 `walle-ai.service`。

### 手动启动（仅开发环境）

```bash
./start.sh  # 默认读取脚本同级的 walle-ai.conf
```

`start.sh` 只用于开发环境；生产请使用下面的 systemd 服务。

### 系统自动启动（生产）

```bash
sudo systemctl start walle-ai   # deploy.sh 已 enable，开机自启
journalctl -u walle-ai -f       # 查看日志
```

上线前请把 `scripts/env_walle.sh` 中的 `WALLE_RUNTIME_ENV=debug` 注释掉，改用 prod。
