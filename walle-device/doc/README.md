# README

## 项目介绍

### 功能实现

- 底盘控制：麦克纳姆轮运动学解算、速度/加速度限幅、cmd_vel 看门狗、串口异常与进程退出安全停车
- 远程控制：MQTT 接入 App，支持状态查询、视频开关、灯光/蜂鸣器/OLED/舵机等指令
- 音视频：USB 摄像头采集，WebRTC/RTMP 推流，支持双向语音
- 语音与大模型：语音唤醒/识别、LLM 对话、TTS 播报，以及巡线、追踪、避障等演示玩法
- 安全与运维：ROS 本机发现隔离、rosbridge 话题白名单、systemd 常驻、启动自检与版本横幅

### 结构设计

- `ros2_ws/src/car_control`：底盘、STM32 控制器、声呐
- `ros2_ws/src/video`：摄像头与 WebRTC 推流
- `ros2_ws/src/net`：MQTT 桥、rosbridge
- `ros2_ws/src/audio`、`ros2_ws/src/ai`：语音交互与大模型
- `ros2_ws/src/protocol`：自定义 ROS 消息与服务
- `ros2_ws/src/bringup`：统一启动、启动自检、版本横幅
- `scripts`：`deploy.sh`、`start.sh`、`env_walle.sh` 与配置/服务模板
- `doc`：部署文档、依赖清单与上线检查清单

### 软硬件要求

硬件：

- 树莓派 5（RaspberryPi5）或香橙派 5（OrangePi5），系统为 Ubuntu 24.04
- Walle 麦克纳姆轮底盘（STM32 控制器，SoC UART 连接）
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

```bash
# 安装ROS2的依赖，注意，如果版本是别的，如Humble，需要修改为对应的包名
sudo apt install -y ros-jazzy-rosbridge-server ros-jazzy-web-video-server ros-jazzy-usb-cam
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

# 阻止 colcon 扫描 venv 目录（venv 不在 ros2_ws 内时可省略）
touch ~/venv/COLCON_IGNORE

# 激活 venv 环境
source ~/venv/bin/activate

# 安装系统依赖（OpenCV / I2C / 推流）
sudo apt install -y ffmpeg python3-opencv python3-smbus libportaudio2

# 安装 walle 项目的 Python 依赖
pip install -r ~/walle-device/doc/requirements.txt
```

> 若 venv 不在 `~/venv`，在 source 前执行 `export WALLE_VENV=/path/to/venv`，`env_walle.sh` 会按实际路径激活。

## 编译与设备准备

### 编译工作空间

```bash
cd ~/walle-device/ros2_ws
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install
```

### 安装 udev 规则

根据主控板型号选择对应的安装脚本（仅安装串口映射规则）：

```bash
# 树莓派 5（RaspberryPi5）
cd ~/walle-ai/walle-device/scripts/udev
sudo ./create_rpi_udev_rules.sh

# 香橙派 5（OrangePi5）
cd ~/walle-ai/walle-device/scripts/udev
sudo ./create_orangePi_udev_rules.sh
```

> 规则会创建 `/dev/wi_uart`（树莓派 `ttyAMA0` / 香橙派 `ttyS6`，`dialout` 0660）；确保运行用户在 `dialout` 与 `video` 组。
> 摄像头不使用 udev 固定符号链接，直接使用实际设备节点（如 `/dev/video0`），由用户按实际设备配置。

### 配置摄像头设备

用 `ls /dev/video*` 或 `v4l2-ctl --list-devices` 确认真实的摄像头节点，然后修改：

- `ros2_ws/src/video/walle_video/config/usb_cam_param.yaml` 的 `video_device`
- `ros2_ws/src/video/video_webrtc_bridge/config/video_webrtc_bridge.yaml` 的 `camera_device`（需与上一项一致）

也可以在启动时覆盖：`ros2 launch bringup bringup.launch.py camera_device:=/dev/video1`。

> `usb_cam.launch.py` 启动前会校验设备存在、属于 USB 摄像头且支持 MJPG 640x480，失败则跳过启动，避免误开 ISP/编解码节点。

### 摄像头后端（auto / shm / gst / usb_cam）

`bringup.launch.py` 新增 `camera_backend` 参数：

- `auto`（默认）：检测到 `mppjpegdec` 时使用 `shm`，否则使用 `usb_cam`；
- `shm`：`walle_camera` 节点独占摄像头并发布 `/image_raw`，H.264 通过 GStreamer 共享内存（`shmsink`/`shmsrc`）交给 `video_webrtc_bridge` 混流推 RTMP，视频只编码一次；
- `gst`：`video_webrtc_bridge` 独占摄像头，MJPG 经 `mppjpegdec` 硬件解码后分两路：一路按 `ros_image_fps`（默认 10fps）发布 `/image_raw`，一路直接 `mpph264enc` 推流；
- `usb_cam`：保持原有 usb_cam + `/image_raw` + 推流桥模式。

```bash
ros2 launch bringup bringup.launch.py                      # RK3588 默认 shm
ros2 launch bringup bringup.launch.py camera_backend:=gst   # GStreamer 独占
ros2 launch bringup bringup.launch.py camera_backend:=shm   # 分离 + 共享内存 H.264
ros2 launch bringup bringup.launch.py camera_backend:=usb_cam
```

### 视频硬件编码

`video_webrtc_bridge` 的 `video_encoder=auto` 会优先使用硬件编码：

