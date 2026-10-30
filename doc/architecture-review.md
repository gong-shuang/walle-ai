# 架构与技术债清单

> **本次复查：2026-10-30**（第三轮，全量按代码重新核对）
> 范围：`app/src/main`（30 个 Java 文件 / 6637 行）、`res/`、`app/build.gradle`、`AndroidManifest.xml`、`device/`
> 上一版：2026-10-29（同日二轮）
>
> **状态图例**：✅ 已完成　　◐ 部分完成　　⭕ 未开始
>
> | 等级 | 含义 | 条目 | ✅ | ◐ | ⭕ |
> |---|---|:--:|:--:|:--:|:--:|
> | 严重 | 安全 / 上架 / 崩溃级 | 15 | 15 | 0 | 0 |
> | 一般 | 架构、可靠性、性能、工程化 | 14 | 5 | 4 | 5 |
> | 忽略 | 技术栈升级（现阶段收益低于成本） | 10 | 0 | 0 | 10 |
>
> **结论**：**严重项已全部清零**（15/15）；一般项已完成 5 条（N4 请求配对、N5 异步发布、N8 前台服务类型、N9 包体、N14 版本号）。剩下 **5 条一般项未开始 + 4 条部分完成**，以及 10 条忽略项，均不阻塞当前开发与使用。

---

## 一、当前还没做的（按优先级）

### 1.1 一般项（建议做）

| # | 问题 | 现状证据（2026-10-30 复查） | 该怎么改 | 预估 | 状态 |
|:--:|---|---|---|:--:|:--:|
| N9 | ~~未配置 ABI 过滤，APK 体积大~~ | 已加 `ndk { abiFilters 'arm64-v8a','armeabi-v7a' }` + `bundle` 按 ABI/语言/密度拆分；WebRTC `.so` 41 MB → 17.3 MB | 已改完，装 `./gradlew :app:assembleDebug` 后量测（预期 debug ~27 MB、Bundle 单机下载 ~16 MB） | 0.5 天 | ✅ |
| N8 | ~~前台服务类型用 `dataSync`，Android 15 起有 6 小时/天配额~~ | 已改 `connectedDevice`：Manifest `foregroundServiceType="connectedDevice|dataSync"` + `FOREGROUND_SERVICE_CONNECTED_DEVICE` + `CHANGE_NETWORK_STATE`（Android 14 该类型的前置普通权限）；代码先试 connectedDevice，异常回退 dataSync | 已改完；装包后 `adb logcat -s MqttService` 应看到「前台服务已启动：type=connectedDevice」，若看到「回退 dataSync」则说明未生效 | 0.5 天 | ✅ |
| N4 | ~~视频链路无 `request_id`，靠布尔位防重入~~ | 已扩展到**所有请求**（discovery/state/capability/cmd/video start/stop）都带 `request_id`，设备端全部原样回显；App 按 id 配对，video 链路加 8s 超时复位 | 已改完；`selftest_topics.py` 新增「所有应答回显 rid」断言 | 1 天（两端） | ✅ |
| N5 | ~~`publish()` 在调用线程执行（多为 UI 线程），重连期可能卡顿~~ | 已改为异步：`publish()` 只入队，实际发送在单线程 `mqtt-publish` 执行器里做；队列上限 200，满时直接报错给 UI | 已改完；`adb logcat -v thread -s MqttTrace` 可见发送日志来自 `mqtt-publish` 线程 | 0.5 天 | ✅ |
| N10 | `allowBackup=true` 且无 `dataExtractionRules` | `AndroidManifest.xml:12` | `allowBackup=false`，或配 `dataExtractionRules` 排除配置文件（凭据已 Keystore 加密，风险中低） | 0.5 天 | ⭕ |
| N11 | 告警、状态只在内存，进程被杀即丢 | `AlarmStore` 用 `ArrayList`，无持久化 | Room 存告警与最近状态；界面先渲染缓存 | 2 天 | ⭕ |
| N1 | `MqttManager` 是 God object，且越来越重 | **914 行**（上轮 736），新增了 v2 路由、去重、旧订阅清理 | 拆 `MqttClient` / `MessageRouter` / `DeviceRepository` / `VideoController` | 3–5 天 | ⭕ |
| N2 | UI 无 ViewModel、无单一数据源 | 0 个 ViewModel/StateFlow；`findViewById` **89 处**；`StatusFragment` 里 **55 处 `optXxx`**；全项目 **51 处 `requireActivity()/requireContext()`** | ViewModel + StateFlow + `repeatOnLifecycle`；协议解析下沉到数据类 | 1–2 周 | ⭕ |
| N3 | 视频管道两份实现 | `PlayerActivity` 649 行 / `WhepPlayer` 205 唯一行，交集 **82 行（40%）** | 抽 `WhepSession`，播放页与控制页共用 | 2–3 天 | ⭕ |
| N14 | ~~协议已大改，版本号没动~~ | 已提为 `versionCode 9` / `versionName "1.8"`，并在 build.gradle 注释与 README 徽章里标明「v1 主题已删除，不兼容旧设备/旧 App」 | 完成 | 10 分钟 | ✅ |
| N12 | Android 侧零自动化测试、无 CI | `app/src` 下只有 `main`，无 `test`/`androidTest`；无 `.github` | 先补 `TopicNames`/`DeviceInfo` 单测 + `MqttManager` 路由去重测试；GitHub Actions 跑 `assembleDebug + lint + test` | 2–3 天 | ◐ |
| N13 | 非 git 仓库 | 无 `.git`（`gradlew` 已补齐） | `git init` + `.gitignore` + 首次提交 | 10 分钟 | ◐ |
| N7 | 设备发现是租户内广播，设备数增长后是 O(N) | `requestDeviceInfo()` 发 `walle/{tenant}/discovery/query`，每台设备各回一条 | 设备数上量后改 MQTT5 响应主题或 `$share/` 聚合 | 等需求 | ◐ |
| N6 | `cleanSession=false` 与 `MemoryPersistence` 语义矛盾 | `MqttManager:300` 仍 `setCleanSession(false)` + `MemoryPersistence`（已加旧订阅清理，解决了「升级后订阅残留」，但在途 QoS1 消息仍不持久化） | 改 `MqttDefaultFilePersistence`，或改 `cleanSession=true` 靠业务补偿 | 1 天 | ◐ |

