<div align="center">

# 树莓派控制器 · walle

**用手机远程监控与操控 Linux / 树莓派设备的 Android 客户端**

![version](https://img.shields.io/badge/version-1.9-blue)
![Android](https://img.shields.io/badge/Android-8.0%2B-3DDC84?logo=android&logoColor=white)
![Java](https://img.shields.io/badge/Java-17-007396?logo=openjdk&logoColor=white)
![MQTT](https://img.shields.io/badge/MQTT-3.1.1-660066)
![WebRTC](https://img.shields.io/badge/WebRTC-WHEP-1D9BF0)
![minSdk](https://img.shields.io/badge/minSdk-26-informational)
![targetSdk](https://img.shields.io/badge/targetSdk-34-informational)

</div>

---

## 项目简介

`walle` 是一个基于 **MQTT** 的 Android 设备面板：手机连上 MQTT Broker 后，可以发现局域网/公网上的树莓派等 Linux 设备，实时查看 CPU、内存、磁盘、网络、温度等运行状态，接收异常告警，远程执行 shell 命令，并通过 **WebRTC（WHEP）** 观看设备端摄像头的实时画面。

App 只负责「看」和「控制」，设备端由配套的 Python 监控脚本（仓库外）负责采集与执行，两端通过一组约定好的 MQTT 主题交互。

```
┌──────────────────┐      MQTT 主题 / JSON      ┌──────────────────┐
│   Android App    │ ◄────────────────────────► │   MQTT Broker    │
│  树莓派控制器      │                            │  Mosquitto 等    │
└────────┬─────────┘                            └────────┬─────────┘
         │ WHEP over HTTPS                               │ MQTT
         ▼                                               ▼
┌──────────────────┐      RTMP / RTSP 推流      ┌──────────────────┐
│  WHEP / 流媒体服务 │ ◄───────────────────────── │ 树莓派 / Linux 设备│
│  SRS、MediaMTX 等 │                            │ main.py          │
└──────────────────┘                            └──────────────────┘
```

---

## 功能特性

### 🗂 设备管理
- 列表 = **当前在线设备**：直接来自设备发布的上线 retained 报文（已含完整身份），订阅即回放，不广播请求
- 下拉刷新 / 手动重新扫描：按需广播一次 `discovery/query` 刷新设备身份详情（连上后一台设备都没回放出来时会自动兜底一次）
- 设备唯一标识以 `uuid` 为主键，设备重启换 `uuid` 时按 `device_id` 自动归并，不会产生幽灵卡片

### 📊 实时状态
- **系统**：设备型号、主机名、IP、系统 / 内核版本、已运行时长
- **CPU**：总使用率、温度、1/5/15 分钟负载、当前 / 最小 / 最大主频、逻辑与物理核心数、各核占用
- **内存**：使用率、已用 / 总量 / 可用、Swap
- **磁盘**：多挂载点逐个展示容量、使用率、文件系统类型
- **网络**：总入站 / 出站速率，各网卡 `↓rx / ↑tx`
- 手动查询 + 自动刷新（间隔可配），下拉刷新

### 🎮 视频控制
- 枚举设备端 v4l2 摄像头，展示支持的格式 / 分辨率 / 帧率
- 点击画面发起推流，运行中可切换摄像头（复用同一房间号，避免黑屏）
- 内嵌预览 + 全屏播放页（跟随重力左右横屏、沉浸式、保持常亮）
- WHEP 拉流：ICE 收集完成 → 发送 SDP Offer → 应用 Answer

### ⌨️ 远程命令
- 在设备上以 shell 执行任意命令，超时 1–300 秒可调
- 基于 `request_id` 的结果配对，避免多命令串台
- 8 条常用命令一键填入（`uptime`、`df -h`、`free -m`、`vcgencmd measure_temp` …）
- 本次会话命令历史（保留 20 条）
- 结果区分 `success / failed / timeout / denied / error` 并按状态着色

### 🔔 告警中心
- 接收设备告警、恢复、USB 接入 / 拔出事件
- 按级别（`critical / warning / info`）与事件类型配色
- 全局未读计数，底部导航「设置」tab 显示红点
- 一键清空

### ⚙️ 连接设置
- 服务器地址、端口、用户名、密码、`clientId`、自动重连、启动自动连接、状态刷新间隔
- 保存后自动重连；`clientId` 持久化避免被 Broker 互踢
- 前台服务保活 MQTT 长连接（常驻通知）

### 🚀 首次启动引导
- 3 屏全屏插图引导，支持左右滑动、指示点、跳过
- 第 3 屏继续左滑即进入主界面，结束后写入「已看过」标记
- 设置页「重看新手引导」随时回顾

---

## 界面预览

|                引导 1 · 设备中枢                 |               引导 2 · 画面与控制                |                  引导 3 · Agent                  |
|:------------------------------------------------:|:------------------------------------------------:|:------------------------------------------------:|
| <img src="images/onboarding_1.png" width="220"/> | <img src="images/onboarding_2.png" width="220"/> | <img src="images/onboarding_3.png" width="220"/> |

---

## MQTT 主题约定

**协议 v1（多租户主题）**：所有主题都在 `walle/{tenant}/{device}/` 下展开。

- `tenant` = App 登录用的 MQTT 用户名（清洗后），Broker 用 `walle/${username}/#` 做 ACL；
- `device` = 设备端稳定的 `device_id`（MAC + CPU + 内存的 md5 前 16 位，重启不变）；
- 所有报文均为 JSON，QoS 1，Keep Alive 60s；除发现广播外，请求必须带匹配的 `uuid`；
- `online` 为 retained：设备上线时发布（含完整身份字段），掉线由遗嘱发布 `online:false`。

请求按语义命名：查询用 `…/query`、命令用 `…/exec`、动作（推流）用 `…/request`；应答一律
`…/result` 且只在收到对应请求后发布，并原样回显 `request_id` 供 App 配对；
主动上报只有 `online` 与 `alarm`。

> **完整的报文字段定义、设计原则与修改流程见 [MQTT_protocol_convention.md](doc/MQTT_protocol_convention.md)。**
> 改主题或报文格式前请先读它。

推流成功后在 App 侧按 `room_number` 拼接 WHEP 地址拉流（`BuildConfig.WHEP_URL_TEMPLATE`）。

> 设备端实现与部署见 [`linux/README.md`](linux/README.md)。

---

## 快速开始

| 重要文档 | 介绍 |
|---|---|
| [doc/service_deploy.md](doc/service_deploy.md) | 如何部署服务器、配置证书，权限介绍|
| [doc/quick_start.md](doc/quick_start.md) | 如何编译app，连接服务器，如何使用|
| [linux/README.md](linux/README.md) | 如何部署linux设备端代码，如何使用和调试设备端代码。|




---

## 技术栈

| 领域 | 选型 |
|---|---|
| 语言 | Java 17 |
| UI | AndroidX AppCompat + Material Components，ViewPager2、SwipeRefreshLayout、ConstraintLayout；**全部页面 ViewModel + LiveData 单一数据源**（纯 Java 故选 LiveData，StateFlow 随 I1 迁移） |
| 网络协议 | MQTT 3.1.1（Eclipse Paho 1.2.5，QoS 1、自动重连、LWT） |
| 视频 | WebRTC（`io.getstream:stream-webrtc-android`）+ WHEP 信令 |
| 序列化 | `org.json` |
| 持久化 | SQLite（仅告警收件箱，`data/LocalDb`；设备状态是实时查询的，不落盘）、SharedPreferences + Android Keystore（AES-GCM 加密凭据） |
| 构建 | AGP 9.4.1 / Gradle 9.6.0 / compileSdk 36 / targetSdk 36 / minSdk 26 |

---

## 常见问题

**连不上 Broker？**
检查手机与设备是否在同一网络、Broker 是否监听、用户名密码是否正确；同 `clientId` 多端登录会被互相踢下线（App 会自动重连）。设置页里能看到具体失败原因（超时 / 凭据被拒 / 端口协议不匹配等）。

**设备列表为空？**
设备端需先连接同一 Broker 并保持在线。点击「重新扫描」会在本租户内广播 `walle/{tenant}/discovery/query`，设备在 3 秒内回复即会出现。

**推流后黑屏？**
确认设备端已启动推流、WHEP 服务可访问、`room_number` 与 App 拼接的地址一致。切换摄像头会复用原房间号，不会新建会话。

**想再看一次引导页？**
「设置 → 重看新手引导」。

---

## 已知限制

- MQTT 默认走 `ssl://:8883`；清单已禁止明文流量（`network_security_config`，debug 包允许用户 CA），仅当显式改用 1883 明文端口时才会以 `tcp://` 连接。
- 凭据已用 Android Keystore 加密存储；MQTT 与 WHEP 均使用系统 CA 严格校验，服务端证书需要是公共 CA 签发且 SAN 与填写域名一致。
- **不做云备份/整机迁移**（`allowBackup=false`）：换机后需要重新输入 Broker 账号密码；这是刻意的取舍，避免凭据随备份外流。
- 仓库不含设备端采集脚本与 Broker 配置，需自行部署。
- 目前的架构与技术债清单、升级路线见 [`doc/architecture-review.md`](doc/architecture-review.md)。

---

## 相关文档

- [快速上手指南](doc/quick_start.md) — 环境要求、构建、Broker 配置、证书与权限
- [任务清单](doc/task_list_20261030.md) — 待开发任务与验收标准
- [MQTT 协议约定（协议 v1）](doc/MQTT_protocol_convention.md) — 全部报文字段定义、设计原则与修改流程（改协议前必读）
- [设备发现收敛（N7）](doc/dev_record/n7-discovery-scaling.md) — 列表改由 retained `online` 驱动，discovery 降级为按需
- [MQTT5 升级评估](doc/dev_record/mqtt5-assessment.md) — 工作量（6–10 / 13–19 人日）、收益排序、ACL 代价与触发条件
- [架构与技术评审](doc/dev_record/architecture-review.md) — 分层、安全、性能、可测试性现状与 P0/P1/P2 升级路线
- [targetSdk 36 升级指南](doc/dev_record/s4-target-sdk-36-migration.md) — 工具链升级、edge-to-edge 适配、视觉回归清单与设备端影响
- [多租户主题方案](doc/dev_record/s3-multi-tenant-topics.md) — 主题契约、EMQX ACL 步骤与验收
- [设备端部署说明](linux/README.md) — 树莓派一键安装脚本、配置项与排障

## 许可

本项目采用 Apache License 2.0 许可证。请参阅 [LICENSE](LICENSE) 文件。
