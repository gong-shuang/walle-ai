# Walle 项目

## 项目介绍

## 部署说明

修改文件 walle-ai.env 中的 ALIYUN_API_KEY 为你的新key
执行 auto.sh 脚本部署项目

```bash
sudo ./src/scripts/auto.sh
```


## 需要注意
```
`large_models` 的 llm_color_track.launch.py 和 llm_visual_patrol.launch.py 仍依赖`app` 的 object_tracking / line_following，这两个节点不迁，所以这两个 launch 在 walle 下不可用；验收用的`start.launch.py` 不受影响。
```