> ◐ 说明：N12 设备端已有 `selftest_topics.py`（Android 侧仍为零）；N13 `gradlew` 已补但仓库未纳入 git；N7 发现范围已从「全局广播」收敛到「租户内广播」；N6 的订阅残留问题已修，持久化语义矛盾仍在。

### 1.2 忽略项（现阶段不做）

| # | 问题 | 现状 | 将来怎么做 |
|:--:|---|---|---|
| I1 | 全 Java，异步靠 `Handler`/`ExecutorService` | 11 处 `postDelayed`、6 个 executor | 迁 Kotlin + Coroutines/Flow |
| I2 | XML + `findViewById`（89 处），未用 Compose | `viewBinding` 只有 `PlayerActivity` 在用 | 新页面 Compose + Material3；存量统一 ViewBinding |
| I3 | 无 DI | 手写单例（`MqttManager.get()`、`AlarmStore.get()`），无 `Application` 子类 | Hilt 或手写 `AppContainer` |
| I4 | Paho 1.2.5（MQTT 3.1.1） | `app/build.gradle:81` | HiveMQ Client（MQTT5：会话过期、响应主题、原因码） |
| I5 | 单模块 | 只有 `app`，`net`/`ui` 两个包 | 拆 `:core:net` `:core:rtc` `:feature:*`（与 N1/N3 一起做） |
| I6 | 依赖版本硬编码，无 version catalog | 见 `app/build.gradle:75-84` | `gradle/libs.versions.toml` |
| I7 | 无 Baseline Profile / 启动度量 | 无 `baselineprofile` 模块 | Baseline Profile + Macrobenchmark |
| I8 | 无深色模式/多语言，`strings.xml` 有 8 条未用 | 无 `values-night`/`values-en`；未用：`device_offline`、`history`、`notif_channel`、`section_alarm`、`section_server`、`tap_to_use`、`video_placeholder(_sub)` | 补 `values-night` + `localeConfig`，清理未用项 |
| I9 | JSON 全手写解析（`org.json`） | `StatusFragment` 单文件 55 处 `optXxx` | kotlinx.serialization / Moshi + 数据类 |
| I10 | 无日志框架/崩溃上报 | 全项目 `android.util.Log`，TAG 为字面量 | Timber + Crashlytics/Sentry |

