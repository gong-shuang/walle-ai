# linux-demo 设备端（无 ROS2 环境）

在树莓派等 Linux 设备上运行的 Walle 设备端：MQTT 接入 App、系统监控、USB 摄像头 RTMP 推流、
手机麦克风上行 WHEP 拉流播放。不依赖 ROS2。

> **与 ROS2 版二选一**：本目录与 `walle-device/ros2_ws` 使用同名 systemd 服务 `walle-ai`
> 和同名 `/etc/walle-ai.conf`，同一台设备只能部署一套，后部署的会覆盖前一套。选型与迁移
> 步骤见 [`../doc/README.md`](../doc/README.md)「部署形态选择」；迁移前先
> `sudo systemctl disable --now walle-ai`，并保留原 `device-id`。


## 配置运行环境
怎么配置venv环境，和怎么配置walle-ai.conf文件，参考[../doc/README.md](../doc/README.md)文件


## 设置systemctl服务自动运行
如果想设置成system服务，开机自动运行，则需要注意的是：walle-ai.service文件的配置，不用使用[../doc/README.md](../doc/README.md)文件的方法，需要手动编写

```bash
cp walle-ai.service.example walle-ai.service
# 编辑walle-ai.service文件，将  $VENV_PATH 和 $WALLE_HOME 替换为实际路径
# 例如：/home/ubuntu/venv
# 例如：/home/ubuntu/walle-ai/
vi walle-ai.service

cp walle-ai.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable walle-ai
```


## 手动运行
如果想手动运行本程序，需要在命令行中执行以下命令：
```bash
/home/ubuntu/venv/python3 main.py
```

常用命令：
python3 main.py                         前台运行（自动读 /etc/walle-ai.conf）
python3 main.py --check                 只打印解析后的配置与订阅主题，不连 broker
python3 main.py --check --connect       配置检查 + Broker 登录/订阅/发布回环验证
python3 main.py --env-file PATH         使用指定配置文件
python3 main.py --help                  查看本说明


