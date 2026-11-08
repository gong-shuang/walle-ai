# 快速上手

> 本文是 `walle` App 的完整上手指南：环境要求、构建、Broker 配置、证书与权限。
> 项目概览见 [README](../README.md)；MQTT 报文约定见 [MQTT_protocol_convention.md](MQTT_protocol_convention.md)；
> 设备端（树莓派）部署见 [linux/README.md](../linux/README.md)。

## 环境要求

| 项 | 版本 |
|---|---|
| Android Studio | 2026.2 及以上（支持 AGP 9.x） |
| JDK | 17（Studio 自带 JBR 21 亦可） |
| Android SDK | Platform 36、Build-Tools 36.0.0 |
| AGP / Gradle | 9.4.1 / 9.6.0（见 [build.gradle](../walle-android/build.gradle) 与 [gradle-wrapper.properties](../walle-android/gradle/wrapper/gradle-wrapper.properties)） |
| 运行设备 | Android 8.0（API 26）及以上（targetSdk 36，已按 edge-to-edge 适配） |

## 构建

用 Android Studio 打开工程目录 `walle-android/`，等待 Gradle Sync 后直接 Run；或命令行：

```bash
cd walle-android

# 按需覆盖默认 Broker 地址（优先级：-P 参数 > 环境变量 > 默认值）
./gradlew :app:assembleDebug \
  -PmqttHost=192.168.1.10 \
  -PmqttPort=8883 \
  -PoryxSecret=<Oryx推流secret>   # 必填：反向音频 WHIP 与设备推流鉴权，见下文「配置 Oryx 推流 secret」

# release APK（R8 混淆 + 资源压缩）
./gradlew :app:assembleRelease

# 发布到 Play（按 ABI / 语言 / 密度拆包，单机只下载自己那套 .so）
./gradlew :app:bundleRelease
```

> 已配置 ABI 过滤：只打 `arm64-v8a` + `armeabi-v7a`（WebRTC `.so` 从 41 MB 降到 17.3 MB）。
> 如果你的模拟器是 **x86_64** 镜像，需要在 [app/build.gradle](../walle-android/app/build.gradle) 的
> `ndk { abiFilters ... }` 里临时加回 `'x86_64'`；arm64 镜像（Apple Silicon 上的模拟器）则无需改动。

> 已提交 `gradlew` / `gradlew.bat` 与 wrapper jar，首次构建会自动下载 Gradle 9.6.0。

## 配置 Broker

**Broker 地址/端口**可在构建期通过 `buildConfigField` 注入（仅这两个参数，代码库中不含任何账号口令）：

| 方式 | 示例 |
|---|---|
| `gradle.properties` | `mqttHost=www.baidong.xyz` |
| 命令行参数 | `-PmqttHost=192.168.1.10 -PmqttPort=8883` |
| 环境变量 | `MQTT_DEFAULT_HOST`、`MQTT_DEFAULT_PORT` |

**用户名/密码不提供默认值**：首次启动时在 App 的「设置 → 服务器连接」里输入，随后由
Android Keystore 加密保存在本地（旧版本遗留的明文凭据会自动迁移并删除）。
无凭据时不会建立 MQTT 连接，也不会驻留前台通知。

**TLS 默认开启**：默认端口就是 `8883`，新装默认走 `ssl://`。设置页的「启用 TLS 加密 (ssl://)」
开关决定 URI 是 `ssl://` 还是 `tcp://`；保存时按端口自动纠正
（8883/8084/8884 → 开启，1883/8083 → 关闭），避免「端口改了但协议没换」。
EMQX 默认端口对应关系：

| 端口 | 协议 | 说明 |
|---|---|---|
| 1883 | `tcp://` | 明文，需关闭 TLS 开关 |
| 8883 | `ssl://` | TLS，需开启 TLS 开关 |
| 8083 | `ws://` | WebSocket，明文 |
| 8084 | `wss://` | WebSocket + TLS |

## 配置 Oryx 推流 secret

服务器部署 Oryx 后会生成一个**推流鉴权 secret**（32 位十六进制），每台服务器**各自不同**，
重置后旧客户端立即失效，因此**不能写死在代码里**，构建期注入：

| 方式 | 写法 |
|---|---|
| 命令行 | `-PoryxSecret=<secret>` |
| `gradle.properties` | `oryxSecret=<secret>` |
| 环境变量 | `WHIP_SECRET=<secret> ./gradlew :app:assembleDebug` |

- 只影响**推流**（手机麦克风 WHIP、设备视频 RTMP）；拉流（WHEP）不需要。
- 没配置时构建会打印警告，点麦克风按钮会提示「未配置 Oryx 推流 secret」。
- 获取方式与设备端配置（`/etc/walle-ai.env` 的 `WHIP_SECRET`）见
  [服务器部署文档 §6.1](service_deploy.md)。

## 证书

App **不内置任何证书**，全部依赖 Android 系统信任库：

- **MQTT（8883）** 与 **WHEP（443）** 都要求服务端使用公共 CA 签发的证书
  （例如 TrustAsia / Let's Encrypt），且证书 SAN 必须包含你在 App 里填写的域名。
- 私有 CA / 自签证书：把 CA 证书放进 `walle-android/app/src/main/res/raw/`，在
  [network_security_config.xml](../walle-android/app/src/main/res/xml/network_security_config.xml)
  加 `domain-config` + `trust-anchors`（**只信任该域名**），不要用「信任所有证书」的写法。
- Debug 构建额外信任「用户安装的 CA」（见 `network_security_config.xml` 的 `debug-overrides`），
  方便自签证书联调；Release 只信系统 CA。
- 启用与排查命令：

```bash
openssl s_client -connect www.baidong.xyz:8883 -servername www.baidong.xyz -showcerts
echo | openssl s_client -connect www.baidong.xyz:443 -servername www.baidong.xyz 2>/dev/null \
  | openssl x509 -noout -subject -dates -text | grep -A2 "Subject Alternative Name"
```

## 权限

| 权限 | 用途 |
|---|---|
| `INTERNET`、`ACCESS_NETWORK_STATE` | MQTT / WHEP 网络通信 |
| `FOREGROUND_SERVICE`、`FOREGROUND_SERVICE_CONNECTED_DEVICE` | 前台服务保活 MQTT 长连接（`connectedDevice` 类型，不受 Android 15 的 6h/天配额限制） |
| `CHANGE_NETWORK_STATE` | Android 14+ 对 `connectedDevice` 前台服务的前置要求（普通权限，安装即得，无弹窗） |
| `FOREGROUND_SERVICE_DATA_SYNC` | 仅兜底：个别 ROM 不认 `connectedDevice` 时回退 |
| `POST_NOTIFICATIONS` | 前台服务常驻通知（Android 13+ 需运行时授予） |
| `WAKE_LOCK` | 播放页保持唤醒 |

## 安装与验证

```bash
# 安装到已连接的设备/模拟器
adb install -r walle-android/app/build/outputs/apk/debug/app-debug.apk

# 启动主界面
adb shell am start -n www.walle.ai/.ui.MainActivity
```

## 下一步

1. 部署设备端采集脚本（树莓派一键安装）：[linux/README.md](../linux/README.md)
2. 了解两端通信的主题与报文字段：[MQTT_protocol_convention.md](MQTT_protocol_convention.md)
3. 跑一遍契约测试确认环境无误：

```bash
(cd walle-android && ./gradlew :app:testDebugUnitTest)   # App 端单测
python3 linux/selftest_topics.py                         # 设备端主题契约自检（不需要树莓派）
```
