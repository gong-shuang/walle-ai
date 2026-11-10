# linux-demo 项目

## 简介
这是一个在 Linux 设备上运行的 walle，适用于没有 ROS2 环境的设备。

## 配置运行环境
venv 与 Python 依赖的配置参考 [../doc/README.md](../doc/README.md)。

### 配置文件
1. 复制模板并填写：
   ```bash
   cp walle-ai.conf.example walle-ai.conf
   vi walle-ai.conf
   ```
   必填 MQTT 配置与 `WHIP_SECRET`；视频/手机音频模板默认必填，不需要对应能力时把 `VIDEO_ENABLE` / `PHONE_AUDIO_ENABLE` 置为 `false`。
2. 开发环境直接运行 `main.py`，会自动读取脚本同级的 `walle-ai.conf`。
3. 生产环境安装为 `/etc/walle-ai.conf`，权限固定 `root:root 600`：
   ```bash
   sudo install -m 600 -o root -g root walle-ai.conf /etc/walle-ai.conf
   ```
4. 生产环境由 systemd 的 `EnvironmentFile=/etc/walle-ai.conf` 注入；不要把配置 `chown` 给普通用户。配置缺失时程序会拒绝启动。

## 自动运行
如果想设置成 systemd 服务，开机自动运行：

```bash
cp walle-ai.service.example walle-ai.service
# 编辑 walle-ai.service，把 $WALLE_PATH / $VENV_PATH 替换为实际路径
# 例如：$WALLE_PATH -> /home/ubuntu（WorkingDirectory 会拼成 /home/ubuntu/walle-ai/...）
# 例如：$VENV_PATH -> /home/ubuntu（ExecStart 会拼成 /home/ubuntu/venv/bin/python）
vi walle-ai.service

sudo cp walle-ai.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable walle-ai
```

## 手动运行
先停掉 systemd 服务（同一 clientId 同时连接会被 Broker 互相踢下线），再执行：

```bash
sudo systemctl stop walle-ai
/home/ubuntu/venv/bin/python main.py
```
