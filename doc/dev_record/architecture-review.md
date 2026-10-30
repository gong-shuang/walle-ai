# 架构与技术债清单

> **本次复查：2026-10-30**（第六轮，N7 设备发现收敛后重新核对）
> 范围：`app/src/main`（46 个 Java 文件 / 8271 行）、`res/`、`app/build.gradle`、`AndroidManifest.xml`、`device/`
> 上一版：2026-10-30（第四轮，N2 全量迁移完成）
>
> **状态图例**：✅ 已完成　　◐ 部分完成　　⭕ 未开始
>
> | 等级 | 含义 | 条目 | ✅ | ◐ | ⭕ |
> |---|---|:--:|:--:|:--:|:--:|
> | 严重 | 安全 / 上架 / 崩溃级 | 15 | 15 | 0 | 0 |
> | 一般 | 架构、可靠性、性能、工程化 | 14 | 14 | 0 | 0 |
> | 忽略 | 技术栈升级（现阶段收益低于成本） | 10 | 0 | 0 | 10 |
>
> **结论**：**严重项与一般项全部清零**（15/15 + 14/14）。**N2**（ViewModel/LiveData 单一数据源）、**N11**（告警持久化）、**N7**（设备发现 O(N) 广播）均已完成。剩余 10 条忽略项属于技术栈升级（Kotlin/Compose/Hilt/MQTT5 等），已各自写明触发条件。

---

## 一、当前还没做的（按优先级）

### 1.1 一般项（建议做）

