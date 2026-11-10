# linux-demo 项目


## 简介
这是一个在linux设备上运行的walle，适用于没有ros2环境的设备

## 配置运行环境
怎么配置venv环境，和怎么配置walle-ai.conf文件，参考[../doc/README.md](../doc/README.md)文件


## 自动运行
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



