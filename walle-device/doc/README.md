# README说明

## 前置条件

配置 MQTT 服务器，获得 MQTT_HOST、MQTT_USERNAME、MQTT_PASSWORD、MQTT_TENANT。
配置 WebRTC 服务器，获得 WHIP_SECRET。
配置阿里云 API 密钥，获得 ALIYUN_API_KEY。

## 部署步骤

### 修改 walle-ai.conf 配置文件

```bash
cd scripts
cp walle-ai.conf.example walle-ai.conf
vi walle-ai.conf  # 打开配置文件，并修改相关参数
```

### 执行一键部署脚本

```bash
./deploy.sh
```

脚本会：
1. 检查 `walle-ai.conf` 是否存在，以及 MQTT 必填项、`WHIP_SECRET` 是否已填写；
2. 安装为 `/etc/walle-ai.conf`（`root:root 600`）；
3. 安装并启用 `walle-ai.service`。

## 手动启动（仅开发环境）

```bash
./start.sh  # 默认读取脚本同级的 walle-ai.conf
```

`start.sh` 只用于开发环境；生产请使用下面的 systemd 服务。

## 系统自动启动（生产）

```bash
sudo systemctl start walle-ai   # deploy.sh 已 enable，开机自启
journalctl -u walle-ai -f       # 查看日志
```

上线前请把 `scripts/env_walle.sh` 中的 `WALLE_RUNTIME_ENV=debug` 注释掉，改用 prod。