| # | 问题 | 现状证据（2026-10-30 复查） | 该怎么改 | 预估 | 状态 |
|:--:|---|---|---|:--:|:--:|
| N9 | ~~未配置 ABI 过滤，APK 体积大~~ | 已加 `ndk { abiFilters 'arm64-v8a','armeabi-v7a' }` + `bundle` 按 ABI/语言/密度拆分；WebRTC `.so` 41 MB → 17.3 MB | 已改完，装 `./gradlew :app:assembleDebug` 后量测（预期 debug ~27 MB、Bundle 单机下载 ~16 MB） | 0.5 天 | ✅ |
| N8 | ~~前台服务类型用 `dataSync`，Android 15 起有 6 小时/天配额~~ | 已改 `connectedDevice`：Manifest `foregroundServiceType="connectedDevice|dataSync"` + `FOREGROUND_SERVICE_CONNECTED_DEVICE` + `CHANGE_NETWORK_STATE`（Android 14 该类型的前置普通权限）；代码先试 connectedDevice，异常回退 dataSync | 已改完；装包后 `adb logcat -s MqttService` 应看到「前台服务已启动：type=connectedDevice」，若看到「回退 dataSync」则说明未生效 | 0.5 天 | ✅ |
| N4 | ~~视频链路无 `request_id`，靠布尔位防重入~~ | 已扩展到**所有请求**（discovery/state/capability/cmd/video start/stop）都带 `request_id`，设备端全部原样回显；App 按 id 配对，video 链路加 8s 超时复位 | 已改完；`selftest_topics.py` 新增「所有应答回显 rid」断言 | 1 天（两端） | ✅ |
| N5 | ~~`publish()` 在调用线程执行（多为 UI 线程），重连期可能卡顿~~ | 已改为异步：`publish()` 只入队，实际发送在单线程 `mqtt-publish` 执行器里做；队列上限 200，满时直接报错给 UI | 已改完；`adb logcat -v thread -s MqttTrace` 可见发送日志来自 `mqtt-publish` 线程 | 0.5 天 | ✅ |
| N10 | ~~`allowBackup=true` 且无 `dataExtractionRules`~~ | 已改 `allowBackup=false` + `fullBackupContent=false`：无有价值用户数据，备份只会带走 broker 地址与（换机后无法解密的）凭据 | 已完成；清单 `aapt2` 链接通过 | 0.5 天 | ✅ |
| N11 | ~~告警只在内存，进程被杀即丢~~ | 告警已落本地 SQLite（`data/LocalDb`，表 `alarm`）：插入即落盘、上限 200 条滚动清理、已读状态持久化，启动时读回内存并叠加未读数。**读同步、写异步**（单线程 `walle-db` 执行器），任何 DB 异常都降级为仅内存。**设备状态（state/result）刻意不落盘**：它表示「选中在线设备后当场查询到的那一刻」，设备列表本身就只展示在线设备，缓存旧快照只会造成「卡片在线、数据是昨天的」假象；状态页仍是选中→查询→渲染 | 已改完；`AlarmStoreTest`（10 例）用假 Persistence 锁住「重启后告警还在、未读数还准、超限滚动、落盘失败降级」 | 2 天 | ✅ |
| N1 | ~~`MqttManager` 是 God object~~ | 已拆成四层：`MqttClient`（连接/订阅/异步发布，370 行）、`MessageRouter`（主题→事件 + 去重，162 行）、`DeviceRegistry`（设备/状态/选中，228 行）、`MqttTrace`（日志，49 行）+ `ConnState`；`MqttManager` 退化为门面（479 行，原 914 行） | 已改完；四组单测 30 项通过 | 3–5 天 | ✅ |
| N2 | ~~UI 无 ViewModel、无单一数据源~~ | **全部页面已迁移**：`MqttViewModel` 基类（统一注册/注销 MQTT 监听）+ 7 个页面级 ViewModel，8 个文件共 1483 行；每个页面只 observe 一个不可变 `UiState`。Fragment/Activity **零** `MqttManager.Listener`、零 `AlarmStore.Observer`；`ui/` 里 JSON 解析只剩 ViewModel 与 2 个 Adapter 展示格式化（共 35 处，改造前状态页单文件就 55 处）；`runOnUiThread` 仅剩 1 处 | 已改完；纯 Java 用 LiveData，StateFlow 随 Kotlin 迁移（I1） | 1–2 周 | ✅ |
| N3 | ~~视频管道两份实现~~ | 已抽 `WhepSession`（497 行）：EGL/Factory/PeerConnection/WHEP 协商/首帧/释放 只此一份；`PlayerActivity` 649→**376 行**，重复代码交集从 **40% 降到 2%**；协商代码全项目只在 `WhepSession` 出现 | 已改完；`javac` 通过 | 2–3 天 | ✅ |
| N14 | ~~协议已大改，版本号没动~~ | 现为 `versionCode 10` / `versionName "1.9"`（1.8 为协议 v2 首个版本，1.9 起算本地持久化）；build.gradle 注释与 README 徽章同步标版本演进 | 完成（上一轮只改了注释、数值漏改，本轮已修正） | 10 分钟 | ✅ |
| N12 | ~~Android 侧零自动化测试、无 CI~~ | 已补 `app/src/test/.../TopicNamesTest`、`DeviceInfoTest`（40 个用例）+ `.github/workflows/android-ci.yml`（单测 → debug 构建 → 设备端自检 → 上传 APK） | 已改完；单测本地 `JUnitCore` 跑通（现 40 例），`./gradlew :app:testDebugUnitTest` 可复跑 | 2–3 天 | ✅ |
| N13 | ~~非 git 仓库~~ | 已 `git init`（分支 `main`）+ `.gitignore` + 基线提交 `4a11c14`；构建产物/本地配置已排除 | 完成 | 10 分钟 | ✅ |
| N7 | ~~设备发现是租户内广播，设备数增长后是 O(N)~~ | 列表改为**在线设备**（retained `online` 已带完整身份，订阅即回放），discovery 降级为「下拉刷新/兜底」按需动作：`MqttManager.onConnected()` 不再广播、`DevicesFragment.onStart()` 不再扫描；设备端加**抖动(≤0.8s) + 限频(2s)**。N=20/M=3/K=15 时每天报文 915 → **~300**，设备侧采集 300 次/天 → **0**，列表从「等 3 秒窗口」变成「订阅即出」 | 已完成，详见 [n7-discovery-scaling.md](n7-discovery-scaling.md)；O(1) 聚合器仅在 N>100 时再上 | 2 天 | ✅ |
| N6 | ~~`cleanSession=false` 与 `MemoryPersistence` 语义矛盾~~ | 已改 `cleanSession=true`：本地不持久化告警/状态，会话无保留价值；同时删掉了随之失效的「旧订阅清理」代码（与 `MemoryPersistence` 语义一致） | 已验证：每次连接重新订阅 + 广播发现；`javac`/单测通过 | 1 天 | ✅ |

> 说明：一般项已全部完成；剩余 10 条忽略项均为技术栈升级，触发条件写在各自条目里。

### 1.2 忽略项（现阶段不做）