- Orange Pi 5 Plus（RK3588）：GStreamer `mpph264enc`（Rockchip MPP）。需 `gst-launch-1.0` 与 `gstreamer1.0-rockchip1`，Orange Pi 官方镜像已预装，可用 `gst-inspect-1.0 mpph264enc` 检查。
- 其他平台：ffmpeg `h264_v4l2m2m`；硬件不可用时自动回退 `libx264`。

启动日志会打印实际使用的编码器；RK3588 上应显示 `mpph264enc`（GStreamer/Rockchip MPP 硬件编码）。

## 部署形态选择（ROS2 版 / linux-demo 二选一）

本仓库提供两套设备端实现，**同一台设备只能部署其中一套**：

| | ROS2 版（本文档） | linux-demo 版（[README](../linux-demo/README.md)） |
| --- | --- | --- |
| 适用场景 | 需要底盘、舵机、声呐、语音/大模型等 ROS2 能力 | 仅做系统监控、摄像头推流与手机麦上行，不依赖 ROS2 |
| 代码目录 | `ros2_ws/` + `scripts/` | `linux-demo/` |
| systemd 服务 | `walle-ai.service` | `walle-ai.service`（同名） |
| 配置文件 | `/etc/walle-ai.conf` | `/etc/walle-ai.conf`（同名，键集合不同） |
| 部署脚本 | `scripts/deploy.sh` | `linux-demo/deploy.sh` |

两套实现的单元名与配置路径相同，后部署的会覆盖前一套的 unit 与配置，导致
`systemctl cat walle-ai` 的 `ExecStart` 指向另一套程序、启动参数不匹配。不要在同一台设备上
同时安装，也不要交替执行两个 `deploy.sh`。

### 从另一套实现迁移

1. 停用旧服务：`sudo systemctl disable --now walle-ai`；
2. 备份旧配置与设备标识（标识文件位置随实现不同）：
   - ROS2 版默认 `~/.config/walle-ai/device-id`（或 `DEVICE_ID_FILE` / yaml 中
     `device_id_file` 指向的文件）；
   - linux-demo 版默认 `/var/lib/walle-ai/device-id`（或 `DEVICE_ID_FILE`）；
3. 在目标实现的目录准备自己的 `walle-ai.conf`（两套的键集合不同，不要直接复用旧文件），
   必要时把旧标识文件的路径写入 `DEVICE_ID_FILE`，再执行对应的 `deploy.sh`；
4. 用 `systemctl cat walle-ai` 确认 `ExecStart` 指向目标实现，
   `journalctl -u walle-ai -n 50` 确认启动日志与版本横幅正常。

> 保留原 `device-id` 可避免换设备号后旧主题上的 retained `online` 变成幽灵卡片。

## 部署 Walle AI

### 前置条件

配置 MQTT 服务器，获得 MQTT_HOST、MQTT_USERNAME、MQTT_PASSWORD、MQTT_TENANT。
配置媒体服务器（Oryx），获得服务器地址与推流 secret（MEDIA_SERVER_HOST / MEDIA_SERVER_SECRET）。
配置阿里云 API 密钥，获得 ALIYUN_API_KEY。

### 部署步骤

#### 修改 walle-ai.conf 配置文件

```bash
cd scripts
cp walle-ai.conf.example walle-ai.conf
vi walle-ai.conf  # 打开配置文件，并修改相关参数
```

#### 执行部署脚本

编译（`colcon build`）、Python 依赖与 udev 规则请先按上文完成；`deploy.sh` 只负责系统级安装，可重复执行：

```bash
./deploy.sh
```

脚本会：
1. 检查 `walle-ai.conf` 是否存在，以及 MQTT 必填项、`MEDIA_SERVER_HOST` / `MEDIA_SERVER_SECRET` 是否已填写；
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

## 稳定性测试（上线前）

`test/stability_test.py` 用于故障注入与长稳验证，建议在整机镜像上以 root 执行：

```bash
sudo ./test/stability_test.py health                       # 服务/节点/MQTT 基线
sudo ./test/stability_test.py kill                         # 逐个 kill 节点，等待自动拉起
sudo ./test/stability_test.py camera                       # 解绑/绑定 uvcvideo，模拟拔插摄像头
sudo ./test/stability_test.py network --offline 15         # 只拦截 Broker 流量，不影响 SSH
./test/stability_test.py soak --hours 24 --require-stream  # 持续推流并采样 24h
```

- `network --mode link --iface wlan0` 才会断开整块网卡，需在串口/本地终端执行（脚本带延时自动恢复兜底）。
- 串口掉线无法软件模拟：手动拔插 `/dev/wi_uart` 后观察 `ros_robot_controller` 是否自动重连。
- `soak` 的 CSV 写入 `~/.ros/log/stability/soak-*.csv`，结束时按 RSS / FD / 磁盘增长判定泄漏；任一项 `FAIL` 需在发布前处理。

## 持续集成

`.github/workflows/ci.yml` 在 PR / push 到 `main`、`dev` 时执行：

- `lint`：全仓库关键 lint（语法、未定义名）+ `scripts` 与测试目录的 flake8
- `build-test`：ROS 2 Jazzy 下 `colcon build`，并运行 `controller`、`net_mqtt_bridge`、`video_webrtc_bridge` 的单元测试

首次使用需在 GitHub 仓库 `Settings → Branches` 为 `main` / `dev` 添加保护规则，
勾选 `Require status checks to pass before merging` 并选择 `lint`、`build-test`。