---

## 二、已完成的（严重项全表）

| # | 内容 | 关键证据 / 验证 |
|:--:|---|---|
| S1 | 客户端补齐 TLS：`ssl://` 拼接 + 设置页开关 + 端口自动识别 + 失败原因提示 | `MqttConfig`/`SettingsActivity`/`MqttManager`；`aapt2` + `javac` 通过 |
| S1b | 默认安全化：默认端口 8883、`tls` 默认跟随端口、清单改用 `network_security_config`、端口与协议双向纠正 | 清单已无 `usesCleartextTraffic`（`AndroidManifest.xml:17` 为 `networkSecurityConfig`） |
| S2 | 删除 WHEP 的 trust-all 与恒 true 的 HostnameVerifier | `WhepClient` 改为系统 CA + 默认主机名校验；线上证书实测链 OK |
| S3 | 多租户主题 v2：`walle/{tenant}/{device}/{resource}` | `TopicNames` + `MqttManager`；v1 全局主题已删除 |
| S3b | EMQX 按账号 ACL（`walle/${username}/#`、设备 `walle/{t}/${clientid}/#`） | `device/emqx/acl.conf.example`；已实测越权收不到 |
| S3-回归 | 同一事件重复投递 → 渲染器二次 init 崩溃 | `MqttManager` 2 秒窗口去重 + `ControlFragment` 同房间幂等 + `WhepPlayer` 渲染器只 init 一次 |
| S4 | targetSdk/compileSdk 36、AGP 9.4.1、Gradle 9.6、edge-to-edge | `app/build.gradle:28/34`；运行日志 `target_sdk_version=36` 证明构建生效；视觉回归见 `doc/s4-target-sdk-36-migration.md` |
| S5 | release 开 R8 + 资源压缩 | `minifyEnabled true` / `shrinkResources true`（`app/build.gradle:55-56`）+ 完整 keep 规则 |
| S6 | 引导页位图按需解码（HARDWARE/RGB_565 + 后台线程 + LRU） | `OnboardingImageLoader.java`；堆占用 36.6 MB → 19 MB / API28+ 近 0 |
| S7 | 移除编进 APK 的默认 MQTT 口令 | `MqttConfig.username/password` 默认空串，首次由用户输入 |
| S8 | 凭据改 Keystore AES-GCM 加密 + 旧明文迁移 | `CredentialCrypto` + `MqttConfigStore` |
| S9 | 补齐 `onboarding_*` 字符串（当时构建失败） | `strings.xml`；`aapt2` 链接通过 |
| S10 | `GuideActivity` → `OnboardingActivity/OnboardingPrefs` | 全项目无 `GuideActivity` 残留 |
| S11 | 引导页挂载深色主题 | `AndroidManifest.xml:41` |
| S12 | 无凭据不建连、不驻留前台通知 | `MqttService.hasCredentials()` 门控 |

---

## 三、本轮（2026-10-30）变更记录