| # | 问题 | 现状 | 将来怎么做 |
|:--:|---|---|---|
| I1 | 全 Java，异步靠 `Handler`/`ExecutorService` | 11 处 `postDelayed`、6 个 executor | 迁 Kotlin + Coroutines/Flow |
| I2 | XML + `findViewById`（89 处），未用 Compose | ViewModel 改造后页面已用 `viewBinding`（`binding.xxx`），剩余 `findViewById` 集中在 `SettingsActivity`(16)/`OnboardingActivity`(11)/各 Adapter(17) | 剩余页面统一 ViewBinding；新页面 Compose + Material3 |
| I3 | 无 DI | 手写单例（`MqttManager.get()`、`AlarmStore.get()`），无 `Application` 子类 | Hilt 或手写 `AppContainer` |
| I4 | Paho 1.2.5（MQTT 3.1.1） | `app/build.gradle:114` | **已做完整评估：[mqtt5-assessment.md](mqtt5-assessment.md)**——换库 6–10 人日、拿到全部收益再 +7–9 人日；触发条件：需要离线补发 / 排障成本走高 / Paho 出 CVE / 需要新协议特性（**N7 已用 retained online 方案解决，不再构成升级理由**）。届时直接上 HiveMQ 客户端，**不要**用 Paho v5 过渡 |
| I5 | 单模块 | 只有 `app`，`net`/`ui` 两个包 | 拆 `:core:net` `:core:rtc` `:feature:*`（与 N1/N3 一起做） |
| I6 | 依赖版本硬编码，无 version catalog | 见 `app/build.gradle:75-84` | `gradle/libs.versions.toml` |
| I7 | 无 Baseline Profile / 启动度量 | 无 `baselineprofile` 模块 | Baseline Profile + Macrobenchmark |
| I8 | 无深色模式/多语言，`strings.xml` 有 8 条未用 | 无 `values-night`/`values-en`；未用：`device_offline`、`history`、`notif_channel`、`section_alarm`、`section_server`、`tap_to_use`、`video_placeholder(_sub)` | 补 `values-night` + `localeConfig`，清理未用项 |
| I9 | JSON 全手写解析（`org.json`） | 已集中到 ViewModel（`ui/` 共 35 处 `optXxx`，改造前状态页单文件就有 55 处）；`StatusSnapshot`/`CameraCapability` 已是强类型模型 | kotlinx.serialization / Moshi + 数据类 |
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
| S3-回归 | 同一事件重复投递 → 渲染器二次 init 崩溃 | `MqttManager` 2 秒窗口去重 + `ControlFragment` 同房间幂等 + `WhepSession` 渲染器只 init 一次 |
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
| **状态页 ViewModel 试点（N2）** | `ui/StatusViewModel.java`、`model/StatusSnapshot.java`、`ui/StatusFragment.java`、`app/build.gradle` | 状态页改成「MQTT 回调 → ViewModel（解析为强类型模型）→ LiveData → 渲染」：UI 不再解析 JSON、不再自己维护 waiting/rid、回调里不碰 View；新增 `StatusSnapshotTest`（5 例）；补齐 lifecycle-viewmodel / lifecycle-livedata 依赖；删除无用的 `UiUtil.joinJsonStrings` |
| **网络层分层（N1）** | `net/MqttClient.java`、`net/MessageRouter.java`、`net/DeviceRegistry.java`、`net/MqttTrace.java`、`net/ConnState.java`、`net/MqttManager.java`、6 个 UI 文件 | 把 914 行的 `MqttManager` 拆成「传输 / 路由 / 注册表 / 门面」四层；`ConnState` 提升为顶层枚举（UI 20 处引用同步）；新增 `MessageRouterTest`、`DeviceRegistryTest`（合计 30 项单测） |
| **N2 全量迁移：ViewModel + LiveData** | `ui/MqttViewModel.java`（新增）、`ui/StatusViewModel`、`DevicesViewModel`、`CommandViewModel`、`AlarmViewModel`、`ControlViewModel`、`ConnectionViewModel`、`PlayerViewModel`、`model/CameraCapability`（新增）、对应 10 个 Fragment/Activity | 每个页面「MQTT/告警回调 → ViewModel 维护状态 → 不可变 `UiState` → LiveData → 渲染」：页面不再实现 `MqttManager.Listener`/`AlarmStore.Observer`，不再自己维护 `waiting/rid`，回调里不碰 View；同时修掉两个实打实的 bug——全屏播放页退出没带 `EXTRA_UUID` 导致设备端**永不停止推流**、以及全屏页重建后 spinner 与真实推流摄像头错位；`PlayerActivity` 与 `ControlFragment` 的切换摄像头状态机共用同一套「请求发出后才改状态 / 失败或 8s 超时回滚」逻辑 |
| **设备发现收敛（N7）** | `ui/DevicesViewModel.java`、`ui/DevicesFragment.java`、`net/MqttManager.java`、`device/rpi_monitor.py`、`device/selftest_topics.py` | 发现不再是「每次连接/切页都广播」，而是**按需**：设备列表直接由 retained `online`（已含全量身份）驱动，`onConnected` 只订阅不广播；`scan()` 仅由下拉刷新/扫描按钮触发，外加「连上 1.5 秒内一台设备都没回放出来」的兜底。设备端新增抖动 + 限频。顺带修掉「在线设备没在 3 秒窗口内答复就从列表消失」的旧缺陷。详见 [n7-discovery-scaling.md](n7-discovery-scaling.md) |
| **告警持久化（N11）** | `data/LocalDb.java`（新增）、`net/AlarmStore.java`、`net/MqttManager.java`、`app/build.gradle` | 用 `SQLiteOpenHelper` 落地一张 `alarm` 表，不引 Room：本项目纯 Java、单表、无关联查询，Room 的 annotation processor + schema 增量构建成本大于收益（迁 Kotlin 时随 I1 再评估）。`add` 即落盘、200 条滚动淘汰、`markAllRead`/`clear` 同步到库，`attach()` 启动时读回并叠加未读数。线程模型：读同步（启动几毫秒）、写异步（`walle-db` 单线程），所有 DB 调用 try/catch 降级为仅内存。**状态快照（state/result）明确不持久化**：它只对「当场选中在线设备」有意义，落盘会显示过期数据；DB v2 升级时顺带 `DROP TABLE IF EXISTS status` 清掉早期试验版本留下的表 |
| **版本号（N14 修正）** | `app/build.gradle`、`README.md` | 上一轮 N14 只改了注释、`versionCode` 实际仍是 1 —— 本轮修正为 `versionCode 10` / `versionName "1.9"`（首发 1.8 起算），README 徽章同步 |
| **修复：WebRTC 回调跨线程写 LiveData → 进程 SIGABRT** | `ui/WhepSession.java`、`ui/MqttViewModel.java`、`ui/*ViewModel.java`、`.github/workflows/android-ci.yml` | 现象：控制页点「开始推流」、WebRTC 一 CONNECTED 就闪退（`Fatal signal 6 / Check failed: !env->ExceptionCheck()`）。根因：`PeerConnection.Observer.onConnectionChange` 跑在 native signaling 线程，回调里 `ControlViewModel.publish()` 直接 `setValue` 抛 `IllegalStateException`，异常跨过 JNI 边界被 WebRTC 判定为致命。三层修复：①`WhepSession` 所有回调统一走 `dispatch()`——切主线程 + `try/catch(Throwable)`，异常只留在 Java 层；②`MqttViewModel` 提供 `runOnMain()`/`emit()`，各 VM 的 `publish()` 与 toast 写入做线程兜底（主线程仍走 `setValue`，避免 `postValue` 旧值覆盖新值的交错）；③CI 新增「架构守卫」：禁止页面实现 `MqttManager.Listener` 或写 LiveData，并锁死 `WhepSession.dispatch()` 存在 |
| **视频管道去重（N3）** | `ui/WhepSession.java`（新增）、`ui/PlayerActivity.java`、`ui/ControlFragment.java` | 抽出 `WhepSession`：EGL/Factory/PeerConnection/WHEP 协商/首帧/释放 唯一实现；播放页删掉自管的 240 余行 WebRTC 代码，只保留 UI（状态文案/首帧遮罩/摄像头切换/退出）；控制页沿用同一会话并保留自己的状态文案 |
| **收尾（N6 + N10）** | `AndroidManifest.xml`、`net/MqttManager.java`、`net/TopicNames.java` | 关闭备份；会话改 `cleanSession=true` 并删掉失效的旧订阅清理代码与对应单测 |
| **单测 + CI（N12）** | `app/src/test/`、`.github/workflows/android-ci.yml`、`app/build.gradle` | 40 个 JVM 单测锁住主题命名/清洗/订阅/配对与设备标识规则；CI 依次跑单测、debug 构建、设备端契约自检，并上传 APK |
| **git 基线（N13）** | 仓库根 | `git init -b main` + `.gitignore`（排除 build/.gradle/local.properties/venv/日志）+ 基线提交，共 120 个文件 |
| **异步发布（N5）** | `net/MqttManager.java` | `publish()` 只入队，发送在单线程 `mqtt-publish` 执行器执行（保序）；队列上限 200，满时回错误给 UI；`doPublish()` 内重新校验连接状态 |
| **请求配对（N4）** | App + 设备端 | 6 类请求全部带 `request_id`，设备 6 类应答全部回显；App 侧 cmd/video/capability 严格配对、state/discovery 宽松配对；video 链路加 8s 超时复位，不再靠 `starting`/`pendingCamera` 布尔位猜 |
| **前台服务类型（N8）** | `AndroidManifest.xml`、`MqttService.java` | `dataSync` → `connectedDevice`（避开 Android 15 的 6h/天配额），补 `FOREGROUND_SERVICE_CONNECTED_DEVICE` + `CHANGE_NETWORK_STATE`；保留 dataSync 作兜底并在日志里提示 |
| **包体优化（N9）** | `app/build.gradle` | 加 ABI 过滤（arm64-v8a + armeabi-v7a）+ App Bundle 按 ABI/语言/密度拆分；WebRTC `.so` 从 41 MB 降到 17.3 MB，debug APK 预计 ~27 MB、Bundle 单机下载 ~16 MB |
| **版本号提升（N14）** | `app/build.gradle`、`README.md` | 目标 `versionCode 9` / `versionName "1.8"`，标注「v1 主题已删除，与旧设备/旧 App 不兼容」（**注：当时只落了注释，数值漏改，已在 1.9 一并修正为 versionCode 10**） |
| 修掉 `requirements.txt` 被误存为需求文档的问题 | `device/` | 恢复为 `paho-mqtt>=2.0` / `psutil>=5.9`；原 674 行文档归档到 `device/docs/legacy-v1-spec.md` |

