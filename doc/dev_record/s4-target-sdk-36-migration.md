# S4 升级指南：targetSdk 34 → 36 + edge-to-edge

> 对应技术债清单：`doc/architecture-review.md` 的 **S4**
> 适用范围：仅 Android 客户端。**树莓派 / 设备端零改动**（详见第 4 节）。
> 更新时间：2026-10-29

---

## TL;DR

| 角色 | 要做的事 |
|---|---|
| 我已经做完 | 工具链升级（AGP 9.4.1 / Gradle 9.6.0 / compileSdk 36 / targetSdk 36）、edge-to-edge 改造、系统栏透明化、预测式返回开关、wrapper 补齐 |
| **你要做** | ① SDK Manager 装 **Android 16 (API 36)** ② Sync + 构建 ③ 在 Android 15/16 设备上过一遍「视觉回归清单」 ④ 出 release AAB 上架 |
| **树莓派设备端** | **不需要改任何东西**。只需配合做一轮联调（连接 / 状态 / 命令 / 推流 / 告警） |

---

## 1. 为什么必须升

| 变化 | 影响 |
|---|---|
| Play 商店上架要求 targetSdk ≥ 35（2025-08 起）、≥ 36（2026-08 起） | 目前 34 已无法更新上架，必须到 36 |
| Android 15（API 35）对 targetSdk ≥ 35 的应用**强制 edge-to-edge** | `setStatusBarColor` / `setNavigationBarColor` 变成空操作，旧写法会出现「背景铺不满 + 标题被状态栏压住」 |
| Android 16（API 36）移除 `windowOptOutEdgeToEdgeEnforcement` 退路 | 不能再用「关闭强制全屏」的方式躲避适配 |
| Android 16 起预测式返回默认启用 | 返回手势/按钮需要走 `OnBackPressedCallback` 体系 |

参考：Play 目标 API 要求、Android 16 行为变更（官方文档 developer.android.com/google/play/requirements/target-sdk、developer.android.com/about/versions/16/behavior-changes-16）。

---

## 2. 代码侧改动（已完成）

| 文件 | 改动 |
|---|---|
| `build.gradle` | AGP `8.5.2` → **`9.4.1`**（compileSdk 36 要求 AGP ≥ 8.9.1；9.4.1 与本机 Studio 2026.2 一致） |
| `gradle/wrapper/gradle-wrapper.properties` | Gradle `8.9` → **`9.6.0`**（AGP 9.4.1 要求的最低版本） |
| `gradlew` / `gradlew.bat` / `gradle/wrapper/gradle-wrapper.jar` | **新增**：原来仓库没有 wrapper，构建不可复现；现在可直接 `./gradlew` |
| `app/build.gradle` | `compileSdk 34`→**36**、`targetSdk 34`→**36**，新增 `buildToolsVersion '36.0.0'` |
| `ui/UiUtil.java` | `applyFullscreen()` 重写为 **`applyEdgeToEdge(activity, content)`**：`setDecorFitsSystemWindows(false)` + 把 systemBars / cutout / **IME** insets 转成 content 的 padding |
| `ui/MainActivity.java`、`ui/SettingsActivity.java`、`ui/AlarmActivity.java` | 改为 `setContentView()` 之后调用 `applyEdgeToEdge(this, findViewById(android.R.id.content))` |
| `res/values/themes.xml` | 系统栏颜色改为透明（背景由 `windowBackground` 铺满），关闭 `enforceStatusBarContrast` / `enforceNavigationBarContrast`（避免三键导航出现灰遮罩） |
| `ui/PlayerActivity.java`、`ui/OnboardingActivity.java` | 删除已废弃的系统栏染色调用（这两个页面本来就有自己的 insets 处理） |
| `AndroidManifest.xml` | 新增 `android:enableOnBackInvokedCallback="true"`；设置页补 `windowSoftInputMode="adjustResize"` |