| 变更 | 影响 | 说明 |
|---|---|---|
| **协议统一为 `query` / `exec` / `request` → `result`** | App + 设备端 | 查询 `.../query`、命令 `.../cmd/exec`、动作 `.../video/*/request`；应答一律 `.../result`，且只在收到请求后发布；主动上报仅 `online`、`alarm` |
| **删除 v1 协议全部代码与注释** | App + 设备端 | `Topics.java` 只剩连接常量；`MqttManager` 只剩 v2 分发；设备端删掉 `TOPIC_*`/`TOPIC_SCHEME`/双发 |
| 修复设备发现不应答 | 设备端 | `walle/{t}/discovery/query` 只有 3 段，原统一按 4 段解析导致 `resource="query"` 匹配不到 handler |
| App 不再自收自发 | App | 订阅改为 8 个精确响应主题（不再 `walle/{t}/#`）；连接时先退订旧会话遗留过滤器（`cleanSession=false` 坑） |
| 设备端命令应答日志 | 设备端 | 新增 `_publish_cmd_result()`：发布 + `已响应命令执行 -> .../cmd/result（request_id=…，status=…，耗时 …）`，覆盖禁用/空命令/白名单/失败/超时全部分支 |
| 设备端工程化 | `device/` | 新增一键部署 `install.sh`（首次交互式必填 5 项）、`--check` 配置自检、环境文件自动加载、`selftest_topics.py` 扩展为 8 组断言 |
| **异步发布（N5）** | `net/MqttManager.java` | `publish()` 只入队，发送在单线程 `mqtt-publish` 执行器执行（保序）；队列上限 200，满时回错误给 UI；`doPublish()` 内重新校验连接状态 |
| **请求配对（N4）** | App + 设备端 | 6 类请求全部带 `request_id`，设备 6 类应答全部回显；App 侧 cmd/video/capability 严格配对、state/discovery 宽松配对；video 链路加 8s 超时复位，不再靠 `starting`/`pendingCamera` 布尔位猜 |
| **前台服务类型（N8）** | `AndroidManifest.xml`、`MqttService.java` | `dataSync` → `connectedDevice`（避开 Android 15 的 6h/天配额），补 `FOREGROUND_SERVICE_CONNECTED_DEVICE` + `CHANGE_NETWORK_STATE`；保留 dataSync 作兜底并在日志里提示 |
| **包体优化（N9）** | `app/build.gradle` | 加 ABI 过滤（arm64-v8a + armeabi-v7a）+ App Bundle 按 ABI/语言/密度拆分；WebRTC `.so` 从 41 MB 降到 17.3 MB，debug APK 预计 ~27 MB、Bundle 单机下载 ~16 MB |
| **版本号提升（N14）** | `app/build.gradle`、`README.md` | `versionCode 9` / `versionName "1.8"`，标注「v1 主题已删除，与旧设备/旧 App 不兼容」 |
| 修掉 `requirements.txt` 被误存为需求文档的问题 | `device/` | 恢复为 `paho-mqtt>=2.0` / `psutil>=5.9`；原 674 行文档归档到 `device/docs/legacy-v1-spec.md` |

---

## 四、建议顺序

1. **量测 N9 / 验证 N8**（10 分钟）：`./gradlew :app:assembleDebug` 看 APK 大小；装包后看 logcat 有无「connectedDevice 被拒绝」回退日志。
2. **N13 + N12**（1–2 天）：`git init` 提交基线；补 `TopicNames`/`DeviceInfo` 单测与 CI（设备端 `selftest_topics.py` 可一并入 CI）。
3. **N1 + N2 + N3**（1–2 周）：分层改造一次性做完，同时处理 N11（Room 持久化）。
4. N6/N7/N10 随手清理；忽略项等业务上量后再做。

---

## 五、复查方法与已知局限

```bash
# 规模
find app/src/main/java -name "*.java" | xargs wc -l | tail -1    # 6637 行 / 30 文件

# 发布配置
grep -n "compileSdk\|targetSdk\|minifyEnabled\|shrinkResources\|abiFilters" app/build.gradle

# 架构信号
grep -rl "ViewModel\|StateFlow\|androidx.room\|Hilt" app/src/main/java | wc -l   # 0
grep -rho "findViewById" app/src/main/java | wc -l                              # 89
grep -rho "requireActivity()\|requireContext()" app/src/main/java | wc -l       # 51

# 视频管道重复度（去空行/注释/import 后求交集）
python3 - <<'PY'
def norm(p):
    out=set()
    for line in open(p,encoding='utf-8'):
        s=line.strip()
        if len(s)<15 or s.startswith(('//','*','import','package')): continue
        out.add(s)
    return out
a=norm('app/src/main/java/www/walle/ai/ui/PlayerActivity.java')
b=norm('app/src/main/java/www/walle/ai/ui/WhepPlayer.java')
print(f'{len(a&b)}/{len(b)} = {100*len(a&b)/len(b):.0f}%')
PY

# 设备端自检
cd device && python3 selftest_topics.py
```

**局限**（本次无法验证的项）：

- 沙箱内 Gradle 无法初始化 native 服务，**未跑 AGP 构建、R8 与真机视觉回归**；Android 侧只做了 `aapt2` 资源链接 + `javac --release 17` 全量编译。
- APK 体积、启动耗时、帧率等运行时指标需要在真机上量测。
- `targetSdk 36` 已通过运行日志确认生效，但 edge-to-edge 的逐页视觉回归仍需按 `doc/s4-target-sdk-36-migration.md` 的清单人工过一遍。