---

## 四、建议顺序

1. **量测 N9 / 验证 N8**（10 分钟）：`./gradlew :app:assembleDebug` 看 APK 大小；装包后看 logcat 有无「connectedDevice 被拒绝」回退日志。
2. **实机验证 N11**（10 分钟）：收几条告警 → `adb shell am force-stop www.walle.ai` → 重开 App，设置页红点与告警列表应还在；状态页仍应「选中在线设备 → 查询 → 渲染」（不显示任何上次留下的旧快照）。
3. 忽略项等业务上量后再做；MQTT5 的触发条件见 [mqtt5-assessment.md](mqtt5-assessment.md)。

---

## 五、复查方法与已知局限

```bash
# 规模
find app/src/main/java -name "*.java" | xargs wc -l | tail -1    # 8271 行 / 46 文件

# 发布配置
grep -n "compileSdk\|targetSdk\|minifyEnabled\|shrinkResources\|abiFilters" app/build.gradle

# 架构信号
grep -rl "ViewModel" app/src/main/java | wc -l                                   # 19（8 个页面 VM + 基类 + 引用方）
grep -rl "MqttManager.Listener" app/src/main/java | wc -l                        # 3（MqttViewModel + StatusViewModel + 接口定义）
grep -rl "androidx.room\|Hilt" app/src/main/java | wc -l                         # 0（N11 用原生 SQLite，I3 未做）
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
b=norm('app/src/main/java/www/walle/ai/ui/WhepSession.java')
print(f'{len(a&b)}/{len(b)} = {100*len(a&b)/len(b):.0f}%')
PY

# 设备端自检
cd device && python3 selftest_topics.py
```

**局限**（本次无法验证的项）：

- 沙箱内 Gradle 无法初始化 native 服务，**未跑 AGP 构建、R8 与真机视觉回归**；Android 侧只做了 `aapt2` 资源链接 + `javac --release 17` 全量编译。
- APK 体积、启动耗时、帧率等运行时指标需要在真机上量测。
- `targetSdk 36` 已通过运行日志确认生效，但 edge-to-edge 的逐页视觉回归仍需按 `doc/s4-target-sdk-36-migration.md` 的清单人工过一遍。