**已完成的自测**：`aapt2 compile + link`（build-tools 36.0.0）、全量 `javac --release 17` 均通过。

**未做的自测**：AGP/R8 构建与真机视觉回归 —— 沙箱里跑不了 Gradle，必须由你在 Studio 里完成（见第 3 节）。

---

## 3. 你需要做什么（按顺序）

### 3.1 安装 SDK 平台（一次性）

本机 SDK 现状：`platforms/` 只有 `android-34` 和 `android-37.0`，**缺少 API 36**；`build-tools` 已有 36.0.0。

```
Android Studio → Settings → Languages & Frameworks → Android SDK → SDK Platforms
→ 勾选 "Android 16 (API 36)"  → Apply
```

> 也可以继续用已装的 `android-37.0`：把 `app/build.gradle` 的 `compileSdk` 改成 `37`（`targetSdk` 保持 36 即可）。**不推荐**，因为 37 是更新的平台，第三方库与工具的兼容性验证更少。

### 3.2 同步与构建

```bash
# 1) 确认 wrapper 生效
./gradlew --version        # 应输出 Gradle 9.6.0

# 2) debug 构建
./gradlew :app:assembleDebug

# 3) release 构建（R8 已开启，见 S5）
./gradlew :app:assembleRelease
```

如果 Sync 报「AGP 版本不被 Studio 支持」或 DSL 不兼容：

- 首选：按 Studio 弹出的「Upgrade Android Gradle Plugin」提示走，它会同时改 wrapper 版本；
- 回退方案：AGP 改 `8.13.x` + Gradle 改 `8.14.x`（这两条只动版本号，**业务代码不用改**）。

### 3.3 视觉回归清单（必须真机/模拟器，Android 15 或 16）

| 页面 | 检查点 | 期望 |
|---|---|---|
| 引导页 | 状态栏区域、底部文案、跳过按钮 | 深色背景铺满到状态栏/导航栏；跳过按钮不被状态栏压住；底部文案不被导航条遮挡 |
| 主界面 | 顶部内容、底部导航栏 | 内容从状态栏下方开始；底部导航整体在导航栏之上（三键导航时尤其看） |
| 设备/状态/控制/命令页 | 列表滚动到顶部/底部 | 首项不被状态栏遮挡，末项能完整滚出到导航栏之上 |
| 设置页 | 顶部返回栏、输入框 + 软键盘 | 键盘弹出后输入框不被遮挡（本次已把 IME insets 并入 padding） |
| 告警页 | 顶部返回栏、列表底部 | 同上 |
| 全屏播放页 | 横屏、刘海区域、返回按钮、状态文字 | 沉浸式不变；返回按钮避开挖孔；退出全屏后返回上一页 |
| 导航模式 | 手势导航 + 三键导航各看一遍 | 三键导航下不应出现灰色半透明遮罩（已用 theme 关掉对比度强制） |
| 分屏/大屏（可选） | 竖屏锁定是否仍生效 | 竖屏锁定保持 |
| 返回操作 | Android 13+ 设备 | 返回手势正常，引导页「左滑跳过」逻辑不受影响 |

### 3.4 上架

```bash
./gradlew :app:bundleRelease
```

- Play Console 上传 AAB 后，检查「目标 API 级别」显示 36；
- 如果之前有内部测试轨道，先走内测再全量。

---

## 4. 树莓派 / 设备端要做什么

### 结论：**不需要改任何东西。**

原因：

1. `targetSdk` / `compileSdk` 只影响 **App 与 Android 系统**的交互（权限、窗口、后台限制），与 MQTT 协议无关；
2. 本次没有改任何 topic、payload 字段、端口、TLS 策略、WHEP 地址 —— 设备端代码（`main.py` 之类）、EMQX 配置、证书都不用动；
3. 设备端连 broker 用的是它自己的 MQTT 客户端，不受 Android targetSdk 影响；
4. WebRTC 的 16KB page 兼容性此前已验证（arm64 `.so` 的 `p_align=0x4000`），与设备端无关。

