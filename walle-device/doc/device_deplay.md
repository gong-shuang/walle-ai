# 设备部署说明



## 部署步骤

复制模板并填写配置（`walle-ai.env` 已被 git 忽略，不会入库）：

```bash
cp walle_ws/src/scripts/walle-ai.env.example walle_ws/src/scripts/walle-ai.env
```

至少填写 `ALIYUN_API_KEY`、`MQTT_HOST`、`MQTT_USERNAME`、`MQTT_PASSWORD`、`MQTT_TENANT`、`WHIP_SECRET`。
执行 auto.sh 脚本部署项目

```bash
sudo ../walle_ws/src/scripts/auto.sh
```