### 但仍需配合做一轮联调（因为 App 的编译/混淆策略变了）

| 验证项 | 怎么做 | 通过标准 |
|---|---|---|
| MQTT 连接 | 打开 App → 设置里填 8883 + TLS → 保存 | 状态显示「已连接」，且 `logcat -s MqttManager` 无异常 |
| 设备发现 | 下拉刷新 / 重新扫描 | 能看到设备卡片，在线数量正确 |
| 状态查询 | 状态页点「查询状态」 | CPU/内存/磁盘/网络卡片有数据 |
| 远程命令 | 命令页执行 `uptime` | 返回结果，状态为 success |
| 视频推流 | 控制页选摄像头 → 点画面 | 能出画面；切换摄像头、退出全屏不黑屏 |
| 告警 | 拔插 USB 或触发阈值 | 告警列表出现对应事件，设置 tab 红点出现 |

> 只有在下次要改 **S3（topic 命名空间 + Broker ACL）** 或协议字段时，才需要设备端同步改。届时建议「新旧 topic 并存 + 灰度」再切换。

---

## 5. 回滚

本次涉及 9 个文件，回滚只需把版本号改回并恢复 `UiUtil`：

| 文件 | 回滚内容 |
|---|---|
| `build.gradle` | AGP 改回 `8.5.2` |
| `gradle/wrapper/gradle-wrapper.properties` | 改回 `8.9`（`gradlew` 可保留） |
| `app/build.gradle` | `compileSdk/targetSdk` 改回 34，删 `buildToolsVersion` |
| `ui/UiUtil.java` + 3 个 Activity | 恢复 `applyFullscreen` 老实现 |
| `res/values/themes.xml` | 系统栏颜色改回 `@color/screen_bg_top` / `@color/nav_bg` |
| `AndroidManifest.xml` | 删 `enableOnBackInvokedCallback` 与 `windowSoftInputMode` |

建议先 `git init` 提交一次当前状态，回滚就是 `git revert`。

---

## 6. 常见报错与处理

| 现象 | 原因 | 处理 |
|---|---|---|
| `compileSdk 36 requires AGP 8.9.1 or higher` | AGP 没生效 | 确认根 `build.gradle` 的 classpath 已是 9.4.1，且 `./gradlew --stop` 后重新 Sync |
| `Unsupported class file major version` | JDK 太老 | Gradle JDK 设为 17+（Studio 自带 JBR 21 即可） |
| 顶部出现双倍留白 | `fitsSystemWindows="true"` 与 insets padding 叠加 | 布局里不要再用 `fitsSystemWindows`；只在 `applyEdgeToEdge` 处理一次 |
| 键盘挡住输入框 | edge-to-edge 后 `adjustResize` 不再缩小窗口 | 已把 `Type.ime()` 并入底部 padding；若仍异常，检查该 Activity 是否设置了 `adjustPan` |
| 三键导航出现灰色遮罩 | 系统对比度强制 | 已在主题关闭 `enforceNavigationBarContrast` |
| 返回键行为异常 | 预测式返回启用 | 所有返回都用 `OnBackPressedCallback` 或 finish()；不要 override `onBackPressed()` |
| R8 报 missing class | 混淆缺规则 | 按提示补 `-dontwarn`，不要写 `-dontwarn **`（见 S5 回归清单） |

---

## 7. 本次刻意没做的（后续可选）

- 分屏 / 自由窗口 / 大屏适配（Manifest 里锁了竖屏，暂不涉及）；
- 大字号（`fontScale`）下的布局回归；
- 折叠屏铰链区域（`WindowInsetsCompat.Type.displayCutout()` 已处理挖孔，未处理 fold）；
- 状态栏图标对比度的逐页微调（当前统一浅色图标 + 深色背景）。
